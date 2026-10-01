# Rooms R3 — the team, addressed rounds, and the incident door · specification v0.2 (review draft)

**Edition:** v0.2 · 1 October 2026 · **Author:** Claude Code (sole implementation editor) · **Reviewer:** Codex (independent) · **Decision owner:** Robert
**Status:** draft for Codex's review before any wiring. Robert authorized R3 on dev "if its prerequisites pass" (1 October 2026). Production, main merges, paid live proofs, new cloud services and account changes keep their gates. Builds on [R1](ROOMS_R1.md) v0.5.1 and [R2](ROOMS_R2.md) v0.2 (draft). v0.2 answers Codex's review R3-01…R3-03 and its notes on R3c–R3e (§10).

## 1. What R3 is

R1 §10 named R3: CoS once Agent A is bounded; everyone and name-list rounds; the incident scenario, which needs a direct door with two prerequisites. **The incident scenario is scoped as a discussion of evidence Robert supplies** (pasted output, attached log files, screenshots): with the portal down, Robert opens Rooms through the direct door, shares what he sees, and MC and Claude Code discuss it in one attributed transcript. Investigation with tools (running commands, reading the repository) is not a Rooms capability in R3; it stays a separate work channel to be specified later with its own delegation, approval and results interface. No CLI tool is enabled inside Rooms to satisfy the narrative. This draft splits that into parts by readiness, so safe parts can proceed and blocked parts are parked with the exact unblocking action.

| Part | What | Readiness today |
|---|---|---|
| R3a | Addressed rounds: "everyone" and name lists, one turn each, in order, each seeing the replies before it | **Buildable** after review |
| R3b | Records logout ends the session on the server (direct-door prerequisite 1) | **Buildable** after review |
| R3c | A pre-existing CoS scheduler boundary finding, tracked privately, fixed before CoS joins anything new | **Buildable** on a branch after review; production follows the normal gate |
| R3d | Bounded CoS in Rooms | **Design decision needed** (§5); parked until Robert chooses |
| R3e | Direct door: Rooms on its own hostname with a front-door control (prerequisite 2) | **Blocked on Robert:** the front-door control lives in his Cloudflare account |
| R3f | The incident scenario as a discussion of owner-supplied evidence (§1) | Needs R2's owner-approved live turns, R3a, R3b and R3e (the same list as §9) |

## 2. R3a — addressed rounds

- **Who answers.** `@everyone` (or `@all`) addresses every accepted teammate with a card and a connector, in the participant strip's order. A name list (`@MC @Claude`) addresses those, in the order written. One message, one round, at most one turn per teammate. Agent replies never start a turn (R1 rule kept); there is no agent-to-agent round.
- **Order.** New table `turn_order(turn_id PK, round_id, position, waits_for, released_at, release_reason)`. A turn is claimable when the turn it waits for is terminal (committed, cancelled, superseded, expired, failed, abandoned), uncertain-and-reconciled, or **released by Records** (below). If an earlier teammate fails or is away, the round continues with the next and the status line says who was skipped and why.
- **Records-owned release (R3-01).** A lost predecessor cannot strand the round: Records releases the dependency, inside `sweep()`, when the predecessor (a) is still `queued` 90 s after it became claimable (its teammate is away), or (b) is `uncertain` and unreconciled 120 s after its lease expired, or (c) is `running`/`recovering` past its absolute `expires`. Release never changes the predecessor: an uncertain turn stays uncertain with its saved-output and reconciliation rules; nothing is re-inferred; nothing is marked failed that did not fail. The successor's snapshot is fixed at its own claim and never rewritten. If the predecessor's saved reply is delivered later, it appears in the transcript labelled "answered later in this round" (a status-view label; the record itself is unchanged).
- **Bounded context.** Earlier-round replies count inside R1's 40-record snapshot limit; the coverage field reports how many earlier-round replies are included and any omitted.
- **Context.** A round turn's snapshot is the R1 snapshot (through `trigger_seq − 1`) plus the committed replies of earlier turns in the same round, attributed, labelled "earlier in this round". The trigger still appears once.
- **Budget and window.** Each turn reserves one slot of the meeting's budget at its own claim (R1 rule). A round larger than the remaining budget is cut where the budget runs out; the cut turns end `cancelled {budget_exhausted}`.
- **Interruptions.** Pause, Stop, End and a new human message behave per R1: pause and stop fence the whole round; a new message supersedes only unclaimed turns of the earlier round.
- **UI.** The status line lists the round: "Round: MC answered · Claude Code is answering… · Codex next". `@everyone` appears in the `@` suggestions when two or more teammates are accepted.

## 3. R3b — Records logout ends the session

Today Records' sign-in is a signed cookie; logout clears it in the browser only, so a copied cookie stays valid until it expires. R3b adds `web_sessions(id PK, principal, credential_id, created, expires, revoked)` (new table only); sign-in creates a row and stores its id in the cookie; every request checks the row; logout and credential revocation revoke it. Older Records code ignores the table (its own cookies keep working there), so R1's data-preserving rollback still holds. Tests: a revoked cookie is refused; a copied cookie stops working after logout; credential revoke ends its web sessions.

