# Guild interaction prototype

The first working version of the Guild interaction (INIT-2026-0007): the **Shop floor** (arrival: Master Craftsman conversation, status lights, Needs you, post-its), the Build workbench, a floating Master Craftsman conversation, Operate evidence, the Build Queue round trip and a phone layout. Specification: [SPEC.md](SPEC.md) (revision 3; earlier reviewed revisions are kept in `SPEC_v1_reviewed.md` and `SPEC_v2_reviewed.md`). Backend map and lift steps: [INTEGRATION_MAP.md](INTEGRATION_MAP.md). Quality notes: [FINDINGS.md](FINDINGS.md).

Everything runs locally. It makes no network calls and no model calls, and it never writes to real data. Master Craftsman and Claude replies, invitations, receipts, the Monday clock and voice are all **simulated** and say so on screen.

## Start

From the worktree root:

```
.venv/bin/python prototype-lab/projects/guild-interaction-prototype/app.py --port 18895
```

Open <http://127.0.0.1:18895/guild>. The server binds 127.0.0.1 only and refuses any other Host header. Stop it with Ctrl-C.

| Option | Effect |
|---|---|
| `--sources live` (default) | Reads `data/guild/build_queue.json` read-only and local `git log`. Records sessions are read only if `GUILD_RECORDS_DB` points to a Records SQLite, which is opened read-only. |
| `--sources sample` | Uses fixtures only. This is deterministic and is what the tests and screenshots use. |
| `GUILD_PROTOTYPE=0` | Production mode. It **refuses to start** unless a real owner guard and write services are bound (SPEC §8.3), so it cannot run standalone. |

## Walkthrough (about 3 minutes)

1. **`/guild`**: four doors, each with a signal and an observation time. Choose **Build**. This opens the **Shop floor** (`/guild/build`): a Status strip of five lights (shape + word + reason; grey means unknown, never green), the conversation, and a rail with Needs you, Continue and Post-its. Try **Ask** on a light. **Open the bench →** shows the detail, where the "file this" rollout is the focused panel.
2. **Arrange the bench**: ↑/↓ or drag ⠿, **Fold**, **☆ Focus**. Leave the page and come back, or reload; the arrangement stays. **Reset to MC default** restores it.
3. **Operate**: the Capabilities drill-down shows four counts and four evidence marks. Tickets is stale and Systems is not instrumented; both say "treat as unknown". Journey health is labelled sample or not instrumented.
   **Usage & limits** (rev 3.1): the light is about limits and headroom (plans and prepaid balances, with projected time-to-limit, vendor warnings and refill receipts). Its detail is a per-agent table in Operate. **Ask** proposes a work shift. To capture evidence, paste a tool's warning or a top-up receipt into the conversation and say "file this". Payment details are removed before anything is kept.
4. **Master Craftsman · floating** (bottom right): ask "Why did usage stall here?", then go to Build. The thread, participants and context carry over.
5. **Open #158 in Build Queue**, then **← Back**. The bench and the conversation are as you left them.
6. Attach **admin-s021-note.txt (sample)**. It is shared for discussion and not filed. Type **file this**, check the payload and **Confirm**. The receipt reads `simulated filing · local receipt r-4486 · file not uploaded` in the thread, on the bench and on item #158.
7. **Invite Claude + Codex**: Claude joins after about 1.5 s and Codex stays *invited*. Type "Agreed. Grant Guest now…" and **Confirm** to record owner decision r-4490. Then **Return Monday · simulated clock** and ask "What did we decide about file this?"
8. **Phone** (390 px): use **Hold to talk**, labelled "simulated · not speech recognition", and **Type**. The four numbers and Needs you fit on the first screen.

**Reset fixtures** (in the banner) clears every prototype key from this browser. The server holds no mutable state; `POST /guild/proto/reset` only returns the list of keys to clear and a digest of the default config.

## Tests

From the worktree root:

