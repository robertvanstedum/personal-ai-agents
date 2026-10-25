# 0.1.10 — correction capture (judgment spec v0.2)

Base: 0.1.8, the live projection on 2026-10-25. It is not built on 0.1.9, whose native-parity tool changes are not activated. The portable AGENTS.md gains one section, "Correction capture (standing)", from SETUP_MC_DAILY_CAPTURE_2026-10-25. SOUL and the runtime binding are unchanged.

Departures from the setup text, for review:
- Capture happens in the reply where MC takes the correction, not "at session end". A Guild conversation has no defined end event (Codex pickup note).
- The block is fenced as `capture` so the collector can find it deterministically. MC has no file-write tool, so "this session's records" is its persisted transcript.
- The template is inline because MC never reads the store file.
- The cards receipt goes in "your first reply of a conversation", because a session has no separate start message.

Projection budget raised from 14,000 to 16,000 characters to fit the section; the projection is 14,948. OpenClaw truncates at 20,000 per file (agents.defaults.bootstrapMaxChars default) and 60,000 in total, so 4,000+ of headroom remains. Private context is preserved byte for byte.

When 0.1.9's parity changes are activated, they must rebase onto 0.1.10 (as 0.1.11), so this section is not lost.
