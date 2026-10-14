"""M4 Ask: the one procedure, the adjacent slot, the date window, the query log, the shared tool, the citation rule, the
CLI and the canary check. Synthetic records only; the embedder is the deterministic fake."""
from __future__ import annotations

import io
import json
import os
import stat
from datetime import datetime, timezone

import pytest

from core.memory_ask import ask as ask_mod
from core.memory_ask import canary_check, citation, cli, querylog, tool
from core.memory_index import indexer, policy
from core.memory_shelf import canary as shelf_canary
from tests.memory_index.helpers import FakeEmbedder, add_record, main_path, make_shelf

NOW = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)
clock = lambda: NOW  # noqa: E731


@pytest.fixture
def shelf_world(tmp_path):
    shelf = make_shelf(tmp_path)
    ids = {
        "bread": add_record(shelf, "grok", "g-bread", [
            ("human", "How do I make my sourdough rise better?"),
            ("assistant", "Feed the starter twice a day and proof the dough somewhere warm.")], created="2026-02-10T10:00:00Z"),
        "bread2": add_record(shelf, "grok", "g-bread2", [
            ("human", "Why did my loaf collapse after baking?"),
            ("assistant", "The dough over-proofed, so the yeast had nothing left to rise on.")], created="2026-09-20T10:00:00Z"),
        "sky": add_record(shelf, "grok", "g-sky", [
            ("human", "Which telescope should I buy for looking at planets?"),
            ("assistant", "A 6 inch Dobsonian shows Jupiter's moons and Saturn's rings.")], created="2026-03-01T10:00:00Z"),
        "other": add_record(shelf, "claude-ai", "ca-yeast", [
            ("human", "Notes on baking: yeast and rise timing for a weekend schedule."),
            ("assistant", "Keep the dough warm; a slow rise gives the best flavour.")], normalizer=2,
            created="2026-04-02T10:00:00Z"),
        "held": add_record(shelf, "codex", "c-old", [
            ("human", "APPROVAL REVIEW prompt that must never be retrieved sourdough"), ("assistant", "decision json")],
            normalizer=2),
    }
    emb = FakeEmbedder()
    assert indexer.update(shelf.root, emb, min_free_bytes=0)["code"] == "ok"
    return shelf, ids, emb


def run(shelf, emb, query, **kw):
    kw.setdefault("caller", policy.Caller(tier="all"))
    kw.setdefault("clock", clock)
    return ask_mod.ask(query, shelf_root=shelf.root, embedder=emb, **kw)


def log_rows(shelf):
    return [json.loads(l) for l in querylog.path_for(indexer.default_index_path(shelf.root)).read_text().splitlines()]


# ── the procedure and the log ────────────────────────────────────────────────

def test_ask_returns_both_lists_the_adjacent_slot_and_logs_one_line_without_passages(shelf_world):
    shelf, ids, emb = shelf_world
    r = run(shelf, emb, "sourdough starter", k=1)
    assert set(r) >= {"query_id", "caller", "tier", "window", "keyword", "meaning", "adjacent", "unresolved", "status"}
    assert r["status"]["log"] == "ok" and r["status"]["tiers"] == ["curated", "raw"]
    rows = log_rows(shelf)
    assert len(rows) == 1 and rows[0]["event"] == "query" and rows[0]["query"] == "sourdough starter"
    assert rows[0]["caller"] == {"agent": "robert", "entry_point": "cli"}
    assert rows[0]["hits"]["keyword"] == [h["chunk_id"] for h in r["keyword"]]
    blob = json.dumps(rows)
    texts = [h["passage"] for h in r["keyword"] + r["meaning"]] + [r["adjacent"].get("passage", "")]
    assert texts and all(t and t not in blob for t in texts if t)             # chunk ids only, never a returned passage
    assert "passage" not in blob
    mode = stat.S_IMODE(os.stat(querylog.path_for(indexer.default_index_path(shelf.root))).st_mode)
    assert mode == 0o600


