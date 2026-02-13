# AI Coding Tools Evaluation & Selection
**Decision Log: Why OpenClaw + Claude Sonnet 4.5 for Production Infrastructure**

_Author: Robert Van Stedum (with Mini-moi AI collaboration)_  
_Date: February 13, 2026_  
_Context: Building personal AI agent infrastructure for RVS Associates LLC_

---

## Executive Summary

When building production AI infrastructure in early 2026, we evaluated three major AI coding platforms:
1. **Claude (Anthropic)** - Artifacts, Claude Code, API access
2. **Grok (xAI)** - Grok Code CLI, code-fast models
3. **OpenAI** - Canvas, GPT-5.x agents, Codex

**Decision:** OpenClaw framework + Claude Sonnet 4.5 via API

**Rationale:** Transparency, production-readiness, educational value, and cost-effectiveness for infrastructure projects outweighed the convenience of autonomous agents or prototype tools.

---

## The Landscape (Early 2026)

### Claude (Anthropic)

**Products:**
- **Artifacts** - Web UI for live code preview/editing (prototyping)
- **Claude Code** - Autonomous CLI agent (multi-file edits, git integration)
- **API Access** - Sonnet 4.5, Opus 4.6 (best reasoning models)

**Strengths:**
- #1 on agentic coding benchmarks (SWE-Bench, Terminal-Bench)
- Best explanations and reasoning
- Strong function calling / tool use

**Limitations:**
- Artifacts: Web UI only, not API-accessible
- Claude Code: Separate product, less transparency
- Higher per-token cost than competitors

### Grok (xAI)

**Products:**
- **Grok Code CLI** - Emerging autonomous agent (early 2026)
- **grok-code-fast** - Optimized for speed + low cost

**Strengths:**
- Extremely cheap ($0.05-0.10 per million tokens)
- Fast code generation
- Competitive on agentic benchmarks

**Limitations:**
- Less mature ecosystem (as of Feb 2026)
- Documentation/community smaller
- Less proven for complex reasoning

### OpenAI

**Products:**
- **Canvas** - Interactive code editing (like Artifacts)
- **GPT-5.x / o3 agents** - Strong reasoning models
- **Codex CLI** - Available via Cursor, Replit, etc.

**Strengths:**
- Most polished ecosystem
- Strong general-purpose reasoning
- Wide integration (IDEs, tools)

**Limitations:**
- Canvas: Prototype-focused, web UI
- Autonomous agents less transparent
- Mid-tier pricing

---

## Use Case: Personal AI Infrastructure

**Project:** Build production-ready AI agent system with:
- Daily geopolitics curator (RSS + LLM filtering)
- Cost tracking & monitoring
- Automated delivery (Telegram)
- Credential security (keychain → production secrets)
- Future: Neo4j context graph, Postgres research storage
- Target: Mac Mini production deployment (April 2026)

**Requirements:**
1. **Production-grade** (not prototypes)
2. **Multi-file projects** (not single scripts)
3. **Version control** (git integration)
4. **Learning opportunity** (understand how it works)
5. **Server deployment** (headless, automated)
6. **Cost-conscious** (but quality matters during build)

---

## Evaluation Framework

| Criteria | Weight | Claude Artifacts | Claude Code | Grok Code | OpenClaw + Sonnet API |
|----------|--------|------------------|-------------|-----------|------------------------|
| **Transparency** | High | Low (black box) | Medium | Medium | **High** ✓ |
| **Learning Value** | High | Low (auto-magic) | Low | Medium | **High** ✓ |
| **Production Ready** | Critical | Low (prototypes) | High | Medium | **High** ✓ |
| **Multi-file Support** | Critical | Low | High | High | **High** ✓ |
| **Git Integration** | High | None | High | Medium | **High** ✓ |
| **Server Deployment** | Critical | None (web only) | High | High | **High** ✓ |
| **Cost (Build Phase)** | Medium | N/A | High? | **Low** | Medium |
| **Cost (Maintenance)** | High | N/A | Medium | **Low** | Tunable ✓ |
| **Code Quality** | High | Good | **Excellent** | Good | **Excellent** ✓ |
| **Explanation Quality** | High | Good | Medium | Medium | **Excellent** ✓ |
| **Community/Docs** | Medium | **High** | Medium | Low | **High** ✓ |

---

## Decision Rationale

### Why OpenClaw + Sonnet 4.5 API

**1. Transparency & Control**
- Every command visible before execution
- Approve/reject changes
- Understand what's happening
- No "magic" autonomous behavior

