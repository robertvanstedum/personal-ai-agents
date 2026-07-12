# Spec: CoS Web Interface — Skeleton Now, Domain Later

**Status:** Spec Ready — Phase A urgent, buildable immediately
**Build queue #:** TBD — assign at registration
**Date:** 2026-07-12
**Author:** Claude.ai design session (Fable 5)
**Relationship to spec #133:** Separate concern (front end, not coordination layer). Does not block, and is not blocked by, #133 Phase 1's containerization work — should work against whatever `chief_of_staff.py` instance is currently running in dev.

---

## Intent

Robert needs a laptop-based interface to CoS *now* — Telegram alone is hindering iterative testing during Phase 1 build. This spec is split deliberately: **Phase A** is a minimal, functional, unstyled skeleton, buildable immediately, that unblocks testing today. **Phase B** captures the fuller domain/visual direction discussed tonight so it isn't lost — but it's explicitly deferred, not part of this build.

---

## What's Not Changing

- Telegram bots (`minimoi_cos_bot`, `minimoi_cos_test_bot`) — untouched, unaffected
- `chief_of_staff.py`'s core chat/memory/tool logic — untouched; the skeleton calls the same `_chat()` path Telegram already calls
- Does not require #133 Phase 1's containerization to be complete — builds against the currently-running dev instance

---

## Phase A — Skeleton (build now)

**Goal: Robert can open a browser tab on his laptop, type a message, see CoS's response — nothing more.**

- One new route on `chief_of_staff.py`'s existing Flask service (port 8769): `GET /ui` — a single page, plain HTML, no styling.
- Text input + submit → calls the same `_chat()` function Telegram already calls. No new logic, no new path.
- Scrolling conversation log on the same page — shows the exchange as it happens.
- **Read-only memory view**, same page or `GET /ui/memory`: dumps current `cos_memory.md` content (same cap as context assembly). This is cheap to add and directly useful for testing — confirms what's actually being stored, without opening the file manually.
- **No auth.** Localhost-only, single-user, dev tool. This is a deliberate, temporary scoping choice — flagged explicitly here so it isn't forgotten, and it must be added before this is ever exposed beyond localhost (see Phase B).
- **No voice input in Phase A.** Adds real complexity (browser mic access, Whisper wiring) that shouldn't gate an urgent text-based unblock. Deferred to Phase B.
- **No styling.** Plain functional HTML. "Pretty later," taken literally.
- **Structural shape, stubbed now (added 2026-07-12):** all four tabs present in the nav — Talk (functional), Feed / To-do / Archive (visible, clearly marked "coming soon," non-functional). This is the app's real shape from day one, not a rebuild later — Phase B fills tabs in, doesn't add them.
- **Placeholder art containers, not real images:** reserve the structural positions where Chicago photos will eventually go — at minimum a header/banner area — as clearly-labeled empty containers (e.g., a bordered box reading "[Chicago photo — header]"), not stock images or lorem-picsum filler. Phase B becomes "drop the real photo in," not "figure out where a photo goes." Exact composition (how many placeholders, where beyond the header) is a Phase B visual decision — Phase A just reserves the space so the shape is right.

### Pre-Flight (Claude Code, before building)

Confirm which running `chief_of_staff.py` instance Robert's Mac testing actually hits — the dev/standby instance, not prod — and confirm its Flask service is already reachable locally (localhost:8769 or equivalent) in that mode. Report back if it isn't, before adding routes.

### Build Tasks

1. Pre-flight check above
2. Add `GET /ui` route serving a single-page conversation view
3. Wire message submission to the existing `_chat()` function
4. Add the read-only memory view (same page or `/ui/memory`)
5. Stub the four-tab nav (Talk functional, Feed/To-do/Archive as visible "coming soon" placeholders) and reserve a labeled placeholder container for the header photo
6. No auth, no styling — confirm this is intentional, not an oversight, if reviewed later

### Definition of Done

- [ ] Robert can open a browser tab, type a message, get a CoS response — no Telegram involved
- [ ] Memory view shows current `cos_memory.md` content
- [ ] Zero regression to Telegram-based interaction — both paths call the same underlying function
- [ ] All four tabs visible in nav; Talk functional, other three clearly marked as placeholders
- [ ] Header photo container present, clearly labeled as a placeholder — not a real image, not stock filler
- [ ] Explicitly unstyled, explicitly unauthenticated — both noted as deliberate Phase A scope, not gaps

