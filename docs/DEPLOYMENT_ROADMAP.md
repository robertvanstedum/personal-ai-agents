# mini-moi Deployment Roadmap & Architecture Strategy
## Document v1.0
**Date:** 2026-04-19  
**Author:** Robert van Stedum  
**Context:** Written after language-german domain build and Telegram bridge debugging session. Captures the real-world deployment constraints discovered during that work and the strategic path forward.

---

## 1. The Problem We Discovered Today

The language-german domain works. The Telegram bridge works. But everything runs on a MacBook that sleeps, closes its lid, and travels with Robert. When the MacBook is unavailable:

- `telegram_bot.py` stops polling — `minimoi_cmd_bot` goes dark
- OpenClaw stops running — no orchestration, no pipeline triggers
- The German domain pipeline cannot fire — no reviews, no Anki cards, no lesson plans
- The curator stops — no daily briefings

This is acceptable for daily home use. It is not acceptable for a Vienna trip where the whole point is to use the system on the go.

**The real-world forcing function:** Robert wants to submit a German practice transcript from Vienna and get a review back on his iPhone. That requires the pipeline to be running somewhere that is not his MacBook.

---

## 2. Current Architecture (MacBook-Dependent)

```
iPhone (Grok Voice)
    │
    │  transcript via Telegram
    ▼
minimoi_cmd_bot
    │
    ▼
telegram_bot.py  ←── runs on MacBook, must be on and awake
    │
    ├── parse_transcript.py
    ├── reviewer.py  ←── calls Anthropic API
    ├── status.py
    └── OpenClaw  ←── runs on MacBook
```

**Single point of failure:** MacBook. If it's closed, asleep, or in Chicago while Robert is in Vienna, nothing works.

---

## 3. Target Architecture (Always-On)

```
iPhone (Grok Voice / Telegram)
    │
    │  transcript or command
    ▼
minimoi_cmd_bot
    │
    ▼
Cloud Relay (lightweight, always-on)
    │  receives Telegram messages
    │  queues them
    │  forwards to home compute
    ▼
Mac Mini (always-on home server)  ←── target v1.2
    │
    ├── telegram_bot.py (systemd service, auto-restart)
    ├── parse_transcript.py
    ├── reviewer.py
    ├── status.py
    ├── OpenClaw
    └── All domain data (local, private)
```

**Key properties of target state:**
- Mac Mini is always on, always polling
- Cloud relay is stateless and cheap — just a message forwarder, no data stored there
- All compute and data stays local on Mac Mini
- Robert can use the system from anywhere in the world
- If Mac Mini goes down, cloud relay queues messages until it comes back

---

## 4. Telegram Bot Architecture (Locked)

This was clarified and decided during the language-german build. It is binding going forward.

### Two bots, clean separation:

**`minimoi_cmd_bot`** — Robert's primary interface for everything
- Owned and polled by `telegram_bot.py` exclusively
- Handles: `!german` commands, transcript submission, future domain commands, curator commands
- Future: forwards open-ended agent requests to OpenClaw via local mechanism
- OpenClaw never polls this token directly

**`@rvsopenbot`** — curator UI bot, legacy
- Handles: inline keyboard buttons (thumbs up/down on briefings)
- Exists only because Telegram button callbacks required a separate implementation
- Candidate for retirement when curator UI is redesigned

### One process polls, others receive:
`telegram_bot.py` is the sole Telegram poller. OpenClaw and future agents receive messages forwarded by `telegram_bot.py` via local mechanism (file drop, socket, or queue — to be decided at v1.2). No agent polls the Telegram API directly. This ensures no 409 conflicts and maintains agent portability.

### Agent portability principle:
The Telegram interface (`telegram_bot.py`) is independent of any specific agent. If OpenClaw is replaced by a different agent in the future, the Telegram interface does not change. Only the routing target changes.

---

## 5. Roadmap

### Now — Vienna Prep (Current sprint, through May 2026)
**Priority: functionality. Portability is secondary.**

