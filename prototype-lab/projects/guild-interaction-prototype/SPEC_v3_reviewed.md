# Guild interaction — first version specification

**Revision 3 · September 26, 2026 · Claude Code · build authorized by Robert (local prototype, dev mount under owner guard)**

**Revision 3 changes** (source: `documents/ROBERT_PROTOTYPE_FEEDBACK_1_2026-09-26.md` [F1R], including Robert's follow-up; revision 2 is preserved unchanged as `SPEC_v2_reviewed.md`, SHA-256 `441538ea…3184`)

| # | Feedback | Change | Sections |
|---|---|---|---|
| R3-1 | "hard to know where to go"; "a cleaner chat … when I come in"; "a clean shop floor is a good metaphor" | New arrival page, **Shop floor**, at `/guild/build`: a compact **Status** strip on top; below it, the Master Craftsman conversation docked in the page (the dominant zone) and a narrow rail with **Needs you** (≤ 3), **Continue**, **Post-its** (≤ 4) and one "Open the bench →" link. The Build door and "Continue where you were" land here. The bench becomes the detail view, and its behaviour is unchanged. | §2, §2a, §4.1 |
| R3-2 | "a stop light red yellow green for some key areas" | Five lights, four states (green · yellow · red · grey-unknown), each with a word, a shape and a reason; derived by explicit rules (§2a table); unknown is never green | §2a, §10 |
| R3-3 | "too much white"; "we need section boundaries" | Deeper, warmer page surface. Each zone (top bar, honesty line, conversation, Stoplights, Reminders, Post-its) is a bounded section with its own surface tone, a visible edge and a small-caps header, with consistent gutters. Clutter inside zones goes: no nested cards, no badge on every line. Tokens apply site-wide. | §8.1 |
| R3-4 | "busy"; "too much detail too soon" | One compact honesty line on the Shop floor instead of repeated badges. Per-item sample/unknown marks are kept but made smaller. No arrange instructions on arrival. | §2a |
| R3-5 | "This would be the main mobile interface too" | Phone arrival = Shop floor: stoplight strip (tap to expand), conversation, pinned composer; reminders and post-its behind one chip that opens a sheet | §9 |
| R3-6 | Dev mount (Robert-approved) | Binding, prefix and owner-guard rules recorded here (previously only in INTEGRATION_MAP §4) | §8.5 |


Status: revision 2 resolves the five findings of Codex's review of revision 1 (`reviews/CODEX_PROTOTYPE_SPEC_REVIEW_2026-09-26.md`, reviewed SHA-256 `78b59c67…9f94`; revision 1 is preserved unchanged as `SPEC_v1_reviewed.md`) and records Robert's seven decisions. It is not a registered Guild/Rooms spec and does not authorize dev or production changes.

**Revision 2 changes**

| # | Codex finding / decision | Change | Sections |
|---|---|---|---|
| F1 | Simulated filing said "filed" | Every post-confirm label reads `simulated filing · local receipt r-xxxx · file not uploaded`; reload keeps name, size, digest as metadata only, never bytes or the `File` object; confirmed and reloaded states tested | §5.3, §7.3, §10 |
| F2 | Off-record boundary vs persisted attachments and direct invite | Attach, invite and every consequential action are disabled while off the record, with the reason shown; reload test proves no off-record text, file metadata, participant change or receipt reached storage | §4.4, §4.5, §5.2, §10 |
| F3 | "No source" vs "source failed" | Two named states: `not_configured` (→ not instrumented, or an explicit per-panel sample opt-in with a visible badge) and `read_failed`/`invalid` (→ unknown, never sample, never zero); the queue adapter reads and validates the JSON itself with error provenance; both cases tested | §3.1, §7.1, §10 |
| F4 | Engineering-quality deliverable missing | `FINDINGS.md` named as a deliverable with its fields; "none found in this bounded review" is valid | §10, §11 |
| F5 | Owner guard must fail closed | With `PROTOTYPE=False`, blueprint registration raises unless a real owner guard and the server proposal/write service are bound; focused test; §1/§2 reworded: templates and assets are intended for reuse, production binding is a separate reviewed change | §1, §8.3, §8.4, §10 |
| Q1–Q7 | Owner decisions | Recorded as ACCEPTED (§11) | §11 |

**Sources cited below**

| Key | Document |
|---|---|
| [H] | `planning-studio/initiatives/INIT-2026-0007-interaction-vision/CLAUDE_CODE_PROTOTYPE_BUILD_HANDOFF_2026-09-26.md` |
| [WP] | `.../PROTOTYPE_FIRST_WORKING_PLAN_2026-09-26.md` |
| [OQ] | `.../GUILD_OPERATIONS_AND_REPO_QUALITY_PLAN_2026-09-26.md` §1 |
| [R3] | `.../reviews/CLAUDE_CHAT_STORYBOARD_OPTIONS_2026-09-26.md` (wording resolutions; user-facing states table) |
| [B1]–[B6], [C6] | `.../mockups/round-3-options-2026-09-26/` frames |
| [RC] | `.../documents/ROBERT_CORRECTIONS_2026-09-26.md` |
| [IN] | `.../documents/INTENT_ROBERT_2026-09-26.md` |
| [D1] | Robert, Sep 26 mid-build: first version of the real thing; adapters with live reads; INTEGRATION_MAP |
| [D2] | Robert, Sep 26 mid-build: look and flow in one place each; templates are the production templates; PROTOTYPE flag |
| [Q] | Existing portal: `minimoi_portal/app.py` (`/guild/build/queue` ~l.2086, `_BUILD_QUEUE_STATUSES` l.1258), `templates/guild/build_queue.html`, `guild_landing.html`, `_portal_nav.html`, `_section_subnav.html`, `static/guild.css` |

---

## 1. Purpose and boundary

**Purpose.** The first working version of the Guild interaction: the B-style Build workbench, a floating Master Craftsman (MC) conversation on every page, Operate evidence, a Build Queue round trip and a conversation-led phone layout [H §Product direction; WP §Direction]. It is production-bound: templates, CSS and behaviour modules are intended for reuse in `minimoi_portal` (production binding is a separate reviewed change, §8.4), and data flows through adapters that already read real sources where a safe read-only source exists [D1, D2]. It runs locally and standalone until Robert decides otherwise.

**The one scenario** [H; R3 "same day"]: Robert arrives → sees the stalled "file this" rollout → inspects evidence in Operate → asks MC → moves to Build → invites Claude and Codex (Claude joins, Codex stays invited) → shares an attachment for discussion, then files it with "file this" → records an owner decision through proposal → confirm → receipt → returns Monday and sees the decision, owners, receipt and open step. Neutral people only: Robert, Admin, Guest; agents MC, Claude, Codex; identifiers r-4490, r-4491, s-021, s-023, q-91 as in [B4, B5].

**Location and runtime.** `prototype-lab/projects/guild-interaction-prototype/` in the worktree `claude/guild-interaction-prototype`. Flask + Jinja + ES modules, no framework, no bundler, no CDN, no external fonts, no network, no model calls [H §Working prototype boundary; D2]. Documented start on `127.0.0.1:18895`; tests on an ephemeral loopback port.

**Out of scope tonight.** Live MC or any AI call; real invitation delivery; speech capture or recognition; live health probes, OpenTelemetry or a KPI dashboard [OQ §1 "fast follow"]; any write to `data/guild/build_queue.json`, Records, the DB, dev or prod; authentication (the owner guard is a seam only); Chief of Staff; Rooms UI; content for Improve and Experiment; the Agents usage glance [RC 3]; a second scenario [WP]; performing the lift into the portal; commits, PRs, issues.

---

## 2. Pages and routes

Route names follow the existing portal, no trailing slash; a trailing slash is also accepted [D1; Q]. Every route is wrapped by one `require_owner` decorator: a no-op only while `PROTOTYPE=True`, placed exactly where the portal's `_require_owner` goes, and fail-closed otherwise (§8.3) [D1; Codex F5]. Each page shows: the portal bar (`portal_nav_html` stub), the Guild section strip with signal dots, the page, and the MC pill or panel. Under `PROTOTYPE=true` a one-line banner reads *Prototype · sample data · simulated actions* with **Reset fixtures**.

| Route | Page | Existing portal route today |
|---|---|---|
| `/guild` | Arrival: hero, four doors | same route (landing) |
| `/guild/build` | **Shop floor** (arrival: conversation + stoplights, reminders, post-its) [R3-1] | Build Log in the portal today; the prototype owns this route only under its own mount (`/guild-proto` on dev), so the portal's Build Log is untouched (§8.5) |
| `/guild/build/bench` | Build workbench (detail view) | new (see Q1) |
| `/guild/build/queue` | Build Queue, two active columns | same route |
| `/guild/build/items/<id>` | Item detail with history and receipts | new GET; existing POST `/status`, `/edit`, GET `/history` live under it |
| `/guild/operate` | Operate console | same route (different content today) |
| `/guild/improve`, `/guild/experiment` | Quiet destinations | same routes |
| `/guild/proto/reset` (POST, PROTOTYPE only) | Returns default config digest; client clears its keys | none |

**Arrival `/guild`.** Keeps the hero title and tagline [IN "I like the art landing hero"; RC 1]. Four door cards (Build, Operate, Improve, Experiment), each with one signal line and its observation time, and a dot whose state is also written in text (*needs you*, *stalled*, *quiet*, *unknown*) [R3 Arrival]. Build: "2 need you · 3 active in queue", Operate: "1 stalled · 'file this'", Improve and Experiment: "quiet". Below the doors: **Continue where you were → <last area>** (default: Shop floor) [R3 Arrival; R3-1]. Nothing on this page calls a model. Door links: Build → Shop floor, Operate → Operate, Improve, Experiment. The section strip's Build link also goes to the Shop floor.

**Build workbench `/guild/build/bench`.** Heading "Build · workbench", the note *MC fills panels, never moves them* [B4], seven panels (§3), **Reset to MC default**, links to Build Queue, and under PROTOTYPE a **Return Monday · simulated clock** control. Robert can: reorder, fold, focus, reset; follow the queue links; open the conversation about the focused item.

**Build Queue `/guild/build/queue`.** Same language and shape as the real page [Q]: heading "Build Queue", "N active items", two columns *Spec Ready* and *In Build* with counts and "—" when empty; each card shows title (70-char truncation), one-line summary, age (*today* / *Nd*), GitHub issue tag if present, the ten-status select (Idea … Done) and a **Save** button that appears only after a change; plus **Open item →** (new). A card named by `?focus=<id>` is highlighted and scrolled into view. Save goes to the local overlay (§5.6), never to the file. A source badge on the page header says *live* or *sample*.

**Item detail `/guild/build/items/<id>`.** Title, status (effective status = overlay if pending, else source), summary, spec file name (text, not a link off-prototype), GitHub issue, history (source history if any, then overlay entries), **Receipts on this item** (every receipt whose work item is this id), linked discussion session, **Discuss this** (opens the conversation with this item as context), **← Back** (browser back) and **Back to workbench**. Unknown id → 404 page with a link to the queue.

**Operate `/guild/operate`.** Eight tiles, one selected drill-down, next-step proposals, and the reserved experience-to-system health area (§6).

**Improve, Experiment.** One heading and one sentence each: "Not part of this prototype's scenario." No fake content [RC 4 density rule].

**Return later.** Not a separate page: it is the Shop floor or bench after the simulated clock moves to Monday (PROTOTYPE control on the bench, or `?clock=mon` on `/guild/build` or `/guild/build/bench` as a deep link for tests and screenshots). In production the clock is real time and the same cards come from records [B5; Q2].

**Phone (≤ 640 px).** Same routes, different layout (§9). The phone arrival is the Shop floor (§2a) [R3-5]. Other pages keep the rev 2 phone layout: priority summary, four numbers, the thread, *Hold to talk* (simulated) and *Type*, with the page content one tap away [R3 Small screen].

### 2a. Shop floor — `/guild/build` [R3-1…R3-5]

A clean workshop: Robert walks in, sees the lights on the wall and the notes pinned up, and talks to the foreman. The title is "Shop floor · Master Craftsman", with the breadcrumb "Build → Shop floor". It has no decorative imagery.

**1280 layout.** Zones, each a bounded section (§8.1):
- the top bar and one compact honesty line: "Prototype · simulated replies · some sample data", with Reset;
- the **conversation**: one column, docked in the page, full height, composer always visible. It is the same thread, participants, proposals and receipts as the floating panel on other pages. On this page the pill, the panel's position and dock controls, and unread counting are off;
- a **Status** strip above both columns: the five stoplights, each with an Ask button;
- a right **rail** (about 300 px) holding exactly, in order: **Needs you** (reminders), **Continue** (last detail page, plus **Open the bench →**), and **Post-its**.

No bench panels appear on this page, and no arrange instructions. Body text is at least 16 px.

**Design choices** (Robert: "don't take me literally"; Codex input relayed Sep 26). These depart from the literal brief, for the reasons given:

| Choice | Reason |
|---|---|
| Stoplights as a full-width **Status** strip at the top, not the first block of the side rail. | It reads at a glance before the conversation. It has the same form and position on the phone. It frees the rail for what needs Robert. |
| "Reminders" are titled **Needs you**. | This is the Guild's existing term, and the one Codex used. |
| A **Continue** zone was added. | Picking up where you were is what "where to go" asks for (Codex). It holds the last detail page Robert visited and the bench link. |
| Lights are rows with a shape, a word and a reason, not traffic-light graphics. | Status is never shown by colour alone, and the rows are calmer than lamps. |
| Zones are separate surfaces with an edge and a small-caps header, and have no inner boxes. | This reconciles Robert ("section boundaries") with Codex ("fewer boxes"). The conversation is the lightest, strongest surface. |
| The Agents and Spend rules need numbers the fixture only had as text. | `operate.sample.json` gained `running` / `needs_robert` / `failed` and `mtd` / `budget` (sample values unchanged). No adapter or wiring changed. |

**Stoplights.** The set and order come from `config/layout.json → floor.lights`. Each light shows:
- the area name;
- a state word: *OK* / *Watch* / *Problem* / *Unknown*;
- a shape: ● circle / ▲ triangle / ■ square / dashed ring with "?";
- a one-line reason;
- a compact source mark (*live* / *sample* / *not instrumented*).

The light's name links to its detail (Build Queue or Operate with that tile selected). An **Ask** button sends "Tell me about <area>" to the conversation, which answers with a scripted, labelled reply. Rules are implemented in `guild_ui/lights.py`. Precedence is unknown > red > yellow > green; any stale or failed source is unknown.

| Light | Source | Green (OK) | Yellow (Watch) | Red (Problem) | Grey (Unknown) |
|---|---|---|---|---|---|
| Build queue | queue adapter (live or sample) | read ok, 0 blocked, 0 unknown-status rows | ≥ 1 unknown-status row | ≥ 1 blocked item | read failed or invalid |
| Rollouts ("file this") | rollout counts per simulated clock (sample) | access gap 0 and reached = enabled | access gap > 0 or reached < enabled | enabled > 0 and active = 0 | no rollout data |
| Systems | Systems tile | — | — | — | not instrumented (no probe yet) |
| Agents | Agent work tile (sample): running, needs Robert, failed | needs Robert 0 and failed 0 | needs Robert ≥ 1 | failed ≥ 1 | tile missing or stale |
| Spend | Build model spend tile (sample): month to date vs budget | < 80 % of budget | 80–100 % | > 100 % | budget missing or stale |

Saturday sample state: Build queue green · Rollouts yellow (access gap 1) · Systems unknown · Agents yellow (1 needs you) · Spend green (71 %). After **Return Monday**, Rollouts turns green (access gap 0, all enabled reached). The first reminder becomes the recorded decision (r-4490) when one exists.

**Needs you (reminders).** At most 3, from `scenario.json → reminders`, in order after the show-when conditions are applied. Each row links to its detail (Build Queue item, bench decision card, or bench). On the phone, the first one shows as the single urgent note. **Continue**: the last non-floor page visited (default: the bench). **Post-its**: at most 4 short notes. The caps are in `layout.json → floor`.


**Links between pages.** Needs-you row "Decide 'file this'" → Build Queue with `?focus=<work item>`; focus panel → item detail; both labelled "in Build Queue" so the path is unmistakable [H]. Operate "file this" tile ↔ bench focus panel ("Open on the bench" / "See Operate evidence"). Item → Back → bench restores arrangement, focus and conversation (§3.3, §4.7).

---

## 3. The workbench

### 3.1 Panels (set, order and default fold come from `config/layout.json`)

| Panel id | Title | Content | Source |
|---|---|---|---|
| `subject` | ☆ In focus · Capabilities → "file this" | Rollout card (four counts, adoption line), field evidence (four marks), recent change, links to Operate and to item #158 in Build Queue, receipts on #158, decision card once recorded | sample (Operate fixture) + live work-item link |
| `needs` | Needs you | Rows: *Decide* "file this" — grant, fix or training? (→ Build Queue); *Approve* PR #212 — Codex review passed · r-4471; after the decision: *Approve* Idea q-91 → spec · r-4490 | sample (scenario, MC-curated) |
| `motion` | In motion | Hill chart *figuring out / executing*: Spec Ready items on the uphill side, In Build on the downhill side; labels are text | live (queue statuses; position derived from status, said in the badge) |
| `discussions` | Discussions | s-023 "'file this' rollout — Robert, MC, Claude" once the conversation has participants; s-021 "Operate matrix density — Admin, Codex" | Sessions adapter: live when `GUILD_RECORDS_DB` is set and readable; configured but unreadable → unknown; not configured → this panel's explicit opt-in to sample (`layout.json` `"when_not_configured": "sample"`), badged "sample · no Records source configured" (§7.1) |
| `since` | Since you were here · <last visit> | Commits on the worktree since the last visit (title, short hash, time) | live (local `git log`) |
| `blocked` | Blocked | Queue items with status `blocked`; scenario blocker "Rooms grant for Guest not issued · owner Robert" until Monday | live queue + sample scenario |
| `postits` | Post-its | Four short notes | sample |

Default (MC default): order as listed; `subject` focused; `blocked` and `postits` folded [B4 "Folded by you · Blocked (1) · Post-its (4)"].

### 3.2 Robert arranges, MC fills [RC 2; B4]

- **Reorder**: drag by the ⠿ handle (pointer), or **Move up / Move down** buttons (keyboard). Focus stays on the moved panel's button.
- **Fold / unfold**: a button with `aria-expanded`; a folded panel collapses to one line in the *Folded by you* row with its item count.
- **Focus (☆)**: one panel at a time, rendered first and full width, `aria-pressed=true`. Focusing another panel un-focuses the previous one; its place in the order is kept.
- **Reset to MC default**: restores `layout.json` defaults for order, folds and focus; does not touch the conversation, receipts or clock.
- MC (the scenario engine now, the agent later) only changes panel *contents*. No code path in MC moves, folds or focuses a panel.

### 3.3 Persistence

Arrangement is stored per browser under `guild.bench.v1` = `{layout_version, order[], folded[], focus}`. It survives navigation, Back and reload. On load it is validated: unknown panel ids are dropped, panels new in `layout.json` are inserted at their default position, a `layout_version` change triggers that merge and a one-line notice. Invalid JSON or wrong types → MC default plus the notice "Saved arrangement was unreadable; MC default loaded." All storage access is wrapped in try/catch; if storage is unavailable, state lives in memory and the banner says it will not survive reload.

### 3.4 Density rule [RC 4; IN]

A panel whose visible rows are zero folds itself and shows one line: "<Title> · nothing to show — <reason>", with the reason from `layout.json` (e.g. Blocked on Monday: "grant issued r-4491; no blocked queue items"). This is drawn differently from *folded by you* and is not stored as Robert's choice. Unfolding an empty panel shows only the reason line. No placeholder rows, no padding.

---

## 4. The floating conversation

### 4.1 Modes [B1–B5]

`pill` → `floating` → `docked`, persisted. **Pill**: bottom-right, "Master Craftsman · floating", plus "N unread" when > 0. **Floating**: 420 px panel over the page, max height 70 vh, dragged by its header (pointer); keyboard **Position** presets *Right · Left · Bottom-left*; position clamped to the viewport and persisted. **Docked**: a 420 px column on the right; page content reflows beside it. Controls in the header: **Minimize**, **Dock / Undock**. Escape minimizes. Opening moves focus to the composer; minimizing returns focus to the pill.

### 4.2 Header and context line [R3 Conversation]

"Context: <area> · <selected item> · recording on · say 'off the record'". Area and item come from the page's `data-context-area` and `data-context-item`: Arrival → "Guild · nothing selected"; Operate → selected tile (default Capabilities → "file this"); Bench → the focused panel's subject; Queue → "Build Queue"; Item → "#<id> <title>". Each message stores the context it was sent from and shows it as provenance ("from Operate · 'file this'").

### 4.3 Participants [R3 Invitation; B4]

Chips with text, never color alone: *Robert · owner*, *Master Craftsman*, *Claude · joined 9:21* (solid border), *Codex · invited 9:21 · not joined* (dashed border, italic). State per agent: `none → invited → joined`. Codex never becomes joined in this scenario. A non-join is never shown as a join. On Monday the decision card lists "Codex never joined".

### 4.4 Recording [R3 wording 1]

On-record turns are recorded automatically into the local thread (the thinking log) with no per-turn approval. **Off the record** (button or typed/spoken "off the record") starts a visibly marked segment: dashed divider "Off the record from 9:19 — not recorded", dashed message style. Off-record turns are kept in memory only (not persisted, excluded from the recorded log, gone on reload, with a note saying so). "Back on the record" ends the segment. While off the record [Codex F2; decision Q6]: **Attach**, **Invite** and every consequential action (file this, decide, post to Needs you, Operate next steps, confirming a pending proposal, queue Save) are disabled, each showing the reason "Off the record — go back on the record to attach, invite or act." Typed commands for them get the same refusal as an off-record, unpersisted MC line. Nothing from an off-record segment reaches storage: no message text, no attachment metadata, no participant change, no proposal, no receipt. A pending proposal created before going off record stays pending (persisted) but cannot be confirmed until back on record.

### 4.5 Composer

Text input (Enter sends), **Attach** (native file picker; plus two sample chips "admin-s021-note.txt", "u02-check-output.txt" under PROTOTYPE; disabled off the record), **Voice** labelled "Simulated voice · not speech recognition" (§4.6), **Invite** control (disabled off the record), and a hint line with the spoken commands *confirm · off the record · file this*. Picked files never leave the browser: only name, size and a SHA-256 computed locally are kept.

### 4.6 Scripted replies [H; D2]

MC and Claude replies come from `config/scenario.json`: an ordered list of turns, each `{id, match (keywords/regex), contexts, requires (state predicates), actions[]}`. Every scripted message carries the label **Simulated reply**. First matching turn wins:

| Turn | Trigger | Result |
|---|---|---|
| T1 ask | "why … stall/usage", "tell me about this" | MC answer separating verified from not verified [B3] + proposal *Post to Needs you: approve "attach on mobile" as a queue Idea* |
| T2 invite | "bring Claude and Codex in", "invite", Invite control (on record only) | Simulated direct owner command (decision Q3). MC: "Invited Claude and Codex with this discussion's context only. I'll show each here when they join, not before." After 1.5 s (simulated): Claude → joined, Claude's scripted reply [B4]. Codex stays invited |
| T3 decide | "agreed", "grant guest", "decide" | Owner-decision proposal (§5.4) |
| T4 file | "file this" | Filing proposal for the latest unfiled attachment; if none: "Nothing is attached to file. Attach it, then say 'file this'." |
| T5 control | "confirm", "edit", "cancel" | Acts on the most recent pending proposal |
| T6 record | "off the record", "back on the record" | §4.4 |
| T7 recall | "what did we decide about …" | Links and quotes the decision card; if none: "No owner decision recorded on 'file this' yet." |
| T8 status | "what's in build" | Lists the queue's active items from the adapter, with its source badge |
| T0 fallback | anything else | "Simulated reply · this prototype only has scripted turns. Try: …" (the hints) |
| Monday | clock → Monday | MC morning message [C6]: "Grant done, Guest reached 'file this' Sunday, nothing filed yet. The mobile fix is an Idea waiting for you." |

**Simulated voice** inserts the next scripted utterance for the current state (pending proposal → "confirm"; unfiled attachment → "file this"; otherwise T1 → T2 → T3 lines; Monday → "What did we decide about file this?"), shown as "Robert · voice (simulated)". The page never requests microphone access.

### 4.7 Continuity and unread

One thread (session s-023) across all pages, stored under `guild.conversation.v1` with participants, attachments, proposals, mode, position and unread. MC/agent messages arriving while minimized increment unread; opening clears it. Replies and receipts are announced through `aria-live="polite"`.

---

## 5. Proposal, confirmation and receipt

### 5.1 State machine [R3 wording 1; R3 Proposal/Receipt]

`proposed → confirmed → receipt issued` · `proposed → editing → proposed (revised, payload diff shown)` · `proposed → cancelled` (terminal, no receipt). A confirmed proposal cannot be confirmed twice. A proposal card shows: kind, the exact payload as field/value lines, **Confirm**, **Edit**, **Cancel**, and "Nothing happens until you confirm." Receipts: `{id, kind, summary, at (simulated clock), work_item, session, proposal_id, "local receipt · simulated"}`.

### 5.2 Consequential actions (always a proposal)

Filing an attachment; recording an owner decision; posting an item to Needs you; each Operate next-step (grant, product fix, training, investigate); queue status change (§5.6). Not consequential: talking on the record, attaching for discussion, folding or moving panels, opening pages. Invitation follows [H]/[B4] as a **simulated** direct owner command (decision Q3); a production invite that grants real agent access requires a scoped proposal → confirm → receipt. All consequential actions, attach and invite are disabled off the record (§4.4).

### 5.3 Attach for discussion vs "file this" [R3 wording 2; IN "attach it and say file this"]

Attach alone → thread item "📎 name · size · shared for discussion · not filed". Nothing else happens: no receipt, no item change. "file this" → proposal with payload: *File* `admin-s021-note.txt` (2.1 KB) → *Records session* s-023 "'file this' rollout" · *work item* #158 · *id* `sha256:3f9a…c21e` · *stays in this browser; not uploaded*. **Edit** lets Robert change file name and destination session (s-023 / s-021). **Confirm** → receipt in the thread; the attachment chip, the receipt line on item #158's page and the one in the bench focus panel all read **`simulated filing · local receipt r-xxxx · file not uploaded`** [Codex F1]. The word "filed" never appears alone. **Cancel** → attachment stays "shared for discussion · not filed", no receipt anywhere.

**What survives reload.** Attachment metadata only: name, size, SHA-256 digest (computed in the browser), origin (picked / sample chip), state (`discussion` / `simulated-filing`) and the receipt id. The file bytes and the browser `File` object are never stored and are gone on reload; after reload the chip adds "file content not kept". Tested in both the confirmed state and after reload.

### 5.4 Owner decision [B4, B5]

Payload: *Record as owner decision:* "grant Guest → mobile fix (Idea) → coach Admin" · *owners* Robert (grant, today), MC (follow-up Mon) · *session* s-023. Confirm → receipt **r-4490** (reserved by the scenario), decision card in the focus panel: decision text, receipt, session, owners, steps with *done/open* written as text. Needs you swaps its first row for "Approve Idea q-91 'attach on mobile' → spec · r-4490". An agent's reply never counts as an owner decision.

### 5.5 Receipt numbering

Local receipts are numbered from r-4486 upward, skipping the scenario's reserved r-4490 (decision) and r-4491 (grant, "issued Sat 9:27, simulated between sessions"). Numbering restarts on Reset fixtures.

### 5.6 Queue status change — overlay, pending real write [D1]

Save on a queue card or item page creates a proposal-equivalent entry in the local overlay store `guild.overlay.v1`: `{item_id, from, to, note, receipt, state: "pending real write"}`. The owner's Save is the confirmation (it is the same direct action as today's queue, Q4). The card and item show the effective status with a chip "pending real write · r-xxxx" and a history row "Robert · spec_ready → in_build · local only". `data/guild/build_queue.json` is never opened for writing; a server test proves it. Cards whose effective status leaves Spec Ready/In Build drop off the board with a notice linking the item.

