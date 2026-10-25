"""Owner preferences in the lesson ledger, below the conversational layer (spec v1.2 §10.1–10.2, M1.3).

Every write carries an operation id fixed by the tool service before anything is appended (the authenticated owner note,
the capability and the canonical request), and is idempotent by it: a retry finds its own event and never appends
again. A preference keeps Robert's exact words and their source (conversation, note, edition hash, span), takes the
next L-number (history is never renumbered), and can supersede an earlier one with both kept as history. Undo is a
new recorded revocation, conditional on the lesson still being at the revision that was kept. A suggestion whose
lasting scope was unclear is recorded as pending: retrievable, never active, and adopted only when Robert resumes it.

Callers must screen the text (core.mc_lessons.commands.clean) and establish the source; nothing here trusts a model.
"""
from __future__ import annotations

from .commands import MAX_ACTIVE
from .ledger import LessonLedger, text_sha


class PreferenceRefused(Exception):
    """A fixed code: the write was not made."""


def read_operation(ledger: LessonLedger, operation_id: str):
    """The event an operation wrote, or None. Read-only."""
    return ledger.read().operations.get(operation_id)


def suggestion(ledger: LessonLedger, suggestion_id: str):
    return ledger.read().suggestions.get(suggestion_id)


def pending_suggestions(ledger: LessonLedger, conversation_id: str | None = None):
    st = ledger.read()
    return [s for s in st.suggestions.values() if s["state"] == "pending"
            and (conversation_id is None or s["source"]["conversation"] == conversation_id)]


def adopt_preference(ledger: LessonLedger, *, operation_id: str, text: str, original: str, source: dict,
                     confirming: dict, policy_version: str, authority_ref: str, supersedes: int | None = None,
                     from_suggestion: str | None = None) -> dict:
    """{"state": "kept"|"existing"|"already", "lesson_no", "revision", "text"}: kept now, the same operation already
    kept it (a retry), or an active lesson already says exactly this (nothing appended)."""
    with ledger.writing() as (st, append):
        done = st.operations.get(operation_id)
        if done is not None:
            if done["type"] != "preference_kept":
                raise PreferenceRefused("operation_reused")
            return {"state": "already", "lesson_no": done["lesson_no"], "revision": done["n"], "text": done["text"]}
        sha = text_sha(text)
        for no, lesson in st.active():
            if lesson["sha256"] == sha:
                return {"state": "existing", "lesson_no": no, "revision": lesson.get("revision"), "text": lesson["text"]}
        if supersedes is not None:
            old = st.lessons.get(supersedes)
            if old is None or old["revoked_at"] is not None:
                raise PreferenceRefused("nothing_to_replace")
        elif len(st.active()) >= MAX_ACTIVE:
            raise PreferenceRefused("too_many")
        if from_suggestion is not None:
            pending = st.suggestions.get(from_suggestion)
            if pending is None or pending["state"] != "pending":
                raise PreferenceRefused("suggestion_not_pending")
        no = st.next_no
        st = append({"type": "preference_kept", "operation_id": operation_id, "lesson_no": no, "text": text,
                     "sha256": sha, "original": original, "kind": "preference", "scope": "mc", "source": source,
                     "confirming": confirming, "policy_version": policy_version, "authority_ref": authority_ref,
                     "supersedes": supersedes, "from_suggestion": from_suggestion})
        return {"state": "kept", "lesson_no": no, "revision": st.lessons[no]["revision"], "text": text}


def revoke_preference(ledger: LessonLedger, *, operation_id: str, lesson_no: int, expected_revision: int,
                      source: dict, policy_version: str) -> dict:
    """{"state": "revoked"|"already", "lesson_no", "text"}; PreferenceRefused("changed") when the lesson is no longer the
    revision that was kept (superseded, revoked, or a different lesson): nothing is undone and later work is kept."""
    with ledger.writing() as (st, append):
        done = st.operations.get(operation_id)
        if done is not None:
            if done["type"] != "preference_revoked":
                raise PreferenceRefused("operation_reused")
            return {"state": "already", "lesson_no": done["lesson_no"], "text": st.lessons[done["lesson_no"]]["text"]}
        lesson = st.lessons.get(lesson_no)
        if lesson is None:
            raise PreferenceRefused("no_such_lesson")
        if lesson["revoked_at"] is not None or lesson.get("revision") != expected_revision:
            raise PreferenceRefused("changed")
        append({"type": "preference_revoked", "operation_id": operation_id, "lesson_no": lesson_no,
                "expected_revision": expected_revision, "source": source, "policy_version": policy_version})
        return {"state": "revoked", "lesson_no": lesson_no, "text": lesson["text"]}


def record_suggestion(ledger: LessonLedger, *, operation_id: str, suggestion_id: str, quote: str, text: str,
                      source: dict) -> dict:
    """A preference whose lasting scope is unclear: pending, never active. The same words already pending in this
    conversation return that suggestion (MC must not ask again)."""
    with ledger.writing() as (st, append):
        done = st.operations.get(operation_id)
        if done is not None:
            return {"state": "already", "suggestion_id": done["suggestion_id"]}
        for s in st.suggestions.values():
            if s["state"] == "pending" and s["text"] == text and s["source"]["conversation"] == source["conversation"]:
                return {"state": "existing", "suggestion_id": s["suggestion_id"]}
        append({"type": "suggestion_recorded", "operation_id": operation_id, "suggestion_id": suggestion_id,
                "quote": quote, "text": text, "source": source})
        return {"state": "recorded", "suggestion_id": suggestion_id}
