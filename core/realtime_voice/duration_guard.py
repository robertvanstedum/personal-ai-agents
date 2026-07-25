"""
core/realtime_voice/duration_guard.py — session warning/hard-stop safeguard.

Per _working/CLAUDE_CODE_BUILD_SPEC_voice_realtime_2026-07-24.md Section 12
"Initial session safeguard": defaults are deliberately 20/30 minutes, not
the earlier, too-restrictive 10/15-minute proposal -- language practice
should allow an unhurried conversation while still placing a bounded
production cost and unattended-session safeguard. Both values are
configuration (env), not code constants.
"""
import os

_DEFAULT_WARNING_MINUTES = 20
_DEFAULT_MAX_MINUTES = 30


class DurationGuard:
    def __init__(
        self,
        warning_minutes: int | None = None,
        max_minutes: int | None = None,
    ) -> None:
        self.warning_minutes = warning_minutes if warning_minutes is not None else int(
            os.environ.get("VOICE_SESSION_WARNING_MINUTES", _DEFAULT_WARNING_MINUTES)
        )
        self.max_minutes = max_minutes if max_minutes is not None else int(
            os.environ.get("VOICE_SESSION_MAX_MINUTES", _DEFAULT_MAX_MINUTES)
        )
        if self.warning_minutes > self.max_minutes:
            raise ValueError(
                f"warning_minutes ({self.warning_minutes}) must not exceed "
                f"max_minutes ({self.max_minutes})"
            )

    def status(self, elapsed_seconds: float) -> str:
        """Returns "ok", "warning", or "stop" for the given elapsed time."""
        elapsed_minutes = elapsed_seconds / 60.0
        if elapsed_minutes >= self.max_minutes:
            return "stop"
        if elapsed_minutes >= self.warning_minutes:
            return "warning"
        return "ok"