def test_the_query_text_is_scrubbed_before_it_is_logged(shelf_world):
    shelf, _, emb = shelf_world
    run(shelf, emb, "sourdough 4111 1111 1111 1111 sk-ant-FAKEFAKEFAKE12345", k=1)
    row = log_rows(shelf)[0]
    assert "4111 1111 1111 1111" not in row["query"] and "FAKEFAKEFAKE" not in row["query"] and row["sanitized"] is True


def test_robert_cos_and_mc_get_identical_results_and_the_log_tells_them_apart(shelf_world):
    shelf, _, emb = shelf_world
    got = {}
    for agent, entry in (("robert", "cli"), ("cos", "chat"), ("mc", "tool")):
        r = run(shelf, emb, "yeast rise baking", caller=policy.Caller(tier="all", agent=agent, entry_point=entry), k=2)
        got[agent] = ([h["chunk_id"] for h in r["keyword"]], [h["chunk_id"] for h in r["meaning"]],
                      r["adjacent"].get("chunk_id"))
    assert got["robert"] == got["cos"] == got["mc"]
    assert [r["caller"]["agent"] for r in log_rows(shelf)] == ["robert", "cos", "mc"]


def test_held_records_and_system_turns_never_come_back_through_ask(shelf_world):
    shelf, ids, emb = shelf_world
    r = run(shelf, emb, "APPROVAL REVIEW sourdough", k=5)
    shown = {h["record_id"] for h in r["keyword"] + r["meaning"]}
    assert ids["held"] not in shown and (r["adjacent"].get("record_id") != ids["held"])


def test_a_log_that_cannot_be_written_does_not_cost_the_answer_and_says_so(shelf_world, tmp_path):
    shelf, _, emb = shelf_world
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    r = run(shelf, emb, "sourdough", log_path=blocker / "ask-log.jsonl")
    assert r["keyword"] and r["status"]["log"] == "failed"


@pytest.mark.parametrize("query,k", [("", 3), ("   ", 3), ("x", 0), ("x", 6), ("x", True)])
def test_bad_queries_are_refused_before_anything_is_searched_or_logged(shelf_world, query, k):
    shelf, _, emb = shelf_world
    with pytest.raises(ask_mod.BadQuery):
        run(shelf, emb, query, k=k)
    assert not querylog.path_for(indexer.default_index_path(shelf.root)).exists()


# ── the adjacent slot ────────────────────────────────────────────────────────

def test_the_adjacent_hit_is_one_labelled_result_from_another_source_not_in_the_lists(shelf_world, monkeypatch):
    shelf, ids, emb = shelf_world
    monkeypatch.setenv("MOI_ADJACENT_MAX_DISTANCE", "0.99")
    r = run(shelf, emb, "sourdough starter dough warm", k=1)
    slot = r["adjacent"]
    listed = {h["record_id"] for h in r["keyword"] + r["meaning"]}
    assert slot["label"] == "Adjacent" and slot["basis"] == "other_source" and slot["record_id"] == ids["other"]
    assert slot["record_id"] not in listed and slot["passage"] and slot["provenance"]
    assert [k for k in r if k.startswith("adjacent")] == ["adjacent"]                  # one slot, never a list


def test_the_slot_is_empty_and_says_why_when_nothing_close_enough_is_elsewhere(shelf_world, monkeypatch):
    shelf, _, emb = shelf_world
    monkeypatch.setenv("MOI_ADJACENT_MAX_DISTANCE", "0.0")
    assert run(shelf, emb, "sourdough starter", k=1)["adjacent"] == {"none": "nothing_close_enough_elsewhere"}


def test_no_results_means_nothing_is_adjacent(shelf_world):
    shelf, _, emb = shelf_world
    r = run(shelf, emb, "zzzunmatchedtermzzz", caller=policy.Caller(tier="curated"))
    assert r["keyword"] == [] and r["adjacent"] == {"none": "no_results"}


