# Roadmap — Operations: From Human Oversight to Bounded Agent Autonomy
*Created: 2026-06-22 — Claude.ai*
*Location: docs/OPERATIONS_ROADMAP.md*
*Status: Living document — update as phases complete*

---

## Vision

Curator, Mein Deutsch, and Guild have been running for months.
With EC2 now the primary environment and the two-node role system
in place, the platform is coherent enough to think seriously about
operational discipline. This roadmap is where I work through what
that looks like.

Right now I handle every operational decision. That's appropriate
at this stage — the system is new to AWS, the patterns are still
settling, and I need to understand what actually goes wrong before
delegating anything. But the longer-term direction is clear: as
reliability is demonstrated, CoS takes on more routine work within
explicit boundaries, and I focus on what actually needs my judgment.

Each phase produces evidence that either justifies moving forward
or doesn't. The Learning System and Decision Records connect here
naturally — operational patterns and policies live alongside
architectural decisions, not separately. That's intentional.

This document is also a record of my thinking at this point in the
project. I'll read it again in six months and either it will have
held up or I'll have learned something that changes it.

---

## Guiding Principles

These don't change across phases. If a decision conflicts with one
of these, the decision is wrong, not the principle.

**Boundaries first.** Agents act only within explicitly defined
policies. What is not explicitly permitted is not permitted.

**Auditability.** Every automated action is logged with reasoning,
timestamp, and outcome. The log is reviewable at any time.

**Human override.** I can always pause automation, reverse an
action, or restrict agent scope. No action is irreversible by design.

**Gradual trust.** Progress between phases depends on demonstrated
reliability from the previous one. Each phase should produce usable
evidence before the next one is pursued.

**Rollback capability.** Automated actions are designed to be
undoable. Actions that can't be rolled back require explicit approval.

**Transparency.** I understand why an agent took or proposed an
action. Opaque automation is not acceptable — if I can't explain
what happened and why, the system isn't ready for that action.

---

## Phase Map

```
Phase 1 (now) Phase 2 (weeks) Phase 3a (months)
Detection only → Proposals + approval → Bounded low-risk autonomy
"Something is wrong" "Should I fix it?" "I fixed it, here's the log"

Phase 3b (later) Phase 4 (future)
Bounded medium-risk → Mature agent operations
"I handled routine "Here's the exception
 maintenance" report for the week"
```

---

## Phase 1 — Detection and Notification (Current)

**What agents do:** Detect problems, send alerts.
**Robert's role:** Makes all decisions, takes all actions.
**Risk level:** Very low — read-only.

**Live today:**
- CoS health checks every 30 minutes on EC2
- CloudWatch + Linux tools two-layer approach
- Alerts via minimoi_system_bot with context and remediation commands
- OpenClaw independent checks spanning broader system

**What good looks like:** Robert receives an alert with the problem,
context, and the exact command to fix it. No ambiguity. No hunting
for information.

**Exit criteria for Phase 2:**
- EC2 health checks running reliably for 4+ weeks with no missed alerts
- Alert format confirmed useful — Robert acts on alerts, not confused by them
- Zero false positives that caused unnecessary interruption
- At least one real incident caught and resolved using the alert + command pattern

---

## Phase 2 — Proposed Actions with Approval (3–6 months)

**What agents do:** Detect problems, propose specific fixes, request
approval, execute on confirmation.
**Robert's role:** Reviews proposals, approves or rejects via Telegram.
**Risk level:** Low — nothing executes without explicit approval.

**Example interaction:**
```
CoS: minimoi-curator container stopped 8 minutes ago.
 Proposed action: docker-compose up -d curator
 This will restart the curator container.
 Approve? Reply /approve_curator or /reject
```

**What needs to be built:**
- Approval workflow in CoS (propose → Telegram → await response)
- Action executor that runs approved commands via `subprocess`
- Action log: every proposal, approval/rejection, and outcome
- Timeout handling (if no response in 30 min, escalate or abort)
- Reject handling (if rejected, note reason and stand down)

**Policy constraints at Phase 2:**
- Only pre-defined commands are proposable (no freeform execution)
- One pending approval at a time (no queuing of multiple proposals)
- All approvals are explicit (no implicit timeout → approve)

**Why this phase matters:** It builds the approval workflow
infrastructure that Phase 3 depends on. It also builds Robert's
confidence in what CoS proposes, which is the foundation for
eventually removing the approval step for low-risk actions.

**Exit criteria for Phase 2.5:**
- 30+ proposals reviewed with high approval rate and low false positives
- Rejection rate < 10% (high false positives indicate poor proposal quality)
- Robert approves proposals within expected timeframe consistently
- No proposal executed incorrectly after approval

---

## Phase 2.5 — Auto-Execute with Mandatory Post-Action Review (3–6 months)

**What CoS does:** Executes the lowest-risk, most reversible actions
automatically (e.g. container restart after 15-minute timeout) with
immediate post-action notification. No pre-approval needed, but I see
every action as it happens and can pause at any time.
**My role:** Review the action log, intervene if anything feels wrong.
**Risk level:** Low-medium — automatic but immediately visible and reversible.

