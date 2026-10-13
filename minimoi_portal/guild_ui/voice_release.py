"""Release boundary for the quiet Guild alpha.

This slice does not include the shared controller CSRF/cancellation changes or
MC's TTS instructions support. No environment flag may enable partial voice.
Flip this only in the independently reviewed shared-voice integration, with its
real controller/signature tests and owner-approved budget/activation in place.
"""
SHARED_VOICE_READY = False
