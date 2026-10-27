"""Voice turns stay hidden while the session is live, then appear once.

A drop with no end receipt shows the recovery line and reveals what was kept.
The spoken "we're done" proposes a summary. It does not confirm one.
"""
RECOVERY = "Voice ended unexpectedly. What was kept is below."
SUMMARY_PROMPT = "Shall I sum up?"


class VoiceReveal:
    def __init__(self):
        self._live = {}
        self._kept = {}
        self._revealed = set()
        self._proposals = {}

    def start(self, session_id):
        self._live[session_id] = []

    def keep(self, session_id, turn):
        if session_id in self._live:
            self._live[session_id].append(turn)
            return True
        return False

    def visible(self, session_id):
        if session_id in self._live:
            return []
        return list(self._kept.get(session_id, []))

    def end(self, session_id, *, dropped=False):
        turns = self._live.pop(session_id, self._kept.get(session_id, []))
        self._kept[session_id] = turns
        if session_id in self._revealed:
            return {"revealed": False, "turns": []}
        self._revealed.add(session_id)
        return {
            "revealed": True,
            "turns": turns,
            "recovery": RECOVERY if dropped else None,
        }

    def propose_summary(self, session_id, text):
        if not isinstance(text, str) or text.strip() in {"we're not done", "we are not done"}:
            return {"proposed": False}
        self._proposals[session_id] = {"text": SUMMARY_PROMPT, "confirmed": False}
        return {"proposed": True, "prompt": SUMMARY_PROMPT}

    def confirm_summary(self, session_id):
        proposal = self._proposals.get(session_id)
        if proposal is None:
            return {"confirmed": False}
        proposal["confirmed"] = True
        return {"confirmed": True, "authorizes": "summary only"}
