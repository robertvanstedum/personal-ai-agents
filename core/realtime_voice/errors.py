"""Refusals every voice host shares. SessionRefused lives here, not in the managed sideband (controlled_session), so the
managed modules can be retired without breaking the direct path (Spec 165 §4.2). controlled_session re-exports it: one
class, so an except clause written against either name catches both."""
from core.realtime_voice.budget import VoiceBudgetError


class SessionRefused(VoiceBudgetError):
    pass


class VoiceUnavailable(SessionRefused):
    """Voice is refused before anything is recorded or reserved, with its reason in the owner's words (Spec 165 §6.3: a
    session text still over its bound once continuity and memory are cut). The direct host answers 503 with those words."""
