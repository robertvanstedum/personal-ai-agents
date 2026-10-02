# Isolated intelligence dev deployment — 2026-10-02

Runtime revision: `b41ab82a4e247564148881a51afdc516927920b5` on `codex/intelligence-layer-dev`.
Base: `7d6e2852b87f31c372bd571e7e04fff2be4d757b`.
Exact implementation diff: `git diff 7d6e2852b87f31c372bd571e7e04fff2be4d757b b41ab82a4e247564148881a51afdc516927920b5 -- intelligence_layer`.
Any subsequent packet-only commit does not change the deployed runtime.

Image: `minimoi-staging/intelligence:b41ab82a4e247564148881a51afdc516927920b5`.
Image manifest list: `sha256:d58e3893528223c2efcb9e893618680142cb1233e1825aafd499f929ba2111c2`.
Image config: `sha256:c4aa04e330453f8cb59d722f10397b7d03e1fc34dc0a9d867ffdacd75a08e6c7`.
Pinned Python base: `python:3.12.15-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3`, native arm64.

## Verified on dev

- Build ran 36 tests under Python 3.12.15: all passed, 9.774 s. Host Python 3.11.9 run: 36 passed, 9.021 s (`tests.txt`). Build network was disabled; no pip or model calls.
- New standalone service and credential-free door started successfully; service reported healthy.
- Host loopback `GET /health`: HTTP 200, exact runtime revision above, `mode=synthetic-dev`. `GET /`: HTTP 200 with expected UI content. Unauthenticated same-origin `POST /api/status`: HTTP 401.
- Authenticated live checks in `live_acceptance.py`: demo re-ingest added zero duplicate sources; exact citations resolved to verified raw bytes; typed proposal/decision/lesson/practice/outcome fixture present; outcome-to-practice owner-receipted relation cited both ends; historical query before the assertions returned no future knowledge; rebuild preserved results; unauthorized topic refused; plan-confirmed synthetic deletion removed its canary from all working files and a fresh search.
- Working deletion explicitly returned backup erasure `pending`. No encrypted-backup or real restore success is claimed.
- Restarted only `minimoi-intelligence-dev`: same generation `380b29a0bf685a2518f83882688b3bc31b13493783913f514a3ce246a86f5825` survived; 9 synthetic records, 3 relations, 1 synthetic deletion tombstone. Generated credential persisted; browser sessions intentionally reset.
- Actual outbound TCP attempt from the main service to an external address was unavailable. Docker network inspection confirms `internal=true`.

## Isolation evidence

Before/after Docker identity snapshots for `minimoi-portal`, `minimoi-records`, `minimoi-rooms-worker`, and `minimoi-rooms-codex` compared byte-identical across this deployment. No existing container was recreated or restarted by this work.

`minimoi-intelligence-dev`: one named-volume mount, `minimoi-intelligence-dev-data:/tmp/intelligence-data`; only `minimoi-intelligence-dev_intelligence-internal`; read-only root filesystem; all capabilities dropped; no host port.

`minimoi-intelligence-door`: no mounts or credentials; only its own internal and door networks; read-only root filesystem; all capabilities dropped; `127.0.0.1:18882->18882/tcp`. No shared application network membership.

Only `intelligence_layer/` changed in the assigned worktree. Protected docs/main workspace/Guild UI worktree/shared release checkout/current application stores were not edited. The independent project's start/stop scripts never invoke the shared staging stack. No production deployment or main merge.

## Access and rollback

Open `http://127.0.0.1:18882/` on Robert's Mac. The Codex browser opening was queued for this chat. Local sign-in instructions are in `BUILD.md`; never paste the generated token into a chat or review packet. Signed-in browser visual acceptance remains part of Robert/Claude Code's posthoc session; HTTP and cookie flows were tested automatically.

To stop only this service while preserving its synthetic store:

```sh
INTELLIGENCE_TAG=b41ab82a4e247564148881a51afdc516927920b5 docker compose -p minimoi-intelligence-dev -f intelligence_layer/compose.dev.yml down
```

For a later update rollback, set that same tag and run `up -d --no-build` with this project/file. No prior intelligence release existed. Do not delete the named volume or stop shared services.

## Remaining gates

This is a usable **synthetic dev service**, not six-source real-data activation, production, or completed release acceptance. Native source capture, approved external/model routes, owner-only key isolation, encryption/backup integration and real recovery/erasure drills remain the concrete P3–P4 steps in `BUILD.md`. Ollama's executable exists locally; its models have not been invoked. `age` and `restic` were not found on the current PATH. No tool choice or D1–D10 proposal is silently adopted by this deployment.

Independent review was explicitly deferred by Robert. Claude Code should inspect the exact committed implementation diff and run the packet's tests plus a signed-in UI walkthrough. The canonical release/operations retrospective remains with the planning/documentation role and owner review.
