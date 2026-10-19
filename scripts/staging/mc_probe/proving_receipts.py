"""Contract-v2 receipt collector for the MC proving loop (Claude Code, 19 Oct 2026).

Turns the proving driver's exchange log plus read-only store snapshots into one run record in the
proving loop's run-record contract, version 2. It never calls the portal, MC, the model gateway or Telegram: the operator
takes the snapshots and passes them in. Receipts come from the stores, never from reply text, and
each is bound to the run's principal, conversation, operation and time window. Anything that cannot
be bound is left out of the receipts and listed under ``unbound``. Cost for settlement stays ``None``:
time-window attribution can't prove exclusive gateway use.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re

CONTRACT = 2
MC_AUTHOR = "master_craftsman"          # stores.MASTER_CRAFTSMAN: author of MC-proposed Board outcomes
TEST_PRINCIPAL = re.compile(r"mc-proving-[a-z0-9-]+")   # the proving driver's isolated test identities only
# The portal's model backends (minimoi_portal/guild_ui/mc: cos, openclaw, grok). A stub answer is not
# MC's, and anything absent or unknown is an error, never an MC turn.
MC_BACKEND_KINDS = frozenset({"cos", "openclaw", "grok"})
CLOCK_SLACK = timedelta(minutes=10)     # stores stamp after the reply returns; deliveries come later
# The operator runs this against the dev database with the run's post-it ids and passes the rows in.
POSTIT_QUERY = ("SELECT id, text, author, author_kind, created_at, binned_at FROM guild.floor_postits "
                "WHERE id = ANY(%s) ORDER BY id")
_NOTE_ENTRY = re.compile(r"<!-- cos-operation:(?P<op>[^ ]+) -->\n\[note\] (?P<date>\d{4}-\d{2}-\d{2}): (?P<text>[^\n]*)")


class Refused(ValueError):
    """The inputs cannot make a valid run record (wrong identity or environment)."""


def _ts(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(value):
    return _ts(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _window(start, end):
    return _ts(start), _ts(end) + CLOCK_SLACK


def exchanges_from_journal(journal_dir):
    """Exchanges from the proving driver's journal, in send order.

    The driver writes ``<key>-intent.json`` for the owner note and ``<key>-turn-intent.json`` before
    each MC turn, then ``<key>-turn-response.json`` or ``<key>-turn-failure.json``. A turn with no
    response is unresolved: it becomes an exchange with no response (an error), never a success."""
    d, out = Path(journal_dir), []
    for intent in sorted(d.glob("*-turn-intent.json")):
        key = intent.name[: -len("-turn-intent.json")]
        turn = json.loads(intent.read_text())
        payload = json.loads((d / f"{key}-intent.json").read_text())["payload"]
        status, body, answered = None, None, None
        for suffix in ("response", "failure"):
            path = d / f"{key}-turn-{suffix}.json"
            if path.exists():
                record = json.loads(path.read_text())
                status, answered = record.get("status"), record.get("at_utc")
                body = record.get("body") if isinstance(record.get("body"), dict) else {
                    "error": record.get("error_class") or "uncertain"}
                break
        out.append({"owner_text": payload["text"], "conversation_id": payload["conversation_id"],
                    "note_id": payload["idempotency_key"], "sent_utc": turn["at_utc"], "answered_utc": answered,
                    "status_code": status, "response": body})
    return sorted(out, key=lambda e: _ts(e["sent_utc"]))


def turns_from_exchanges(exchanges, principal):
    """Owner turn plus its reply per exchange. Roles come from the portal's backend_kind, never text.

    exchange = {owner_text, conversation_id, note_id, sent_utc, answered_utc, status_code, response}.
    Returns (turns, errors). A platform reply keeps its operation; an MC reply keeps a saved Board
    outcome as a board_postit operation expected to be authored by Master Craftsman."""
    turns, errors = [], []
    for ex in exchanges:
        conv = ex["conversation_id"]
        turns.append({"role": "owner", "text": ex["owner_text"], "conversation_id": conv,
                      "note_id": ex.get("note_id"), "at_utc": _iso(ex["sent_utc"])})
        resp = ex.get("response")
        if not isinstance(resp, dict) or ex.get("status_code") != 200 or resp.get("error") or resp.get("status") != "answered":
            reason = resp.get("error") or resp.get("failure_class") or resp.get("status") if isinstance(resp, dict) else "no_response"
            errors.append(f"{ex.get('note_id')}: {ex.get('status_code')} {reason}")
            turns.append({"role": "platform", "backend_kind": "portal_refusal", "conversation_id": conv,
                          "text": (resp or {}).get("message", "") if isinstance(resp, dict) else "", "operation": None,
                          "at_utc": _iso(ex.get("answered_utc") or ex["sent_utc"])})
            continue
        reply = resp.get("reply_note") or {}
        text = resp.get("message") or reply.get("text") or ""
        if resp.get("backend_kind") == "platform":
            op = dict(resp.get("operation") or {}) or None
            if op and op.get("type") == "board_postit":
                op["expected_author"] = principal
            turns.append({"role": "platform", "backend_kind": "platform", "conversation_id": conv, "text": text,
                          "operation": op, "at_utc": _iso(ex["answered_utc"])})
        elif resp.get("backend_kind") not in MC_BACKEND_KINDS:
            errors.append(f"{ex.get('note_id')}: answered by backend {resp.get('backend_kind')!r}, not an MC backend")
            turns.append({"role": "platform", "backend_kind": "unrecognized_backend", "conversation_id": conv,
                          "text": "", "operation": None, "at_utc": _iso(ex["answered_utc"])})
        else:
            outcome = resp.get("board_outcome") or {}
            op = ({"type": "board_postit", "saved": True, "id": outcome.get("postit_id"), "via": "board_outcome",
                   "source_id": outcome.get("source_id"), "expected_author": MC_AUTHOR}
                  if outcome.get("state") == "saved" else None)
            turns.append({"role": "mc", "backend_kind": resp.get("backend_kind"), "conversation_id": conv,
                          "text": reply.get("text") or "", "operation": op, "at_utc": _iso(ex["answered_utc"])})
    return turns, errors


def _ops(turns, kind):
    return [(t, t["operation"]) for t in turns if isinstance(t.get("operation"), dict)
            and t["operation"].get("type") == kind and t["operation"].get("saved") is True]


def board_receipts(postit_rows, turns, start, end):
    """Rows bound to a saved board_postit operation in this run, by id, expected author and time."""
    lo, hi = _window(start, end)
    expected = {op.get("id"): op.get("expected_author") for _, op in _ops(turns, "board_postit")}
    bound, unbound = [], []
    for row in postit_rows:
        rid = row.get("id")
        ok = (rid in expected and row.get("author") == expected[rid] and row.get("created_at") is not None
              and lo <= _ts(row["created_at"]) <= hi)
        if ok:
            bound.append({"id": rid, "text": row.get("text", ""), "author": row["author"],
                          "created_utc": _iso(row["created_at"]), "binned": row.get("binned_at") is not None,
                          "source": "db:guild.floor_postits"})
        else:
            unbound.append(f"board_row {rid}")
    missing = [i for i in expected if i not in {r.get("id") for r in postit_rows}]
    unbound += [f"board_row {i} (operation without a stored row)" for i in missing]
    return bound, unbound


def note_receipts(cos_memory_text, turns, start, end):
    """CoS notes bound by operation id. The file stamps a UTC date, not a time: the entry's date must
    fall inside the run's dates and its operation id must match a saved cos_note in this run."""
    lo, hi = _window(start, end)
    dates = {(lo + timedelta(days=d)).date().isoformat() for d in range((hi.date() - lo.date()).days + 1)}
    wanted = {op.get("operation_id") for _, op in _ops(turns, "cos_note")}
    bound, seen = [], set()
    for m in _NOTE_ENTRY.finditer(cos_memory_text or ""):
        if m["op"] in wanted and m["date"] in dates:
            bound.append({"operation_id": m["op"], "text": m["text"], "created_date": m["date"],
                          "source": "file:cos_memory.md#cos-operation"})
            seen.add(m["op"])
    return bound, [f"note {o} (no stored entry in the run's dates)" for o in sorted(wanted - seen)]