---

## 6. Operate [B2, B3; OQ §1]

**Header**: "Operate · current as of <time> · no model call" plus a legend of the source badges.

**Tiles** (set, order and labels in `config/layout.json`; each shows value, one-line sub-signal, observation time, source badge):

| Tile | Sample value | Source now | Later |
|---|---|---|---|
| Capabilities (selected) | 5 / 3 · 1 stalled "file this" | sample | Records/Rooms usage + grants |
| Sites | 2 · laptop offline since Thu | sample | host inventory |
| Systems | 7 · 1 warning · disk | not instrumented | Operations agent `:8768/status` |
| Disk | 18% free · recurring alert | sample | host check |
| Tickets | 5 open · measured 2 h ago | sample, **stale** | ticket source |
| Build queue | 3 active · 0 blocked | live (queue adapter) | same |
| Agent work | 2 running · 1 needs Robert | sample | work/attempt store |
| Build model spend | $42.80 MTD · +$9.10 wk | sample | model gateway cost checkpoints (location to verify) |

**Stale / unknown** [R3 Stale/unknown]: dashed border, grey number, italic text "treat as unknown" and the reason ("measured 2 h ago; freshness limit 30 min" or "live read failed"). Never green, never zero. A failed live read renders *unknown*, not the sample value and not 0.

