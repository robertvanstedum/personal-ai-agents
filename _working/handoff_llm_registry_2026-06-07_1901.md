# Handoff — Commit LLM_REGISTRY.md
*mini-moi · personal-ai-agents*

- **Authored:** 2026-06-07 19:01 CDT (2026-06-08 00:01 UTC) — Claude.ai
- **Status:** READY — single file, no build
- **Branch:** `main` directly (this is documentation, not a feature)

---

## Where it goes

```
docs/LLM_REGISTRY.md
```

The `docs/` directory already holds `INTELLIGENCE_LAYER.md` and the design session notes — this
belongs in the same company. One level deep, named clearly, easy to find without digging.

**Also update `README.md`:** add a link to `docs/LLM_REGISTRY.md` in whatever documentation or
architecture section the README has. This is portfolio-facing material — surfacing it from the
README means anyone scanning the public repo finds it in under 10 seconds.

---

## Task

1. Copy `LLM_REGISTRY.md` from the outputs folder to `docs/LLM_REGISTRY.md`.
2. Open `README.md` and add one line in the docs/architecture section:
   ```
   - [LLM Registry](docs/LLM_REGISTRY.md) — all LLM call sites, model rationale, and change log
   ```
3. Commit both files.

```
git add docs/LLM_REGISTRY.md README.md
git commit -m "docs: add LLM registry — 22 call sites, 3 providers, changelog for experiment tracking"
git push origin main
```

---

## Maintenance note (for Robert)

Every time a model or provider changes, open `docs/LLM_REGISTRY.md`, update the registry table,
and add one entry to the Part 5 changelog:
```
### YYYY-MM-DD — [short description]
- File: which .py file changed
- Change: what changed
- Why: the reason
```
Then commit with a message like:
```
git commit -m "llm: switch curator scoring from haiku to [new model] — [one-line reason]"
```
The git history is the full diff; the changelog is the human-readable why.

---

*LLM_REGISTRY.md · docs/ · main · 2026-06-07 19:01 CDT*
