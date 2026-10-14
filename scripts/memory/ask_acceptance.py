#!/usr/bin/env python3
"""M4 acceptance run (spec v0.5 T-C): Robert's real questions, run through the one Ask procedure, with mechanical checks.

    python3 scripts/memory/ask_acceptance.py [--out DIR] [--max-distance 0.30]

For each question it runs, against the REAL shelf and index (read-only apart from the query log):

* the question verbatim, as Robert's CLI (``robert/cli``);
* the same question through the shared agent tool as ``mc`` and as ``cos`` (identity bound by this script, as a host would),
  and checks all three give identical hits (agent-neutral);
* a few supporting queries (reformulations a synthesis agent would issue), labelled as such.

Checks it reports, none of which trusts the index:

* **pointing**: every hit's passage equals the exact lines of the cited file, re-read here, byte for byte;
* **semantic-only**: a meaning hit that is not in the keyword list and shares no query word with its passage
  (a hit grep would have missed);
* **identical across agents**; **adjacent present and its distance**; **latency**; **held/system turns absent**
  (every hit is a human or assistant turn);
* **log hygiene**: no returned passage appears in the query log.

**Exit status:** 0 only if every required check passes (pointing on every hit, identical full results across agents, only
human/assistant turns, no returned passage in the query log, no unresolved hits on the verbatim questions, and at least one
meaning-only hit across the questions); 1 otherwise, with each failure named. The report separates the **direct** answers
(the question exactly as asked) from the **assisted** ones (supporting queries written into this script by hand, standing in
for the reformulation a deployed agent should do itself): the second kind shows what retrieval can do, not what an agent
did on its own. "Identical across agents" tests the shared function in one process with identities set here, not deployed
MC/CoS. The report records the code revision and the index identity so a run can be reproduced.

The output (JSON and a Markdown report with the passages) contains Robert's own conversation text. It is written under
``--out`` (default ``_working/memory-build/acceptance/<date>/``), which is private and untracked. Nothing is committed or sent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from core.memory_ask import ask as ask_mod                      # noqa: E402
from core.memory_ask import querylog, tool                      # noqa: E402
from core.memory_index import adjacent as adjacent_slot, indexer, policy, store  # noqa: E402
from core.memory_index.embedder import DEFAULT_URL, OllamaEmbedder  # noqa: E402

SHELF = Path("~/minimoi-staging/data/memory").expanduser()
ADJACENT_DEFAULT = adjacent_slot.DEFAULT_MAX_DISTANCE

# Robert's words, verbatim (4 Oct 2026). "window" is a created-date filter, used only where his question names a period.
QUESTIONS = [
    {"id": "Q1", "text": "I'd like to know claude designs that had improvements due to Grok feedback and how often Grok was wrong. "
                         "Not used suggestions are fine.", "window": {},
     "support": ["Grok review adopted not adopted", "Grok was wrong stale outdated review",
                 "design changed after Grok feedback suggestion incorporated"]},
    {"id": "Q2", "text": "I don't have visibility to testing in build. I'd like to know the found and fixed issues during the build, "
                         "not in integration test.", "window": {},
     "support": ["found a bug while building and fixed it", "review caught a defect before the integration test",
                 "test failed then fixed the issue"]},
    {"id": "Q3", "text": "what were some important areas we discussed earlier in the year that we have not acted on, "
                         "but might be relevant now", "window": {"created_before": "2026-07-01"},
     "support": ["decided to do this later deferred parked idea", "we should revisit this later not now",
                 "good idea but not building it yet"]},
]
STOP = set("a an and are as at be but by do for from had has have how i i'd in is it its not of on or our that the their "
           "them there these this to was we were what when which who will with you your often might now some".split())


def provenance(index_path) -> dict:
    """Code revision (and whether the tree is clean), the index's identity and the shelf's size, for reproducing a run."""
    import subprocess
    def git(*a):
        try:
            return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return "unknown"
    info = {"git_head": git("rev-parse", "HEAD"), "git_dirty": bool(git("status", "--porcelain")),
            "adjacent_default_cutoff": ADJACENT_DEFAULT}
    try:
        con = store.connect(index_path, readonly=True)
        meta = store.get_meta(con)
        info["index"] = {"chunks": con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
                         "records": con.execute("SELECT COUNT(*) FROM records").fetchone()[0],
                         **{k: meta.get(k) for k in ("embedder", "embedder_digest", "built_at", "updated_at", "policy_version",
                                                     "chunker_version", "schema_version")}}
        con.close()
    except Exception as exc:                                                  # noqa: BLE001
        info["index"] = {"error": type(exc).__name__}
    return info


def payload(result: dict) -> dict:
    """Everything an asker sees, minus the per-call identity and timing: what must be equal across agents."""
    def hit(h):
        return {k: h.get(k) for k in ("chunk_id", "file", "lines", "date", "speaker", "provenance", "passage", "score",
                                      "label", "basis", "distance", "none")}
    return {"keyword": [hit(h) for h in result["keyword"]], "meaning": [hit(h) for h in result["meaning"]],
            "adjacent": hit(result["adjacent"]), "unresolved": result["unresolved"], "window": result.get("window")}


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in STOP and len(w) > 2}


def verify_pointing(hit: dict) -> bool:
    """Re-read the cited file here and compare the exact lines with the passage, independent of the index and the resolver."""
    try:
        lines = (SHELF / hit["file"]).read_bytes().split(b"\n")
        a, b = hit["lines"]
        return b"\n".join(lines[a - 1:b]).decode("utf-8", errors="replace") == hit["passage"]
    except (OSError, IndexError):
        return False


def slim(hit: dict) -> dict:
    return {k: hit[k] for k in ("chunk_id", "file", "lines", "date", "speaker", "provenance", "score") if k in hit} | \
           {"passage": hit["passage"]}


def ids(result: dict) -> list:
    return [[h["chunk_id"] for h in result["keyword"]], [h["chunk_id"] for h in result["meaning"]],
            result["adjacent"].get("chunk_id")]


def run_one(emb, text, window, caller, via_tool: bool):
    t = time.monotonic()
    if via_tool:
        args = {"query": text, "include_raw": True, **window}
        out = tool.call(args, agent=caller.agent, entry_point=caller.entry_point, shelf_root=SHELF, embedder=emb)
        assert "error" not in out, out
        out["_ms"] = round((time.monotonic() - t) * 1000)
        return out
    out = ask_mod.ask(text, shelf_root=SHELF, embedder=emb, caller=caller, k=5, created_after=window.get("created_after"),
                      created_before=window.get("created_before"))
    out["_ms"] = round((time.monotonic() - t) * 1000)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-distance", type=float, default=None,
                    help="adjacent cutoff for this run only (default: the code's own)")
    args = ap.parse_args()
    if args.max_distance is not None:
        os.environ["MOI_ADJACENT_MAX_DISTANCE"] = str(args.max_distance)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = Path(args.out) if args.out else Path.home() / "Projects/personal-ai-agents/_working/memory-build/acceptance" / day
    out_dir.mkdir(parents=True, exist_ok=True)
    emb = OllamaEmbedder(os.environ.get("MOI_OLLAMA_URL") or DEFAULT_URL)
    index_path = indexer.default_index_path(SHELF)
    report = {"run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "provenance": provenance(index_path),
              "adjacent_max_distance": args.max_distance if args.max_distance is not None else "code default",
              "questions": [],
              "kinds": {"primary": "direct: the question exactly as asked", "support": "assisted: queries written by hand into this "
                        "script, not produced by an agent"}}
    returned_passages: list[str] = []
    for q in QUESTIONS:
        row = {"id": q["id"], "text": q["text"], "window": q["window"], "primary": None, "agent_neutral": None, "support": []}
        robert = run_one(emb, q["text"], q["window"], policy.Caller(tier="all", agent="robert", entry_point="cli"), False)
        mc = run_one(emb, q["text"], q["window"], policy.Caller(agent="mc", entry_point="tool"), True)
        cos = run_one(emb, q["text"], q["window"], policy.Caller(agent="cos", entry_point="tool"), True)
        terms = words(q["text"])
        kw_ids = {h["chunk_id"] for h in robert["keyword"]}
        sem_only = [h["chunk_id"] for h in robert["meaning"]
                    if h["chunk_id"] not in kw_ids and not (terms & words(h["passage"]))]
        every = robert["keyword"] + robert["meaning"] + ([robert["adjacent"]] if "chunk_id" in robert["adjacent"] else [])
        returned_passages += [h["passage"] for h in every]
        row["primary"] = {"query_id": robert["query_id"], "ms": robert["_ms"], "window": robert["window"],
                          "keyword": [slim(h) for h in robert["keyword"]], "meaning": [slim(h) for h in robert["meaning"]],
                          "adjacent": (slim(robert["adjacent"]) | {"basis": robert["adjacent"]["basis"],
                                                                    "distance": robert["adjacent"]["distance"]})
                          if "chunk_id" in robert["adjacent"] else robert["adjacent"],
                          "unresolved": len(robert["unresolved"]),
                          "checks": {"pointing_verified": all(verify_pointing(h) for h in every),
                                     "hits": len(every), "speakers": sorted({h["speaker"] for h in every}),
                                     "semantic_only_meaning_hits": sem_only, "meaning_status": robert["status"]["meaning"]}}
        row["agent_neutral"] = {"identical": payload(robert) == payload(mc) == payload(cos), "compared": "full result payloads in one process, identities bound by this script (not deployed agents)", "mc_ms": mc["_ms"], "cos_ms": cos["_ms"]}
        for s in q["support"]:
            r = run_one(emb, s, q["window"], policy.Caller(tier="all", agent="claude", entry_point="synthesis"), False)
            every_s = r["keyword"] + r["meaning"] + ([r["adjacent"]] if "chunk_id" in r["adjacent"] else [])
            returned_passages += [h["passage"] for h in every_s]
            row["support"].append({"query": s, "query_id": r["query_id"], "ms": r["_ms"],
                                   "keyword": [slim(h) for h in r["keyword"]], "meaning": [slim(h) for h in r["meaning"]],
                                   "adjacent": slim(r["adjacent"]) if "chunk_id" in r["adjacent"] else r["adjacent"],
                                   "pointing_verified": all(verify_pointing(h) for h in every_s)})
        report["questions"].append(row)
    log_text = querylog.path_for(index_path).read_text(encoding="utf-8")
    report["log_hygiene"] = {"returned_passage_in_log": any(p and p in log_text for p in returned_passages),
                             "log_lines_total": len(log_text.splitlines())}
    (out_dir / "acceptance_run.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    os.chmod(out_dir / "acceptance_run.json", 0o600)

    def block(h, label):
        text = h["passage"].strip()
        text = text if len(text) < 700 else text[:700] + " ..."
        return (f"- **{label}** `{h['file']}:{h['lines'][0]}-{h['lines'][1]}` · {h['date']} · {h['speaker']} · "
                f"{h['provenance']} · score {h['score']}\n\n  > " + text.replace("\n", "\n  > ") + "\n")
    md = [f"# M4 acceptance run, {report['run_at']}\n", f"Adjacent cutoff: {report['adjacent_max_distance']}\n"]
    for q in report["questions"]:
        p = q["primary"]
        md += [f"\n## {q['id']}: {q['text']}\n", f"Window: {q['window'] or 'none'} · query `{p['query_id']}` · {p['ms']} ms · "
               f"checks: {json.dumps(p['checks'])} · agent-neutral: {json.dumps(q['agent_neutral'])}\n", "\n### keyword\n"]
        md += [block(h, f"K{i}") for i, h in enumerate(p["keyword"], 1)]
        md += ["\n### meaning\n"] + [block(h, f"M{i}") for i, h in enumerate(p["meaning"], 1)]
        md += ["\n### adjacent\n", block(p["adjacent"], "A") if "file" in p["adjacent"] else f"- {p['adjacent']}\n"]
        for s in q["support"]:
            md += [f"\n### supporting query: {s['query']}  (`{s['query_id']}`, {s['ms']} ms, pointing_verified={s['pointing_verified']})\n"]
            md += [block(h, f"K{i}") for i, h in enumerate(s["keyword"], 1)]
            md += [block(h, f"M{i}") for i, h in enumerate(s["meaning"], 1)]
    md += [f"\n## Log hygiene\n\n{json.dumps(report['log_hygiene'])}\n"]
    (out_dir / "acceptance_run.md").write_text("\n".join(md), encoding="utf-8")
    os.chmod(out_dir / "acceptance_run.md", 0o600)
    failures = []
    for q in report["questions"]:
        c = q["primary"]["checks"]
        if not c["pointing_verified"]:
            failures.append(f"{q['id']}: a hit does not match its cited lines")
        if not q["agent_neutral"]["identical"]:
            failures.append(f"{q['id']}: results differ between robert, mc and cos")
        if not set(c["speakers"]) <= {"human", "assistant"}:
            failures.append(f"{q['id']}: a non-dialogue turn was returned")
        if q["primary"]["unresolved"]:
            failures.append(f"{q['id']}: {q['primary']['unresolved']} unresolved hits")
        if c["meaning_status"] != "ok":
            failures.append(f"{q['id']}: meaning search was {c['meaning_status']}")
        failures += [f"{q['id']}: supporting query '{s['query']}' has a hit that does not match its cited lines"
                     for s in q["support"] if not s["pointing_verified"]]
    if report["log_hygiene"]["returned_passage_in_log"]:
        failures.append("a returned passage appears in the query log")
    if not any(q["primary"]["checks"]["semantic_only_meaning_hits"] for q in report["questions"]):
        failures.append("no meaning-only hit in any question (spec T-C wants at least one)")
    report["failures"] = failures
    (out_dir / "acceptance_run.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    md.insert(2, f"Code {report['provenance']['git_head'][:10]}{' (dirty)' if report['provenance']['git_dirty'] else ''} · "
                 f"index {json.dumps(report['provenance']['index'])}\n\n**Failures: {failures or 'none'}**\n")
    (out_dir / "acceptance_run.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({"out": str(out_dir), "failures": failures, "questions": [{"id": q["id"], "ms": q["primary"]["ms"], **q["primary"]["checks"],
                                                           "identical_across_agents": q["agent_neutral"]["identical"]}
                                                          for q in report["questions"]], "log_hygiene": report["log_hygiene"]},
                     indent=1))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
