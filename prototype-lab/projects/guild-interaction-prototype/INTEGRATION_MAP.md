# Integration map: Guild interaction prototype → portal

**September 26, 2026 · Claude Code · build list for "the rest"**

Each row lists the adapter as built today, the real backend it should read or write, what is missing, and the write path it needs. "Receipted write" means proposal → owner confirm → platform operation → durable receipt. Every row here was checked against this checkout at `6b3f2331`. Items marked *to verify* were not confirmed.

## 1. Reads (adapter matrix as built)

| Surface | Adapter today (`--sources live`) | `--sources sample` | Real backend | Missing to make it fully live |
|---|---|---|---|---|
| Build Queue page, item detail, In motion, Blocked (queue part), Build queue tile, door "N active in queue", MC "what's in build" | **live** `LiveBuildQueue`: reads `data/guild/build_queue.json` read-only. A missing or unreadable file, invalid JSON, a wrong top-level shape or a row without an integer id makes the whole queue *unknown*, and no count is shown anywhere. A row with an unrecognised, missing or non-text status is kept, marked `unknown status '…'`, excluded from active counts, and summarised as "N rows with unknown status" beside the live badge | `SampleBuildQueue` | the same file (the portal's source of truth, `_load_build_queue`, `minimoi_portal/app.py` ~l.1273) | Nothing for reads. Do **not** switch to `_load_build_queue()`, which returns `[]` on error (a false zero); keep this adapter's validation. |
| Item history | **not instrumented** (`history()` returns not_configured) | fixture history | `guild.design_log_transitions` (DB), `GET /guild/build/items/<id>/history` | A DB read adapter that returns *unknown* on failure. Today's endpoint returns `[]` on error (FINDINGS G-04). |
| Since you were here | **live** `LiveActivity`: `git log -n5` in the worktree (local, 2 s timeout, no fetch/gh) | `SampleActivity` | local git of the deployed checkout; later GitHub PR/CI state | A real "since last visit" marker; GitHub reads need a token and network, which is out of scope here. |
| Discussions | **live** `LiveSessions` if `GUILD_RECORDS_DB` is set (SQLite `mode=ro`, `rooms` table); unset → this panel's **explicit sample opt-in** ("sample · no Records source configured"); set but unreadable → *unknown* | `SampleSessions` | Records store sessions (`rooms`, `persistent_rooms`, `members`) of the Rooms service | Participants per session (`members` join); the Rooms API rather than direct SQLite once it is on the portal path. |
| Shop floor lights | derived by `guild_ui/lights.py` from the same adapters: Build queue **live**; Rollouts, Agents, Usage & limits **sample**; Systems **not instrumented** (grey) | same, all sample | as the rows below: queue file, Records usage and grants, work/attempt store, gateway cost checkpoints, Operations agent `:8768/status` | the live sources below. The rules (access gap; usage warn 80 %, projected limit before reset, 48 h refill horizon, precedence) are Robert's to confirm. |
| Usage & limits (rev 3.1): light, Operate tile + per-agent table, Limit reminder, Ask → shift proposal | **sample** `fixtures/usage.sample.json` via `SampleOperate.usage()`; rules and linear projection in `guild_ui/usage.py`; Grok plan marked **not instrumented** | same | plan usage windows (Claude, ChatGPT/Codex, Grok plans: vendor usage pages or APIs where they exist, otherwise manual entry); prepaid API balances (xAI, Anthropic, OpenAI billing APIs); per-agent attribution from the shared model gateway cost checkpoints (`866facc1`, location *to verify*); overall spend from gateway totals | a read adapter per plan/balance with observed time and staleness; agent→source mapping as config; recent-window usage history for projections. Vendor warnings (REPORTED) today come only from manual paste + "file this"; planned: per-tool hook or log capture. Refill receipts (REPORTED, receipt) today come only from manual paste; planned: forwarded receipt emails or billing APIs. Filed evidence lives in the browser (localStorage + one validated cookie) and must move to a server evidence store with receipts; payment-method details must never be stored (strip before storage, as `guild_ui/evidence.py` and `capture.js` do). The shift proposal needs a real routing/assignment operation, which is proposal → confirm → receipt, with MC accountable (see `DECISIONS_MC_BACKEND_v0.1_ROBERT_2026-09-26.md` addendum). |
| Needs you rows | **sample** (scenario, "MC-curated") | same | future MC agent (OpenClaw, spec #146) plus queue `blocked`/approval states and PR review state | MC curation job, PR/approval source, durable Needs-you store |
| Focus panel: rollout counts, field evidence, recent change | **sample** (`fixtures/operate.sample.json`) | same | Records/Rooms usage (who reached/used "file this"), access grants (portal auth, `guest` files / grants), U02 check output, deploy log | Usage events per capability, grant registry read, check-result store |
| Operate tiles: Capabilities, Sites, Disk, Tickets, Agent work (Usage & limits: see the row above) | **sample**, set explicitly per tile | same | Capabilities: as above. Sites/Disk: host checks. Tickets: ticket source (*to verify*). Agent work: work/attempt store. | Probes with observed time and freshness limits (OQ §1). |
| Operate tile: Systems | **not instrumented** (no fixture on purpose) | same | Operations agent `GET http://localhost:8768/status` (used by today's `/guild/operate`) | A read adapter with timeout that returns *unknown* on failure. The prototype must not call port 8768. |
| Journey health (3 journeys) | **sample / not instrumented** labels only | same | end-to-end probes: sign-in + open Guild, ask MC, save/retrieve record | Journey definitions, probes, freshness, owners (OQ §1 fast follow) |
| MC and Claude replies | **simulated** (`config/scenario.json`) | same | MC OpenClaw agent through the shared model gateway (`4d0a67e2`) | The agent, the gateway route, and retrieval over thinking logs |
| Participants / invitations | **simulated**: Claude joins after 1.5 s; Codex never joins | same | Rooms membership + collaborator access (spec 157) | Scoped invitation grant, join acknowledgement from the agent runtime |
| Recorded conversation | **browser localStorage** (`guild.conversation.v1`) | same | Records `events` of session s-023 | Server-side session writes. Off-record segments stay client-only (never sent). |

## 2. Writes (all local today; none reaches a real store)

| Action | Today | Real backend | Receipted write path needed |
|---|---|---|---|
| Queue status Save (card or item) | overlay `guild.overlay.v1`, chip "pending real write · r-xxxx", thread receipt | `POST /guild/build/items/<id>/status` (writes JSON + `design_log_transitions`) | Owner Save = confirmation (decision Q4) → server write → receipt id returned by the server. Replace `proposals.queueSave`. |
| "file this" (attachment) | receipt `simulated filing · local receipt r-xxxx · file not uploaded`; name/size/sha256 metadata only | Records `documents` (sha256, content, source_note) in the destination session | Proposal (payload: file, session, work item, digest) → confirm → upload + store → receipt with document id. Refuse off the record. |
| Owner decision | receipt r-4490 (reserved), decision card | Records `events` (kind: decision) + work-item link | Proposal → confirm → event write → receipt; Monday card reads it back |
| Post to Needs you | local flag + receipt | Needs-you store (new) or queue item | Proposal → confirm → write → receipt |
| Operate next steps (grant, training, product, investigate) | local receipt, "nothing executed" | grant: portal auth/guest grants; product: queue item; others: MC task | Proposal → confirm → platform operation → receipt; the grant is the highest-risk case and needs its own review |
| Invite Claude/Codex | simulated direct command (decision Q3) | collaborator access (spec 157) + Rooms membership | **Scoped proposal → confirm → receipt** before any real agent access |
| Bench arrangement | `guild.bench.v1` in the browser | per-user preference store (portal) | Plain preference write; no receipt needed |

`static/guild-ui/js/proposals.js` is the seam. `propose`, `proposeFile`, `edit`, `confirm`, `cancel`, `queueSave` and `receiptsFor` become `fetch` calls with the same signatures. `register_guild_ui(write_services=…)` is where the server store is bound; production mode refuses to start without it.

## 3. Lift to portal

Templates and assets are intended for reuse; **production binding is a separate reviewed change**. Suggested order, one reviewed change per step:

1. Copy `guild_ui/` (package), `config/`, `fixtures/`, `templates/guild/ui_*.html` and `_ui_*.html`, and `static/guild-ui/` into `minimoi_portal/`. Template names are prefixed `ui_`/`_ui_` so nothing collides with existing `guild/*.html`. Assets are served by the blueprint's own guarded route (`…/guild/ui-assets/`) and never shadow `/static`.
2. Register: `register_guild_ui(app, prototype=False, owner_guard=_require_owner, write_services=<server store>, current_user=_current_user, sources="live", url_prefix=…)`. The real `portal_nav_html` global is kept, because the stub is registered only when the host has none.
3. Resolve route overlaps. The blueprint serves `/guild`, `/guild/build`, `/guild/build/queue` and `/guild/operate`, which the portal also owns. Either mount under a prefix (as for dev, §4), or retire the old view functions one at a time. `/guild/build` stays the Build Log (decision Q1); remove the blueprint's redirect when mounting unprefixed.
4. Replace `proposals.js` storage with server calls (§2), starting with queue Save, which already has a server endpoint.
5. Decide CSP: the blueprint sets its own CSP on its responses only. The portal nav HTML uses inline `<style>`/`style=` attributes, which the blueprint CSP (`style-src 'self'`) would block. Either keep blueprint pages on a portal-compatible CSP or move the nav styles into a stylesheet (*to decide*).
6. Fonts: the prototype loads no web fonts. The token families match the portal's Google Fonts, so the portal's font link applies once a page includes it (the blueprint CSP would need `font-src`/`style-src` for Google Fonts, or self-hosted fonts).

## 4. Dev mount (dev.minimoi.ai → Mac `localhost:5001`)

Robert approved this mount. Robert or Claude Code does the wiring in the dev portal runtime. **This pass does not touch `minimoi_portal/` or any runtime worktree.**

```python
from guild_ui import register_guild_ui          # with the prototype dir on sys.path
register_guild_ui(app, prototype=True, owner_guard=_require_owner,
                  current_user=_current_user, url_prefix="/guild-proto", sources="live")
```

- Every route **and** the asset path move under the prefix: `/guild-proto/guild`, `/guild-proto/guild/build/bench`, `/guild-proto/guild/build/queue`, `/guild-proto/guild/build/items/<id>`, `/guild-proto/guild/operate`, `/guild-proto/guild/ui-assets/…`, `POST /guild-proto/guild/proto/reset`. The portal's own `/guild…` routes and `/static` are untouched. Test: `test_mounted_under_prefix_on_host_app`.
- A passed `owner_guard` is always used, including with `prototype=True`. The no-op guard applies only when `prototype=True` **and** no guard is passed. The guard has the same decorator shape as `_require_owner` and wraps pages, the reset endpoint and assets. Test: `test_owner_guard_refuses_anonymous_serves_owner`.
- The `portal_nav_html` stub is registered only if the app lacks that global, so the real portal bar with real links is used on dev. Test: `test_portal_nav_global_not_overridden`.
- CSP and `no-store` headers are set in a **blueprint** `after_request` and never app-wide. Test: `test_csp_absent_on_host_routes`.
- Templates use `url_for` only; JS gets every URL (`base`, `urls.*`) from the page's JSON block. There are no absolute paths in templates or JS. The prefixed-mount test checks that every `href`/`src` starts with the prefix.
- localStorage keys are namespaced by mount: standalone `guild.bench.v1`, dev `guild.guild-proto.bench.v1`, and so on. The reset endpoint returns the namespaced keys.
- The standalone runner (`app.py`, no prefix, 127.0.0.1:18895) is unchanged. Its loopback Host/Origin guard is app-level in the runner only, not in the blueprint. On dev the portal's own host handling applies.
- Pass `current_user=_current_user` so the real portal bar shows the signed-in user. Without it, a sample "Robert" dict is used.
