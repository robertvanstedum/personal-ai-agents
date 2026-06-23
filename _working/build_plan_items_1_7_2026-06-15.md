# Build Plan — Kanban Items 1–7
*2026-06-15 — Claude Code*
*Audience: Robert + Claude Code (next session)*
*Source specs: all 7 in `_working/`*

---

## Sequencing overview

Not all 7 items are independent. Build in this order:

```
Morning gate  → Gespräche latency decision (5 min test, then decide)
Item 1        → Mein Deutsch v1.1 release + landing copy        (quick win, no deps)
Item 6        → Housekeeping: ROADMAP move + repo audit          (quick, no deps)
Item 2        → CoS guest access (DB + portal + CoS nudge)       (independent of Cloudflare)
Item 3        → Security architecture: Cloudflare Pages + tunnel (Robert does DNS steps)
Item 4        → Preview layer capture script + animation         (depends on item 3)
Item 5        → Lesen translate hover fix                        (depends on item 4 script)
Item 7        → Lesen writing drill                              (last — most complex)
```

Items 2 and 3 can be worked in parallel if needed (different codebases). Items 4 and 5 must come after 3 because they write to `static/public/` which doesn't exist until then.

---

## Morning gate — Gespräche latency (5 min, before anything else)

**What to do:** In browser, switch the Gespräche MODELL dropdown from Grok to **OpenAI** and run a 2-turn session with Maria. Note whether the response feels snappy.

**Expected result:** gpt-4o-mini benchmarked at ~1.2s per turn vs 5–10s for Grok.

**If it feels right:** change the default model in the dropdown. One-line change in `domains/german/templates/german_gesprache.html` ~line 69:
```html
<!-- Change this: -->
<option value="grok" selected>Grok</option>
<option value="openai">OpenAI</option>
<!-- To this: -->
<option value="grok">Grok</option>
<option value="openai" selected>OpenAI</option>
```
Commit: `ux: default Gespräche model to OpenAI (gpt-4o-mini, ~1s vs 5-10s for Grok)`

**If it doesn't feel right or the persona voice is off:** keep Grok default, note what was wrong, flag to OpenClaw for design decision.

---

## Item 1 — Mein Deutsch v1.1 GitHub Release + Landing Copy
*Spec: `spec_mein_deutsch_v11_release_2026-06-15.md`*
*Est: 30 min. No dependencies. Quick visible win.*

### Part A — Landing copy update

**File:** `minimoi_portal/templates/landing.html` (current location pre-Cloudflare migration)

Find the Mein Deutsch entry in the "What's running" section. Replace:
```
Mein Deutsch v1.0 — German language coaching pipeline. Create personas
and scenes, hold a real-time conversation, then review the transcript
and iterate. Vienna-tested. Lesen, Gespräche, Schreiben, Wörter. Anki
cards built from your own mistakes.
```
With:
```
Mein Deutsch v1.1 — German language coaching pipeline. Finding real
German speakers to practice with depends on where you live — in Chicago,
they're scarce. KI-Personas fill that gap: practice offline, apply with
real people when you find them, then feed real conversations back into
new personas to close the loop. Vienna-tested. Lesen, Gespräche,
Schreiben, Wörter. Anki cards built from your own mistakes.
```

After editing: visual check on `localhost:5001` — confirm the "What's running" column still looks balanced with the slightly longer entry.

**Note:** when item 3 (Cloudflare Pages) ships, this copy moves to `static/public/index.html`. The commit there should copy the updated text, not the old text. Keep a note of this.

### Part B — GitHub release

```bash
gh release create mein-deutsch-v1.1 \
  --title "Mein Deutsch v1.1 — Cross-Workflow Learning Loop" \
  --notes "$(cat <<'EOF'
## Mein Deutsch v1.1 — Cross-Workflow Learning Loop

The core insight behind Mein Deutsch: finding real German conversation
partners depends heavily on where you live. In Germany or Austria,
practice partners are everywhere. In Chicago, German speakers are scarce
— the same challenge applies to Portuguese, French, and most languages
that aren't Spanish. You can't just walk outside and find one.

KI-Personas fill that gap. But they don't replace real interaction —
they prepare you for it and extend the learning from it.

**The loop:**
1. Practice with a KI-Persona — café waitress, museum guide, U-Bahn
   stranger — in a controlled setting where mistakes are low-stakes
2. Apply what you practiced in a real conversation (friend, tutor,
   chance encounter)
3. After the real conversation, review the transcript — what worked,
   what didn't
4. Create a new persona modeled on the real person or the patterns
   that emerged — to drill offline what happened in real life
5. Come back to the real person better prepared next time

**What v1.1 adds:**
- **In-browser KI-Sitzung** — Start Session / End Session inside the HTML interface, no copy-paste, no external tool required
- **Provider-agnostic review** — transcript analysis via Grok, OpenAI, or Claude (per-session selector)
- **Voice input in Gespräche** — MediaRecorder + Whisper transcription, session transcript auto-populates for review
- **Mit KI-Persona / Mit echtem Mensch tabs** — named to make the distinction and the loop explicit
- **Shared review backend** — same analysis engine for KI and real sessions, common error patterns tracked across both

**Coming in v1.2:**
- Persona creation from real conversations
- Cross-session pattern detection

Vienna-tested. 69+ sessions and counting.
EOF
)"
```

