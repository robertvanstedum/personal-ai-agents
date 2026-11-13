"""What a Master Craftsman turn did, in plain words (issue #545): "Ran 2 commands" ▸ the step names, then the answer.

The relay sends each tool step's name and timing (``{"t":"step"}``, docker/mc-agent/relay_v2.mjs); never its arguments or
result. Here a tool name becomes a plain label ("minimoi_github_issues" -> "Looked up GitHub issues"), the steps of one turn
are tallied, and the turn's record keeps them (``mc_turns.jsonl``, turn_log.py) as a small versioned block:

    {"v": 1, "items": [{"tool": "exec", "label": "Ran a command", "ok": true, "ms": 1200}, ...], "more": 0}

A turn without the block (every turn before this) renders exactly as before. A block of another version, or anything
malformed, is no steps, never a guess. Labels are made from the tool name alone, so nothing a tool was given or returned
can reach the page.
"""
from __future__ import annotations

import re

from .stream import STEP_NAME, Step

VERSION = 1
ITEMS_MAX = 50
LABEL_MAX = 80

# OpenClaw's own tools, and MiniMoi's (core/mc_tools/tools.json). A name not listed is said generically ("Used web
# fetch"), from the name only.
LABELS = {
    "read": "Read a file", "write": "Wrote a file", "edit": "Edited a file", "apply_patch": "Edited files",
    "exec": "Ran a command", "bash": "Ran a command", "process": "Checked a running command",
    "web_search": "Searched the web", "web_fetch": "Read a web page", "browser": "Used the browser",
    "memory_search": "Searched memory", "memory_get": "Read from memory", "image": "Looked at an image",
    "message": "Sent a message", "cron": "Checked the schedule",
    "minimoi_github_issues": "Looked up GitHub issues", "minimoi_github_get": "Read GitHub",
    "minimoi_issue_comment": "Proposed an issue comment", "minimoi_issue_create": "Proposed a new issue",
    "minimoi_issue_update": "Proposed an issue change",
    "minimoi_host_health": "Checked the host's health", "minimoi_host_read": "Read a file",
    "minimoi_host_list": "Listed a folder", "minimoi_working_write": "Saved a working file",
    "minimoi_describe_house": "Looked at MiniMoi's domains", "minimoi_describe_domain": "Looked at a domain",
    "minimoi_guest_requests": "Read the guest requests", "minimoi_guest_request_reject": "Proposed rejecting guest requests",
    "minimoi_data_repair_ticket": "Proposed a repair ticket", "minimoi_lesson_propose": "Proposed a lesson",
    "minimoi_keep_preference": "Kept a preference", "minimoi_revoke_preference": "Undid a preference",
    "minimoi_record_gap": "Recorded a gap", "minimoi_withdraw_gap": "Withdrew a gap",
    "minimoi_delegate_research": "Proposed a research hand-off", "minimoi_delegate_build": "Proposed a build hand-off",
    "minimoi_access_invite": "Proposed a guest invite",
    "minimoi_board_notes_query": "Searched the Board", "minimoi_board_note_read": "Read a Board note",
    "minimoi_board_note_create": "Saved a Board note", "minimoi_board_note_update": "Changed a Board note",
    "minimoi_board_operation_read": "Checked a Board action", "minimoi_board_operation_undo": "Undid a Board action",
    "minimoi_clarify_action": "Asked a question first",
    "minimoi_conversation_export": "Exported the conversation", "minimoi_artifact_recover": "Recovered an artifact",
    "minimoi_agent_contact": "Contacted a builder", "minimoi_agent_status": "Checked a builder's reply",
    "minimoi_agent_watch": "Set a watch on a request", "minimoi_agent_ask": "Asked a builder",
    "minimoi_agent_request": "Asked a builder for a review", "minimoi_agent_request_withdraw": "Withdrew a builder request",
    "minimoi_build_log_add": "Added to the Build Log", "minimoi_build_log_search": "Searched the Build Log",
    "minimoi_build_log_add_to": "Added to a Build Log item", "minimoi_build_item": "Started a build",
    "minimoi_career_trial": "Ran the Career Kit check", "minimoi_recall": "Looked through past conversations",
    "minimoi_publish_update": "Published a build update",
    "minimoi_task_list": "Read the task list", "minimoi_task_delegate": "Handed off a task step",
    "minimoi_task_submit": "Submitted a task result", "minimoi_task_accept": "Accepted a task result",
    "minimoi_task_adopt": "Moved a task onto a correction",
    "minimoi_helper_ask": "Asked the Apprentice", "minimoi_helper_result": "Checked the Apprentice's result",
    "minimoi_helper_review": "Reviewed the Apprentice's result",
    "minimoi_work_read": "Read a working document", "minimoi_work_save": "Saved a working document",
}

