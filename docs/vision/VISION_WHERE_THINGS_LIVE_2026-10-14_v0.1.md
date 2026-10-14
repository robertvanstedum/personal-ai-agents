---
title: Where things live — code, capabilities, working documents, the Vault and the archive
version: v0.1 working draft
date: 2026-10-14
owner: Robert van Stedum
authors: Claude Chat (draft from Robert's 14 Oct direction); Claude Code to add facts and gaps; Codex and Grok to review
status: public-repo vision document in progress; nothing here authorizes a data move
tone: public README register (matter-of-fact, personal, no sales language)
---

# Where things live

mini-moi is built so that one person's thinking, learning and work accumulate over time and stay his. This document says where each kind of thing lives, for how long, who can see it, and why. It is meant to be read by someone looking at the public repository and by the agents that work inside mini-moi, so it has to be true for both.

## 1. The rule

**The capability is public. The instance is private.**

mini-moi does company research, presentations, resume versioning, real prototypes, daily briefings and language practice, and it keeps the thinking behind all of them. That is the point of it, and it should be visible. What stays private is the particular: which company, which interviewer, which draft, which article I rated and what I wrote about it.

So every capability in mini-moi has two parts:

- a **public method and one worked example** with example data, so the code is runnable and the capability can be seen working, and
- **private instances** that use the same code on real material.

The worked examples use invented names of the Acme Co and John Smith kind. The private instances use real ones. Nothing moves from private to public except by my deliberate choice, one item at a time, with the names and data replaced at that moment.

**Coverage is a policy and a fact, and they are reported separately.** The policy is that everything I author or choose, in every domain, goes to the Vault and is visible to me and to the agents within their scope. The fact is what has an accepted capture path today. A domain without one is a visible gap on the coverage matrix, never a silent omission. "All domains" and "forever" describe the policy; the matrix says what is true.

## 2. Five kinds of things

| Kind | What it is | Where it lives | How long | Who sees it |
|---|---|---|---|---|
| **Code and structure** | mini-moi's code; Planning Studio's rules, record formats, templates and tools | Public repository on GitHub | As long as the project | Everyone |
| **Capability examples** | One worked example per capability, on example data: an Acme Co deck, a fictional-company prototype, a John Smith resume set, a career-topic briefing on a public subject | Public repository, beside the code that produces it | Maintained with the code | Everyone |
| **Working documents** | Plans, drafts, research, resume versions, presentation versions, Planning Studio content | A private data repository (data only, never code) | Editable, with history | Me and my agents |
| **The Vault** | My comments and thinking, from every domain: conversations with agents, ratings and notes, writing exercises and corrections, flags, Board notes, decisions and why they changed | The memory shelf, private, encrypted, backed up off the Mac | Forever | Me and my agents, by scope |
| **The archive** | Reference material: articles, reports, exports, anything I did not write | Private archive, prunable | Until pruned | Me and my agents |

Two of these are the ones to get right first: the Vault and the archive, because the split between them is what makes "forever" affordable.

## 3. Vault versus archive

The Vault keeps what I made or decided. The archive keeps what I read. The Vault is forever; the archive can be pruned.

A comment in the Vault often points at something in the archive: a rating on an article, a note on a report. When that article is pruned, the comment must still make sense. So the Vault keeps, with every comment, a **citation, a short excerpt and a fingerprint** of what it pointed at. The article can go; the reason I cared about it stays.

Pruning rules for the archive are simple and mine: by age or by size, never-referenced items first, and nothing referenced by a Vault comment is pruned before its excerpt and fingerprint are saved. The rule is set once and run by software.

## 4. What counts as "my comment"

Anything I authored or chose, in any domain. Not which domain it happened in, but whether I made it:

- a rating, a note, a flag on a Curator article
- a writing exercise and its correction in Mein Deutsch or Meu Português
- a conversation with any agent, in any app
- a Board note, a follow-up question, a decision and the reasons it changed

The domains stay independent. Each exports what I authored, in its own format, on a schedule, through the Vault's approved-source path with a dry run and exclusions, the same way the agents' sessions are captured today. No domain needs to know the Vault exists.

## 5. What "forever" means

The Vault never overwrites. A correction is a new edition that points at the old one. Removing something by choice leaves a tombstone with the item's fingerprint, which every replica and every backup made from then on carries. Forever means the record of what was there, and that I chose to remove it, survives. It does not mean I cannot fix a mistake or take something out.

Two different things happen when I delete, and the Vault keeps them apart. The first is that the deleted record must not come back: a tombstone cannot appear inside a backup taken before it existed, so a restore from an older backup replays the tombstones recorded since, and the record stays gone. The second is that the content itself is physically purged: that only happens when every backup that still holds it has aged out of retention, or when I ask for an explicit purge that rewrites those backups. The tombstone is a promise about the first; the retention period, stated in the custody rules, is the honest answer about the second.

An explicit Private or off-record instruction keeps its established meaning and is the only exception to capture.

## 6. Codenames

The data is private, so codenames are not needed for storage. They are needed wherever private material could leak into something shared: a public worked example, a search tool, a screenshot, the public tour. The replacement happens at the moment an item is published, not before.

## 7. The working-documents repository and the Vault

Both, with a clear split. The private data repository is where documents are edited, with history and review. The Vault captures their editions and the discussion around them. The repository is a convenience; the Vault is the one that must outlive mini-moi.

## 7a. Retrieval and insight: using the Vault, not only keeping it

Storage without retrieval is a filing cabinet. The Vault is used through a light query and insight layer, and that layer is how we learn whether the storage and tags are right. Retrieval teaches storage, not the other way round.

Three kinds of question, in order of how often I ask them:

1. **What did we decide about X, and why?** Provenance: the current decision, what it replaced, the sources, with citations to exact passages and explicit gaps. The existing Ask path with citations serves this.
2. **What do I know about X?** Semantic, across domains, returning raw passages with provenance, never a summary in place of them.
3. **What happened this week?** Temporal and aggregate: what changed, what recurred, what is open. A scheduled reflection job proposes this; I review it; nothing becomes a curated record without my approval.

**Borrowing, not building.** The dedicated memory layers (mem0, Zep/Graphiti, Letta) are built for retrieval and may well do it better than mini-moi will on its own; that is the claim to test, not a finding. None is installed. If one is adopted it would be a rebuildable index over the Vault's files, never the store, self-hosted, with no cloud mode. The proposed first candidate is Graphiti, because its two time axes (when a fact was true, when it was recorded) match the Vault's decisions and supersessions; mem0 is the simpler alternative. Letta's idea of a budgeted working set the agent curates is worth borrowing; its runtime is not. The comparison is a bounded experiment in the Prototype Lab style against the existing Ask on the same questions; the existing Ask is the baseline and stays unless a candidate beats it with citations. This is decision 6 in section 9, still open.

**How it learns.** A gold set of about twenty questions in my own words, with answers I confirm, re-run monthly. A query log recording each question, what came back and whether I used it. Together these show which tags matter, which sources are missing, and whether an index earns its place. The use week is the first run.

**What this is not.** Not retrieval of raw-tier material by default (curated first, raw by exact reference), not an agent reading the whole Vault into every turn, and not a model summary that can remove a gap or add an approval.

## 8. Usable next week: a proposed target

The public claim and the private use have to be true at the same time. The list below is the proposed target for the week of 19 October, pending the decisions in section 9 and Codex's 1.2 run; it is not a commitment and nothing on it is done yet:

- The Vault backup runs on a schedule and has been restored once.
- One domain (Curator) exports my ratings and notes to the Vault through a dry run, with the coverage matrix showing every domain as full, partial or none.
- The private data repository is chosen and holds the career working documents and Planning Studio content, once Codex's 1.2 run is finished.
- One capability has its public worked example in the repository on example data: the resume method with a John Smith set, or the Acme Co deck, whichever is ready first.
- The first twenty gold-set questions are written in my words, and the existing Ask path answers them with citations; the Graphiti-or-mem0 comparison is at most set up as a bounded experiment, with nothing installed until decision 6 is taken.

## 9. Decisions still open

1. Adopt "capability public, instance private" as the rule for every capability. (Recommended: yes.)
2. One private data repository: reuse `mini-moi-private` or create a data-only one. (Recommended: a data-only one, so the existing nightly code-adjacent syncs do not mix with working documents.)
3. Must every capability have its public worked example before it is described in the README? (Recommended: yes; that is what makes the claim honest.)
4. Archive pruning thresholds: age and size, set once.
5. Which domain exports first (Curator is the recommendation because its notes are the simplest shape).
6. First retrieval experiment, and whether to run one at all: Graphiti (temporal facts) or mem0 (simpler), as a rebuildable index over the shelf, self-hosted, compared against the existing Ask. (Recommended: Graphiti first, mem0 as fallback; the existing Ask stays the baseline either way.)

## 10. Facts to add (Claude Code)

- Current Vault size, record count and approved sources, dated.
- Where each domain keeps owner-authored items today, and in what format, so the export per domain can be scoped.
- What `mini-moi-private` currently receives nightly, so decision 2 is made on facts.
- Current backup state for the Vault, Curator and the language domains, with the last verified restore.
- Any place private material already sits in the public repository (uncommitted Planning Studio content, career references) that this model would move.

## 11. The wide lens: who else is doing this

mini-moi is one person's answer to a question the field is now asking in public: whose memory is it, and what survives when the tools change. Nobody is building the whole of it, but several projects build parts well, and this document is honest about both.

**Assistants.** OpenClaw is the breakout open-source personal agent of 2026: always on, reachable through messaging, skills, voice, scheduled jobs, a memory system. mini-moi uses it as its assistant layer rather than competing with it. Khoj is a self-hostable "second brain" for search and chat over your own documents, with custom agents and automations; it is the nearest thing to Curator plus Ask over the Vault. Vellum, Hermes, Jan and Leon cover the same ground with different emphases (credential isolation, local models, offline).

**Owned knowledge.** Obsidian, plain Markdown in a folder you own, with official agent skills since January 2026 and MCP servers, is where most people now point an agent when they want "memory." The Vault shares its file principle. The difference, which critics of the Obsidian-as-memory idea make correctly, is that a folder of notes is a knowledge base, not a record: it has no provenance, no editions, no capture of what the agents did and why. The Vault adds those.

**Agent memory infrastructure.** Letta, mem0 and Zep are purpose-built memory layers for agents, with better retrieval than mini-moi has. They are the store, and they are for developers. mini-moi's rule is that files are the store and any of these may be a rebuildable index over them.

**Total capture.** Screenpipe, Limitless (acquired by Meta) and Microsoft Recall record everything you see and hear. mini-moi chooses the opposite: capture by designation, with provenance, and nothing recorded that was not chosen. An encrypted Limitless store was shown to be extractable in March 2026; that is the argument for owner-held keys and a record you can read without the vendor.

**The standards push.** Stanford HAI, with the Gates Foundation, Mozilla and AWS, published a report in March 2026 calling for a user-governed layer of personal memory that moves across tools. MacPaw proposed Portable Memory in July: an open format, plain files, verifiable deletion. Vana shipped a personal data server with an MCP endpoint. The Vault is built to the shape these describe, before the standard exists.

**What is distinct here**, put plainly: a provenance-preserving record across several vendors' agents and the owner's own domains; a build loop in which agents from different vendors build and review production code with attributed receipts; domains that are products for the owner (briefings, language practice); and the rule that the record outlives the platform. No single project combines these. If mini-moi stopped, the nearest substitute would be OpenClaw plus Obsidian plus Khoj, which covers much of the personal side and none of the build loop or the provenance.

**The honest counterpoint.** Retrieval is not where mini-moi should spend effort; the dedicated memory layers do it better and can sit on top of the files. And if Obsidian's agent skills grow a provenance and edition model, the Vault's file layer could become an Obsidian vault without loss. Both are reasons to keep the files as the authority and everything else replaceable.

## 12. Bibliography

Five with a short abstract; the rest listed.

1. **Stanford Digital Economy Lab, with the Gates Foundation, Mozilla and AWS. "The Future of Personal AI: Portable and Persistent Personal Memory through a Unified Human Context Protocol." March 2026.** Argues that personalization now depends on accurate personal context, that this context is consolidating inside proprietary "cognitive layers," and that the next foundation is a user-governed memory layer that persists across interactions, moves across tools and is shared selectively by role, consent and purpose. Proposes a Human Context Protocol as the interoperability layer and an agenda for standards, evaluation and policy. The Vault is this layer, for one person. https://digitaleconomy.stanford.edu/publication/the-future-of-personal-ai-portable-and-persistent-personal-memory-through-a-unified-human-context-protocol/

2. **Kryvoblotskyi, Stulova, Hamolia (MacPaw Research). "Memory Belongs to the User: Portable Memory, an Open Standard Proposal for Cross-Vendor AI Memory." July 2026.** Proposes an open, vendor-neutral format for AI memory: a self-contained set of plain files the user holds, lossless, local, with verifiable deletion as the integrity guarantee, and open-source reference implementations. The closest published design to the Vault's file and tombstone rules. https://research.macpaw.com/publications/portable-memory

3. **Ink & Switch. "Local-first software: You own your data, in spite of the cloud." 2019.** The essay that named the principle mini-moi's Workshop follows: software whose primary copy lives on the user's device, works offline, syncs when connected, and keeps the data usable if the vendor disappears. Its seven ideals (fast, multi-device, offline, collaboration, longevity, privacy, user control) are a useful checklist against the five kinds of things in this document. https://www.inkandswitch.com/local-first/

4. **Hashimoto, M. "Agent = Model + Harness." February 2026, and the harness-engineering writing it started.** The framing that everything around the model (tools, context loading, permissions, memory, review) is where reliability and advantage live, and that every agent mistake should become a rule or a tool so it cannot repeat. Guild's spec, build, review and improve loop is harness engineering by another name. Overview: https://learnagentic.substack.com/p/what-is-harness-engineering

5. **Margalit et al. "Governed Shared Memory for Multi-Agent LLM Systems." 2026 (as summarized by mem0's library).** Names four failure modes when several agents share a store: unauthorized leakage, stale propagation, contradiction persistence and provenance collapse. mini-moi's scope tags, freshness fields, supersedes links and source hashes answer them one for one. https://mem0.ai/library/agent-memory/multi-agent-memory-systems

Also consulted:
- OpenClaw, open-source personal AI agent. https://github.com/openclaw/openclaw
- Khoj, self-hostable AI second brain (AGPL). https://github.com/khoj-ai/khoj
- Obsidian as AI memory, and its limits. https://unabyss.com/blog/obsidian-as-ai-memory and https://limitededitionjonathan.substack.com/p/stop-calling-it-memory-the-problem
- Screenpipe. "Personal AI Memory in 2026: Who Owns What You Remember?" https://screenpi.pe/blog/personal-ai-memory-2026
- Vana. "Own your AI memory." July 2026. https://vana.org/blog/own-your-ai-memory
- Vellum. "10 Best Local AI Assistants in 2026." https://www.vellum.ai/blog/best-local-ai-assistants
- Atlan. "Agent Memory Architectures: Patterns and Trade-offs (2026)." https://atlan.com/know/agent-memory-architectures/
- Tyk. "Agent protocols: A complete guide to MCP, A2A, and ACP." https://tyk.io/learning-center/agent-protocols-a-complete-guide-to-mcp-a2a-and-acp/
- Mergify. "How Agents Work With Parallel Agents." June 2026. https://mergify.com/blog/how-agents-work-with-parallel-agents
- Claude Chat. "mini-moi and the field." MINIMOI_DIRECTION_WHITEPAPER_2026-10-09_v0.1.md (this repository), the longer version of section 11.

---

## Review comments

_(append below, newest last: `### YYYY-MM-DD — <reviewer>`)_

### 2026-10-14 — Grok Chat (advisory; full text in FEEDBACK_TO_CLAUDE_CHAT_VISION_WHERE_THINGS_LIVE_2026-10-14.md)
Verdict: the rule is right; keep it. Layers are distinct and the "usable next week" list is the right size. Five corrections: (1) state the coverage rule up front, policy versus delivered, gaps visible, so "forever" and "all domains" are not read as already true; (2) keep the private data repository data-only, do not reuse a repository receiving mixed nightly copies; (3) require the public worked example before a capability is described in the README; (4) archive pruning stays mechanical, no manual importance step; (5) codenames only at the publication boundary. Also: the Vault is not a backup of the private repository and not a showcase; do not edit documents in the Vault; do not expand capture before the backup restore and one simple export are proven; no multi-person layer.

### 2026-10-14 — Claude Chat (disposition of Grok's review)
(1) added to section 1 as the coverage paragraph. (2), (3), (4), (5) are already the document's positions, written as recommendations in section 9 pending Robert's decision; once he decides, sections 3, 6, 7 and 9 are reworded as rules. The "what not to do" points are consistent with sections 5, 7 and 8 and need no change. No disagreement.

### 2026-10-14 — Codex (second review, across the three October documents)
Two points for this document: keep retrieval choices provisional (Graphiti/mem0 are candidates to compare against Ask, not installed components or proven improvements; "usable next week" is a proposed target); and distinguish, for deletion, preventing a deleted record from returning on restore from physically purging its contents through backup retention or an explicit mechanism, since a tombstone cannot appear in an older immutable backup.

### 2026-10-14 — Claude Chat (disposition)
Both applied: section 7a reworded to candidates and a test, section 8 retitled as a proposed target, section 5 given the restore-versus-purge paragraph. Version stays v0.1 until Robert's section 9 decisions produce v0.2.
