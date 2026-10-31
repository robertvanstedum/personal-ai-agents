import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from threading import Event, Thread

import pytest

from core.telegram.feedback_contract import parse_callback, article_url
from core.telegram.feedback_state import FeedbackTracker
from core.telegram.feedback_writer import write_feedback, result
from core.telegram.feedback_handlers import polling_feedback

URL = "https://example.invalid/article"


def query(data="like:3", chat=42, message_id=1):
    return {"data": data, "message": {"message_id": message_id,
            "chat": {"id": chat, "type": "private"}, "text": "Synthetic title",
            "entities": [{"type": "text_link", "url": URL}]}}


@pytest.mark.parametrize("data", [None, "", "like", "like:0", "like:-1", "like:1:2", "other:1", [], "like:1.2", "like:"+"1"*13])
def test_invalid_actions_or_ranks_refuse(data):
    with pytest.raises(ValueError): parse_callback(query(data))


@pytest.mark.parametrize("action", ["like", "dislike", "save"])
def test_legacy_payload_retains_rank_and_exact_url(action):
    parsed = parse_callback(query(f"{action}:03"))
    assert (parsed.action, parsed.rank, parsed.expected_url) == (action, 3, URL)
    assert parsed.key != parse_callback(query(f"{action}:03", chat=43)).key


@pytest.mark.parametrize("entities", [[], [{"type": "text_link", "url": "javascript:alert(1)"}],
    [{"type": "text_link", "url": URL}, {"type": "text_link", "url": URL+"/other"}],
    3, [{"type": "url", "offset": -1, "length": 2}], [{"type": "url", "offset": 0, "length": 999}]])
def test_missing_or_ambiguous_identity_refuses(entities):
    message = query()["message"]; message["entities"] = entities
    with pytest.raises(ValueError): article_url(message)


def test_actual_telegram_utf16_offsets_preserve_url():
    text = "😀 See " + URL
    offset = len("😀 See ".encode("utf-16-le")) // 2
    msg = NS(text=text, entities=(NS(type="url", offset=offset, length=len(URL)),))
    assert article_url(msg) == URL
    msg.entities = (NS(type="url", offset=1, length=1),)
    with pytest.raises(ValueError): article_url(msg)


def writer_root(tmp_path):
    script = tmp_path / "domains/curator/curator_feedback.py"
    script.parent.mkdir(parents=True); script.write_text("# synthetic")
    return tmp_path


def test_writer_uses_explicit_cli_contract_and_ignores_untrusted_stderr(tmp_path):
    root = writer_root(tmp_path); calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs)); return NS(returncode=0, stderr=b"\xff")
    answer = write_feedback("like", 3, URL, root=root, run=run)
    assert answer["success"] and answer["outcome"] == "confirmed"
    args, kwargs = calls[0]
    assert args[2:] == ["like", "3", "--channel", "telegram", "--text", "liked from Telegram", "--expected-url", URL]
    assert kwargs == {"capture_output": True, "cwd": str(root), "timeout": 30}


def test_writer_missing_script_refuses_before_process(tmp_path):
    answer = write_feedback("like", 3, URL, root=tmp_path, run=lambda *a, **k: pytest.fail("process"))
    assert answer["outcome"] == "refused"


@pytest.mark.parametrize("failure", ["return", "raise"])
def test_persisted_then_failed_is_uncertain_and_never_automatically_replayed(tmp_path, failure):
    root = writer_root(tmp_path); tracker = FeedbackTracker(); count = []
    def run(*args, **kwargs):
        count.append(1); (tmp_path / "effect").write_text("persisted")
        if failure == "raise": raise TimeoutError()
        return NS(returncode=1, stderr=b"\xff")
    for _ in range(2):
        answer = tracker.execute("key", lambda: write_feedback("like", 3, URL, root=root, run=run))
        assert not answer["success"] and answer["outcome"] == "uncertain"
    assert len(count) == 1 and (tmp_path / "effect").read_text() == "persisted"


def test_proven_prewrite_refusal_allows_retry_but_success_deduplicates():
    tracker = FeedbackTracker()
    assert tracker.execute("key", lambda: result("refused", "missing script"))["outcome"] == "refused"
    assert tracker.execute("key", lambda: result("confirmed", "stored"))["success"]
    assert tracker.execute("key", lambda: pytest.fail("duplicate"))["duplicate"]


def test_concurrent_callback_is_pending_without_second_writer():
    tracker = FeedbackTracker(); entered, release = Event(), Event(); answers = []
    def writer():
        entered.set(); assert release.wait(3); return result("confirmed", "stored")
    thread = Thread(target=lambda: answers.append(tracker.execute("key", writer)))
    thread.start()
    try:
        assert entered.wait(3)
        assert tracker.execute("key", lambda: pytest.fail("second writer"))["outcome"] == "pending"
    finally:
        release.set(); thread.join(3)
    assert answers[0]["success"]


def test_success_survives_failed_message_edit():
    raw = query(); message = NS(**raw["message"], reply_markup=None)
    obj = NS(data="like:3", message=message, answer=AsyncMock(),
             edit_message_text=AsyncMock(side_effect=RuntimeError("transport")))
    tracker = FeedbackTracker(); calls = []
    def writer(*args): calls.append(args); return result("confirmed", "stored")
    asyncio.run(polling_feedback(obj, tracker, writer))
    asyncio.run(polling_feedback(obj, tracker, writer))
    assert calls == [("like", 3, URL)]


@pytest.mark.parametrize("expected,current,accepted", [(URL, URL, True), (URL, URL+"/new", False),
                                                      (URL, URL+"/", False)])
def test_actual_cli_rejects_stale_rank_before_provider_or_write(monkeypatch, expected, current, accepted):
    import sys
    source = Path(__file__).resolve().parents[1] / "domains/curator/curator_feedback.py"
    fn = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == "main")
    article = {"title": "Synthetic", "source": "Source", "category": "other", "url": current}
    calls = []
    namespace = {"sys": sys, "parse_curator_output": lambda: {3: article},
                 "record_feedback": lambda *a, **kw: calls.append((a, kw))}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), "exec"), namespace)
    monkeypatch.setattr(sys, "argv", ["curator_feedback.py", "like", "3", "--channel", "telegram",
                                     "--text", "synthetic", "--expected-url", expected])
    if accepted:
        namespace["main"]()
        assert len(calls) == 1 and calls[0][0][3] is article
    else:
        with pytest.raises(SystemExit) as exc: namespace["main"]()
        assert exc.value.code == 2 and calls == []
