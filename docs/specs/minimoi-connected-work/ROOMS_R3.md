# Rooms R3 — the team, addressed rounds, and the incident door · specification v0.3.2 (review draft)

**Edition:** v0.3.2 · 1 October 2026 · **Author:** Claude Code (sole implementation editor) · **Reviewer:** Codex (independent) · **Decision owner:** Robert
**Status:** draft for Codex's review before any wiring. Robert authorized R3 on dev "if its prerequisites pass" (1 October 2026). Production, main merges, paid live proofs, new cloud services and account changes keep their gates. Builds on [R1](ROOMS_R1.md) v0.5.1 and [R2](ROOMS_R2.md) v0.2 (draft). v0.2 answered Codex's review R3-01…R3-03; v0.3 answers its recheck R3-01b and two stale references (§10).

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
- **Claimable time is recorded.** `turn_order` also stores `claimable_at`, set in the transaction that makes a position claimable (its predecessor ends or is released); a turn waiting behind another speaker accrues no away time.
- **Records-owned release (R3-01, R3-01b).** A lost predecessor cannot strand the round, and a released predecessor can never run later. Inside `sweep()`, in one transaction each:
  1. **Queued and away:** a predecessor still `queued` 90 s after its `claimable_at` is **cancelled** (`disposition='skipped_away'`). It never consumes a budget slot and can never be claimed later; the successor becomes claimable.
  2. **Past its absolute expiry while `claimed` or `running`:** the turn is fenced with the existing protocol in the same transaction (`claimed` → `cancelled`; `running` → `cancel_requested`, disposition `round_deadline`), so no further dispatch or ordinary delivery passes R1's admission and fence; the successor becomes claimable. A turn `recovering` past the deadline is **not** cancelled: the successor is released (`round_deadline_recovering`) and the delivery-only recovery keeps its fresh lease (pause, stop and revocation still fence it). A worker acknowledgement or the R1 lease-expiry rule then ends it as `cancelled` with `stop_ack` recorded; nothing claims the provider stopped. **Deadline scope (v0.3.1, Codex's clarification):** the absolute `expires` is an inference and ordinary-delivery deadline: the fence refuses a post from a `running` turn past it (closing the gap where a renewed lease alone would pass). Delivery-only recovery of an already-generated reply under a fresh recovery lease, with unchanged generation and membership, is preserved past the deadline; it never reopens inference or a cancelled or abandoned turn. Tests cover recovery after the original deadline.
  3. **Uncertain and unreconciled 120 s after its lease expired:** the dependency is released; the turn stays `uncertain` with R1's saved-output and reconciliation rules. Recovery may deliver only an already-generated, journaled reply (no new inference); that reply appears labelled "answered later in this round". The successor's snapshot was fixed at its own claim and is never rewritten.
- **Staggered queued expiry.** A round turn's queued expiry is 10 minutes × (its position + 1), so a later teammate does not expire while waiting its turn; it overrides none of pause, stop, membership, budget, window or fresh-dispatch checks.
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

R2's forwarding sidecar (`records-door`, published only on `127.0.0.1:18881`, forwarding only to Records) is the door; a tunnel route for a hostname such as `rooms.dev.minimoi.ai` would publish it. Prerequisite 1 is R3b. **Prerequisite 2, the front-door control,** needs two things from Robert: approval to publish a new hostname under minimoi.ai, and either creating, or granting access to create, a Cloudflare Zero Trust Access application and policy for that hostname tied to his identity, in his Cloudflare account. Code, configuration and local negative tests can be prepared without publishing anything; no tunnel route or hostname is added, and the door is not exposed, until the access policy exists and a check shows an unauthenticated request stopped at the front door.

## 7. Out of scope

Voice, a second human, cost reporting, tools from meeting turns, laptop-off continuity, production.

## 8. Tests (no model call)

R3a: order, waits-for, skip on failure or away, budget cut, round snapshot contents and attribution, pause and stop mid-round, new message supersedes only unclaimed round turns, agent replies start nothing, UI round line; and the three R3-01b cases: a skipped queued teammate whose connector returns while its successor runs starts nothing; an expired predecessor that still heartbeats or posts is fenced; an uncertain predecessor that recovers after its successor's claim delivers one labelled saved reply, with no repeat inference and the successor's snapshot unchanged. R3b: listed in §3, plus the baseline-code rollback test still passing. R3c: negative tests in the private fix. Export: round replies validate under transcript 1.1 unchanged.

## 9. Gates

G-R3-0 Codex rechecks this draft. G-R3-1 R3a–R3c built, tests green, Codex reviews the frozen diff. G-R3-2 live rounds wait for R2's owner-approved live turns. R3d waits for its boundary evidence and Robert's choice; R3e for his front-door setup; R3f needs R2's owner-approved live turns, R3a, R3b and R3e (the table's list; CoS is not part of R3f).

## 10. Finding map (v0.2)

| Finding | Resolution |
|---|---|
| R3-01 lost predecessor strands a round | §2 Records-owned release (queued 90 s, unreconciled uncertain 120 s, past expiry); predecessor state preserved; late replies labelled; snapshots fixed at claim; bounded context |
| R3-02 logout and rollback | §3 legacy cookies refused after upgrade; rollback withdraws the door, rotates the signing key, then runs older code; bearer clients unaffected |
| R3-03 incident scope | §1 discussion of owner-supplied evidence; tool-based investigation a separate future channel; R3f prerequisites aligned with §9 |
| R3c note | concrete private patch reviewed privately before any merge |
| R3d note | option 1 is a candidate pending boundary evidence; parked |
| R3e note | the specific owner approvals and account access named; no exposure before the check |
| R3-01b released turn could still start | §2: queued-away release cancels the turn (`skipped_away`, no slot, never claimable); expiry release fences dispatch and delivery in the same transaction, and the fence refuses posts past absolute expiry; uncertain release allows only saved-reply delivery, labelled; `claimable_at` recorded; three tests in §8 |
| Stale references | §6 names R2's forwarding sidecar; §9 uses the table's R3f dependency list |
