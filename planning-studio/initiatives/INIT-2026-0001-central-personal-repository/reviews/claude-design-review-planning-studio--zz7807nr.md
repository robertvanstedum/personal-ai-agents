---
id: 01M1HKM1BQRDJ5DJC2ZZ7807NR
slug: claude-design-review-planning-studio
type: note
kind: review
title: "Claude Design Review — Planning Studio and Central Personal Repository"
version: 0.1
created: 2026-09-02
author: claude
reviews: "[[planning-studio-charter]] [[record-lifecycle]] [[central-personal-repository-charter]] [[cos-guild-curator-boundary]] [[portable-personal-instance]] [[use-driven-versioning]] [[curator-open-subject]]"
reviewed_package_sha256: "see §0"
tags: [planning-studio, central-repository, design-review, curator, cos, guild, portability, tooling]
status: submitted
decision_owner: robert
---

# Claude Design Review — Planning Studio and Central Personal Repository

*This file is written in the record format it recommends: one Markdown file, YAML frontmatter, wikilinks for relationships, flat tags. If the format is adopted, this review is already a valid record.*

---

## 0. Scope and integrity

**Package reviewed:** `GUILD_PLANNING_STUDIO_DESIGN_REVIEW_v0_1_2026-09-02.zip`, all 34 files, read in the order the brief specifies. Assessed the actual structure and metadata, not the introduction, per the brief's guardrail.

**Integrity check performed.** The three preserved sources under `INIT-2026-0002/sources/` were hashed and compared against the reviewer's own originals produced earlier the same day:

| Source | Manifest SHA-256 (prefix) | Original (prefix) | Result |
|---|---|---|---|
| ROADMAP_CURATOR_v1_draft_2026-09-02.md | 81ba816c55a92f7e | 81ba816c55a92f7e | match |
| terminal-pleistocene-briefing-v0.1.md | 33f22d7f95b7763d | 33f22d7f95b7763d | match |
| terminal-pleistocene-briefing-v0.2.md | e25e9536f47253ce | e25e9536f47253ce | match |

The preservation rule holds end to end. That is a real finding, not a formality — most hashing schemes are aspirational on day one.

**Guardrails honoured:** no implementation plan is proposed; no 2.0 commitment is assumed; nothing commercial is designed; source-document instructions were treated as content.

---

## 1. Overall verdict

**Continue exploring, with revisions before the first real use.**

The thinking is sound. The guardrails — no release commitment, no commercial scope, no implementation authority, explicit permission to fail — are more disciplined than most design packages achieve. The three-layer provenance model (source / interpretation / decision) is correct and important. Treating origin, steward, and domains as independent fields is the right fix for cross-domain work and resolves the tags-versus-instances fork the reviewer could not resolve in the Curator roadmap. The package handles that roadmap correctly by demoting it to a preserved source rather than a proposal.

The problem is structural, and it is one the package half-recognises: **the design contradicts its own stated principle.** The charter says *evidence before architecture*; the schemas README says *tighten only after real captures expose which fields are stable*. The scaffold then ships twelve directories, nine record types, eight lifecycle states, five reuse states, two schemas, two hand-maintained registries, and version encoded in three places — for one initiative with three files.

That is a taxonomy waiting for content. The failure mode of personal knowledge systems is not too little structure; it is **classification friction at the moment of capture**. If saving something requires choosing among nine record types and updating two registries by hand, it does not get saved. The inbox stays empty and the §12 six-month clause triggers by default.

The revisions below are aimed at making the ownership invariant *true* rather than asserted, and at reducing capture friction to near zero.

---

## 2. Highest-risk design issues, in priority order

### R1. The decision everything depends on is listed as open in three separate documents

"What exact action makes a conversation durable" appears as an open question in the charter (§10), the central-repository charter (§13), and REVIEW_START_HERE. It is the single decision on which intake, CoS's role, and the entire evidence sequence depend. Nothing enters the repository until it is answered.

