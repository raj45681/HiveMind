"""Disposable, reproducible retrieval measurements; never touches the real vault."""
from contextlib import ExitStack
import json
import math
from pathlib import Path
import statistics
import tempfile
import time
from unittest.mock import patch

from .store import Hive, atomic_write


TARGET = "03-Projects/alpha/Solutions/token-refresh-race.md"
WRONG = "03-Projects/beta/Solutions/chromatic-elephant-cache.md"


def _milliseconds(samples):
    ordered = sorted(samples)
    return {"median": round(statistics.median(ordered), 2),
            "p95": round(ordered[min(len(ordered) - 1, math.ceil(len(ordered) * .95) - 1)], 2)}


async def run(root, distractors=200, semantic=False):
    if not 0 <= distractors <= 2000:
        raise ValueError("Benchmark distractors must be between 0 and 2000")
    from . import semantic as semantic_module
    semantic_health = semantic_module.health(root) if semantic else None
    if semantic and semantic_health['status'] != 'ready':
        raise ValueError(f"Local semantic model is {semantic_health['status']}: {semantic_health['detail']}")
    from .server import build_server

    with tempfile.TemporaryDirectory(prefix="hivemind-retrieval-") as tmp, ExitStack() as stack:
        if semantic:
            real_embed = semantic_module._embed
            stack.enter_context(patch.object(semantic_module, "ready", return_value=True))
            stack.enter_context(patch.object(semantic_module, "_embed",
                                            side_effect=lambda _root, texts, download=False:
                                            real_embed(root, texts, download=download)))
        hive = Hive(tmp)
        notes = {
            TARGET: "# Token refresh race\n\nA concurrent login request can renew an expired credential twice. "
                    "Guard token refresh with a lock, then rerun the login regression test.\n",
            WRONG: "# Chromatic elephant cache\n\nA chromatic elephant cache was repaired in a separate project.\n",
            "03-Projects/alpha/Solutions/upload-retry.md":
                "# Upload retry\n\nRetry an interrupted media upload with its existing idempotency key.\n",
        }
        for index in range(distractors):
            notes[f"03-Projects/alpha/Notes/distractor-{index:04d}.md"] = (
                f"# Background note {index}\n\nRoutine build observation {index}: "
                "CI output, packaging metadata and harmless project administration.\n")
        for path, content in notes.items():
            atomic_write(hive.note_path(path), content)
        hive.index()

        cases = [
            ("exact", "token refresh lock", "alpha", TARGET),
            ("paraphrase", "simultaneous sign-in secret replacement", "alpha", TARGET),
            ("unrelated", "volcanic marmalade", "alpha", None),
            ("unrelated_domain", "How to repair a leaking bathroom faucet?", "alpha", None),
            ("unrelated_near", "unrelated project accounting system", "alpha", None),
            ("wrong_project", "chromatic elephant cache", "alpha", None),
        ]
        search_results, search_times = [], []
        for label, query, project, expected in cases:
            start = time.perf_counter()
            found = hive.search(query, limit=3, project=project)
            elapsed = (time.perf_counter() - start) * 1000
            paths = [item["path"] for item in found]
            search_times.append(elapsed)
            hit = expected in paths if expected else not paths
            search_results.append({"case": label, "query": query, "expected": expected,
                                   "hit": hit,
                                   "returned": paths, "latency_ms": round(elapsed, 2),
                                   "response_bytes": len(json.dumps(found, ensure_ascii=False).encode("utf-8"))})
        context_results, context_times = [], []
        for budget in (512, 1000, 1800):
            start = time.perf_counter()
            brief = hive.context(project="alpha", query="token refresh lock", budget_tokens=budget)
            elapsed = (time.perf_counter() - start) * 1000
            context_times.append(elapsed)
            context_results.append({"budget_tokens": budget, "target_included": any(
                item["path"] == TARGET for item in brief["relevant"]),
                "estimated_tokens": brief["budget"]["estimated_tokens"],
                "response_bytes": len(json.dumps(brief, ensure_ascii=False).encode("utf-8")),
                "latency_ms": round(elapsed, 2)})
        schemas = {}
        for profile in ("full", "memory"):
            tools = await build_server(tmp, tool_profile=profile).list_tools()
            schemas[profile] = {"tools": len(tools),
                                "json_bytes": len(json.dumps([tool.model_dump(mode="json") for tool in tools],
                                                       ensure_ascii=False).encode("utf-8"))}
        return {"fixture": "disposable synthetic vault", "mode": "local-semantic" if semantic else "lexical",
                "distractors": distractors, "paid_model_calls": 0,
                "semantic_health": semantic_health,
                "search": {"cases": search_results,
                           "target_hit_rate": sum(row["hit"] for row in search_results[:2]) / 2,
                           "unrelated_false_hits": sum(len(row["returned"]) for row in search_results if row["case"].startswith("unrelated")),
                           "wrong_project_leaks": sum(path.startswith("03-Projects/beta/")
                                                      for path in search_results[-1]["returned"]),
                           "wrong_project_irrelevant_results": len(search_results[-1]["returned"]),
                           "latency_ms": _milliseconds(search_times)},
                "context": {"budgets": context_results, "latency_ms": _milliseconds(context_times)},
                "tool_schemas": schemas}
