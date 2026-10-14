"""``moi rebuild`` and ``moi index ...``: the index's owner commands (v0.5 C5; amendment M3).

    moi rebuild [--no-fidelity]        build a new index from the shelf alone, swap it in, then the F2 full sweep
    moi index update                   embed-on-file: index new and changed records, drop deleted ones
    moi index status                   freshness light, held/pending counts, orphans, model pin (read-only)
    moi index check                    status, exit 1 unless the light is ok (orphans and pin are failures)
    moi index search "question" [--tier curated|raw|all] [--canary] [-k N] [--json]
                                       the raw two-list result (M4's ``moi ask`` adds the log and adjacent slot)

Options for all: ``--shelf-root`` (default: the shelf config's root), ``--index`` (default
``<shelf root>/../memory-index/<shelf name>.sqlite``). The embedder is Ollama at ``MOI_OLLAMA_URL`` (default local).
Everything except ``search`` prints counts and fixed codes only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from core.memory_index import health, indexer, policy, search, store
from core.memory_index.embedder import DEFAULT_URL, Embedder, OllamaEmbedder

EXIT_OK, EXIT_PROBLEM, EXIT_USAGE = 0, 1, 2


def _embedder() -> Embedder:
    return OllamaEmbedder(os.environ.get("MOI_OLLAMA_URL") or DEFAULT_URL)


def _paths(args) -> tuple[Path, Path]:
    if args.shelf_root:
        root = Path(args.shelf_root).expanduser()
    else:
        from core.memory_shelf import config
        root = config.load().shelf_root
    return root, (Path(args.index).expanduser() if args.index else indexer.default_index_path(root))


def _print(doc: dict, out) -> None:
    print(json.dumps(doc, sort_keys=True, indent=1), file=out)


def _render_hits(result: dict, out) -> None:
    print(f"query {result['query_id']}  tier={result['tier']}  meaning={result['status']['meaning']}  "
          f"{result['status']['ms']} ms", file=out)
    for label in ("keyword", "meaning"):
        print(f"\n== {label} ({len(result[label])}) ==", file=out)
        for i, hit in enumerate(result[label], 1):
            print(f"\n[{i}] {hit['file']}:{hit['lines'][0]}-{hit['lines'][1]}  {hit['date']}  {hit['speaker']}  "
                  f"score={hit['score']:.4f}\n    {hit['provenance']}", file=out)
            print("    " + hit["passage"].replace("\n", "\n    "), file=out)
    print(f"\n== adjacent ==\n{result['adjacent']}", file=out)
    if result["unresolved"]:
        print(f"\n== unresolved ({len(result['unresolved'])}) ==", file=out)
        for u in result["unresolved"]:
            print(f"  {u['chunk_id']}  {u['list']}  {u['reason']}", file=out)


def main(argv: Sequence[str] | None = None, *, embedder: Embedder | None = None, out=None) -> int:
    out = out or sys.stdout
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--shelf-root")
    common.add_argument("--index")
    parser = argparse.ArgumentParser(prog="moi", add_help=True)
    sub = parser.add_subparsers(dest="cmd", required=True)
    rb = sub.add_parser("rebuild", parents=[common])
    rb.add_argument("--no-fidelity", action="store_true")
    ix = sub.add_parser("index")
    isub = ix.add_subparsers(dest="action", required=True)
    isub.add_parser("update", parents=[common])
    isub.add_parser("status", parents=[common])
    isub.add_parser("check", parents=[common])
    sq = isub.add_parser("search", parents=[common])
    sq.add_argument("query")
    sq.add_argument("--tier", default="curated", choices=sorted(policy.TIERS))
    sq.add_argument("--canary", action="store_true")
    sq.add_argument("-k", type=int, default=search.DEFAULT_K)
    sq.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    root, index_path = _paths(args)
    emb = embedder or _embedder()

    if args.cmd == "rebuild":
        result = indexer.rebuild(root, emb, index_path=index_path)
        if result.get("code") == indexer.OK and not args.no_fidelity:
            result["fidelity"] = health.fidelity_sweep(root)
        _print(result, out)
        return EXIT_OK if result.get("code") == indexer.OK and not result.get("fidelity", {}).get("failed") else EXIT_PROBLEM
    if args.action == "update":
        result = indexer.update(root, emb, index_path=index_path)
        _print(result, out)
        return EXIT_OK if result.get("code") == indexer.OK else EXIT_PROBLEM
    if args.action in ("status", "check"):
        result = health.status(root, index_path=index_path, embedder=emb)
        _print(result, out)
        return EXIT_OK if args.action == "status" or result["light"] == "ok" else EXIT_PROBLEM
    try:
        result = search.search(args.query, shelf_root=root, embedder=emb, index_path=index_path,
                               caller=policy.Caller(tier=args.tier, canary=args.canary), k=args.k)
    except store.IndexMissing:
        print("no index: run moi rebuild", file=out)
        return EXIT_PROBLEM
    except store.IndexIncompatible:
        print("index made by another version: run moi rebuild", file=out)
        return EXIT_PROBLEM
    except search.QueryRefused as exc:
        print(f"refused: {exc}", file=out)
        return EXIT_PROBLEM
    if args.json:
        _print(result, out)
    else:
        _render_hits(result, out)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