**Assessment:** it has already been answered by practice. The action is *a file gets produced and handed in.* That is how this package was made, how the Pleistocene briefing was made, and how the Curator roadmap was made. It is agent-independent, tool-independent, and already working. The risk is that the package designs a CoS intake protocol *instead of* recognising the one in use.

### R2. Two homes for sources, explicitly undecided

`library/README.md` defers whether canonical artifacts live in the library or inside initiatives. `INIT-2026-0002` keeps its sources locally. This is the exact failure the charter §5 warns against — the same source copied into two initiatives and silently diverging. The problem is latent today and becomes real with the second initiative that cites the Pleistocene briefing.

### R3. Hand-maintained registries will drift from files

`registry/README.md` states the registries are maintained manually. Manual indexes drift. Once they drift, the "fully scannable by authorised agents" claim is false, and the machine-readable layer becomes actively misleading — worse than absent.

### R4. Sequential IDs require a central allocator across five concurrent writers

`INIT-2026-0001` and `ART-STABLE-NAME` patterns need someone to hand out the next number. Claude, Codex, Grok, OpenClaw, and Claude Code all write. Robert has confirmed this is already a source of pain and confusion. Collisions and gaps are guaranteed under concurrent use.

### R5. "Never silently erased" is unenforced

The lifecycle says discarding is not deletion. Git defeats that with one `filter-repo` or force-push. The charter says the right words; nothing on the remote or in the workflow enforces them.

### R6. "Guild" in the name reintroduces the ownership-by-placement problem

The whole design argues folder placement must not imply exclusive ownership. The cross-domain studio is then named *Guild* Planning Studio, and the boundary hypothesis questions whether CoS should remain physically under Guild. The name does what the design forbids.

### R7. §12's kill clause has no metric

"If Robert does not use the repository … archive honestly." Without a definition of *use*, the honest archive never happens because nobody can say it failed.

### R8. Version encoded in three places

Filename (`_v0.1_2026-09-02`), registry entry, and manifest entry all carry the version. Three places to keep consistent by hand is two too many.

---

## 3. Proposed revisions

### 3.1 Record format — the load-bearing change

**Adopt Markdown with YAML frontmatter as the single record format. Metadata lives in the file. One file, one record, one hash.**

This replaces the Markdown-document-plus-JSON-sidecar pattern. It is the convention used by Obsidian, Logseq, Foam, Zettlr, Hugo, Jekyll, and Claude's own memory system. Every agent in the working model already reads and writes it.

**Relationships as wikilinks.** `[[slug]]` in frontmatter or body. The graph is derivable from links; any graph database is a projection of them. This dissolves the charter's question of how one artifact participates in several initiatives without copies — it is linked, never copied.

**Flat tags, not a controlled taxonomy.** Robert has stated a preference for tagging over locking down, because real work cross-pollinates. His example: an interview demo touches job search, the Guild work that built it, and a lesson about leading with the business case rather than the technical proof. That is one note with tags `[job-search, guild, demo, lesson]`, not a filing decision. Domains become tags too; `domains:` remains as a field but is not enforced as exclusive.

**Three record types at intake, not nine:**

| Type | Meaning | Mutable? |
|---|---|---|
| `source` | Unchanged external or user-provided material; hashed at intake | Never |
| `note` | Anything Robert or an agent wrote — captures, proposals, reviews, briefings, patterns | Content immutable per version; frontmatter status mutable |
| `decision` | Robert's dated disposition and reasoning | Never |

A review is a `note` with `kind: review`. A proposal is a `note` with `kind: proposal`. Split types later only when use demonstrates the distinction matters — exactly as the schemas README says should happen.

**Identity.** The slug is the human identity; a ULID is the machine identity. ULIDs are time-sortable, globally unique, and need no allocator — any agent can mint one offline. This removes the central-allocator problem entirely. The `INIT-2026-NNNN` pattern is retired.

