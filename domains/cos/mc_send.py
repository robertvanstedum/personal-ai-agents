"""CoS's bound ``cos_send_to_mc`` tool: Robert's own words go to Master Craftsman, who answers on his own route.

Robert, 6 Nov: "CoS should send to MC. MC can handle the claude part."

The model supplies nothing but the wish to send: the tool takes no text at all. On the host:
- the words come from the bound owner turn (origin ``owner``: his saved history turn; ``service:telegram``: the text of
  the signed request, kept against the binding), never from model text;
- those words must clearly tell CoS to send to MC (``parse``). Meetings, Private conversations and turns MC itself relayed
  (``service:mc``, so no loops) are refused;
- "send this to MC" attaches the latest prior message of that conversation with its true author. It is quoted material and
  a separate part, never his words (core/owner_referent.py);
- one delivery per CoS turn, recorded before the POST: a retry reads the record and never posts again. An unknown outcome
  stays unknown. CoS reports only what the receipt says.

Signing: the same assertion format as owner_assertion, in the other direction. cos-scheduler already holds the key it shares
with the portal; audience ``mc-from-cos`` and subject ``service:cos`` keep this direction apart from the portal's own
``cos-owner`` assertions, and MC's reply back is ``service:mc`` on one exact path (owner_assertion.MC_REPLY_PATH).
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

import requests

from core import owner_referent as R
from core import portal_assertion as PA
from core.canonical_json import digest
from domains.cos.owner_words import points_only

FLAG = "MINIMOI_COS_TO_MC"
URL_VAR = "COS_TO_MC_URL"
AUDIENCE = "mc-from-cos"
SUBJECT = "service:cos"
HEADER = "X-CoS-Assertion"
TARGET_PATH = "/internal/mc/v1/from-cos"
SCHEMA = "minimoi.cos.to_mc/1"
WORDS_MAX = 2000                 # MC keeps an owner note up to this (guild_ui.stores.NOTE_MAX)
ATTACH_MAX = 3000                # the attached item; a longer one is cut visibly
TIMEOUT_S = 10                   # the plugin allows a tool call 15 s
REPLY_NOTE_MAX = 8000            # the stream note contract
WHERE = "MC's answer comes back into this CoS chat as a note, and it is always in MC's history under Through CoS."
_DELIVERY = re.compile(r"[0-9a-f]{40}")
_log = logging.getLogger("cos_tools")

_LEAD = re.compile(r"(?:(?:!cos|please|just|hey|ok|okay|so|and|then|also|thanks|cos|chief\s+of\s+staff)\b[ ,]*"
                   r"|(?:can|could|would|will)\s+you\s+|go\s+ahead\s+and\s+|i\s+(?:want|need)\s+you\s+to\s+"
                   r"|i(?:'|’)?d\s+like\s+you\s+to\s+|try\s+(?:to|and)\s+)*", re.I)
_MC = r"(?:mc|master\s+craftsman)\b"
_NAMED = re.compile(r"\b(?:tell|ask|message|ping|notify|inform|contact|show|let|have|get|write\s+to|reach\s+out\s+to"
                    r"|get\s+in\s+touch\s+with)\s+" + _MC + r"(?:\s+know\b)?", re.I)
_CARRY = re.compile(r"\b(?:send|pass|forward|give|hand|relay|share|show|take)\b(?P<mid>.*?)\b(?:to|with)\s+" + _MC, re.I | re.S)
_CONNECT = re.compile(r"[\s,:;\-–—]*(?:(?:to|that|saying|and|please)\b)?[\s,:;\-–—]*", re.I)
_NOTHING = re.compile(r"(?:nothing|no\b|not\b|never\b)", re.I)
_ACK = re.compile(r"(?:(?:thanks|thank\s+you|ok|okay|great|perfect|good|nice|yes|yeah|right|cool)[\s,.!]*)*", re.I)
_SPLIT = re.compile(r"[.!?;\n]")


def enabled(environ=None) -> bool:
    return str((os.environ if environ is None else environ).get(FLAG, "")).strip().lower() in ("1", "true", "on", "yes")


def _blank(match):
    return re.sub(r"[^\n]", " ", match.group(0))


def _masked(text: str) -> str:
    """The same length, with quoted, fenced, blockquoted and indented text blanked: words inside them are data, never his
    instruction. An unclosed quote blanks to the end."""
    out = re.sub(r"(?ms)^ {0,3}(`{3,}|~{3,}).*?(?:^ {0,3}\1[`~]*\s*$|\Z)", _blank, text)
    out = re.sub(r"(?m)^(?: {4}|\t|\s*>).*$", _blank, out)
    for pattern in (r"`[^`]*(?:`|\Z)", r'"[^"]*(?:"|\Z)', r"“[^”]*(?:”|\Z)", r"(?<!\w)'[^']*'"):
        out = re.sub(pattern, _blank, out)
    return out


def _instruction(text: str):
    """(clause start, match, kind) for the first clause that opens by telling CoS to send to MC, or None."""
    masked, start = _masked(text), 0
    for end in [m.start() for m in _SPLIT.finditer(masked)] + [len(masked)]:
        clause = masked[start:end]
        found = [(m.start(), m, kind) for kind, rx in (("named", _NAMED), ("carry", _CARRY)) for m in [rx.search(clause)] if m]
        if found:
            at, match, kind = min(found, key=lambda f: f[0])
            if _LEAD.fullmatch(clause[:at].lstrip()):
                return start, match, kind
        start = end + 1
    return None


def parse(text) -> dict | None:
    """What his words ask, from his words alone. None: they do not clearly tell CoS to send to MC. Otherwise
    {"words": <a verbatim slice of them>, "pointer": <the phrase pointing at an earlier message, or None>}; "words" None means
    he asked and named nothing to send."""
    if not isinstance(text, str) or not text.strip():
        return None
    found = _instruction(text)
    if found is None:
        return None
    clause_start, match, kind = found
    at = clause_start + match.start()
    after = text[clause_start + match.end():]
    tail = after[_CONNECT.match(after).end():].strip()
    mid = "" if kind == "named" else text[clause_start + match.start("mid"): clause_start + match.end("mid")].strip()
    if _NOTHING.match(mid) or (not mid and _NOTHING.fullmatch(tail.rstrip(".!? "))):
        return None
    phrase = mid if R.is_bare_pointer(mid) else tail if not mid and R.is_bare_pointer(tail) else None
    if phrase is not None:
        rest = "" if phrase is tail or not tail.strip(" .!?") else tail
        if rest:
            return {"words": rest, "pointer": phrase}          # "send this to MC to ask Claude Code": his instruction, plus the item
        before = text[:clause_start].strip()
        if before and not _ACK.fullmatch(before):
            return {"words": before, "pointer": None}          # "GitHub is down. Send that to MC": "that" is his own sentence
        return {"words": text[at:].strip(), "pointer": phrase}
    if mid and points_only(mid):                               # "send a message to MC: ..." names nothing by itself
        mid = ""
    words = tail if not mid else text[clause_start + match.start("mid"):].strip() if tail else mid
    if not words or points_only(words):
        return {"words": None, "pointer": None}
    return {"words": words, "pointer": R.pointer_inside(words)}


def delivery_id(environment: str, turn_id: str) -> str:
    return digest(f"{environment}\0cos-to-mc\0{turn_id}")[7:47]


class CosMcSend:
    def __init__(self, *, bindings, history, url, key_file, env, post=None):
        self.bindings, self.history, self.url = bindings, history, url
        self.key_file, self.env = key_file, env
        self.post = post or requests.post

    def serve(self, binding, op: str, args: dict) -> dict:
        if op != "send":
            return _refuse(400, "invalid_operation", "The only operation is send. Nothing was sent.")
        if args:                                               # no text is ever taken from the model
            return _refuse(422, "model_text_refused", "This tool takes no text: it carries Robert's own words. Nothing was sent.")
        if binding.scope != "owner" or binding.origin == "meeting":
            return _refuse(403, "not_in_a_meeting", "A meeting cannot send to MC. Nothing was sent.")
        if binding.origin not in ("owner", "service:telegram"):
            return _refuse(403, "relayed_turn_refused", "This turn came from MC, so it cannot go back to MC. Nothing was sent.")
        did = delivery_id(binding.environment, binding.turn_id)
        earlier = self.bindings.delivery(did)
        if earlier is not None:
            return _from_record(earlier, replayed=True)
        source, conversation, refusal = self._words(binding)
        if refusal:
            return refusal
        asked = parse(source)
        if asked is None:
            return _refuse(403, "not_asked", "Robert's message does not tell you to send something to MC. Nothing was sent.")
        words = asked["words"]
        if not words:
            return _refuse(422, "nothing_to_send", "Nothing was sent: Robert did not say what to send. Ask him what MC should get.")
        if words not in source:                                # the check that makes "verbatim" true, whatever parse did
            return _refuse(409, "words_not_verbatim", "Nothing was sent: those words are not Robert's, exactly.")
        if len(words) > WORDS_MAX:
            return _refuse(422, "words_too_long", f"Nothing was sent: that is over {WORDS_MAX} characters. Ask Robert to shorten it or attach it in MC.")
        attached = None
        if asked["pointer"]:
            attached, refusal = self._referent(conversation, asked["pointer"])
            if refusal:
                return refusal
        return self._deliver(binding, did, words, attached, conversation)

    def _words(self, binding):
        if binding.origin == "service:telegram":
            words = self.bindings.owner_words(binding.turn_id)
            if not isinstance(words, str) or not words.strip():
                return None, None, _refuse(409, "owner_words_not_found", "Nothing was sent: CoS could not read Robert's message.")
            return words, None, None
        ref = binding.source_ref
        if not isinstance(ref, dict):
            return None, None, _refuse(409, "owner_words_not_found", "Nothing was sent: this message was not saved in CoS history.")
        try:
            conversation = self.history.conversation(ref["conversation_id"])
            turn = self.history.turn(ref["conversation_id"], ref["turn_id"])
        except OSError:
            conversation = turn = None
        if conversation is None or turn is None or not isinstance(turn.get("user_text"), str):
            return None, None, _refuse(409, "owner_words_not_found", "Nothing was sent: CoS could not read Robert's message.")
        if conversation.get("private"):
            return None, None, _refuse(403, "private_conversation", "This is a Private conversation, so nothing goes to MC. Nothing was sent.")
        return turn["user_text"], {"conversation_id": ref["conversation_id"], "turn": turn}, None

    def _referent(self, conversation, phrase):
        ask = _refuse(409, "no_referent", "Nothing was sent: I can't tell what that points at. Ask Robert what to send, or to say it in this message.")
        if conversation is None:                               # Telegram keeps no conversation record here
            return None, _refuse(409, "no_referent", "Nothing was sent: Telegram has no earlier message on record here. Ask Robert to say it in this message.")
        turn = conversation["turn"]
        try:
            page = self.history.turns(conversation["conversation_id"], before_seq=turn["seq"], limit=1)
        except Exception:
            return None, ask
        if not page["items"]:
            return None, ask
        prior = page["items"][-1]
        last = prior.get("answered_at") or prior["created_at"]
        # A note arrived after that turn: "this" could be either, so ask.
        if any(_after(n["created_at"], last) and not _after(n["created_at"], turn["created_at"]) for n in page.get("notes") or ()):
            return None, _refuse(409, "ambiguous_referent", "Nothing was sent: \"this\" could be the note or the message before it. Ask Robert which, or to say it in this message.")
        hint = R.author_hint(phrase)
        reply = prior.get("reply_text") if prior.get("status") in ("answered", "recovered") else None
        if hint == "owner" or (hint is None and not (isinstance(reply, str) and reply.strip())):
            text, author, at = prior.get("user_text"), "Robert's message", prior["created_at"]
        else:
            text, author, at = reply, "CoS's reply", last
        if not isinstance(text, str) or not text.strip():
            return None, ask
        cut_text, was_cut = R.cut(text, ATTACH_MAX)
        return {"author": "robert" if author.startswith("Robert") else "cos", "label": author + R.time_label(at),
                "text": cut_text, "cut": was_cut, "full_chars": len(text)}, None

    def _deliver(self, binding, did, words, attached, conversation):
        key = PA.load_key(self.key_file)
        if key is None or not self.env:
            return _refuse(503, "signing_unavailable", "Nothing was sent: the link to MC is not set up here.")
        record = {"schema": SCHEMA, "delivery_id": did, "words": words, "attachment": attached, "origin": binding.origin}
        raw = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        cid = conversation["conversation_id"] if conversation else None
        if not self.bindings.claim_delivery(did, binding.turn_id, cid):
            return _from_record(self.bindings.delivery(did), replayed=True)
        try:
            header = PA.sign(key, audience=AUDIENCE, subject=SUBJECT, method="POST", target=TARGET_PATH, body=raw, env=self.env)
            response = self.post(self.url, data=raw, timeout=TIMEOUT_S, allow_redirects=False,
                                 headers={"Content-Type": "application/json", HEADER: header})
        except (requests.ConnectTimeout, requests.ConnectionError) as exc:
            state = "refused" if _never_connected(exc) else "unknown"
            return self._close(did, state, {"code": "mc_unreachable" if state == "refused" else "outcome_unknown"})
        except Exception:
            _log.warning("cos to mc: outcome unknown")
            return self._close(did, "unknown", {"code": "outcome_unknown"})
        state, receipt = _classify(response)
        return self._close(did, state, receipt)

    def _close(self, did, state, receipt):
        try:
            self.bindings.finish_delivery(did, state, receipt)
        except Exception:
            _log.exception("cos to mc: receipt not recorded")
            return _from_record({"state": "unknown", "receipt": {"code": "outcome_unknown"}}, replayed=False)
        return _from_record({"state": state, "receipt": receipt}, replayed=False)

    def reply(self, body) -> tuple[int, dict]:
        """MC's answer, on the signed reply path: one note per delivery, into the conversation THIS service recorded."""
        if not isinstance(body, dict) or set(body) - {"delivery_id", "outcome", "text"} \
                or not _DELIVERY.fullmatch(str(body.get("delivery_id"))) or body.get("outcome") not in ("answered", "not_answered") \
                or not isinstance(body.get("text"), str) or not body["text"].strip():
            return 400, {"error": "reply_invalid"}
        row = self.bindings.delivery(body["delivery_id"])
        if row is None or row["state"] != "sent":
            return 404, {"error": "no_such_delivery"}
        from domains.cos.history import HistoryRefused
        note_id = "consult:mc-" + body["delivery_id"]
        try:
            cid = row.get("conversation_id") or self.history.ensure_default()["conversation_id"]
            head = "MC answered what you sent through CoS:\n\n" if body["outcome"] == "answered" else "MC did not answer what you sent through CoS:\n\n"
            note = {"schema": "minimoi.cos.stream_note/1", "note_id": note_id, "conversation_id": cid, "kind": "consult",
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "text": _bounded(head + body["text"])}
            self.history.add_note(note)
        except HistoryRefused as exc:
            if exc.code != "note_id_reused":                   # a retry carries a new time: the first note stands
                return 409, {"error": exc.code}
        except OSError:
            return 503, {"error": "history_unavailable"}
        return 200, {"state": "kept"}


