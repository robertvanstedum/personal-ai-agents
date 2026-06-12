# Handoff — Morning Build: Manual Entry + Handoff Gap-Check
*mini-moi · Guild*
*Authored: 2026-06-12 05:56 CDT — Claude.ai*
*Covers items 1, 2, and 4 from plan_six_week_focus_2026-06-11.md*
*Item 3 (Robert adding applications) is Robert's action — nothing to build*

---

## Item 1 — Wire `cos_soul.md` into CoS `/chat`

### Place the file

`cos_soul.md` content was written previously — copy it to:
```
domains/guild/config/cos_soul.md
```

### Helper function

Add to the CoS chat module (e.g. `domains/guild/services/cos_chat.py` or
directly in the route file):

```python
from pathlib import Path

SOUL_PATH = Path("domains/guild/config/cos_soul.md")

def get_cos_soul() -> str:
    """Lightweight read of the soul file. Returns empty string if missing,
    disabled, or on any error — must never break /chat."""
    if not get_cos_context().get("cos_soul_enabled", True):
        return ""
    if not SOUL_PATH.exists():
        return ""
    try:
        content = SOUL_PATH.read_text(encoding="utf-8").strip()
        if content:
            return f"\n\n---\nCoS Personality & Style (from cos_soul.md):\n{content}\n---\n"
        return ""
    except Exception:
        return ""
```

### Injection — append to system prompt in `/chat`

```python
@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_message = data.get("message", "")

    base_system = """You are the Chief of Staff for mini-moi..."""  # existing prompt

    soul_block = get_cos_soul()
    full_system_prompt = base_system + soul_block

    response = call_llm(
        system=full_system_prompt,
        user=user_message,
        # ... existing context (cos_context.json, memory, etc.)
    )
    return jsonify({"response": response})
```

### Toggle — add to `cos_context.json`

```json
{
  "cos_soul_enabled": true
}
```

Instant rollback if the 1-2 week informal read goes badly — flip to `false`,
no code change, no redeploy.

### Deliberately NOT doing (v0.1)

- **No caching** — file read is cheap, and caching would delay edits taking
  effect, which works against the soul's own "if it stops feeling true,
  change it" premise.
- **No memory compaction integration** — keep scope to `/chat` only until the
  informal read tells us something. Baking the soul's voice into
  `cos_memory.md` before then makes it harder to revise if needed.

### Definition of done — Item 1

- [ ] `domains/guild/config/cos_soul.md` exists with the written content
- [ ] `cos_soul_enabled: true` added to `cos_context.json`
- [ ] `get_cos_soul()` helper added, fails silently if file missing/disabled
- [ ] `/chat` system prompt includes soul block when enabled
- [ ] Quick manual test: ask CoS something via `/chat`, confirm it still
      works normally (soul shouldn't be dramatically obvious — subtle shift
      in tone, not a personality switch)

---

## Item 2 — Manual entry form + `source` field

### Migration (small addition to existing `pipeline.items`)

```sql
ALTER TABLE pipeline.items ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'loop_a';

-- Backfill: existing rows came from Loop A
UPDATE pipeline.items SET source = 'loop_a' WHERE source IS NULL;
```

New rows from the manual form set `source = 'manual'`. Loop A continues
writing `source = 'loop_a'` (no change needed to the scout script — the
column default handles it, but confirm the INSERT statement is compatible
with the new column).

### Form — where it lives

On the Positions page (`/guild/career`), add a small "+ Add Position" link
near the top, above the table. Click reveals an inline form (no modal,
no new page) — same parchment aesthetic, minimal.

```html
<a href="#" id="add-position-toggle" style="color: var(--accent); font-size: 0.85rem;">
    + Add position
</a>

<form id="add-position-form" method="POST" action="/guild/career/items/new"
      style="display:none; margin: 0.75rem 0; padding: 0.75rem;
             border: 0.5px solid var(--border); border-radius: 6px;">
    <div style="display:grid; grid-template-columns: 2fr 1fr 1fr; gap: 8px; margin-bottom: 8px;">
        <input name="title" placeholder="Title — Company" required
               style="padding:4px 6px; font-size:0.85rem; border:0.5px solid var(--border); background:var(--bg);">
        <input name="geo" placeholder="Location"
               style="padding:4px 6px; font-size:0.85rem; border:0.5px solid var(--border); background:var(--bg);">
        <select name="opportunity_type"
               style="padding:4px 6px; font-size:0.85rem; border:0.5px solid var(--border); background:var(--bg);">
            <option value="employment">Employment</option>
            <option value="contract">Contract</option>
            <option value="advisory">Advisory</option>
        </select>
    </div>
    <div style="display:grid; grid-template-columns: 3fr 1fr auto; gap: 8px;">
        <input name="url" placeholder="Posting URL (optional)" type="url"
               style="padding:4px 6px; font-size:0.85rem; border:0.5px solid var(--border); background:var(--bg);">
        <select name="status">
            <option value="applied" selected>Applied</option>
            <option value="suggested">Suggested</option>
            <option value="reviewing">Reviewing</option>
            <option value="interview">Interview</option>
        </select>
        <button type="submit" style="padding:4px 12px; font-size:0.85rem;
                background: var(--accent); color: white; border:none; border-radius:4px;">
            Add
        </button>
    </div>
</form>

<script>
document.getElementById('add-position-toggle').addEventListener('click', function(e) {
    e.preventDefault();
    const form = document.getElementById('add-position-form');
    form.style.display = form.style.display === 'none' ? 'block' : 'none';
});
</script>
```

### Route

