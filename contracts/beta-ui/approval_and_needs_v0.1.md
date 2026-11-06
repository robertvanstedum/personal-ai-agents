# Beta UI contracts: approval card in chat, pending-decision count (v0.1)

For the 1.2 Beta UI lane (U1, U2). Backend owner: Claude Code. "Exists" means it's on the integration line today. "Coming" means I'm adding it; the field or item is additive, so build to handle it being absent.

## 1. Work card in the CoS chat (U1)

### Read: `GET <cos>/work-cards` (exists)

```json
{"admissions": [{
  "operation_id": "...", "task_id": "TK-...", "card_digest": "...",
  "approval": null,
  "conversation_id": "coming: string or null",
  "card": {"card_id": "...", "class": "build_only|review", "repository": "...", "ref": "...",
           "workers": {"builder": {"actor": "...", "route": "...", "amount": 0, "cost_cap_usd": 0}},
           "briefs": {"build": "..."}, "step_deadline_s": 0, "attempts": 0, "input_artifacts": []}
}]}
```

- `route: "fakework"` means simulated: no model runs and nothing is spent. Say so, as `cos-work-cards.js` does.
- `approval` not null means approved, waiting for the authority to admit it. Don't offer the buttons again.
- **Where it shows:** in the conversation whose id matches `conversation_id`. If it's null or absent, show it in whichever CoS chat is open. Never hide a waiting card. It stays in Actions too, which is the same record.
- Refresh after every completed CoS turn, after a decision, and on load.

### Decide: `POST <cos>/work-cards/approve` or `/decline` (exists)

Body: `{"operation_id": "...", "card_digest": "<as shown>"}`. Send `cosWriteHeaders()`. One click sends one card exactly as shown.

Success, 200: `{"state": "approved|declined", "operation_id": "...", "note": "..."}`. Show `note`. "Approved" is not "running" or "done".

Refusals are `{"error": code, "message": words}`. Always show `message`, never the code or a raw field name.

| Code | Status | Meaning |
|---|---|---|
| `changed` | 409 | Card changed since shown: reload it and show again |
| `not_pending` | 409 | Already decided: reload |
| `not_found`, `no_card` | 404, 409 | Not available: reload |
| `invalid` | 422 | Page bug: show the message, don't retry |
| `unavailable` | 409, 503 | Unknown state: say so, offer Refresh, don't claim anything |
| `csrf` | 403 | Reload the page |

No answer at all: "The portal couldn't be reached. Nothing is claimed; try again." Re-read before offering another click.

### Approval in Robert's own words (coming, backend only)

Needs no new UI call. It's a normal CoS turn: refresh cards after the turn, as above. The owner's actual words bind to the exact card version; a model's reading alone doesn't approve; more than one possible card means CoS asks. This slice gets its own review, and U1 doesn't wait for it.

### Not in this contract

Progress and result in the chat beyond approval. They arrive as CoS's own messages today. If U1 needs a state line, say so in `reviews/` rather than guessing.

## 2. Pending-decision count (U2)

`needs_you()` in `minimoi_portal/guild_ui/needs.py` keeps its shape:

```json
{"status": "ok|unknown", "items": [{"tag": "...", "item_id": 0, "op_id": null, "text": "...", "at": "..."}],
 "total": 0, "observed_at": "...", "text": "...", "error": null}
```

**Coming:** tag `Approve` for task proposals and work cards waiting for Robert, with an extra optional `href`. `item_id` may be a string for these. If the task service can't be read, `status` is `unknown` and `total` is null: show unknown, never "Nothing needs you".

The UI shows `text` and doesn't compute its own count.

## 3. Task proposal in the CoS chat (U1, added 6 November)

A proposal is what CoS asks before a task exists; a work card is what MC asks before an agent starts. Same page pattern, separate records. Same gates as work cards. It stays in the Guild task view too, which is the same record.

### Read: `GET <cos>/task-proposals`

`{"proposals": [{"interpretation_id": "...", "proposal_digest": "...", "revision": 0, "proposed": {"outcome": "...", "done_when": "...", "task": {...}}, "sources": [...], "conversation_id": "string or null"}]}`. Show `proposed` as the record has it. Placement follows section 1's rule for `conversation_id`.

### Decide: `POST <cos>/task-proposals/confirm` or `/decline`

Body: `{"interpretation_id": "...", "proposal_digest": "<as shown>"}`, with `cosWriteHeaders()`.

- Confirm, 200: `{"state": "confirmed", "interpretation_id": "...", "request_id": "...", "task_id": "...", "lead": "..."}`. "Confirmed" means the task exists, not that work began.
- Decline, 200: `{"state": "declined", "interpretation_id": "..."}`.
- Refusals as section 1: `changed`, `not_pending`, `invalid`, `unavailable`, `csrf`. Show `message`. A confirm that stopped part-way finishes forward on another click, under the same ids, so keep the card and reread after a failure.
