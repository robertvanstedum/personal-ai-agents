# Handoff — Guild Docs View
*Built: 2026-06-19 · Claude Code*
*Commits: 026cd87, 7b5a0c3, 32b1218*
*Design log: #89 → done*

---

## What was built

A DOCS tab in the Guild UI that lets Robert browse and read all files in `docs/` without going to GitHub. Three routes, three templates. Design log #89.

---

## Routes added to `minimoi_portal/app.py`

| Route | Template | Purpose |
|---|---|---|
| `GET /guild/docs` | `docs_list.html` | Grouped doc index with sticky pill nav |
| `GET /guild/docs/_readme` | `docs_reader.html` | Serves repo-root README.md (special case) |
| `GET /guild/docs/decisions` | `docs_decisions.html` | Decision Records table with filters |
| `GET /guild/docs/<path:filename>` | `docs_reader.html` | Markdown reader for any file in docs/ |

All routes are `@_require_owner` — owner-only, same as rest of Guild.

---

## Templates created

**`minimoi_portal/templates/guild/docs_list.html`**
- Sticky pill bar just below the subnav (position: sticky, top: 80px)
- Pills for each section: Core, Curator, German, Guild, Infrastructure, Process, Portfolio, Releases, Decision Records
- Clicking a pill smooth-scrolls to that section; active pill updates via IntersectionObserver as you scroll
- Two-column grid of compact single-line rows: title on left, date on right
- Decision Records always last, full-width block with count and "View all →"

**`minimoi_portal/templates/guild/docs_decisions.html`**
- Table of all files in `docs/decision-records/`
- Reads YAML frontmatter from each DR: `domain`, `status`, `lora-candidate`
- Title extracted from first `# ` heading after frontmatter block
- Date extracted from filename (`dr_YYYY-MM-DD_*`)
- Client-side filter pills: All / German / Platform / Guild / Active / LoRA
- Click any row → docs reader for that DR

**`minimoi_portal/templates/guild/docs_reader.html`**
- Markdown rendered with `python-markdown` (fenced_code, tables extensions)
- Subnav: `← Docs` back link, filename, git last-modified date, "View on GitHub →"
- GitHub URL: `https://github.com/robertvanstedum/personal-ai-agents/blob/main/docs/{filename}`
- Full typography: Playfair headings, DM Mono code, Source Sans 3 body, parchment palette

---

## Helper functions in `app.py`

| Function | Purpose |
|---|---|
| `_docs_git_date(rel_path)` | `git log -1 --format=%ci` per file, returns YYYY-MM-DD |
| `_docs_read_meta(path)` | Extracts `title` (first `# ` heading) from file |
| `_docs_group_files(files)` | Assigns files to 8 domain categories |
| `_dr_parse_frontmatter(path)` | Parses YAML frontmatter + extracts DR title |

Constants: `_DOCS_DIR`, `_DR_DIR`, `_REPO_DIR` (repo root, for README).

---

## Grouping logic

`docs/` is scanned with `rglob("*.md")`, excluding `decision-records/` subdirectory and any `README.md`. Files from subdirectories (`portfolio/`, `releases/`, `test-reports/`, `design/`, `poc/`) are included and categorized by their name prefix and parent directory.

**8 categories:**
- **Core** — ROADMAP, SERVICES, LLM_REGISTRY, DB_SCHEMA, GERMAN.md, GUILD.md, WORKSPACE-SETUP.md + repo README (always first)
- **Curator** — All curator plans, build records, feature docs (`CURATOR*`, `curator-*`, `BUILD_WS*`, `PLAN_WS*`, `INTELLIGENCE_*`, `FEATURE_*`)
- **German** — `GERMAN*`, `GESPRACHE*`, `LANGUAGE_CASE*`
- **Guild** — `GUILD*`, `COS_*`, `OPS_*`
- **Infrastructure** — `AWS_*`, `CODE_*`, `LEARNING_*`, `PLAN_*` + files in `poc/` and `design/` subdirectory
- **Process** — `DECISION_RECORD*`, `DESIGN_SESSION_PROMPT*`, `HANDOFF*`, `DESIGN_UI*`, `OPENCLAW*`
- **Portfolio** — `portfolio/` and `test-reports/` subdirectories + `CASE_STUDY*`, `AI_TOOLS*`
- **Releases** — `releases/` subdirectory + `*_RELEASE*` files

