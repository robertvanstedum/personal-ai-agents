# mini-moi Portal — Build State & Next Steps

> SUPERSEDED 2026-07-21. Current version: [ARCHITECTURE.md](../../../ARCHITECTURE.md).
> Preserved for history.

**Date:** 2026-05-28  
**Repo:** `~/Projects/personal-ai-agents`  
**Branch:** `feat/german-html-interface`  
**Public URL:** https://minimoi.ai  
**Author:** Robert Van Stedum + Claude Code

---

## What This Project Is

Robert's personal AI platform. Three interconnected Flask apps running on a single
machine, exposed publicly via Cloudflare Tunnel or DNS. The portal (`minimoi.ai`)
is the front door — a branded public landing page, login, dashboard, then proxied
access to two backend apps.

**The three apps:**

| App | What it does | Port |
|-----|-------------|------|
| **minimoi portal** | Public landing + login + dashboard + reverse proxy | 5001 |
| **Curator** | AI morning briefing: geopolitics articles scored by xAI Grok, delivered via Telegram and web | 8766 |
| **Mein Deutsch** | German language learning: Lesen, Gespräche, Wörter, Schreiben, Übungen tabs | 8767 |

---

## Infrastructure: Current State

### MacBook = production right now. Nothing is on Mac Mini yet.

All three apps run on MacBook as persistent launchd services (auto-restart on reboot).

| launchd label | runs | port |
|---|---|---|
| `com.vanstedum.minimoi-portal` | `minimoi_portal/app.py` | 5001 |
| `com.vanstedum.german-html-server` | `html_server.py` | 8767 |
| `com.user.curator-server` | `curator_server.py` | 8766 |
| `com.vanstedum.curator` | daily briefing cron | — |
| `com.vanstedum.lesen-refresh` | daily German news refresh | — |
| `com.vanstedum.curator-intelligence` | weekly intelligence cron | — |
| `com.vanstedum.curator-priority-feed` | priority feed updater | — |

All plists live in `~/Library/LaunchAgents/`.

### Cloudflare tunnel — NEEDS FIX

`cloudflared` is installed at `/opt/homebrew/bin/cloudflared`.  
Tunnel ID: `594dd8fb-8e28-4efd-b090-d43819a9fc00`

**Problem 1:** `~/.cloudflared/config.yml` points to `http://localhost:8444` — wrong.
Portal runs on port 5001.

**Problem 2:** `cloudflared` is not registered as a launchd service. If MacBook
reboots, the tunnel dies and `minimoi.ai` goes dark.

**Fix required before anything else:**
```yaml
# ~/.cloudflared/config.yml — corrected
tunnel: 594dd8fb-8e28-4efd-b090-d43819a9fc00
ingress:
  - service: http://localhost:5001
```
Then: `sudo cloudflared service install` → launchd plist created → survives reboot.

---

## Portal Architecture

**Stack:** Python 3 + Flask, Jinja2 templates, vanilla CSS (566 lines), no JS framework.  
**Fonts:** Playfair Display (headings) + Source Sans 3 (body) via Google Fonts.

### File layout
```
minimoi_portal/
├── app.py              — Flask routes, auth middleware, proxy wiring
├── auth.py             — user loading, password check, guest CRUD
├── config.py           — port/backend URLs/secret key from env vars
├── proxy.py            — reverse proxy + HTML/CSS/JS URL rewriting
├── auth/
│   ├── users.json      — permanent users (owner + family tier)
│   └── guests.json     — time-limited guest credentials
├── templates/
│   ├── landing.html        — public marketing page
│   ├── login.html          — username/password form
│   ├── dashboard.html      — post-login tile grid
│   ├── admin_guests.html   — owner: create/revoke guest credentials
│   └── guest_briefing.html — stripped Curator view for guests
└── static/
    └── portal.css      — all portal styles
```

### Auth tiers

| tier | who | access |
|---|---|---|
| `owner` | Robert | everything + admin panels |
| `family` | family members | full Curator + full Mein Deutsch |
| `guest` | time-limited visitors | read-only top-20 briefing + Lesen tab only |

Passwords hashed with Werkzeug (`generate_password_hash`).  
Sessions permanent (30-day cookie).  
`SECRET_KEY` from `PORTAL_SECRET_KEY` env var — currently using dev default, must set in production.

### Current accounts

- `robert` / tier `owner` — in `auth/users.json`
- No family accounts yet (adding them requires manual JSON edit — next step fixes this)
- Guest accounts managed via `/admin/guests` UI

### Config (`config.py`)
```python
CURATOR_BACKEND = os.environ.get("CURATOR_BACKEND", "http://localhost:8766")
GERMAN_BACKEND  = os.environ.get("GERMAN_BACKEND",  "http://localhost:8767")
PORT            = int(os.environ.get("PORTAL_PORT", 5001))
```
On any new host, set these env vars in the launchd plist or environment.

