# Reusable dev MC exam operators

These operators preserve the real-route proving workflow: test-owner login → saved
conversation → MC turn → CoS/OpenClaw → restricted repository tools → saved answer
and gateway receipts. Rendering never starts services, arms the guard, or buys a
model call. The example deliberately refuses to render until an operator replaces
its placeholders and zero limits.

The existing `proving_driver`, `proving_runner`, `proving_budget`, `proving_tools`,
`proving_tool_queue`, `proving_text_fixture`, and `proving_receipts` modules remain
the implementation of those contracts. `operator_templates/` captures the previously
one-off window, runner, worker, priced guard, and settlement scripts. Templates are
rendered into a **new persistent private directory**, never a temporary directory.

## Prerequisites and initial setup

This package operates an already provisioned isolated dev stack on macOS/Colima.
It does not create accounts, keys, containers, networks, or grants. Use the staging
[setup](../README.md) for the regular dev environment. Provision and independently
review the following isolated resources before the first run; do not substitute
production resources or the owner's regular identity:

| Resource | Required contract |
| --- | --- |
| `minimoi-proving-db` | Separate PostgreSQL data/role, Guild migrations including turn ledger/barrier; no shared owner data |
| `minimoi-portal-proving` | Separate auth files and state; network `minimoi-proving-isolated`; internal port 5001; health reports `proving.environment=dev` and a random 32-hex `instance_id`; normal `/guild-next/api/v1` APIs |
| `minimoi-proving-cos` | Separate memory/state; `COS_BACKGROUND_JOBS=0`; only the isolated agent backend |
| `minimoi-proving-agent` | Separate state volume; pinned reviewed policy; only four scoped tools; no general exec/network tools; internal port 18789; stopped outside the exam window |
| `minimoi-proving-model-guard` | Private credential environment; mount state root's `model-guard.py` at `/guard.py` and `guard-state/` at `/state`; route only to `minimoi-mc-agent`; no fallback; stopped or disarmed outside the window |
| Gateway | Existing scoped test key restricted to MC with $25/day budget, dedicated non-secret usage `key_ref`, priced usage JSONL; both dev aliases map to the independently reviewed concrete model |
| Tool test image | Existing immutable local image with test dependencies; `RepositoryTools` runs it with `--network none --read-only --pull never` |

Existing mounts, resource/security limits, isolated database schema, and test identity
must be reviewed independently. The operator restores starting container states and
checks regular dev health; it does not reproduce provisioning from an empty host.
Do not use `docker compose down -v`, broad pruning, or real owner data to reset tests.

Keep the following in a private state root (directory 0700, files 0600):

- `test-identity.json`: existing `mc-proving-*` username/password; never commit it.
- `snapshot.json`: `instance_id` binding to the isolated portal.
- `budget-v2.sqlite`: existing reservation ledger. **Never reset it for a retry.**
- `guard-state/model-admission.sqlite`: existing `calls(id, day, at)` history.
- `guard-state/expected-policy.txt`: reviewed policy for content-free delivery audit.
- `model-guard.py`: installed original guard to restore; record its reviewed SHA256.
- `tool-queue/`: mounted only to the scoped plugin; preserve completed requests,
  replies and any unresolved `.request` or `.claimed` files.
- Frozen repository snapshot and manifest in the format required by
  `RepositoryTools`; pinned full commit SHAs and discoverable parent labels.

Credentials stay in existing private environment files. Sealed answer keys stay
outside the repository, operator fixtures, agent mounts and retrieval/capture scope.
The examining operator must not read them; an independent reviewer owns grading.
Record/review the model rollout receipt (`verified: true`) and the existing read-only
voice-idle probe as private inputs. The current utility reuses that probe rather than
inventing a new voice-state authority.

## Render, review, run

