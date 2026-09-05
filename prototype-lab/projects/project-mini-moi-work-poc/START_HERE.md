# Mini-moi Work Proof of Concept — Start Here

**Status:** Built proof-of-concept candidate; use validation next  
**Home:** Guild → Experiment → Prototype Lab  
**Decision owner:** Robert van Stedum  
**First use:** Career cover-letter collaboration

## What this project contains

This Prototype Lab project tracks one experiment with two deliverables:

1. **The Mini-moi Work capability** — the provider-neutral code and evidence
   boundary that Chief of Staff will use to carry substantive work across
   sessions.
2. **The agent-and-Robert team method** — the role separation, handoffs,
   independent verification, human gates, and case-study chain used to design
   and build that capability.

Both deliverables are in the `prove` portion of Guild's `try → prove → keep`
loop. The code exists and has passed independent technical review. The method
has produced a reviewed case-study draft. Neither has yet earned the `keep`
decision through real use.

## Read in this order

1. [Mini-moi Work vision](docs/MINI_MOI_WORK_VISION.md)
2. [Accepted foundation specification](spec/README.md)
3. [Current case study](docs/CASE_STUDY_AGENT_TEAM.md)
4. [Proof-of-concept validation plan](build/POC_VALIDATION_PLAN.md)
5. [Decisions](DECISIONS.md)
6. [Case-study evidence chain](evidence/case-study/README.md)
7. [Claude Code registration handoff](handoffs/HANDOFF_TO_CLAUDE_CODE.md)

## Where the code lives

The implementation is not copied into this project folder. Its authoritative
location remains `domains/cos/work/`, with synthetic tests under `tests/cos/`.
W0a merged through PR `#200`; W0b was independently cleared at commit
`c9cdde7` and remained open as PR `#201` at the September 5 fact check.

The design began in the local Planning Studio work under `INIT-2026-0004`, but
this project does not depend on Planning Studio becoming a production system.
The accepted specification is copied into `spec/`, with its original identity
and bytes preserved. Prototype Lab is now the durable tracked home for the
proof of concept; the implementation remains authoritative in its code package.

## Current gate

Run the file-level test, then the thin Chief of Staff entry-point test: give
Chief of Staff a job description and say, **“Start cover letter.”** Record the
result honestly, including defects and the amount of Robert rewriting needed.

If the work is useful and safe, Robert may choose `keep` and authorize the
first private Chief of Staff Work release, tentatively `v0.9`. A close `v1.0`
may follow after the first real-use refinements. Those are domain capability
versions, not automatic whole-system Mini-moi releases.
