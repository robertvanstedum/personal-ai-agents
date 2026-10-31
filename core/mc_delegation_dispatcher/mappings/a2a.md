# Card and result schema → A2A task and artifact shape

- **Pinned:** A2A version: 0.3.0 (the Agent2Agent protocol's `Task`, `TaskStatus`, `TaskState`, `Message`, `Part` and
  `Artifact` objects in that release).
- **Status:** a mapping note, not a dependency (host runtime spec v0.5 R6.2-b). No A2A library, endpoint, JSON-RPC
  method or agent card is built. It keeps "expose MC as an A2A agent later" a thin adapter over these records.
- **Source:** the card schema `minimoi.card.v1` and its compiled steps (`cards.py`), the item rows and the version 2
  `result` body (`store.py`).
- **Not verified:** the A2A field names were written from the build agent's knowledge, with no network. Check them
  against the 0.3.0 schema before any adapter is built, as N4 does for vendor hooks.
- **Beside it:** the OpenTelemetry GenAI mapping (S1-F) goes in this folder and is pinned the same way.

## Objects

| MiniMoi | A2A 0.3.0 | How |
|---|---|---|
| card (`card_id`) | `contextId` | Every step item of a card is a task in the card's context. A non-card item uses its `job_id`. |
| the card itself (its digest is what Robert approves) | none | It stays MiniMoi's: the approval's subject. Each task carries `metadata.minimoi.card_digest`. |
| step item (`request_id`) | `Task.id`, `kind: "task"` | One task per item. A retry after a definite failure (R4.3-d) is the same task: its attempts are in `history`. |
| item state | `Task.status.state` | See "States". `status.timestamp` is the item's latest transition time. |
| the step's request: `brief`, `input_artifacts` | `history[0]`: a `Message` with `role: "user"` | A `TextPart` holds the brief. Each input artifact is a `DataPart`: `{ref, sha256}` and the `artifactId` it had in its source task. `referenceTaskIds` names the items its inputs came from (`facts.card.inputs_from`). |
| result `artifacts` (`kind`, `ref`, `sha256`, `bytes`, `author`, `review_status`) | `Task.artifacts[]`: `Artifact` | `artifactId` = `<request_id>/<ref>`, so identical bytes in two steps never share an id; `name` = `ref`; one `DataPart` whose `data` is `{kind, ref, sha256, bytes, author, review_status}`. |
| result `status`, `detail`, `output_text` | `Task.status` and a final `Message` with `role: "agent"` | `output_text` is a `TextPart`, and `detail` a `DataPart`. |
| result `route`, `usage` | `Task.metadata.minimoi` | `{route, usage: {amount, unit, children_stopped}}`. A2A has no spend object. |
| `attempt.failed` | a `history` entry (`role: "agent"`, a `DataPart` with the exit code) | The task stays open, back to `submitted`. |
| `decision.queued` | `status.message` while `input-required` | It names the decision id, its class and its reason. |
| grant, reservation, lease, generation, receipts | `Task.metadata.minimoi` only | Authority records. A2A has no grant or lease. Exposing them gives a client no authority (R4.1-e). |

**No `FilePart`, no `file.uri`.** A runner artifact's `ref` is a file name in the host's artifact folder, not a URI,
and MiniMoi serves no artifact over A2A. So every artifact and input is a `DataPart` carrying its reference and
digest. An adapter that later serves the bytes adds a `FilePart` whose `file.uri` is the URL it serves them from.

**`messageId`** is the `event_id` of the audit event that recorded the message: `item.queued` or `item.proposed` for
`history[0]`, `result.accepted` or `attempt.failed` for a host's report, and `decision.queued` for a `status.message`.
Each is unique, and the same event always maps to the same message.

## States (item → `TaskState`)

| Item state | `TaskState` | Why |
|---|---|---|
| `proposed` | `input-required` | It waits on the owner's admission. |
| `queued` | `submitted` | |
| `claimed`, `running` | `working` | Only while the snapshot shows it `running`. A `held` item maps as `lapsed` does. |
| `lapsed`, `paused` | `unknown` | A2A has no suspended state. These are never mapped to `working` (R5.1-b). |
| `unknown` | `input-required` | Its outcome can't be established, and the owner decides (R5.1-e). |
| `completed` | `completed` | The worker's result was accepted. It has not been verified. |
| `failed`, `timed_out`, `output_limit`, `interrupted`, `expired` | `failed` | The exact state goes in `metadata.minimoi.state`. |
| `refused` | `rejected` | |
| `cancelled` | `canceled` | |

## Card fields, for `metadata.minimoi.card`

These are carried as they are: `schema`, `card_id`, `class`, `class_version`, `repository`, `ref`, `workers`, `briefs`,
`attempts`, `rounds`, `input_artifacts`, `required_capabilities`, `step_deadline_s`, `lifetime_s`, `paths_allowed` and
`estimate_s`. Each step adds `step`, `kind`, `index`, `attempt`, `on_card` and `template_digest`.