**Example:** When building credential security:
```
Mini-moi: "Let me create credential_manager.py with keychain support"
[Shows code before writing]
Me: "Looks good, proceed"
[File created, committed to git]
```

vs. Autonomous agent:
```
Agent: "Credential system implemented"
[20 files changed, unclear what happened]
```

**2. Educational Value**
- Learning terminal commands (`head`, `tail`, `grep`, `find`)
- Understanding file structures (`.jsonl` parsing)
- Git workflow visible
- Architecture decisions explained

**ROI:** Not just building a tool, building **knowledge** for future projects.

**3. Production-First Design**
- Server-deployable (Mac Mini target)
- Version controlled (git history)
- Documented (markdown files in repo)
- Testable (can run commands manually)

**Artifacts/Canvas = great for demos, terrible for production infrastructure.**

**4. Cost Strategy: Invest Now, Optimize Later**

**Build Phase (Feb-April 2026):**
- Sonnet 4.5 for everything: ~$2-3/day
- Investment: ~$150-200 total
- Quality code, fewer mistakes, better explanations

**Production Phase (April+):**
- Switch to tiered approach:
  - Haiku for curator/routine tasks: ~$0.10/day
  - Sonnet for complex work only: ~$0.50/day
  - Target: ~$1/day maintenance vs ~$3/day build

**Why not start cheap?**
- Mistakes with cheaper models cost more time
- Learning requires quality explanations
- Foundation must be solid
- $150 investment = tiny for professional infrastructure

**5. Flexibility**
- Not locked into web UI
- Can switch models anytime
- Can integrate other tools later
- Portable (works on any machine with OpenClaw)

---

## What We're NOT Using (And Why)