1. Copy `operator-config.example.json` outside the checkout and fill it from current
   evidence. All paths must be absolute. Supply the current **Chicago day**, a new
   run id, total historical guard call count, and `base_micro`: current-day charged
   cost plus every unresolved reservation (older reservations remain conservatively
   counted). Supply integer micro-USD/token prices from the live gateway, including
   the highest applicable cache-write rate. Example: $8/M = 8 micro-USD/token.
   Supply reviewed `max_output_tokens` (1–4,096); it sets both the request ceiling
   and output admission estimate. Independently verify the isolated OpenClaw model
   `maxTokens` matches this cap; a lower caller limit is preserved by the guard.
   `window_micro + base_micro` must not exceed 25,000,000. The identical window
   amount is reserved by the normal-chat runner. Do not release unknown costs.
2. Render, without running the generated code:

   ```sh
   python scripts/staging/mc_probe/render_operators.py \
     --config /absolute/private/operator-config.json \
     --out /absolute/private/operators/new-run
   ```

3. Independently review the configuration, five scripts and `MANIFEST.json`; bind
   the review to their hashes. The window verifies all five script hashes and the
   original guard hash before changing anything. Keep the rendered bundle and review
   with the private evidence. A manifest is an integrity check, not approval by itself.
4. Coordinate one active heavy job and an idle regular dev MC. Before 23:30 Chicago,
   execute the reviewed `window.py` using the configured Python (PyYAML required).
   It checks idle ledger/voice, exact model routes, existing budget totals, no earlier
   run output, a disarmed guard and an empty tool queue. It pauses regular dev MC,
   requires >=8 GiB free, pressure <=2, and two consecutive non-growing swap samples
   in at most nine ten-second intervals. Abort is a valid result; preserve >=5 GiB.
5. The window restores its own loopback-only SSH tunnel through the current Colima
   config, verifies the exact portal instance at `127.0.0.1:15001`, then starts and
   verifies the bounded worker **before** starting the agent or arming. No host-port
   publishing or credential changes are needed. The guard admits at most the reviewed
   call count, 400 KB/request, the configured output-token cap, and the reviewed spend allowance.
6. In `finally`, it disarms, stops the isolated agent/worker/tunnel, restores the old
   guard, restores support container states, restarts the same regular dev agent,
   verifies its health, and records cleanup failures and pending tools. Inspect the
   window record; a process exit alone is not proof of restoration.
7. When complete and restored, inspect actual receipts and run the rendered
   `settle.py`. It requires one turn, matching exclusive-key/window admission and
   receipt counts, successful statuses, the expected concrete model, and unused
   receipt IDs. Missing/error/uncorrelated receipts leave the reservation held.
   The provider's gateway receipts are cost evidence; inline estimates are controls.
8. Send the completed journal, tools, summary, policy audit and cost evidence to the
   independent grader. Record functional result, omissions/truncation, citations,
   tool use and model, not merely HTTP success. Do not grant general trust from one
   exam. Use a new run id and re-reviewed baseline for any subsequent attempt.

Each rendered run has a distinct `bounds_<run_id>` table in the existing admission
DB. Old call rows and prior price tables are retained. Admission counts **all**
historical calls, including earlier days; pricing is bound to the reviewed current
day. At midnight it refuses further calls. Stop after three rounds without progress
on the same failure, on unreconciled effects, or on the daily cap.

## Restart recovery and release evidence

After a Mac restart, verify Docker, concrete model routes, ledger/queue state and
regular dev health. Temporary files and SSH tunnels may be gone. The generated
bundle lives outside temporary storage; the window recreates only its own local
transport. Never infer zero spend from missing output: match guard admissions,
gateway receipts and reservation state first. Never reuse an uncertain journal.

For the release record link the source commit and review, frozen exam brief hash,
model/policy/config hashes, completed-run summary, receipt-backed cost, independent
grade, restoration evidence, and remaining limitations. Keep private evidence private;
public release notes should use reviewed sanitized findings. Built, reviewed,
dev-verified and production-verified are separate claims.

## Offline verification

```sh
python -m pytest -q tests/test_proving_operator_package.py
```

These tests render without executing operators and exercise the generated guard's
conservative pricing, historical preservation, date and total limits, and refusal of
unpriced admissions. They make no model, Docker or network calls.
