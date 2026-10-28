"""Refusals every voice host shares. SessionRefused lives here, not in the managed sideband (controlled_session), so the
managed modules can be retired without breaking the direct path (Spec 165 §4.2). controlled_session re-exports it: one
class, so an except clause written against either name catches both."""
from core.realtime_voice.budget import VoiceBudgetError


class SessionRefused(VoiceBudgetError):
    pass
