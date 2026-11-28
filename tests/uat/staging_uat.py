#!/usr/bin/env python3
"""Automatic UAT for staging (SPEC_AUTOMATIC_UAT v0.1, sections 3 and 4).

Drives dev.minimoi.ai the way Robert does his staging checklist: a real Chromium (Playwright, sync API), at desktop
size (1280x800) and phone size (375x812). It signs in through the staging-only ``POST /uat/sign-in`` door, runs every
non-voice case, and writes ``report.json`` and ``report.md`` with evidence for each case.

Rules it keeps (spec section 3):
* agent replies are checked against facts (ids, key words), never exact wording;
* every run uses new chats and a new meeting named ``uat-<run id>`` with only MC and CoS;
* it never answers "yes" to anything that reaches outside mini-moi: :func:`approval_text` refuses "yes" for every case
  except B1 (a CoS task inside mini-moi), and B5 can only ever answer "no";
* it never starts a voice call or spends voice minutes, and it puts Robert's saved Voice back after A2;
* the sign-in token is read from an environment variable, sent only in the sign-in header, and scrubbed from every
  report.

Exit code: 0 when no case failed or errored, 1 when any did, 2 for a usage or configuration problem.

Playwright is imported only when a run starts, so ``--list`` and the offline tests need no browser.
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"

DEFAULT_BASE_URL = "https://dev.minimoi.ai"
DEFAULT_TOKEN_ENV = "MINIMOI_UAT_TOKEN"
SIGNIN_PATH = "/uat/sign-in"
SIGNIN_HEADER = "X-MiniMoi-UAT-Token"          # minimoi_portal/uat_signin.py HEADER

GUILD = "/guild-next"                            # minimoi_portal/guild_mounts.py NEXT_PREFIX
GUILD_API = GUILD + "/api/v1"
MC_PAGE = GUILD + "/guild/build"
ROOMS_PAGE = GUILD + "/guild/rooms"
RECORDS_API = "/app/records/api/v1"             # pages.py rooms config "api": "/app/records/api"
COS_PAGE = "/app/cos/ui/confer"
COS_UI = "/app/cos/ui"

DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 375, "height": 812}
PHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/18.0 Mobile/15E148 Safari/604.1")

REPLY_TIMEOUT_S = 180                            # per agent reply
POLL_S = 3.0

STATUSES = ("pass", "fail", "skip", "manual", "error")


# ── Results and the case registry ────────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class Result:
    id: str
    title: str
    status: str
    evidence: dict
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "status": self.status, "evidence": self.evidence,
                "seconds": round(self.seconds, 1)}


class CaseFail(Exception):
    """A check that did not hold. Carries the evidence gathered so far."""

    def __init__(self, reason: str, evidence: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence or {}


class CaseSkip(Exception):
    def __init__(self, reason: str, evidence: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence or {}


@dataclasses.dataclass
class Case:
    id: str
    title: str
    fn: Callable | None          # None for cases the runner never automates (manual / not seen)
    kind: str = "auto"           # auto | manual
    manual_note: str = ""


CASES: list[Case] = []


def case(cid: str, title: str):
    def wrap(fn):
        CASES.append(Case(cid, title, fn))
        return fn
    return wrap


def manual_case(cid: str, title: str, note: str):
    CASES.append(Case(cid, title, None, kind="manual", manual_note=note))


def case_ids() -> list[str]:
    return [c.id for c in CASES]


def select_cases(only: str | None) -> list[Case]:
    """The cases to run, in registry order. ``only`` is a comma list of ids; an unknown id is a usage error."""
    if not only:
        return list(CASES)
    wanted = [x.strip().upper() for x in only.split(",") if x.strip()]
    unknown = [w for w in wanted if w not in case_ids()]
    if unknown:
        raise ValueError(f"unknown case id(s): {', '.join(unknown)}; known: {', '.join(case_ids())}")
    return [c for c in CASES if c.id in wanted]


# ── Evidence matchers (pure; unit-tested offline) ────────────────────────────────────────────────────────────────

P_CODE = re.compile(r"\bP-[A-Z0-9]{4}\b")
TASK_ID = re.compile(r"\bTK-cos-[0-9a-f]{6,}\w*", re.I)
PI_ID = re.compile(r"\bPI-2026-\d{4}\b")
INIT_ID = re.compile(r"\bINIT-\d+", re.I)
SHA256_HEX = re.compile(r"\b[0-9a-f]{64}\b")
ROUTE_ID = re.compile(r"\b(?:api|plan|sub|subscription)-(?:openai|xai|anthropic|google|grok)[-\w]*", re.I)
PLATFORM_TROUBLE = re.compile(r"Platform:[^\n]*(?:refused|could not be confirmed|did not finish|failed)", re.I)
# The approval command grammar Records accepts (domains/records/room_approval.py _COMMAND): the whole message.
APPROVAL_COMMAND = re.compile(
    r"^\s*(?:@?(?:mc|cos|master craftsman|chief of staff)\s*[,:]?\s*)?(yes|no)\s+(?:proposal\s+)?p-[a-z0-9]{4}\.?\s*$",
    re.I)

C1_TARGET = "gghhx28k"
C2_TARGETS = ("248w2as6",)
CAREER_IDS = ("gghhx28k", "eaqwy0mm")


def find_p_code(text: str | None) -> str | None:
    m = P_CODE.search(text or "")
    return m.group(0) if m else None


def match_b2(reply: str | None) -> bool:
    return "copperleaf" in (reply or "").lower()


def match_b1_confirm(reply: str | None, code: str) -> dict:
    text = reply or ""
    task = TASK_ID.search(text)
    ok = bool(re.search(r"\bConfirmed\s+" + re.escape(code) + r"\b", text, re.I)) and task is not None
    return {"ok": ok, "task_id": task.group(0) if task else None}


def match_b1_second(texts: list[str], dispositions: list[str], first_task: str | None) -> dict:
    """The second ``yes <code>``: "already confirmed" (as a routing note or a reply) and no new task id."""
    already = any("already_confirmed" in (d or "") for d in dispositions) or \
        any("already confirmed" in (t or "").lower() for t in texts)
    tasks = {m.group(0).lower() for t in texts for m in TASK_ID.finditer(t or "")}
    new_tasks = sorted(tasks - ({first_task.lower()} if first_task else set()))
    return {"ok": already and not new_tasks, "already": already, "new_task_ids": new_tasks}


def match_b3(reply: str | None, file_sha256: str | None = None) -> dict:
    text = reply or ""
    pi = PI_ID.search(text)
    parked = "parked" in text.lower()
    hashes = SHA256_HEX.findall(text.lower())
    sha_ok = None if not hashes else (file_sha256 is not None and file_sha256.lower() in hashes)
    init = INIT_ID.search(text)
    ok = bool(pi) and parked and sha_ok is not False and init is None
    return {"ok": ok, "pi_id": pi.group(0) if pi else None, "parked": parked, "sha256_in_reply": sha_ok,
            "init_number": init.group(0) if init else None}


def _norm(s: str) -> str:
    s = (s or "").replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    return re.sub(r"\s+", " ", s).strip()


def match_b4_saved(reply: str | None) -> dict:
    text = reply or ""
    trouble = PLATFORM_TROUBLE.search(text)
    saved = bool(re.search(r"\bsaved\b", text, re.I))
    return {"ok": saved and trouble is None, "saved": saved, "platform_line": trouble.group(0) if trouble else None}


def match_b4_readback(reply: str | None, expected: str) -> bool:
    return _norm(expected) in _norm(reply or "")


def match_b5_declined(reply: str | None) -> bool:
    t = (reply or "").lower()
    return "cancelled" in t or "canceled" in t or "nothing was changed" in t or "declined" in t


def match_c1(reply: str | None) -> dict:
    text = reply or ""
    cited = C1_TARGET in text
    yes_november = bool(re.match(r"\W*yes\b", text, re.I)) and "november" in text.lower()
    return {"ok": cited or yes_november, "cited_target": cited, "yes_with_november": yes_november}


def match_c2(reply: str | None) -> dict:
    text = reply or ""
    low = text.lower()
    hits = [w for w in ("correction", "restudy", "re-study") if w in low]
    build_log = bool(re.search(r"build log\s*#\s*167", low))
    cited = [t for t in C2_TARGETS if t in text]
    career = [c for c in CAREER_IDS if c in text]
    ok = bool(hits or build_log or cited) and not career
    return {"ok": ok, "words": hits, "build_log_167": build_log, "cited": cited, "career_ids_seen": career}


def match_model_text(text: str | None, provider: str) -> bool:
    want = {"xai": "Grok 4.7 · API", "openai": "GPT-6 Astra · Plan"}[provider]
    return _norm(text) == want


def route_ids_in(text: str | None) -> list[str]:
    return sorted({m.group(0) for m in ROUTE_ID.finditer(text or "")})


def issue_line_ok(text: str | None) -> dict:
    """A6: the walk's issues tool gave a real open issue, not a waiting notice or an "off" line."""
    t = text or ""
    m = re.search(r"#(\d+)\s*·\s*issue\s*·\s*open[^\n]*", t)
    waiting = bool(re.search(r"\bW-\d+\b|blocked on you|not on this call|waiting", t, re.I))
    return {"ok": m is not None and not waiting, "first_issue": m.group(0) if m else None, "waiting_like": waiting}