**Selection**: one selected tile per page; clicking or Enter selects it and sets the conversation context. Only Capabilities has a drill-down in this scenario; others show "No drill-down in this prototype."

**Rollout drill-down** [R3 wording 3]: '"File this" · rollout · stuck'; four counts **Intended 3** (Robert, Admin, Guest) · **Enabled 2** (Guest: no Rooms grant) · **Reached 2** (both opened it) · **Active 1** (Robert · 6× this week); line "Adoption 1 of 2 enabled · access gap 1 · observed Sat 9:15". Monday values: 3 · 3 · 3 · 1, "adoption 1 of 3 enabled · access gap 0". Guest is outside the adoption denominator while not enabled.

**Field evidence** [R3 wording 4]: four marks with text labels: *reported* — Admin, s-021: "attach isn't on the phone, only the form."; *verified* — attach control hidden below 360 px · U02 check; *unknown* — whether Guest wants it at all; *blocker* — Rooms grant for Guest not issued · owner Robert. Plus "Recent change: PR #212 merged to dev Sat 08:50 · Spec 158 §8".

**Next step** buttons: *Training · show Admin*, *Config · grant Guest*, *Product · attach on mobile*, *Investigate* — "each is a proposal you confirm". Each opens the conversation with the matching MC proposal (§5).

