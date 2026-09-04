"""Packaging and reliability proofs for the standalone beta.

These tests exist only to prove the containerized packaging contract:
deterministic seed data, no dated or laptop-specific paths, stable relative
links, and the presence of the delivered Compose/Make surface.
"""
from pathlib import Path

import pytest

from app.domain.demo_seed import DEFAULT_SEED_TIMESTAMP, build_demo_seed
from app.main import create_repository
from conftest import BASE_DIR


def test_seed_is_deterministic_by_default():
    first = build_demo_seed()
    second = build_demo_seed()

    assert first == second
    assert first["accounts"][0]["updated_at"] == DEFAULT_SEED_TIMESTAMP


def test_seed_timestamp_can_be_pinned_through_the_environment(monkeypatch):
    monkeypatch.setenv("IOTCONNECT_SEED_TIMESTAMP", "2026-09-01T00:00:00+00:00")

    seed = build_demo_seed()

    assert seed["customers"][0]["created_at"] == "2026-09-01T00:00:00+00:00"
    assert build_demo_seed(now="2026-01-01T00:00:00+00:00")["sims"][0]["updated_at"] == (
        "2026-01-01T00:00:00+00:00"
    )


@pytest.mark.parametrize("store", ["snowflake", "SNOWFLAKE", "sqlite"])
def test_selecting_an_unsupported_store_fails_fast_at_startup(monkeypatch, store):
    monkeypatch.setenv("IOTCONNECT_STORE", store)

    with pytest.raises(RuntimeError) as failure:
        create_repository()

    message = str(failure.value)
    assert f"IOTCONNECT_STORE={store.lower()} is not supported in IoT Connect v0.9" in message
    assert "use postgres (operating) or memory (tests)" in message
    assert "downstream reporting destination" in message


def test_delivered_packaging_surface_is_present_and_uses_stable_ports():
    compose = (BASE_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    makefile = (BASE_DIR / "Makefile").read_text(encoding="utf-8")

    assert (BASE_DIR / "Dockerfile").exists()
    assert (BASE_DIR / ".env.example").exists()
    assert not (BASE_DIR / ".env").exists() or ".env" in (BASE_DIR / ".gitignore").read_text()
    assert "${IOTCONNECT_APP_PORT:-8095}:8095" in compose
    assert "${IOTCONNECT_INTEGRATIONS_PORT:-8096}:8096" in compose
    assert compose.count("healthcheck:") == 3
    for target in ("up:", "down:", "reset:", "status:", "smoke:", "verify:"):
        assert f"\n{target}" in makefile, target


def test_runtime_has_no_dated_or_laptop_paths():
    main_source = (BASE_DIR / "app" / "main.py").read_text(encoding="utf-8")
    assert "claude-draft" not in main_source
    assert "output/pdf" not in main_source and "output/pptx" not in main_source
    for relative in ("README.md", "HANDS_ON_RUNBOOK.md", "Makefile", "docker-compose.yml"):
        text = (BASE_DIR / relative).read_text(encoding="utf-8")
        assert "/Users/" not in text, relative
        assert "/home/" not in text, relative


def test_user_visible_pages_link_swagger_relatively_and_carry_no_personal_links(client):
    for route in ("/", "/admin", "/workbench", "/operator/api-activity"):
        page = client.get(route)
        assert page.status_code == 200, route
        assert "http://127.0.0.1:8095" not in page.text, route
        assert "github.com" not in page.text, route
    assert 'href="/docs"' in client.get("/").text


def test_billing_file_download_uses_product_name(client):
    from conftest import ADMIN_HEADERS, fixture_text

    aster = next(
        row for row in client.get("/api/v1/accounts").json()
        if row["account_number"] == "ACCT-000100"
    )
    upload = client.post(
        f"/api/v1/admin/accounts/{aster['account_id']}/subscriptions:upload",
        content=fixture_text("aster_5_subscriptions.csv"),
        headers={**ADMIN_HEADERS, "Content-Type": "text/csv"},
    )
    assert upload.status_code == 201
    run = client.post(
        f"/api/v1/admin/accounts/{aster['account_id']}/bill-runs",
        json={"bill_cycle": "2026-08"},
        headers=ADMIN_HEADERS,
    ).json()
    download = client.get(f"/api/v1/bill-runs/{run['bill_run_id']}/file.csv")
    assert 'filename="iot-connect-ACCT-000100-2026-08-' in download.headers["content-disposition"]
    for retired in ("nightjar", "connecthq", "connect-hq-", "wham"):
        assert retired not in download.headers["content-disposition"].lower()


def _run_scan():
    import subprocess

    return subprocess.run(
        ["sh", "scripts/scan_candidate.sh"],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("retired_identifier", ["Connect HQ", "connecthq", "CONNECTHQ_ROOT_PATH", "nightjar", "WhAM"])
def test_scanner_rejects_retired_identifiers_on_a_user_visible_page(retired_identifier):
    """Every retired product or technical identifier must fail `make scan`.

    Only this category is asserted, so the test is valid on hosts where another
    scan category is noisy (inside the application image the scanning account is
    named after a technical identifier, which the plain-name category reports).
    The scanner excludes this test file by name, so the literal below is inert
    here but must be reported when it appears on a delivered surface.
    """
    if not (BASE_DIR / "scripts" / "scan_candidate.sh").exists():
        pytest.skip("scanner not present in this tree")

    clean = _run_scan()
    assert "PASS  retired identifiers" in clean.stdout, clean.stdout + clean.stderr

    fixture = BASE_DIR / "app" / "static" / "_scan_negative_fixture.html"
    fixture.write_text(f"<!doctype html><title>{retired_identifier}</title>\n", encoding="utf-8")
    try:
        dirty = _run_scan()
    finally:
        fixture.unlink(missing_ok=True)

    assert dirty.returncode == 1, dirty.stdout + dirty.stderr
    assert "FAIL  retired identifiers" in dirty.stdout, dirty.stdout
    assert "_scan_negative_fixture.html" in dirty.stdout, dirty.stdout
    assert not fixture.exists()

    restored = _run_scan()
    assert "PASS  retired identifiers" in restored.stdout, restored.stdout


SLIDE_DIR = BASE_DIR / "app" / "static" / "iotconnect" / "presentation"


def test_presentation_slides_match_their_visual_review_checksums():
    """Binary slides cannot be text-scanned for retired names; the manifest recorded
    at visual review (docs/PRESENTATION_VISUAL_REVIEW_2026-09-04.md) pins them."""
    import hashlib

    manifest = (SLIDE_DIR / "CHECKSUMS.sha256").read_text(encoding="utf-8").split("\n")
    entries = dict(reversed(line.split()) for line in manifest if line.strip())
    assert sorted(entries) == [f"slide-{n}.png" for n in range(1, 6)]
    for name, digest in entries.items():
        actual = hashlib.sha256((SLIDE_DIR / name).read_bytes()).hexdigest()
        assert actual == digest, f"{name} differs from its reviewed render"