### Routes

```
GET       /                       public landing page
GET/POST  /login                  username/password login
GET       /logout                 clears session
GET       /dashboard              tile grid (login required)
GET       /app/curator            proxied → localhost:8766 (owner/family only)
GET       /app/curator/<path>     proxied (guests redirected to /guest/briefing)
GET       /app/german             proxied → localhost:8767 (all logged-in users)
GET       /app/german/<path>      proxied (guests: lesen path only)
GET       /guest/briefing         today's top-20 articles, no feedback buttons
GET       /admin/guests           guest management (owner only)
POST      /admin/guests/create    create guest credential
POST      /admin/guests/revoke    delete guest
```

### Proxy (`proxy.py`)

Rewrites HTML so internal backend links resolve through the portal prefix.
Example: `/api/articles` inside Curator becomes `/app/curator/api/articles`.
Handles `href`, `src`, `action`, `fetch()`, `axios.*()`, `url:` patterns in JS.
Known limitation: dynamically-constructed JS template literals not caught — acceptable
for personal/portfolio use.

---

## What Is Built and Working (2026-05-28)

- [x] Public landing page live at minimoi.ai
- [x] Login / logout (hashed password, 30-day session)
- [x] Dashboard with Curator + Mein Deutsch tiles
- [x] Owner → full Curator access via proxy
- [x] Owner → full Mein Deutsch access via proxy
- [x] Guest credential creation (admin UI, one-time credential reveal)
- [x] Guest → read-only briefing (top 20, no feedback)
- [x] Guest → Lesen only in Mein Deutsch
- [x] All three apps running as persistent launchd services
- [x] LinkedIn URL correct (`/in/robert-van-stedum/`)

---

## Next Steps — In Priority Order

---

### Step 1: Fix Cloudflare tunnel (reliability — do first)

The tunnel is the public gateway. If it's not a launchd service with the correct
port, a MacBook reboot kills the site.

**Files to touch:**
- `~/.cloudflared/config.yml` — change port to 5001
- Run `sudo cloudflared service install`
- Verify: `launchctl list | grep cloudflare` shows it running

**Time estimate:** 30 minutes

---

### Step 2: Family user management UI (`/admin/users`)

Adding a family-tier account currently requires:
1. Running `python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('password'))"` in terminal
2. Manually editing `auth/users.json`

Need a proper admin UI — mirrors the existing `/admin/guests` page.

**Files to create/modify:**
- `auth.py` — add `create_user(display_name, username, password, tier)` and `revoke_user(username)`
- `app.py` — add routes: `GET /admin/users`, `POST /admin/users/create`, `POST /admin/users/revoke/<username>`
- `templates/admin_users.html` — new template, same design language as `admin_guests.html`
- `templates/dashboard.html` — add "Users" link in owner nav (alongside "Guests")

**Time estimate:** 2-3 hours

---

### Step 3: Invite link flow — "linking"

Instead of texting someone a raw password, the owner generates a one-time invite URL.
Family member clicks it, sets their own password, account activates automatically.

**Flow:**
1. Owner goes to `/admin/users` → clicks "Generate invite link"
2. System creates a token in `auth/invites.json` with `{token, username, tier, expires_at}`
3. Owner copies URL: `https://minimoi.ai/invite/abc123def456`
4. Family member opens link → sees "Set your password" form
5. They submit → password hashed + written to `users.json` → token deleted
6. They're immediately logged in and land on dashboard

**Files to create/modify:**
- `auth/invites.json` — new file, tracks pending tokens
- `auth.py` — `create_invite(display_name, tier)` and `consume_invite(token, password)`
- `app.py` — routes: `GET /invite/<token>`, `POST /invite/<token>`
- `templates/invite.html` — new: set-password form, branded
- `templates/admin_users.html` — "Generate invite" button + copyable URL display

**Time estimate:** 3-4 hours

---

### Step 4: Password reset (owner-driven, no SMTP)

If a family member forgets their password, currently Robert has to re-hash and
edit JSON by hand. Simple fix: owner generates a new temporary password via admin UI.

**Approach A (recommended first):** Owner resets → shows new temp password once →
family member logs in with temp password → optional: force password change on next login.

**Approach B (later):** Self-service "Forgot password" → email magic link → requires
SMTP setup (e.g. SendGrid, Postmark, or iCloud SMTP relay).

Start with A. B can come when email is wired up.

**Time estimate:** 1-2 hours for Approach A

---

### Step 5: Mobile / PWA (future — noted, not urgent)

The viewport meta tag is already in all templates so the site is functional on mobile.
To make it feel native (home screen icon, full-screen launch):

- Add `static/manifest.json` with name, icons, `display: standalone`, theme color
- Add `static/apple-touch-icon.png` (180×180)
- Add `<link rel="manifest" href="/static/manifest.json">` to all templates
- Test on iOS Safari: "Add to Home Screen" → mini-moi icon, full-screen launch

