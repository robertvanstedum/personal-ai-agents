"""FF-07 / #474: language analysis errors and stale transcripts.

Two faults, fixed and tested in Mein Deutsch and Meu Português together:

1. Analysing a pasted (Curator-style) text showed a raw JSON parse error
   ("Unexpected token '<', "<!DOCTYPE "... is not valid JSON"). The domain
   servers now answer every /api/ request in JSON, and the pages read analysis
   answers through one reader that turns an HTML page into a plain sentence.
2. A new session still showed the previous session's transcript. Starting a
   session now clears the earlier transcript state first.

No test here calls a real provider: models are replaced with fakes.
"""

import json
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DE_TEMPLATE = ROOT / "domains" / "german" / "templates" / "german_gesprache.html"
PT_TEMPLATE = ROOT / "domains" / "portuguese" / "templates" / "portuguese_conversas.html"

OWNER = {"X-Minimoi-Auth-Id": "7", "X-Minimoi-User-Tier": "owner"}

# What Robert pasted: a Curator briefing, long and full of markup and URLs,
# not a dialogue with speaker labels.
CURATOR_TEXT = (
    "## Morning briefing — 12 Nov 2026\n"
    + "".join(
        f"**{i}. Fed holds rates as markets weigh data** <b>Markets</b> rallied 3.2% "
        f"https://example.com/news/{i}?utm_source=curator&ref=a%20b — \"quoted\" <i>analysis</i>; "
        "Dive: <a href=\"/app/curator/dive?id=1\">read more</a> {json: [1,2]} ✓\n"
        for i in range(150)
    )
)


def _fake_review(calls):
    def run_review(transcript, persona_name, scene, model, learner_name="Learner"):
        calls.append(transcript)
        return {"feedback": {"overall_summary": "ok", "errors": [], "strengths": [], "next_focus": ""}, "model": model}
    return run_review


# ── Server: German ──────────────────────────────────────────────────────────


@pytest.fixture
def german(monkeypatch, tmp_path):
    from domains.german import html_server

    # Patch the module namespace analyse_session actually runs in: other tests
    # reload german_domain, so ``import german_domain`` can be a newer copy.
    domain_globals = html_server.analyse_session.__globals__

    def patch_domain(name, value):
        monkeypatch.setitem(domain_globals, name, value)

    patch_domain("_SESSIONS_DIR", tmp_path / "sessions")
    patch_domain("_PERSONA_MEMORY_FILE", tmp_path / "persona_memory.json")
    patch_domain("_publish_activity", lambda: None)
    monkeypatch.setattr(html_server, "save_review_session", lambda **kwargs: "session_test")
    html_server.app.config.update(TESTING=True)
    return types.SimpleNamespace(
        server=html_server, patch_domain=patch_domain, client=html_server.app.test_client(), tmp=tmp_path
    )


@pytest.fixture
def portuguese(monkeypatch, tmp_path):
    from domains.portuguese import html_server

    saved = []
    monkeypatch.setattr(html_server, "_pt_save_conversas_session", lambda uid, data: saved.append(data))
    monkeypatch.setattr(html_server, "_save_session", lambda **kwargs: None)
    monkeypatch.setattr(html_server, "_update_persona_progress", lambda *args: None)
    html_server.app.config.update(TESTING=True)
    return types.SimpleNamespace(server=html_server, client=html_server.app.test_client(), saved=saved)


def _patch_review(monkeypatch, which, calls):
    if which == "german":
        import providers.review_router as router
    else:
        sys.path.insert(0, str(ROOT / "domains" / "portuguese"))
        import review_router as router
    monkeypatch.setattr(router, "run_review", _fake_review(calls))


@pytest.mark.parametrize("which,url", [("german", "/api/review"), ("portuguese", "/api/pt/review")])
def test_long_curator_text_is_analysed_and_answered_in_json(request, monkeypatch, which, url):
    app = request.getfixturevalue(which)
    calls = []
    _patch_review(monkeypatch, which, calls)

    response = app.client.post(url, json={"transcript": CURATOR_TEXT, "model": "claude"}, headers=OWNER)

    assert response.status_code == 200
    assert response.content_type.startswith("application/json")
    assert response.get_json()["ok"] is True
    assert calls == [CURATOR_TEXT.strip()]