### Commit
```bash
git add minimoi_portal/templates/landing.html
git commit -m "content: Mein Deutsch v1.1 — update landing copy with learning loop

Updates 'What's running' Mein Deutsch entry to lead with the cross-workflow
learning loop concept. Bumps to v1.1 to match GitHub release."
git push origin main
```

---

## Item 6 — Housekeeping: ROADMAP Move + Repo Audit
*Spec: `spec_housekeeping_2026-06-12.md`*
*Est: 45 min. No dependencies.*

### Part 1 — Repo audit (investigate only, no remediation)

```bash
# Find the 222-file commit
git log --oneline | grep -i "222\|session\|anki\|curator state" | head -5

# List what's on origin/main that was in that commit
git show --stat <commit-hash> | grep "^[^|]" | head -100
```

Categorize each file as:
- **Fine for public**: code, templates, CSS, generic config (no personal data)
- **Needs review**: session transcripts, Anki CSVs, `cos_context.json`, `_working/` docs with personal/career detail, screenshots with personal info

Present categorized list to Robert. **Stop.** No git action without explicit approval.

### Part 2 — ROADMAP file move

```bash
# Check if the file exists
ls _working/ROADMAP_2026-06-12.md

# Move it
mv _working/ROADMAP_2026-06-12.md docs/ROADMAP.md
```

Update the roadmap route in the portal. Find the route in `minimoi_portal/app.py`:
```bash
grep -n "ROADMAP\|roadmap" minimoi_portal/app.py
```
Change the path reference from `_working/ROADMAP_2026-06-12.md` to `docs/ROADMAP.md`.

Check `docs/GUILD.md` for any "Roadmap" section that duplicates content — if found, replace with a pointer to `docs/ROADMAP.md`.

Confirm the Roadmap tab renders at `localhost:5001/guild/build/roadmap`.

### Commit
```bash
git add docs/ROADMAP.md minimoi_portal/app.py docs/GUILD.md
git rm _working/ROADMAP_2026-06-12.md
git commit -m "docs: move roadmap to docs/ROADMAP.md + update portal route

Roadmap is a living doc, not spec-track — moves out of _working/ to docs/.
Portal roadmap route updated. docs/GUILD.md Roadmap section updated if present."
git push origin main
```

---

## Item 2 — CoS Guest Access: DB + Portal + Nudge
*Spec: `spec_cos_guest_access_2026-06-14.md`*
*Est: 2–3h. Independent of Cloudflare work. Can build before item 3.*

**Note on testability:** the `/register` → Telegram → Guild Briefing flow is fully testable on `localhost:5001`. End-to-end via `app.minimoi.ai` only after item 3 is deployed — but don't block the build on that.

### Step 1 — Database migration

```bash
docker exec -i postgres-ai-agents psql -U minimoi -d personal_agents <<'SQL'
CREATE TABLE IF NOT EXISTS guild.guest_requests (
    id             SERIAL PRIMARY KEY,
    name           TEXT NOT NULL,
    email          TEXT NOT NULL UNIQUE,
    reason         TEXT,
    requested_at   TIMESTAMPTZ DEFAULT NOW(),
    status         TEXT DEFAULT 'requested',
    actioned_at    TIMESTAMPTZ,
    last_nudged_at TIMESTAMPTZ
);
SQL
```

Verify: `docker exec postgres-ai-agents psql -U robert_ro -d personal_agents -c "\d guild.guest_requests"`

### Step 2 — DB helper functions in portal

Add to `minimoi_portal/app.py` (or a new `portal_db.py` if one exists):

