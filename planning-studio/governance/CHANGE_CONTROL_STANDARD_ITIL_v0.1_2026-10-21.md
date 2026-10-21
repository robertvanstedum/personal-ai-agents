# MiniMoi Production Change Control Standard (ITIL-based)

**Version:** 0.1
**Date:** 2026-10-21
**Status:** Adopted by Robert as the guardrail for MiniMoi production. The wording here is a draft for his review.
**Applies to:** everyone who touches production: Robert, Master Craftsman (MC), Claude Code, Codex and any other agent.
**Reference framework:** ITIL 4 service management practices: incident management, problem management and change enablement (called "change management" in earlier ITIL versions).

## 1. Why

Robert, 21 Oct 2026:

> If it's a production change it needs a ticket and a change control process, not just data repairs in prod. I don't want to break the system.

MiniMoi follows a named industry process, so every participant can hold every other participant to it, Robert included.

## 2. The rule

**No production change happens without a ticket and an approved change record.**

A production change is any change to:
- **code** or deployed images;
- **configuration**, flags or environment;
- **infrastructure**: hosts, containers, network, DNS, certificates;
- **access**: accounts, roles, keys, grants;
- **data**: any insert, update or delete outside the product's own normal user flows. That includes "small" data repairs and status fixes.

Reading and investigating production is not a change; it needs no change record. A normal product action that Robert could do through the UI, done through the same authorized application operation, is not a change either.

## 3. Practices

| Practice | Purpose | Record |
|---|---|---|
| **Incident** | Restore service when something is broken or blocked. | An incident ticket: symptom, impact, timeline, workaround, resolution. |
| **Problem** | Find and remove the underlying cause, so the incident doesn't recur. | A problem ticket linked to its incidents. The permanent fix enters the build queue. |
| **Change** | Make a production change safely. | A change record (RFC), see §4. |

An incident may need a change to resolve it. That change still goes through §4 and §5.

## 4. The change record (RFC)

Every change record states:
1. **What** changes, exactly: scope, and the items or rows affected.
2. **Why**: the linked incident, problem or build ticket.
3. **Risk and impact**: what could break, who is affected, and the size of the impact if it goes wrong.
4. **Steps**: the exact procedure or command, using a reviewed operation. Never ad hoc SQL or hand edits.
5. **Pre-checks**: the state that must be true before applying.
6. **Verification**: how success is proven, by read-back or check.
7. **Rollback**: how to undo it, and when to decide to.
8. **Test evidence**: the dev run, or why dev can't reproduce it.
9. **Review**: the independent reviewer (never the change's author alone).
10. **Approval**: the change authority, with date and time.
11. **Outcome**: applied, verified, rolled back or failed; with evidence and closure time.

## 5. Change types

| Type | When | Path |
|---|---|---|
| **Standard** | Low risk, repeatable, and a written runbook exists that Robert pre-approved once. | Follow the runbook. Log each use on its ticket, with verification. Robert may withdraw a pre-approval at any time. |
| **Normal** | Everything that is not standard or emergency. | Ticket → RFC → dev test → independent review → Robert approves → apply in an agreed window → verify → close. |
| **Emergency** | Only to stop active, serious harm: an outage, data loss or corruption, a security exposure, or users blocked from essential use, *and* waiting for the normal path would make it materially worse. | It must be **declared** an emergency, with the reason, *before* acting. Robert gives his yes, verbal or in chat. Apply the smallest change that stops the harm, then verify. **Within 24 hours:** a full RFC, an incident record and a post-incident review (cause, what was done, follow-up problem ticket). |

An emergency is never a shortcut for convenience. "I'm in a hurry" or "it's small" don't qualify.

## 6. Roles

- **Change authority: Robert** for production. No one approves their own change.
- **MC** owns production support and change coordination. It opens and maintains tickets, drafts RFCs, gets reviews, implements approved changes through reviewed operations, verifies them and closes records. It keeps the incident, problem, change and build queues moving, and reports what is waiting on Robert.
- **Builders (Claude Code, Codex)** implement and review. Neither applies a production change without an approved RFC.
- **Reviewer:** an agent other than the author, working from the actual diff or procedure.

## 7. Everyone is expected to push back, including on Robert

This standard binds Robert too. If anyone, Robert included, asks for a production change outside this process ("just do it now", "skip the ticket"), every agent must:
1. **Say plainly** that it is a production change and needs a ticket and approval.
2. **Offer the right path**: a standard runbook if one exists. Otherwise, a normal change prepared quickly. If the situation meets §5, propose declaring an emergency.
3. **Not act** until the path is followed. Robert can't waive the process in the moment; he can declare an emergency, which carries its own documentation duty.

Pushing back here is doing the job, not refusing it.

## 8. MiniMoi specifics

- **Tickets** are GitHub issues in `robertvanstedum/personal-ai-agents`. That repository is **public**: tickets and RFCs never contain personal data (guests' names, emails, credentials). Refer to records by reference (for example `guild:12`); keep details in the private planning repository.
- **Evidence:** platform actions use MC action receipts (C06) and exact grants (C07). The receipt is the execution evidence attached to the RFC.
- **Dev first:** every normal change is tried on dev before production. Production releases come from a clean release worktree and a PR that Robert approves.
- **Data repairs** use a reviewed, bounded repair procedure with verification and rollback, never arbitrary SQL (see the Master Craftsman job description, "Operating authority versus system changes", 20 Oct).

## 9. Worked example: guest requests with no UI

1. **Incident:** Robert can't act on 2 guest requests; MC opens the incident.
2. **Problem:** linked to the known problem ticket (#69, lost in the 1.2 build). The permanent fix is in the build queue.
3. **Workaround:** a normal change (an RFC to reject `guild:12` through the reviewed reject operation, with pre-checks, read-back and rollback), approved by Robert.
4. **Done:** MC applies it, verifies it, and closes the RFC and the incident.

After a few successful uses, the procedure can be proposed as a standard change.