**Experience-to-system health (reserved area)** [OQ §1; H]: a compact table titled "Journey health · sample layout, not instrumented". Columns: *Journey* · *User impact* · *Component / agent risk* · *Observed* · *Evidence path*. Rows: "Sign in and open Guild", "Ask Master Craftsman", "Save and retrieve a record". Every value carries a **sample** or **not instrumented** tag; user impact and component risk are separate columns so a component risk never turns the journey red by itself; unknown/stale cells use the stale style. Evidence paths name real files or plans (e.g. `scripts/tools/health_check.py` "checks three localhost URLs; not an end-to-end probe"). No green state exists in this area.

---

## 7. Data architecture [D1]

### 7.1 Adapter contract (Python, `guild_ui/adapters/`)

Every read returns a `SourceResult`:

```
SourceResult = {
  "source":      "live" | "sample" | "not_instrumented",
  "status":      "ok" | "stale" | "unknown",
  "reason":      null | "not_configured" | "read_failed" | "invalid",
  "observed_at": ISO-8601 | null,
  "fresh_for_s": int | null,        # older than this → status "stale"
  "evidence":    str,               # file path, command or table the value came from
  "error":       str | null,        # short; never secrets or full paths outside the repo
  "data":        <payload>
}
```

| Adapter | Methods | Fixture impl | Live impl (read-only) |
|---|---|---|---|
| `BuildQueue` | `list_items(statuses)`, `get_item(id)`, `history(id)` | `fixtures/queue.sample.json` | `data/guild/build_queue.json`, opened read-only; `history` → `unknown` (DB-backed today) |
| `Activity` | `recent_commits(since, limit)`, `branches()` | `fixtures/activity.sample.json` | `git log` / `git branch --list` in the worktree via subprocess, timeout 2 s, `GIT_OPTIONAL_LOCKS=0`, no fetch, no `gh` |
| `Sessions` | `list_sessions(limit)`, `get_session(id)` | `fixtures/sessions.sample.json` | Records SQLite named by `GUILD_RECORDS_DB`, opened `file:…?mode=ro`, reading `rooms` (sessions) directly — never through `Store()`, whose constructor migrates the schema |
| `Operate` | `tiles()`, `rollout(subject, clock)`, `evidence(subject)`, `journeys()` | `fixtures/operate.sample.json` | none yet → Build queue tile uses `BuildQueue`; Systems returns `not_instrumented` |