This phase exists because the jump from "always requires approval" to
"trusted autonomy" is too large without an intermediate step. I want to
see how CoS behaves when it acts automatically before expanding what it
can do. The intent is to test whether low-risk actions can be handled
without constant review while keeping full transparency.

**Exit criteria for Phase 3a:**
- 20+ auto-executed actions with zero errors or unexpected outcomes
- Robert reviews action log weekly and finds no surprises
- Rollback tested at least once successfully
- Circuit breaker triggered and handled correctly at least once

## Phase 3a — Bounded Low-Risk Autonomy (6–12 months)

**What agents do:** Auto-execute low-risk, reversible actions within
defined policies. Log everything. Alert after action.
**Robert's role:** Reviews action log, handles exceptions.
**Risk level:** Medium — automatic execution, but constrained.

**Example actions in scope:**
- Restart a stopped container (after configurable timeout, e.g. 15 min)
- Clear temporary files when disk > 85% (defined directories only)
- Rotate logs older than configurable threshold
- Retry a failed cron job once (with cooldown)

**Example actions explicitly out of scope:**
- DNS changes
- Security group changes
- Anything touching EC2 instance configuration
- Database operations
- Any action that affects external services

**What needs to be built:**
- Policy file: YAML/JSON defining allowed actions, thresholds, limits
- Policy engine: CoS reads policy before acting — rejects anything
 not explicitly permitted
- Action log: structured, queryable, linked to relevant Decision Records
- Post-action alert: "I restarted curator at 07:23 UTC. Container
 is healthy. Log: [link]"
- Circuit breaker: if the same action is triggered 3+ times in 24h,
 stop auto-executing and escalate to Robert

**Connection to Learning System:**
Action logs become training signal. Patterns in what actions are
needed most often inform what to expand or optimize next. The local
LLM eventually assists in interpreting operational patterns.

**Exit criteria for Phase 3b:**
- 6+ months of Phase 3a operation with clean action logs
- No significant errors or unintended consequences from automated actions
- Policy engine reviewed and updated at least twice based on real experience
- Circuit breaker has functioned correctly every time it triggered
- Robert comfortable delegating routine restarts without reviewing every log entry

---

## Phase 3b — Bounded Medium-Risk Autonomy (12+ months)

**What agents do:** Handle most routine maintenance autonomously
within stricter, explicitly defined policies.
**Robert's role:** Sets policy, reviews exceptions and weekly summary.
**Risk level:** Higher — broader scope, still bounded.

**Example actions in scope:**
- Scheduled maintenance windows (e.g. weekly image updates)
- Disk management with higher thresholds
- Performance tuning within defined parameters
- Proactive scaling recommendations (not execution)

**Prerequisites from Phase 3a:**
- 6+ months of clean action logs with no significant errors
- Policy engine mature and well-tested
- Rollback mechanisms proven reliable
- Robert comfortable with Phase 3a autonomy level

---

## Phase 4 — Mature Agent Operations (Future)

**What agents do:** Manage most routine operations autonomously.
Produce a weekly operations summary report. Handle incidents within
policy without interrupting Robert for routine issues.
**Robert's role:** Reviews weekly summary, handles strategic decisions
and genuine exceptions. Not interrupted for routine operations.
**Risk level:** High autonomy — but well-contained by mature policy,
circuit breakers, and full auditability.

**Example weekly summary format:**
```
mini-moi Operations Summary — week of 2027-03-10
Automated actions: 12 container restarts, 3 disk cleanup runs
Incidents escalated to Robert: 1 (EC2 disk > 90% — resolved)
Policy changes: none
Action log: [link]
```

This phase is not a near-term target. It requires Phase 3 to be
stable for 6+ months, the policy framework to be mature and reviewed,
and the local LLM capable enough to reason about novel situations.

---

## Safety Mechanisms (apply across all phases)

Because the goal is gradual expansion of capability rather than
rapid automation, these mechanisms apply regardless of phase.
They're not afterthoughts — they're what makes it reasonable to
expand agent scope at all.

**Circuit Breaker**
If CoS triggers the same automated action 3+ times in 24 hours,
automatic execution stops and Robert is escalated:
"Curator has restarted 3 times today. Automatic restarts paused.
Something may need investigation."
The same pattern repeated frequently signals a root cause problem,
not a temporary glitch. Automation that keeps trying is dangerous.

**Rollback by Design**
Every automated action is designed to be undoable:
- Container restart: trivially reversible
- Disk cleanup: moves to recovery directory, never deletes directly
 (retention policy handles eventual cleanup)
- Log rotation: keeps originals for defined period before removal
Actions that cannot be rolled back require explicit pre-approval
regardless of phase.

**Action Log**
Structured, queryable, permanent record of every automated action:
what triggered it, what was done, what the outcome was, which policy
permitted it. Linked to Decision Records where relevant.
```
2026-07-15T07:23:41Z | restart_container | curator |
 reason: container_stopped_18min | outcome: healthy |
 policy: phase_3a_container_restart | approved_by: policy
```

