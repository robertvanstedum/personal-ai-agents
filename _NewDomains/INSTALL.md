# Install Instructions — _NewDomains Package
**Date:** 2026-03-07

## What This Is
A folder of working design documents for new domains.
Nothing here is code. Nothing gets built from these docs
without explicit approval in PROJECT_STATE.md.

## How to Install

1. Copy the `_NewDomains/` folder into your project root:
   ```
   personal-ai-agents/
   └── _NewDomains/          ← place here
       ├── README.md
       ├── PROJECT_STATE.md
       ├── ARCHITECTURE.md
       ├── DOMAIN_SPEC_language_learning.md
       └── DOMAIN_SPEC_finance.md
   ```

2. Add to `.gitignore` (append, do not replace):
   ```
   # Finance domain — private data
   domains/finance/statements/
   domains/finance/reports/

   # Language learning — private data
   domains/language_learning/languages/
   domains/language_learning/*/sessions/
   domains/language_learning/*/exercises/
   domains/language_learning/*/vocabulary/
   ```

3. Add one line to `CLAUDE.md` (append only):
   ```
   Read _NewDomains/PROJECT_STATE.md at the start of every session.
   ```

## What NOT to Do
- Do not modify README.md, CHANGELOG.md, OPERATIONS.md, WHITEBOARD.md
- Do not merge _NewDomains content into existing docs
- Do not start building anything in the domain specs without Robert's sign-off

## For Claude Code
Run steps 2 and 3 only. Commit with message:
`Docs: Add _NewDomains working folder, update gitignore and CLAUDE.md`
