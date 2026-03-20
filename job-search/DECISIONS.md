# Job Search — Design Decisions Log

> Auto-seeded from JOB_SEARCH_FUNCTIONAL_DESIGN.md v1.0 (2026-03-20)
> Add new rows as decisions are made during build sessions.

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Module inside personal-ai-agents, not new repo | Shares model stack, scheduler, Telegram, Mac Mini deployment |
| 2 | Independence via separate data/ and scripts/ | Pause one without touching the other |
| 3 | Grok for scoring, Haiku for bulk filter | Same pattern as Mini-moi — proven cost/quality balance |
| 4 | OpenClaw does not auto-send outreach | Human approves all external messages — operator stays in control |
| 5 | Startup branch in same repo, separate search profile | Same infrastructure, different scoring weights |
| 6 | company_normalized as join key | Ensures network contacts link to opportunities regardless of punctuation |
| 7 | Score ≥ 90 triggers immediate Telegram, not next digest | High-match opportunities require Day 1 action, not Day 30 |
| 8 | Portal monitoring via Haiku parse, not API | Workday/Taleo have no public API; HTML parse is pragmatic |
| 9 | Equity weight 1.5x for startup tier | Compensates for lower base salary probability; aligns with stated priority ranking |
| 10 | German-US profile uses target-companies.json ref | Cannot filter by company language/origin on standard boards; manual list is more reliable |