**Escalation Paths**
Clear rules for when CoS stops and asks Robert:
- Same action triggered 3+ times in 24h → circuit breaker → escalate
- Action outside defined policy scope → reject + alert
- Uncertain outcome → alert and stand down, do not guess
- Any action affecting DNS, security, or EC2 config → always escalate

## Key Capabilities to Build (across phases)

### Policy Engine (needed for Phase 3a)
Defines what agents are allowed to do. Lives in a committed config
file (`docs/operations-policy.yaml` or similar). Changes to policy
require a Decision Record. Policy is version-controlled — you can
always see what was allowed at any point in time.

Policy changes follow the same review process as architectural decisions:
proposed in a design session, captured in a DR, committed to the repo.
The policy file history answers "what was allowed, when, and why."
Rollback of a policy change is a git revert — instant and auditable.

```yaml
# Example policy structure
allowed_actions:
 restart_container:
 condition: "container stopped for > 15 minutes"
 scope: [portal, curator, german, postgres]
 cooldown_minutes: 60
 max_per_day: 3
 requires_approval: false # Phase 3a
 log_required: true
 clear_temp_files:
 condition: "disk > 85%"
 scope: ["/tmp", "/var/log/minimoi"]
 requires_approval: true # Phase 2 for now
```

### Approval Workflow (needed for Phase 2)
Telegram-based: CoS proposes, Robert approves/rejects with a reply.
Simple, no new infrastructure. Builds the pattern that Phase 3
eventually automates for low-risk actions.

### Action Log (needed for Phase 2 onward)
Structured log of every automated action: what, why, when, outcome.
Linked to Decision Records where relevant. Queryable by CoS for
pattern detection. Reviewable by Robert at any time.

```
2026-07-15T07:23:41Z | restart_container | curator |
 reason: container_stopped_18min | outcome: healthy_after_restart |
 approved_by: policy_3a | log_ref: curator_restart_20260715
```

### Rollback Mechanisms (needed for Phase 3)
Every automated action has a defined rollback procedure. For container
restarts this is trivial. For disk operations, rollback means the
action is designed to be safe by construction (never delete, only
move to a recovery directory with a retention policy).

### Circuit Breaker (needed for Phase 3a)
If CoS triggers the same automated action 3+ times in 24 hours,
it stops auto-executing and escalates: "curator has restarted 3 times
today. Automatic restarts paused. Something may need investigation."

---

## Connection to mini-moi Architecture

Keeping operations connected to the rest of the system is intentional.
It means operational patterns and policies can be reviewed and evolved
the same way as architectural decisions — not separately.

**Decision Records as policy history:** Every policy change produces
a DR. The policy file and its DR history answer "why is this allowed?"
I want to be able to read back through these and understand my own
reasoning, not just see what the current policy says.

**Learning System → Policy Intelligence:** Action logs become training
signal. As the local LLM matures, it can help interpret whether a
novel situation falls within the spirit of a policy, not just its
letter. That's Phase 3b territory — worth designing for now even
if it's months away.

**CoS as the operations agent:** CoS is the natural home for this
because it already has execution context, the Telegram channel, and
the established role. The operations capability grows within CoS as
each phase completes — not as a separate system bolted on later.

**Guild visibility:** Automated actions should be visible without
asking. Whether that's a new Operations Log view or something in the
Build Log, I want to be able to see what CoS did without pulling logs
manually.

---

## Interview / Portfolio Framing

Phase 1 is live and demonstrable today. That's the honest starting
point.

Phase 2 (approval workflows) is achievable in weeks and demonstrates
the full human-in-the-loop pattern that serious AI operations teams
are building now.

Phase 3 is where mini-moi enters research territory — very few teams
have autonomous agent operations working reliably at any scale. The
policy engine, circuit breakers, and structured action logs are the
right architecture to get there safely.

The story: "I built the detection layer, I'm building the approval
workflow, and I've designed the policy framework for bounded autonomy.
The architecture supports expanding agent scope as trust is earned —
that's the right way to build toward autonomous operations."

---

## Open Questions for Future Design Sessions

**Highest priority — answer before Phase 2 build:**
1. What is the minimum set of actions to allow in Phase 3a?
 Start conservative — one or two actions only, expand from evidence.
2. How should policy changes be reviewed? Same DR process as code
 decisions is the default — confirm before policy engine is built.

**Medium priority — answer before Phase 3a:**
3. Different trust levels for production vs standby? Stricter policy
 on minimoi.ai than dev.minimoi.ai makes sense — define the delta.
4. How does the action log integrate with Guild Docs view?
 New Operations Log view, or fold into Build Log?

**Lower priority — address in Phase 3b planning:**
5. When does the local LLM become capable enough to assist in
 policy interpretation? What's the evaluation criteria from the
 Learning System roadmap that signals readiness?

---

*Operations Roadmap · 2026-06-22 · Claude.ai*
*Short-term spec: docs/specs/spec_operations_monitoring_v2_2026-06-22.md*
*Update this document as phases complete and decisions are made*
