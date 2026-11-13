# 0.1.33 — voice answers status questions from the platform status

12 November 2026 · Claude Code · #542.

- **Voice card only.** Robert asked MC by voice "How are things?" and got small talk, then "the deployment went out" and got "I can't confirm that from here". Typed MC knew the release (`e6017b5`) and the job health. Voice sessions now start with a bounded platform status digest (job health first, what production last deployed, what waits on Robert; `core/realtime_voice/status_digest.py`), and the card gets one line: answer status questions from that status or the app's answer, problems first, in two or three sentences, and say plainly when neither is there.
- FF-16's short-answer rule is unchanged. SOUL, AGENTS (apart from its header version), RUNTIME binding and handbook 0.1.3 are byte-identical to 0.1.32. No new tool, permission or authority.
