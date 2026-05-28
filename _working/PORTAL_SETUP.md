# minimoi Portal — Setup and Operations
**Created:** 2026-05-27  
**Status:** Running on MacBook. Mac Mini deployment pending.  
**Owner:** Robert Van Stedum  
**For OpenClaw:** Add portal service to OPERATIONS.md (see section below)

---

## What Was Built

A Flask portal app (`minimoi_portal/`) that serves as the public face and authentication
gateway for the mini-moi platform at `minimoi.ai`.

### Architecture

```
Internet → minimoi.ai → Cloudflare Tunnel → Mac Mini:5001 (portal)
                                                   ↓ authenticated proxy
                                              localhost:8766 (Curator)
                                              localhost:8767 (Mein Deutsch)
```

Currently running on MacBook at `http://localhost:5001`.  
Cloudflare Tunnel pending Mac Mini setup.

---

## Auto-Start on Login (MacBook — already configured)

The portal runs as a **launchd agent** and starts automatically every time you log in.
You do not need to start it manually. After a reboot: log in → portal is up.

The plist is installed at:
```
~/Library/LaunchAgents/com.vanstedum.minimoi-portal.plist
```

**`RunAtLoad: true`** — starts immediately when the plist is loaded (at login).  
**`KeepAlive: true`** — if the process crashes, launchd restarts it automatically.

### Verify it is registered

```bash
launchctl list | grep minimoi-portal
```

A running entry looks like: `55724  0  com.vanstedum.minimoi-portal`  
(first number = PID, must be non-zero for it to be running)

### If the plist is ever lost or needs reinstalling

Reference copy is committed at `launchd/com.vanstedum.minimoi-portal.plist`.

```bash
# Copy to LaunchAgents and load
cp launchd/com.vanstedum.minimoi-portal.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.vanstedum.minimoi-portal.plist
```

---

## File Locations

| File | Purpose |
|------|---------|
| `minimoi_portal/app.py` | Flask app — all routes |
| `minimoi_portal/auth.py` | Authentication, guest management |
| `minimoi_portal/proxy.py` | Reverse proxy to Curator + German backends |
| `minimoi_portal/config.py` | Backend URLs, port, secret key |
| `minimoi_portal/templates/` | HTML templates (landing, login, dashboard, etc.) |
| `minimoi_portal/static/portal.css` | Parchment palette styles |
| `minimoi_portal/auth/users.json` | Permanent user credentials — **gitignored, local only** |
| `minimoi_portal/auth/guests.json` | Guest credentials — **gitignored, local only** |
| `minimoi_portal/auth/users.json.example` | Template for creating users.json |
| `launchd/com.vanstedum.minimoi-portal.plist` | Reference plist — committed to repo |
| `~/Library/LaunchAgents/com.vanstedum.minimoi-portal.plist` | Active plist (local, not in repo) |
| `restart_portal.sh` | Restart script |

---

## User Tiers

| Tier | Access |
|------|--------|
| `owner` (Robert) | Full — Curator + German + guest admin |
| `family` | Full — Curator + German |
| `guest` | Limited — today's briefing only + Lesen tab only, auto-expiring |

Guest restrictions:
- Curator: today's briefing view only (no scores, no feedback, no archive, no deep dives)
- German: landing + Lesen only (all other tabs blocked by path check)
- Expires at timestamp set on creation

---

## Port

**5001** on MacBook — macOS AirPlay Receiver occupies port 5000.

On Mac Mini: check `lsof -i :5000`. If free, switch to 5000:
- `minimoi_portal/config.py` → `PORT = 5000`
- `~/Library/LaunchAgents/com.vanstedum.minimoi-portal.plist` → `PORTAL_PORT = 5000`
- Cloudflare Tunnel public hostname → `http://localhost:5000`

---

## Day-to-Day Management

### Restart (after code changes to templates, CSS, app.py)

```bash
./restart_portal.sh
```

### Start / stop manually

```bash
# Start
launchctl load ~/Library/LaunchAgents/com.vanstedum.minimoi-portal.plist

# Stop
launchctl unload ~/Library/LaunchAgents/com.vanstedum.minimoi-portal.plist
```

### View logs

