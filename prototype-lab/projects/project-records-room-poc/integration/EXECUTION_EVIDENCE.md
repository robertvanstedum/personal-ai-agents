# Opt-in CoS execution evidence boundary

`OpenClawBackend.call_backend_with_evidence` returns an immutable
`AgentExecutionReply`. Existing `call_backend` still returns text with its
previous validation behavior. No caller, portal route, or meeting adapter is
automatically switched to the new method.

- `coordination_request_id`: canonical platform correlation UUID, validated
  before inference. This is not proof of runtime execution.
- `openclaw_run_id`: captured exclusively from the authenticated configured
  runtime HTTP response's `id`, never caller context or model text.
- `agent_id`: configured target, checked against the response model.
- `text`: completed assistant text, rejecting pending tool calls or refusals.
- `mode`: `actual_agent_response` describes this adapter path, not an H1 pass.

The response contract is pinned to the inspected OpenClaw 2026.7.1 handler:
`chatcmpl_<UUIDv4>` is generated as `runId`, passed to `agentCommandFromIngress`,
then returned as the completion ID. Format validation alone does not prove
execution. Deployment route verification and capture from the trusted runtime
are necessary. Injected test transports produce contract-test evidence only.

Errors never produce an evidence result and are not retried. A timeout or bad
response may follow actual execution: the future caller must journal uncertain
attempts and must not blindly retry. This component does not implement that
journal, idempotent Records delivery, or persist a receipt.

Important: `tool_policy` is not enforced by the existing HTTP adapter. Neither
a completed text response nor absence of pending tool calls proves that the
runtime used no tools. Runtime authority must be verified before supplying
untrusted meeting records. This change does not enable meeting participation,
grant tool authority, or change the live service.

Before H1-02: reviewed integration must bind authorized session snapshot,
platform request, runtime run and authoritative Records receipt; preserve
capture/access checks and uncertain-write recovery; run a real synthetic CoS
turn through verified routing. Do not substitute the old model-gateway
responder. The runtime backend change alone does not satisfy H1-02.

Tests (from repository root with pytest and requests installed):

```sh
python -m pytest -q tests/cos/test_openclaw_backend.py tests/cos/test_openclaw_execution_evidence.py
```
