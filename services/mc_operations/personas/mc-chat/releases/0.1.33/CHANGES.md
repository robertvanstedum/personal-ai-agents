# 0.1.33 — voice answers status questions from the platform status

12 November 2026 · Claude Code · #542.

- **Voice card only.** Robert asked MC by voice "How are things?" and got small talk, then "the deployment went out" and got "I can't confirm that from here". Typed MC knew the release (`e6017b5`) and the job health. Voice sessions now start with a bounded platform status digest (job health first, what production last deployed, what waits on Robert; `core/realtime_voice/status_digest.py`), and the card gets one line (Robert, 12 Nov): a general "how are things" is answered at once, in a sentence or two, from that status, problems first, with an offer to check something specific; a specific question (did the deploy finish, what's deployed, is X done) is checked through the backend and its answer said. With no status, MC says plainly he can't see it.
- FF-16's short-answer rule is unchanged. SOUL, AGENTS (apart from its header version), RUNTIME binding and handbook 0.1.3 are byte-identical to 0.1.32. No new tool, permission or authority.
