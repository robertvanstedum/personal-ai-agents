# mini-moi Portal — Build Handoff
**Date:** 2026-05-28  
**Branch:** `feat/german-html-interface`  
**Status:** Deployed and running on MacBook, accessible at minimoi.ai

---

## What Was Built

### 1. minimoi_portal — New Flask App

A reverse-proxy portal that gates access to Curator and Mein Deutsch behind authentication.

**Location:** `minimoi_portal/` (package at repo root)

**Files:**
- `app.py` — Flask routes, auth decorators, guest/admin APIs
- `auth.py` — User loading, password hashing, guest/pending management
- `proxy.py` — HTML/CSS/JS URL rewriting reverse proxy with portal nav bar injection
- `config.py` — Backend URLs, secret key, port config
- `guest_data.py` — Per-guest feedback/comment storage
- `auth/users.json` — Owner account (robert, tier=owner)
- `auth/guests.json` — Approved guest accounts (30-day expiry)
- `auth/pending.json` — Pending registration queue
- `guest_data/` — Per-guest feedback.json and comments.json
- `templates/` — login, register, dashboard, landing, admin_guests, guest_briefing, admin_guests
- `static/portal.css` — Portal design system

### 2. Auth Tiers

| Tier | Access |
|------|--------|
| owner | Everything including /admin/guests |
| family | Curator + German (full) — not yet provisioned |
| guest | Guest briefing + German Lesen only, no admin |

**Login:** username OR email address accepted.

### 3. Guest Registration & Approval Flow

1. Guest registers at `/register` (name + email + password)
2. Entry saved to `auth/pending.json`
3. Owner gets Telegram notification via `minimoi_cmd_bot` bot token (keyring: `telegram` / `bot_token`)
4. Owner approves at `/admin/guests` → entry moves to `auth/guests.json` (30-day expiry)
5. On approval:
   - Approval email sent to guest from `robert.vanstedum@gmail.com` via Gmail SMTP
   - Telegram sent to owner confirming email was dispatched

### 4. Gmail SMTP — Approval Emails

- **From:** robert.vanstedum@gmail.com
- **SMTP:** smtp.gmail.com:465 (SSL)
- **Credential:** Gmail App Password stored in macOS Keychain
  - Service: `gmail`
  - Account: `app_password`
  - Value: 16-char app password (generated 2026-05-28)
- **Triggered by:** POST /admin/guests/approve/<token>

### 5. Cloudflare Tunnel

- Both `minimoi.ai` and `mini-moi.ai` route to portal on port 5001
- Tunnel token stored in macOS Keychain: service=`cloudflare`, account=`tunnel_token`
- Runs as launchd agent: `com.vanstedum.cloudflared`
- Script: `scripts/start_cloudflared.sh`
- Plist: `~/Library/LaunchAgents/com.vanstedum.cloudflared.plist`

### 6. Portal as launchd Agent

- Plist: `~/Library/LaunchAgents/com.vanstedum.minimoi-portal.plist`
- Logs: `logs/portal_stdout.log`, `logs/portal_stderr.log`
- Port: 5001
- Start/stop: `launchctl start/stop com.vanstedum.minimoi-portal`

### 7. Reverse Proxy

`proxy.py` rewrites all internal URLs so Curator (`localhost:8766`) and German (`localhost:8767`) render correctly under `/app/curator` and `/app/german` prefixes.

**Rewrites:**
- HTML tag attributes: href, src, action, data-src, data-url
- Inline `style="background-image: url('/...')"` attributes
- CSS `url('/...')` references
- Inline `<script>` blocks: fetch(), axios, url:, XHR.open(), window.location
- External `.js` files
- Redirect `Location:` headers

**Portal nav bar** injected at top of every proxied page (sticky positioning):
- mini-moi → /dashboard
- Curator / German (active one highlighted)
- Display name + Sign out

### 8. Guest Access Restrictions

**German (`/app/german/<path>`):**
- Guests allowed: `static/`, `lesen`, `api/lesen-*`, `api/translate`, `api/save-phrase`
- Guests blocked: `admin` (redirects to German root)

**Curator (`/app/curator/<path>`):**
- Guests allowed: `static/`, `interests/` (deep dive HTML files)
- Guests blocked: everything else (redirects to `/guest/briefing`)

### 9. Guest Briefing

`/guest/briefing` — Portal-native page (not proxied) showing today's Curator articles with:
- Like / Dislike / Save buttons
- Comment box
- Deep Dive button (calls Curator `/deepdive` endpoint, ~30-60s)
- State persisted per-guest in `guest_data/feedback.json` and `guest_data/comments.json`

---

## Owner Account

- **Username:** robert
- **Email:** robert.vanstedum@gmail.com
- **Password:** (stored securely, not documented here)
- **Tier:** owner

---

## Known Next Steps / Not Yet Built

- Family tier user management UI (`/admin/users`) — deferred
- Password reset flow (owner-driven) — deferred
- PWA manifest + apple-touch-icon for mobile — deferred
- Mac Mini migration (currently MacBook-only) — future
- Telegram notification uses `rvsopenbot` bot in practice (keyring naming inconsistency) — future cleanup

---

## Commits (this session, on feat/german-html-interface)

- `5c93f59` — Portal: inject nav bar, fix inline style URLs, clarify registration
- `2d67818` — Portal: fix guest static asset access + approval Telegram notification
- `603da4d` — Portal: send approval email to guest via Gmail SMTP
