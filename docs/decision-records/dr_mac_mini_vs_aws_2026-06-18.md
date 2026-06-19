---
type: decision-record
dr-type: roadmap
domain: platform
status: active
lora-candidate: yes
---

# Decision Record — Mac Mini vs AWS for Production and Local LLM
*2026-06-18 · mini-moi*

---

## Decision

Abandon the Mac Mini purchase plan entirely. Use AWS for both
production hosting and local LLM capability:

- **t3.small** (~$17/month, always-on): Portal, Curator, Mein Deutsch
- **g4dn.xlarge spot** (~$0.16/hr, on-demand): Ollama, RAG, LoRA training

The MacBook remains the dev environment. The Mac Mini is deferred
indefinitely. AWS architecture is enterprise-appropriate from day one.

---

## Context

The original plan was to buy a Mac Mini as a dedicated always-on
production machine — eliminating the Cloudflare tunnel dependency
and giving mini-moi a persistent home not tied to the MacBook's
sleep cycles. A separate, more expensive Mac Mini with sufficient
RAM was also being considered for running larger local models (Ollama
at 30B-70B quantized).

Two questions reframed the decision:
1. Is the Mac Mini the right way to achieve always-on production?
2. Is a Mac Mini the right way to run local LLMs at the quality
   level the Learning System requires?

Both answers turned out to be no.

---

## Alternatives considered

### Mac Mini for production hosting (~$600–800)

**What it was:** Purchase a Mac Mini, set it as a dedicated always-on
server, run Portal/Curator/German there, retire the Cloudflare tunnel.
Same services, just running on hardware that doesn't sleep.

**Why it was attractive:** One-time cost. Local hardware, no cloud
dependency. Familiar environment (macOS, same toolchain). No monthly
bill for compute.

**Rejected because:**
- **Capital expenditure with no recovery path.** $600-800 upfront
  for a box that sits on a desk. If AWS achieves the same goal at
  $17/month, the break-even on the Mac Mini purchase is 35-47 months.
  The Mac Mini never makes financial sense over that horizon.
- **Hardware to manage.** A physical machine requires setup, updates,
  and physical access for intervention. AWS instances can be replaced
  in minutes.
- **Not enterprise-appropriate architecture.** The stated goal is
  enterprise applicability. A Mac Mini on a desk is not the
  architecture a professional engineering team would deploy. Running
  containerized Flask apps on EC2 with a CI/CD pipeline is.
- **Locks to Apple hardware.** The Learning System roadmap may involve
  GPU workloads. A Mac Mini with enough GPU headroom for 30B model
  inference is not the same $600-800 device.

> [FLAG: principle — the same operational goal achieved through
> enterprise-appropriate architecture is worth more than the same goal
> achieved through local hardware, even if local is cheaper short-term.
> The architecture is the skill being built.]

---

### Mac Mini for local LLM (~$2,000)

**What it was:** Purchase a Mac Mini with sufficient RAM (64GB+) to
run Ollama at 30B-70B quantized model quality — the quality range
needed for the Learning System (RAG in Phase 1, LoRA in Phase 2).

**Why it was attractive:** Local inference, no cloud cost per query,
no data leaving the machine, Apple Silicon unified memory (excellent
for LLM inference), one-time cost.

**Rejected because:**
- **$2,000 upfront vs. $0 upfront.** A g4dn.xlarge spot instance at
  ~$0.16/hour used 4-8 hours/day costs $19-38/month. Break-even on
  the Mac Mini purchase is 52-105 months. The Mac Mini never makes
  sense as an investment.
- **Model quality ceiling.** An Apple Silicon Mac Mini at 64GB RAM
  runs ~70B quantized models. A g4dn.xlarge has a T4 GPU with 16GB
  VRAM — runs 13B-30B quantized models well at high throughput.
  Larger EC2 instances can run larger models. The GPU instance
  scales with the task; the Mac Mini doesn't.
- **Spot pricing is the right model for LLM workloads.** LLM inference
  for design sessions and German practice is a bursty, interruptible
  workload. Spot pricing exists exactly for this pattern — 70% discount
  vs on-demand, acceptable for workloads that can handle a 2-minute
  reclamation notice. The Mac Mini is a fixed cost whether or not it
  is being used.
- **Defers Learning System dependency.** The Learning System Phase 1
  (RAG) and Phase 2 (LoRA training) were both waiting on the Mac Mini
  purchase. With the g4dn.xlarge spot, they no longer depend on hardware
  that hasn't been bought.

> [FLAG: principle — bursty, interruptible workloads are the ideal use
> case for spot pricing. Don't buy fixed infrastructure for variable
> demand.]

> [FLAG: constraint — contract ends August 3. The Learning System cannot
> wait for a hardware purchase cycle. AWS spot is available immediately.]

---

### AWS t3.small + g4dn.xlarge spot (chosen)

**Web apps (t3.small, ~$17/month, always-on):**
Portal, Curator, Mein Deutsch run in Docker containers on a t3.small
(2 vCPU, 2GB RAM). Nginx handles SSL and routing. Always-on replaces
the Cloudflare tunnel. Total: ~$17/month after free tier.