# ── The "never yes outside mini-moi" guard ───────────────────────────────────────────────────────────────────────

YES_ALLOWED = frozenset({"B1"})   # B1 confirms a CoS task inside mini-moi. Nothing else may ever say yes.


class ApprovalRefused(RuntimeError):
    pass


def approval_text(case_id: str, decision: str, code: str) -> str:
    """The only way the runner builds an approval reply. "yes" is refused for every case but B1; B5 is "no" only."""
    decision = (decision or "").strip().lower()
    if decision not in ("yes", "no"):
        raise ApprovalRefused(f"unknown decision {decision!r}")
    if not P_CODE.fullmatch(code or ""):
        raise ApprovalRefused(f"not a card code: {code!r}")
    if decision == "yes" and case_id not in YES_ALLOWED:
        raise ApprovalRefused(f"{case_id} may not answer yes: it could reach outside mini-moi")
    return f"{decision} {code}"


def guard_outgoing(case_id: str, text: str) -> None:
    """Every message the runner posts passes here: a "yes" command from any case outside YES_ALLOWED is refused,
    however it was built."""
    m = APPROVAL_COMMAND.match(text or "")
    if m and m.group(1).lower() == "yes" and case_id not in YES_ALLOWED:
        raise ApprovalRefused(f"{case_id} may not send an approving reply")
    if case_id == "B5" and re.search(r"\b(yes|approve|confirm|go ahead)\b", text or "", re.I) and m is None \
            and re.search(r"P-[A-Z0-9]{4}", text or "", re.I):
        raise ApprovalRefused("B5 may not approve its card in any wording")


# ── Redaction and reports ────────────────────────────────────────────────────────────────────────────────────────

def redact(obj, secrets: list[str]):
    secrets = [s for s in secrets if s]
    if not secrets:
        return obj
    if isinstance(obj, str):
        for s in secrets:
            obj = obj.replace(s, "[redacted]")
        return obj
    if isinstance(obj, dict):
        return {redact(k, secrets): redact(v, secrets) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v, secrets) for v in obj]
    return obj


def summarize(results: list[Result]) -> dict:
    out = {s: 0 for s in STATUSES}
    for r in results:
        out[r.status] = out.get(r.status, 0) + 1
    return out


def exit_code(results: list[Result]) -> int:
    return 1 if any(r.status in ("fail", "error") for r in results) else 0


