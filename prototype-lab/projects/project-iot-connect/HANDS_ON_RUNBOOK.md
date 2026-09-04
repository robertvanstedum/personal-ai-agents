# IoT Connect — Demo Operations and Recovery Runbook

Use this runbook to start, verify, recover, and stop the IoT Connect enterprise
connectivity demonstration on any machine with Docker. The whole stack runs in
containers; nothing depends on a host virtual environment or a fixed folder
location. Run every command from the project directory (the one containing
`Makefile` and `docker-compose.yml`).

## Mental model

| Service | Address | Responsibility |
|---|---:|---|
| `app` — IoT Connect | `127.0.0.1:8095` | Launch page, presentation, customer and operator applications, APIs, Swagger, technical workbenches |
| `integrations` | `127.0.0.1:8096` | Synthetic FlowOne SOAP provisioning and legacy-billing middleware REST, with a one-shot failure control |
| `postgres` | `127.0.0.1:54329` | Persistent operational and billing evidence in the named volume `postgres_data` |

A bare request to `/` on 8096 returns `{"detail":"Not Found"}`; that is
harmless because 8096 is an API service, not a website. Open `/docs` there.

## 1. Check before starting anything

```bash
make status
```

If the three services already show `healthy` and both health calls return
`"status":"ok"`, the demo is running. Do not start a second copy.

If ports 8095/8096/54329 are used by something else, either stop that process
or start IoT Connect on other ports:

```bash
IOTCONNECT_APP_PORT=18095 IOTCONNECT_INTEGRATIONS_PORT=18096 IOTCONNECT_DB_PORT=15432 make up
```

(Or copy `.env.example` to `.env` and edit the values there.)

Two hosting switches live in `.env`: `IOTCONNECT_ROOT_PATH` (URL prefix behind a
reverse proxy) and `IOTCONNECT_AUTH_MODE` (`local_demo` for this runbook's
local demo; `minimoi_proxy` only behind the mini-moi portal, which supplies
the verified `X-Minimoi-*` identity headers and strips `X-Demo-*`). Leave both
at their defaults for a laptop demo.

## 2. Start from a cold machine

```bash
docker info          # Docker must be running (Docker Desktop, or: colima start)
make up
```

`make up` builds the image on first use, starts PostgreSQL, the integration
stub, and IoT Connect, waits for all three health checks, and prints the URLs.
On the very first start the database schema is applied and the deterministic
demo data is seeded automatically; on later starts existing data is kept.

Expected `make status` output includes:

```text
PASS  IoT Connect /api/v1/health
PASS  IoT Connect data backend = postgres
PASS  IoT Connect database query (GET /api/v1/accounts)
PASS  Integration stub /mock/flowone/v1/health
```

## 3. Five-minute preflight

```bash
make status
make smoke
```

Then open and verify:

| Surface | URL |
|---|---|
| Launch page | <http://127.0.0.1:8095/> |
| Presentation | <http://127.0.0.1:8095/presentation> |
| Aster customer | <http://127.0.0.1:8095/portal?account=ACCT-000100> |
| Boreal customer | <http://127.0.0.1:8095/portal?account=ACCT-000200> |
| Operator application | <http://127.0.0.1:8095/operator> |
| Developer/support workbench | <http://127.0.0.1:8095/admin> |
| Billing workbench | <http://127.0.0.1:8095/workbench> |
| IoT Connect Swagger | <http://127.0.0.1:8095/docs> |
| Integration Swagger | <http://127.0.0.1:8096/docs> |

The **C / IoT Connect** brand returns customer and operator pages to the launch
page. If a page predates a code change, press `Command-Shift-R` once.

Note that `make smoke` creates one bill run for Aster on the first unused bill
cycle (it must prove that a bill run writes rows). Run `make reset` afterwards
if you want a pristine dataset for the rehearsal.

## 4. Prepare deterministic demo data

Only reset at a planned preparation point. Never reset while presenting or
after a failure; the reset deletes the current demo transactions and restores
the two prepared accounts.

```bash
make reset
```