def reminder_receipts(reminders_document, turns, start, end):
    """Reminder records bound by reminder id and operation id, created inside the window; deliveries
    are the delivered records' Telegram message ids, only for this run's bound reminders."""
    lo, hi = _window(start, end)
    ops = {(op.get("reminder_id"), op.get("operation_id")) for _, op in _ops(turns, "reminder_set")}
    records = (reminders_document or {}).get("reminders", [])
    reminders, deliveries, unbound = [], [], []
    for r in records:
        key = (r.get("id"), r.get("operation_id"))
        if key not in ops:
            continue
        try:
            in_window = lo <= _ts(r["created_utc"]) <= hi
        except (KeyError, TypeError, ValueError):
            in_window = False
        if not in_window:
            unbound.append(f"reminder {r.get('id')} (created outside the run)"); continue
        reminders.append({"reminder_id": r["id"], "operation_id": r["operation_id"], "due_utc": r.get("due_utc"),
                          "text": r.get("text", ""), "state": r.get("state"), "created_utc": r["created_utc"],
                          "source": "file:cos_reminders/reminders.json"})
        if r.get("state") == "delivered" and isinstance(r.get("telegram_message_id"), int) and r.get("delivered_utc"):
            deliveries.append({"reminder_id": r["id"], "message_id": r["telegram_message_id"],
                               "delivered_utc": r["delivered_utc"], "source": "file:cos_reminders/reminders.json"})
    found = {(r["reminder_id"], r["operation_id"]) for r in reminders}
    unbound += [f"reminder {rid} (operation without a stored record)" for rid, _ in sorted(ops - found, key=str)
                if not any(u.startswith(f"reminder {rid}") for u in unbound)]
    return reminders, deliveries, unbound


