# Handoff → OpenClaw: GitHub issues to post
*From: Claude Code · Date: 2026-06-22*
*Re: AWS parity milestone — file the follow-up issues below*

## Context / milestone

**Full feature parity is now running on AWS (minimoi.ai / EC2), validated end-to-end in production.** The German domain and Curator pipeline were rebuilt for the two-bot, containerized production architecture. Validated live:

- **Telegram** (`minimoi_system_bot`): drills, persona prompts, transcript → analysis
- **Web Gespräche** (minimoi.ai): live KI-Sitzung chat + Analysieren
- **Curator**: daily briefing + research Desk (Like / Scan / Deeper Dive)

Root cause across all broken paths: code read secrets from macOS keyring, which doesn't exist in the containers. Fixes route every key through `get_secret` (env → keyring on Mac → SSM in container). `minimoi_system_bot` now serves the German domain and **replaces the retired `minimoi_cmd_bot`**.

**Code:** committed on branch `aws-german-curator-parity` (commit `cca5901`), pending Robert's review + merge to `main`. 10 source/infra files; no roadmap/data files touched.

---

## Issues to create (4)

Claude Code does not open issues — these are for OpenClaw to file after Robert's review. Four in-app task chips already capture the same scope; the GitHub issues are the durable record.

### 1. [P1 / bug] X-bookmark pull broken on EC2 — tweepy missing + OAuth2 tokens keyring-only
**Labels:** bug, curator, aws, priority-high
The curator container can't pull X bookmarks. Two problems:
1. `tweepy` is not in `docker/requirements.curator.txt` — `x_pull_incremental.py:21 import tweepy` raises `ModuleNotFoundError` in `minimoi-curator`. (RSS curation still works; only the X pull is broken.)
2. The X OAuth2 flow in `x_oauth2_authorize.py` is keyring-based AND stateful: `get_valid_token()` reads `client_id/client_secret/access_token/refresh_token/expires_at` from keyring, and `store_tokens()` writes the rotated tokens back after every refresh (X rotates the refresh token each time — persistence is mandatory). No keychain in the container → can't authenticate.
**Scope:** add tweepy + rebuild curator; make the 5 values readable in-container (static via SSM/get_secret); provide a **writable, persistent** store for the rotating tokens (recommend a JSON file on the mounted `/opt/minimoi/data/curator` volume; SSM PutParameter is blocked for `minimoi-deploy`); refactor `get_valid_token()/store_tokens()` accordingly; seed the initial refresh_token; verify two consecutive `docker exec minimoi-curator python x_pull_incremental.py` runs persist the rotated token.
**Impact:** until fixed, briefings are RSS-only (no fresh X bookmarks).

### 2. [P3 / bug] Curator research Desk shows "never run" for a thread that has run
**Labels:** bug, curator, ui, priority-low
At minimoi.ai/app/curator/research/dashboard ("The Desk"), thread "Trump Iran Deal Israel" shows "1 session" but its status reads "never run" (▶ icon). Run-status display disagrees with session count. Likely the template keys "never run" off a missing `last_run_at` while the session count comes from a different source. Fix: derive run-status from the same source as the session count (or backfill `last_run_at` from the latest session) so "never run" only shows when sessions == 0. Files: `research_routes.py` (/research/dashboard), `_NewDomains/research-intelligence/web/dashboard.html`.

### 3. [P3 / bug] German Dropbox watcher not container-safe
**Labels:** bug, german, aws, priority-low
`domains/german/watch_transcripts.py` (`!german watcher start`) reads its Telegram token from macOS keyring (lines ~68-69) and watches a hardcoded `~/Dropbox` path — neither exists in the containers, so invoking it on EC2 fails. Low priority: no Dropbox on EC2 and the watcher isn't used in production (Robert: "not a priority"). When picked up: decide if the watcher is wanted on AWS; if retired, gate `!german watcher` to inform/no-op off Dropbox-capable nodes; if kept, switch the token to `utils.telegram.get_system_token()/get_chat_id()` and make the path configurable. Pattern reference: the already-applied fixes in `get_german_session.py` and `reviewer.py`.

### 4. [P3 / tech-debt] Move German off the fat system-bot onto HTTP endpoints
**Labels:** refactor, german, aws, tech-debt
Not a bug — a parity-port shortcut to record. `minimoi-system-bot` currently carries the whole German domain and shells out to scripts, and mounts the same `domains/german/data` volume as the web container. Cleaner design: expose German session/drill/transcript generation as HTTP endpoints on the existing `mein-deutsch` container and have the bot call them — returns system-bot to a light image, single owner of the German data volume, no duplicated code. Needs a small design pass (endpoints, auth between containers, who owns in-process drill/phrase state) before building. Files: `telegram_system_bot.py`, `telegram_bot.py`, `domains/german/`, `docker/Dockerfile.telegram`, `docker-compose.prod.yml`.

---

## Also for the roadmap (OpenClaw's call, not an issue)
- `_working/NEAR_TERM_PLAN.md` Block A is marked complete; this milestone closes the AWS-parity arc. Claude Code did **not** edit roadmap/CHANGELOG beyond the Block-A status note — those updates are yours.