This is a polish layer, ~2 hours, can be done anytime.

---

## Hosting Options: MacBook vs Mac Mini vs VPS

As of today the plan is MacBook (dev + prod) → Mac Mini (future always-on prod).
Robert is open to a hosted VPS instead. Here is the honest comparison:

### Option A: Mac Mini (original plan)
- **Cost:** $0/month (hardware already owned)
- **Pros:** No monthly fee, full control, same macOS environment as MacBook (launchd, Keychain, same commands)
- **Cons:** Needs to stay on and connected. Power outage, network blip, or macOS update = downtime. Cloudflare Tunnel still required.
- **Migration effort:** Copy repo + launchd plists + credentials. Half a day.

### Option B: Hetzner VPS (recommended if going hosted)
- **Cost:** ~$4-6/month (CX22: 2 vCPU, 4GB RAM, 40GB SSD)
- **Pros:** Always on by definition. Static IP → no Cloudflare Tunnel needed (use plain Cloudflare DNS). Professional uptime. Easy to snapshot/restore. Robert already knows Hetzner from the RVSAssociates plan.
- **Cons:** Linux (Ubuntu), so `launchd` → `systemd`, macOS Keychain → environment variables in a `.env` file or systemd service env. Small rewrite of credential loading.
- **Migration effort:** One afternoon. Mostly setting up systemd units and swapping `keyring` calls for env var reads.

### Option C: Render / Railway / Fly.io
- **Cost:** $5-10/month
- **Pros:** Nice DX, deploy from git, managed SSL
- **Cons:** The Curator and German apps are stateful (lots of JSON files as the data layer). PaaS platforms expect stateless apps. You'd need to mount persistent storage or migrate to a database. More friction than it's worth for now.
- **Verdict:** Not recommended yet. Maybe later if the data layer moves to PostgreSQL.

### Recommendation
**Short term:** Fix the tunnel on MacBook (Step 1), build features (Steps 2-4).  
**When ready to make it always-on:** Hetzner VPS is the cleanest choice — cheap,
professional, no Cloudflare Tunnel complexity, familiar from RVSAssociates planning.  
**Mac Mini:** Still a good option if it's sitting idle — zero cost and easiest migration
since it's the same OS. Only downside is it needs to stay powered on and connected.

---

## Mac Mini / VPS Migration Checklist (when ready)

```
[ ] git clone repo
[ ] python3 -m venv venv && pip install -r requirements.txt
[ ] Copy auth/users.json and auth/guests.json  (NOT committed to git — has real passwords)
[ ] Set PORTAL_SECRET_KEY env var (same value as MacBook or regenerate + log everyone out)
[ ] Set CURATOR_BACKEND and GERMAN_BACKEND if ports differ
[ ] Copy API credentials:
      Mac Mini: copy from Keychain or re-enter via keyring CLI
      VPS:      write to .env file, load in systemd units
[ ] Install and configure launchd plists (Mac Mini) OR systemd units (VPS)
[ ] Configure Cloudflare:
      Mac Mini: install cloudflared tunnel
      VPS:      point A record to VPS IP, no tunnel needed
[ ] Verify all three services start on reboot
[ ] Smoke test: landing → login → dashboard → Curator → Mein Deutsch
[ ] Stop tunnel/services on MacBook (or keep as dev-only)
```

---

## Credentials Reference

All stored in macOS Keychain via `keyring`. On Linux/VPS, swap for env vars.

| keyring service | account | contains |
|---|---|---|
| `xai` | `api_key` | xAI / Grok API key |
| `anthropic` | `api_key` | Anthropic / Claude API key |
| `telegram` | `bot_token` | Curator bot (`minimoi_cmd_bot`) |
| `telegram` | `polling_bot_token` | OpenClaw gateway bot |

Portal `SECRET_KEY` not yet in Keychain — currently uses dev default in `config.py`.
Should be added before treating this as production-hardened.

---

## Agent / Workflow Notes

- **Claude Code (terminal):** writes code, commits, runs builds
- **Claude.ai / OpenClaw:** planning, design decisions, writing specs and handoffs
- **Robert:** reviews designs before Claude Code builds them — no build starts without explicit OK
- Protected files (do not modify without instruction): `README.md`, `CHANGELOG.md`, `OPERATIONS.md`, `WHITEBOARD.md`, `docs/*`
- OpenClaw handoff docs go in `_working/` directory

---

## Recent Commits (context)

```
0d478ab  fix: correct LinkedIn URL on portal landing page
048286d  ops: portal launchd plist + full ops writeup for OpenClaw
0112d54  fix: portal landing page polish
843c94f  feat: portal launchd setup, landing page content, restart script
8995f6f  feat: minimoi portal - auth layer, reverse proxy, landing page, guest access
```