def test_with_the_embedder_down_the_slot_is_empty_not_a_keyword_hit(shelf_world):
    shelf, _, _ = shelf_world
    r = run(shelf, FakeEmbedder(down=True), "sourdough")
    assert r["status"]["meaning"] == "embedder_unavailable" and r["adjacent"] == {"none": "meaning_unavailable"}


def test_an_older_thread_of_the_same_source_can_be_adjacent_when_nothing_else_differs(tmp_path, monkeypatch):
    shelf = make_shelf(tmp_path)
    near = add_record(shelf, "grok", "a", [("human", "sourdough starter question"), ("assistant", "feed it daily")],
                      created="2026-09-25T10:00:00Z")
    same_month = add_record(shelf, "grok", "b", [("human", "loaf rise question"), ("assistant", "dough warm rise")],
                            created="2026-09-26T10:00:00Z")
    old = add_record(shelf, "grok", "c", [("human", "yeast rise timing"), ("assistant", "dough proof warm")],
                     created="2026-03-01T10:00:00Z")
    emb = FakeEmbedder()
    indexer.update(shelf.root, emb, min_free_bytes=0)
    monkeypatch.setenv("MOI_ADJACENT_MAX_DISTANCE", "0.99")
    slot = run(shelf, emb, "sourdough starter", k=1)["adjacent"]
    assert slot["basis"] == "older_thread" and slot["record_id"] == old != same_month


def test_an_adjacent_candidate_that_fails_the_pointing_rule_is_reported_and_skipped(shelf_world, monkeypatch):
    shelf, ids, emb = shelf_world
    monkeypatch.setenv("MOI_ADJACENT_MAX_DISTANCE", "0.99")
    path = main_path(shelf, ids["other"])
    path.write_bytes(path.read_bytes().replace(b"slow rise", b"slow  rise"))      # the cited bytes no longer match the index
    r = run(shelf, emb, "sourdough starter dough warm", k=1)
    assert r["adjacent"].get("record_id") != ids["other"]
    assert {u["list"] for u in r["unresolved"]} >= {"adjacent"}
    assert all(set(u) == {"chunk_id", "list", "reason"} for u in r["unresolved"])


# ── the date window ──────────────────────────────────────────────────────────

def test_a_created_window_limits_both_lists_and_the_slot(shelf_world, monkeypatch):
    shelf, ids, emb = shelf_world
    monkeypatch.setenv("MOI_ADJACENT_MAX_DISTANCE", "0.99")
    r = run(shelf, emb, "dough rise loaf", created_after="2026-01-01", created_before="2026-06-01", k=5)
    seen = {h["record_id"] for h in r["keyword"] + r["meaning"]} | {r["adjacent"].get("record_id")}
    assert ids["bread"] in seen and ids["other"] in seen
    assert ids["bread2"] not in seen                                           # created 20 Sep
    assert r["window"] == {"created_after": "2026-01-01", "created_before": "2026-06-01"}
    late = run(shelf, emb, "dough rise loaf", created_after="2026-09-01", k=5)
    shown = {h["record_id"] for h in late["keyword"] + late["meaning"]}
    assert shown and shown <= {ids["bread2"]}


@pytest.mark.parametrize("bad", ["2026-1-1", "yesterday", "2026-01-01T00:00:00Z", "'; DROP TABLE chunks;--"])
def test_a_malformed_date_is_refused(shelf_world, bad):
    shelf, _, emb = shelf_world
    from core.memory_index.search import QueryRefused
    with pytest.raises(QueryRefused) as exc:
        run(shelf, emb, "dough", created_after=bad)
    assert exc.value.code == "bad_date"


# ── opened, marked, summarised ───────────────────────────────────────────────

