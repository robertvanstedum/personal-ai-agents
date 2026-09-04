# IoT Connect — standalone beta (v0.9.0-beta.1 candidate)

IoT Connect is a working enterprise-IoT customer and operator demonstration. It
re-enacts the final step in decoupling an IoT platform from a legacy billing
system after provisioning, product configuration, rating, and charge
calculation have already moved to the new platform.

The demonstration is deliberately bounded and entirely synthetic: enough of the
end-to-end system is real to test the operating decision, without claiming to be
a carrier-grade network or billing product. All customer names, identifiers,
volumes, and financial values are invented and vendor-neutral.

> Historical provenance: the prototype was developed under the working name
> *Project Nightjar* and carried an interim product name before this one.
> IoT Connect is now the product name and the technical namespace throughout
> the active standalone package. Earlier design records remain available in
> the history directory without defining the release configuration.

## What the standalone beta demonstrates

- Operator SIM inventory is loaded and assigned to exactly one customer account.
- Enterprise or operator users select eligible SIMs and submit an account-scoped
  activation batch.
- Each SIM is processed independently through synchronous FlowOne-style
  provisioning against a synthetic SOAP service; network evidence records
  `HSS → POLICY → SMSC → AAA`, with AAA conditional on a private APN.
- A failed network element rolls back completed elements, releases the MDN,
  retains the SIM, leaves the subscription in `ACTIVATION_FAILED`, and blocks
  the legacy-billing submission; a failed item can be retried individually.
- Legacy-billing compatibility submission happens only after network success and
  only when account policy requires it.
- Summarized billing is a one-way, audited account control: subscriptions are
  no longer sent to legacy billing and the bill file posts account-scope rows
  with source counts preserved and zero reconciliation variance.
- Detailed source charges remain available for revenue assurance even when the
  posting output is summarized.

## Run it

Requirements on the host: Docker with Compose v2 (Docker Desktop, Colima, or
equivalent), `make`, and `curl`. No Python virtual environment and no absolute
path is required.

```bash
make up        # build, start PostgreSQL + integration stub + IoT Connect, wait until healthy
make status    # health and dependency report
make smoke     # smoke suite (API shapes, Swagger, pages, integration health, bill run writes rows)
make verify    # full verification: reset → smoke → billing → IoT success/failure → restart persistence → SQL check
make reset     # restore the deterministic seed state
make down      # stop; the PostgreSQL volume keeps the demo evidence
```

| Command | Behaviour |
|---|---|
| `make up` | Builds the application image, starts the three services, waits for every health check, prints the URLs. The database schema is applied and an empty database is seeded automatically. |
| `make down` | Stops and removes containers and the network. The named volume `postgres_data` is kept, so `make up` afterwards restores the same data. |
| `make reset` | `POST /api/v1/admin/demo:reset` (truncate and reseed in one transaction) plus `POST /mock/flowone/v1/demo:reset` (clear an armed failure). Idempotent; prints the seed fingerprint so two resets can be compared. |
| `make status` | `docker compose ps`, in-container health of the app, its database query path, the integration stub, both Swagger documents, the FlowOne WSDL, and host-port checks. |
| `make smoke` | Documented `/api/v1` route shapes, error envelopes, Swagger on both services, user-visible pages, presentation assets, and an *output-produced* check: a bill run must write billing rows and charges, reconcile with zero variance, and render its statement artifact. |
| `make verify` | Reset twice and compare fingerprints, smoke, detailed billing (Aster), summarized billing (Boreal: 53 charges → 6 account-scope rows, 310.00), IoT activation success (public and private APN), IoT failure with rollback and retry, the four saved Postman evidence calls, then an application-container restart and a `down`/`up` cycle proving the persisted evidence is unchanged, then an API-versus-SQL comparison of every bill run. Exits non-zero on any failure. |
| `make test` | Runs the pytest suite in a throwaway container on the memory backend. |
| `make scan` | Deterministic scan for secrets, laptop paths, stale product names, personal identifiers, PAN-like numbers, and out-of-scope vendor scope. |
| `make verify-prefix` | Recreates the app with `IOTCONNECT_ROOT_PATH=/app/iotconnect`, checks `/app/iotconnect/openapi.json` reports the prefixed server and that no served page or script carries an unprefixed `/static`, `/api/v1`, `href="/` or `src="/` reference, then restores the default. Also run at the end of `make verify`. |
| `make verify-proxy-auth` | Recreates the app with `IOTCONNECT_AUTH_MODE=minimoi_proxy` and the prefix, proves owner `X-Minimoi-*` headers → 200 on admin and customer-scoped calls, no identity → 403, `X-Demo-*` alone or with a non-owner tier → 403, then restores the defaults and re-checks local mode. Also run at the end of `make verify`. |
| `make lock-check` | Proves the image's installed set is consistent (`pip check`), that `requirements.lock` resolves cleanly, and that every range in `requirements.txt` is pinned in the lock. |
| `CONFIRM=yes make clean` | Stops the stack **and deletes** the database volume. |