```python
def _insert_guest_request(name, email, reason):
    sql = """INSERT INTO guild.guest_requests (name, email, reason)
             VALUES (%s, %s, %s) RETURNING id"""
    return _guild_db_query(sql, [name, email, reason or ''])[0]['id']

def _update_guest_request_status(req_id, status):
    sql = """UPDATE guild.guest_requests
             SET status=%s, actioned_at=NOW()
             WHERE id=%s"""
    _guild_db_query(sql, [status, req_id])

def _get_guest_requests():
    return _guild_db_query("""
        SELECT id, name, reason, requested_at, status, actioned_at
        FROM guild.guest_requests
        ORDER BY
            CASE status WHEN 'requested' THEN 0 ELSE 1 END,
            requested_at DESC
    """)
```

### Step 3 — `/register` route

The existing `/register` route is in `minimoi_portal/app.py`. Update it:

```python
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name   = request.form.get('name', '').strip()
        email  = request.form.get('email', '').strip()
        reason = request.form.get('reason', '').strip()
        if name and email:
            _insert_guest_request(name, email, reason)
            _send_telegram(
                f"Guest access request\nName: {name}\nEmail: {email}"
                f"\nReason: {reason or 'not provided'}"
            )
        return render_template('register_thanks.html')
    return render_template('register.html')
```

Check if `register.html` already has the right form fields. If not, update it to include `name` (text), `email` (email), and `reason` (textarea, optional).

Create `minimoi_portal/templates/register_thanks.html` if it doesn't exist:
```html
<!-- minimal: "Thanks, we'll be in touch." parchment style matching portal -->
```

### Step 4 — Status update route

```python
@app.route('/admin/guest-requests/<int:req_id>/status', methods=['POST'])
@_require_owner
def update_guest_request_status(req_id):
    status = request.form.get('status')
    if status in ('granted', 'rejected'):
        _update_guest_request_status(req_id, status)
    return redirect(url_for('guild_briefing'))
```

### Step 5 — Guild Daily Briefing section

In `minimoi_portal/app.py` at the `guild_briefing` route, add `guest_requests` to the template context:
```python
requests = _get_guest_requests()
# pass to template: guest_requests=requests
```

In `minimoi_portal/templates/guild/guild_landing.html`, add an ACCESS REQUESTS section between Career Focus and Build sections. Hidden when `guest_requests` is empty.

Display format per spec:
- Pending (status=requested): show name, time-ago, "requested" label, [Grant] and [Reject] POST buttons
- Actioned: show name, time-ago, status label only — no buttons
- Do not show email (briefing may be visible on screen during demos)
- Sort: requested first, then actioned

### Step 6 — CoS staleness nudge (Loop F)

**File:** `domains/guild/agents/chief_of_staff.py` (or wherever the CoS service runs — check `~/Library/LaunchAgents/com.user.cos.plist` for the entry point)

```bash
grep -r "run_hourly\|scheduler\|Timer" domains/guild/agents/ | head -10
cat ~/Library/LaunchAgents/com.user.cos.plist
```

Add to CoS service startup:
```python
import threading

def check_stale_guest_requests():
    from minimoi_portal.portal_db import get_stale_guest_requests, update_nudged_at
    stale = get_stale_guest_requests()   # query from spec
    if not stale:
        return
    # build message, send Telegram, update last_nudged_at
    # see spec for exact message format

def run_hourly_checks():
    check_stale_guest_requests()
    threading.Timer(3600, run_hourly_checks).start()

run_hourly_checks()  # call at startup after app init
```

**Risk:** if CoS service is not running (launchd plist not loaded), the nudge never fires. Verify `launchctl list | grep cos` before declaring done.

### Commit
```bash
git add minimoi_portal/ domains/guild/agents/
git commit -m "feat: guest access requests — DB + notification + Daily Briefing + CoS nudge

guild.guest_requests table (name, email, reason, status, last_nudged_at).
/register logs to DB and fires Telegram ping. Guild Daily Briefing: Access
Requests section with inline Grant/Reject for pending, status label for
actioned, hidden when empty, email not shown. CoS Loop F: nudge after 2h
pending, repeat every 6h, combined message for multiple stale requests."
git push origin main
```

---

## Item 3 — Security Architecture: Cloudflare Pages + Tunnel
*Spec: `spec_security_architecture_2026-06-14.md`*
*Est: Half day. Robert does DNS/Cloudflare UI steps. Claude Code does code prep.*
*This is the gate for items 4 and 5.*

