# RFC (draft): MC lessons, guest-request commands, voiced outcomes and R93 to production

**Status:** DRAFT. Not approved; nothing is scheduled.
**Standard:** `planning-studio/governance/CHANGE_CONTROL_STANDARD_ITIL_v0.1_2026-10-21.md`, §4.
**Change type:** normal. **Change authority:** Robert.
**Prepared by:** Claude Code (reviewer session), 21 Oct 2026.
**Ticket:** to be opened, naming no personal data.

## 1. What changes

The production portal gains four owner features. Each sits behind its own flag, and each can be switched off alone.

| Feature | Flag(s) | Effect in production |
|---|---|---|
| Teach MC lessons ("Remember: …", Confirm, Forget, list) | `MINIMOI_MC_LESSONS=1`, `_ENV=prod`, `_DIR=<persistent volume>` | Guidance text in MC's context. No authority. |
| Guest-request reject by owner command (propose, then Confirm) | `MINIMOI_MC_GUEST_ACTIONS=1`, `_ENV=prod` | **A production data change**: a request's status becomes `rejected`, verified by read-back. |
| MC voices the platform's outcome | `MINIMOI_MC_VOICED_OUTCOMES=1` | Wording only. MC can't start anything in these turns. |
| R93 downloads ("deliver/send/save … as a file") | already flagged (`MINIMOI_MC_DELIVERIES`) | The parser change only. |

**Excluded and staying off in production:**
- delegation to Claude Code (`MINIMOI_MC_DELEGATION`): no production transport, and the file-read finding is deferred;
- MC tools (`MINIMOI_MC_TOOLS`): built for the Mac runtime, and option A is undecided.

## 2. Why

Robert's direction, 21 Oct: MC is his production support. Lessons let him shape MC once. Guest rejects fill the gap in #69 until the screen is rebuilt.

## 3. Risk and impact

| Risk | Likelihood | Impact | Control |
|---|---|---|---|
| The release carries more than intended: the dev branch is **51 commits ahead of `main`**, including the receipts and grants foundation (C06/C07) that guest rejects depend on | High unless handled | Untested code reaches production | Release worktree from `origin/main`. Cherry-pick only the listed commits **plus** their foundation, each named in the PR. Full suite in the release worktree. |
| Guest reject acts on the wrong request | Low (exact approval, target-version check, retried-Confirm fix) | One real guest wrongly rejected | Approve each one by Confirm; read-back; rollback step 7.2 |
| Lesson ledger lost on redeploy | Medium if the directory isn't on a persistent volume | Lessons silently gone | `_DIR` on a named volume; verify after deploy |
| Migrations fail or lock tables | Low (additive `CREATE TABLE IF NOT EXISTS`) | Portal start delayed | Apply before the flag flip, in a quiet window |
| The turn ledger is off in production | Unknown; to check | Retry protections weaker | Pre-check 5.3 |

## 4. Steps

1. Open the ticket (no personal data) and link this RFC.
2. Create a release worktree from current `origin/main`. Cherry-pick the approved commits; list them in the PR body.
3. Run the focused tests, the full CI-safe suite, and the real-PostgreSQL suites (`tests/mc_domain_actions`, `tests/mc_lessons`).
4. Open a PR. Robert reviews and merges it; CI deploys the image with **all four flags off**.
5. Apply the production migrations (additive): `004_action_receipts`, `005_action_grants`, `mc_domain_actions/001`, `002`.
6. Create the persistent lessons directory, mode 0700, owned by the portal user.
7. Turn the flags on **one at a time**: lessons, then voiced outcomes, then guest actions. After each, run its verification in §6.

## 5. Pre-checks

1. Robert's hands-on dev trial passed (the guide in `HANDS_ON_DEV_2026-10-21.md`).
2. An independent review of the 21 Oct review fixes is closed.
3. The turn ledger is on in production (`MINIMOI_MC_TURN_LEDGER=1`). If it isn't, decide before step 7.
4. Production health is green, and no other change is in flight.

## 6. Verification

- **Lessons:** "Remember: …" then Confirm gives "verified by read-back"; the lesson reaches the next answer; Forget drops it; the ledger survives a container restart.
- **Voiced outcomes:** MC words the next platform reply, and it adds nothing beyond the platform's result.
- **Guest actions:** a list with no change; then, only if a real request is waiting, Robert decides whether to reject it. Otherwise verify on a synthetic request inserted and removed under this RFC.
- **Logs:** no new errors; health 200.

## 7. Rollback

1. Turn the flag(s) off. The features stop at once; nothing else changes.
2. **A wrongly rejected guest:** set that one request back to `requested`, under an emergency or normal change of its own, recorded on the ticket.
3. **The code itself:** revert the PR. The tables are additive and stay.

## 8. Test evidence (dev)

- Dev runs the merged code: image `44262cef`, health 200.
- Suites, 21 Oct: Guild regression 3,051 passed; real PostgreSQL 41/41.
- Robert's hands-on trial: **pending**.

## 9. Review

- **Code:** independent review of the journeys (`dev_journeys_review.md`).
- **The 21 Oct fixes:** independent review in progress.
- **This RFC:** Robert.

## 10. Approval

Robert: ____________________ (date and time)

## 11. Outcome

To be filled in after the change: applied, verified, rolled back or failed, with evidence and closure time.