```
.venv/bin/python -m pytest prototype-lab/projects/guild-interaction-prototype/tests/test_app.py -q
.venv/bin/python -m pytest prototype-lab/projects/guild-interaction-prototype/tests/browser_checks.py -q
.venv/bin/python prototype-lab/projects/guild-interaction-prototype/tests/capture_evidence.py /Users/vanstedum/Projects/personal-ai-agents/_working/guild-prototype-evidence
```

- `test_app.py`: Flask test client. Covers routes, fixtures, adapters (not configured vs unreadable), the no-write guarantee, fail-closed binding and the dev mount under `/guild-proto`.
- `browser_checks.py`: Playwright with installed Chrome (`channel="chrome"`), headless, on an ephemeral loopback port. It is run explicitly and is not collected by default.
- `capture_evidence.py`: walkthrough screenshots at 1280, 390 and 360 px, plus one live-mode bench.

## How to change the look

| To change | Edit |
|---|---|
| Colours, fonts, sizes, spacing, radii, badge/chip/mark colours, panel width | `static/guild-ui/tokens.css` (the only file with colour values) |
| How a component is drawn (panels, tiles, chips, conversation, phone layout) | `static/guild-ui/components.css` (tokens only, no literal colours) |
| Markup of a page or message | `templates/guild/ui_*.html` pages, `_ui_*.html` partials, and the `<template>` elements in `_ui_mc_conversation.html` |

There are no inline styles in templates or JS. The one exception is the floating panel's dragged position (`--mc-x/--mc-y`, set through CSSOM).

## How to change the flow

| To change | Edit |
|---|---|
| Shop floor lights (which, order, labels, detail links, Usage & limits precedence), Needs-you cap, post-it cap | `config/layout.json` → `floor`; light rules in `guild_ui/lights.py`; usage rules and projection in `guild_ui/usage.py` |
| Usage & limits sample data (plans, balances, agents, shift targets, sample vendor warnings, sample refill receipt, tolerances) | `fixtures/usage.sample.json` |
| Vendor-warning / receipt capture; safe summaries for recognized pasted receipts | `static/guild-ui/js/capture.js` and `scenario.js` (browser), `guild_ui/evidence.py` (structured cookie validation) |
| Shop floor Needs-you rows and Ask replies | `config/scenario.json` → `reminders`, `turns` (`TA-*`) |
| Panel set, default order, default folds, default focus, empty-panel reasons | `config/layout.json` → `bench` |
| Door signals, section strip, Operate tile set and order, phone four numbers | `config/layout.json` → `doors`, `sections`, `operate`, `phone` |
| Scripted MC/Claude turns (trigger → action), proposal payloads, reserved receipts, voice lines, the Monday card and timeline, Needs you rows, post-its | `config/scenario.json` |
| Sample values (queue, commits, sessions, Operate tiles, rollout counts, evidence, journeys) | `fixtures/*.sample.json` (each is marked `"sample": true`) |

Turns are matched in order; the first match wins (`turns[].match` is a list of regexes). Rows that depend on state use `show_when`, for example `{"clock": "mon"}` or `{"decision": true}`.

## Files

```
app.py                     standalone runner (loopback guard, no URL prefix)
guild_ui/__init__.py       blueprint: routes, owner seam, fail-closed registration, scoped CSP
guild_ui/lights.py         stoplight rules (pure functions, tested)
guild_ui/adapters/         one contract (contract.py); live + sample per source
config/layout.json         flow: sections, doors, panels, tiles, phone numbers
config/scenario.json       flow: scripted turns, proposals, receipts, voice, Monday
fixtures/*.sample.json     sample data
templates/guild/           production-shaped Jinja (ui_* pages, _ui_* partials)
static/guild-ui/           tokens.css, components.css, js/ (ES modules)
tests/                     test_app.py, browser_checks.py, capture_evidence.py
```

JS modules: `main.js` (bootstrap), `state.js` (storage, validation, fallback), `world.js` (shared state, clock, show-when), `proposals.js` (proposal/confirm/receipt, queue overlay), `scenario.js` (scripted turns), `conversation.js` (panel UI), `floor.js` (Shop floor), `bench.js`, `operate.js`, `queue.js`, `dom.js`.
