# Companion: the local model's first job is the Vault

**Companion to:** MiniMoi-Private-Local-Hybrid-Intelligence-2026-10-14.md (Codex, v0.1)
**Date:** 14 October 2026 (America/Chicago)
**Version:** 0.1
**Author:** Claude Chat, at Robert's request, reviewing Codex's brief and answering its questions for Claude
**Status:** review and companion direction; not a purchase, build authorization or change to MiniMoi 1.2 scope. Dated topic record, maintained beside its predecessor per the brief's section 10.

## Abstract

Codex's brief sets the direction: substantial local work including coding, two or three hosted models kept for escalation and independent perspective, no fixed local/cloud quota, the Vault as the lasting asset and models as replaceable engines, functionality before infrastructure. This companion accepts that direction and adds one thing Robert raised after reading it: **the first substantial local workload should be the Vault itself, not coding.** A local model passing over the Vault (tagging, reflection drafts, embeddings, the gold-set questions) is the cheapest bounded comparison available, it runs on hardware we already have, it never sends private material anywhere, and it produces the evidence that decides every later question about models and hardware. The companion also answers the five questions the brief poses for Claude, lists where the current Vault design quietly assumes a vendor, and proposes the comparison method that lets local and hosted models be compared over time on the same questions.

## 1. Review of the brief

**Agreed, no change:**
- The three-layer separation (knowledge and continuity; execution; infrastructure) is the right frame and matches the vision document's rule that files are the store and everything else is replaceable.
- "Privacy precedes model selection": a local-only designation cannot be overridden by a quality or cost router. This should be enforced in the model gateway, which already exists, not in prompts.
- "Measure accepted work, not calls or tokens." A local patch that costs two hours of repair saved nothing.
- The K2.5/K3 correction and the refusal to treat exploratory hardware figures as quotes.
- Quarterly cadence, dated snapshots, predecessor preserved unchanged.

**Three additions:**

1. **Order the comparisons by cost of being wrong.** The brief proposes a coding comparison as the first bounded test. A Vault comparison is cheaper, safer and more informative first: tagging and reflection over records we already have, judged against a gold set Robert confirms, on the laptop, with no hosted call needed to run it. Coding comparisons follow once a local runtime is proven on that.

2. **Hardware is already an open thread.** The 8 October discussion (Omarchy on a 128 GB Strix Halo machine versus a MacBook Pro M5 Pro at 64 GB) is the same decision this brief defers. Keep it deferred, but link the two records so the next snapshot does not rediscover the options. The constraint that governs both is unchanged: the current 8 GB MacBook can run an embedding model and a small quantized model for tagging and summaries, one heavy thing at a time, and nothing larger.

3. **Local models need the same honesty fields as everything else.** The vision document already separates saved, backed up, synced and searchable. Add one more for anything a local model produced: which model, which quantization, on which runtime, with what prompt version. A tag or a reflection without that provenance cannot be compared over time, and comparison over time is the whole point.

## 2. The local model's first job: a pass over the Vault

Robert's suggestion, made into a plan. Four local workloads over the Vault, in order of how little they risk and how much they teach:

