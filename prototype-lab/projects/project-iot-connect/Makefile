# IoT Connect standalone beta — one command per operation.
#
#   make up       build images, start PostgreSQL + integrations + IoT Connect, wait until healthy
#   make down     stop the stack; the PostgreSQL volume (demo evidence) is kept
#   make reset    restore the deterministic seed state (app data + integration failure arm)
#   make status   health and dependency report for all three services
#   make smoke    smoke suite (API shapes, Swagger, pages, integration health, bill-run output written)
#   make verify   reset → smoke → summarized billing → IoT success → IoT failure/rollback
#                 → direct action POST/GET capture → app-only restart (isolation proven) → GET bodies unchanged
#                 → down/up persistence → SQL/API consistency
#   make verify-restart   only the app-only restart isolation check
#   make verify-prefix    URL-prefix (reverse proxy) rendering check with IOTCONNECT_ROOT_PATH=/app/iotconnect
#   make verify-proxy-auth  trusted-identity check with IOTCONNECT_AUTH_MODE=minimoi_proxy + the prefix, then restore
#
# Optional:
#   make build         rebuild the application image
#   make logs          follow container logs
#   make test          run the pytest suite in a throwaway container (memory backend)
#   make psql          interactive SQL prompt
#   make sql-evidence  run postgres/03_demo_evidence.sql
#   make scan          secret / absolute-path / stale-name / scope scan of the tree
#   make lock-check    dependency lock consistency (pip check, lock resolves, ranges pinned)
#   make clean         CONFIRM=yes make clean — stop AND delete the database volume
#
# Host ports default to 8095 (IoT Connect), 8096 (integration stub), 54329 (PostgreSQL).
# Override through .env (see .env.example) or the environment, e.g.
#   IOTCONNECT_APP_PORT=18095 make up
# Requirements on the host: Docker with Compose v2, make, curl. No Python needed.

SHELL := /bin/sh
-include .env
export

COMPOSE ?= docker compose
IOTCONNECT_APP_PORT ?= 8095
IOTCONNECT_INTEGRATIONS_PORT ?= 8096
IOTCONNECT_DB_PORT ?= 54329
IOTCONNECT_DB_NAME ?= iotconnect
IOTCONNECT_DB_USER ?= iotconnect_app

