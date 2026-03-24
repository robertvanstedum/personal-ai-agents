# _NewDomains — Staging Area for New Domains

Design and planning documents for domains under development.
Nothing here is built yet unless explicitly noted in PROJECT_STATE.md.

**Staging pattern:** New domains start here as specs and prototypes.
They graduate to main when v1 is stable and tested.
`research-intelligence/` is the first graduate — v1 shipped 2026-03-23.

## Read This First
1. Read `PROJECT_STATE.md` before starting any work
2. Check "Approved to Build" section before touching anything

## Hard Rules for Claude Code and OpenClaw
- Do NOT modify any existing repo files
- Do NOT merge content into README.md, CHANGELOG.md, OPERATIONS.md, WHITEBOARD.md
- Do NOT build anything from WHITEBOARD.md without explicit sign-off
- Do NOT build anything not listed in PROJECT_STATE.md "Approved to Build" section
- When in doubt — stop and ask Robert

## Protected Existing Files (never touch without explicit instruction)
- README.md
- CHANGELOG.md
- WHITEBOARD.md
- OPERATIONS.md
- CLAUDE.md
- Any existing docs/ files

## Contents
- `PROJECT_STATE.md` — master orientation, read first every session
- `ARCHITECTURE.md` — platform vision and principles
- `DOMAIN_SPEC_language_learning.md` — German learning system design
- `DOMAIN_SPEC_finance.md` — tax prep and household finance design
- `DOMAIN_SPEC_commercial.md` — RVSAssociates commercial platform design