def _bounded(text):
    marker = "\n\n[Cut here: the whole answer is in MC's history under Through CoS.]"
    return text if len(text) <= REPLY_NOTE_MAX else text[: REPLY_NOTE_MAX - len(marker)] + marker


def _after(a, b) -> bool:
    from domains.cos.history import _instant
    return _instant(a) > _instant(b)


def _never_connected(exc) -> bool:
    if isinstance(exc, requests.ConnectTimeout):
        return True
    return "NewConnectionError" in repr(exc) or "Failed to establish a new connection" in str(exc)


def _classify(response) -> tuple[str, dict]:
    """(sent | refused | unknown, receipt) from MC's answer. Sent only on MC's own accepted receipt."""
    status = getattr(response, "status_code", 0)
    try:
        body = response.json()
    except Exception:
        body = None
    body = body if isinstance(body, dict) else {}
    if status in (200, 202) and body.get("state") == "accepted" and isinstance(body.get("conversation_id"), str):
        return "sent", {k: body.get(k) for k in ("conversation_id", "note_request_id", "ledger_id", "replayed")}
    if body.get("state") == "refused":
        return "refused", {"code": str(body.get("code") or "refused")[:60], "words": str(body.get("message") or "")[:400]}
    if status in (401, 403, 404, 413, 415):                    # never admitted: the route, signature or size turned it away
        return "refused", {"code": f"http_{status}"}
    return "unknown", {"code": "outcome_unknown"}


