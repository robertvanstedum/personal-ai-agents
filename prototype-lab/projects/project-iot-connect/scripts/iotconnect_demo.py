#!/usr/bin/env python3
"""IoT Connect standalone demo control — reset, status, smoke, verify.

Standard library only, so it runs unchanged inside the application container
(the default used by the Makefile) or on any host with Python 3.9+.

Commands
    reset            restore the deterministic seed state and clear the armed
                     integration failure; prints the seed fingerprint
    status           health of IoT Connect, its database path, and the
                     integration stub, plus both Swagger documents
    smoke            documented API shapes, Swagger, pages, integration
                     health, and an output-produced billing check
    verify-before    reset twice (determinism) → smoke → summarized billing →
                     IoT success → IoT failure/rollback → Postman-equivalent
                     evidence calls → direct action POST/GET capture; prints a
                     table on stderr and the evidence JSON on stdout
    verify-after     read that JSON from stdin and prove the persisted
                     evidence is unchanged after a restart, including that
                     every captured direct-action GET still returns 200 with
                     the identical body (a 404 fails verification)
    evidence         print the current evidence fingerprint JSON
    bill-runs-table  bill runs as pipe-separated rows for SQL comparison

Base URLs come from IOTCONNECT_APP_URL / IOTCONNECT_INTEGRATIONS_URL, else from
IOTCONNECT_APP_PORT / IOTCONNECT_INTEGRATIONS_PORT on 127.0.0.1, else 8095/8096.
A .env file next to the project root is honoured when present.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
ADMIN_HEADERS = {"X-Demo-Role": "BUSINESS_OPS_ADMIN"}
ASTER = "ACCT-000100"
BOREAL = "ACCT-000200"
CANDIDATE_CYCLES = [f"2026-{month:02d}" for month in range(8, 13)] + [
    f"2027-{month:02d}" for month in range(1, 13)
]


# --------------------------------------------------------------------------- #
# configuration and HTTP
# --------------------------------------------------------------------------- #
def load_dotenv() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def app_url() -> str:
    return os.getenv("IOTCONNECT_APP_URL") or (
        f"http://127.0.0.1:{os.getenv('IOTCONNECT_APP_PORT', '8095')}"
    )


def integrations_url() -> str:
    return os.getenv("IOTCONNECT_INTEGRATIONS_URL") or (
        f"http://127.0.0.1:{os.getenv('IOTCONNECT_INTEGRATIONS_PORT', '8096')}"
    )


class Response:
    def __init__(self, status: int, headers: dict, body: bytes) -> None:
        self.status = status
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body = body

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


def request(
    method: str,
    url: str,
    *,
    json_body: Any = None,
    data: Optional[bytes] = None,
    headers: Optional[dict] = None,
    timeout: float = 60.0,
) -> Response:
    merged = dict(headers or {})
    payload = data
    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
        merged.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=payload, method=method, headers=merged)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return Response(response.status, dict(response.headers), response.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, dict(exc.headers), exc.read())
    except urllib.error.URLError as exc:
        raise SystemExit(f"cannot reach {url}: {exc.reason}")


def app(method: str, path: str, **kwargs) -> Response:
    return request(method, app_url() + path, **kwargs)


def integrations(method: str, path: str, **kwargs) -> Response:
    return request(method, integrations_url() + path, **kwargs)


def admin(method: str, path: str, **kwargs) -> Response:
    headers = {**ADMIN_HEADERS, **kwargs.pop("headers", {})}
    return app(method, path, headers=headers, **kwargs)


# --------------------------------------------------------------------------- #
# result table
# --------------------------------------------------------------------------- #
class Report:
    def __init__(self, title: str) -> None:
        self.title = title
        self.rows: list[tuple[str, str, str]] = []

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        self.rows.append((name, "PASS" if condition else "FAIL", detail))
        return bool(condition)

    def expect(self, name: str, actual: Any, expected: Any) -> bool:
        return self.check(name, actual == expected, f"expected {expected!r}, got {actual!r}")

    @property
    def failed(self) -> int:
        return sum(1 for _, status, _ in self.rows if status == "FAIL")

    def render(self, stream=sys.stderr) -> None:
        width = max((len(name) for name, _, _ in self.rows), default=20)
        print(f"\n{self.title}", file=stream)
        print("-" * (width + 8), file=stream)
        for name, status, detail in self.rows:
            suffix = f"  ({detail})" if status == "FAIL" and detail else ""
            print(f"{status:<5} {name:<{width}}{suffix}", file=stream)
        print("-" * (width + 8), file=stream)
        total = len(self.rows)
        print(f"{total - self.failed}/{total} passed, {self.failed} failed", file=stream)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# domain helpers
# --------------------------------------------------------------------------- #
def accounts() -> list[dict]:
    return app("GET", "/api/v1/accounts").json()


def account_by_number(number: str) -> dict:
    for row in accounts():
        if row["account_number"] == number:
            return row
    raise SystemExit(f"account {number} not found")


def free_cycle(account_id: str) -> str:
    used = {run["bill_cycle"] for run in app("GET", f"/api/v1/bill-runs?account_id={account_id}").json()}
    for cycle in CANDIDATE_CYCLES:
        if cycle not in used:
            return cycle
    raise SystemExit("no free bill cycle left; run `make reset`")


def upload_fixture(account_id: str, fixture: str) -> Response:
    body = (BASE_DIR / "fixtures" / fixture).read_bytes()
    return admin(
        "POST",
        f"/api/v1/admin/accounts/{account_id}/subscriptions:upload",
        data=body,
        headers={"Content-Type": "text/csv"},
    )


def assign_sims(account_id: str, count: int) -> list[dict]:
    available = app("GET", "/api/v1/inventory/sims/available").json()[:count]
    if len(available) < count:
        raise SystemExit("not enough available SIMs")
    response = admin(
        "POST",
        f"/api/v1/admin/accounts/{account_id}/sim-assignments",
        json_body={"sim_resource_ids": [row["sim_resource_id"] for row in available]},
    )
    if response.status != 200:
        raise SystemExit(f"SIM assignment failed: {response.text}")
    return available


def create_batch(account_id: str, sims: list[dict], prefix: str, private_apn: Optional[str] = None) -> Response:
    return admin(
        "POST",
        f"/api/v1/admin/accounts/{account_id}/activation-batches",
        json_body={
            "items": [
                {
                    "source_order_ref": f"{prefix}-{index:03d}",
                    "sim_resource_id": row["sim_resource_id"],
                    "product_offering_id": "OFFER-IOT-CONNECTIVITY",
                    "price_plan_id": "PLAN-IOT-001",
                    "technical_profile_id": "NET-DATA-SMS-DOM",
                    "private_apn": private_apn,
                }
                for index, row in enumerate(sims, start=1)
            ]
        },
    )


def submit_batch(batch_id: str) -> Response:
    return admin("POST", f"/api/v1/admin/activation-batches/{batch_id}:submit")


def seed_fingerprint() -> dict:
    sims = app("GET", "/api/v1/inventory/sims/available").json()
    projection = {
        "accounts": accounts(),
        "account_summaries": app("GET", "/api/v1/account-summaries").json(),
        "rate_plans": app("GET", "/api/v1/catalog/rate-plans").json(),
        "available_sim_count": len(sims),
        "first_sim": sims[0] if sims else None,
        "last_sim": sims[-1] if sims else None,
        "legacy_accounts_available": admin("GET", "/api/v1/admin/legacy-accounts/available").json(),
        "activation_batches": admin("GET", "/api/v1/admin/activation-batches").json(),
        "bill_runs": app("GET", "/api/v1/bill-runs").json(),
        "aster_subscriptions": app("GET", f"/api/v1/accounts/{ASTER}/subscriptions?system=iot").json(),
        "boreal_subscriptions": app("GET", f"/api/v1/accounts/{BOREAL}/subscriptions?system=iot").json(),
    }
    return {"sha256": digest(projection), "projection": projection}


def evidence_fingerprint() -> dict:
    projection: dict[str, Any] = {
        "accounts": accounts(),
        "bill_runs": app("GET", "/api/v1/bill-runs").json(),
        "boreal_policy": app("GET", f"/api/v1/demo-evidence/accounts/{BOREAL}/billing-policy").json(),
        "aster_policy": app("GET", f"/api/v1/demo-evidence/accounts/{ASTER}/billing-policy").json(),
        "aster_subscriptions": app("GET", f"/api/v1/accounts/{ASTER}/subscriptions?system=iot").json(),
        "boreal_subscriptions": app("GET", f"/api/v1/accounts/{BOREAL}/subscriptions?system=iot").json(),
        "aster_legacy_lines": app("GET", f"/api/v1/accounts/{ASTER}/subscriptions?system=legacy").json(),
        "activation_batches": admin("GET", "/api/v1/admin/activation-batches").json(),
    }
    for number in (ASTER, BOREAL):
        latest = app("GET", f"/api/v1/demo-evidence/accounts/{number}/latest-activation")
        projection[f"latest_activation_{number}"] = latest.json() if latest.status == 200 else {"status": latest.status}
    projection["bill_run_files"] = {
        run["bill_run_id"]: app("GET", f"/api/v1/bill-runs/{run['bill_run_id']}/file").json()
        for run in projection["bill_runs"]
    }
    projection["bill_run_reconciliations"] = {
        run["bill_run_id"]: app("GET", f"/api/v1/bill-runs/{run['bill_run_id']}/reconciliation").json()
        for run in projection["bill_runs"]
    }
    return {"sha256": digest(projection), "projection": projection}


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def do_reset(quiet: bool = False) -> dict:
    reset = admin("POST", "/api/v1/admin/demo:reset")
    if reset.status != 200:
        raise SystemExit(f"reset failed ({reset.status}): {reset.text}")
    stub = integrations("POST", "/mock/flowone/v1/demo:reset")
    if stub.status != 200:
        raise SystemExit(f"integration reset failed ({stub.status}): {stub.text}")
    fingerprint = seed_fingerprint()
    if not quiet:
        print(json.dumps(reset.json(), indent=2))
        print(f"integration stub: {stub.json()}")
        print(f"seed fingerprint (sha256 of API projection): {fingerprint['sha256']}")
    return fingerprint


def cmd_reset(_: argparse.Namespace) -> int:
    do_reset()
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    report = Report(f"IoT Connect status  app={app_url()}  integrations={integrations_url()}")
    health = app("GET", "/api/v1/health")
    body = health.json() if health.status == 200 else {}
    report.check("IoT Connect /api/v1/health", health.status == 200 and body.get("status") == "ok", health.text[:120])
    report.check(f"IoT Connect data backend = {body.get('data_backend')}", body.get("data_backend") in {"postgres", "memory"})
    rows = app("GET", "/api/v1/accounts")
    report.check("IoT Connect database query (GET /api/v1/accounts)", rows.status == 200 and len(rows.json()) >= 2, rows.text[:120])
    report.check("IoT Connect Swagger /docs", app("GET", "/docs").status == 200)
    report.check("IoT Connect OpenAPI /openapi.json", app("GET", "/openapi.json").status == 200)
    stub = integrations("GET", "/mock/flowone/v1/health")
    report.check("Integration stub /mock/flowone/v1/health", stub.status == 200 and stub.json().get("status") == "ok", stub.text[:120])
    report.check("Integration Swagger /docs", integrations("GET", "/docs").status == 200)
    report.check("FlowOne WSDL", "definitions" in integrations("GET", "/mock/flowone/v1/FlowOneProvisioningService?wsdl").text)
    report.render()
    return 1 if report.failed else 0


def run_smoke(report: Report) -> None:
    health = app("GET", "/api/v1/health")
    body = health.json() if health.status == 200 else {}
    report.check("health returns documented shape", health.status == 200 and {"status", "service", "api_version", "data_backend"} <= set(body), health.text[:120])
    report.expect("health service name", body.get("service"), "IoT Connect")
    expected_backend = os.getenv("IOTCONNECT_EXPECTED_BACKEND", "postgres")
    report.expect(f"health data backend is {expected_backend}", body.get("data_backend"), expected_backend)

    plans = app("GET", "/api/v1/catalog/rate-plans")
    report.check("catalog/rate-plans lists PLAN-IOT-001", plans.status == 200 and any(row["rate_plan_id"] == "PLAN-IOT-001" for row in plans.json()))
    offerings = app("GET", "/api/v1/catalog/product-offerings")
    report.check("catalog/product-offerings lists OFFER-IOT-CONNECTIVITY", offerings.status == 200 and any(row["product_offering_id"] == "OFFER-IOT-CONNECTIVITY" for row in offerings.json()))
    profiles = app("GET", "/api/v1/catalog/network-profiles")
    report.check("catalog/network-profiles lists NET-DATA-SMS-DOM", profiles.status == 200 and any(row["technical_profile_id"] == "NET-DATA-SMS-DOM" for row in profiles.json()))

    rows = accounts()
    numbers = {row["account_number"] for row in rows}
    report.check("accounts include the two prepared accounts", {ASTER, BOREAL} <= numbers, str(sorted(numbers)))
    aster = account_by_number(ASTER)
    boreal = account_by_number(BOREAL)
    report.check("account-summaries returns one view per account", len(app("GET", "/api/v1/account-summaries").json()) == len(rows))
    report.check("accounts/{id}", app("GET", f"/api/v1/accounts/{aster['account_id']}").json()["account_number"] == ASTER)
    summary = app("GET", f"/api/v1/accounts/{boreal['account_id']}/summary").json()
    report.check("accounts/{id}/summary has account and counters", summary.get("account", {}).get("account_number") == BOREAL and "active_subscriptions" in summary)
    report.check("accounts/{id}/resources", app("GET", f"/api/v1/accounts/{aster['account_id']}/resources").status == 200)
    available = app("GET", "/api/v1/inventory/sims/available")
    report.check("inventory/sims/available returns operator stock", available.status == 200 and len(available.json()) > 0)
    report.check("accounts/{id}/inventory/sims", app("GET", f"/api/v1/accounts/{aster['account_id']}/inventory/sims").status == 200)
    report.check("admin/legacy-accounts/available (admin role)", admin("GET", "/api/v1/admin/legacy-accounts/available").status == 200)
    report.check("admin/activation-batches (admin role)", admin("GET", "/api/v1/admin/activation-batches").status == 200)
    report.check("bill-runs list", app("GET", "/api/v1/bill-runs").status == 200)
    policy = app("GET", f"/api/v1/demo-evidence/accounts/{BOREAL}/billing-policy")
    report.check("demo-evidence billing-policy shape", policy.status == 200 and {"billing_mode", "summarized_billing_enabled", "posting_scope"} <= set(policy.json()))
    report.check("accounts/{number}/subscriptions?system=iot", app("GET", f"/api/v1/accounts/{ASTER}/subscriptions?system=iot").status == 200)
    report.check("accounts/{number}/subscriptions?system=legacy", app("GET", f"/api/v1/accounts/{ASTER}/subscriptions?system=legacy").status == 200)
    forbidden = app("POST", "/api/v1/admin/demo:reset", headers={"X-Demo-Role": "ENTERPRISE_CUSTOMER"})
    report.check("admin route with a non-admin role returns 403 FORBIDDEN envelope", forbidden.status == 403 and forbidden.json().get("code") == "FORBIDDEN", forbidden.text[:120])
    missing = app("POST", "/api/v1/admin/demo:reset")
    report.check("admin route without the role header returns 422 contract error", missing.status == 422 and missing.json().get("code") == "REQUEST_VALIDATION_ERROR", missing.text[:120])
    bad = app("POST", "/api/v1/network-activations", json_body={"imsi": "x"})
    report.check("request validation returns 422 REQUEST_VALIDATION_ERROR envelope", bad.status == 422 and bad.json().get("code") == "REQUEST_VALIDATION_ERROR", bad.text[:120])
    report.check("responses carry X-Request-ID", "x-request-id" in health.headers)

    docs = app("GET", "/docs")
    report.check("IoT Connect Swagger loads", docs.status == 200 and "swagger" in docs.text.lower())
    openapi = app("GET", "/openapi.json")
    report.check("IoT Connect OpenAPI lists /api/v1/health", openapi.status == 200 and "/api/v1/health" in openapi.json().get("paths", {}))

    stub_health = integrations("GET", "/mock/flowone/v1/health")
    report.check("integration stub health", stub_health.status == 200 and stub_health.json().get("status") == "ok", stub_health.text[:120])
    stub_docs = integrations("GET", "/docs")
    report.check("integration Swagger loads", stub_docs.status == 200 and "swagger" in stub_docs.text.lower())
    stub_openapi = integrations("GET", "/openapi.json")
    report.check("integration OpenAPI lists Amdocs middleware route", stub_openapi.status == 200 and "/mock/amdocs-middleware/v1/subscription-actions" in stub_openapi.json().get("paths", {}))
    wsdl = integrations("GET", "/mock/flowone/v1/FlowOneProvisioningService?wsdl")
    report.check("FlowOne WSDL served", wsdl.status == 200 and "definitions" in wsdl.text)

    for route in ("/", "/presentation", "/admin", "/workbench", "/artifacts/invoice-comparison", "/artifacts/statement", "/project-design", "/operator", "/portal", "/operator/bill-cycles"):
        page = app("GET", route)
        report.check(f"page {route} renders IoT Connect branding", page.status == 200 and "IoT Connect" in page.text and "Project IoT Connect" not in page.text, f"status {page.status}")
    slide = app("GET", "/presentation/assets/slide-1.png")
    report.check("presentation slide asset served from the tree", slide.status == 200 and slide.headers.get("content-type", "").startswith("image/png"))
    report.check("missing presentation slide returns 404", app("GET", "/presentation/assets/slide-6.png").status == 404)

    # Output-produced check: a bill run must write rows, not just return 200.
    subscriptions = app("GET", f"/api/v1/accounts/{aster['account_id']}/subscriptions?system=iot").json()
    if not any(row["source_subscription_ref"].startswith("ASTER-DEVICE-") for row in subscriptions):
        uploaded = upload_fixture(aster["account_id"], "aster_5_subscriptions.csv")
        report.check("Aster fixture subscriptions uploaded", uploaded.status == 201 and uploaded.json().get("iot_created") == 5, uploaded.text[:120])
    cycle = free_cycle(aster["account_id"])
    run_response = admin("POST", f"/api/v1/admin/accounts/{aster['account_id']}/bill-runs", json_body={"bill_cycle": cycle})
    run = run_response.json() if run_response.status == 201 else {}
    report.check(f"bill run for Aster {cycle} created", run_response.status == 201, run_response.text[:160])
    if run:
        file_rows = app("GET", f"/api/v1/bill-runs/{run['bill_run_id']}/file").json()
        charges = app("GET", f"/api/v1/bill-runs/{run['bill_run_id']}/charges").json()
        report.check("bill run wrote billing rows (output produced)", len(file_rows) > 0 and len(file_rows) == run["output_row_count"], f"{len(file_rows)} rows vs output_row_count {run['output_row_count']}")
        report.check("bill run wrote source charges", len(charges) == run["source_charge_count"] and len(charges) > 0)
        report.check("bill run reconciliation PASSED with zero variance", run["status"] == "PASSED" and run["variance"] == "0.00", f"{run['status']} variance {run['variance']}")
        recon = app("GET", f"/api/v1/bill-runs/{run['bill_run_id']}/reconciliation").json()
        report.check("reconciliation acceptance checks all true", all(recon.get("acceptance_checks", {}).values()) and recon.get("acceptance_checks"))
        csv_download = app("GET", f"/api/v1/bill-runs/{run['bill_run_id']}/file.csv")
        report.check("billing file CSV download named after the product", csv_download.status == 200 and 'filename="iot-connect-' in csv_download.headers.get("content-disposition", ""))
        statement = app("GET", f"/api/v1/artifacts/accounts/{aster['account_id']}/legacy-statement/{cycle}").json()
        report.check("legacy statement artifact rendered line items", len(statement.get("line_items", [])) == run["output_row_count"] and statement.get("amount_due") == run["output_total"])
        comparison = app("GET", "/artifacts/invoice-comparison")
        report.check("invoice comparison page rendered with its data script", comparison.status == 200 and "invoice-comparison.js" in comparison.text)


def cmd_smoke(_: argparse.Namespace) -> int:
    report = Report(f"IoT Connect smoke  app={app_url()}  integrations={integrations_url()}")
    run_smoke(report)
    report.render()
    return 1 if report.failed else 0


def run_summarized_billing(report: Report) -> None:
    boreal = account_by_number(BOREAL)
    enabled = admin("POST", f"/api/v1/admin/accounts/{boreal['account_id']}/billing-mode", json_body={"billing_mode": "SUMMARIZED", "reason": "Standalone verification: summarized posting"})
    report.check("Boreal switched to SUMMARIZED (send_subscriptions_to_amdocs=false)", enabled.status == 200 and enabled.json()["billing_mode"] == "SUMMARIZED" and enabled.json()["send_subscriptions_to_amdocs"] is False, enabled.text[:120])
    reverted = admin("POST", f"/api/v1/admin/accounts/{boreal['account_id']}/billing-mode", json_body={"billing_mode": "DETAILED", "reason": "Standalone verification: reversal must be rejected"})
    report.check("SUMMARIZED → DETAILED reversal rejected with 409 CONFLICT", reverted.status == 409 and reverted.json().get("code") == "CONFLICT", reverted.text[:120])
    upload = upload_fixture(boreal["account_id"], "boreal_50_subscriptions.csv")
    body = upload.json() if upload.status == 201 else {}
    report.check("Boreal 50 subscriptions: iot_created=50, legacy_created=0, skipped_by_policy=50", body.get("iot_created") == 50 and body.get("legacy_created") == 0 and body.get("legacy_skipped_by_policy") == 50, upload.text[:160])
    run_response = admin("POST", f"/api/v1/admin/accounts/{boreal['account_id']}/bill-runs", json_body={"bill_cycle": "2026-08"})
    run = run_response.json() if run_response.status == 201 else {}
    report.check("Boreal summarized bill run: 53 source charges → 6 rows, 310.00, variance 0.00, PASSED", run.get("source_charge_count") == 53 and run.get("output_row_count") == 6 and run.get("source_total") == "310.00" and run.get("output_total") == "310.00" and run.get("variance") == "0.00" and run.get("status") == "PASSED", run_response.text[:200])
    if run:
        rows = app("GET", f"/api/v1/bill-runs/{run['bill_run_id']}/file").json()
        report.check("summarized rows post at ACCOUNT scope with null MDN", rows and all(row["posting_scope"] == "ACCOUNT" and row["mdn"] is None for row in rows))
        report.check("summarized rows preserve source record counts (sum = 53)", sum(int(row["source_record_count"]) for row in rows) == 53, str([row["source_record_count"] for row in rows]))
        recon = app("GET", f"/api/v1/bill-runs/{run['bill_run_id']}/reconciliation").json()
        report.check("summarized reconciliation acceptance checks all true", all(recon.get("acceptance_checks", {}).values()))
        legacy_lines = app("GET", f"/api/v1/accounts/{BOREAL}/subscriptions?system=legacy").json()
        report.check("no legacy lines created for policy-skipped subscriptions", len(legacy_lines) == 0, f"{len(legacy_lines)} legacy lines")
    policy = app("GET", f"/api/v1/demo-evidence/accounts/{BOREAL}/billing-policy").json()
    report.check("billing-policy evidence shows ACCOUNT posting scope", policy.get("summarized_billing_enabled") is True and policy.get("posting_scope") == "ACCOUNT")


def run_detailed_billing_check(report: Report) -> None:
    aster = account_by_number(ASTER)
    runs = app("GET", f"/api/v1/bill-runs?account_id={aster['account_id']}").json()
    run = next((row for row in runs if row["bill_cycle"] == "2026-08"), None)
    report.check("Aster detailed bill run 2026-08: 6 source charges → 6 rows, 65.00, PASSED", bool(run) and run["source_charge_count"] == 6 and run["output_row_count"] == 6 and run["source_total"] == "65.00" and run["status"] == "PASSED", str(run)[:160])
    if run:
        rows = app("GET", f"/api/v1/bill-runs/{run['bill_run_id']}/file").json()
        report.check("detailed rows: 5 SUBSCRIPTION-scope rows with MDN + 1 ACCOUNT-scope row", sum(1 for r in rows if r["posting_scope"] == "SUBSCRIPTION" and r["mdn"]) == 5 and sum(1 for r in rows if r["posting_scope"] == "ACCOUNT") == 1)


def run_iot_success(report: Report) -> None:
    aster = account_by_number(ASTER)
    sims = assign_sims(aster["account_id"], 2)
    created = create_batch(aster["account_id"], sims, "VERIFY-ASTER")
    batch = created.json() if created.status == 201 else {}
    report.check("Aster activation batch created as DRAFT with reserved MDNs", batch.get("status") == "DRAFT" and all(row["mdn"]["status"] == "RESERVED" for row in batch.get("items", [])), created.text[:160])
    submitted = submit_batch(batch["batch_id"]) if batch else None
    result = submitted.json() if submitted and submitted.status == 200 else {}
    report.check("Aster batch COMPLETED with 2 successes", result.get("status") == "COMPLETED" and result.get("success_count") == 2, submitted.text[:160] if submitted else "no batch")
    items = result.get("items", [])
    report.check("items ACTIVE on the network, legacy SUBMITTED (policy ON)", items and all(row["network_status"] == "ACTIVE" and row["legacy_status"] == "SUBMITTED" and row["overall_status"] == "ACTIVE" for row in items))
    report.check("HSS/POLICY/SMSC SUCCESS and AAA SKIPPED (public APN)", items and all([r["provisioning_status"] for r in row["flowone_element_results"]] == ["SUCCESS", "SUCCESS", "SUCCESS", "SKIPPED_NOT_APPLICABLE"] for row in items))
    report.check("SIM ACTIVE and MDN ASSIGNED after success", items and all(row["sim"]["resource_status"] == "ACTIVE" and row["mdn"]["status"] == "ASSIGNED" for row in items))
    subs = app("GET", f"/api/v1/accounts/{ASTER}/subscriptions?system=iot").json()
    verify_subs = [row for row in subs if row["source_subscription_ref"].startswith("VERIFY-ASTER-")]
    report.check("subscriptions ACTIVE with activation timestamp", len(verify_subs) == 2 and all(row["status"] == "ACTIVE" and row["activated_at"] for row in verify_subs))
    legacy = app("GET", f"/api/v1/accounts/{ASTER}/subscriptions?system=legacy").json()
    report.check("legacy lines created for the activated subscriptions", any(line["legacy_line_ref"] == f"LL-{row['subscription_number']}" for row in verify_subs for line in legacy))

    boreal = account_by_number(BOREAL)
    sims = assign_sims(boreal["account_id"], 1)
    created = create_batch(boreal["account_id"], sims, "VERIFY-BOREAL-APN", private_apn="BOREAL_IOT_PRIVATE")
    batch = created.json() if created.status == 201 else {}
    submitted = submit_batch(batch["batch_id"]) if batch else None
    result = submitted.json() if submitted and submitted.status == 200 else {}
    item = result.get("items", [{}])[0]
    report.check("Boreal private-APN activation COMPLETED with AAA SUCCESS", result.get("status") == "COMPLETED" and item.get("flowone_element_results", [{}])[-1].get("provisioning_status") == "SUCCESS", (submitted.text[:160] if submitted else created.text[:160]))
    report.check("Boreal (summarized) item legacy SKIPPED_BY_ACCOUNT_POLICY", item.get("legacy_status") == "SKIPPED_BY_ACCOUNT_POLICY", str(item.get("legacy_status")))


def run_iot_failure(report: Report) -> None:
    boreal = account_by_number(BOREAL)
    armed = integrations("POST", "/mock/flowone/v1/demo/fail-next", json_body={"element": "POLICY"})
    report.check("integration stub armed to fail next activation at POLICY", armed.status == 200 and armed.json().get("status") == "ARMED", armed.text[:120])
    sims = assign_sims(boreal["account_id"], 1)
    created = create_batch(boreal["account_id"], sims, "VERIFY-BOREAL-FAIL")
    batch = created.json() if created.status == 201 else {}
    reserved_mdn = batch.get("items", [{}])[0].get("mdn", {}).get("mdn")
    submitted = submit_batch(batch["batch_id"]) if batch else None
    result = submitted.json() if submitted and submitted.status == 200 else {}
    item = result.get("items", [{}])[0]
    report.check("failed batch COMPLETED_WITH_ERRORS (1 failure)", result.get("status") == "COMPLETED_WITH_ERRORS" and result.get("failure_count") == 1, submitted.text[:160] if submitted else created.text[:160])
    report.check("item FAILED_ROLLED_BACK, legacy NOT_ELIGIBLE_NETWORK_FAILURE", item.get("network_status") == "FAILED_ROLLED_BACK" and item.get("legacy_status") == "NOT_ELIGIBLE_NETWORK_FAILURE" and item.get("overall_status") == "FAILED")
    elements = item.get("flowone_element_results", [])
    statuses = [(e["element"], e["provisioning_status"], e["rollback_status"]) for e in elements]
    report.check("HSS rolled back, POLICY FAILURE, SMSC NOT_ATTEMPTED", statuses[:3] == [("HSS", "SUCCESS", "SUCCESS"), ("POLICY", "FAILURE", "NOT_APPLICABLE"), ("SMSC", "NOT_ATTEMPTED", "NOT_APPLICABLE")], str(statuses))
    report.check("MDN released back to AVAILABLE", item.get("mdn", {}).get("status") == "AVAILABLE" and item.get("mdn", {}).get("assigned_account_id") is None, str(item.get("mdn")))
    report.check("SIM retained by the account as ASSIGNED", item.get("sim", {}).get("resource_status") == "ASSIGNED" and item.get("sim", {}).get("current_owner_ref") == boreal["account_id"], str(item.get("sim")))
    subs = app("GET", f"/api/v1/accounts/{BOREAL}/subscriptions?system=iot").json()
    failed = next((row for row in subs if row["source_subscription_ref"] == "VERIFY-BOREAL-FAIL-001"), None)
    report.check("subscription left in ACTIVATION_FAILED for reconciliation", failed is not None and failed["status"] == "ACTIVATION_FAILED", str(failed)[:120])
    evidence = app("GET", f"/api/v1/demo-evidence/accounts/{BOREAL}/latest-activation").json()
    report.check("latest-activation evidence shows the failed batch", evidence.get("batch_id") == batch.get("batch_id") and evidence.get("failure_count") == 1)

    retry = admin("POST", f"/api/v1/admin/activation-batches/{batch['batch_id']}/items/{item['batch_item_id']}:retry") if item else None
    retried = retry.json() if retry and retry.status == 200 else {}
    retry_item = retried.get("items", [{}])[0]
    report.check("retry of the failed item succeeds in a new one-item batch", retried.get("status") == "COMPLETED" and retry_item.get("network_status") == "ACTIVE", retry.text[:160] if retry else "no item")
    report.check("retry reused the same MDN after release", retry_item.get("mdn", {}).get("mdn") == reserved_mdn and retry_item.get("mdn", {}).get("status") == "ASSIGNED", f"{retry_item.get('mdn', {}).get('mdn')} vs {reserved_mdn}")


def run_postman_equivalent(report: Report) -> None:
    """The four saved calls of the IoT Connect interview-evidence collection."""
    first = app("GET", f"/api/v1/accounts/{ASTER}/subscriptions?system=iot")
    report.check("Postman 1: GET Aster activated subscriptions returns ACTIVE rows", first.status == 200 and any(row["status"] == "ACTIVE" for row in first.json()))
    second = app("GET", f"/api/v1/accounts/{BOREAL}/summary")
    report.check("Postman 2: GET Boreal summary shows SUMMARIZED billing mode", second.status == 200 and second.json()["account"]["billing_mode"] == "SUMMARIZED")
    third = admin("GET", f"/api/v1/admin/activation-batches?account_id={BOREAL}")
    batches = third.json() if third.status == 200 else []
    report.check("Postman 3: GET latest Boreal activation batches", third.status == 200 and len(batches) >= 1)
    if batches:
        fourth = app("GET", f"/api/v1/activation-batches/{batches[0]['batch_id']}")
        report.check("Postman 4: GET batch FlowOne and Legacy Billing outcomes", fourth.status == 200 and all("flowone_element_results" in row for row in fourth.json()["items"]))
    walkthrough = app("GET", f"/api/v1/demo-evidence/accounts/{ASTER}/latest-activation")
    report.check("demo-evidence latest-activation (Aster) returns element evidence", walkthrough.status == 200 and all(len(row["flowone_element_results"]) == 4 for row in walkthrough.json()["items"]))


DIRECT_ACTIVATION = {
    "imsi": "310150123456789",
    "mdn": "+13125550101",
    "service_package": "DATA_SMS",
    "roaming_package": "DOMESTIC",
}
DIRECT_LEGACY_ACTION = {
    "amdocs_account_number": "AMD-45001",
    "wdh_account_reference": "WDH-200",
    "mdn": "+13125550121",
    "imsi": "310150123456789",
    "action": "CREATE",
}


def capture_direct_actions(report: Report) -> dict:
    """POST both direct integration APIs, GET them back, and collect batch-created IDs."""
    captured: dict[str, dict] = {}
    created = app("POST", "/api/v1/network-activations", json_body=DIRECT_ACTIVATION)
    body = created.json() if created.status == 201 else {}
    report.check("direct POST /api/v1/network-activations → 201 ACTIVE", created.status == 201 and body.get("wdh_service_status") == "ACTIVE", created.text[:120])
    if body:
        fetched = app("GET", f"/api/v1/network-activations/{body['activation_id']}")
        report.check("direct GET /api/v1/network-activations/{id} → 200, same body", fetched.status == 200 and fetched.json() == body)
        captured["network_activation"] = {"path": f"/api/v1/network-activations/{body['activation_id']}", "body": body}
    created = app("POST", "/api/v1/legacy-subscription-actions", json_body=DIRECT_LEGACY_ACTION)
    body = created.json() if created.status == 201 else {}
    report.check("direct POST /api/v1/legacy-subscription-actions → 201 SUBMITTED", created.status == 201 and body.get("wdh_status") == "SUBMITTED", created.text[:120])
    if body:
        fetched = app("GET", f"/api/v1/legacy-subscription-actions/{body['compatibility_action_id']}")
        report.check("direct GET /api/v1/legacy-subscription-actions/{id} → 200, same body", fetched.status == 200 and fetched.json() == body)
        captured["legacy_subscription_action"] = {"path": f"/api/v1/legacy-subscription-actions/{body['compatibility_action_id']}", "body": body}
    latest = app("GET", f"/api/v1/demo-evidence/accounts/{ASTER}/latest-activation")
    items = latest.json().get("items", []) if latest.status == 200 else []
    batch_ok = bool(items)
    for index, item in enumerate(items, start=1):
        for key, path in (("flowone_activation_id", "/api/v1/network-activations/"), ("legacy_action_id", "/api/v1/legacy-subscription-actions/")):
            identifier = item.get(key)
            if not identifier:
                batch_ok = False
                continue
            fetched = app("GET", path + identifier)
            if fetched.status != 200:
                batch_ok = False
                continue
            captured[f"batch_item_{index}_{key}"] = {"path": path + identifier, "body": fetched.json()}
    report.check(f"batch-created action IDs resolve via GET ({sum(1 for k in captured if k.startswith('batch_item_'))} resources from the Aster batch)", batch_ok and any(k.startswith("batch_item_") for k in captured))
    return captured


def cmd_verify_before(_: argparse.Namespace) -> int:
    report = Report(f"IoT Connect verification (before restart)  app={app_url()}  integrations={integrations_url()}")
    health = app("GET", "/api/v1/health").json()
    report.expect("application reports the postgres backend", health.get("data_backend"), "postgres")
    first = do_reset(quiet=True)
    report.check("reset #1 restores 2 accounts, 1000 available SIMs, no batches, no bill runs", len(first["projection"]["accounts"]) == 2 and first["projection"]["available_sim_count"] == 1000 and first["projection"]["activation_batches"] == [] and first["projection"]["bill_runs"] == [])
    second = do_reset(quiet=True)
    report.check(f"reset #2 fingerprint identical to reset #1 ({first['sha256'][:12]}…)", first["sha256"] == second["sha256"], f"{first['sha256']} vs {second['sha256']}")
    run_smoke(report)
    run_detailed_billing_check(report)
    run_summarized_billing(report)
    run_iot_success(report)
    run_iot_failure(report)
    run_postman_equivalent(report)
    direct_actions = capture_direct_actions(report)
    evidence = evidence_fingerprint()
    report.check(f"evidence fingerprint captured ({evidence['sha256'][:12]}…)", True)
    report.render()
    print(json.dumps({"seed_sha256": first["sha256"], "evidence": evidence, "direct_actions": direct_actions}, indent=1))
    return 1 if report.failed else 0


def cmd_verify_after(args: argparse.Namespace) -> int:
    saved = json.load(sys.stdin)
    report = Report(f"IoT Connect verification ({args.label})  app={app_url()}")
    health = app("GET", "/api/v1/health")
    report.check("application healthy on the postgres backend", health.status == 200 and health.json().get("data_backend") == "postgres")
    current = evidence_fingerprint()
    same = current["sha256"] == saved["evidence"]["sha256"]
    detail = ""
    if not same:
        before = saved["evidence"]["projection"]
        after = current["projection"]
        differing = [key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)]
        detail = f"differing sections: {differing}"
    report.check(f"persisted evidence unchanged ({saved['evidence']['sha256'][:12]}…)", same, detail)
    projection = current["projection"]
    report.check("bill runs still present", len(projection["bill_runs"]) >= 2, str(len(projection["bill_runs"])))
    report.check("activation batches still present", len(projection["activation_batches"]) >= 4, str(len(projection["activation_batches"])))
    report.check("Boreal still SUMMARIZED", projection["boreal_policy"].get("billing_mode") == "SUMMARIZED")
    direct_actions = saved.get("direct_actions", {})
    report.check("direct-action resources were captured before the restart", len(direct_actions) >= 2, f"{len(direct_actions)} captured")
    for name, saved_resource in direct_actions.items():
        fetched = app("GET", saved_resource["path"])
        same = fetched.status == 200 and fetched.json() == saved_resource["body"]
        report.check(f"GET {saved_resource['path'].rsplit('/', 1)[0]}/… ({name}) → 200, body unchanged", same, f"status {fetched.status}: {fetched.text[:100]}")
    report.render()
    return 1 if report.failed else 0


def cmd_evidence(_: argparse.Namespace) -> int:
    print(json.dumps(evidence_fingerprint(), indent=1))
    return 0


def cmd_bill_runs_table(_: argparse.Namespace) -> int:
    runs = sorted(app("GET", "/api/v1/bill-runs").json(), key=lambda row: row["bill_run_number"])
    for run in runs:
        print("|".join(str(run[key]) for key in ("bill_run_number", "account_number", "billing_mode", "bill_cycle", "status", "source_charge_count", "output_row_count", "source_total", "output_total", "variance")))
    return 0


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reset").set_defaults(func=cmd_reset)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("smoke").set_defaults(func=cmd_smoke)
    sub.add_parser("verify-before").set_defaults(func=cmd_verify_before)
    after = sub.add_parser("verify-after")
    after.add_argument("--label", default="after restart")
    after.set_defaults(func=cmd_verify_after)
    sub.add_parser("evidence").set_defaults(func=cmd_evidence)
    sub.add_parser("bill-runs-table").set_defaults(func=cmd_bill_runs_table)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