### Step 0 — Code prep (Claude Code, before Robert touches Cloudflare)

Create the `static/public/` directory structure:
```bash
mkdir -p static/public/assets
mkdir -p static/public/preview
```

Copy and update the landing page:
```bash
cp minimoi_portal/templates/landing.html static/public/index.html
cp minimoi_portal/static/portal.css static/public/assets/portal.css
```

In `static/public/index.html`:
- Update the CSS `<link>` to point to `assets/portal.css`
- Check for other asset references (fonts, favicon, images) and update those paths
- Update "Dashboard →" link to `https://app.minimoi.ai`
- Update "Request guest access →" link to `/contact` (or a placeholder until that page exists)
- Remove any Jinja2 template syntax (`{{ }}`, `{% %}`) — this becomes static HTML, not a template

Search for sensitive data before committing:
```bash
grep -i "api_key\|token\|password\|secret\|robert@\|vanstedum" static/public/index.html
```
Nothing should appear. If it does, remove it.

Update `tools/capture_snapshot.py` — change output path from `minimoi_portal/static/preview/` to `static/public/preview/`. Find the path constant near the top of the file and update it.

Grep Flask for hardcoded domain references:
```bash
grep -rn "minimoi\.ai" minimoi_portal/ --include="*.py" --include="*.html" | grep -v ".pyc"
```
Update any found references to use `app.minimoi.ai` (login redirects, CSRF config, etc.) or make them env-var driven:
```python
SERVER_NAME = os.getenv('FLASK_SERVER_NAME', None)
SESSION_COOKIE_DOMAIN = os.getenv('SESSION_COOKIE_DOMAIN', None)
```

Flask security audit (while in the code):
- Confirm `app.debug = False` in production path (check `FLASK_ENV` handling in `minimoi_portal/app.py`)
- Spot-check: `flask routes` output — any route without `@_require_owner` or `@_require_login` that shouldn't be public?
- Confirm custom error handlers exist for 404 and 500 (no stack traces to public)
- Confirm `/register` is accessible without auth (it should be) but isn't linked from any public page

Commit the code prep:
```bash
git add static/public/ tools/capture_snapshot.py minimoi_portal/app.py minimoi_portal/templates/
git commit -m "feat: static/public/ scaffold for Cloudflare Pages + Flask security hardening

static/public/index.html: landing page copy from portal template, asset paths
updated, Jinja stripped, links updated to app.minimoi.ai. portal.css copied to
assets/. capture_snapshot.py output path updated to static/public/preview/.
Flask: SERVER_NAME and SESSION_COOKIE_DOMAIN moved to env vars. Security audit:
[list findings and fixes]."
```

### Steps Robert does in Cloudflare (sequential)

1. **Cloudflare Pages → Create project**
   - Connect to `personal-ai-agents` repo, branch `main`
   - Build output: `static/public/`
   - No build command
   - Custom domain: `minimoi.ai`
   - First deploy will fail or serve partial until DNS propagates — expected

2. **Remove old `minimoi.ai` tunnel DNS record**
   - In Cloudflare DNS, delete any CNAME/A pointing `minimoi.ai` to the old tunnel ID
   - This prevents Pages + tunnel conflict

3. **Add `app.minimoi.ai` CNAME**
   - `app.minimoi.ai CNAME <tunnel-id>.cfargotunnel.com`

4. **Update tunnel config**
   - Edit `~/.cloudflared/config.yml`
   - Change hostname from `minimoi.ai` to `app.minimoi.ai`, port 5001
   - Run `sudo cloudflared service install` to make it survive reboots

5. **Verify**
   - `minimoi.ai` loads the static landing page (works with Mac off)
   - `app.minimoi.ai` loads the portal (requires Mac on, login)

---

## Item 4 — Preview Layer + Mein Deutsch Animation
*Spec: `build_spec_preview_layer_2026-06-14.md`*
*Est: Full day. Depends on item 3 (static/public/ must exist).*

### Workstream 1 — Capture script

**File:** `tools/capture_snapshot.py`

The script already exists (git status shows it modified). Check current state:
```bash
head -50 tools/capture_snapshot.py
```