def _refuse(status, error, message):
    return {"status": status, "body": {"error": error, "message": message}}


def _from_record(row, *, replayed):
    state, receipt = row["state"], row.get("receipt") or {}
    if state == "sent":
        return {"status": 200, "body": {"schema": SCHEMA, "state": "sent", **receipt, "replayed": replayed,
                                        "message": "Sent to MC, with a receipt. " + WHERE}}
    if state == "refused":
        words = receipt.get("words") or "MC could not take it just now."
        return _refuse(409, receipt.get("code") or "refused", f"Not sent. {words} Tell Robert plainly; he can send it again in a new message.")
    # sending (an earlier attempt that never finished) or unknown: never sent again from here.
    return {"status": 202, "body": {"schema": SCHEMA, "state": "unknown", "error": "outcome_unknown",
                                    "message": "CoS could not confirm whether MC received it, so it was not sent again. "
                                               "Tell Robert it is unknown, and that MC's history under Through CoS will show it if it arrived."}}


def from_environ(environ, *, bindings, history):
    """The tool when switched on and connected, else None (the listener refuses it)."""
    if not enabled(environ) or bindings is None or history is None:
        return None
    url = str(environ.get(URL_VAR) or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    return CosMcSend(bindings=bindings, history=history, url=url, key_file=environ.get("COS_OWNER_ASSERTION_KEY_FILE"),
                     env=environ.get("COS_OWNER_ASSERTION_ENV") or None)