Selection: `--sources live|sample` (default `live`; tests and walkthrough screenshots use `sample` for determinism) [decision Q7].

**Two distinct non-ok cases** [Codex F3]:

| Case | Example | Result | On screen |
|---|---|---|---|
| **Not configured** | `GUILD_RECORDS_DB` unset; a source with no live implementation (Systems) | `source: not_instrumented, status: unknown, reason: not_configured` — unless that panel/tile explicitly opts into sample in `layout.json` (`"when_not_configured": "sample"`), which yields `source: sample` | "not instrumented" badge, dashed/grey, or "sample · no source configured" for an opted-in panel |
| **Configured but unreadable** | queue file missing, unreadable, not JSON, wrong shape; SQLite path set but cannot open read-only or lacks `rooms` | `source: live, status: unknown, reason: read_failed \| invalid`, `error` names the file and the failure, `data: null` | "live · unknown — treat as unknown" with the error; never the sample value, never 0 |

Live mode never silently falls back to sample. Operate sources that have no live implementation are sample by explicit per-tile configuration, and are badged so. The queue adapter does **not** call the portal's `_load_build_queue()` (which returns `[]` on any error); it reads the file itself, requires a JSON list of objects each with an integer `id` and a `status` in the ten `_BUILD_QUEUE_STATUSES`, and on any failure returns unknown with error provenance. A valid empty list is a real zero (`status: ok, data: []`).