- ✅ Language-german domain built and validated
- ✅ Telegram bridge working (minimoi_cmd_bot → pipeline → summary back)
- ✅ Two-bot architecture clarified and locked
- 🔲 Telegram bridge fully automatic (auto-detect transcript, no manual paste)
- 🔲 Morning German reminder from OpenClaw
- 🔲 `!german next` flag in reviewer.py (currently stubbed)
- 🔲 Anki import workflow confirmed

**Acceptable limitation for Vienna:** MacBook must be on and connected for the pipeline to run. Workaround: MacBook stays home plugged in, or Robert uses manual fallback (paste transcript in webchat, run pipeline manually).

---

### v1.2 — Mac Mini Migration (Post-Vienna, Summer 2026)
**Priority: always-on home server. Unlocks full mobile use.**

This is the real-world reason for the Mac Mini. Not just a hardware upgrade — it's the foundation for using mini-moi reliably from anywhere.

**Migration targets:**
- Move entire mini-moi repo to Mac Mini
- Set up systemd services for `telegram_bot.py` and OpenClaw — auto-restart on reboot, auto-start on power-on
- Configure Tailscale for secure remote access from anywhere (free, 20 min setup)
- Validate full pipeline from iPhone while MacBook is off
- Mac Mini becomes the sole compute host — MacBook is just a development machine

**Tailscale is non-negotiable for v1.2.** It provides:
- SSH access to Mac Mini from Vienna or anywhere
- Ability to restart services remotely from iPhone
- Secure tunnel without exposing Mac Mini to public internet
- Free for personal use

---

### v1.3 — Cloud Relay (Post-Mac Mini, when travel frequency justifies it)
**Priority: portability. Only build if Mac Mini proves insufficient.**

**The hybrid architecture:**
- Mac Mini stays home as compute brain — all data local, all processing local
- Lightweight cloud relay (Cloudflare Workers, Railway, or similar — $0-5/month) acts as Telegram message receiver
- Relay queues messages when Mac Mini is unreachable, forwards when it comes back
- Mac Mini failure = processing pauses, not message loss

**Build trigger:** Build this only if Robert finds himself traveling frequently enough that Mac Mini downtime (power outage, internet outage) is causing real problems. If the Mac Mini + Tailscale combination proves reliable enough, skip this entirely.

**Cloud service candidates when ready:**
- Cloudflare Workers — free tier, serverless, global, stateless relay
- Railway — $5/month, simple deployment, good for small always-on services
- Fly.io — free tier, good latency, Docker-based

**What does NOT go to the cloud:**
- Session data
- Anki cards
- Progress tracking
- Any personal content
- Anthropic API calls (these go direct from Mac Mini)

The cloud relay is a dumb pipe — it receives a Telegram message and forwards it. Nothing more.

---

## 6. Immediate Next Steps (Before Vienna)

In priority order:

1. **Confirm Telegram bridge auto-trigger** — OpenClaw revert of dmPolicy, end-to-end test with real transcript
2. **Morning German reminder** — daily scheduled message from OpenClaw in German
3. **Anki import workflow** — confirm cards import cleanly into Anki desktop
4. **MacBook stay-on plan for Vienna** — decide: leave MacBook home plugged in, or accept manual fallback
5. **Order/plan Mac Mini** — not urgent for Vienna but the real-world case is now clear

---

## 7. What This Is Really About

mini-moi started as a daily briefing tool. The language-german domain proved it can be a real-time personal practice system. The Vienna trip is the first time Robert will genuinely need the system to work away from his desk.

The Mac Mini migration is not a technical nicety. It is the infrastructure that makes mini-moi a genuinely mobile personal intelligence system rather than a desktop tool that happens to have a Telegram interface.

The cloud relay is the layer that makes it resilient. But resilience is a post-Vienna problem. Functionality is the Vienna problem.

**The principle for all decisions until May 10:** If it doesn't help Robert practice German before Vienna, it waits.