CONTROL := scripts/iotconnect_demo.py
PY_IN_APP := $(COMPOSE) exec -T app python
PSQL := $(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 -U $(IOTCONNECT_DB_USER) -d $(IOTCONNECT_DB_NAME)
VERIFY_DIR := .verify

.PHONY: up down reset status smoke verify verify-sql verify-restart verify-prefix verify-proxy-auth lock-check build logs test test-postgres psql sql-evidence scan clean urls

up:
	$(COMPOSE) build app
	$(COMPOSE) up -d --wait
	@$(MAKE) --no-print-directory urls

urls:
	@echo ""
	@echo "IoT Connect           http://127.0.0.1:$(IOTCONNECT_APP_PORT)/"
	@echo "IoT Connect Swagger   http://127.0.0.1:$(IOTCONNECT_APP_PORT)/docs"
	@echo "Admin workbench       http://127.0.0.1:$(IOTCONNECT_APP_PORT)/admin"
	@echo "Integration Swagger   http://127.0.0.1:$(IOTCONNECT_INTEGRATIONS_PORT)/docs"
	@echo "FlowOne WSDL          http://127.0.0.1:$(IOTCONNECT_INTEGRATIONS_PORT)/mock/flowone/v1/FlowOneProvisioningService?wsdl"
	@echo "PostgreSQL            127.0.0.1:$(IOTCONNECT_DB_PORT)  db=$(IOTCONNECT_DB_NAME) user=$(IOTCONNECT_DB_USER)"
	@echo ""

down:
	$(COMPOSE) down

build:
	$(COMPOSE) build app

logs:
	$(COMPOSE) logs -f

reset:
	$(PY_IN_APP) $(CONTROL) reset

status:
	$(COMPOSE) ps
	$(PY_IN_APP) $(CONTROL) status
	@echo "host port checks:"
	@curl -fsS -o /dev/null -w "  http://127.0.0.1:$(IOTCONNECT_APP_PORT)/api/v1/health -> %{http_code}\n" http://127.0.0.1:$(IOTCONNECT_APP_PORT)/api/v1/health
	@curl -fsS -o /dev/null -w "  http://127.0.0.1:$(IOTCONNECT_INTEGRATIONS_PORT)/mock/flowone/v1/health -> %{http_code}\n" http://127.0.0.1:$(IOTCONNECT_INTEGRATIONS_PORT)/mock/flowone/v1/health
	@$(COMPOSE) exec -T postgres pg_isready -U $(IOTCONNECT_DB_USER) -d $(IOTCONNECT_DB_NAME) | sed 's/^/  /'

smoke:
	$(PY_IN_APP) $(CONTROL) smoke
	@echo "host-side Swagger checks:"
	@curl -fsS -o /dev/null -w "  http://127.0.0.1:$(IOTCONNECT_APP_PORT)/docs -> %{http_code}\n" http://127.0.0.1:$(IOTCONNECT_APP_PORT)/docs
	@curl -fsS -o /dev/null -w "  http://127.0.0.1:$(IOTCONNECT_INTEGRATIONS_PORT)/docs -> %{http_code}\n" http://127.0.0.1:$(IOTCONNECT_INTEGRATIONS_PORT)/docs

verify:
	@mkdir -p $(VERIFY_DIR)
	$(PY_IN_APP) $(CONTROL) verify-before > $(VERIFY_DIR)/evidence.json
	@echo "--- restarting ONLY the application container (isolation proven by scripts/check_restart_isolation.sh) ---"
	sh scripts/check_restart_isolation.sh
	$(PY_IN_APP) $(CONTROL) verify-after --label "after app restart" < $(VERIFY_DIR)/evidence.json
	@echo "--- down, then up ---"
	$(COMPOSE) down
	$(COMPOSE) up -d --wait
	$(PY_IN_APP) $(CONTROL) verify-after --label "after down/up" < $(VERIFY_DIR)/evidence.json
	@$(MAKE) --no-print-directory verify-sql
	@$(MAKE) --no-print-directory verify-prefix
	@$(MAKE) --no-print-directory verify-proxy-auth
	@echo "VERIFY: all phases passed"

verify-restart:
	sh scripts/check_restart_isolation.sh

PREFIX_UNDER_TEST ?= /app/iotconnect

verify-prefix:
	@echo "--- recreating app with IOTCONNECT_ROOT_PATH=$(PREFIX_UNDER_TEST) ---"
	IOTCONNECT_ROOT_PATH=$(PREFIX_UNDER_TEST) $(COMPOSE) up -d --wait app
	sh scripts/check_url_prefix.sh http://127.0.0.1:$(IOTCONNECT_APP_PORT) $(PREFIX_UNDER_TEST)
	@echo "--- restoring the default (no prefix) ---"
	IOTCONNECT_ROOT_PATH= $(COMPOSE) up -d --wait app
	sh scripts/check_url_prefix.sh http://127.0.0.1:$(IOTCONNECT_APP_PORT) ""

verify-proxy-auth:
	@echo "--- recreating app with IOTCONNECT_AUTH_MODE=minimoi_proxy IOTCONNECT_ROOT_PATH=$(PREFIX_UNDER_TEST) ---"
	IOTCONNECT_AUTH_MODE=minimoi_proxy IOTCONNECT_ROOT_PATH=$(PREFIX_UNDER_TEST) $(COMPOSE) up -d --wait app
	sh scripts/check_proxy_auth.sh http://127.0.0.1:$(IOTCONNECT_APP_PORT) $(PREFIX_UNDER_TEST) minimoi_proxy
	@echo "--- restoring defaults (local_demo, no prefix) ---"
	IOTCONNECT_AUTH_MODE=local_demo IOTCONNECT_ROOT_PATH= $(COMPOSE) up -d --wait app
	sh scripts/check_proxy_auth.sh http://127.0.0.1:$(IOTCONNECT_APP_PORT) "" local_demo

verify-sql:
	@mkdir -p $(VERIFY_DIR)
	$(PY_IN_APP) $(CONTROL) bill-runs-table > $(VERIFY_DIR)/api_bill_runs.txt
	$(PSQL) -At -F'|' -c "SELECT bill_run_number, account_number, billing_mode, bill_cycle, status, source_charge_count, output_row_count, source_total, output_total, variance FROM control.bill_runs ORDER BY bill_run_number" > $(VERIFY_DIR)/sql_bill_runs.txt
	@diff $(VERIFY_DIR)/api_bill_runs.txt $(VERIFY_DIR)/sql_bill_runs.txt && echo "SQL/API consistency: PASS ($$(wc -l < $(VERIFY_DIR)/sql_bill_runs.txt | tr -d ' ') bill runs identical in API and PostgreSQL)"

test:
	$(COMPOSE) run --rm --no-deps -e IOTCONNECT_STORE=memory app python -m pytest -q -p no:cacheprovider

# Runs the same suite against the running PostgreSQL. The suite RESETS the demo data.
test-postgres:
	@[ "$(CONFIRM)" = "yes" ] || { echo "This resets the demo database. Re-run with CONFIRM=yes"; exit 1; }
	$(COMPOSE) exec -T app sh -c 'IOTCONNECT_TEST_POSTGRES_DSN=$$POSTGRES_DSN python -m pytest -q -p no:cacheprovider'

psql:
	$(COMPOSE) exec postgres psql -U $(IOTCONNECT_DB_USER) -d $(IOTCONNECT_DB_NAME)

sql-evidence:
	$(PSQL) < postgres/03_demo_evidence.sql

scan:
	sh scripts/scan_candidate.sh

lock-check:
	$(COMPOSE) run --rm --no-deps -e IOTCONNECT_STORE=memory app sh -c 'pip check && pip install --dry-run -r requirements.lock && python -m pytest -q -p no:cacheprovider tests/test_dependency_lock.py'

clean:
	@[ "$(CONFIRM)" = "yes" ] || { echo "This deletes the PostgreSQL volume. Re-run with CONFIRM=yes"; exit 1; }
	$(COMPOSE) down -v