---

## Security note

The reader route has a path traversal guard:
```python
target = (_DOCS_DIR / filename).resolve()
if not str(target).startswith(str(_DOCS_DIR.resolve())):
    return "Not found", 404
```
The `_readme` route is a separate explicit route — it does not use the catch-all and reads only `_REPO_DIR / "README.md"`.

---

## Subnav updated

DOCS link added to the Guild subnav in three existing templates:
- `minimoi_portal/templates/guild/build_queue.html`
- `minimoi_portal/templates/guild/build_log.html`
- `minimoi_portal/templates/guild/build_roadmap.html`

---

## What the spec said vs what was built

| Spec item | Status | Notes |
|---|---|---|
| DOCS tab in Guild subnav | ✅ done | |
| `/guild/docs` grouped list | ✅ done | |
| Title, date, subtitle per entry | Subtitle dropped | 66 docs — subtitle was too noisy; removed for cleanliness |
| Click entry → markdown reader | ✅ done | |
| "View on GitHub →" link | ✅ done | |
| `/guild/docs/decisions` DR table | ✅ done | |
| DR frontmatter filters (domain/status/LoRA) | ✅ done | Client-side JS |
| Git last-modified date per doc | ✅ done | |
| DR count badge accurate | ✅ done | |
| Subdirectory files included | ✅ done | Spec implied root only; expanded to rglob |
| Project README in Core | ✅ added | Not in spec; added at Robert's request |
| Sticky pill navigation | ✅ added | Not in spec; added at Robert's request for UX |
| 8 domain categories | ✅ added | Spec had 4 generic groups; replaced with domain-based |

---

## Decision Records committed (also this session)

Three retroactive DRs written during this session and committed to `docs/decision-records/`:

- `dr_gesprache_async_analysis_2026-06-18.md` — WebSocket server push chosen over fire-and-forget, polling, animated labels
- `dr_monitoring_stack_2026-06-18.md` — Sentry + Prometheus + Grafana + CloudWatch
- `dr_mac_mini_vs_aws_2026-06-18.md` — AWS t3.small + g4dn.xlarge spot over Mac Mini purchase

These are visible in the DR table at `/guild/docs/decisions`.

---

## Other session work (same session, earlier)

- **Colima boot fix** — `com.vanstedum.colima.plist` now has correct PATH; `homebrew.mxcl.colima.plist` unloaded to prevent conflict. Documented in `OPERATIONS.md` and `ops_memory.md`.
- **OPERATIONS.md** — full system applications inventory, boot order, Docker/Colima section, post-reboot health check steps
- **AWS Migration Plan Phase 0.0** — added inventory audit step to `_working/docs_AWS_MIGRATION_PLAN_2026-06-18.md`
- **ROADMAP.md** — Code Quality Review and Monitoring Stack sections added; AWS Phase 0 corrected to `spec_ready`
- **`docs/CODE_REVIEW_PLAN.md`** — committed from `_working/docs_CODE_REVIEW_PLAN_2026-06-19.md`

---

## For OpenClaw

**Design log #89 is done.** No follow-up items from this spec.

The docs/ grouping logic lives in `_docs_group_files()` in `minimoi_portal/app.py` (around line 1495). If new doc categories are needed, add a name pattern there — no template changes required.

The DR table at `/guild/docs/decisions` reads live from `docs/decision-records/`. New DRs appear automatically on next page load. No index file to maintain.

*Handoff · 2026-06-19 · Claude Code*