The script needs to:
1. Authenticate (Playwright with session cookies or env-based login)
2. Visit all pages in the manifest (see spec table — 12+ pages)
3. Inject `FETCH_INTERCEPT_JS` block (the MyMemory intercept from item 5 should be in here)
4. Inject the preview banner HTML at top of `<body>`
5. Rewrite `data-admin-blocked` attributes on admin tabs
6. Disable form POSTs and write-action buttons
7. For `/guild/career`: replace pipeline table with aggregate counts
8. Rewrite internal hrefs to `/preview/...` equivalents
9. Replace "Dashboard →" nav link with "Request Access →"
10. Write `static/public/preview/manifest.json`
11. Save rendered HTML to `static/public/preview/<domain>/<page>.html`

Key implementation decision: Playwright's `page.content()` gives the full rendered DOM. Use `page.evaluate()` to inject the fetch intercept before page load (or inject it into the saved HTML as a `<script>` tag at the top of `<body>`).

**Add inline JS to each saved page** (one `<script>` block injected into the DOM):
- Fetch intercept (MyMemory for translate calls — item 5)
- Admin modal trigger (for `data-admin-blocked` elements)
- Disabled write-action visual feedback

The admin modal can be pure CSS + a few lines of inline JS — no framework:
```javascript
document.querySelectorAll('[data-admin-blocked]').forEach(el => {
  el.addEventListener('click', e => { e.preventDefault(); showAdminModal(); });
});
function showAdminModal() { document.getElementById('preview-admin-modal').style.display='flex'; }
```

### Workstream 2 — Mein Deutsch animation

**File:** `static/public/preview/german/gesprache.html` (after capture script runs)

The animation is a `<div class="md-walkthrough">` block injected above the static snapshot content. It's CSS keyframes + vanilla JS (no libraries). 

Steps per spec: 6 steps, 40–50s total runtime. See spec for step-by-step detail.

Key implementation notes:
- Use `IntersectionObserver` to auto-play on scroll into view (fires once)
- `prefers-reduced-motion`: wrap all animation CSS in `@media (prefers-reduced-motion: no-preference)`; if motion is reduced, show steps as instant sequence or static
- The "typing" effect for dialogue lines: `setInterval` adding one character at a time, ~40ms interval
- Replay button: appears on completion, resets all animation classes and re-triggers from step 1
- All dialogue content is hardcoded (static demo, not live data)

### Workstream 3 — Guest access entry point

- `/contact` page: add a simple page or section on the landing page with Robert's LinkedIn DM link and a note about guest access
- The preview banner links to `/contact` — confirm it exists before committing

### Commits
```bash
# Workstream 1 + 3
git add tools/capture_snapshot.py static/public/preview/ minimoi_portal/templates/contact.html
git commit -m "feat: minimoi.ai always-on preview layer

Playwright capture script: 12-page manifest, preview banner, admin modal,
write-action disabling, Career Focus aggregate view, internal link rewriting.
/contact page for guest access requests. static/public/preview/ populated."
git push origin main

# Workstream 2
git add static/public/preview/german/gesprache.html
git commit -m "feat: Mein Deutsch animated walkthrough in preview

6-step CSS/JS animation, 40-50s, reflects real KI-Sitzung flow.
Auto-play on scroll (IntersectionObserver), Replay button, prefers-reduced-motion."
git push origin main
```

---

## Item 5 — Lesen Translate Hover (MyMemory fix)
*Spec: `spec_lesen_translate_hover_2026-06-14.md`*
*Est: 30 min. Depends on item 4 (capture script must exist).*

The entire change is adding the fetch intercept JavaScript block to `capture_snapshot.py`. The spec has the complete code — copy it verbatim into `FETCH_INTERCEPT_JS` in the capture script.

The intercept:
- Matches any `fetch` call to a URL containing `/api/translate`
- Extracts `options.body.phrase` from the POST body
- Calls `https://api.mymemory.translated.net/get?q={phrase}&langpair=de|en`
- Returns `{ translation: "..." }` in the format the Lesen JS expects
- Silent fail on error (empty translation, no crash)

After adding: re-run `python tools/capture_snapshot.py` and test the Lesen preview in incognito — hover a word, confirm translation popover appears.

```bash
git add tools/capture_snapshot.py static/public/preview/german/lesen.html
git commit -m "fix: Lesen preview translate hover — route to MyMemory public API

Fetch intercept in preview redirects /api/translate to MyMemory (de→en, no auth).
Fixes JS parse error from HTML login redirect. Works in static Cloudflare Pages context."
git push origin main
```

---

## Item 7 — Lesen Writing Drill
*Spec: `_working/archive/2026-06/feature_lesen_writing_drill_2026-06-08.md`*
*Est: 4–6h. Lowest priority, no dependencies, most complex.*

### Backend