def _cell(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def _short(v, n=600) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + " …"


def render_md(meta: dict, results: list[Result]) -> str:
    counts = summarize(results)
    lines = [f"# Staging UAT {meta.get('run_id', '')}", "",
             f"- Base URL: {meta.get('base_url', '')}",
             f"- Started {meta.get('started', '')}, finished {meta.get('finished', '')}",
             "- Totals: " + ", ".join(f"{k} {v}" for k, v in counts.items() if v),
             "- The UAT never gives a production go. Production needs a green UAT and Robert's own word.", "",
             "| Id | Case | Status | Seconds | Note |", "|---|---|---|---|---|"]
    for r in results:
        note = r.evidence.get("reason") or r.evidence.get("note") or ""
        lines.append(f"| {r.id} | {_cell(r.title)} | **{r.status}** | {r.seconds:.0f} | {_cell(_short(note, 140))} |")
    bad = [r for r in results if r.status in ("fail", "error")]
    lines += ["", "## Failures", ""]
    if not bad:
        lines.append("None.")
    for r in bad:
        lines += [f"### {r.id} {r.title}: {r.status}", ""]
        for k, v in r.evidence.items():
            if k == "screenshots" and v:
                lines.append(f"- screenshots: {', '.join(v)}")
            else:
                lines.append(f"- {k}: {_short(v)}")
        lines.append("")
    robert = [r for r in results if r.status in ("manual", "skip")]
    lines += ["", "## For Robert", ""]
    if not robert:
        lines.append("Nothing.")
    for r in robert:
        lines.append(f"- {r.id} {r.title} ({r.status}): {r.evidence.get('note') or r.evidence.get('reason') or ''}")
    return "\n".join(lines) + "\n"


def write_reports(out_dir: Path, meta: dict, results: list[Result], secrets: list[str]) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {**meta, "summary": summarize(results), "exit_code": exit_code(results),
            "results": [r.as_dict() for r in results]}
    data = redact(data, secrets)
    jpath, mpath = out_dir / "report.json", out_dir / "report.md"
    jpath.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    safe_results = [Result(**{**r.as_dict(), "evidence": redact(r.evidence, secrets)}) for r in results]
    mpath.write_text(redact(render_md(redact(meta, secrets), safe_results), secrets), encoding="utf-8")
    return jpath, mpath


# ── The browser session ──────────────────────────────────────────────────────────────────────────────────────────

_FETCH_JS = """
async ([method, url, body, headers]) => {
  const h = Object.assign({'Accept': 'application/json'}, headers || {});
  const init = {method, headers: h, credentials: 'same-origin', cache: 'no-store'};
  if (method !== 'GET') { h['Content-Type'] = 'application/json'; init.body = JSON.stringify(body === undefined ? {} : body); }
  const r = await fetch(url, init);
  const t = await r.text();
  let j = null; try { j = JSON.parse(t); } catch (e) {}
  return {status: r.status, body: j, text: j === null ? t.slice(0, 2000) : null};
}
"""


def _key(prefix="uat") -> str:
    return f"{prefix}{uuid.uuid4().hex[:24]}"


class Session:
    """One Playwright browser plus helpers. Every API call runs as ``fetch`` inside a page on the portal's origin, so
    the browser sets Origin and Sec-Fetch-Site exactly as the real page does."""

    def __init__(self, base_url: str, token: str, out_dir: Path, run_id: str, headless: bool = True):
        from playwright.sync_api import sync_playwright
        self.base = base_url.rstrip("/")
        self._token = token
        self.out = out_dir
        self.shots = out_dir / "screenshots"
        self.shots.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=headless)
        self.contexts = []
        self.ctx = self.new_context(DESKTOP)
        self.page = self.ctx.new_page()
        self._guild_csrf = None
        self._cos_csrf = None
        self._cos_page = None
        self.meetings: dict[str, dict] = {}
        self.current_case = "-"

    # contexts and sign-in
    def new_context(self, viewport: dict, phone: bool = False):
        kw = {"viewport": viewport, "bypass_csp": True, "locale": "en-US", "timezone_id": "America/Chicago"}
        if phone:
            kw.update(is_mobile=True, has_touch=True, device_scale_factor=3, user_agent=PHONE_UA)
        ctx = self.browser.new_context(**kw)
        ctx.set_default_timeout(20000)
        self.sign_in(ctx)
        self.contexts.append(ctx)
        return ctx

    def sign_in(self, ctx) -> None:
        r = ctx.request.post(self.base + SIGNIN_PATH, headers={SIGNIN_HEADER: self._token}, data="",
                             max_redirects=0)
        body = None
        try:
            body = r.json()
        except Exception:
            pass
        if r.status != 200 or not (isinstance(body, dict) and body.get("uat") is True):
            raise RuntimeError(f"UAT sign-in refused: HTTP {r.status} (door closed, wrong token or not staging)")

    def close(self) -> None:
        for c in self.contexts:
            try:
                c.close()
            except Exception:
                pass
        try:
            self.browser.close()
        finally:
            self._pw.stop()

    # pages
    def goto(self, page, path: str, ready: str | None = "body[data-ready=true]"):
        page.goto(self.base + path, wait_until="domcontentloaded")
        if ready:
            page.wait_for_selector(ready, state="attached", timeout=30000)
        return page

    def shot(self, page, name: str) -> str:
        fname = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{self.current_case}-{name}") + ".png"
        try:
            page.screenshot(path=str(self.shots / fname), full_page=False)
        except Exception:
            return f"screenshots/{fname} (failed)"
        return f"screenshots/{fname}"

    def fetch(self, page, method: str, path: str, body=None, headers=None) -> dict:
        return page.evaluate(_FETCH_JS, [method, path if path.startswith("http") else self.base + path, body,
                                         headers or {}])

    # Guild (MC) API
    def guild_page(self):
        if not self.page.url.startswith(self.base + GUILD):
            self.goto(self.page, MC_PAGE)
        return self.page

    def guild(self, method: str, path: str, body=None) -> dict:
        page = self.guild_page()
        headers = {}
        if method != "GET":
            if self._guild_csrf is None:
                s = self.fetch(page, "GET", GUILD_API + "/session")
                self._guild_csrf = (s.get("body") or {}).get("csrf_token")
            headers = {"X-CSRF-Token": self._guild_csrf or "", "X-Record-Mode": "on_record"}
        r = self.fetch(page, method, GUILD_API + path, body, headers)
        if r["status"] == 403 and (r.get("body") or {}).get("error") == "csrf" and method != "GET":
            self._guild_csrf = None
            return self.guild(method, path, body)
        return r

    # Records (Guildhall) API
    def records(self, method: str, path: str, body=None) -> dict:
        headers = {} if method == "GET" else {"Idempotency-Key": _key("uat-")}
        return self.fetch(self.guild_page(), method, RECORDS_API + path, body, headers)

    # CoS API
    def cos_page(self):
        if self._cos_page is None:
            self._cos_page = self.ctx.new_page()
            self.goto(self._cos_page, COS_PAGE, ready="meta[name=minimoi-csrf-token]")
            wait_until(lambda: self._cos_page.evaluate("Boolean(window.cosHistory && window.cosHistory.ready)"), 30,
                       "CoS history did not become ready")
            self._cos_csrf = self._cos_page.evaluate(
                "document.querySelector('meta[name=minimoi-csrf-token]').content")
        return self._cos_page

    def cos(self, method: str, path: str, body=None) -> dict:
        page = self.cos_page()
        headers = {} if method == "GET" else {"X-CSRF-Token": self._cos_csrf or ""}
        return self.fetch(page, method, COS_UI + path, body, headers)


def wait_until(fn, timeout_s: float, message: str, every: float = 0.5):
    end = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < end:
        last = fn()
        if last:
            return last
        time.sleep(every)
    raise CaseFail(message)


def need(r: dict, *ok: int, what: str):
    if r["status"] not in ok:
        raise CaseFail(f"{what}: HTTP {r['status']}", {"response": r.get("body") or r.get("text")})
    return r.get("body") or {}


# ── Chat drivers ─────────────────────────────────────────────────────────────────────────────────────────────────

def mc_new_chat(s: Session) -> str:
    body = need(s.guild("POST", "/conversations", {"idempotency_key": _key(), "record_mode": "on_record"}),
                200, 201, what="new MC chat")
    return body["conversation"]["id"]


def mc_agent_notes(s: Session, cid) -> dict:
    r = s.guild("GET", f"/notes?limit=50&conversation={cid}")
    notes = (r.get("body") or {}).get("notes") or []
    return {n.get("id"): n for n in notes if n.get("author_kind") == "agent"}


def mc_ask(s: Session, text: str, label: str) -> dict:
    """A new MC chat, the question typed and sent through the real composer, and MC's reply read from the notes API
    the page itself uses. Returns {conversation_id, reply, note_id, screenshot}."""
    cid = mc_new_chat(s)
    page = s.goto(s.page, f"{MC_PAGE}?c={cid}")
    before = set(mc_agent_notes(s, cid))
    page.fill("#mc-input", text)
    page.click("[data-mc-send]")
    end = time.monotonic() + REPLY_TIMEOUT_S
    while time.monotonic() < end:
        new = {k: v for k, v in mc_agent_notes(s, cid).items() if k not in before}
        if new:
            note = list(new.values())[-1]
            return {"conversation_id": cid, "note_id": note.get("id"), "reply": note.get("text") or "",
                    "screenshot": s.shot(page, label)}
        fail = page.locator("[data-turn-failure], [data-mc-refusal]:not([hidden])")
        if fail.count() and fail.first.is_visible() and (fail.first.inner_text() or "").strip():
            raise CaseFail("MC did not answer", {"conversation_id": cid, "page_says": fail.first.inner_text(),
                                                 "screenshots": [s.shot(page, label + "-failure")]})
        time.sleep(POLL_S)
    raise CaseFail(f"no MC reply within {REPLY_TIMEOUT_S}s", {"conversation_id": cid,
                                                             "screenshots": [s.shot(page, label + "-timeout")]})