```python
@app.route('/guild/career/items/new', methods=['POST'])
@owner_required
def add_career_item():
    title = request.form.get('title', '').strip()
    if not title:
        return redirect(url_for('guild_career'))

    # Split "Title — Company" if it contains a separator, else title only
    company = None
    if ' — ' in title:
        title, company = [s.strip() for s in title.split(' — ', 1)]
    elif ' - ' in title:
        title, company = [s.strip() for s in title.split(' - ', 1)]

    db_execute("""
        INSERT INTO pipeline.items
            (title, company, context, geo, opportunity_type, url, status, source, created_at)
        VALUES (%s, %s, 'career', %s, %s, %s, %s, 'manual', NOW())
    """, (
        title,
        company,
        request.form.get('geo') or None,
        request.form.get('opportunity_type', 'employment'),
        request.form.get('url') or None,
        request.form.get('status', 'applied'),
    ))

    return redirect(url_for('guild_career'))
```

**Note on `company`:** the title field accepts "Title — Company" or
"Title - Company" and splits it. If neither separator is present, `company`
stays NULL and the title displays as-is. Don't overbuild this — if it's
awkward in practice, a separate company field can be added later, but for
v0.1 this single-field approach keeps the form minimal.

**Note on `fit_score` / `fit_narrative`:** manual entries don't get these —
they're NULL. The Positions table should handle NULL scores gracefully
(blank or em-dash, not "0.0" or an error). Sort order
(`priority DESC, fit_score DESC, created_at DESC`) already handles NULLs
correctly in Postgres (NULLs sort last by default) — confirm this is
acceptable, or add `NULLS LAST` explicitly if manual entries should sort
by recency instead.

### Visual distinction — `source`

In the Positions table, `loop_a` items get a small muted tag: `· scout` next
to the type badge. Manual items get nothing extra (they're the default,
unmarked case — manual is "normal," scout-found is the addition). This keeps
the table calm for Robert's own entries and quietly flags the supplementary
ones.

```html
{% if item.source == 'loop_a' %}
<span style="font-size:9px; color:var(--text-muted);">· scout</span>
{% endif %}
```

### Definition of done — Item 2

- [ ] `source` column added, backfilled to `loop_a` for existing rows
- [ ] "+ Add position" reveals inline form on Positions page
- [ ] Form submits, creates row with `source='manual'`, `context='career'`
- [ ] Title splits on " — " or " - " into title/company if present
- [ ] NULL fit_score renders cleanly (blank, not "0.0")
- [ ] `loop_a` items show "· scout" tag; manual items unmarked
- [ ] Robert adds T-Mobile + other current applications, stars priorities
- [ ] Active Pipeline board shows starred items correctly

---

## Item 4 — Handoff gap-check (first concrete version)

### The problem (concrete, happened twice this session)

A spec is written and saved to Claude.ai's outputs. It's referenced in
`docs/GUILD.md`, a build plan, or conversation — but never copied to
`_working/`. Claude Code can't find it. The handoff stalls until someone
notices.

### v0.1 — a script, not automation yet

A simple script that checks: "every `_working/*.md` file referenced in
`docs/GUILD.md` or `docs/GUILD_BUILD_LOG.md` actually exists in `_working/`."

```python
#!/usr/bin/env python3
# scripts/check_handoff_gaps.py
"""
Scans docs/GUILD.md and docs/GUILD_BUILD_LOG.md for references to
_working/*.md files and reports any that don't exist.

Usage: python scripts/check_handoff_gaps.py
Exit code: 0 if no gaps, 1 if gaps found (for scripting/cron use later).
"""
import re
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_FILES = ['docs/GUILD.md', 'docs/GUILD_BUILD_LOG.md']
PATTERN = re.compile(r'_working/[\w\-\.]+\.md')

def main():
    referenced = set()
    for f in SCAN_FILES:
        path = os.path.join(ROOT, f)
        if not os.path.exists(path):
            continue
        with open(path) as fh:
            content = fh.read()
        referenced.update(PATTERN.findall(content))

    missing = []
    for ref in sorted(referenced):
        full_path = os.path.join(ROOT, ref)
        if not os.path.exists(full_path):
            missing.append(ref)

    if missing:
        print(f"⚠️  {len(missing)} referenced file(s) missing from _working/:")
        for m in missing:
            print(f"   - {m}")
        sys.exit(1)
    else:
        print(f"✅ All {len(referenced)} referenced _working/ files present.")
        sys.exit(0)

if __name__ == '__main__':
    main()
```

### How it gets used (v0.1 — manual, not wired into anything yet)

Run by hand, or via a new `!dev check-handoffs` Telegram command on the
Design/Dev agent (simple addition — one new route that shells out to this
script and returns the output).

**Not yet:** scheduled runs, blocking commits, or CoS integration. This is
v0.1 — prove the check is useful before automating it. If it catches the
same class of problem a couple more times, it graduates to a CoS Loop F
check (build discipline mandate) and/or a pre-commit hook.

### Definition of done — Item 4

- [ ] `scripts/check_handoff_gaps.py` created, runs cleanly
- [ ] `!dev check-handoffs` Telegram command added (optional — can be v0.2
      if the script alone is enough for now)
- [ ] Run once against current repo state — fix any gaps it finds
      (e.g., commit `spec_career_two_page_2026-06-11.md` if not already done)

---

## Commit

```bash
git add minimoi_portal/ \
        domains/guild/db/ \
        scripts/check_handoff_gaps.py \
        [devagent route if added]
git commit -m "feat: career manual entry + source field; add handoff gap-check script"
git push origin main
```

---

*Morning build handoff · Guild · 2026-06-12*