def test_opened_and_mark_are_accepted_only_for_what_the_query_returned(shelf_world):
    shelf, _, emb = shelf_world
    idx = indexer.default_index_path(shelf.root)
    r = run(shelf, emb, "sourdough starter", k=1)
    chunk = r["keyword"][0]["chunk_id"]
    ask_mod.opened(r["query_id"], chunk, index_path=idx, clock=clock)
    ask_mod.mark(r["query_id"], "useful", index_path=idx, clock=clock)
    for args, code in (((r["query_id"], "not-a-chunk"), "not_a_hit_of_that_query"), (("nope", chunk), "unknown_query")):
        with pytest.raises(querylog.LogRefused) as exc:
            ask_mod.opened(*args, index_path=idx)
        assert exc.value.code == code
    for qid, value, code in ((r["query_id"], "great", "bad_mark"), ("nope", "useful", "unknown_query")):
        with pytest.raises(querylog.LogRefused) as exc:
            ask_mod.mark(qid, value, index_path=idx)
        assert exc.value.code == code
    s = querylog.summary(querylog.path_for(idx))
    assert s["queries"] == 1 and s["opened"] == 1 and s["marks"] == {"useful": 1, "not": 0}
    assert s["by_caller"] == {"robert/cli": 1}


# ── the shared tool ──────────────────────────────────────────────────────────

def call(shelf, emb, arguments, agent="mc"):
    return tool.call(arguments, agent=agent, entry_point="tool", shelf_root=shelf.root, embedder=emb, clock=clock)


def test_the_tool_spec_is_a_closed_function_schema_with_the_citation_rule(shelf_world):
    spec = tool.SPEC
    assert spec["name"] == "memory_ask" and spec["input_schema"]["additionalProperties"] is False
    assert spec["input_schema"]["required"] == ["query"]
    assert not {"agent", "entry_point", "tier", "scopes", "canary"} & set(spec["input_schema"]["properties"])
    r = call(*shelf_world[::2], {"query": "sourdough"})
    assert "file:line" in r["citation_rule"]


@pytest.mark.parametrize("arguments", [{"query": "x", "agent": "cos"}, {"query": "x", "canary": True}, {"query": "x", "tier": "raw"},
                                       {"query": "x", "scopes": ["robert"]}, {}, {"query": "x", "include_raw": "yes"},
                                       {"query": "x", "created_after": 20260101}, "sourdough", None])
def test_the_model_cannot_pick_an_identity_scope_canary_or_tier(shelf_world, arguments):
    shelf, _, emb = shelf_world
    assert call(shelf, emb, arguments) == {"error": "bad_arguments"}
    assert not querylog.path_for(indexer.default_index_path(shelf.root)).exists()


def test_the_host_binds_the_agent_and_include_raw_chooses_the_tier(shelf_world):
    shelf, _, emb = shelf_world
    curated = call(shelf, emb, {"query": "sourdough"}, agent="cos")
    assert curated["caller"] == {"agent": "cos", "entry_point": "tool"} and curated["tier"] == "curated"
    assert curated["keyword"] == [] and curated["status"]["tiers"] == ["curated"]          # nothing is designated yet
    raw = call(shelf, emb, {"query": "sourdough", "include_raw": True}, agent="mc")
    assert raw["tier"] == "all" and raw["keyword"]
    assert [r["caller"]["agent"] for r in log_rows(shelf)] == ["cos", "mc"]


def test_tool_failures_are_codes_never_exceptions_or_text(tmp_path):
    shelf = make_shelf(tmp_path)
    assert tool.call({"query": "x"}, agent="mc", shelf_root=shelf.root, embedder=FakeEmbedder()) == {"error": "index_missing"}
    assert tool.call({"query": "x"}, agent="Bad Agent!", shelf_root=shelf.root, embedder=FakeEmbedder()) == {"error": "bad_caller"}
    assert tool.call({"query": "  "}, agent="mc", shelf_root=shelf.root, embedder=FakeEmbedder()) == {"error": "empty_query"}


def test_a_pin_mismatch_is_refused_visibly_through_the_tool(shelf_world):
    shelf, _, _ = shelf_world
    out = call(shelf, FakeEmbedder(digest="deadbeef" + "0" * 56), {"query": "sourdough"})
    assert out == {"error": "pin_mismatch"}


# ── the citation rule ────────────────────────────────────────────────────────

