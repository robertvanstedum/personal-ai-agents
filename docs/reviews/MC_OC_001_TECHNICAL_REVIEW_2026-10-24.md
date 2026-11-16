# MC-OC-001 technical review and disposition

24 October 2026. Robert requested collaboration with Claude on the new specification. Codex submitted the exact v0.2 text and selected installed/source excerpts to Claude Code CLI in one bounded, tool-disabled call. The reviewer could not edit files, run commands or activate anything. This is a design review from supplied evidence, not an independent runtime test or an implementation approval.

Reviewed v0.2 SHA-256: `f2c6112696281a20f3f8772ffbcb4cf2b7ca6c6de35f24c7a5004970dea5d960`.

## Codex disposition in v0.3

- **B1 — source/diagnostic boundary:** added explicit supported-design options, recommended separate inspection context, and a compatibility-evidence gate. Concrete runtime mapping is still unresolved before build authorization; not claimed closed.
- **B2 — web configuration:** named web_search/web_fetch additions to alsoAllow plus removal of group:web and any explicit deny on those tools. Did not adopt an unexplained `web` deny entry not present in the inspected config.
- **B3 — AGENTS deliverable:** require complete reconciled SOUL/AGENTS and diff with the build-plan review package; remove inactive JOB/ASK routing rather than annotate it. This deliverable remains pending.
- **C1/C2 — adoption:** A09 now explicitly tests existing-session refresh and startup with read-only persona mounts before promotion.
- **C3 — slice clarity:** added delivery priorities. Retained remote repository investigation in the first proposed slice because the owner explicitly requires usefulness without local Mac access. Delegation/export requires explicit scope disposition; no silent removal from MC’s role.
- Added stable operation/read-back recovery wording, corrected acceptance ordering and made sequential CoS adoption explicit.

Claude reviewed v0.2. Codex authored v0.3 reconciliation; no second independent approval of v0.3 is claimed. Both are suitable for stakeholder circulation, not yet build authorization. The proposed document remains pending Robert’s adoption and the identified access-mapping/persona decisions.

## Verbatim Claude Code review of v0.2

# Design Review: MC-OC-001 v0.2

**Reviewer:** Claude Code (independent review, not assigned as build owner)
**Date:** 2026-10-24
**Verdict:** Ready for circulation and owner decision. Not yet build-ready — three blockers and several owner decisions must resolve first.

---

## Overall Assessment

This is a well-structured specification that correctly identifies the gap between MC's intended role (capable operational partner) and its current state (restricted, contradictory configuration). The issue table (§3) is unusually honest — it distinguishes evidence levels carefully and avoids the common trap of asserting runtime behavior from source inspection alone. The product intent (§1) clearly preserves a genuinely capable partner, not a research-only assistant.

The spec succeeds at its stated goal: a single circulation document from which reviewers can assess intent, requirements, gaps, and acceptance without needing private evidence files.

---

## Blockers (must resolve before build planning)

**B1. No concrete source/diagnostic boundary proposal.** §6.1 correctly identifies the problem — disabling `workspaceOnly` is a wider host-read grant, prompt-based restriction is not enforcement — but then defers the solution to "the implementation design must choose." The spec itself acknowledges `fs.allowPaths` is not a valid `ToolFsSchema` field (confirmed: the schema is `{ workspaceOnly: boolean().optional() }.strict()`). This is the hardest technical problem in the spec and it has no proposed answer. **Recommendation:** Before build, the owner must decide between (a) a curated `alsoAllow` expansion with `workspaceOnly` retained plus a broader workspace mapping, (b) a container-based mount strategy with limited filesystem exposure, or (c) an explicit upstream feature request for path-scoped access. The spec should name these options and require a selection.

**B2. Web enablement mechanics are underspecified.** I02 correctly notes that removing `group:web` from deny does not add `web_search`/`web_fetch` to the allow set. The source confirms `mergeAlsoAllowPolicy` requires an existing `policy.allow` array and explicit `alsoAllow` entries. The fix is adding `web_search` and `web_fetch` to `alsoAllow` *and* removing `group:web` (or the individual IDs) from deny. **Recommended text correction in §5, row "Search public web":**

> *Current:* "Explicit native web allow entries plus applicable deny removal"
> *Replace with:* "Add web_search and web_fetch to alsoAllow; remove web and group:web from the deny list. Both changes are required — allow without deny removal has no effect, and deny removal without allow addition does not surface tools on a minimal profile."