def cos_ask(s: Session, text: str, title: str) -> dict:
    """A new CoS conversation and one turn through the API the confer page calls (send, then poll the turn)."""
    conv = need(s.cos("POST", "/history/conversations", {"title": title}), 200, 201, what="new CoS chat")
    cid = conv.get("conversation_id")
    page = s.cos_page()
    device = page.evaluate("localStorage.getItem('minimoi-device') || ''") or str(uuid.uuid4())
    r = s.cos("POST", "/send", {"text": text, "channel": "html_text", "conversation_id": cid, "device": device,
                                "request_id": str(uuid.uuid4()), "attachments": [], "background": True})
    body = r.get("body") or {}
    if r["status"] == 202 and body.get("turn"):
        handle = body["turn"]
        end = time.monotonic() + REPLY_TIMEOUT_S
        while time.monotonic() < end:
            t = s.cos("GET", f"/turn/{handle}")
            tb = t.get("body") or {}
            if tb.get("state") == "done":
                body = tb
                break
            if tb.get("state") == "unknown":
                raise CaseFail("CoS lost the turn", {"conversation_id": cid, "response": tb})
            time.sleep(POLL_S)
        else:
            raise CaseFail(f"no CoS reply within {REPLY_TIMEOUT_S}s", {"conversation_id": cid})
    elif r["status"] != 200:
        raise CaseFail(f"CoS send refused: HTTP {r['status']}", {"conversation_id": cid, "response": body or r["text"]})
    s.goto(page, COS_PAGE, ready="meta[name=minimoi-csrf-token]")
    return {"conversation_id": cid, "turn_id": body.get("turn_id"), "reply": body.get("reply") or "",
            "model_label": body.get("model_label"), "completion": body.get("completion"),
            "uat_marked": conv.get("uat") is True}       # evidence only: CoS marked this chat, so the Vault never takes it


# ── Guildhall drivers ────────────────────────────────────────────────────────────────────────────────────────────

AVAILABLE_REACH = ("here", "reachable", "answering")      # rooms.js participantState


def meeting(s: Session, suffix: str = "") -> dict:
    """The run's meeting (created once): ``uat-<run id>``, members Robert, MC and CoS only. Waits for both partners to
    accept. Returns {id, title, participants}."""
    key = suffix or "main"
    if key in s.meetings:
        return s.meetings[key]
    title = f"uat-{s.run_id}{suffix}"
    made = need(s.records("POST", "/rooms", {"title": title, "purpose": f"Automatic staging UAT, run {s.run_id}. "
                                             "Members: Robert, MC and CoS only.", "mode": "meeting",
                                             "recording_acknowledged": True}), 201, what="create meeting")
    rid = made["result"]["id"]
    for actor in ("mc", "cos"):
        need(s.records("POST", f"/rooms/{rid}/invite", {"actor": actor}), 200, 201, what=f"invite {actor}")

    def ready():
        parts = (s.records("GET", f"/rooms/{rid}/turns").get("body") or {}).get("participants") or []
        by = {p.get("id"): p for p in parts}
        if all((by.get(a) or {}).get("rsvp") == "accepted" for a in ("mc", "cos")):
            return by
        return None
    try:
        by = wait_until(ready, 90, "MC and CoS did not both accept the meeting", every=2)
    except CaseFail as e:
        parts = (s.records("GET", f"/rooms/{rid}/turns").get("body") or {}).get("participants")
        raise CaseFail(e.reason, {"room_id": rid, "participants": parts})
    m = {"id": rid, "title": title, "participants": by}
    s.meetings[key] = m
    return m


def room_post(s: Session, rid: str, text: str, target: str | None = None) -> dict:
    guard_outgoing(s.current_case, text)
    body = {"body": text, "kind": "message"}
    if target:
        body["target"] = target
    return need(s.records("POST", f"/rooms/{rid}/events", body), 201, what="post message")["result"]


def room_share(s: Session, rid: str, path: Path) -> dict:
    data = path.read_bytes()
    res = need(s.records("POST", f"/rooms/{rid}/documents",
                         {"name": path.name, "base64": base64.b64encode(data).decode()}), 201, what="share file")
    return res["result"]


def room_events(s: Session, rid: str, after: int) -> list[dict]:
    return (s.records("GET", f"/rooms/{rid}?after={after}").get("body") or {}).get("events") or []


TERMINAL_BAD = ("failed", "expired", "cancelled", "superseded", "abandoned")


def wait_turn(s: Session, rid: str, seq: int, addressee: str, timeout: float = REPLY_TIMEOUT_S,
              want_turn: bool = True) -> dict:
    """Waits for the addressee's reply to the message at ``seq``. Returns {state, reply, event, routing_notes,
    turn}. A routing note with no turn (for example "already confirmed") returns at once with state "note"."""
    end = time.monotonic() + timeout
    last = {}
    while time.monotonic() < end:
        tv = s.records("GET", f"/rooms/{rid}/turns").get("body") or {}
        notes = [n for n in tv.get("routing_notes") or [] if n.get("trigger_seq") == seq]
        turns = [t for t in tv.get("turns") or [] if t.get("trigger_seq") == seq and t.get("addressee") == addressee]
        turn = max(turns, key=lambda t: t.get("attempt") or 0) if turns else None
        last = {"turn": turn, "routing_notes": notes}
        if turn and turn.get("state") == "committed":
            ev = next((e for e in room_events(s, rid, seq) if e.get("id") == turn.get("event_id")), None)
            return {"state": "committed", "reply": (ev or {}).get("body") or "", "event": ev, **last}
        if turn and turn.get("state") in TERMINAL_BAD:
            return {"state": turn["state"], "reply": "", "event": None, **last}
        if notes and not turn and not want_turn:
            return {"state": "note", "reply": "", "event": None, **last}
        if notes and not turn and time.monotonic() > end - timeout + 20:
            return {"state": "note", "reply": "", "event": None, **last}
        time.sleep(POLL_S)
    state = (last.get("turn") or {}).get("state") or "no_turn"
    return {"state": f"timeout ({state})", "reply": "", "event": None, **last}


def wait_card(s: Session, rid: str, after: int, actor: str, timeout: float = 90) -> dict | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for e in room_events(s, rid, after):
            if e.get("kind") == "proposal" and e.get("actor") == actor:
                code = ((e.get("origin") or {}).get("code")) or find_p_code(e.get("body"))
                if code:
                    return {"code": code, "event_id": e.get("id"), "body": e.get("body"), "seq": e.get("seq")}
        time.sleep(POLL_S)
    return None


def ask_in_room(s: Session, rid: str, who: str, text: str) -> dict:
    ev = room_post(s, rid, text, target=who)
    out = wait_turn(s, rid, ev["seq"], who)
    out["sent_seq"] = ev["seq"]
    out["sent"] = text
    return out


def reply_evidence(r: dict) -> dict:
    return {"reply": r.get("reply"), "state": r.get("state"), "sent": r.get("sent"),
            "routing": [n.get("disposition") for n in r.get("routing_notes") or []]}


def need_reply(r: dict, who: str) -> str:
    if r.get("state") != "committed":
        raise CaseFail(f"{who} did not reply ({r.get('state')})", reply_evidence(r))
    return r.get("reply") or ""


# ── Menu helpers (A1–A3) ─────────────────────────────────────────────────────────────────────────────────────────