def test_a_sentence_is_supported_only_by_a_citation_to_a_passage_this_query_returned(shelf_world):
    shelf, _, emb = shelf_world
    r = run(shelf, emb, "sourdough starter", k=1)
    hit = r["keyword"][0]
    f, (a, b) = hit["file"], hit["lines"]
    text = (f"The starter is fed twice a day [{f}:{a}-{b}]. Grok recommended a cold proof overnight for flavour. "
            f"It also said the oven should be preheated [{f}:9000]. Summary:")
    states = [v["state"] for v in citation.check(text, r)]
    assert states == ["supported", "unsupported", "invalid_citation", "exempt"]
    marked = citation.mark_unsupported(text, r)
    assert marked.count("[unsupported") == 2 and "Grok recommended a cold proof" in marked


def test_a_citation_to_a_file_the_query_did_not_return_is_invalid(shelf_world):
    shelf, _, emb = shelf_world
    r = run(shelf, emb, "sourdough starter", k=1)
    assert citation.check("Something else entirely was said somewhere [elsewhere/other.md:1-2].", r)[0]["state"] == "invalid_citation"


# ── the CLI ──────────────────────────────────────────────────────────────────

def cli_run(shelf, emb, *argv):
    out = io.StringIO()
    code = cli.main([*argv, "--shelf-root", str(shelf.root)], embedder=emb, out=out, clock=clock)
    return code, out.getvalue()


def test_moi_ask_prints_both_lists_and_the_adjacent_slot_and_hints_at_raw(shelf_world):
    shelf, _, emb = shelf_world
    code, text = cli_run(shelf, emb, "sourdough starter", "--raw", "-k", "1")
    assert code == 0 and "== keyword (1) ==" in text and "== meaning (1) ==" in text and "== Adjacent ==" in text
    assert "robert/cli" in text and "log=ok" in text
    code, text = cli_run(shelf, emb, "sourdough starter")
    assert code == 0 and "Nothing in the curated record. Add --raw" in text


def test_moi_ask_mark_opened_summary_and_usage(shelf_world):
    shelf, _, emb = shelf_world
    code, text = cli_run(shelf, emb, "sourdough starter", "--raw", "--json", "-k", "1")
    result = json.loads(text)
    chunk = result["keyword"][0]["chunk_id"]
    assert cli_run(shelf, emb, "--opened", result["query_id"], chunk)[0] == 0
    assert cli_run(shelf, emb, "--mark", result["query_id"], "useful")[0] == 0
    code, text = cli_run(shelf, emb, "--mark", result["query_id"], "meh")
    assert code == 1 and "bad_mark" in text
    code, text = cli_run(shelf, emb, "--log-summary")
    assert json.loads(text)["marks"]["useful"] == 1
    assert cli_run(shelf, emb)[0] == 2


def test_moi_ask_is_always_the_owner_and_cannot_be_told_to_be_an_agent(shelf_world):
    """Host-bound identity (Codex lane, 5 Oct): the CLI no longer takes --agent or --entry-point; an agent's identity comes
    from its token at the Ask service."""
    shelf, _, emb = shelf_world
    assert cli_run(shelf, emb, "sourdough", "--raw", "--agent", "mc")[0] == 2
    assert cli_run(shelf, emb, "sourdough", "--raw", "--entry-point", "tool")[0] == 2
    assert not querylog.path_for(indexer.default_index_path(shelf.root)).exists()
    assert cli_run(shelf, emb, "sourdough", "--raw")[0] == 0
    assert log_rows(shelf)[0]["caller"] == {"agent": "robert", "entry_point": "cli"}


# ── F3: the canary is findable by Ask ────────────────────────────────────────

def canary_world(tmp_path, day="2026-10-04"):
    shelf = make_shelf(tmp_path)
    add_record(shelf, "canary", shelf_canary.canary_id(day), [(s, t) for s, t in shelf_canary.TURNS], kind="canary")
    emb = FakeEmbedder()
    indexer.update(shelf.root, emb, min_free_bytes=0)
    return shelf, emb