New endpoint in `domains/german/html_server.py`:

```python
@app.route("/api/lesen/correct", methods=["POST"])
def api_lesen_correct():
    body = request.get_json(force=True)
    text = body.get("text", "").strip()
    direction = body.get("direction", "de_in")  # de_in or en_in
    article_title = body.get("article_title", "")

    if not text:
        return jsonify({"error": "text required"}), 400

    # Build prompt based on direction
    if direction == "de_in":
        prompt = (
            f"Correct this German text. Return JSON: "
            f'{{\"corrected\": \"...\", \"translation\": \"...\"}}\n'
            f"Text: {text}\nContext: article titled '{article_title}'"
        )
    else:  # en_in
        prompt = (
            f"Translate this English text to natural German. Return JSON: "
            f'{{\"corrected\": \"...\", \"translation\": \"{text}\"}}\n'
            f"Text: {text}\nContext: article titled '{article_title}'"
        )

    # Use grok-4.3 via review_router pattern (or gpt-4o-mini for speed)
    # Store to domains/german/data/lesen_drills/YYYY-MM-DD.json
    ...
```

Create `domains/german/data/lesen_drills/` directory.

### Frontend

**File:** `domains/german/templates/german_lesen.html` (or wherever the Lesen article page template is — check `html_server.py` for the template name used by the `/lesen` route)

Additions to the NOTIZEN section:
- Add `[Korrigieren ✓]` button alongside existing Merken/Vorlesen buttons
- On click: POST to `/api/lesen/correct` with text, direction, article_title
- Render corrected block below textarea (Korrigiert + Auf Englisch sections)
- Show retype field below corrected block, auto-focused
- Green visual when retyped text matches corrected text (case-insensitive, trimmed)

Language detection for direction: simple heuristic — if input contains common German characters (ä, ö, ü, ß) or common German words, assume `de_in`; otherwise `en_in`. Or add a toggle button.

### Storage

On retype submit: POST to a new `/api/lesen/save-drill` endpoint that appends to `domains/german/data/lesen_drills/YYYY-MM-DD.json`.

### Commit
```bash
git add domains/german/html_server.py domains/german/templates/ domains/german/data/lesen_drills/
git commit -m "feat: Lesen writing drill — contextual correction + retype

/api/lesen/correct: LLM corrects German input or translates English input.
Direction auto-detected. Corrected block + retype field shown inline below NOTIZEN.
Green match on retype. Drill stored to data/lesen_drills/YYYY-MM-DD.json.
Multi-user field populated from session. No regression on existing Lesen functions."
git push origin main
```

---

## Risk flags and watch-outs

| Item | Risk | Mitigation |
|------|------|-----------|
| 1 | Landing copy in wrong file after item 3 | Note in item 3 to copy updated text, not original |
| 2 | CoS service not running → nudge never fires | `launchctl list \| grep cos` before declaring done |
| 3 | DNS conflict (Pages + old tunnel both pointing minimoi.ai) | Delete old tunnel record BEFORE adding Pages domain |
| 3 | Flask SERVER_NAME hardcoded → login loop | Grep before deploying; move to env var |
| 4 | Playwright auth — capture script may not have valid session | Check `.env` or keyring for credentials; test script locally before committing preview output |
| 4 | Career Focus table: what counts as "pipeline table" to exclude | Check what `/guild/career` actually renders; confirm what to show in aggregate view |
| 5 | MyMemory rate limit (1000 words/day per IP) | Acceptable for demo — one visitor, on-demand hover; not pre-loaded |
| 7 | Lesen template name | Check `html_server.py` `render_template()` call for the lesen article page to confirm filename |

---

## Definition of done — summary

| Item | Gate |
|------|------|
| Morning gate | Robert confirms OpenAI feels fast enough, or decision documented |
| 1 | GitHub release created; landing copy live on localhost:5001; Robert visual sign-off |
| 2 | Test request submitted → Telegram ping received → appears on /guild → Grant/Reject work |
| 3 | minimoi.ai loads (Mac off) · app.minimoi.ai loads (Mac on) · Robert confirms in incognito |
| 4 | Capture script runs clean, all 12 pages captured, animation plays and replays |
| 5 | Lesen word hover in incognito preview shows English translation, no console errors |
| 6 | Roadmap tab works at /guild/build/roadmap · Part 1 audit report presented to Robert |
| 7 | Correction + translation shown · Retype field works · Record stored · No Lesen regression |

---

*Build plan · 2026-06-15 · Claude Code*