**Trusted identity (`IOTCONNECT_AUTH_MODE`).** `local_demo` (default) keeps the
demo headers: the UI and Postman send `X-Demo-Role` / `X-Demo-Account-ID`.
`minimoi_proxy` is for hosting behind the mini-moi portal (hosting spec #154
§4.3/§5): the portal authenticates the user, **strips any `X-Demo-*` headers**
and forwards portal-verified `X-Minimoi-User-Tier` (required; only `owner` is
authorised), `X-Minimoi-Username` and `X-Minimoi-Auth-Id` (informational).
In that mode `X-Demo-*` headers are ignored entirely — a spoofed
`X-Demo-Role` never elevates — and the owner acts as the seeded administrator
and as every seeded customer persona (the UI persona switching is unchanged;
the JavaScript may keep sending `X-Demo-*`). A missing tier or any non-owner
tier is a 403. Any other mode value fails at startup. `make verify-proxy-auth`
proves it against the live stack; a pass through the real portal is not proven
here.

**Reverse-proxy prefix.** Set `IOTCONNECT_ROOT_PATH=/app/iotconnect` (in `.env`
or the environment) when IoT Connect is served behind a portal reverse proxy at
that path. FastAPI is constructed with `root_path`, so `/docs`,
`/openapi.json` (its `servers` entry) and redirects are prefix-correct; every
served page gets one `<meta name="iotconnect-root-path">` tag and every
root-absolute `href`/`src` is prefixed; the JavaScript reads the meta once
(`appPath()` / `API_BASE` in `api-client.js`). Unset (the default) the pages are
served byte-for-byte from disk. `make verify-prefix` proves the prefixed
rendering against the live stack; a pass through a real nginx/portal proxy is
not proven here.

*Prefix contract for the proxy:* with `IOTCONNECT_ROOT_PATH` set, Starlette
matches API routes on both `/api/v1/...` and `/app/iotconnect/api/v1/...`, but
the mounted static files resolve **only** under the prefixed path
(`/static/iotconnect/iotconnect.css` → 404, `/app/iotconnect/static/iotconnect/iotconnect.css`
→ 200). A reverse proxy in front of a prefixed IoT Connect must therefore
forward the **full prefixed path** (the standard root-path contract; the
mini-moi portal does). The Compose health check keeps using the unprefixed
API path, which still routes.

Ports default to **8095** (IoT Connect), **8096** (integration stub), and
**54329** (PostgreSQL, bound to 127.0.0.1). Override them through `.env`
(copy `.env.example`) or the environment, for example
`IOTCONNECT_APP_PORT=18095 make up`.

## Topology

```text
                 ┌──────────────────────────────┐
  browser ─────► │ app  (IoT Connect, :8095)     │ ── FLOWONE_BASE_URL ──────┐
  Postman        │ FastAPI + Pydantic            │ ── AMDOCS_MIDDLEWARE_URL ─┤
  Swagger        │ scripts/start-app.sh          │                          ▼
                 │  └ bootstrap_database.py      │   ┌──────────────────────────────┐
                 └──────────────┬───────────────┘   │ integrations (:8096)          │
                                │ POSTGRES_DSN      │ synthetic FlowOne SOAP +      │
                                ▼                   │ Amdocs-style middleware REST  │
                 ┌──────────────────────────────┐   │ (in-process failure control)  │
                 │ postgres  (PostgreSQL 16)     │   └──────────────────────────────┘
                 │ volume: postgres_data         │
                 └──────────────────────────────┘
```

- `app` waits for `postgres` and `integrations` to be healthy (Compose
  `depends_on … service_healthy`), applies `postgres/01_schema.sql`
  idempotently (`scripts/bootstrap_database.py` is the only schema mechanism —
  there is no `initdb.d` bind mount, so the project path need not be shared
  with the Docker VM), seeds an **empty** database, and never wipes data on
  restart.
- `integrations` is one container exposing both synthetic surfaces, exactly as
  the prototype's single mock process did.
- Direct integration-action resources (`POST/GET /api/v1/network-activations`,
  `POST/GET /api/v1/legacy-subscription-actions`) and every batch-created
  action ID are persisted in PostgreSQL (`control.network_activations`,
  `control.legacy_subscription_actions`), so their GETs return the same body
  after an application restart; `make verify` proves it. The integration
  stub's armed one-shot failure is the only in-process state and is cleared by
  `make reset`.
- Only `app` builds the runtime image; `integrations` reuses the tag with
  `pull_policy: never`, so `make up` never has two writers of one image and an
  app-only restart never recreates the integration stub.