| Workload | What the local model does | Judged against | What it teaches |
|---|---|---|---|
| **Embeddings** | Produce the vectors for the semantic index (question 2 in the vision document's retrieval section) | The gold set; recall of known passages | Whether a local embedding model is good enough for "what do I know about X" on Robert's material |
| **Tagging and classification** | Propose topic, domain, kind and time-validity tags on raw-tier records | Robert's confirmed tags on a sample; the hosted model's tags on the same sample | Whether the storage and tags are right (the vision document's point that retrieval teaches storage) |
| **Reflection drafts** | Draft the weekly "what happened, what recurred, what is open" (question 3) and candidate promotions from raw to curated, with citations | Robert's review; nothing becomes curated without approval | Whether a local model can do the consolidation job the field calls reflection, with provenance intact |
| **Gold-set answering** | Answer the twenty gold-set questions with citations to exact passages and explicit gaps | Robert-confirmed answers; the hosted model's answers on the same questions | The direct local-versus-hosted comparison on the one metric that measures the promise |

Each run records model, quantization, runtime, prompt version, date, and the result against the gold set. Repeat monthly. Over a quarter this is the dataset that answers "how far behind is local on my material," which is the only version of the question that matters for MiniMoi.

**What this does not do.** It does not send private material to a hosted model that was not already authorized to see it; the hosted side of each comparison runs only on records already in that model's permitted scope. It does not change capture grants. It does not replace the existing Ask path; it sits beside it as the experiment the vision document's section 7a already names.

## 3. Answers to the brief's questions for Claude

**Where does the Vault design accidentally assume a vendor?** Four places, all fixable without redesign:
- The intent-hash and provenance fields record `{provider, requested, actual}` for the model; they must also record quantization and runtime, or local runs are indistinguishable from each other.
- The existing Ask path's embeddings were produced by one model; re-embedding with a different model is a rebuild of a derived index, which the architecture permits, but the index must carry the embedding model's identity so a mixed index is impossible by accident.
- Capture adapters read vendor session files (Claude Code, Codex). That is a dependency on vendor formats, not on vendor models; the mitigation is already stated (thin adapters, exports as fallback).
- The scrubbers and attribution rules were tested on hosted-model transcripts. A local model's output needs the same attribution tests, because "agent-authored, not Robert" must hold whoever the agent is.

**Can a new local model use the same permitted knowledge and preserve attribution?** Yes, if it is a client of the Vault through the same interface: authorized read by scope, citations to retained ranges, output recorded as agent-authored with model provenance. The vision document's rule that retrieved content is data, never instructions, applies to it unchanged. The test is the gold set, run through the same renderer that MC and CoS use.

**What must never leave local control?** The Vault's originals and retained editions; the keys and recovery sheet; anything designated Private or local-only; embeddings and summaries of those records (the brief is right that these carry the information). Hosted models receive deliberately authorized excerpts, never the corpus, and the model gateway refuses a hosted route for a local-only designation rather than falling back.

**Which real coding task would make a fair comparison?** One we have already done with a hosted agent, with the acceptance held separately: a bounded Workshop journal fix of the kind the October 8 review reproduced (a torn-tail defect with a known test), or a domain export adapter with a dry-run contract. Same brief, isolated checkouts, same tools and resource limits, correctness and rework measured before anyone judges the explanation. Not RVS Associates or anything on the career path; those have a deadline and an audience.

**Which capabilities are missing from the agent tools rather than the model?** Most of them, on today's evidence. A verified native message route for each agent, a deterministic dispatcher, the inbox drop-off in daily use, the Vault consumer wired into MC and CoS, the coverage matrix with every domain listed. None of these depends on which model answers. A local model dropped into the current harness would inherit the same friction. That is an argument for finishing the harness before judging any model, local or hosted.

## 4. What this changes in the plan, and what it does not

**Changes:** the vision document's retrieval section (7a) gains the local-model pass as its first experiment, ahead of the Graphiti/mem0 index comparison or alongside it, since embeddings are shared work. The gold set is written once and used for both.

**Does not change:** MiniMoi 1.2 scope, the Vault backup and custody order, the career track, the hardware deferral. No model is downloaded, no job scheduled, no hardware priced by this document.

## 5. Open for Robert

1. Confirm the Vault pass as the first local workload, ahead of a coding comparison.
2. Which small model to try first on the 8 GB MacBook for tagging and reflection drafts (a 4B-class quantized model is the realistic ceiling until new hardware; the embedding model already running is the first candidate for the embedding row).
3. Whether the monthly local-versus-hosted gold-set run is reported in the morning brief or only in the quarterly snapshot.

## Snapshot record

- **2026-10-14 — v0.1:** companion written against Codex's v0.1; Vault pass proposed as the first local workload; five questions answered; vendor assumptions in the Vault design listed; hardware thread cross-referenced to the 8 October discussion.
- **Not performed:** model download, local benchmark, scheduling, hardware pricing, change to capture grants or release scope.
