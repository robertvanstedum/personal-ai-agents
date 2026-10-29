#!/usr/bin/env python3
"""waiting.py — tell Robert an agent is blocked on him, through MiniMoi's action-waiting notices.

    waiting.py raise   --env prod|dev --agent claude-code|codex [--key K] [--session S] [--branch B] [--no-fallback] "words"
    waiting.py resolve --env prod|dev --agent claude-code|codex --key K
    waiting.py install --env prod|dev                     (Robert, once per environment)

Spec v0.2 §3.8 (planning-studio/operations/specs/SPEC_ACTION_WAITING_NOTICES_v0.2_2026-10-29.md). Standard library only;
it uses the repository's own standard-library scrubbers for the fallback message when they can be imported.

Exit codes: 0 recorded; 2 refused (bad token, bad request, key conflict, too many open); 3 MiniMoi unreachable after 3
tries over about 30 s. On 3, one direct Telegram message goes on the Mac's outbound bot (Keychain telegram/bot_token
and telegram/chat_id, the bot tg.py uses), marked "NOT RECORDED in MiniMoi". On 0, when MiniMoi recorded the ask but
says it will not tell Robert now (record only, not configured, failing, stalled), one direct message says "recorded as
W-n, not sent by MiniMoi". --no-fallback turns both off. Fallback messages are not deduplicated (spec §3.8).

The agent's token: ~/.minimoi/notices/<agent>-<env>.token (0600) when present, else the Keychain (service
minimoi-notices, account <agent>-<env>). No token, URL with a token, or secret is ever printed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

AGENTS = ("claude-code", "codex")
NAMES = {"claude-code": "Claude Code", "codex": "Codex"}
PROD_URL = "https://minimoi.ai"
DEV_URL = "https://dev.minimoi.ai"
KEYCHAIN_SERVICE = "minimoi-notices"
TOKEN_DIR = Path.home() / ".minimoi" / "notices"
UNDELIVERED = ("record", "not_configured", "failing", "stalled")
OK, REFUSED, UNREACHABLE = 0, 2, 3
TRIES, WAIT_S = 3, 10.0
KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
SESSION = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")
BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")


class Deps:
    """Everything that reaches outside the process, replaceable in tests (no real network, Keychain or Telegram)."""

    def __init__(self, *, opener=None, run=subprocess.run, sleep=time.sleep, environ=None, token_dir=None,
                 telegram=None, out=None):
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({})).open
        self.run = run
        self.sleep = sleep
        self.environ = os.environ if environ is None else environ
        self.token_dir = Path(token_dir) if token_dir else TOKEN_DIR
        self.telegram = telegram or self._telegram
        self.out = out or sys.stdout

    def say(self, text):
        print(text, file=self.out)

    def keychain(self, service, account):
        try:
            done = self.run(["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                            capture_output=True, text=True)
        except OSError:
            return None
        return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else None

    def _telegram(self, text):
        """The fallback: one message on the Mac's outbound bot. Never echoes the URL (it holds the token)."""
        token, chat = self.keychain("telegram", "bot_token"), self.keychain("telegram", "chat_id")
        if not token or not chat:
            return "fallback not sent: the Mac's Telegram bot is not in the Keychain"
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                     data=json.dumps({"chat_id": chat, "text": text[:3900],
                                                      "disable_web_page_preview": True}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=15) as r:
                return "fallback sent" if r.status == 200 else f"fallback not sent: status {r.status}"
        except Exception as exc:
            return f"fallback not sent: {type(exc).__name__}"


def base_url(env, deps) -> str:
    if env == "prod":
        return PROD_URL
    url = str(deps.environ.get("MINIMOI_NOTICES_DEV_URL") or DEV_URL).rstrip("/")
    if not re.match(r"^(https://[A-Za-z0-9.-]+|http://(127\.0\.0\.1|localhost)(:\d+)?)(/.*)?$", url):
        raise SystemExit("MINIMOI_NOTICES_DEV_URL must be an https URL (or http on 127.0.0.1/localhost)")
    return url


def read_token(agent, env, deps):
    path = deps.token_dir / f"{agent}-{env}.token"
    if path.exists():
        if path.stat().st_mode & 0o077:
            raise SystemExit(f"refused: {path} is readable by others (chmod 600 it)")
        return path.read_text(encoding="utf-8").strip() or None
    return deps.keychain(KEYCHAIN_SERVICE, f"{agent}-{env}")


def call(url, token, body, deps):
    """(status, answer) or (None, None) when MiniMoi could not be reached after TRIES tries."""
    data = json.dumps(body).encode()
    for attempt in range(TRIES):
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        try:
            with deps.opener(req, timeout=10) as r:
                return r.status, json.loads(r.read(65536) or b"{}")
        except urllib.error.HTTPError as exc:
            if exc.code not in (500, 502, 503, 504):
                try:
                    return exc.code, json.loads(exc.read(65536) or b"{}")
                except ValueError:
                    return exc.code, {}
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if attempt < TRIES - 1:
            deps.sleep(WAIT_S)
    return None, None


def _scrub(text):
    """The repository's credential and payment scrubbers (standard library only), or None when not importable."""
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from utils.credential_scrub import scrub as scrub_credentials
        from utils.payment_scrub import scrub as scrub_payment
    except Exception:
        return None
    cleaned, _ = scrub_credentials(text)
    return scrub_payment(cleaned)