**B3. AGENTS reconciliation has no concrete deliverable described.** §6.4 says to remove contradictory JOB/ASK routing and unsupported tool promises, but doesn't specify what replaces them. The `owns_actions` function (lines 274–284) is the actual routing switch — when true, JOB/ASK regex paths are dead. The reconciled AGENTS must acknowledge this is the *runtime* truth and remove the dead instructions entirely rather than leaving them annotated. **Recommendation:** Add a requirement that the reconciled AGENTS draft accompanies the build plan as a reviewable artifact, not deferred to implementation.

---

## Significant Concerns (non-blocking but should address before build)

**C1. Reload semantics are asserted but unverified.** The spec notes `agents.entries` is classified `hot` with actions including `restartHeartbeat`, `reconcileSystemJobs`, `refreshHooksPolicy`, `reloadInternalHooks`. It correctly flags this is not proof of session refresh. However, §6.3 says "Reload or session refresh is determined from installed runtime behavior" without requiring this determination *before* the first persona deployment. A persona release that doesn't actually load in existing sessions is a silent failure. **Recommendation:** Add to acceptance A09: "Verify whether editing the persona source refreshes an already-running session or requires a new session. Document the actual behavior; do not assume either."

**C2. Container startup seeding conflict.** §6.3 identifies that Docker startup copies image workspace content into the persistent selected workspace. The proposed read-only persona mount must override this seeding. The spec says "the build must remove/override that seeding for these files and test that startup does not overwrite the mount or fail unexpectedly" — correct, but this should be an explicit acceptance gate, not buried in prose. **Recommendation:** Add a sub-criterion to A09: "Container startup with read-only persona mount does not overwrite or fail; verified by hash comparison post-startup."

**C3. The capability matrix mixes proven and aspirational rows.** Board operations (A06) have real owner evidence. Delegation/export (A08) and remote investigation (A02) have none. The spec handles this correctly in prose ("either an explicit included slice … or an explicitly tracked successor") but the matrix table presents them at equal visual weight. This risks a build plan that tries to deliver everything. **Recommendation:** Add a priority column or explicitly split the table into "first slice" and "tracked successor" sections.

---

## Text Corrections

1. **§3, I09:** "dir_list/grep/find are not core IDs in that catalogue" — confirmed by the probe. No correction needed; this is accurate.

2. **§6.1:** Remove the sentence "fs.allowPaths is not a supported option in the inspected strict ToolFsSchema" and replace with: "The installed `ToolFsSchema` accepts only `{ workspaceOnly: boolean }` under strict validation. Any additional filesystem scoping must use a mechanism outside this schema (workspace mapping, mount strategy, or runtime identity separation)."

3. **§8.2, recovery loop:** "A delivery acknowledgement is a pending state, not successful completion" — good, keep this. But add: "A mutation that may have completed but whose receipt was lost must not be retried without a read-back check."

4. **§9, A12/A13 numbering:** A13 appears before A12 in the table. Reorder to sequential.

---

## Owner Decisions Required

1. **First delivery slice scope** — confirm: persona consistency + web/project investigation + deployed-version lookup. Exclude or explicitly defer delegation/export and remote investigation.
2. **Source boundary mechanism** — choose from the options in B1 above.
3. **Build owner selection** — Claude Code or Codex. The spec is well-suited to Claude Code given the source inspection and validation emphasis.
4. **CoS timing** — the spec says "fast follow" but CoS adoption (A13) depends on MC mechanics passing acceptance first. Confirm this is sequential, not parallel.

---

## What the Spec Gets Right

- Preserves MC as a capable partner, not a diminished assistant. The "ask → investigate → verify → answer → retain" loop is the right product.
- Refuses to treat a more assertive SOUL as a fix for structural configuration problems (§3 final paragraph).
- Maintains MiniMoi memory ownership without introducing competing stores.
- Keeps application mutations governed through existing domain semantics rather than inventing new frameworks.
- The acceptance package (§9) is concrete and testable, with appropriate evidence-level rigor.
- Correctly identifies that persona cannot authorize what runtime policy denies.

**Verdict:** Circulate as-is for stakeholder review. Resolve B1–B3 and the four owner decisions before authorizing a build plan.
