"""Isolated Graphify adapter. Called only by HiveMind, never imports project code."""
import contextlib
import json
from pathlib import Path
import sys


def offline_guard(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.system"}:
        raise RuntimeError("Network and process execution are disabled in the code indexer")


def main():
    sys.addaudithook(offline_guard)
    request = json.loads(sys.stdin.read())
    # Diagnostics cannot corrupt the JSON protocol returned to HiveMind.
    with contextlib.redirect_stdout(sys.stderr):
        import networkx as nx
        from graphify.extract import extract
        from graphify.build import build_from_json
        from graphify.serve import _query_graph_text
        if request["operation"] == "build":
            source = Path(request["source"])
            paths = [source / name for name in request["files"]]
            raw = extract(paths, root=source, cache_root=Path(request["cache"]), parallel=False)
            graph = build_from_json(raw, directed=True, root=source)
            result = {"graph": nx.node_link_data(graph, edges="links"),
                      "nodes": graph.number_of_nodes(), "edges": graph.number_of_edges()}
        else:
            data = json.loads(Path(request["index"]).read_text(encoding="utf-8"))
            graph = nx.node_link_graph(data["graph"], edges="links")
            output = _query_graph_text(graph, request["query"], depth=1, token_budget=8000)
            # Keep source-bearing graph records, not upstream budget claims: our
            # caller applies a hard byte ceiling and marks omitted records.
            nodes = [line for line in output.splitlines() if line.startswith("NODE ")]
            edges = [line for line in output.splitlines() if line.startswith("EDGE ")]
            # Put relationships near definitions so small briefs aren't consumed
            # entirely by node names. Upstream orders seed nodes first.
            lines = []
            for i in range(max(len(nodes), len(edges))):
                if i < len(nodes):
                    lines.append(nodes[i])
                if i < len(edges):
                    lines.append(edges[i])
            result = {"lines": lines, "matched": bool(lines)}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
