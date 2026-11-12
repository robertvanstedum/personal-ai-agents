# 0.1.32 — how Guildhall works, and what counts as a harness defect

12 November 2026 · Claude Code · 1.2 fast-follow (lane C, #526), built into `release/1.2-ff` for the single production deployment.

- **Guildhall (FF-10).** Asked whether to leave a meeting open, MC said he didn't know if meetings are kept. They are. A new "Guildhall (Robert, 12 Nov)" section says so in plain words, checked against `domains/records/store.py`, `domains/records/meetings.py` and the Rooms page: every meeting is kept; pause, close and archive delete nothing; a closed meeting continues as a new session in the same room; transcripts and after-close notes; 30-day archive with Restore; teammates with a card, proven before they answer.
- **Harness defects (FF-08, answer A5).** "+1 defect noted" now says what the harness is: count only permission loops, missing tools or capabilities, and integration failures, with one example. Product bugs and model mistakes are not counted. The wording reuses the 0.1.10–0.1.17 examples. `scripts/judgment/judgment_store.py` is unchanged.
- Pins handbook 0.1.3 (a Guildhall row in the product map). SOUL, RUNTIME binding and voice card are byte-identical to 0.1.31. No new tool, permission or authority.
