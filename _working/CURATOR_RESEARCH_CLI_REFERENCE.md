# Curator Research CLI — Reference

**File:** `curator_research.py`
**Invoke:** `python3 curator_research.py <command> [args]`
**Branch:** `feat/curator-research-v2` (to be merged to main)

---

## What this CLI is

`curator_research.py` is the data layer for Sources, Topics, and Groups — the
gathering tier added in Curator v2. It has no Flask dependency and no LLM calls.

The CLI (`__main__` block at the bottom of the file) serves two permanent roles:

1. **Your daily interface** — use it directly until Flask routes are added to the
   research UI. All read operations are safe to run any time. Write operations
   (promote, migrate, activate, create-group) are explicit commands: you decide.

2. **Automated test tool** — use it in shell scripts or CI to verify the data
   layer without spinning up a web server. All commands exit `0` on success,
   `1` on error. Output is human-readable stdout; errors go to stderr.

**Governing constraint (from the PLAN):** spend follows attention. Nothing in this
module triggers an AI call or background process. Every write operation is a
deliberate command you type.

---

## Quick-reference by use case

### See what's active

```bash
python3 curator_research.py status
```
Shows: Source count, Topics by state (active-pull / dormant / paused / one-shot /
closed), Group count, tag alias count, and a warning if any active-pull topic is
past its expiry date.

```bash
python3 curator_research.py topics
python3 curator_research.py topics --status active-pull
```

---

### Manage topic lifecycle

```bash
# Start a research pull (dormant → active-pull)
python3 curator_research.py activate quad-flexibility-not
python3 curator_research.py activate quad-flexibility-not --days 7

# Pause a running pull (banks remaining days)
python3 curator_research.py pause china-rise --note "pausing while I read Arrighi"

# Resume a paused pull (restores banked days)
python3 curator_research.py activate china-rise

# Close (terminal — use when done, not just paused)
python3 curator_research.py close hellscape-taiwan-porcupine --note "archived"

# Check and close any topic that ran past its expiry
python3 curator_research.py auto-stop
```

---

### Get pull context for the session runner

The session runner needs a query list + scope. The `pull` command prints the JSON
it expects:

```bash
# Single topic
python3 curator_research.py pull narrow quad-flexibility-not

# All topics in a group (merged queries, deduplicated)
python3 curator_research.py pull contextual grp_001
```

Output is JSON, suitable for piping or copy-pasting into the session runner:
```json
{
  "scope": "narrow",
  "topic_slug": "quad-flexibility-not",
  "status": "active-pull",
  "tags": [],
  "queries": ["...", "..."],
  "motivation": "..."
}
```

---

### Save sources

**From today's briefing** (article hash ID is shown in the daily briefing page):
```bash
python3 curator_research.py promote-feed abc123def quad,india,china \
  --note "Good framing of the access-denial problem" \
  --topics quad-flexibility-not
```

**Add a book or paper manually:**
```bash
python3 curator_research.py promote-manual book \
  "The Rise and Fall of Great Powers" \
  geopolitics,hegemony,longue-duree \
  --reference "Kennedy, Paul. 1987. Random House." \
  --cost-to-act book \
  --date 1987 --date-precision year \
  --topics empire-landpower

python3 curator_research.py promote-manual article \
  "Quad 2.0 and the Indo-Pacific Order" \
  quad,india,japan,australia \
  --url https://example.com/article \
  --topics quad-flexibility-not,china-rise
```

**Migrate your existing saved articles** (5 articles in article_signals.json,
already saved via the briefing UI — these predate Sources):
```bash
# Preview first
python3 curator_research.py migrate-signals --dry-run

# Then commit when ready
python3 curator_research.py migrate-signals
```
After migration, tag each source:
```bash
python3 curator_research.py sources   # find the new src_YYYYMMDD_NNN ids
# tagging not yet a CLI command — edit sources.json directly or via promote-*
```

---

### Inspect sources

```bash
# All sources
python3 curator_research.py sources

# Filter by topic
python3 curator_research.py sources --topics quad-flexibility-not

# Filter by tag
python3 curator_research.py sources --tags quad,india

# Full detail for one source
python3 curator_research.py source src_20260531_001

# See what topics a source might link to (tag overlap, no write)
python3 curator_research.py suggest-links src_20260531_001
```

---

### Groups