MENUS = {
    "mc": {"page": MC_PAGE, "ready": "body[data-ready=true]", "toggle": "details[data-mc-options] > summary",
           "details": "details[data-mc-options]",
           "rows": [("Dictate", "[data-dictate='#mc-input']"), ("Voice", "[data-partner-voice]"),
                    ("Model", "[data-partner-model-row]"), ("Private", "[data-mc-record]"),
                    ("Footnote", "[data-partner-menu] .partner-menu-foot")]},
    "cos": {"page": COS_PAGE, "ready": "meta[name=minimoi-csrf-token]", "toggle": "details[data-cos-options] > summary",
            "details": "details[data-cos-options]",
            "rows": [("Dictate", "#btn-dictate"), ("Voice", "[data-partner-voice]"),
                     ("Model", "[data-partner-model-row]"), ("Private", "#cos-private"),
                     ("Footnote", "[data-partner-menu] .partner-menu-foot")]},
}


def open_menu(s: Session, page, partner: str, path: str | None = None):
    m = MENUS[partner]
    s.goto(page, path or m["page"], ready=m["ready"])
    if partner == "cos":
        wait_until(lambda: page.evaluate("Boolean(window.cosHistory && window.cosHistory.ready)"), 30,
                   "CoS page did not become ready")
    page.locator(m["toggle"]).first.click()
    page.locator(m["details"] + " [data-partner-model-row]").first.wait_for(state="visible", timeout=20000)
    page.wait_for_timeout(500)
    return m


def menu_state(page, partner: str) -> dict:
    m = MENUS[partner]
    root = m["details"]
    voice = page.locator(f"{root} [data-partner-voice]").first
    return {"voice": voice.input_value() if voice.count() else None,
            "model": (page.locator(f"{root} [data-partner-model]").first.inner_text() or "").strip(),
            "note": (page.locator(f"{root} [data-partner-note]").first.inner_text() or "").strip()}