def test_the_days_canary_is_found_by_ask_and_the_check_does_not_touch_the_query_log(tmp_path):
    shelf, emb = canary_world(tmp_path)
    verdict = canary_check.check("2026-10-04", shelf_root=shelf.root, embedder=emb)
    assert verdict["state"] == "found" and verdict["keyword"] is True
    assert not querylog.path_for(indexer.default_index_path(shelf.root)).exists()
    code, text = cli_run(shelf, emb, "--canary-check", "2026-10-04")
    assert code == 0 and '"state": "found"' in text


def test_a_missing_canary_or_a_missing_index_is_loud(tmp_path):
    shelf, emb = canary_world(tmp_path)
    assert canary_check.check("2026-10-05", shelf_root=shelf.root, embedder=emb)["state"] == "missing"
    assert cli_run(shelf, emb, "--canary-check", "2026-10-05")[0] == 1
    empty = make_shelf(tmp_path / "x")
    assert canary_check.check("2026-10-04", shelf_root=empty.root, embedder=emb)["state"] == "unavailable"


def test_ordinary_asks_never_see_the_canary(tmp_path):
    shelf, emb = canary_world(tmp_path)
    r = ask_mod.ask("canary memory path conversation", shelf_root=shelf.root, embedder=emb, caller=policy.Caller(tier="all"))
    assert r["keyword"] == [] and r["meaning"] == [] and r["adjacent"] == {"none": "no_results"}


# ── citation ranges (Codex review of 974fb204, P1): a citation must be a real range inside a returned passage ──────────

PASSAGE = {"keyword": [{"file": "s/source.md", "lines": [10, 20]}], "meaning": [], "adjacent": {"none": "no_results"}}


@pytest.mark.parametrize("cite,state", [
    ("s/source.md:10-20", "supported"),            # exactly the returned passage
    ("s/source.md:12-15", "supported"),            # a range inside it
    ("s/source.md:10", "supported"),               # first line
    ("s/source.md:20", "supported"),               # last line
    ("s/source.md:19-12", "invalid_citation"),     # reversed, both ends inside (Codex's reproduction)
    ("s/source.md:999-1", "invalid_citation"),     # reversed and outside
    ("s/source.md:20-10", "invalid_citation"),
    ("s/source.md:10-21", "invalid_citation"),     # runs one past the end
    ("s/source.md:9-20", "invalid_citation"),      # starts one before
    ("s/source.md:0", "invalid_citation"),         # there is no line zero
    ("s/source.md:0-15", "invalid_citation"),
    ("s/source.md:999999999", "invalid_citation"),
    ("s/other.md:10-20", "invalid_citation"),      # right lines, a file this query did not return
])
def test_a_citation_is_supported_only_by_a_real_range_inside_a_returned_passage(cite, state):
    text = f"This claim says something specific about the passage {cite}"
    assert citation.check(text, PASSAGE)[0]["state"] == state


def test_two_citations_in_one_sentence_must_both_be_real_ranges():
    claim = "The starter is fed twice a day and proofed warm"
    assert citation.check(f"{claim} s/source.md:10-20 and s/source.md:12", PASSAGE)[0]["state"] == "supported"
    assert citation.check(f"{claim} s/source.md:10-20 and s/source.md:19-12", PASSAGE)[0]["state"] == "invalid_citation"


def test_mark_unsupported_marks_a_reversed_citation():
    marked = citation.mark_unsupported("The starter is fed twice a day and proofed warm s/source.md:19-12", PASSAGE)
    assert "[unsupported: citation is not a passage this query returned]" in marked


def test_a_returned_passage_with_a_zero_line_cannot_back_a_citation_to_line_zero():
    broken = {"keyword": [{"file": "s/source.md", "lines": [0, 5]}], "meaning": [], "adjacent": {"none": "no_results"}}
    assert citation.check("This claim says something specific about it s/source.md:0-3", broken)[0]["state"] == "invalid_citation"