# The row's headline when every step is of one kind ("Ran 2 commands"); a mix is "Ran 3 steps".
KINDS = {
    "exec": "command", "bash": "command",
    "read": "file_read", "minimoi_host_read": "file_read",
    "write": "file_change", "edit": "file_change", "apply_patch": "file_change", "minimoi_working_write": "file_change",
    "web_search": "web_search", "web_fetch": "web_page",
    "minimoi_github_issues": "github", "minimoi_github_get": "github",
}
HEADLINES = {
    "command": ("Ran a command", "Ran {n} commands"),
    "file_read": ("Read a file", "Read {n} files"),
    "file_change": ("Changed a file", "Changed {n} files"),
    "web_search": ("Searched the web", "Searched the web {n} times"),
    "web_page": ("Read a web page", "Read {n} web pages"),
    "github": ("Checked GitHub", "Checked GitHub {n} times"),
}


def label_for(name: str) -> str:
    """A plain label from the tool's name alone."""
    name = str(name or "")
    if not STEP_NAME.fullmatch(name):
        return "Used a tool"
    if name in LABELS:
        return LABELS[name]
    words = re.sub(r"[_.:-]+", " ", re.sub(r"^minimoi_", "", name)).strip()
    return f"Used {words}"[:LABEL_MAX] if words else "Used a tool"


def headline(tools: list[str], more: int = 0) -> str:
    n = len(tools) + max(0, more)
    kinds = {KINDS.get(t) for t in tools}
    if n and not more and len(kinds) == 1 and None not in kinds:
        one, many = HEADLINES[kinds.pop()]
        return one if n == 1 else many.format(n=n)
    return "Ran 1 step" if n == 1 else f"Ran {n} steps"


class StepTally:
    """The steps of one streamed turn, in the order they began."""

    def __init__(self):
        self._steps: dict[int, dict] = {}
        self.more = 0
        self._offset = 0

    def new_run(self):
        """A second run takes over the turn (a switched or deeper attempt): its steps count on after the first run's."""
        self._offset = max(self._steps, default=0)

    def add(self, step: Step) -> dict | None:
        """Record a step event; returns the browser's event for it (labels only), or None to drop it."""
        n = step.n + self._offset
        known = self._steps.get(n)
        if known is None:
            if len(self._steps) >= ITEMS_MAX:
                if step.phase == "start":
                    self.more += 1
                return None
            known = self._steps[n] = {"tool": step.name, "label": label_for(step.name), "ok": None, "ms": None}
        elif known["tool"] != step.name:
            return None                              # the same place, another tool: not this run's step
        if step.phase == "end":
            known["ok"], known["ms"] = step.ok, step.ms
        tools = [s["tool"] for s in self._steps.values()]
        return {"t": "step", "n": n, "label": known["label"], "phase": step.phase,
                "count": len(tools) + self.more, "headline": headline(tools, self.more)}

    def __len__(self):
        return len(self._steps)

    def record(self) -> dict | None:
        """The block kept with the turn, or None when the turn used no tool."""
        if not self._steps:
            return None
        items = [{k: v for k, v in s.items() if v is not None} for _, s in sorted(self._steps.items())]
        return {"v": VERSION, "items": items, "more": self.more}


def view(block) -> dict | None:
    """What a page shows for a kept block: {"headline", "labels", "count"}; None for no steps, an old turn, another
    version or anything malformed."""
    if not isinstance(block, dict) or block.get("v") != VERSION or not isinstance(block.get("items"), list):
        return None
    tools = [i["tool"] for i in block["items"][:ITEMS_MAX]
             if isinstance(i, dict) and isinstance(i.get("tool"), str) and STEP_NAME.fullmatch(i["tool"])]
    if not tools:
        return None
    more = block.get("more") if isinstance(block.get("more"), int) and not isinstance(block.get("more"), bool) else 0
    more = max(0, more)
    return {"headline": headline(tools, more), "labels": [label_for(t) for t in tools], "count": len(tools) + more}


__all__ = ["StepTally", "label_for", "headline", "view", "VERSION"]
