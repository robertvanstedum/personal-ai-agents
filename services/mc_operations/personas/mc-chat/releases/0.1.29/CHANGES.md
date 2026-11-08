# 0.1.29 — Front door, and a 12,000-character prompt (8 Nov)

- Robert, 8 Nov: "you can cut down the prompt to 12,000 so we have headroom to adjust."
- AGENTS.md and RUNTIME.openclaw.md rewritten shorter with the same rules. The start-up limit stays 16,000, so the difference is the headroom.
- AGENTS.md gains the front-door section (MC front door spec v0.5 §2), which also carries "answer now or say when" and the ledger sentence (Q4).
- **A persona release under decision Q8: not waived.** It may run on Dev so the owner-evidence suite can test it; it does not go to production until the personality cases pass on the frozen bundle.
- SOUL.md, voice.json and the handbook: unchanged, byte for byte. Roll back by moving the pin, Dockerfile and deploy script names to 0.1.28.