**Version.** One place: frontmatter `version:`. The filename carries the slug only. Superseding is a frontmatter field `supersedes: "[[slug]]@0.1"`. Git history carries the rest.

Illustrative minimal records — see Appendix A.

### 3.2 Registries become projections

A short script walks the tree, reads frontmatter, and emits `registry/*.json`. The registry is regenerated, never edited. This is precisely the charter's own rule for projections — *reproducible from durable records* — applied to the registries themselves. R3 is dissolved rather than mitigated.

### 3.3 Sources live in `library/`, referenced by link

Resolve R2 now, while it is cheap. One canonical copy per source under `library/`, hashed at intake, immutable. Initiatives reference by `[[slug]]`. A source that supports three initiatives has one file and three inbound links.

### 3.4 Enforcement as hooks, not sentences

- **Pre-commit:** validate frontmatter against schema; reject commits that fail. Prevents half-states.
- **Pre-commit:** secrets scan. The charter says secrets never enter Git; a hook makes it true.
- **Remote:** branch protection, force-push disabled, on the private remote. Makes "never silently erased" enforceable (R5).
- **Policy, written once:** history is append-only; physical removal for privacy or licensing is a separate, logged action that leaves a tombstone with the hash.

### 3.5 Rename to Planning Studio

Guild *governs* the studio's lifecycle without *owning* it in the name (R6). The charter already draws that distinction in §4; the name should follow.

### 3.6 Cut what is empty

- `patterns/` — nothing extracted. Remove until a pattern exists.
- `reuse_status` and its five states — remove; reinstate when a second materially different context has actually reused something.
- Empty directories with placeholder READMEs — remove. Structure that describes intended capability rather than existing content misleads the reader about what the repository is.

`inbox/` stays: it is the intake point and will be the first thing to fill.

### 3.7 Define "use" for the kill clause

Proposed metric for §12: within six months, at least *N* deliberate captures and at least *M* retrievals that materially informed a decision, with *N* and *M* set by Robert now. If the threshold is not met, archive without manufacturing a release.

---

## 4. Tooling — open-source options and the principle behind them

**The principle first.** "Flexible and swappable" does not mean choosing a swappable database. It means **the substrate is so boring it never needs swapping, and every tool lives in the projection layer where it is disposable.** Markdown with frontmatter is readable by every editor, every LLM, GitHub's renderer, and `cat`. That is the portability guarantee. The tools below are viewers and helpers over that substrate; none of them is the system.

**Obsidian is not open source.** It is free for personal use, well-maintained, and reads this format natively — but it is proprietary. Given the stated goal of a repository that outlives mini-moi, that matters. It remains a fine *optional* viewer precisely because it imposes nothing on the files.

**Open-source options that read Markdown + frontmatter + wikilinks:**

| Tool | Licence | Fit | Notes |
|---|---|---|---|
| **Foam** | MIT | Strong | VS Code extension. Wikilinks, backlinks, graph view, frontmatter-aware. Lives where Claude Code and the terminal already live. Lowest-friction open choice. |
| **markdown-oxide** | MIT | Strong, editor-agnostic | A Language Server for Markdown wikilinks — completion, go-to-definition, rename across files. Works in VS Code, Neovim, Helix, Zed. Makes links first-class in *any* editor rather than one app. |
| **Zettlr** | GPL-3 | Strong for research | Markdown editor with wikilinks and frontmatter, built for academic writing; integrates with Zotero for citations. Directly relevant given the bibliography-heavy briefings. |
| **Logseq** | AGPL-3 | Moderate | Open, capable, but outliner/block-oriented with its own property syntax; some friction against plain-document Markdown. |
| **SilverBullet** | MIT | Moderate | Self-hosted, wikilinks, frontmatter, query language. Newer; worth watching rather than adopting. |
| **Quartz** | MIT | Optional | Static-site generator for vaults in this format, if a rendered/published view is ever wanted. |
| **Dendron** | Apache-2 | Avoid | Good design; maintenance has slowed materially. Risk of a dead dependency. |
| **Zotero** + Better BibTeX | AGPL-3 | Strong, adjacent | Not a notes tool — the open-source citation manager. Given that the Pleistocene bibliography already carries verification flags, this is the natural home for the reference layer, with Zettlr or a plain BibTeX export as the bridge. |