**Local LLM (g4dn.xlarge spot, ~$0.16/hr, on-demand):**
T4 GPU, 16GB VRAM, 4 vCPU, 16GB RAM. Runs 13B-30B quantized models
in Ollama. Start for design sessions, German practice, RAG queries,
LoRA training runs. Stop when done. No idle cost.

**Why this is correct:**
- No capital expenditure
- Enterprise-appropriate architecture (the skill being built)
- AWS CI/CD pipeline is learnable through actual use
- GPU instance scales to the workload; Mac Mini doesn't
- Both Learning System phases now unblocked without hardware purchase
- Spot pricing is the right model for bursty LLM demand

**Total cost: ~$55-74/month at moderate GPU usage (4-8 hrs/day).
Year 1 actual cost (free tier on web apps): ~$20-40/month.**

---

## Constraints that shaped this

**Contract ends August 3 (~7 weeks from decision date).** The
Learning System roadmap could not wait for a hardware purchase,
setup, and configuration cycle. AWS is operational within hours.
The Mac Mini would take a week of setup before it's production-ready.

**$60/month operating budget.** The combined AWS cost fits comfortably
within this. The Mac Mini is a $600-2,000 upfront cost with no
comparable monthly operating cost.

**Enterprise applicability goal.** Containerization, CI/CD pipelines,
IAM, CloudWatch — these are learned by doing, not by reading. The
migration is itself the skill-building exercise. A Mac Mini on a desk
does not build AWS skills.

---

## Assumptions made

**t3.small can handle three Flask apps + Postgres under light load.**
One user, light request volume. 2GB RAM may be tight; start with
t3.small and monitor. Downgrade to t3.micro if headroom is
comfortable; upgrade to t3.medium if it's tight.

**g4dn.xlarge T4 GPU quality is sufficient for Phase 1 RAG and
Phase 2 LoRA.** This assumption hasn't been validated with actual
workloads. If the T4 is insufficient for LoRA training at the required
quality, a larger GPU instance (p3.2xlarge with V100) is available
at higher spot cost.

**Spot reclamation is acceptable for LLM sessions.** A 2-minute
reclamation notice is sufficient to save state for conversational
sessions. If a training run is interrupted by spot reclamation, it
can be resumed. This assumption holds for Phase 1-2 workloads; it
may need revisiting for long training runs in Phase 3.

---

## Known failure modes

**t3.small is RAM-constrained.** Flask + Gunicorn with multiple workers
for three apps + Postgres could exhaust 2GB. Mitigation: monitor via
CloudWatch from day one, upgrade if needed.

**Spot reclamation during LoRA training.** A training run interrupted
mid-epoch loses that epoch's work. Mitigation: checkpoint regularly,
use Spot instance interruption notice to trigger checkpoint before
reclamation.

**Elastic IP exhaustion.** AWS allows 5 Elastic IPs per region by
default. Not a current concern — 1 needed for the web instance.

---

## Principles this decision reflects

- "The same operational goal achieved through enterprise-appropriate
  architecture is worth more than the same goal achieved through
  local hardware, even if local is cheaper short-term. The
  architecture is the skill being built."

- "Bursty, interruptible workloads are the ideal use case for spot
  pricing. Don't buy fixed infrastructure for variable demand."

- "Don't buy hardware when cloud is cheaper over the relevant time
  horizon and teaches better skills."

- "Capital expenditure defers optionality. Monthly operating cost
  preserves it."

---

## What this is not

**Not a permanent rejection of local hardware.** If the use case
ever requires always-on local inference (data sovereignty, latency
requirements, truly large models that no cloud instance makes
economical), the calculation changes. The rejection is of the Mac Mini
at the current price/capability point for the current use case.

**Not a commitment to AWS permanently.** The containerization (Phase 0)
makes the stack portable. If another cloud or provider makes more sense
later, the containers move.

---

## Flags from the session

- [FLAG: principle — enterprise architecture is the skill being built,
  not just the outcome]
- [FLAG: principle — spot pricing for bursty, interruptible workloads]
- [FLAG: constraint — contract deadline makes hardware purchase cycle
  impractical]
- [FLAG: deferral — Mac Mini deferred indefinitely, not permanently
  rejected]

---

## Impact / Follow-up

- **Mac Mini purchase: deferred indefinitely.** No further evaluation
  needed unless spot instance cost significantly exceeds projection.
- Spec: `_working/docs_AWS_MIGRATION_PLAN_2026-06-18.md` — Phase 0
  through Phase 6 build plan
- Learning System Phase 1 (RAG) and Phase 2 (LoRA) are now unblocked
  at the infrastructure level. Gate: AWS Phase 5 stable.
- Supersedes: Mac Mini migration plan (prior roadmap item, now removed)

---

*Decision Record · 2026-06-18 · Claude Code (from session transcript)*
*File: `docs/decision-records/dr_mac_mini_vs_aws_2026-06-18.md`*
