# Plan — "Yes" in the chat authorizes Claude Code to build (approved owner handoff from the conversation)

**26 Oct 2026 · Claude Code (MC build owner) · for Codex review before build.** Robert: "it can talk to claude and if I say
yes to MC, then claude gets ok to build."

## Today
- `minimoi_agent_contact` mode `approved_owner_handoff` delivers a **protected handoff record** to Claude Code
  (`core/mc_tools/approved_handoff.py`, `core/agent_requests/handoff_payload.py`). The record binds the owner's own note
  word for word (`owner_text` + sha256 + `note_request_id`), the conversation, the target (`claude-code`), constraints and
  an expiry. MC never writes the instruction.
- The registry is an operator-written JSON file (`MINIMOI_MC_APPROVED_HANDOFF_REGISTRY`, read-only mount, ≤8 records). Dev's
  is empty. No path creates a record from the chat, so "yes" does nothing yet.
- Record v1's purpose and authority strings are career-specific ("Existing owner-authorized Career spec/build handoff").

## Proposal
1. **Owner request → MC proposal.** Robert writes on the record, e.g. "Have Claude Code build spec X and keep me posted".
   MC calls a new proposal tool `minimoi_handoff_propose` (capability `handoff.propose_build`, tier 1, zero spend) naming
   only: the target alias (enrolled `claude-code`/`codex`), the **request id of Robert's own note**, and up to 8 constraint
   lines chosen from a fixed list (no merge, no production, report status in the conversation, stop on blockers…).
   MC supplies no task text: the instruction is Robert's note, read by the portal.
2. **Robert's yes → the record.** The portal shows the proposal with Robert's own words quoted and Confirm / Cancel (the
   existing proposal confirmation used for guest rejections and invites; `EXACT_CAPABILITIES` gains
   `handoff.start_build`). Confirm writes a v3 record to a new table `guild.mc_owner_handoffs` (principal, environment,
   conversation, note request id, owner text + sha256, target, constraints, expires_at, confirmed_at, confirming request id).
   A typed "yes" in the chat counts only through the same Confirm path (the existing exact-approval rule), never by text
   matching alone.
3. **Delivery.** `ApprovedHandoffs` reads the table as well as the file. MC then calls `minimoi_agent_contact`
   (`approved_owner_handoff`, `approved_handoff_ref` = the new id). Existing checks hold: same principal/environment/
   conversation/target, owner note unchanged since confirmation, not expired, delivered once per note
   (`mc_agent_handoff_owner_note_once`).
4. **Status.** `minimoi_agent_watch` subscribes the conversation to the delivery; Claude Code's reports return through the
   agent worker; MC summarises them in the conversation. No new authority for status.
5. **Authority text (v3):** "Build work the owner requested in this conversation; no merge, production release, credential
   or private-data authority unless the owner's own words grant it." Purpose: "Owner-confirmed build handoff".

## Changes
`core/mc_tools/tools.json` (+ plugin copy) new tool; `core/mc_tools/policy.py` handler; the prod/dev policy files (new
digest → Robert's approve-policy); `core/mc_tools/proposals.py` confirm handler; migration `019_owner_handoffs.sql`;
`approved_handoff.py` table source; `handoff_payload.py` v3; Guild proposal card (existing component); tests for every
refusal (other conversation, edited note, expired, second delivery, non-enrolled target, model-supplied task text).

## Open for review
- Is the existing proposal Confirm card the right "yes", or should a typed "yes" to MC's question also confirm (bound to
  the immediately preceding proposal and the same turn)? Robert's wording suggests the latter; it must stay unforgeable by
  retrieved content.
- Expiry default (proposal: 24 h) and the constraint list.