- Both application containers run the same image (`Dockerfile`) as a non-root
  user; configuration is environment-only (`docker-compose.yml`, `.env.example`).
- The image is reproducible: the base is pinned by digest
  (`python:3.12-slim@sha256:78387bc3…184ea`) and Python packages install from
  `requirements.lock` (exact versions that passed verification);
  `requirements.txt` keeps the human-edited ranges.

## URLs

| Surface | URL |
|---|---|
| Launch page | <http://127.0.0.1:8095/> |
| Presentation | <http://127.0.0.1:8095/presentation> |
| Enterprise customer (Aster) | <http://127.0.0.1:8095/portal?account=ACCT-000100> |
| Enterprise customer (Boreal) | <http://127.0.0.1:8095/portal?account=ACCT-000200> |
| Operator application | <http://127.0.0.1:8095/operator> |
| Admin and activation workbench | <http://127.0.0.1:8095/admin> |
| Billing and reconciliation workbench | <http://127.0.0.1:8095/workbench> |
| Detailed-vs-summarized statement comparison | <http://127.0.0.1:8095/artifacts/invoice-comparison> |
| IoT Connect Swagger / OpenAPI | <http://127.0.0.1:8095/docs> · <http://127.0.0.1:8095/openapi.json> |
| Integration Swagger | <http://127.0.0.1:8096/docs> |
| FlowOne WSDL | <http://127.0.0.1:8096/mock/flowone/v1/FlowOneProvisioningService?wsdl> |

## Evidence artifacts

- `postman/IoT_Connect_interview_evidence.postman_collection.json` — four
  read-only evidence calls (base URL `http://127.0.0.1:8095`).
- `postman/IoT_Connect_Demo_API.postman_collection.json` + `IoT_Connect_Demo_API.postman_environment.json`
  — the full API walkthrough (discover → admin setup → bill → evidence).
- `postgres/03_demo_evidence.sql` (`make sql-evidence`), `04_interview_evidence.sql`,
  `05_activation_integration_evidence.sql`, `02_inspect.sql` — read-only SQL.
- `app/static/iotconnect/presentation/slide-{1..5}.png` — the packaged
  presentation slides served at `/presentation/assets/slide-N.png`; pinned by
  `CHECKSUMS.sha256` beside them (checked by `make scan` and the test suite) and
  recorded in [docs/PRESENTATION_VISUAL_REVIEW_2026-09-04.md](docs/PRESENTATION_VISUAL_REVIEW_2026-09-04.md).

## Seed and reset

The seed is deterministic: fixed UUIDs (`uuid5` from display numbers), fixed
account/contract/legacy-account values, 1,000 SIMs and 1,000 MDNs generated
from fixed patterns, sequences restarted, and a fixed seed timestamp
(`IOTCONNECT_SEED_TIMESTAMP`, default `2026-08-01T00:00:00+00:00`). `make reset`
truncates and reseeds inside one database transaction; running it twice yields
the same API fingerprint, which `make verify` proves.

## Repository adapters

`IOTCONNECT_STORE` selects the repository: `postgres` is the operating database
(the delivered stack); `memory` (the default when unset) is for the test suite
and for `scripts/run-memory-demo.sh` as a no-Docker fallback.

**Snowflake is unsupported as an application database in IoT Connect v0.9**
(decision 2026-09-03). Selecting `IOTCONNECT_STORE=snowflake` — or any value other
than `postgres`/`memory` — fails immediately at startup with a clear message.
The adapter source is retained in `app/repositories/snowflake.py` for future
reference; Snowflake may return later as a downstream reporting
destination, not in the activation path.

## Design references

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — reusable UI → API → service → repository pattern.
- [docs/CANONICAL_DATA_MODEL_AND_ORDERING_FLOW_2026-08-28.md](docs/CANONICAL_DATA_MODEL_AND_ORDERING_FLOW_2026-08-28.md)
- [docs/FLOWONE_PROVISIONING_COMPONENT_SPEC_2026-08-28.md](docs/FLOWONE_PROVISIONING_COMPONENT_SPEC_2026-08-28.md)
- [docs/AMDOCS_MIDDLEWARE_COMPONENT_SPEC_2026-08-28.md](docs/AMDOCS_MIDDLEWARE_COMPONENT_SPEC_2026-08-28.md)
- [docs/history/connect-hq/INDEX.md](docs/history/connect-hq/INDEX.md) — retained design-stage sources and provenance.
- [HANDS_ON_RUNBOOK.md](HANDS_ON_RUNBOOK.md) — operate, verify, recover, stop.

## Explicit boundaries

- no SIM purchasing, usage metering, real-time balance control, or overage charging;
- no live network telemetry and no cross-customer lifecycle batch;
- no production authentication or public authorization — roles are demo headers;
- no cloud deployment, CI pipeline, or shared-package extraction in this release.
