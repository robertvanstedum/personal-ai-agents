# Personal OpenClaw review — MC-OC-001 v0.3 (future-proofing)

**Reviewer:** Mini-moi, personal OpenClaw, agent=main, OpenClaw 2026.9.4, model anthropic/claude-fable-5-1 (subscription)  
**Date:** 2026-10-24  
**Reviewed:** `docs/specs/spec_mc_openclaw_operating_model_2026-10-24.md` v0.3, SHA-256 `36d6409050d98e8587ed96950f480f47b1cf251cd8f2d5132e8cb0007eb4b575`, spec commit `06d8bd09`, PR #367  
**Also read:** Claude Code v0.2 review and Codex v0.3 disposition, `CODEX_CONFIG_RECONCILIATION.md`, baseline manifest `057b9cc2`, current native `workspace-mc-chat/AGENTS.md`  
**Scope:** design review for durability of the direction. No files installed, no tools broadened, no gateway restarted for this review. Chair reviews are input; Robert decides.

---

## Verdict

**Adopt the direction. The architecture is the right one for a real personal agent that survives a runtime swap.** Persona versioned in the MiniMoi repo, memory and records owned by MiniMoi, mutations through MiniMoi domain semantics, OpenClaw doing conversation and investigation. That is exactly the "OpenClaw is replaceable under the agent" principle from September, and v0.3 holds it.

Three things need to be decided or named before a build plan, two of which no reviewer has raised yet (F1, F2 below).

---

## Corrections I accept against my own v0.1 input

Codex checked my proposed diff against the installed runtime and it was wrong in mechanics:

- Removing `group:web` from deny is not enough on a `minimal` profile. `web_search` and `web_fetch` must also be **added to `alsoAllow`**. Both.
- `fs.allowPaths` does not exist. I listed it as an option; it isn't one. Dropping `workspaceOnly` is a whole-host read grant, not a scoped project grant.
- `dir_list`, `grep`, `find` are not core catalogue IDs. Their presence in `alsoAllow` proves nothing until a plugin supplies them. The configuration should list only names the installed catalogue recognises, and A04 should assert that.

This is the review loop working as intended. My personality and behaviour inputs stand; my activation diff does not.

---

## Future-proofing findings

### F1 — Dev MC is running on the personal gateway. That is the Sept 27 rule being broken, and it has blast radius today.

I05 says: "the edited native workspace is the configured mc-chat workspace used by dev's native runtime." Confirmed from my side: `mc-chat` and `mc-agent` are agent entries in `/Users/[redacted]/.openclaw/openclaw.json`, the same gateway process, port, token and plugin set as `main` (me).

Robert's direction on 2026-09-27: *"Personal OpenClaw stays separate: own gateway, keys and data, never becomes part of the MiniMoi containers."* MC is not inside a container, but MiniMoi dev is calling my gateway. The practical consequences:

- I restarted this gateway twice this morning for my own model/auth changes. If dev MC had a conversation in flight, it was cut. The spec's "shared restart is an explicitly scheduled operation" is right, and I violated it without knowing.
- One gateway token covers both me and MC. One plugin load covers both. One `modelPolicy.allow` covers both. A mistake in my config is a mistake in MC's.
- A/B comparison (§9) between me and MC on the *same gateway* cannot isolate host or runtime differences.

