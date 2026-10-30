# Agent census → OpenTelemetry GenAI (OTLP/JSON)

- **Pinned:** OpenTelemetry semantic conventions 1.37.0 (schema URL `https://opentelemetry.io/schemas/1.37.0`). The writer
  (`core/mc_delegation_runner/otel.py`, `SEMCONV_VERSION`) and this file name the same version, and
  `tests/mc_delegation_runner/test_census.py` holds them equal, with every attribute and metric name below.
- **Status:** a mapping, not a dependency (host runtime spec v0.5 R6.6-d). The writer is stdlib only. MiniMoi's census
  file (`artifact_root/census/<day>.jsonl`) is the source of truth; `census/otlp/traces.json` and
  `census/otlp/metrics.json` are a snapshot view of it, rewritten when the census changes. No viewer is chosen (§10).
  The optional exporter (`otel.OtlpHttpExporter`) is off by default, removable, and outside acceptance.
- **Verified offline (29 Oct, build agent):** every `gen_ai.*` name below, the operation value `invoke_agent`, the
  provider values `anthropic`, `openai` and `x_ai`, the token type values `input` and `output`, the metric
  `gen_ai.client.token.usage` with unit `{token}`, and `service.name` are constants in the installed
  `opentelemetry-semantic-conventions` 0.66b1 (the repository's venv); that package's `Schemas` lists 1.37.0. The test
  checks the names against it whenever it is importable.
- **Not verified:** that each name already existed, with this meaning, in 1.37.0 itself (the installed package is 1.44.0's,
  where these GenAI names are marked as moved to the separate OpenTelemetry GenAI semantic-conventions repository, names
  unchanged); the histogram's bucket boundaries (written from the build agent's knowledge of the GenAI metrics advice);
  the span kind and span name form the conventions recommend for `invoke_agent`. Check them against the pinned release
  (or the GenAI repository's release that supersedes it) before any exporter is switched on, as N4 does for vendor hooks.
- **Beside it:** the A2A mapping (`a2a.md`), pinned the same way.

## Spans: one `invoke_agent` span per agent instance

| Span field | From the census row | How |
|---|---|---|
| `traceId` | `item_id`, else the root agent | 16 bytes of SHA-256 of `trace:item:<item_id>` or `trace:root:<root agent_id>`: one trace per work item, or per root session. |
| `spanId` | `agent_id` | 8 bytes of SHA-256 of `span:<agent_id>`: the same agent is the same span in every rewrite. |
| `parentSpanId` | `parent_id` | The parent's span id. Every parent resolves to a census row (a parent never seen is a placeholder row, `unknown: not_captured`), CEN-5. |
| `name` | `role` | `invoke_agent <role>` (or the agent id when no role is reported). |
| `kind` | — | `1` (INTERNAL): the census observes agents; MiniMoi's code does not make a call to them. |
| `startTimeUnixNano` | `started_at` (or the first event) | Event times only (R6.6-a). |
| `endTimeUnixNano` | `ended_at`, else `last_activity_at` | While an agent has not ended, the span runs to its latest event; `minimoi.census.state` says it has not ended. |
| `status.code` | `state` | `1` OK for `done`, `2` ERROR for `failed`, `0` UNSET otherwise. |

## Attributes (spans)

| Attribute | From the census row | Written when |
|---|---|---|
| `gen_ai.operation.name` | — | Always `invoke_agent`. |
| `gen_ai.agent.id` | `agent_id` | Always. |
| `gen_ai.agent.name` | `role` | Reported. |
| `gen_ai.provider.name` | `vendor` | A known provider: `anthropic`, `openai`, `x_ai` (census `xai`). `minimoi` (fakework) writes none. |
| `gen_ai.request.model` | `model` | Reported. |
| `gen_ai.usage.input_tokens` | `usage.input_tokens` | Reported. Unknown is left out, never 0. |
| `gen_ai.usage.output_tokens` | `usage.output_tokens` | Reported. Unknown is left out, never 0. |
| `minimoi.item.id` | `item_id` | The agent serves an item. |
| `minimoi.host.id` | `host_id` | Always. |
| `minimoi.environment` | `environment` | Always. |
| `minimoi.census.state` | `state` (derived, R6.6-b) | Always. |
| `minimoi.census.reason` | `reason` | Given. |
| `minimoi.cost` | `usage.cost` | Reported. |
| `minimoi.cost.unit` | `usage.unit` | Reported. |
| `minimoi.usage.complete` | `usage.complete` | Reported. |
| `minimoi.census.source` | `source` | Always. |
| `minimoi.census.verified` | `verified` | Known. |
| `minimoi.census.children` | `children` | Known (`exposed` or `not_exposed`). |
| `minimoi.start_path` | `start_path` | Known. |
| `minimoi.route` | `route` | Known. |
| `minimoi.parent.id` | `parent_id` | A subagent. |

Resource attributes: `service.name` (`minimoi-runtime`), `minimoi.host.id`, `minimoi.environment`. Scope:
`minimoi.census` version `1`.

## Metrics

| Metric | Type, unit | Data points |
|---|---|---|
| `gen_ai.client.token.usage` | histogram, `{token}`, cumulative | One per (`gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.token.type`), from the agents that reported that count. Attributes also carry `gen_ai.operation.name`. |
| `minimoi.census.agents` | gauge, `{agent}` | One per derived state (`minimoi.census.state`): the active-agent count. |

## Never written (content is off, R6.6-d)

`gen_ai.prompt`, `gen_ai.completion`, `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`,
`gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`, `gen_ai.tool.definitions`, `gen_ai.retrieval.query.text`,
`gen_ai.retrieval.documents`, `gen_ai.agent.description`. The census holds none of these values to begin with: its
events carry ids, names, digests, paths, numbers and fixed words only.
