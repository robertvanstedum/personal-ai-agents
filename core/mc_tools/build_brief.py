"""The brief of a delegated build (#693): the design Robert approved, at one exact revision, with his own message.

MC passes no task text. It names a private working document (``wa-…``) and a revision; the platform reads that revision
from the canonical working-document store, shows it on the proposal card, and keeps the sha256 of the exact text in the
proposal. When Robert answers yes with the proposal's code, the platform reads the same revision again and starts
nothing unless the text is the one he was shown and no newer revision has replaced it.

The store carries no marker for career, professional or private material (one data scope, "personal", for both leads).
The one thing it does record is who wrote a revision: a revision written by Chief of Staff's seat is CoS's own working
material and is refused here, because a build's branch and pull request are public. That is a provenance check, never
a content filter: the card says plainly that the result is public.
"""
from __future__ import annotations

import hashlib
import re

ARTIFACT_ID = re.compile(r"wa-[0-9a-f]{32}")
MAX_BRIEF_CHARS = 16_000            # the delegation envelope's bound (core/mc_delegation_runner/envelope.py)
EXCERPT_CHARS = 1_200               # how much of the design the card quotes word for word
COST_CAP_USD, MINUTES = 5, 60       # Robert, 27 Nov 2026 (#693)
HEADER = ("Build task from Robert, started by his own yes in MiniMoi. Build the approved design below, exactly that and "
          "no more. His own message comes first; the design follows it.")
PUBLIC = "The branch and pull request are public."

REFUSALS = {
    "design_not_found": "There is no working document with that id here. Nothing was proposed. Save the design with "
                        "minimoi_work_save first, then name its artifact_id and revision.",
    "design_revision_not_found": "That working document has no such revision. Nothing was proposed. Read it with "
                                 "minimoi_work_read and name its current revision.",
    "design_revision_superseded": "That is not the document's current revision, so it is not the design as it stands. "
                                  "Nothing was proposed. Read it again, tell Robert which revision is current, and "
                                  "propose that one.",
    "design_empty": "That working document has no text, so there is nothing to build from. Nothing was proposed.",
    "design_too_large": "That design is too long for one build brief (the brief holds 16,000 characters with Robert's "
                        "own message). Nothing was proposed. Split the work into smaller designs, or tell him it is too "
                        "big for one build.",
    "private_material_not_for_public_build": "That working document is Chief of Staff's own material, and a build ends "
                                             "as a public branch and pull request. Nothing was proposed. Tell Robert "
                                             "plainly; private or professional material is never built on this path.",
    "design_unreadable": "The working document could not be read as a design. Nothing was proposed.",
}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def design_from(doc, artifact_id: str, revision: int):
    """(design, None) from the store's answer to a read of ``artifact_id`` at ``revision``, or (None, refusal code).
    ``design`` is {"artifact_id", "revision", "title", "content", "sha256", "chars"}: the exact text and its digest."""
    if not isinstance(doc, dict):
        return None, "design_unreadable"
    status = doc.get("status")
    if status == "not_found":
        return None, "design_not_found"
    if status == "revision_not_found":
        return None, "design_revision_not_found"
    if (status != "found" or doc.get("artifact_id") != artifact_id or doc.get("revision") != revision
            or not isinstance(doc.get("content"), str) or not isinstance(doc.get("title"), str)):
        return None, "design_unreadable"
    if doc.get("superseded") is not False or doc.get("current_revision") != revision:
        return None, "design_revision_superseded"
    if doc.get("author") != "mc":
        return None, "private_material_not_for_public_build"
    content = doc["content"]
    if not content.strip():
        return None, "design_empty"
    return {"artifact_id": artifact_id, "revision": revision, "title": doc["title"].strip(), "content": content,
            "sha256": sha256(content), "chars": len(content)}, None


def compose(note_text: str, design: dict) -> str:
    """The whole brief: a fixed header, Robert's own message, then the approved design word for word."""
    return (f"{HEADER}\n\n--- Robert's message ---\n{note_text}\n\n"
            f"--- Approved design: {design['title']} (revision {design['revision']}, sha256 {design['sha256']}) ---\n"
            f"{design['content']}")


def excerpt(content: str) -> tuple[str, int]:
    """(the first EXCERPT_CHARS characters word for word, how many characters follow)."""
    return content[:EXCERPT_CHARS], max(0, len(content) - EXCERPT_CHARS)


def card_words(agent_name: str, job_title: str, design: dict) -> str:
    """What the platform shows Robert before his yes. Every sentence is something the path does."""
    shown, more = excerpt(design["content"])
    length = f"{design['chars']:,} characters"
    lead = (f"It is shown here in full ({length}):" if not more
            else f"Its first {len(shown):,} of {length} are shown here:")
    tail = (f"\n\n[{more:,} more characters follow in the working document. The whole of revision {design['revision']} "
            "is the brief.]" if more else "")
    return (f"Hand this design to {agent_name} to build (“{job_title}”)?\n\n"
            f"Design: “{design['title']}”, revision {design['revision']} (sha256 {design['sha256'][:12]}). {lead}\n\n"
            f"{shown}{tail}\n\n"
            f"What happens: {agent_name} builds it in a fresh copy of the repository on its own branch mc/…. The runner "
            "runs the tests itself. A DRAFT pull request is opened only if the runner is set up to publish. Nothing is "
            f"merged, released or deployed. Cost cap {COST_CAP_USD} USD, time limit {MINUTES} minutes. {PUBLIC} The "
            "brief is this design at this exact revision, with your own message; if the document changes before you "
            "answer, nothing starts.")