---

## Phase B — Domain & Visual Direction (deferred, captured now so nothing's lost)

**Not part of this build. Direction only, for whenever front-end work resumes properly.**

- **Visual language, not tab structure, borrowed from German/Portuguese:** parchment/dark-nav design system, Georgia serif headings, book-cover-to-pages transition pattern. CoS's own tab structure, not theirs.
- **Chicago photography as CoS's visual identity** — personal photos, not a borrowed cultural aesthetic (unlike German→Germany, Portuguese→Brazil). Needs actual image files when this phase starts — a small set (skyline/street shots), not one image stretched everywhere.
- **Tab set:** Talk (the skeleton's conversation view, styled), Feed (decisions/actions/recent history — graduates from "CoS view in Guild" into CoS's own home), To-do (filtered action-type `cos_memory.md` entries, open/closed), Archive (browse/search history).
- **Auth:** reuse the existing three-tier portal auth (owner/family/guest) — required before any exposure beyond localhost. This is the item Phase A explicitly deferred.
- **Voice:** input reuses the existing Whisper path (same as Telegram voice notes), triggered from a browser mic. Voice *output* (text-to-speech) is a separate, explicit decision — default to text-back for v1, don't assume voice-back.
- **Mobile-first** — becoming Robert's primary channel per earlier discussion; design for that, not desktop-first with mobile as an afterthought.

---

## Answers to Claude Code's Pre-Build Questions (2026-07-12)

**On the Guild CoS section:** correctly reverted. This spec supersedes it — spec #133's "CoS View in Guild" language is obsolete; Feed lives in CoS's own domain, not Guild, as this document already says.

**1. URL structure:** Phase A stays exactly where it is — `localhost:8769/ui`, direct, no portal integration, dev-only. That's deliberate; don't move it. Phase B, when it happens, should live inside the existing portal's URL space — something like `app.minimoi.ai/cos/` — not its own domain or a directly-exposed port.

**2. Own Flask service or merged into the portal?** Keep them separate. CoS keeps its own isolated Flask service on 8769 — that's "domains stand alone" (spec #133 Principle #4), and it's also what makes the coordination-layer/backend-swap architecture clean. For Phase B, the **portal becomes the authenticated gateway**, reverse-proxying to CoS's service after checking auth — not merging CoS's code into the portal's codebase, and not exposing 8769 directly to the internet. On EC2, once that proxy exists, 8769 should bind to localhost/internal only — smaller exposure surface, consistent with tonight's credential-scoping discipline.

**3. Primary views, precisely:**
- **Talk** — the chat interface (Phase A)
- **Feed** — `cos_memory.md`'s episodic entries (decisions, actions, notes, questions)
- **To-do** — open action-type entries from `cos_memory.md`, **merged with pending `guild.cos_agenda` recommendations.** These are two different backend stores but the same conceptual category to Robert — "things CoS flagged for me" — worth combining in the view even though they don't share a data source.
- **Archive** — full/older history, searchable, across `cos_memory.md` + `agent_logs`

**4. "Master Craftsman" — not this spec, don't let it bleed in.** That's a Build-domain concept (Build Intelligence / the Build domain refresh Robert mentioned as a separate future multi-agent effort — Claude Code prime + a junior agent). Unrelated to CoS's UI. CoS's role label in this interface is CoS / right-hand advisor, per spec #133's Architecture Principle #4 — not Master Craftsman.

**5. Chat interface scope:** in scope now. It's Phase A's entire point — the "Talk" tab *is* the urgent deliverable, not something deferred to Phase B.

---

## Commit

| Item | Location | Actor |
|---|---|---|
| This spec | `docs/specs/spec_cos_web_interface_2026-07-12.md` | Claude Code |
| Build queue entry | New, Phase A scoped as the immediate deliverable | Claude Code |

Registration and build approval: Robert.

---

*Spec: CoS Web Interface · 2026-07-12 · Claude.ai (Fable 5)*
*Phase A: build now. Phase B: direction captured, not scheduled.*