@pytest.mark.parametrize("which,url", [("german", "/api/review"), ("portuguese", "/api/pt/review")])
def test_text_over_the_limit_is_refused_in_json_without_a_provider_call(request, monkeypatch, which, url):
    app = request.getfixturevalue(which)
    calls = []
    _patch_review(monkeypatch, which, calls)
    too_long = "x" * (app.server.MAX_TRANSCRIPT_CHARS + 1)

    response = app.client.post(url, json={"transcript": too_long}, headers=OWNER)

    assert response.status_code == 413
    payload = response.get_json()
    assert payload["ok"] is False
    assert "too long" in payload["error"]
    assert calls == []


@pytest.mark.parametrize(
    "which,url",
    [("german", "/api/review"), ("german", "/api/analyse-transcript"), ("portuguese", "/api/pt/review")],
)
@pytest.mark.parametrize(
    "body",
    [
        {"data": "not json at all", "content_type": "application/json"},
        {"data": json.dumps(["a", "list"]), "content_type": "application/json"},
        {"data": b"\xff\xfe broken", "content_type": "text/plain"},
    ],
)
def test_malformed_bodies_get_a_json_error_not_an_html_page(request, which, url, body):
    app = request.getfixturevalue(which)

    response = app.client.post(url, headers=OWNER, **body)

    assert response.status_code == 400
    assert response.content_type.startswith("application/json")
    assert response.get_json()["ok"] is False


@pytest.mark.parametrize("which", ["german", "portuguese"])
def test_unknown_api_path_is_json_but_pages_keep_flask_behaviour(request, which):
    app = request.getfixturevalue(which)

    api = app.client.get("/api/does-not-exist")
    page = app.client.get("/does-not-exist")

    assert api.status_code == 404
    assert api.get_json() == {"ok": False, "error": "not found"}
    assert page.status_code == 404
    assert page.content_type.startswith("text/html")


def test_german_unexpected_exception_in_analysis_is_json(german, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(german.server, "analyse_session", boom)
    german.server.app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)
    try:
        response = german.client.post(
            "/api/analyse-transcript", json={"transcript": CURATOR_TEXT, "persona_name": "Tutor"}, headers=OWNER
        )
    finally:
        german.server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=None)

    assert response.status_code == 500
    assert response.content_type.startswith("application/json")
    assert response.get_json() == {"ok": False, "error": "the server could not finish this request (RuntimeError)"}


def test_portuguese_unexpected_exception_is_json(portuguese, monkeypatch):
    def boom():
        raise RuntimeError("unexpected")

    monkeypatch.setattr(portuguese.server, "_request_user_id", boom)
    monkeypatch.setattr(portuguese.server, "_pt_save_conversas_session", lambda *a: None)
    portuguese.server.app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)
    try:
        response = portuguese.client.get("/api/pt/sessions", headers=OWNER)
    finally:
        portuguese.server.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=None)

    assert response.content_type.startswith("application/json")
    assert response.get_json()["ok"] is False


class _FakeAnthropic:
    def __init__(self, text=None, error=None):
        self.text, self.error = text, error

    def module(self):
        fake = self

        class Client:
            def __init__(self, api_key):
                self.messages = self

            def create(self, **kwargs):
                if fake.error:
                    raise fake.error
                return types.SimpleNamespace(content=[types.SimpleNamespace(text=fake.text)])

        return types.SimpleNamespace(Anthropic=Client)


def test_german_tutor_analysis_provider_failure_says_so_and_saves_nothing(german, monkeypatch):
    german.patch_domain("_get_secret", lambda *a, **k: "test-key")
    monkeypatch.setitem(sys.modules, "anthropic", _FakeAnthropic(error=TimeoutError("slow")).module())

    response = german.client.post(
        "/api/analyse-transcript",
        json={"transcript": CURATOR_TEXT, "persona_name": "Tutor", "scene": "human_session"},
        headers=OWNER,
    )

    assert response.status_code == 502
    payload = response.get_json()
    assert payload["ok"] is False and payload["saved"] is False
    assert "TimeoutError" in payload["error"] and "nothing was saved" in payload["error"]
    assert not (german.tmp / "sessions").exists() or not list((german.tmp / "sessions").glob("*.json"))