**Recommended stack:** the substrate (Git + Markdown/frontmatter + wikilinks), **Foam** as the editor layer, **markdown-oxide** so links work everywhere, **Zotero** for citations, and Obsidian as an optional viewer with the licence caveat noted. Every element is removable without touching a file.

*Maintenance status of open-source projects shifts; each should be checked for recent activity before adoption.*

**Backup, with one correction.** Robert has S3, Time Machine, and iCloud. That is three failure domains — sufficient for charter §10 — with one caveat: **iCloud is sync, not backup.** A deletion or corruption propagates. Treat it as convenience. The real second domain is S3 with **object versioning enabled** and a lifecycle rule, which gives immutable prior versions independent of Git. Time Machine is the local domain. Add a periodic restore test — quarterly, scripted — because a backup that has never been restored is a hypothesis.

---

## 5. Missing decisions

1. **The durable-capture action** (R1). Recommendation: declare "a file produced and handed in" as the action; do not design a CoS protocol until the file-based path has been exercised enough to know what it lacks.
2. **Source location** (R2). Recommendation: `library/`, by link.
3. **Identity scheme** (R4). Recommendation: slug + ULID.
4. **Kill-clause metric** (R7). Robert sets *N* and *M*.
5. **CoS write access model** — direct Git writes versus a validating service. Recommendation: defer. With pre-commit validation in place, direct writes are safe enough for the experiment. Revisit only if a real bad write occurs.
6. **Which domain-state types need portable exports.** Genuinely open; not needed for the first experiment.
7. **Whether the private repository is new or succeeds the OpenClaw workspace.** Recommendation: new, and the OpenClaw workspace is *inventoried and migrated by classification* per the charter §11 — never copied wholesale.

---

## 6. Smallest evidence-producing next step

**Run the Pleistocene v0.3 round trip through the studio, in the revised format.**

Robert already intends to bring Grok's and ChatGPT's reviews back on the briefing. Do it inside the structure:

1. Each external review lands as a `note` with `kind: review`, linking `[[terminal-pleistocene-briefing]]@0.2`.
2. Robert's disposition lands as a `decision`.
3. v0.3 is produced, hashed, and carries `supersedes: "[[terminal-pleistocene-briefing]]@0.2"`.
4. The registry is regenerated by script, not edited.
5. One week later, ask CoS a question about the topic and observe whether it retrieves the right records without Robert reconstructing the background.