def _valid_cost(value):
    """A real, finite, non-negative number. Booleans, NaN, infinity and negatives are not costs."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def cost(gateway_receipts, exchanges, turns, model_route):
    """Cost for budget settlement stays unknown (``usd: None``).

    MC's turn response carries no gateway receipt id, so the only possible attribution is by time
    window, and holding the proving loop exclusively does not prove that other scheduled callers of
    the gateway were idle. The window sum is reported as ``window_estimate_usd`` for information only,
    and only when every receipt in every window is valid (unique id, this model route, a real
    non-negative finite cost) and every exchange has one; otherwise the estimate is ``None`` too."""
    out = {"usd": None, "source": "gateway_receipts", "attribution": "time_window",
           "reason": "time-window attribution cannot prove exclusive gateway use; not settled",
           "window_estimate_usd": None, "receipt_ids": []}
    total, problems, seen = 0.0, [], set()
    replies = [t for t in turns if t["role"] in ("mc", "platform")]
    for ex, reply in zip(exchanges, replies):
        lo, hi = _ts(ex["sent_utc"]), _ts(ex.get("answered_utc") or ex["sent_utc"])
        inside = [r for r in gateway_receipts if lo <= _ts(r["occurred_at"]) <= hi]
        if not inside:
            problems.append(f"{ex.get('note_id')}: no gateway receipt ({reply['role']} reply)")
        for r in inside:
            rid = r.get("receipt_id")
            if not isinstance(rid, str) or not rid or rid in seen:
                problems.append(f"{ex.get('note_id')}: missing or repeated receipt id")
            seen.add(rid)
            if r.get("logical_model") != model_route:
                problems.append(f"{ex.get('note_id')}: other model traffic in the window")
            if not _valid_cost(r.get("cost_usd")):
                problems.append(f"{ex.get('note_id')}: a receipt without a valid cost")
            else:
                total += r["cost_usd"]
            out["receipt_ids"].append(rid)
    if problems:
        return {**out, "estimate_problems": problems}
    return {**out, "window_estimate_usd": round(total, 6)}


def _environment_ok(meta):
    """The server-reported marker must say dev and name the instance the operator expected."""
    marker, expected = meta.get("env_marker"), meta.get("expected_instance_id")
    return (meta.get("env") == "dev" and isinstance(marker, dict) and marker.get("environment") == "dev"
            and isinstance(expected, str) and expected.strip() and marker.get("instance_id") == expected)


def build_run_record(meta, exchanges, snapshots):
    """One contract-v2 run record. ``meta`` carries identity, env, the server-reported ``env_marker``
    ({"environment": "dev", "instance_id": ...}) with the ``expected_instance_id`` it must match,
    commit, policy hash, model route, suite version, journey, spend admission and run times;
    ``snapshots`` carries read-only store data: postit_rows, cos_memory_text, reminders_document,
    gateway_receipts, and optional retrievals/tool_calls (each already carrying its own source)."""
    principal = (meta.get("identity") or {}).get("principal")
    if not isinstance(principal, str) or not TEST_PRINCIPAL.fullmatch(principal) or (meta.get("identity") or {}).get("test") is not True:
        raise Refused("an isolated test identity is required")
    if not _environment_ok(meta):
        raise Refused("a server-reported dev marker matching the expected instance id is required")
    turns, errors = turns_from_exchanges(exchanges, principal)
    start, end = meta["started_utc"], meta["finished_utc"]
    boards, unbound = board_receipts(snapshots.get("postit_rows", []), turns, start, end)
    notes, more = note_receipts(snapshots.get("cos_memory_text", ""), turns, start, end); unbound += more
    reminders, deliveries, more = reminder_receipts(snapshots.get("reminders_document"), turns, start, end); unbound += more
    return {
        "contract": CONTRACT, "suite_version": meta["suite_version"], "journey": meta["journey"],
        "env": "dev", "env_marker": meta["env_marker"], "commit": meta["commit"],
        "mc_policy_sha256": meta.get("mc_policy_sha256"), "model_route": meta.get("model_route"),
        "identity": {"principal": principal, "test": True},
        "started_utc": _iso(start), "finished_utc": _iso(end),
        "spend_admission": meta.get("spend_admission"),
        "cost": cost(snapshots.get("gateway_receipts", []), exchanges, turns, meta.get("model_route")),
        "turns": turns,
        "receipts": {"board_rows": boards, "notes": notes, "reminders": reminders, "deliveries": deliveries,
                     "retrievals": list(snapshots.get("retrievals", [])), "tool_calls": list(snapshots.get("tool_calls", []))},
        "unbound": unbound, "errors": errors,
    }