def test_german_tutor_analysis_without_a_key_saves_nothing(german, monkeypatch):
    german.patch_domain("_get_secret", lambda *a, **k: None)

    response = german.client.post(
        "/api/analyse-transcript", json={"transcript": "Tutor: Hallo\nIch: Hallo"}, headers=OWNER
    )

    assert response.status_code == 502
    assert "not configured" in response.get_json()["error"]
    assert not (german.tmp / "sessions").exists() or not list((german.tmp / "sessions").glob("*.json"))


def test_german_tutor_analysis_tolerates_plain_string_errors_from_the_model(german, monkeypatch):
    review = {
        "overall_summary": "Gut",
        "errors": ["Artikel: der/die", {"explanation": "Verbstellung"}, 7],
        "strengths": "not a list",
        "next_focus": "Dativ",
    }
    german.patch_domain("_get_secret", lambda *a, **k: "test-key")
    monkeypatch.setitem(sys.modules, "anthropic", _FakeAnthropic(text=json.dumps(review)).module())

    response = german.client.post(
        "/api/analyse-transcript",
        json={"transcript": CURATOR_TEXT, "persona_name": "Tutor", "scene": "human_session"},
        headers=OWNER,
    )

    assert response.status_code == 200, response.data[:200]
    payload = response.get_json()
    assert payload["saved"] is True and payload["session_id"].startswith("session_")
    assert payload["feedback"]["strengths"] == []
    memory = json.loads((german.tmp / "persona_memory.json").read_text())
    notes = memory["7_tutor"]["active_memory"]["errors_noted"]
    assert notes == ["Artikel: der/die", "Verbstellung"]


def test_portuguese_save_failure_keeps_the_review_and_is_not_a_provider_error(portuguese, monkeypatch):
    calls = []
    _patch_review(monkeypatch, "portuguese", calls)

    def broken_save(uid, data):
        raise OSError("disk full")

    monkeypatch.setattr(portuguese.server, "_pt_save_conversas_session", broken_save)

    response = portuguese.client.post("/api/pt/review", json={"transcript": "Maria: Olá\nEu: Olá"}, headers=OWNER)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True and payload["saved"] is False
    assert payload["feedback"]["overall_summary"] == "ok"
    assert len(calls) == 1  # one provider call; nothing for the page to retry


# ── Client: the analysis response reader, run in node ───────────────────────


def _reader_block(template: Path) -> str:
    source = template.read_text(encoding="utf-8")
    match = re.search(r"// <analysis-response-reader>.*?\n(.*?)// </analysis-response-reader>", source, re.S)
    assert match, f"reader block missing in {template.name}"
    return match.group(1)