```bash
# Errors and startup failures
tail -f logs/portal_stderr.log

# Access log (requests)
tail -f logs/portal_stdout.log
```

### Health check

```bash
launchctl list | grep minimoi-portal
# Expect: <PID>  0  com.vanstedum.minimoi-portal
# PID non-zero = running
```

---

## Credentials Management

### Permanent users (owner / family)

`minimoi_portal/auth/users.json` is gitignored — local only. Template at
`minimoi_portal/auth/users.json.example`.

```bash
# Generate a password hash
source venv/bin/activate
python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('YOURPASSWORD'))"

# Paste hash into users.json (follow structure in users.json.example)
```

Keep a backup of `users.json` in mini-moi-private before Mac Mini setup.

### Guest accounts (via web UI)

1. Log in at `http://localhost:5001` as owner
2. Dashboard → Guests (top nav)
3. Fill display name, password, expiry days → Create
4. Credentials shown once — copy and send to guest
5. Guest logs in at `https://minimoi.ai/login`

Revoke: Admin → Guests → Revoke.

---

## Secret Key

Flask session secret — must be real on production (Mac Mini).

```bash
# Generate
python3 -c "import secrets; print(secrets.token_hex(32))"

# Add to plist EnvironmentVariables:
# <key>PORTAL_SECRET_KEY</key>
# <string>GENERATED_KEY_HERE</string>
```

MacBook dev key (default) is acceptable for local use.  
**Mac Mini: must set before going live on minimoi.ai.**

---

## Mac Mini Deployment Checklist

When Mac Mini setup day arrives (see `MACMINI_MIGRATION_PLAN_v2.md`):

- [ ] `git pull` — portal code is on `main`
- [ ] Restore `minimoi_portal/auth/users.json` from backup
- [ ] Create `minimoi_portal/auth/guests.json`: `{"guests": []}`
- [ ] Generate and set `PORTAL_SECRET_KEY` in plist
- [ ] Check port 5000 free: `lsof -i :5000`
- [ ] Copy plist: `cp launchd/com.vanstedum.minimoi-portal.plist ~/Library/LaunchAgents/`
- [ ] Update PORTAL_PORT in plist if switching to 5000
- [ ] `launchctl load ~/Library/LaunchAgents/com.vanstedum.minimoi-portal.plist`
- [ ] Verify: `curl http://localhost:5001/` returns HTML
- [ ] Cloudflare Tunnel: public hostname `minimoi.ai` → `http://localhost:5001`
- [ ] Test from external network: `https://minimoi.ai`

---

## Cloudflare Tunnel Setup (pending Mac Mini)

Both domains on Cloudflare (nameservers propagated 2026-05-27):

| Domain | Role | Status |
|--------|------|--------|
| `minimoi.ai` | Primary | ✅ Cloudflare active |
| `mini-moi.ai` | 301 → minimoi.ai | ✅ Cloudflare active |

Tunnel setup on Mac Mini:
```bash
brew install cloudflare/cloudflare/cloudflared
cloudflared service install <TUNNEL_TOKEN>
# Token from: Cloudflare dashboard → Zero Trust → Networks → Tunnels → Create tunnel
```

Cloudflare dashboard after tunnel:
- Public hostname: `minimoi.ai` → `http://localhost:5001`
- Redirect rule: `mini-moi.ai/*` → 301 → `https://minimoi.ai`

---

## For OpenClaw — OPERATIONS.md Update

Add this entry to the **Services Overview** table in OPERATIONS.md:

| Service | Label | What It Does |
|---------|-------|-------------|
| Portal | `com.vanstedum.minimoi-portal` | Public landing page + auth gateway. Proxies authenticated users to Curator (8766) and German (8767). Port 5001. Runs on login via launchd. |

Add to the **Daily Health Check** section:
```bash
# Is portal running?
launchctl list | grep minimoi-portal
# Expect non-zero PID
```

Add a **Portal** section to OPERATIONS.md covering:
- Port 5001, restart with `./restart_portal.sh`
- Log files: `logs/portal_stdout.log`, `logs/portal_stderr.log`
- Guest management via web UI at `/admin/guests`
- `minimoi_portal/auth/users.json` is gitignored — keep backup in mini-moi-private
