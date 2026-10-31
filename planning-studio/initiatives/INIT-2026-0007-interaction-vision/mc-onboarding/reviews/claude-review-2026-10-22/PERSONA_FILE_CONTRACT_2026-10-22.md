# Persona file contract (for the persona Robert writes with Claude chat)

**22 Oct 2026.** Robert's decision: he writes the Master Craftsman persona with Claude chat, in his own words; it is versioned and lives in GitHub. This page says where it goes and how it reaches the running agent, so the draft drops in without rework. It is a contract, not a draft of the persona.

## 1. Where it lives

| File | Purpose | Owner |
|---|---|---|
| `planning-studio/initiatives/INIT-2026-0007-interaction-vision/documents/MASTER_CRAFTSMAN_PERSONA_v1.0_<date>.md` | The approved persona, versioned. Each revision is a new file; the previous stays. | Robert |
| `…/documents/CHIEF_OF_STAFF_PERSONA_v1.0_<date>.md` | The same for the Chief of Staff, so the two identities stay distinct (D1, v1.0 §12.2). | Robert |
| `…/documents/MASTER_CRAFTSMAN_PERSONA.md` (18 Oct, owner-maintained, Austrian/Viennese direction) | The baseline, already in GitHub (commit baf57c0b). Keep it as v0 history. | Robert |

Header lines for each version: `Version`, `Date`, `Approved by: Robert`, `Supersedes: <previous file>`, and a one-line `Change`.

## 2. What belongs in it (and what doesn't)

Under 400 words, in Robert's voice (Claude chat's R07 answer 2, adopted in v1.0 §6):

**In:** who MC is to Robert and what it is for; how it speaks (lead with the answer, plain words, short, proposes, acts on yes, pushes back with reasons); Robert's working preferences (no process overhead, apply a correction once, never coach syntax); the language clause (reply in the language of the substantive request; a greeting alone doesn't set it; refusals, safety outcomes and anything Robert must act on are stated so he never has to translate them; standing German practice colours the rest); the three authority tiers in one paragraph, so MC knows what it may do without asking and what always waits for Robert.

**Out:** tool descriptions (the tool catalogue carries those); domain facts (manifests carry those); anything Robert said once (lessons carry those); any rule that code or policy can check (the policy table carries those); secrets, paths, credentials.

**Test:** if a line could be enforced by code or looked up by a tool, it is not persona.

## 3. How it reaches the running agent

OpenClaw loads the agent's workspace files. For `mc-chat` on dev (`~/.openclaw/workspace-mc-chat/`):

| Runtime file | Today | After the persona lands |
|---|---|---|
| `SOUL.md` | 74 words, "build and operations partner, in conversation" | **The persona, verbatim**, with a first line naming its source: `Source: MASTER_CRAFTSMAN_PERSONA_v1.0_<date>.md @ <git commit>` |
| `IDENTITY.md` | name and role | Name, role, runtime; unchanged otherwise |
| `USER.md` | 215 words about Robert | The small owner profile (v1.0 §6): facts about Robert that aren't preferences; reviewed with the persona |
| `AGENTS.md` | 743 words of lane rules, including the `JOB:` and `ASK` reply markers | **Kept for now** (the markers still drive background jobs and agent requests on dev until M1.2 retires them). Trim only the lines the persona now covers (tone, length, "don't list what you can't do"). Shrinks again at M1.2. |

Deployment is an operator step with a record (as for every dev change): copy the approved file into `SOUL.md`, back up the previous file, note the source commit in `~/minimoi-runtime/operators/<change>/APPLIED.json`. No persona text is edited in the workspace directly; the file in GitHub is the only source. The same applies to CoS's workspace (`cos-agent-a`) for its persona.

## 4. How we know it worked

Re-run the exchange that exposed the gap: "Feel free to correct my mistakes. I want that." Pass: MC says it will, in its own words, and (once M1.3 lands) keeps it as a preference with an undo, without saying "Remember:". Until M1.3, pass is: MC says it will, and offers to keep it, without teaching syntax.

Record the result against the persona version in the advisor brief v0.2.