def check_menu_layout(page, partner: str, viewport: dict) -> dict:
    m = MENUS[partner]
    rows, problems = [], []
    for name, sel in m["rows"]:
        loc = page.locator(f"{m['details']} {sel}").first
        if not loc.count():
            problems.append(f"{name}: missing")
            rows.append({"row": name, "present": False})
            continue
        vis = loc.is_visible()
        box = loc.bounding_box() if vis else None
        inside = bool(box) and box["x"] >= -1 and box["y"] >= -1 and box["x"] + box["width"] <= viewport["width"] + 1 \
            and box["y"] + box["height"] <= viewport["height"] + 1
        hit = False
        if box:
            hit = page.evaluate("""([sel, x, y]) => { const el = document.elementFromPoint(x, y);
                const want = document.querySelector(sel); return !!(el && want && (want === el || want.contains(el))); }""",
                                [f"{m['details']} {sel}", box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
        row = {"row": name, "present": True, "visible": vis, "inside_viewport": inside, "not_covered": hit,
               "box": {k: round(v) for k, v in box.items()} if box else None}
        if name == "Dictate" and box:
            radius = page.evaluate("(sel) => parseFloat(getComputedStyle(document.querySelector(sel)).borderTopLeftRadius)",
                                   f"{m['details']} {sel}")
            row["round"] = radius >= min(box["width"], box["height"]) / 2 - 1
            if not row["round"]:
                problems.append("Dictate: not a round mic")
        rows.append(row)
        if not vis:
            problems.append(f"{name}: not visible")
        elif not inside:
            problems.append(f"{name}: outside the screen")
        elif not hit:
            problems.append(f"{name}: covered by something else")
    ys = [r["box"]["y"] for r in rows if r.get("box")]
    order_ok = len(ys) == len(m["rows"]) and ys == sorted(ys)
    if len(ys) == len(m["rows"]) and not order_ok:
        problems.append("rows not in the order Dictate, Voice, Model, Private, footnote")
    text = page.locator(m["details"]).first.inner_text()
    if "Voice is AI-generated." not in text:
        problems.append('footnote "Voice is AI-generated." missing')
    ids = route_ids_in(text)
    if ids:
        problems.append(f"route ids shown: {', '.join(ids)}")
    return {"rows": rows, "problems": problems, "menu_text": text}


# ── Cases ────────────────────────────────────────────────────────────────────────────────────────────────────────

@case("A1", "⋯ menu on MC and CoS, desktop and phone")
def case_a1(s: Session) -> dict:
    ev, problems, shots = {}, [], []
    phone_ctx = s.new_context(PHONE, phone=True)
    try:
        for size, ctx, vp in (("desktop", s.ctx, DESKTOP), ("phone", phone_ctx, PHONE)):
            for partner in ("mc", "cos"):
                page = ctx.new_page()
                try:
                    open_menu(s, page, partner)
                    res = check_menu_layout(page, partner, vp)
                except CaseFail as e:
                    res = {"problems": [e.reason]}
                shots.append(s.shot(page, f"{partner}-{size}"))
                page.close()
                ev[f"{partner}_{size}"] = res
                problems += [f"{partner} {size}: {p}" for p in res["problems"]]
    finally:
        phone_ctx.close()
        s.contexts.remove(phone_ctx)
    ev["screenshots"] = shots
    if problems:
        raise CaseFail("; ".join(problems), ev)
    return ev


@case("A2", "Voice sets Model (MC), saved Voice put back")
def case_a2(s: Session) -> dict:
    page = s.ctx.new_page()
    shots = []
    start = (s.guild("GET", "/mc/partner-settings").get("body") or {})
    original = ((start.get("voice") or {}).get("selected"))
    ev = {"original_voice": original, "options": (start.get("voice") or {}).get("options")}
    if original not in ("openai", "xai"):
        raise CaseFail("could not read the saved Voice", {**ev, "response": start})
    try:
        open_menu(s, page, "mc")
        for provider in ("xai", "openai"):
            page.locator("details[data-mc-options] [data-partner-voice]").first.select_option(provider)
            try:
                wait_until(lambda: match_model_text(menu_state(page, "mc")["model"], provider), 20,
                           f"Model did not follow Voice={provider}")
            except CaseFail as e:
                ev[provider] = menu_state(page, "mc")
                shots.append(s.shot(page, f"voice-{provider}"))
                raise CaseFail(e.reason, {**ev, "screenshots": shots})
            st = menu_state(page, "mc")
            ev[provider] = st
            shots.append(s.shot(page, f"voice-{provider}"))
            if "Applies from your next message" not in st["note"]:
                raise CaseFail(f'note "Applies from your next message and your next call." missing after {provider}',
                               {**ev, "screenshots": shots})
            api = (s.guild("GET", "/mc/partner-settings").get("body") or {})
            if (api.get("voice") or {}).get("selected") != provider:
                raise CaseFail(f"server did not save Voice={provider}", {**ev, "server": api, "screenshots": shots})
    finally:
        ev["restored"] = restore_voice(s, "mc", original)
        page.close()
    ev["screenshots"] = shots
    if not ev["restored"]:
        raise CaseFail("could not put the saved Voice back", ev)
    return ev


def restore_voice(s: Session, partner: str, original: str | None) -> bool:
    if original is None:
        return False
    for _ in range(3):
        cur = (s.guild("GET", "/mc/partner-settings").get("body") or {}) if partner == "mc" else \
            (s.cos("GET", "/history/partner-settings").get("body") or {})
        if (cur.get("voice") or {}).get("selected") == original:
            return True
        body = {"voice": original, "revision": cur.get("revision"), "conversation": None,
                "idempotency_key": "pm" + uuid.uuid4().hex[:24]}
        if partner == "mc":
            s.guild("POST", "/mc/partner-settings", body)
        else:
            s.cos("POST", "/history/partner-settings", body)
    return False


@case("A3", "Voice and Model kept: new chat and a second browser")
def case_a3(s: Session) -> dict:
    ev, problems, shots = {}, [], []
    mc_saved = s.guild("GET", "/mc/partner-settings").get("body") or {}
    cos_saved = s.cos("GET", "/history/partner-settings").get("body") or {}
    want = {"mc": {"voice": (mc_saved.get("voice") or {}).get("selected"), "model": (mc_saved.get("model") or {}).get("text")},
            "cos": {"voice": (cos_saved.get("voice") or {}).get("selected"),
                    "model": (cos_saved.get("model") or {}).get("text")}}
    ev["saved"] = want
    # a new MC chat in this browser
    cid = mc_new_chat(s)
    page = s.ctx.new_page()
    open_menu(s, page, "mc", f"{MC_PAGE}?c={cid}")
    ev["mc_new_chat"] = menu_state(page, "mc")
    shots.append(s.shot(page, "mc-new-chat"))
    page.close()
    # a second browser (fresh context, phone size, its own sign-in)
    other = s.new_context(PHONE, phone=True)
    try:
        for partner in ("mc", "cos"):
            p2 = other.new_page()
            try:
                open_menu(s, p2, partner)
                ev[f"{partner}_second_browser"] = menu_state(p2, partner)
            except CaseFail as e:
                ev[f"{partner}_second_browser"] = {"error": e.reason}
            shots.append(s.shot(p2, f"{partner}-second-browser"))
            p2.close()
    finally:
        other.close()
        s.contexts.remove(other)
    for label, partner in (("mc_new_chat", "mc"), ("mc_second_browser", "mc"), ("cos_second_browser", "cos")):
        got = ev.get(label) or {}
        if got.get("voice") != want[partner]["voice"] or _norm(got.get("model")) != _norm(want[partner]["model"]):
            problems.append(f"{label}: shows {got.get('voice')}/{got.get('model')}, saved "
                            f"{want[partner]['voice']}/{want[partner]['model']}")
    ev["screenshots"] = shots
    if problems:
        raise CaseFail("; ".join(problems), ev)
    return ev


manual_case("A4", "MC call voices (leo/marin)", "manual (Robert: voices). Listen to an MC call on Grok and on OpenAI.")


# GET-only probes for an instructions preview that mints nothing. None exists in the code read for v0.1; when one is
# added, list it here. The runner never calls /mc/voice/bootstrap: that mints a call and spends voice minutes.
A5_PREVIEW_PATHS = (GUILD_API + "/mc/voice/walk/preview", GUILD_API + "/mc/voice/instructions/preview")


@case("A5", "Walk instructions carry the Chicago clock")
def case_a5(s: Session) -> dict:
    caps = s.guild("GET", "/mc/voice/capabilities")
    ev = {"capabilities": {k: (caps.get("body") or {}).get(k) for k in ("voice_path", "mode", "default_provider")}}
    for path in A5_PREVIEW_PATHS:
        r = s.fetch(s.guild_page(), "GET", path)
        if r["status"] == 200 and isinstance(r.get("body"), dict):
            text = json.dumps(r["body"], ensure_ascii=False)
            ev["preview_path"] = path
            ev["instructions_excerpt"] = _short(text, 400)
            if "Central time in Chicago." not in text:
                raise CaseFail('"Central time in Chicago." not in the walk instructions', ev)
            return ev
    raise CaseSkip("no preview route returns the walk instructions without minting a call; not checked "
                   "(the runner never mints a call)", ev)


@case("A6", "Walk issues tool gives the newest open GitHub issue")
def case_a6(s: Session) -> dict:
    ev = {}
    for q in ("what is the newest open issue?", "list open issues"):
        r = s.guild("POST", "/mc/voice/walk/tool", {"name": "issues", "query": q})
        body = r.get("body") or {}
        m = issue_line_ok(body.get("text"))
        ev[q] = {"status": r["status"], "found": body.get("found"), "text": _short(body.get("text") or "", 500),
                 **m}
        if r["status"] == 200 and body.get("ok") and m["ok"]:
            ev["query_used"] = q
            return ev
    raise CaseFail("the issues tool did not return an open GitHub issue", ev)


manual_case("A7", "CoS call voices (ara/ballad)", "manual (Robert: voices). Listen to a CoS call on Grok and on OpenAI.")


@case("A8", "Fallback line in the ⋯ menu")
def case_a8(s: Session) -> dict:
    ev = {}
    for partner, path in (("mc", "/mc/partner-settings"), ("cos", "/history/partner-settings")):
        body = (s.guild("GET", path) if partner == "mc" else s.cos("GET", path)).get("body") or {}
        ev[partner] = body.get("last_answer") or ""
    seen = {k: v for k, v in ev.items() if v}
    if not seen:
        raise CaseSkip("not seen: no fallback happened, so there is no Last answer line to check", ev)
    bad = {k: v for k, v in seen.items() if not re.fullmatch(r"Last answer: .+ \(fallback\)", v)}
    if bad:
        raise CaseFail("Last answer line is not in the expected form", ev)
    ev["note"] = "a fallback was seen; Robert can look at the menu line"
    return {"_status": "manual", **ev}


@case("H1", "MC and CoS Available in a new meeting")
def case_h1(s: Session) -> dict:
    m = meeting(s)
    parts = (s.records("GET", f"/rooms/{m['id']}/turns").get("body") or {}).get("participants") or []
    by = {p.get("id"): p for p in parts}
    ev = {"room_id": m["id"], "title": m["title"],
          "participants": {a: {k: (by.get(a) or {}).get(k) for k in ("rsvp", "reach", "available_here", "needs_proof")}
                           for a in ("mc", "cos")}}
    members = sorted({(p.get("id")) for p in (s.records("GET", f"/rooms/{m['id']}").get("body") or {}).get("members") or []})
    ev["members"] = members
    page = s.ctx.new_page()
    s.goto(page, f"{ROOMS_PAGE}?room={m['id']}")
    try:
        page.locator("[data-rm-participation]").first.wait_for(state="attached", timeout=20000)
        page.wait_for_timeout(3000)
        ev["page_participation"] = page.locator("[data-rm-participation]").first.inner_text()
    except Exception as e:  # the API answer still decides
        ev["page_participation"] = f"not read: {e.__class__.__name__}"
    ev["screenshots"] = [s.shot(page, "meeting")]
    page.close()
    problems = []
    if set(members) - {"robert", "mc", "cos"}:
        problems.append(f"meeting has other members: {members}")
    for a in ("mc", "cos"):
        reach = ((by.get(a) or {}).get("reach") or {}).get("state")
        if (by.get(a) or {}).get("rsvp") != "accepted" or reach not in AVAILABLE_REACH:
            problems.append(f"{a} not Available (rsvp {(by.get(a) or {}).get('rsvp')}, reach {reach})")
    if "Not reachable" in (ev.get("page_participation") or ""):
        problems.append("the page shows Not reachable")
    if problems:
        raise CaseFail("; ".join(problems), ev)
    return ev


def _meeting_or_skip(s: Session, suffix: str = "") -> dict:
    try:
        return meeting(s, suffix)
    except CaseFail as e:
        raise CaseSkip(f"meeting not ready: {e.reason}", e.evidence)


@case("B1", "Guildhall approval: card code, yes confirms, second yes does nothing")
def case_b1(s: Session) -> dict:
    rid = _meeting_or_skip(s)["id"]
    ask = ask_in_room(s, rid, "cos", f"CoS, track drafting the Q1 plan outline, MC leads. (UAT run {s.run_id})")
    ev = {"room_id": rid, "ask": reply_evidence(ask)}
    need_reply(ask, "CoS")
    card = wait_card(s, rid, ask["sent_seq"], "cos")
    ev["card"] = card
    if not card:
        raise CaseFail("no P-xxxx card from CoS", ev)
    yes1 = room_post(s, rid, approval_text("B1", "yes", card["code"]))
    r1 = wait_turn(s, rid, yes1["seq"], "cos")
    ev["first_yes"] = reply_evidence(r1)
    conf = match_b1_confirm(r1.get("reply"), card["code"])
    ev["task_id"] = conf["task_id"]
    if not conf["ok"]:
        raise CaseFail(f"no 'Confirmed {card['code']}' with a TK-cos- id", ev)
    yes2 = room_post(s, rid, approval_text("B1", "yes", card["code"]))
    r2 = wait_turn(s, rid, yes2["seq"], "cos", timeout=60, want_turn=False)
    time.sleep(10)                                     # give a wrong second turn time to show up
    later = [e for e in room_events(s, rid, yes2["seq"]) if e.get("actor") == "cos"]
    tv = s.records("GET", f"/rooms/{rid}/turns").get("body") or {}
    dispositions = [n.get("disposition") for n in tv.get("routing_notes") or [] if n.get("trigger_seq") == yes2["seq"]]
    second_turns = [t for t in tv.get("turns") or [] if t.get("trigger_seq") == yes2["seq"]]
    texts = [e.get("body") or "" for e in later] + ([r2.get("reply")] if r2.get("reply") else [])
    m2 = match_b1_second(texts, dispositions, conf["task_id"])
    ev["second_yes"] = {"routing": dispositions, "cos_events_after": [e.get("body") for e in later],
                        "turns_queued": len(second_turns), **m2}
    if not m2["ok"] or later:
        raise CaseFail("second yes was not a no-op ('already confirmed', no new task, no new CoS reply)", ev)
    return ev


@case("B2", "Meeting files: MC quotes Copperleaf from .docx and .txt")
def case_b2(s: Session) -> dict:
    ev, problems = {}, []
    # .docx in the run's meeting; .txt in a meeting of its own so the two answers cannot lean on each other
    for suffix, fixture in (("", "b2_reading_list.docx"), ("-txt", "b2_reading_list.txt")):
        rid = _meeting_or_skip(s, suffix)["id"]
        doc = room_share(s, rid, FIXTURES / fixture)
        r = ask_in_room(s, rid, "mc", f"MC, which book did I say I need to read, in the file {fixture} I just shared?")
        ok = r.get("state") == "committed" and match_b2(r.get("reply"))
        ev[fixture] = {"room_id": rid, "document_id": doc.get("id"), **reply_evidence(r), "ok": ok}
        if not ok:
            problems.append(f"{fixture}: no 'Copperleaf' ({r.get('state')})")
    if problems:
        raise CaseFail("; ".join(problems), ev)
    return ev


@case("B3", "CoS files a professional initiative with the notes attached")
def case_b3(s: Session) -> dict:
    rid = _meeting_or_skip(s)["id"]
    path = FIXTURES / "b3_initiative_notes.md"
    local_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    doc = room_share(s, rid, path)
    ev = {"room_id": rid, "document_id": doc.get("id"), "file_sha256": local_sha, "stored_sha256": doc.get("sha256")}
    if doc.get("sha256") and doc["sha256"] != local_sha:
        raise CaseFail("stored file sha256 differs from the fixture", ev)
    # Never retried: a second ask could file a second initiative.
    r = ask_in_room(s, rid, "cos", "CoS, file this as a professional initiative with the notes attached "
                                   f"(b3_initiative_notes.md), then read it back. (UAT run {s.run_id})")
    ev.update(reply_evidence(r))
    m = match_b3(need_reply(r, "CoS"), local_sha)
    ev.update(m)
    if not m["ok"]:
        raise CaseFail("no PI-2026-xxxx parked record (or a sha256 mismatch, or an INIT number)", ev)
    return ev


@case("B4", "MC working document: Saved, then the exact text read back")
def case_b4(s: Session) -> dict:
    rid = _meeting_or_skip(s)["id"]
    name = f"uat-{s.run_id}.txt"
    text = f"UAT {s.run_id} working note: kettle, lantern, 42 blue pebbles."
    save = ask_in_room(s, rid, "mc", f"MC, save a working document named {name} with exactly this text: {text}")
    ev = {"room_id": rid, "name": name, "text": text, "save": reply_evidence(save)}
    m = match_b4_saved(need_reply(save, "MC"))
    ev["save_check"] = m
    if not m["ok"]:
        raise CaseFail("MC did not say Saved cleanly", ev)
    back = ask_in_room(s, rid, "mc", f"MC, read back the working document {name}, quoting its text exactly.")
    ev["read_back"] = reply_evidence(back)
    if not match_b4_readback(need_reply(back, "MC"), text):
        raise CaseFail("read-back does not contain the exact saved text", ev)
    ev["note"] = "file existence is not checkable over HTTP; the exact read-back stands in for it"
    return ev


@case("B5", "Ticket card answered no: nothing written")
def case_b5(s: Session) -> dict:
    rid = _meeting_or_skip(s)["id"]
    ask = ask_in_room(s, rid, "mc", f"MC, please open a GitHub issue titled 'UAT {s.run_id}: harmless note' with the "
                                    "body 'Automatic UAT check from staging. Safe to close.' Show me the card first.")
    ev = {"room_id": rid, "ask": reply_evidence(ask)}
    need_reply(ask, "MC")
    card = wait_card(s, rid, ask["sent_seq"], "mc")
    ev["card"] = card
    if not card:
        said = ask.get("reply") or ""
        if (re.search(r"no repository is configured", said, re.I)
                and re.search(r"nothing was (proposed|created)", said, re.I)
                and not re.search(r"github\.com/[^\s]+/issues/\d+", said)):
            raise CaseSkip("staging has no repository for issue writes, so MC offers no card and writes nothing; "
                           "the 'no' path was not exercised", ev)
        raise CaseFail("no P-xxxx card from MC", ev)
    no = room_post(s, rid, approval_text("B5", "no", card["code"]))      # "yes" is impossible here by construction
    r = wait_turn(s, rid, no["seq"], "mc")
    ev["no_reply"] = reply_evidence(r)
    if r.get("state") != "committed" or not match_b5_declined(r.get("reply")):
        raise CaseFail("'no' did not close the card with nothing changed", ev)
    if re.search(r"github\.com/[^\s]+/issues/\d+", r.get("reply") or ""):
        raise CaseFail("the reply links an issue", ev)
    ev["note"] = "GitHub itself is not queried; MC's 'Cancelled. Nothing was changed.' is the evidence"
    return ev


# Each UAT chat used to land in the vault, so asking the same question every run filled CoS's evidence budget with
# earlier UAT chats and crowded out the target (release session, 26 Nov: an earlier C1 chat outranked gghhx28k). The real
# fix: a chat created under the UAT session is marked ``uat`` by the server and never reaches Vault search
# (minimoi_portal/uat_signin.py ``is_uat``; earlier chats: core/uat_chats.py). The rotation stays as a second guard: it
# costs nothing, and it covers a staging that has not yet deployed the marker or marked its earlier chats.
C1_QUESTIONS = [
    "Did we ever talk about why I'm hearing back from employers less often lately?",
    "Have we discussed the reasons companies seem to reply to me less these days?",
    "Did we ever go over why fewer hiring managers are getting back to me recently?",
    "Have we talked before about why my applications get fewer responses than they used to?",
    "Did I ever bring up with you that recruiters and firms answer me less often now, and why?",
    "Have we covered why the callbacks from prospective employers have dropped off?",
]


def _rotating(questions: list[str], run_id: str) -> str:
    return questions[int(hashlib.sha256(run_id.encode()).hexdigest(), 16) % len(questions)]


@case("C1", "CoS meaning search finds the employers conversation")
def case_c1(s: Session) -> dict:
    r = cos_ask(s, _rotating(C1_QUESTIONS, s.run_id), f"uat-{s.run_id} C1")
    m = match_c1(r["reply"])
    ev = {**r, **m}
    if not m["ok"]:
        raise CaseFail(f"no citation of {C1_TARGET} and no 'yes' with November", ev)
    return ev


@case("C2", "MC meaning search finds the slip-ups discussion, no career material")
def case_c2(s: Session) -> dict:
    r = mc_ask(s, _rotating([
        "Did we ever discuss collecting the foreign-language slip-ups from my day-to-day chats for a study list?",
        "Have we talked about gathering the language mistakes I make in everyday chats into something to review?",
        "Did we ever plan to save the errors from my daily foreign-language messages so I can practise them later?",
        "Have we discussed turning the little language blunders from my normal conversations into revision material?",
    ], s.run_id), "c2")
    m = match_c2(r["reply"])
    ev = {"conversation_id": r["conversation_id"], "reply": r["reply"], "screenshots": [r["screenshot"]], **m}
    if not m["ok"]:
        raise CaseFail("no corrections/restudy, Build Log #167 or 248w2as6 (or career material appeared)", ev)
    return ev


ORDER = ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "H1", "B1", "B2", "B3", "B4", "B5", "C1", "C2"]
CASES.sort(key=lambda c: ORDER.index(c.id) if c.id in ORDER else 99)


# ── Running ──────────────────────────────────────────────────────────────────────────────────────────────────────

def run_case(c: Case, session) -> Result:
    t0 = time.monotonic()
    if c.fn is None:
        return Result(c.id, c.title, "manual", {"note": c.manual_note}, 0.0)
    if session is not None:
        session.current_case = c.id
    try:
        ev = c.fn(session) or {}
        status = ev.pop("_status", "pass")
        return Result(c.id, c.title, status, ev, time.monotonic() - t0)
    except CaseFail as e:
        return Result(c.id, c.title, "fail", {"reason": e.reason, **e.evidence}, time.monotonic() - t0)
    except CaseSkip as e:
        return Result(c.id, c.title, "skip", {"reason": e.reason, **e.evidence}, time.monotonic() - t0)
    except ApprovalRefused as e:
        return Result(c.id, c.title, "error", {"reason": f"guard refused: {e}"}, time.monotonic() - t0)
    except Exception as e:
        tb = traceback.format_exc().strip().splitlines()[-6:]
        return Result(c.id, c.title, "error", {"reason": f"{e.__class__.__name__}: {e}", "traceback": tb},
                      time.monotonic() - t0)


def open_session(args, token: str, out_dir: Path, run_id: str):
    return Session(args.base_url, token, out_dir, run_id, headless=not args.headed)


def base_url_allowed(url: str) -> bool:
    """Staging or a local host only. The live site (minimoi.ai, www.minimoi.ai) is refused."""
    m = re.match(r"^https?://([^/:]+)(?::\d+)?(?:/|$)", url or "")
    if not m:
        return False
    host = m.group(1).lower()
    local = host in ("localhost", "127.0.0.1") or host.endswith(".localhost")
    if local:
        return True
    return host == "dev.minimoi.ai" and url.lower().startswith("https://")   # the token never travels in cleartext


def new_run_id() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_args(argv):
    p = argparse.ArgumentParser(description="Automatic staging UAT for mini-moi (never against production).")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--token-env", default=DEFAULT_TOKEN_ENV, help="name of the env var holding the UAT token")
    p.add_argument("--token-file", default=None, help="file holding the UAT token (preferred: keeps it out of the env)")
    p.add_argument("--out", default=None, help="output directory (default ./uat-runs/<run id>)")
    p.add_argument("--only", default=None, help="comma list of case ids, e.g. A1,B2")
    p.add_argument("--list", action="store_true", help="print the cases and exit")
    p.add_argument("--run-id", default=os.environ.get("UAT_RUN_ID") or None)
    p.add_argument("--headed", action="store_true", help="show the browser (local debugging)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        cases = select_cases(args.only)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.list:
        for c in cases:
            kind = "manual" if c.kind == "manual" else "auto"
            print(f"{c.id:<4} {kind:<7} {c.title}")
        return 0
    if not base_url_allowed(args.base_url):
        print(f"error: refusing to run against {args.base_url}: https://dev.minimoi.ai or a local host only", file=sys.stderr)
        return 2
    if args.token_file:
        try:
            token = Path(args.token_file).read_text(encoding="ascii").strip()
        except OSError as e:
            print(f"error: cannot read the token file: {e.__class__.__name__}", file=sys.stderr)
            return 2
    else:
        token = os.environ.get(args.token_env) or ""
    if not token:
        print("error: no UAT token (empty --token-file or token env var)", file=sys.stderr)
        return 2
    run_id = args.run_id or new_run_id()
    out_dir = Path(args.out) if args.out else Path("uat-runs") / run_id
    meta = {"run_id": run_id, "base_url": args.base_url, "started": _dt.datetime.now(_dt.timezone.utc).isoformat(
        timespec="seconds"), "cases": [c.id for c in cases]}
    results: list[Result] = []
    session = None
    try:
        if any(c.fn for c in cases):
            session = open_session(args, token, out_dir, run_id)
        for c in cases:
            r = run_case(c, session)
            results.append(r)
            print(f"{r.id:<4} {r.status:<6} {r.seconds:6.1f}s  {r.title}", flush=True)
    except Exception as e:
        reason = redact(f"{e.__class__.__name__}: {e}", [token])
        done = {r.id for r in results}
        results += [run_case(c, None) if c.fn is None else
                    Result(c.id, c.title, "error", {"reason": f"run could not start or continue: {reason}"})
                    for c in cases if c.id not in done]
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
    meta["finished"] = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    jpath, mpath = write_reports(out_dir, meta, results, [token])
    print(f"report: {mpath}")
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
