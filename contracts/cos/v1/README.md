# CoS-first 1.2 contracts (v1): B0

These are the frozen contracts for MiniMoi 1.2 with CoS first. They are B0 of spec v0.3, which Robert accepted on
30 Oct 2026 ("Accept v0.3"):
`planning-studio/initiatives/INIT-2026-0007-interaction-vision/minimoi-1-2-usability/15_SPEC_1_2_COS_FIRST_v0.3_2026-10-30.md`.

**Status:** schemas, data files and fixtures only. No app code reads them yet; B1a is the first batch to build against
them. When the spec and a schema disagree, the spec wins and the schema is corrected through a change with a receipt.

## What's here

| File | Spec | What it fixes in place |
|---|---|---|
| `common.schema.json` | 4.1, 4.2 | `data_scope` (only `personal` is written in 1.2), `environment`, typed refs, the one provenance vocabulary, seats, actors, origins, offset-only timestamps |
| `activity-record.schema.json` | 4.3, 4.4 | DAR v2: complete, partial and unreadable observations; explicit withdrawal; declared truncation; MC's facts and update parts |
| `store-records.schema.json` | 4.2, 4.5, 4.6 | goals, plans, obligation definitions, obligation and episode events, CoS decisions, corrections (in the judgment store's shape), meeting links |
| `store-command.schema.json` | 4.5, 4.8 | the one command contract and its result; `commitment.open`, the receipt behind "I'll follow up" (N1) |
| `read-surface.schema.json` | 4.7–4.9 | reader responses (ok, stale, unavailable), the picture, the injected digest, the briefing |
| `incident.schema.json` | 4.10 | E1–E4 raise, the incident, channel attempts (Telegram, email, chat), authenticated acknowledgment |
| `consultation.schema.json` | 4.11 | `consult_v1`: one bounded MC turn, its states and provenance |
| `issue-operation.schema.json` | 4.12 | an issue operation in `mini-moi-private`, its approval tier and reconciliation |
| `intake.schema.json` | 4.18 | `intake.submit_v1` into the Guild Build Queue |
| `conversation.schema.json` | 4.19 | the canonical CoS history: conversations, turns, idempotent sends, stream notes |
| `seat-binding.schema.json` | 4.14 | the server-side seat binding, and model arguments that can never carry authority |
| `access-matrix.json` | 4.7 | who may touch which path, and how |
| `capabilities.json` | 4.14 | the CoS capability catalogue |
| `legacy-mapping.json` | 4.1 | `data_scope` against the names Vault, the harness and Rooms use |
| `loop-inventory.json` | 4.6 | every CoS loop, and what becomes of it |
| `index.json` | — | the schema identifier → file registry |

Fixtures live in `tests/cos_contracts/fixtures/<file stem>/{valid,invalid}/`. They cover a healthy result, an
unavailable dependency, a stale last-good result, a repeated operation and a changed payload under the same id
(`store-command/pairs/`), and a wrong or mixed scope. `tests/cos_contracts/test_cos_contracts.py` checks every schema,
fixture, digest and invariant. Each invalid fixture must fail inside the branch it names.

## Rules a schema can't hold

These are frozen too, and B1a and B2 implement them.

**Canonical digest (4.3 rule 5).** `sha256:` plus the hex SHA-256 of JSON with sorted keys, separators `(",", ":")`,
UTF-8 and `ensure_ascii=False`. An activity record's digest leaves out `seq`, `digest` and `published_at`. A command's
`payload_digest` covers `payload` only.

**Activity record ordering (4.3 rule 4).** Within one `generation`, `seq` orders. A generation the reader hasn't seen
replaces the old one, in the reader's own receipt order.
- The same `seq` with the same digest is a duplicate.
- The same `seq` with a different digest is a conflict: flagged, never merged.
- `published_at` is for display only. A clock skew is flagged, never used to reorder.

**Replacement (4.3 rule 2).** Only `complete` replaces last-known. `partial` adds or updates and never retracts.
`unreadable` keeps last-known, marked stale, and is never shown as zero. Withdrawal happens only through `withdrawn`.

**Commands (4.5).**
- A repeated `command_id` with the same `payload_digest` returns the first receipt (`replayed: true`).
- The same id with a different digest is `refused` with `command_id_reused`.
- A command counts as saved only after read-back.

**Quiet days (4.6, N3).** These use final, complete records with `count: 0`, three in a row, and only for a domain named
in an active goal whose `owner_words` hold a `robert_source` span. An `unreadable` or missing day ends the run.

## The B0 checks

These are lookups and one verification, not decisions. They were made on `origin/main` `4f531c6f` with `git show` and
`git grep`. No production system was read.

| Check | Result |
|---|---|
| **German time fields** | Gespräche sessions record **`date` only** (`YYYY-MM-DD`), with no time of day (`domains/german/german_domain.py:2278`, `:2323`). Writing entries record `timestamp`, a naive ISO datetime (`:1612`). Drills keep only `last_practiced` per phrase, overwritten on each drill, so there's no drill history (`:1683`). **So:** items carry `time_precision`, which is `day` for sessions and `second` for writing; drills are counted only when `last_practiced` falls inside the covered interval. |
| **Container time zones** | `minimoi-german` sets no `TZ` (`docker-compose.prod.yml`; only cos-agent-a `:83` and the supervisor `:483` set one), so German's naive timestamps are container local time, which is **UTC**. The writer converts them explicitly to `America/Chicago`. No naive time reaches a record: the schema refuses them. |
| **Users, groups and modes** | The Python service images have no `USER` line and run as uid 0. `cos-agent-a` runs as the OpenClaw image's `node` user. Files are therefore `0640` with group gid 1000, so the agent can read and not write (`access-matrix.json`). **To confirm on the host at B1a deploy:** node's uid and gid, and the supervisor's user. |
| **Loop inventory** | Confirmed against `chief_of_staff.py:1841-1894`. The code also runs loops D, F, G, I and R, which the spec's table didn't list. F, G and D stay. I raises E3. R migrates with reminders (`loop-inventory.json`). |
| **Production backend** | The scheduler and `cos-bot` default to `COS_BACKEND_TYPE=openclaw` (`docker-compose.prod.yml:379`, `:414`); the code's own default is `grok` (`chief_of_staff.py:861`). Which one serves production **today** is a live fact, checked at B1a deploy with the capped key (`COS_AGENT_MASTER_KEY` empty, `/run/cos-key` present). |
| **V-1, the OpenClaw context refresh** | **Not run.** It reads production's routing receipts and one OpenClaw session store on the EC2 host, which holds personal text, so it waits for Robert's word. B1b can't choose the injection channel until V-1 has a result; the permanent fallback is written in spec 4.8, and `channel` already carries both answers. The method and the exact read-only commands are below. |
| **The supervisor's database role** | **None of its own.** The supervisor connects with the portal's `DATABASE_URL` (`docker-compose.prod.yml:479`, `services/supervisor/main.py:141`), and `core/supervision/sql/` creates no role and grants nothing. Spec 4.10's "the incident tables and nothing wider" therefore needs a new role at B6a, with its URL projected as a file like the token. That is a new production credential and needs Robert's yes (`access-matrix.json`). |
| **Loops may run twice in production** | **A hypothesis from the code; no log confirms it.** `cos-bot` imports `domains.cos.chief_of_staff` (`core/telegram/telegram_cos_bot.py:51`, `:143`), whose `_start_cos()` runs at import (`chief_of_staff.py:1766`, `:1906`) and starts the scheduler and a Telegram poll thread. The cos-bot image copies `domains/cos`, and its compose block sets neither `COS_BACKGROUND_JOBS` nor `DISABLE_TELEGRAM_POLL` (`/opt/minimoi/.env` could). Only loop R is guarded against a second copy, by the reminder store lock. If the cos-bot startup log confirms it, B1a/B3 leave exactly one scheduler process: cos-bot reaches CoS through the store client and the backend's API and imports nothing from `chief_of_staff` (`loop-inventory.json`, `processes`). |

### V-1: the method and the read-only commands

What the code adds to spec 4.8's method:
- The routing receipt's id **is** the turn id (`domains/cos/confer_service.py:353`), and the marker is written only on
  the Confer path, so V-1 counts Confer turns only.
- A receipt holds no conversation id. The turn-to-conversation mapping comes from the Confer log, whose lines carry
  `at`, `conversation_id` and `turn_id` (`domains/cos/conversation_log.py`). The same lines hold Robert's words and
  CoS's reply, so the commands project those three fields only.
- The callback takes the **first** marker in the messages, so a matched receipt on a later turn means no earlier
  system text sat ahead of it.

To run on the EC2 host only with Robert's yes. Nothing leaves the host except counts.

```bash
# 1. Turns per conversation: three fields only, never the text.
sudo sh -c 'cat /opt/minimoi/cos/conversations/*.jsonl' | jq -c '{at, conversation_id, turn_id}' > /tmp/v1_turns.jsonl
# 2. Receipt ids only.
sudo jq -r '.receipt_id' /opt/minimoi/data/model_gateway_receipts.jsonl | sort -u > /tmp/v1_receipts.txt
# 3. Second-and-later turns, and how many have their own receipt.
jq -rs 'group_by(.conversation_id)[] | sort_by(.at)[1:][] | .turn_id' /tmp/v1_turns.jsonl | sort -u > /tmp/v1_later.txt
wc -l < /tmp/v1_later.txt; comm -12 /tmp/v1_later.txt /tmp/v1_receipts.txt | wc -l
# 4. The agent's session store: the schema first (no rows), then one COUNT of system messages per session.
sudo ls /var/lib/docker/volumes/ | grep cos-agent-a-state
sudo sqlite3 "file:/var/lib/docker/volumes/<that volume>/_data/agents/cos-agent-a/agent/openclaw-agent.sqlite?mode=ro" '.schema'
rm -f /tmp/v1_turns.jsonl /tmp/v1_receipts.txt /tmp/v1_later.txt
# 5. In the same visit: the backend in use, the capped key, and the loop hypothesis.
sudo /opt/minimoi/scripts/operations/cos_prod.sh status
sudo /opt/minimoi/scripts/operations/cos_prod.sh audit
sudo grep -E '^(COS_BACKEND_TYPE|COS_BACKGROUND_JOBS|DISABLE_TELEGRAM_POLL)=' /opt/minimoi/.env | cut -d= -f1,2 || echo "no override"
docker logs minimoi-cos-scheduler 2>&1 | grep -E 'Backend initialized|Scheduler:|Telegram poll' | tail -3
docker logs minimoi-cos-bot 2>&1 | grep -E 'Backend initialized|Scheduler:|Telegram poll' | tail -3
docker exec cos-agent-a id
```

The session-store path follows the one the judgment store reads for MC (`scripts/judgment/judgment_store.py:42`) and is
confirmed by step 4's listing before anything is opened. **It passes** when every later turn has its own receipt and
the session holds no earlier system texts: the system message is the channel. **If it fails,** the picture moves to the
labelled data block at the head of the user turn (4.8).

## Spec text these contracts make precise

Nothing here changes a decision. Where the spec left a shape open, this is the shape B0 chose:
- typed-ref ids allow `@`, which joins an obligation or episode id to its date;
- `practice_period` adds `until_due`, for one-off commitments;
- `time_precision` on activity items, following the German finding above;
- Rooms event ids are the text `events.id`, not the integer `seq` (`rooms/store.py:108`);
- the `cos-before-1-2` id for the imported, read-only legacy conversation;
- stream note ids are `briefing:`, `nudge:`, `incident:`, `consult:` or `commitment:`.
