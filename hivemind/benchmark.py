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
UPLOAD = "03-Projects/alpha/Solutions/upload-retry.md"
ARCHIVED = "99-Archive/03-Projects/alpha/Solutions/old-refresh-policy.md"
CANDIDATE = "01-Memory/Candidates/alpha/hidden-candidate.md"
DRAFT = "03-Projects/alpha/Review-Queue/hidden-checkpoint.md"
SCOPED = "03-Projects/alpha_2/Solutions/segment-boundary.md"


def cases():
    # A small, manually labelled corpus. Paraphrases are measured in core mode,
    # but only required when the user explicitly selects semantic retrieval.
    return [
        ("exact", "token refresh lock", "alpha", TARGET, True),
        ("paraphrase", "simultaneous sign-in secret replacement", "alpha", TARGET, False),
        ("upload_exact", "interrupted upload idempotency", "alpha", UPLOAD, True),
        ("upload_paraphrase", "resend a failed file transfer without duplicating it", "alpha", UPLOAD, False),
        ("ambiguous", "refresh", "alpha", TARGET, True),
        ("conflicting_archive", "token refresh bypass obsolete", "alpha", TARGET, True),
        ("unrelated", "volcanic marmalade", "alpha", None, True),
        ("unrelated_domain", "How to repair a leaking bathroom faucet?", "alpha", None, True),
        ("unrelated_near", "unrelated project accounting system", "alpha", None, True),
        ("wrong_project", "chromatic elephant cache", "alpha", None, True),
        ("candidate_excluded", "quartz candidate secret", "alpha", None, True),
        ("draft_excluded", "nebula checkpoint draft", "alpha", None, True),
        ("archive_excluded", "obsolete bypass policy", "alpha", None, True),
        ("literal_project", "segment boundary semaphore", "alpha_2", SCOPED, True),
        ("project_prefix", "segment boundary semaphore", "alpha", None, True),
        ("empty", "", "alpha", None, True),
        ("stopwords", "the and of", "alpha", None, True),
    ]


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
            UPLOAD: "# Upload retry\n\nRetry an interrupted media upload with its existing idempotency key. "
                    "This prevents duplicate files when a file transfer fails.\n",
            ARCHIVED: "# Obsolete bypass policy\n\nToken refresh bypass was once recommended. "
                      "Obsolete advice allowed concurrent renewal without a lock.\n",
            CANDIDATE: "# Quartz candidate secret\n\nAn unapproved inferred preference.\n",
            DRAFT: "# Nebula checkpoint draft\n\nStatus: draft\n\nUnreviewed checkpoint evidence.\n",
            SCOPED: "# Segment boundary semaphore\n\nThis semaphore belongs only to alpha_2.\n",
            "03-Projects/alphaX2/Solutions/segment-boundary.md":
                "# Segment boundary semaphore\n\nThis semaphore belongs only to alphaX2.\n",
        }
        for index in range(distractors):
            notes[f"03-Projects/alpha/Notes/distractor-{index:04d}.md"] = (
                f"# Background note {index}\n\nRoutine build observation {index}: "
                "CI output, packaging metadata and harmless project administration.\n")
        for path, content in notes.items():
            atomic_write(hive.note_path(path), content)
        cold_index = hive.index()
        unchanged_index = hive.index()
        atomic_write(hive.note_path(UPLOAD), notes[UPLOAD] + "\nThe retry preserves the original key.\n")
        changed_index = hive.index()
        search_results, search_times = [], []
        for label, query, project, expected, core_required in cases():
            start = time.perf_counter()
            found = hive.search(query, limit=3, project=project)
            elapsed = (time.perf_counter() - start) * 1000
            paths = [item["path"] for item in found]
            search_times.append(elapsed)
            hit = expected in paths if expected else not paths
            rank = paths.index(expected) + 1 if expected in paths else None
            forbidden = [path for path in paths if path in {ARCHIVED, CANDIDATE, DRAFT}
                         or (path.startswith("03-Projects/") and path.split("/")[1] != project)]
            required = core_required or semantic
            search_results.append({"case": label, "query": query, "project": project, "expected": expected,
                                   "hit": hit, "required": required, "rank": rank,
                                   "forbidden_results": forbidden,
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
        positives = [row for row in search_results if row["expected"]]
        negatives = [row for row in search_results if not row["expected"]]
        failures = [row["case"] for row in search_results
                    if row["forbidden_results"] or (row["required"] and not row["hit"])]
        if not all(item["target_included"] and item["estimated_tokens"] <= item["budget_tokens"] for item in context_results):
            failures.append("context_budget")
        if unchanged_index["read_notes"] != 0 or changed_index["read_notes"] != 1:
            failures.append("incremental_index")
        return {"fixture": "disposable synthetic vault", "mode": "local-semantic" if semantic else "lexical",
                "ok": not failures, "failures": failures, "case_count": len(search_results),
                "distractors": distractors, "paid_model_calls": 0,
                "semantic_health": semantic_health,
                "indexing": {"cold": cold_index, "unchanged": unchanged_index, "one_edit": changed_index},
                "search": {"cases": search_results,
                           "target_hit_rate": sum(row["hit"] for row in positives) / len(positives),
                           "mean_reciprocal_rank": sum(1 / row["rank"] if row["rank"] else 0 for row in positives) / len(positives),
                           "negative_case_count": len(negatives),
                           "negative_false_hits": sum(len(row["returned"]) for row in negatives),
                           "excluded_results": sum(len(row["forbidden_results"]) for row in search_results),
                           "unrelated_false_hits": sum(len(row["returned"]) for row in search_results if row["case"].startswith("unrelated")),
                           "wrong_project_leaks": sum(path.startswith("03-Projects/") and path.split("/")[1] != row["project"]
                                                      for row in search_results for path in row["returned"]),
                           "wrong_project_irrelevant_results": sum(len(row["returned"]) for row in search_results
                                                                  if row["case"] == "wrong_project"),
                           "latency_ms": _milliseconds(search_times)},
                "context": {"budgets": context_results, "latency_ms": _milliseconds(context_times)},
                "tool_schemas": schemas}