```bash
# Create a group (grouping related topics — no AI, just a record)
python3 curator_research.py create-group \
  --name "Indo-Pacific Buffer" \
  --topics quad-flexibility-not,china-rise \
  --tags quad,china,indo-pacific

# List groups
python3 curator_research.py groups

# Pull context across a group (merged queries from all member topics)
python3 curator_research.py pull contextual grp_001
```

---

### Tag aliases

Aliases are in `_NewDomains/research-intelligence/data/tag_aliases.json`.
Edit that file directly — it's a hand-maintained map. No UI, no AI.

```bash
# See current aliases
python3 curator_research.py tag-aliases
```

Example `tag_aliases.json`:
```json
{
  "prc": "china",
  "quad-security": "quad",
  "us-china": "china"
}
```
Aliases resolve at read time — stored tags are always the original string you typed.

---

## Full command list

| Command | Description |
|---|---|
| `status` | Overview: counts by type and state, overdue warning |
| `topics [--status STATE]` | List topics; filter by state |
| `activate SLUG [--days N] [--note "..."]` | Activate dormant topic |
| `pause SLUG [--note "..."]` | Pause active-pull (banks days) |
| `close SLUG [--note "..."]` | Close topic (terminal) |
| `auto-stop` | Close any topic past its expiry date |
| `groups` | List all groups |
| `create-group [--name] [--topics] [--tags] [--note]` | Create a group |
| `sources [--tags] [--topics] [--resolve]` | List sources, optional filter |
| `source SOURCE_ID` | Full detail for one source |
| `promote-feed HASH_ID TAGS [--note] [--topics]` | Save article from briefing |
| `promote-manual TYPE TITLE TAGS [--url] [--reference] [--cost-to-act] [--note] [--topics] [--date] [--date-precision]` | Add manual source |
| `migrate-signals [--dry-run]` | Migrate article_signals.json → Sources |
| `migrate-threads [--dry-run]` | Upgrade thread.json → Topics schema v2 (idempotent) |
| `pull narrow SLUG` | Pull context for single topic (JSON) |
| `pull contextual GROUP_ID` | Pull context for whole group (JSON) |
| `suggest-links SOURCE_ID` | Suggest topic links by tag overlap (read-only) |
| `tag-aliases` | List all tag aliases |

**Topic states:** `dormant` → `active-pull` ↔ `paused` → `closed`; also `one-shot` → `closed`

**Source types:** `article`, `post`, `paper`, `book`

**cost-to-act values:** `free` (read now), `dive` (requires a deep-dive session), `book` (physical or paid)

---

## Using for automated testing

All commands are subprocess-safe. Exit codes: `0` success, `1` error.

```bash
# Smoke test: module loads and data files are readable
python3 curator_research.py status && echo "OK"

# Verify a known topic exists and is in the right state
python3 curator_research.py topics --status active-pull | grep "quad-flexibility-not"

# Verify pull context produces valid JSON
python3 curator_research.py pull narrow quad-flexibility-not | python3 -m json.tool > /dev/null && echo "valid JSON"

# Dry-run migration is always safe to include in CI
python3 curator_research.py migrate-signals --dry-run
python3 curator_research.py migrate-threads --dry-run
```

To call from Python test code directly (no subprocess overhead):
```python
import curator_research as cr

# State checks
assert cr.get_topic("quad-flexibility-not")["status"] == "active-pull"
assert len(cr._all_topics()) == 6

# Pull context structure
ctx = cr.narrow_pull_context("quad-flexibility-not")
assert ctx["scope"] == "narrow"
assert len(ctx["queries"]) > 0

# Tag alias resolution
cr._load_tag_aliases()  # returns {} until aliases are populated
```

---

## Data locations

```
_NewDomains/research-intelligence/data/
  sources/sources.json        — Source records (append-only array)
  tag_aliases.json            — Hand-edited alias map
  threads/{slug}/thread.json  — Topic records (one per topic)
  groups/groups.json          — Group records
  feedback/article_signals.json  — Legacy save signals (read-only after migration)
```

None of these are committed in the normal Curator pipeline. They're your research
layer — local, editable, no pipeline dependency.

---

*Written 2026-05-31. Covers `feat/curator-research-v2` Steps 1–4.*
*Next: Leaning object (later PLAN), Flask routes (later PLAN), dormant section (later PLAN).*