### Claude Artifacts
**Why not:**
- Web UI only (can't deploy to server)
- Single-file focus (we need multi-file projects)
- Prototype-oriented (we need production code)

**Good for:**
- Quick demos
- Interactive web apps
- Non-technical users
- Teaching coding concepts

**Not good for:**
- Production infrastructure
- Server deployment
- Multi-component systems

### Claude Code (Autonomous Agent)
**Why not:**
- Black box behavior (harder to learn from)
- Less control over changes
- Overkill for our use case
- We already have equivalent via OpenClaw tools

**Good for:**
- Large refactors
- Legacy codebase updates
- Speed over understanding

**Not good for:**
- Learning projects
- When you want to understand decisions
- When transparency matters

### Grok Code
**Why not (yet):**
- Less mature (early 2026)
- Smaller community/docs
- Unknown production reliability

**Could revisit:**
- Phase 5 (production optimization)
- For cost reduction on routine tasks
- When ecosystem matures

---

## Cost Analysis

### Current Spend (Build Phase)

**Week of Feb 10-16, 2026:**
- 462 API calls
- 24M tokens
- **$19.35 total** (~$2.76/day)

**Breakdown:**
- 80% conversations/teaching (quality explanations)
- 15% building/coding (file edits, debugging)
- 5% curator cron jobs (minimal)

**Projected Build Phase (8 weeks, Feb-April):**
- ~$2.50/day × 56 days = **~$140 total**

### Post-Production (Optimized)

**Maintenance Phase (April+):**
- Daily curator (Haiku): $0.10/day
- Weekly maintenance (Sonnet): $0.50/week = $0.07/day
- Occasional deep work (Sonnet): $2/month = $0.07/day
- **Target: ~$0.25/day** = $7.50/month

**Savings:** 90% reduction from build phase

---

## Lessons Learned

### 1. Autonomous ≠ Better for Learning
**Observation:** Tools that "do everything" teach you nothing.

**Example:** 
- Autonomous agent: "Database integrated" ✓
- Our approach: "Here's how Neo4j queries work, here's the connection code, here's why we structured it this way"

**Impact:** Can now build similar systems independently.

### 2. Prototype Tools Don't Scale
**Observation:** Artifacts/Canvas great for demos, terrible for production.

**Example:**
- Artifacts: Single HTML file with embedded JS
- Our approach: Multi-file structure, version control, deployment scripts, security

**Impact:** Built infrastructure that will run on a server for years.

### 3. Cost Optimization Has Phases
**Observation:** Cheap during building = expensive during fixing.

**Math:**
- Save $1/day on cheaper model = $56 over 8 weeks
- Cost to fix mistakes from cheaper model = 5+ hours @ your time value
- Quality explanations = learn faster = worth $150 investment

**Impact:** Sonnet 4.5 was right choice for build, Haiku for maintenance.

### 4. Transparency Enables Trust
**Observation:** When you see every decision, you trust the system.

**Example:**
- Credential security: Saw keychain integration, reviewed code, understood trade-offs
- vs. "Your credentials are secure" (how? where? what method?)

**Impact:** Comfortable deploying to production because we built it together.

---

## Interview Talking Points

**"Tell me about a technical decision you made recently"**

> "When building AI infrastructure in early 2026, I evaluated Claude Artifacts, Claude Code, and Grok against a traditional API-based approach. Despite the hype around autonomous agents, I chose transparency and production-readiness over convenience.
>
> The autonomous tools were faster for prototypes but provided zero insight into *how* things worked. Since this was infrastructure I'd maintain for years, I optimized for understanding and control.
>
> Cost was $2.50/day during build (~$150 total) but I learned terminal commands, system architecture, and security best practices. Post-production, I optimized to $0.25/day by switching to cheaper models for routine tasks.
>
> Result: Production-ready infrastructure, reusable knowledge, and a 90% cost reduction after launch."

**"How do you approach AI tool selection?"**

> "I evaluate based on the job to be done, not the marketing hype. For production infrastructure:
>
> 1. **Transparency:** Can I understand and audit what's happening?
> 2. **Production-readiness:** Will this work on a server in 6 months?
> 3. **Learning value:** Am I building knowledge or just dependency?
> 4. **Cost strategy:** Invest during build, optimize in production.
>
> Autonomous agents are great for speed, but terrible teachers. For infrastructure projects, I prioritize understanding over convenience."

**"What's your experience with modern AI tools?"**

> "I've built production systems with Claude Sonnet 4.5, evaluated Grok and GPT-5.x alternatives, and worked extensively with OpenClaw framework. I understand when to use autonomous agents (large refactors) vs. supervised assistance (learning new domains) vs. cost-optimized models (production maintenance).
>
> Key insight: The right tool depends on your goal. Prototyping? Use Artifacts. Learning? Use API with full visibility. Production at scale? Use tiered model approach (cheap for routine, premium for complex)."

---

## Future Considerations

### When to Revisit This Decision

**Scenarios that might change our approach:**

1. **Grok Code matures** (mid-2026)
   - If cost drops to $0.05/day for equivalent quality
   - Revisit for Phase 5 maintenance mode

2. **OpenAI releases production-grade agent framework**
   - If Canvas/agents become server-deployable
   - Evaluate for complex refactoring tasks

3. **Anthropic releases Claude Code API**
   - If autonomous behavior becomes transparent/auditable
   - Consider for multi-file updates

4. **Cost constraints tighten**
   - If running on corporate budget vs. personal
   - Switch to Haiku earlier (Phase 3 vs. Phase 5)

### What We'd Do Differently

**If starting over:**
- Same approach (validated by results)
- Maybe document decisions earlier (this file!)
- Test Grok sooner for cost comparison

**What we got right:**
- Quality over speed during build
- Transparency over automation
- Learning over magic
- Production-first architecture

---

## Conclusion

**The right tool depends on your goal:**
- **Prototyping?** Use Claude Artifacts or Canvas
- **Learning?** Use API-based approach with full visibility
- **Speed?** Use autonomous agents (Claude Code, Grok Code)
- **Production infrastructure?** Use transparent, auditable, deployable approach

**We chose transparency and learning for infrastructure that will run for years.**

**Cost:** $150 build investment → $7.50/month maintenance  
**Outcome:** Production-ready system + transferable knowledge  
**ROI:** Both the infrastructure AND the understanding of how to build it

---

_This decision log demonstrates strategic thinking about tool selection, cost-benefit analysis, and production-oriented development practices. It shows the ability to evaluate hype vs. reality and optimize for long-term value over short-term convenience._

_For employment discussions: This represents the kind of architectural decision-making and cost optimization thinking applied to AI agent infrastructure projects._

---

**Related Documents:**
- [PROJECT_ROADMAP.md](./PROJECT_ROADMAP.md) - Overall project vision
- [ARCHITECTURE.md](./ARCHITECTURE.md) - System design decisions
- [PRODUCTION_SECURITY.md](./PRODUCTION_SECURITY.md) - Security migration plan
- [CURATOR_ROADMAP.md](./CURATOR_ROADMAP.md) - Feature evolution thinking

**Repository:** [github.com/robertvanstedum/personal-ai-agents](https://github.com/robertvanstedum/personal-ai-agents)