### 7.2 Things with no backend (simulated, labelled)

MC and Claude replies, invitations, the decision, receipts, the grant, KPI/health values, voice, the Monday clock. They live in `config/scenario.json` and `fixtures/operate.sample.json` and are labelled *simulated* or *sample* on screen.

### 7.3 Browser stores (prototype)

`guild.bench.v1`, `guild.conversation.v1`, `guild.overlay.v1` (pending writes + receipts), `guild.clock.v1`, `guild.lastvisit.v1`. Attachments are stored as metadata only (§5.3); off-record segments are never written (§4.4). `static/guild-ui/js/proposals.js` implements `propose / edit / confirm / cancel / listFor(workItem)` against localStorage behind the same interface a server proposal store will offer; at lift it is swapped for `fetch` calls. Malformed values → defaults + notice; Reset fixtures clears all five keys.

### 7.4 Source badges

Every panel, tile, queue page and item shows one badge: **live · 9:12**, **sample**, or **not instrumented**, plus **stale** / **unknown** when applicable. Mixed panels (e.g. Blocked) badge each row.

### 7.5 INTEGRATION_MAP.md (delivered with the build)

One row per panel, tile and action: current adapter; real backend it should connect to (route, file, table or service, e.g. `_load_build_queue`, `POST /guild/build/items/<id>/status`, `guild.design_log_transitions`, Records `rooms`/`events`/`documents`, Operations agent `:8768/status`, the model gateway cost checkpoints, the future MC OpenClaw agent); what is missing to make it live; the receipted write path (proposal → confirm → platform operation → receipt). Plus the lift-to-portal steps (§8.4).

---

## 8. Look and flow separation [D2]

### 8.1 Look — one place

**Rev 3 [R3-3]:**
- **Page surface.** The page surface is deeper and warmer.
- **Zones.** Each zone has its own surface tone, a visible edge and a small-caps header, with consistent gutters. The conversation is the lightest, highest-contrast reading surface.
- **Inside a zone.** Items are simple rows or notes: no nested cards, no badge on every line.
- **Contrast.** Body and muted text meet WCAG AA (≥ 4.5:1) on every zone surface; checked by a test.
- **Site-wide.** The same tokens apply to the bench and Operate, so their panels read as distinct sections. Their layouts are not restyled beyond what tokens do.

