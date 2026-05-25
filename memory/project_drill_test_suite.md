---
name: Drill regression test suite — build note
description: Reminder to build a comprehensive pytest suite for drill routing logic; every fix has caused a routing regression
type: project
---

A regression test suite for the German drill bot routing is needed and has been prioritized.

**Why:** Three successive regressions during the drill list / number-selection fix (May 2026) — each fix broke an untested routing path. The routing logic has 4 parallel code paths (text active-drill, text non-active, voice active-drill, voice non-active) with no automated coverage.

**How to apply:** When planning any drill routing work, flag that tests should accompany the change. Handoff note for OpenClaw is at `_working/build_note_drill_regression_tests.md`.
