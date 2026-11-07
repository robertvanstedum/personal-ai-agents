# 0.1.24 — the voice card carries Robert's manner on the direct path (6 Nov)

- Robert's direction of 6 Nov 2026, restoring VOICE_MANNER's clauses to the direct path. VOICE_MANNER (core/realtime_voice/controlled_config.py) reaches only the managed
  path; Dev and production run the direct path (rules + card + data), whose card had a short summary, so voice sounded
  generic and verbose. The card now restates the clauses: partner not receptionist, the same desk, one or two short
  sentences then the floor, no offers or invitations, quiet through silences, follow him when he cuts in, no recaps.
- voice.json: only the card text changed (OpenAI marin, Grok leo, accents and transcription as 0.1.23). AGENTS.md: only
  its header version. SOUL.md, RUNTIME.openclaw.md and the handbook (0.1.2): unchanged, byte for byte.
- One persona: the portal's voice pin (pin.json) and the container's Dockerfile both name this release; 0.1.23 left them
  on 0.1.22 and 0.1.23, with identical cards. Roll back by moving both to 0.1.23 (docs/runbooks/voice_prompts.md).