`static/guild-ui/tokens.css`: every colour, font family, size, spacing step, radius, border, chip and badge style as custom properties, starting from the Guild palette (`--bg #f5f0e8`, `--surface #faf7f2`, `--surface2 #f0ebe0`, `--border #ddd6c8`, `--text #2a2418`, `--text-muted #6b5f4e`, `--accent #8b5e2a`, nav `#2a1f14` / `#C9AB85` / `#F5EDE0` / `#D49A6A`) and the evidence-mark colours of [B2]. `--text-dim #9e9080` is decoration only (2.7:1 on the background; muted text #6b5f4e is 5.5:1). `components.css` holds component rules and uses tokens only. No inline styles in templates or JS; the single documented exception is the floating panel's geometry, set as `--mc-x/--mc-y` through CSSOM. Font families name Source Sans 3, DM Mono and Playfair Display with system fallbacks; the prototype loads no web fonts (no network); the portal's existing font link applies after the lift. Swapping a theme = editing `tokens.css`.

### 8.2 Flow — one place

`config/layout.json`: sections and door signals, bench panel set, default order, default folds, default focus, empty-panel reasons, Operate tile set and order, phone four numbers. `config/scenario.json` (`"simulated": true`): clock steps, participants, scripted turns (§4.6), proposal templates, reserved receipts, voice script, Monday timeline and decision card. Reordering panels, renaming a tile or changing the walkthrough is a data edit. Views read `data-*` attributes and the config; they contain no scenario text.

### 8.3 Templates

`templates/guild/`: `_guild_base.html`, `_section_subnav.html` (portal markup plus dots), `_mc_conversation.html` (panel plus `<template>` elements for each message kind), `_source_badge.html` macros, `_proto_banner.html`, and one page template per route. Templates call `portal_nav_html(user, '/guild')` exactly as the portal does; the prototype registers a stub of that global. Behaviour: ES modules in `static/guild-ui/js/` (`state.js`, `bench.js`, `conversation.js`, `scenario.js`, `proposals.js`, `operate.js`, `queue.js`, `dom.js`), bound through `data-*` attributes and a JSON config block (`<script type="application/json">`). A strict CSP (`default-src 'self'`) is sent.

`PROTOTYPE=true` (env `GUILD_PROTOTYPE`, default on in `app.py`) gates the banner, Reset fixtures, Return Monday, sample attachment chips, the simulated voice button and scripted replies. With it off, the composer shows "Master Craftsman is not connected" instead of scripted replies, and voice is hidden. Hiding prototype controls is not access control.

**Fail-closed binding** [Codex F5]. `register_guild_ui(app, prototype, owner_guard=None, write_services=None)`: when `prototype` is False, registration raises `GuildBindingError` unless `owner_guard` is a real guard (not the prototype no-op) and `write_services` provides the server proposal/receipt store. No production-mode route can serve through the no-op guard. The local prototype adds no authentication.

### 8.4 Lift to portal (summarized; full steps in INTEGRATION_MAP.md)

The code is a Flask blueprint `guild_ui`. Templates and assets are intended for reuse; production binding is a separate reviewed change. Lift = copy `templates/guild/*` and `static/guild-ui/`; register the blueprint in `minimoi_portal/app.py`; bind `require_owner` to `_require_owner`; set `PROTOTYPE=False`; choose live adapters; resolve route overlaps (`/guild`, `/guild/build`, `/guild/build/queue`, `/guild/operate`) by replacing the old view functions one at a time behind review; replace the browser proposal store with server writes to the existing queue endpoints plus receipts. Each step is a separate reviewed change.

---

### 8.5 Dev mount (dev.minimoi.ai → Mac portal on `localhost:5001`) [R3-6]

`register_guild_ui(app, *, prototype, owner_guard=None, write_services=None, url_prefix="", current_user=None, sources="live", …)`.

- **Prefix.** With `url_prefix` (dev: `/guild-proto`), every route and the blueprint's asset path (`…/guild/ui-assets/`) live under the prefix. The host's `/guild…` routes and `/static` are never shadowed.
- **Owner guard.** A passed `owner_guard` (a decorator shaped like the portal's `_require_owner`) is always used, also with `prototype=True`, on pages, the reset endpoint and assets. The no-op guard applies only when `prototype=True` and no guard is passed. `prototype=False` fails closed as in §8.3.
- **Portal nav.** The `portal_nav_html` stub is registered only if the host has none. `current_user` supplies the signed-in user to the real portal bar.
- **Headers.** CSP and `no-store` are set only on the blueprint's own responses.
- **URLs.** Templates use `url_for`; JS receives its base path and URLs from the page's JSON block.
- **Storage.** localStorage keys are namespaced per mount (`guild.guild-proto.*`).
- **Standalone.** The standalone runner (127.0.0.1:18895, no prefix, loopback guard) is unchanged.

## 9. Accessibility and responsive rules

- Widths: 1280 px (primary desktop), 390 px and 360 px phones; no horizontal page scroll at 390 or 360.
- Every control reachable by keyboard in DOM order; visible focus ring (3 px, token colour); no keyboard traps; Escape minimizes the conversation.
- Status never by colour alone: every chip, dot, badge and evidence mark carries text.
- `aria-live="polite"` on the thread and on a receipt announcer; `aria-expanded` on folds; `aria-pressed` on focus; proposals are regions with a heading.
- Touch targets ≥ 24 px everywhere, ≥ 44 px for phone primary actions (*Hold to talk*, *Type*, *Confirm*).
- `prefers-reduced-motion`: no transitions or smooth scroll; the simulated join delay stays (it is not motion).
- Phone layout (≤ 640 px) [C6, B6; R3 Small screen]: compact bar; one-line banner; **priority summary** ("Needs you · 2" plus its first row) and **four numbers** (Capabilities 1 stalled · Disk 18% · Agent work 2, 1 needs you · Spend MTD $42.80), all inside the first 390×844 viewport; then the thread; a bottom composer with **Hold to talk · simulated**, **Type**, **Attach**, and hints *say "confirm" · "off the record" · "file this"*. The floating panel becomes the main column; the bench and other pages are reachable, not required.
- **Phone Shop floor** [R3-5], at 390 and 360 px:
  - **Top.** A compact stoplight strip: five items, each with shape and word. Tapping it expands the full lights with reasons, detail links and Ask.
  - **Urgent note.** One line below the strip: the first Needs-you item.
  - **Middle.** The conversation fills the screen.
  - **Bottom.** The composer bar with *Hold to talk · simulated* and *Type* is pinned.
  - **Sheet.** Needs you, Continue and Post-its sit behind one chip, "Needs you N · Post-its M", which opens a sheet that also holds "Open the bench →".
  - **First viewport.** The strip, the start of the conversation and the pinned composer are all visible; there is no horizontal scroll and no bench on arrival.

---

## 10. Acceptance cases and tests

Server tests `tests/test_app.py` (pytest, Flask test client); browser tests `tests/browser_checks.py` (pytest + Playwright, installed Chrome, headless, `--sources sample` unless noted). Screenshots at 1280 (`step1-arrive-1280.png` … `step8-…`) and 390 into `_working/guild-prototype-evidence/`.

**Walkthrough [H §Acceptance] → tests**

| Step | Test(s) |
|---|---|
| 1 Arrive, four doors, dated signals, enter the **Shop floor** (conversation, stoplights, reminders, post-its), open the bench and find the focus | `test_arrival_doors_signals_and_strip_dots`, `test_shop_floor_zones_lights_reminders_postits`, `test_shop_floor_zones_are_bounded_sections`, `test_light_opens_detail_and_ask_inserts_prompt` |
| 2 From the Shop floor, open the bench; move/fold/focus persists; reset restores default | `test_bench_arrangement_persists_across_navigation_and_reload`, `test_bench_reset_to_mc_default`, `test_empty_panel_autofolds_with_reason` |
| 3 Operate evidence marks; health area sample / not instrumented | `test_operate_drilldown_counts_and_evidence_marks`, `test_health_area_has_no_healthy_claim`, `test_stale_tile_treat_as_unknown` |
| 4 Ask MC, move to Build, keep thread, participants, context | `test_conversation_continuity_operate_to_build` |
| 5 Queue link → item → Back restores bench | `test_queue_round_trip_restores_arrangement_focus_conversation` |
| 6 Attach for discussion; file this → proposal → Confirm → receipts | `test_attach_for_discussion_creates_no_receipt`, `test_file_this_confirm_receipt_in_thread_and_item`, `test_file_this_cancel_and_edit` |
| 7 Invite; Claude joined, Codex invited; Monday card | `test_invite_claude_joins_codex_stays_invited`, `test_owner_decision_receipt_and_monday_return`, `test_what_did_we_decide_resolves` |
| 8 Phone path from the Shop floor (strip, conversation, pinned composer, reminders sheet); voice labelled simulated | `test_phone_shop_floor_first_viewport` (390 and 360), `test_phone_layout_390_and_360`, `test_phone_essential_path_text_and_simulated_voice`, `test_keyboard_only_open_and_confirm` |

**User-facing states [R3] → tests (one to one)**

| State | Test |
|---|---|
| Arrival | `test_arrival_doors_signals_and_strip_dots` (also asserts "continue where you were" resolves to the last area and no request leaves the origin) |
| Conversation | `test_conversation_continuity_operate_to_build`, `test_off_the_record_segment_not_persisted`, `test_minimize_dock_keep_participants_and_unread` |
| Drill-down | `test_operate_drilldown_counts_and_evidence_marks` (one selected subject; source, time, owner shown) |
| Invitation | `test_invite_claude_joins_codex_stays_invited` |
| Proposal / confirmation | `test_file_this_cancel_and_edit`, `test_phone_essential_path_text_and_simulated_voice` (typed/spoken "confirm") |
| Receipt | `test_file_this_confirm_receipt_in_thread_and_item`, `test_queue_status_overlay_pending_real_write` |
| Later retrieval | `test_owner_decision_receipt_and_monday_return`, `test_what_did_we_decide_resolves` |
| Stale / unknown | `test_stale_tile_treat_as_unknown`, `test_live_read_failure_shows_unknown_not_zero` |
| Small screen | `test_phone_layout_390_and_360` |

**Robustness and boundary tests**: `test_no_external_network_requests`, `test_malformed_local_state_falls_back_to_defaults`, `test_no_microphone_request`, `test_simulated_filing_labels_confirmed_and_after_reload` (F1), `test_off_record_disables_attach_invite_actions_and_persists_nothing` (F2: reload after off-record attach/invite attempts; localStorage has no off-record text, attachment metadata, participant change or receipt). Server: `test_routes_render`, `test_unknown_item_404`, `test_fixtures_marked_sample`, `test_scenario_marked_simulated`, `test_reset_endpoint_returns_defaults_and_writes_nothing`, `test_no_route_writes_outside_prototype_dir` (hashes `data/guild/build_queue.json` and the prototype tree before/after exercising every route), `test_live_queue_adapter_reads_real_shape_read_only`, `test_queue_adapter_missing_or_invalid_file_is_unknown_not_zero` (F3), `test_queue_adapter_valid_empty_list_is_zero`, `test_git_adapter_local_only`, `test_sessions_not_configured_vs_unreadable` (F3), `test_sessions_adapter_reads_readonly_sqlite`, `test_production_mode_fails_closed_without_owner_guard` (F5), `test_production_mode_guard_applies_to_every_route` (F5, with a denying test guard), `test_prototype_flag_off_hides_prototype_controls`, `test_loopback_host_guard_and_csp`.

**Rev 3 tests** [R3-1…R3-6].
- **Server** (`test_app.py`):
  - `test_light_rules` (each rule, including precedence; unknown ≠ green; read-failed queue → unknown; stale → unknown);
  - `test_shop_floor_route_and_lights_render` (text + shape per light; no bench panels);
  - `test_shop_floor_postits_capped`;
  - `test_shop_floor_read_failed_queue_light_unknown`;
  - `test_mounted_under_prefix_on_host_app` (extended to `/guild-proto/guild/build`).
- **Browser** (`browser_checks.py`):
  - `test_shop_floor_zones_lights_reminders_postits` (≤ 3 reminders, ≤ 4 post-its, no `[data-panel]`, conversation docked, pill hidden);
  - `test_shop_floor_zones_are_bounded_sections` (each zone's background differs from the page, and it has a visible header);
  - `test_lights_not_colour_alone`;
  - `test_light_opens_detail_and_ask_inserts_prompt`;
  - `test_shop_floor_monday_changes_rollouts_light_and_first_reminder`;
  - `test_shop_floor_same_thread_as_floating_panel`;
  - `test_phone_shop_floor_first_viewport` (390 and 360: strip, conversation and pinned composer inside the first viewport; no horizontal scroll; sheet opens);
  - `test_text_contrast_aa_on_zones`.

Rev 2 tests whose assumptions change (the Build door and strip now lead to the Shop floor) are updated in place.

**Engineering-quality deliverable** [H §Handoff artifacts; WP; Codex F4]. `FINDINGS.md` in the prototype directory: one entry per code or GitHub quality concern encountered during this bounded work, each with ID, path/ref (and revision), observation, evidence, impact, severity, proposed disposition and verification status (`unverified` unless reproduced; `reproduced` with the command). "None found in this bounded review" is a valid result. No GitHub issues are opened; the wider read-only audit remains the fast follow in [OQ §2].

**Other deliverables**: `README.md` (start, test, reset, how to change the look/flow), `INTEGRATION_MAP.md`, screenshots, and the handback.

---

## 11. Real vs simulated, risks, open questions

**Real (working code):** navigation between pages; panel reorder/fold/focus/reset and persistence; floating/docked/minimized panel with drag and presets; thread persistence and unread; attachment picking with local SHA-256; the proposal state machine and local receipts; queue overlay; the Monday view switch; phone layout; live read-only adapters for the build queue, local git activity and (if configured) a Records SQLite; source badges; stale/unknown rendering.

**Simulated (labelled on screen):** every MC and Claude reply; invitations and joins; the owner decision's effect and the grant; receipts (local only, "simulated"); pending real writes; the Monday clock; voice (inserts a scripted line; no microphone); all Operate values except the Build queue tile; all journey-health values.

**Risks.** (1) Live and sample data on one screen can mislead; mitigated by mandatory badges and the rule that failure shows unknown. (2) The live queue can disagree with the scenario (today #158 is *spec_ready* in the real file); the scenario binds to work item #158 and, if it is absent, the link is hidden with "#158 not found in live queue · treat as unknown". (3) Browser-side proposal/receipt state is not the future server store; the JS interface is the seam. (4) Prototype fonts differ from the portal's web fonts. (5) The floating-panel geometry exception to "no inline styles". (6) Template reuse assumes the portal accepts a blueprint and a `_guild_base.html`; the portal pages today inline their CSS.

**Owner decisions — ACCEPTED** (Robert, Sep 26, per Codex's recommendation table)

| # | Decision |
|---|---|
| Q1 Route | ACCEPTED: workbench at `/guild/build/bench`; the existing `/guild/build` Build Log stays (the prototype redirects `/guild/build` to the bench only because it has no Build Log). |
| Q2 Return | ACCEPTED: four-door arrival at `/guild` with "continue where you were". |
| Q3 Invite | ACCEPTED: direct **simulated** owner command in the prototype; production real agent access needs scoped proposal → confirm → receipt; disabled off the record. |
| Q4 Queue Save | ACCEPTED: owner Save is the confirmation, with a local "pending real write" receipt; MC-initiated changes remain proposals. |
| Q5 Operate conversation | ACCEPTED: floating by default, dockable on request. |
| Q6 Off record | ACCEPTED: refuse proposals, and extend the refusal to attach and invite (§4.4). |
| Q7 Sources | ACCEPTED: live by default locally; sample for deterministic tests and screenshots, plus one live screenshot; missing vs unreadable per §7.1. |