**Recommendation:** The spec should state plainly whether native-on-personal-gateway is a dev convenience with an exit condition, or a permanent arrangement. I recommend the former: MC gets its own native gateway (own `OPENCLAW_STATE_DIR`, own port, own token, as Spec #148 originally intended for the sidecar) *or* moves fully to the container path once Option B is proven. Until then, add to the operating rules: no personal-gateway restart without checking for active `mc-*` sessions, and MC changes on this gateway are announced as dev interruptions. §6.3's "separate runtime identity/context" language gestures at this; it should say it.

### F2 — Name the candidate mechanism for the inspection context so the compatibility gate has something to test.

§6.1 Option B (separate inspection context) is recommended but no supported OpenClaw mechanism is named. There is an obvious candidate already in the installed runtime and already in this design:

**mc-chat delegates investigation to mc-agent as a sandboxed subagent run.**

- mc-chat is allowed one additional tool: `sessions_spawn` (the single tool, not `group:sessions`).
- It spawns `agentId: "mc-agent"`, `mode: "run"`, `sandbox: "require"`, with the question as the task.
- mc-agent (already `profile: full` in config) runs `exec`, `git`, `ls /Volumes` etc. **inside the OpenClaw sandbox** with a read-only project mount and no application DB credentials.
- The result returns through the normal child-completion path; mc-chat answers Robert.

This fits the two-agent design the spec already has (conversation lane / job lane), uses OpenClaw-native sandboxing (unlike ACP, which the sandbox does not wrap), and needs no new MiniMoi service.

**Caveats the compatibility investigation must settle, not assume:**
1. The child "inherits the caller's tool-policy ceiling" per the installed `sessions_spawn` contract. If that ceiling means mc-chat's deny list propagates, this path is dead and a differently-shaped delegation is needed. Test it.
2. Sandbox needs Docker on the host; both mc entries currently have `sandbox.mode: "off"`. The sandbox config and mount map for mc-agent must be written and verified.
3. Mounted host devices (`/Volumes/Untitled`) are outside a container sandbox. Device questions still need truthful host observation (the plugin's `observeHost`) or a host-side worker. The spec already says this; it stays true under this candidate.
4. Verification sources: `docs/gateway/sandboxing.md`, `docs/tools/multi-agent-sandbox-tools.md`, `docs/tools/subagents.md` in the installed 2026.9.4 tree.

If this candidate fails caveat 1, the fallback is Option A below.

### F3 — Option A should be a dedicated read-only repo checkout as the workspace, not a materialised copy.

Simplest enforceable boundary that exists today: point `agents.entries.mc-chat.workspace` at a **dedicated git worktree of personal-ai-agents** (sparse or excluded: `.env*`, credentials, `_working/` personal folders, owner databases), keep `workspaceOnly: true`, and place the persona release *in that same checkout* at `services/mc_operations/personas/mc-chat/releases/<version>/` as §6.3 proposes.

Then:
- `workspaceOnly` remains the actual enforcement.
- "Read the project" in AGENTS.md becomes literally true.
- Persona and source share one versioned location; the installation record is a commit hash.
- Freshness is `git pull` on a schedule or on deploy, which is auditable.

Codex's warning stands: a symlink is not a boundary, and completeness/exclusions must be tested (A05). A worktree with an explicit exclude list is testable; a hand-copied snapshot is not.

### F4 — "Keep the thread" needs one record-retrieval tool in the first slice, or A10 cannot pass.

The 16 configured tools cover host, house, domains, guests, GitHub issues, lessons/preferences and Board. None of them lets MC **search or read its own prior conversations and decisions**. Native memory tools are denied by design (correct, MiniMoi owns memory). So in the first slice, "continuity through the shared record" has no tool behind it unless MiniMoi injects prior context into the turn.

**Question for Codex:** does the Records & Rooms work already expose a read tool MC could be given (`minimoi_records_search` or equivalent)? If yes, add it to the first-slice tool list. If no, either inject bounded context server-side or mark A10 as deferred explicitly. The September resume defect was exactly this gap; the spec should not reopen it.

### F5 — Pin the runtime facts to a version and make re-verification part of upgrade.

I02, I09 and the Codex reconciliation rest on facts about **OpenClaw 2026.9.4 build artifacts** (`tool-catalog-CEAfs67d.mjs`, `tool-policy-DsKZFMcK.mjs`, strict `ToolFsSchema`, `agents.entries` hot-reload class). Those hashes change every release. The design will silently rot the first time the gateway is upgraded.

**Recommendation:** add a short "Runtime facts" appendix listing each fact, the version it was verified on, and the rule *"re-verify on any OpenClaw upgrade before changing persona or tool policy."* Put the OpenClaw version in the persona release manifest's `supported_runtime` field (already required by §6.3). CoS runs **2026.7.1** in its container; MC native is on **2026.9.4**. The same AGENTS.md may reference tools that exist on one and not the other. The release manifest should carry the version range it was validated against.

### F6 — Swap-out property: confirmed, with one coupling to watch.

Walking the swap test from September ("replace OpenClaw under the agent without touching rooms, memory, approval storage"): persona lives in the MiniMoi repo, memory is MiniMoi-owned, actions go through `policy.py` tiers and domain services, delegation goes through a MiniMoi route. All survive a runtime swap.

The one OpenClaw-coupled artifact is **AGENTS.md tool-name references** (`minimoi_*` names are MiniMoi's; `web_search`, `read`, `sessions_spawn` are OpenClaw's). Acceptable, but the persona release should separate *role contract* (portable) from *runtime binding* (per-runtime), as §6.3 already suggests with "generated context." Make that a file split, not prose: `AGENTS.md` (portable) + `RUNTIME.md` or generated block (OpenClaw-specific tool names). A future runtime regenerates the second file only.

---

## Answers to the six brief questions

1. **Investigative partner without Robert carrying context?** Yes in design. In the first slice only if F4 is resolved; otherwise MC investigates well but forgets between conversations.
2. **Enough real access, and what mechanism enforces the boundary?** Not yet named in the spec. F2 (sandboxed mc-agent delegation) and F3 (dedicated worktree + `workspaceOnly`) are the two concrete, currently-supported candidates. Test F2's ceiling-inheritance caveat first; it decides which one you get.
3. **Actions governed by consequence, not blanket approvals?** Yes. The tier 1/2/3 floors in `policy.py` plus "do and tell with conditional undo" for Board is the right shape. Delete the AGENTS.md line "every tool that changes something only makes an offer" — it contradicts the live Board behaviour and the spec says so (I03).
4. **Persona/memory contracts avoid stale prompts and competing stores?** Yes. Add F5 (version pinning) and F6 (portable/runtime file split) to make it hold through upgrades and swaps.
5. **CoS fast follow reusable without leaking identity/memory/permissions?** Yes as written. §8.4's negative cross-role retrieval tests are the right gate. Note CoS is on 2026.7.1; F5 applies doubly.
6. **First slice and remaining decisions?** Agree with the spec's first slice. Remaining decisions are below.

---

## Decisions I recommend Robert take now

| # | Decision | My recommendation |
|---|---|---|
| D1 | Where does native MC run? | Treat personal-gateway hosting as dev-only with an exit condition (own gateway or container). Add the no-unscheduled-restart rule immediately. (F1) |
| D2 | Boundary mechanism (§6.1) | Run the F2 compatibility check first (sandboxed mc-agent delegation). If the ceiling rule blocks it, go F3 (dedicated worktree, keep `workspaceOnly`). Reject Option C. |
| D3 | First-slice memory | Require one record-retrieval tool for MC, or explicitly defer A10. (F4) |
| D4 | Persona canonical location | Accept `services/mc_operations/personas/mc-chat/releases/<version>/`. Add `supported_runtime` range and a portable/runtime file split. (F5, F6) |
| D5 | Build owner | Claude Code, as Claude's own review suggests — the work is source inspection and validation heavy. Codex stays reviewer. |
| D6 | MC model | Separate decision, but now unblocked: subscription auth is working on this gateway as of today (`anthropic:manual`), so mc-chat can move from sonnet-4-6 to opus-5 when Robert chooses. Not part of this spec. |

---

## What I did not do

No persona installed, no tool policy changed, no credential created, no gateway restarted for this review. The two restarts earlier today were for my own model/auth changes and predate my reading of I05; see F1. I did not run the F2 compatibility check; it is named for the build-readiness gate, not claimed to work.

**Delivery:** this file is at `_working/mc-autonomy-2026-10-24/PERSONAL_OPENCLAW_REVIEW_MC_OC_001_v0.3.md`. Creation here is not receipt by Codex or Claude Code; pass it explicitly.