def default_key(agent, session, words) -> str:
    return hashlib.sha256(f"{agent}|{session or ''}|{words}".encode("utf-8")).hexdigest()[:16]


def do_raise(args, deps) -> int:
    words = args.words
    if not 10 <= len(words) <= 1500:
        deps.say("refused: words must be 10 to 1,500 characters")
        return REFUSED
    for name, pattern in (("session", SESSION), ("branch", BRANCH)):
        value = getattr(args, name)
        if value is not None and not pattern.match(value):
            deps.say(f"refused: --{name} does not match {pattern.pattern}")
            return REFUSED
    key = args.key or default_key(args.agent, args.session, words)
    if not KEY.match(key):
        deps.say("refused: --key must match ^[A-Za-z0-9._:-]{8,128}$")
        return REFUSED
    label = "" if args.env == "prod" else "[dev] "
    where = " · ".join(x for x in (f"branch {args.branch}" if args.branch else "",
                                   f"session {args.session[:8]}" if args.session else "") if x)
    who = NAMES[args.agent] + " is blocked on you" + (f" · {where}" if where else "")
    token = read_token(args.agent, args.env, deps)
    if not token:
        deps.say(f"refused: no token for {args.agent} on {args.env} (waiting.py install --env {args.env})")
        return REFUSED
    body = {"key": key, "words": words}
    if args.session:
        body["session"] = args.session
    if args.branch:
        body["branch"] = args.branch
    base = base_url(args.env, deps)
    status, answer = call(base + "/internal/notices/v1/raise", token, body, deps)
    if status is None:
        deps.say("MiniMoi unreachable after 3 tries: NOT RECORDED")
        if not args.no_fallback:
            scrubbed = _scrub(words)
            text = f"{label}MiniMoi · NOT RECORDED in MiniMoi\n{who}\n" + (
                f"“{scrubbed[:1000]}”" if scrubbed is not None else "(words left out: the scrubber is not available)")
            deps.say(deps.telegram(text))
        return UNREACHABLE
    if status != 200 or not isinstance(answer, dict) or not answer.get("code"):
        deps.say(f"refused: {status} {answer.get('error', '') if isinstance(answer, dict) else ''}".rstrip())
        return REFUSED
    code, delivery = answer["code"], answer.get("delivery")
    deps.say(f"{code} {'new' if answer.get('new') else 'already waiting'} · delivery {delivery}")
    if delivery in UNDELIVERED and not args.no_fallback:
        text = (f"{label}MiniMoi · recorded as {code}, not sent by MiniMoi ({delivery})\n{who}\n"
                f"Open in MC: {base}/guild-next/guild/build?waiting={code}")
        deps.say(deps.telegram(text))
    return OK


def do_resolve(args, deps) -> int:
    if not args.key or not KEY.match(args.key):
        deps.say("refused: --key is required and must match ^[A-Za-z0-9._:-]{8,128}$")
        return REFUSED
    token = read_token(args.agent, args.env, deps)
    if not token:
        deps.say(f"refused: no token for {args.agent} on {args.env}")
        return REFUSED
    status, answer = call(base_url(args.env, deps) + "/internal/notices/v1/resolve", token, {"key": args.key}, deps)
    if status is None:
        deps.say("MiniMoi unreachable after 3 tries: not resolved")
        return UNREACHABLE
    if status != 200 or not isinstance(answer, dict):
        deps.say(f"refused: {status}")
        return REFUSED
    deps.say(f"{answer.get('code') or 'no item'} {answer.get('state')}")
    return OK


def do_install(args, deps) -> int:
    """Make each agent's token in the Keychain (once) and print only the settings line of their SHA-256 hashes."""
    entries = []
    for agent in AGENTS:
        account = f"{agent}-{args.env}"
        token = deps.keychain(KEYCHAIN_SERVICE, account)
        if not token:
            token = secrets.token_urlsafe(36)
            done = deps.run(["security", "add-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w", token],
                            capture_output=True, text=True)
            if done.returncode != 0:
                deps.say(f"could not store the {agent} token in the Keychain")
                return REFUSED
        entries.append(f"{agent}:{hashlib.sha256(token.encode('utf-8')).hexdigest()}")
    deps.say(f"MINIMOI_ACTION_NOTICES_AGENT_TOKENS={','.join(entries)}")
    return OK


def parser():
    p = argparse.ArgumentParser(prog="waiting.py", description="Tell Robert an agent is blocked on him (MiniMoi).")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("raise")
    r.add_argument("--env", choices=("prod", "dev"), required=True)
    r.add_argument("--agent", choices=AGENTS, required=True)
    r.add_argument("--key")
    r.add_argument("--session")
    r.add_argument("--branch")
    r.add_argument("--no-fallback", action="store_true")
    r.add_argument("words")
    s = sub.add_parser("resolve")
    s.add_argument("--env", choices=("prod", "dev"), required=True)
    s.add_argument("--agent", choices=AGENTS, required=True)
    s.add_argument("--key", required=True)
    i = sub.add_parser("install")
    i.add_argument("--env", choices=("prod", "dev"), required=True)
    return p


def main(argv=None, deps=None) -> int:
    args = parser().parse_args(argv)
    deps = deps or Deps()
    return {"raise": do_raise, "resolve": do_resolve, "install": do_install}[args.command](args, deps)


if __name__ == "__main__":
    sys.exit(main())
