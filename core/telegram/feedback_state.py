"""Process-local feedback outcome tracking; not cross-process exactly-once."""
from threading import Lock
from core.telegram.feedback_writer import result


class FeedbackTracker:
    def __init__(self):
        self._lock = Lock()
        self._states = {}

    def execute(self, key, writer):
        with self._lock:
            state = self._states.get(key)
            if state == "confirmed":
                return {**result("confirmed", "Already recorded"), "duplicate": True}
            if state is not None:
                return result(state, "Feedback is pending or uncertain; check before trying again")
            self._states[key] = "pending"
        try:
            answer = writer()
            if not isinstance(answer, dict) or answer.get("outcome") not in {"refused", "confirmed", "uncertain"}:
                answer = result("uncertain", "Feedback outcome could not be confirmed")
        except Exception:
            answer = result("uncertain", "Feedback outcome could not be confirmed")
        with self._lock:
            if answer["outcome"] == "refused":
                del self._states[key]
            else:
                self._states[key] = answer["outcome"]
        return answer
