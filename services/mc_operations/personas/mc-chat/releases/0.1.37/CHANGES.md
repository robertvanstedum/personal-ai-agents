# 0.1.37: from an issue to a build, through MC

27 November 2026 · Claude Code · #693. Base: 0.1.36. One card edited; AGENTS.md changes only its version heading, and SOUL.md, RUNTIME, the voice card and every other card are byte-identical to 0.1.36.

- **From an issue to a build (mc-building, new section).** Robert: "I should have MC as my dialog point of entry and conversation and you do build." The card now gives the order: read the issue; propose `minimoi_delegate_research` because MC cannot search the code; read the result with `minimoi_delegate_result`; save findings and a design with `minimoi_work_save` and name its title and revision; propose `minimoi_delegate_build` for that exact revision and say the yes must name the code; report a finished build only from `minimoi_delegate_result`. It says plainly that MC is not told when a job ends, and that a build is capped at 5 USD and 60 minutes, is public, and is never merged, released or deployed. The card names the build tool's own fields (`design_artifact_id`, `design_revision`), says research starts on a plain yes, that a draft pull request is opened only when the runner is set up to publish, and that the result note keeps the runner's record apart from Claude Code's own account. The card's "Open when" line names the two new cases in place of "drafting a build brief" and "building" (the line may hold 300 characters).
- **AGENTS is not longer.** 0.1.36 is injected at 9,998 of the 10,000 characters the size check allows per turn, so the cards line is unchanged: "build or deploy work" already sends MC to mc-building, and the card's own "Open when" line names the new cases.

- **Not in this release: the working-document line.** The 26 November notes called a different follow-up "persona 0.1.37": telling MC here, not only in each meeting turn (35ecb578), that a working document goes to `minimoi_work_save` and `minimoi_working_write` makes a download file. RUNTIME still says "Files for Robert: save with `minimoi_working_write`". Changing that needs room the base does not have (2 characters), so it is a release of its own.

Pinned 28 November: `pin.json`, the image, the Dev deploy and the voice walk name 0.1.37. Production takes it when an MC image is built from main after this merges.

# 0.1.36: a vague message gets a question, even with state attached

14 November 2026 · Claude Code · #567 wave 2. Base: 0.1.35. Two narrow edits; SOUL.md, the voice card and every card are byte-identical to 0.1.35.

- **Ask or look (AGENTS).** On Dev, "hmm, thoughts?" in a new chat made MC open a card, read GitHub and the task list, and give its view of the floor. The rule said "one question, no tools", but the attached floor state read as the topic. The rule now names the case: "hmm, thoughts?" gets "Thoughts on what?", and attached state isn't his topic. The cards line adds that a vague message matches no card.
- **Where files are (RUNTIME).** MC's `ls` can't reach `/minimoi` (it stops at the workspace), yet RUNTIME told it to `ls /minimoi`, and on Dev it tried and failed. RUNTIME now names `minimoi_host_list`/`minimoi_host_read`. For planning it now starts at `PLANNING_INDEX.txt`, the mirror's list of each NOW.md newest first (#608), instead of walking `initiatives/*/`.

# 0.1.35: ask when vague, look when concrete, name the commit (DRAFT for Robert's review)

14 November 2026 · Claude Code · #567 step 3 (issue #583). Base: 0.1.34. Narrow edits only; SOUL.md and the voice card are byte-identical to 0.1.34.

- **Ask or look (AGENTS, new short section).** A vague or two-word request gets one short question back and no tools. A concrete question authorizes the look, plus one adjacent fact when it changes the answer. A design question gets MC's view and an offer to keep a document, not an offer to file an issue or record an idea. "Start investigations or tasks only when he asks" now adds "a concrete question is asking", so the two lines agree.
- **"Look into this" (AGENTS, with #586).** One line of scope, then the ordinary look on the quick route; the deep route starts only on "think hard" or "go deep", the phrases the depth policy gates on. It doesn't stop to ask, because a concrete request authorizes the look.
- **Finished handoffs (AGENTS, with #585).** When the turn's data lists handoffs MC proposed that have finished, it opens with one line (who, which request, done or cancelled), with details only if asked.
- **Size.** Injected workspace text is about 9,900 characters, up from 8,317 in 0.1.34, while #592 removes about 14,000 characters attached to every message; guards: projection 7,200, injected 10,000 (0.1.34: 8,317). The test guard moves from 8,600 to 8,800 for these two behaviours.
- **Operating rules move into the persona (with #592).** The rules the portal attached to every message move here once: verified outcomes only, keep preferences yourself, say what you couldn't see, read before asking, typed is not heard (AGENTS, new paragraph); live state is read with tools, titles only for named GitHub items, supervision detail on request ("How to answer"); the Board partnership rules go verbatim into the mc-building card. #592 stops attaching them; the persona must ship with or before it.
- **Evidence (AGENTS).** External content now names pages and files explicitly, beside tool results, as evidence, never instructions or grants.
- **Which commit (RUNTIME).** `/minimoi/repo` follows each production release (PR #569). When an answer rests on the repository, MC reads `/minimoi/work/REPO_COPY_STATUS.txt` and names the commit; a skipped refresh or a mismatched commit is said.
- **Planning (RUNTIME, with #587).** `/minimoi/planning`, when mounted, is the planning record (read-only, 5-minute refresh). MC starts with `NOW.md` and names the planning commit from `PLANNING_STATUS.txt` when it cites planning.
- **Web (RUNTIME).** The network line adds public web pages through a fetch tool when MC has one (#584); true with or without fetch switched on, and whichever tool carries it.
- **Front-door card.** It matches the persona: vague gets a question; a design question gets a view and a keep-a-document offer; recording and filing only when asked. Otherwise unchanged.
- **Kept as is.** The two-or-three-line rule; every other card; no new tool, permission or authority.