- **Upgrade (R3-02):** cookies issued before R3b carry no session id; R3b refuses them and asks for sign-in again (one re-login after the upgrade).
- **Rollback without resurrection:** the older code would accept any cookie it can verify, including one revoked under R3b. The rollback procedure is therefore: (1) withdraw the direct door (remove the tunnel route) before older code runs; (2) **rotate `session-key.txt`**, which both versions use to sign cookies, so every outstanding cookie fails verification under either version; (3) then start the older image. A rollback test replays a revoked cookie against the older code after rotation and expects 401.
- **Clients unaffected:** bearer credentials (connectors, workers, `roomctl`) never pass through the web-session check; tests prove a worker credential works before and after logout events.

## 4. R3c — CoS scheduler boundary (private finding)

A boundary finding about the CoS scheduler, recorded privately on 1 October 2026, must be closed before CoS joins anything new. The fix is a shared service credential between the scheduler and its existing callers, with negative tests and caller-migration tests, built on a branch; its details stay in the private note until fixed and reviewed. This paragraph approves no code: the concrete private patch gets its own private review before it is merged anywhere. On dev it is deployed with the R3 release; production follows the normal reviewed-PR gate and a read-only check of the production port mapping.

## 5. R3d — bounded CoS (decision needed)

The CoS meeting adapter is synthetic-only today because Agent A keeps web search, a seeded memory file and a full-operator token (feasibility review §3; confirmed today in `docker/cos-agent-a/openclaw.json`). A bounded CoS for Rooms needs CoS's voice without CoS's private memory or tools. Options:

1. **A second OpenClaw agent inside Agent A** (`cos-meeting`: `session_status` only, memory off, no seeded memory, fresh session per turn) behind its own one-way relay like MC's. Strongest reuse of the MC pattern; changes the CoS container's configuration.
2. **The CoS persona as a plain gateway call** with CoS's own capped key and no tools. Simplest; but the Rooms worker would then reach the model gateway, which R1 forbids; it needs its own small caller instead.
3. **Wait** for Spec 159's agent-runtime work to bound Agent A itself.

Recommendation: option 1 as a **candidate**, not yet shown equivalent to MC's separate-container boundary: today's Agent A configuration carries global plugins, provider sign-in and defaults that a second agent inside it could inherit. Before it can be built it needs source and effective-configuration evidence of a distinct workspace, no seeded context, no tools beyond the allowed list, and a relay that cannot target another agent or session. Parked until that boundary is resolved and Robert chooses; nothing in R3a–R3c depends on it.

## 6. R3e — the direct door (blocked on an owner action)

Records' loopback port from R2 (`127.0.0.1:18881`) is the door; a tunnel route for a hostname such as `rooms.dev.minimoi.ai` would publish it. Prerequisite 1 is R3b. **Prerequisite 2, the front-door control,** needs two things from Robert: approval to publish a new hostname under minimoi.ai, and either creating, or granting access to create, a Cloudflare Zero Trust Access application and policy for that hostname tied to his identity, in his Cloudflare account. Code, configuration and local negative tests can be prepared without publishing anything; no tunnel route or hostname is added, and the door is not exposed, until the access policy exists and a check shows an unauthenticated request stopped at the front door.

## 7. Out of scope

Voice, a second human, cost reporting, tools from meeting turns, laptop-off continuity, production.

## 8. Tests (no model call)

R3a: order, waits-for, skip on failure or away, budget cut, round snapshot contents and attribution, pause and stop mid-round, new message supersedes only unclaimed round turns, agent replies start nothing, UI round line. R3b: listed in §3, plus the baseline-code rollback test still passing. R3c: negative tests in the private fix. Export: round replies validate under transcript 1.1 unchanged.

## 9. Gates

G-R3-0 Codex rechecks this draft. G-R3-1 R3a–R3c built, tests green, Codex reviews the frozen diff. G-R3-2 live rounds wait for R2's owner-approved live turns. R3d waits for Robert's choice; R3e for his front-door setup; R3f for both plus R2 live.

## 10. Finding map (v0.2)

| Finding | Resolution |
|---|---|
| R3-01 lost predecessor strands a round | §2 Records-owned release (queued 90 s, unreconciled uncertain 120 s, past expiry); predecessor state preserved; late replies labelled; snapshots fixed at claim; bounded context |
| R3-02 logout and rollback | §3 legacy cookies refused after upgrade; rollback withdraws the door, rotates the signing key, then runs older code; bearer clients unaffected |
| R3-03 incident scope | §1 discussion of owner-supplied evidence; tool-based investigation a separate future channel; R3f prerequisites aligned with §9 |
| R3c note | concrete private patch reviewed privately before any merge |
| R3d note | option 1 is a candidate pending boundary evidence; parked |
| R3e note | the specific owner approvals and account access named; no exposure before the check |