If the round trip is painful, the structure is wrong. If it is easy, it works. This is a real thing — but low-stakes real, already inside the structure, and the workflow being learned is the studio's rather than the content's. The 2026-07-20 lesson (don't learn a workflow on a real thing) applies weakly here and is outweighed by the value of a genuine test.

---

## 7. What is right and should be defended

- Source, interpretation, decision as three separable layers.
- Origin, steward, domains as independent fields — the actual fix for cross-domain work.
- Use-driven versioning; 2.0 as horizon only.
- Permission to fail, stated in writing.
- No undifferentiated copy of the OpenClaw workspace.
- Hash-pinning that actually verifies.
- Correctly treating `Mini-moi-2.0` in a source path as provenance, not assignment.
- Correctly demoting the Curator roadmap to a preserved source.

---

## 8. Explicit recommendation

**Continue exploring.** Apply revisions 3.1 through 3.7 before the first real capture. Run the experiment in §6. Reassess at the six-month gate against the metric set under 3.7.

Nothing in this review authorises implementation, assigns a release, or assumes mini-moi 2.0.

---

## 9. Disposition recorded from discussion

*Recorded by the reviewer from conversation on 2026-09-02. This is a note of what Robert said, not a decision record; the decision record is Robert's to write.*

Robert indicated acceptance of: R1–R5 as prioritised; the cuts in 3.6; frontmatter plus wikilinks as the record format; tagging over locked taxonomy (with cross-pollination as the stated reason); ULIDs to remove the central allocator, which he confirmed is a current source of confusion. Open-source tooling options were requested and are supplied in §4. Backup domains confirmed as S3, Time Machine, iCloud, with the sync-versus-backup caveat noted.

---

## Appendix A — Illustrative minimal records

Three records showing the full format. Nothing here is prescriptive beyond the field set.

**A source (immutable after intake):**

```markdown
---
id: 01M1HKM1BQRDJ5DJC2ZZ7807NR
slug: terminal-pleistocene-briefing
type: source
title: "The Terminal Pleistocene Inflection — working briefing paper"
version: 0.2
created: 2026-09-02
origin: claude-chat
sha256: e25e9536f47253ceb48251186c948794e1ae5bce8da8daa15c2a3d12ac2088e1
supersedes: "[[terminal-pleistocene-briefing]]@0.1"
tags: [curator, research, younger-dryas, megafauna, agriculture]
---
(body unchanged from intake)
```

**A note (Robert's cross-domain capture, showing tagging over filing):**

```markdown
---
id: 01M1HKN4X2Q8T7W3Y9R1V5A6B0
slug: interview-demo-lesson-2026-09-01
type: note
kind: capture
title: "Interview demo — led with the technical proof, not the business case"
version: 0.1
created: 2026-09-02
origin: robert
related: "[[job-search]] [[guild-domain]]"
tags: [job-search, guild, demo, lesson, presentation]
---
Built the demo through Guild. It proved the technical case well. I didn't
polish the intro and didn't lead with slides on why it mattered to the
business — normally my strongest move. Got too scoped on proving the tech.
```

**A decision (Robert's, immutable):**

```markdown
---
id: 01M1HKP9C3D2E4F5G6H7J8K9L0
slug: decision-planning-studio-record-format
type: decision
title: "Adopt frontmatter + wikilinks as the Planning Studio record format"
created: 2026-09-02
decided_by: robert
applies_to: "[[planning-studio-charter]]"
supersedes_direction: "JSON sidecar + manual registries (charter v0.1)"
tags: [planning-studio, format, decision]
---
Accepted Claude review recommendations 3.1–3.7. Sources in library/ by link.
ULIDs for identity. Registries generated, not maintained.
```

**Regenerated registry (projection, never hand-edited):**

```json
{
  "generated_at": "2026-09-02T18:40:00Z",
  "generator": "scripts/build_registry.py",
  "records": [
    {"id": "01M1HKM1BQRDJ5DJC2ZZ7807NR", "slug": "terminal-pleistocene-briefing",
     "type": "source", "version": "0.2", "path": "library/terminal-pleistocene-briefing.md",
     "sha256": "e25e9536…", "tags": ["curator","research","younger-dryas","megafauna","agriculture"]}
  ]
}
```

---

## Appendix B — Minimal directory shape after revisions

```text
planning-studio/
  README.md
  governance/     charter, lifecycle (as notes with kind: governance)
  library/        all sources, one canonical copy each, immutable
  inbox/          new captures awaiting tags and links
  initiatives/    one folder per initiative; notes + decisions; sources by link only
  decisions/      Robert's dated dispositions (or co-located in initiatives; pick one)
  registry/       generated JSON — never hand-edited
  schemas/        frontmatter contracts, enforced by pre-commit
  scripts/        build_registry.py, validate.py, secrets-scan hook
  archive/        superseded and discarded records, with tombstones
```

Removed: `patterns/`, `templates/` (a template is a note with `kind: template` in governance), per-initiative `sources/`.