NODE_SCENARIOS = r"""
const { readAnalysisResponse, ANALYSIS_MESSAGES: M } = new Function(BLOCK + '\nreturn { readAnalysisResponse, ANALYSIS_MESSAGES };')();
function res({ status = 200, type = 'application/json', body = '', redirected = false, url = 'https://minimoi.ai/app/german/api/review' }) {
  return {
    status, ok: status >= 200 && status < 300, redirected, url,
    headers: { get: (k) => (k.toLowerCase() === 'content-type' ? type : null) },
    json: async () => JSON.parse(body),
  };
}
async function outcome(r) {
  try { return { data: await readAnalysisResponse(r) }; }
  catch (e) { return { message: e.message, status: e.status, retryable: e.retryable }; }
}
(async () => {
  const doctype = '<!DOCTYPE html><html><head><title>Sign in</title></head></html>';
  const out = {
    login: await outcome(res({ type: 'text/html; charset=utf-8', body: doctype, redirected: true, url: 'https://minimoi.ai/login?next=/app/german/api/review' })),
    html500: await outcome(res({ status: 500, type: 'text/html; charset=utf-8', body: '<!doctype html><title>500</title>' })),
    htmlWithJsonType: await outcome(res({ status: 200, type: 'application/json', body: doctype })),
    html503: await outcome(res({ status: 503, type: 'text/html', body: '<h2>Backend unavailable</h2>' })),
    timeout: await outcome(res({ status: 504, body: JSON.stringify({ error: 'timeout', message: 'No answer within 30 s.' }) })),
    tooLong: await outcome(res({ status: 413, body: JSON.stringify({ ok: false, error: 'transcript too long' }) })),
    nginx413: await outcome(res({ status: 413, type: 'text/html', body: '<html>413</html>' })),
    identity: await outcome(res({ status: 401, body: JSON.stringify({ ok: false, error: 'identity required' }) })),
    provider: await outcome(res({ status: 502, body: JSON.stringify({ ok: false, error: 'xAI refused: rate limit' }) })),
    refusal: await outcome(res({ status: 200, body: JSON.stringify({ ok: false, error: 'model refused' }) })),
    success: await outcome(res({ status: 200, body: JSON.stringify({ ok: true, saved: true, feedback: { overall_summary: 'gut' } }) })),
  };
  out.messages = { signedOut: M.signedOut, tooLong: M.tooLong, timeout: M.timeout };
  console.log(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
@pytest.mark.parametrize("template", [DE_TEMPLATE, PT_TEMPLATE], ids=["german", "portuguese"])
def test_reader_turns_every_html_answer_into_a_plain_sentence(template, tmp_path):
    script = tmp_path / "reader.mjs"
    script.write_text("const BLOCK = " + json.dumps(_reader_block(template)) + ";\n" + NODE_SCENARIOS)
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    m = out["messages"]

    for key in ("login", "html500", "htmlWithJsonType", "html503", "nginx413", "timeout", "tooLong", "identity", "provider", "refusal"):
        assert "Unexpected token" not in out[key]["message"], key
        assert "JSON" not in out[key]["message"], key

    assert out["login"] == {"message": m["signedOut"], "status": 200, "retryable": False}
    assert "HTTP 500" in out["html500"]["message"] and out["html500"]["retryable"] is False
    assert "HTTP 200" in out["htmlWithJsonType"]["message"]
    assert "HTTP 503" in out["html503"]["message"] and out["html503"]["retryable"] is False
    assert out["timeout"]["message"] == m["timeout"] and out["timeout"]["retryable"] is False
    assert out["tooLong"]["message"] == m["tooLong"] and out["nginx413"]["message"] == m["tooLong"]
    assert out["identity"]["message"] == m["signedOut"]
    assert "xAI refused: rate limit" in out["provider"]["message"] and out["provider"]["retryable"] is True
    assert "model refused" in out["refusal"]["message"] and out["refusal"]["retryable"] is False
    assert out["success"] == {"data": {"ok": True, "saved": True, "feedback": {"overall_summary": "gut"}}}


def test_reader_logic_is_the_same_in_both_languages():
    def logic(template):
        block = _reader_block(template)
        block = re.sub(r"const ANALYSIS_MESSAGES = \{.*?\n  \};", "", block, flags=re.S)
        return [line for line in block.splitlines() if line.strip() and not line.strip().startswith("//")]

    assert logic(DE_TEMPLATE) == logic(PT_TEMPLATE)


@pytest.mark.parametrize(
    "template,endpoints",
    [
        (DE_TEMPLATE, ["/api/review", "/api/review", "/api/analyse-transcript"]),
        (PT_TEMPLATE, ["/api/pt/review", "/api/pt/review"]),
    ],
    ids=["german", "portuguese"],
)
def test_every_analysis_call_uses_the_reader(template, endpoints):
    source = template.read_text(encoding="utf-8")
    found = []
    for match in re.finditer(r"fetch\('(/api/(?:pt/)?review|/api/analyse-transcript)'", source):
        found.append(match.group(1))
        following = source[match.end(): match.end() + 900]
        assert "readAnalysisResponse(res)" in following
        assert "await res.json()" not in following.split("readAnalysisResponse(res)")[0]
    assert found == endpoints
    # A timeout or an HTML page is never retried: only err.retryable (502/503).
    assert "res.status >= 500" not in source


# ── Client: a new session starts clean ──────────────────────────────────────


def _function(source: str, name: str) -> str:
    match = re.search(r"  function " + name + r"\(\) \{\n.*?\n  \}\n", source, re.S)
    assert match, name
    return match.group(0)


NEW_SESSION_SCENARIOS = r"""
function run(saved, answer) {
  const calls = { reset: 0, confirm: 0 };
  const cards = { innerHTML: '<div class="session-card">old transcript</div>' };
  const env = {
    window: { confirm: () => { calls.confirm++; return answer; } },
    document: { getElementById: (id) => (id === 'session-cards-list' ? cards : null) },
    resetNachDerSitzung: () => { calls.reset++; },
  };
  const result = new Function('window', 'document', 'resetNachDerSitzung', 'initial',
    'let savedSessions = initial;\n' + FN + '\nconst ok = beginNewSession();\nreturn { ok, savedSessions };'
  )(env.window, env.document, env.resetNachDerSitzung, saved);
  return { ok: result.ok, left: result.savedSessions.length, cards: cards.innerHTML, ...calls };
}
console.log(JSON.stringify({
  analysedOnly: run([{ transcript: 'Sie: alt', feedbackData: { x: 1 } }], false),
  unanalysedYes: run([{ transcript: 'Sie: alt', feedbackData: null }], true),
  unanalysedNo: run([{ transcript: 'Sie: alt', feedbackData: null }], false),
  empty: run([], false),
}));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
@pytest.mark.parametrize("template", [DE_TEMPLATE, PT_TEMPLATE], ids=["german", "portuguese"])
def test_starting_a_new_session_clears_the_previous_transcript(template, tmp_path):
    fn = _function(template.read_text(encoding="utf-8"), "beginNewSession")
    script = tmp_path / "new_session.mjs"
    script.write_text("const FN = " + json.dumps(fn) + ";\n" + NEW_SESSION_SCENARIOS)
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)

    # An analysed session is already saved: it is cleared without asking.
    assert out["analysedOnly"] == {"ok": True, "left": 0, "cards": "", "reset": 1, "confirm": 0}
    # An unanalysed one goes only on a yes; on a no the new session does not start.
    assert out["unanalysedYes"] == {"ok": True, "left": 0, "cards": "", "reset": 1, "confirm": 1}
    assert out["unanalysedNo"]["ok"] is False and out["unanalysedNo"]["left"] == 1
    assert out["unanalysedNo"]["reset"] == 0 and "old transcript" in out["unanalysedNo"]["cards"]
    assert out["empty"] == {"ok": True, "left": 0, "cards": "", "reset": 1, "confirm": 0}


@pytest.mark.parametrize(
    "template,hook,start_alert",
    [
        (DE_TEMPLATE, "window.gespracheBeginNewSession", "alert('Bitte zuerst Persona und Szene auswählen.');"),
        (PT_TEMPLATE, "window.conversasBeginNewSession", "alert('Primeiro selecione uma persona e uma cena.');"),
    ],
    ids=["german", "portuguese"],
)
def test_both_session_starts_clear_first_and_ignore_a_stale_finalize(template, hook, start_alert):
    source = template.read_text(encoding="utf-8")

    # Typed/spoken AI session: cleared right after the persona check.
    after_alert = source.split(start_alert, 1)[1][:200]
    assert "if (!beginNewSession()) return;" in after_alert
    # Realtime voice session: cleared before a controller is created.
    assert f"{hook} = beginNewSession;" in source
    realtime = source.split("startBtn?.addEventListener('click'", 1)[1]
    assert realtime.index(f"!{hook}()") < realtime.index("new RealtimeVoiceController(")
    # A late finalize from an earlier controller cannot reach the new view.
    assert "onFinalize: (result) => { if (controller === thisController) return finalizeAndSave(result); }" in realtime
    assert "onFinalize: finalizeAndSave," not in source