This restores Aster and Boreal, 1,000 unassigned SIMs, 1,000 available MDNs,
detailed billing on both accounts, no rehearsal transactions, and clears any
armed one-shot FlowOne failure. The command prints a seed fingerprint; it is
identical on every run. Reload any already-open browser tabs.

The admin workbench's **Reset prepared demo** button does the same for the
application data (it does not clear an armed FlowOne failure; use `make reset`
or `curl -X POST http://127.0.0.1:8096/mock/flowone/v1/demo:reset`).

## 5. Rehearsal sequence

1. Open the operator portfolio, then enter a selected customer account.
2. Use SIM Inventory to assign operator-owned stock to that account.
3. Open the account's Subscriptions & SIMs page and hand selected resources to
   the governed activation flow.
4. Submit an activation batch and inspect one outcome per SIM, including the
   network-element evidence and conditional legacy-billing submission.
5. To show a network failure and rollback, arm the stub first:
   `curl -X POST http://127.0.0.1:8096/mock/flowone/v1/demo/fail-next -H 'Content-Type: application/json' -d '{"element":"POLICY"}'`
   then submit a one-SIM batch and use the visible retry control.
6. Use Account Configuration to enable summarized posting for the prepared
   account. The change is one-way; reversal requires a migration.
7. Run the operator bill cycle and inspect the independent reconciled runs.
8. Open the customer billing view and the illustrative legacy statement.
9. Use the workbench and `make sql-evidence` to compare detailed source charges
   with summarized posting output.
10. Repeat selected API calls in Swagger or the Postman collections.

`make verify` performs steps 2–9 automatically and prints a pass/fail table.

## 6. Recovery matrix

### A service is not healthy

```bash
make status
make logs            # Control-C to stop following
```

Restart only the affected service, for example `docker compose restart app`,
then `make status` again. Successful activations are committed in PostgreSQL;
use the visible retry control only for failed items. Never resubmit a completed
batch.

### `port is already allocated`

Another process owns 8095, 8096, or 54329. Either stop it or start on other
ports (section 1). Do not run two copies of the stack on the same ports.

### Browser shows `{"detail":"Not Found"}`

- On 8096 `/`, this is expected. Open `/docs` or `/mock/flowone/v1/health`.
- On 8095, verify the URL is exactly `http://127.0.0.1:8095/`.
- Do not open an HTML file from Finder with a `file://` URL.

### Customer or operator page looks stale

Press `Command-Shift-R`. If necessary, return to the launch page and reopen the
view.

### Bill cycle reports accounts as skipped

Read the reason displayed on the Bill Cycles page. The common causes are:

- the account has no active subscriptions;
- that account and cycle were already billed;
- a reconciliation control failed.

For rehearsal, use a new cycle such as `2026-11`, or reset before starting
over. Never reset in the middle of the live presentation.

### Docker is unavailable

Start Docker (Docker Desktop, or `colima start`), then `make up`. The database
volume survives Docker restarts.

### PostgreSQL cannot be recovered quickly

Fallback without Docker (application and API only, no persistence, no SQL
proof), if a Python virtual environment with `requirements.lock` exists. The
memory store is the only fallback; Snowflake is unsupported as an application
database in v0.9 and the application refuses to start with it:

```bash
./scripts/run-memory-demo.sh
```

Say:

> I have switched to the portable repository so we can continue the workflow.
> I will use the API response and prepared evidence for persistence rather than
> pretend this is the PostgreSQL-backed run.

## 7. SQL access and evidence

```bash
make psql            # interactive prompt; exit with \q
make sql-evidence    # runs postgres/03_demo_evidence.sql
```

The two most important tables are:

- `control.bill_runs` — account-level run status and reconciliation totals;
- `legacy.billing_rows` — the detailed or summarized outbound biller rows.

`make verify-sql` compares every bill run in `control.bill_runs` with the
`/api/v1/bill-runs` API and reports `PASS` when they are identical.

External SQL tools connect to `127.0.0.1:54329`, database `iotconnect`, user
`iotconnect_app`, password as in `.env.example` (a local demo constant).

## 8. Clean shutdown

```bash
make down
```

Containers stop; the PostgreSQL volume and therefore the demo evidence remain.
`make up` later restores the same state. Only `CONFIRM=yes make clean` deletes
the volume.
