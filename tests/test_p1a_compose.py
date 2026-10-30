"""Host runtime P1-a's Compose: production's own file is the base's, so with P1-a not switched on every production
service's configuration is today's (no new environment value, mount, limit or service: review B2); everything P1-a
changes lives in docker-compose.runtime.prod.yml, the switch-on layer an operator copies to the host on Robert's word,
which the deploy scripts layer only when it is there. One authority file and one principal for the portal, the
supervisor and the witness (review SF5)."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / "docker-compose.prod.yml"
LAYER = ROOT / "docker-compose.runtime.prod.yml"
FILE = "/app/core/mc_delegation_dispatcher/authority.prod.json"


def _env(service) -> dict:
    out = {}
    for item in service.get("environment") or []:
        key, _, value = item.partition("=")
        out[key] = value
    return out


GOLDEN = ROOT / "tests" / "p1a_prod_compose_golden.json"
P1A_NAMES = ("MINIMOI_MC_DELEGATION_AUTHORITY_FILE", "MINIMOI_MC_DELEGATION_DOOR_CREDENTIALS", "MINIMOI_RUNTIME_",
             "MINIMOI_WITNESS_", "runtime-witness", "/run/minimoi/runtime", "docker-compose.runtime.prod.yml",
             "core.mc_delegation_runner.witness")


def _rendered(text) -> dict:
    """Every service's rendered values, as JSON holds them (the golden's form)."""
    return json.loads(json.dumps(yaml.safe_load(text)["services"], sort_keys=True, default=str))


def test_unconfigured_production_keeps_todays_values_for_every_service_without_git():
    """Review T1 (M7'): CI checks out one commit, so this guard reads a golden copy committed with it, not the base
    commit. Every production service's rendered values equal the golden's; a P1-a value added to
    docker-compose.prod.yml fails here. A change that is not P1-a's regenerates the golden on purpose:
    python tests/test_p1a_compose.py --regenerate."""
    golden = json.loads(GOLDEN.read_text())["services"]
    assert _rendered(PROD.read_text()) == golden, "docker-compose.prod.yml differs from today's (tests/p1a_prod_compose_golden.json)"


def test_no_p1a_name_reaches_productions_own_compose_file():
    text = PROD.read_text()
    assert [n for n in P1A_NAMES if n in text] == []
    sup = yaml.safe_load(text)["services"]["supervisor"]
    assert (sup["mem_limit"], sup["cpus"]) == ("192m", 0.25)                     # today's limits


def test_the_layer_touches_only_the_portal_the_supervisor_and_adds_the_profiled_witness():
    layer = yaml.safe_load(LAYER.read_text())
    assert set(layer) == {"services"}
    assert set(layer["services"]) == {"portal", "supervisor", "runtime-witness"}
    assert layer["services"]["runtime-witness"]["profiles"] == ["runtime-witness"]   # started only by its profile
    prod = yaml.safe_load(PROD.read_text())["services"]
    assert "runtime-witness" not in prod
    assert prod["supervisor"]["mem_limit"] == "192m" and prod["supervisor"]["cpus"] == 0.25    # today's limits
    assert (layer["services"]["supervisor"]["mem_limit"], layer["services"]["supervisor"]["cpus"]) == ("256m", 0.5)


def test_one_authority_file_and_one_principal_for_every_production_process():
    layer = yaml.safe_load(LAYER.read_text())["services"]
    prod = yaml.safe_load(PROD.read_text())["services"]
    portal, sup, witness = _env(layer["portal"]), _env(layer["supervisor"]), _env(layer["runtime-witness"])
    assert portal["MINIMOI_MC_DELEGATION_AUTHORITY_FILE"] == sup["MINIMOI_RUNTIME_AUTHORITY_FILE"] \
        == witness["MINIMOI_WITNESS_AUTHORITY_FILE"] == FILE
    assert (ROOT / FILE.removeprefix("/app/")).is_file()
    assert sup["MINIMOI_RUNTIME_PRINCIPAL"] == _env(prod["portal"])["MINIMOI_MC_DELEGATION_PRINCIPAL"] == "robert"
    assert _env(prod["portal"])["MINIMOI_MC_DELEGATION_ENV"] == "prod"
    assert sup["MINIMOI_RUNTIME_STATE_FILE"] == portal["MINIMOI_RUNTIME_STATE_FILE"] \
        == sup["MINIMOI_RUNTIME_STATE_DIR"] + "/state.json"
    assert sup["MINIMOI_RUNTIME_TELEGRAM_ANSWERS"] == "${MINIMOI_RUNTIME_TELEGRAM_ANSWERS:-}"   # off unless .env says 1
    assert witness["MINIMOI_WITNESS_ENDPOINT_ID"] == sup["MINIMOI_RUNTIME_ENDPOINT_ID"]
    assert witness["MINIMOI_WITNESS_HOST_ID"] == sup["MINIMOI_RUNTIME_HOST_ID"]
    # The supervisor writes the snapshot where the portal reads it (read only).
    assert any(v.endswith(":/run/minimoi/runtime/authority") for v in layer["supervisor"]["volumes"])
    assert any(v.endswith(":/run/minimoi/runtime:ro") for v in layer["portal"]["volumes"])


def test_the_layer_holds_no_secret_only_references_and_digests_paths():
    text = LAYER.read_text()
    for value in re.findall(r"=(\S+)", text):
        assert not re.fullmatch(r"\d{5,15}:[A-Za-z0-9_-]{30,64}", value)       # no bot token
        assert not re.fullmatch(r"[A-Za-z0-9+/]{32,}={0,2}", value)            # no token-shaped value
    for ref in ("MINIMOI_WITNESS_READ_TOKEN_REF", "MINIMOI_WITNESS_TELEGRAM_TOKEN_REF",
                "MINIMOI_WITNESS_TELEGRAM_CHAT_REF"):
        assert f"{ref}=${{{ref}:-}}" in text                                   # references, from .env


def _layer_block(rel):
    source = (ROOT / rel).read_text()
    start = source.index("RUNTIME_LAYER=/opt/minimoi/docker-compose.runtime.prod.yml")
    end = source.index("\nfi\n", start) + 4
    return source[start:end]


@pytest.mark.parametrize("rel", ["scripts/operations/deploy_scoped_release.sh", "scripts/deploy.sh"])
def test_the_deploy_scripts_layer_the_file_only_when_it_is_on_the_host(tmp_path, rel):
    block = _layer_block(rel).replace("/opt/minimoi/docker-compose.runtime.prod.yml", str(tmp_path / "layer.yml"))
    prefix = 'set -euo pipefail\nCOMPOSE=(docker-compose --project-name minimoi -f /opt/minimoi/docker-compose.prod.yml)\n'
    trailer = '\nprintf "%s\\n" "${COMPOSE[@]}"'
    absent = subprocess.run(["bash", "-c", prefix + block + trailer], capture_output=True, text=True, timeout=5)
    assert absent.returncode == 0 and absent.stdout.splitlines()[-1] == "/opt/minimoi/docker-compose.prod.yml"
    (tmp_path / "layer.yml").write_text("services: {}\n")
    present = subprocess.run(["bash", "-c", prefix + block + trailer], capture_output=True, text=True, timeout=5)
    assert present.returncode == 0 and present.stdout.splitlines()[-2:] == ["-f", str(tmp_path / "layer.yml")]
    assert "eval" not in block and "source" not in block


def test_a_portal_release_recreates_an_active_witness_only_with_the_layer():
    source = (ROOT / "scripts/operations/deploy_scoped_release.sh").read_text()
    assert ('if [[ " ${SERVICES[*]} " == *" portal "* && -f "$RUNTIME_LAYER" ]] && docker container inspect '
            'minimoi-runtime-witness >/dev/null 2>&1; then') in source
    assert '"${COMPOSE[@]}" --profile runtime-witness up -d --no-deps runtime-witness' in source
    assert source.index("RUNTIME_LAYER=") < source.index("aws ecr get-login-password")
    assert 'wait_for_container_health minimoi-runtime-witness 24' in source


# ── the MC container script's supervision switch (review R2) ──
def _supervision_blocks():
    source = (ROOT / "scripts/operations/deploy_mc_container_ec2.sh").read_text()
    body = source[source.index("supervision() {"):source.index("\nrunner() {")]
    start = body.index('  local compose=(docker-compose --project-name minimoi -f "$ROOT/docker-compose.prod.yml")')
    layer = body[start:body.index("\n  fi\n", start) + 6]
    off = body.index("    off)\n")
    guard = body[off + len("    off)\n"):body.index("\n      fi\n", off) + 9]
    return layer, guard


def test_the_supervision_switch_adds_the_layer_only_when_it_is_on_the_host(tmp_path):
    layer, _ = _supervision_blocks()
    run = 'set -euo pipefail\nROOT="$1"\nf() {\n' + layer + '\n  printf "%s\\n" "${compose[@]}"\n}\nf'
    absent = subprocess.run(["bash", "-c", run, "x", str(tmp_path)], capture_output=True, text=True, timeout=5)
    assert absent.returncode == 0 and absent.stdout.splitlines()[-1] == f"{tmp_path}/docker-compose.prod.yml"
    (tmp_path / "docker-compose.runtime.prod.yml").write_text("services: {}\n")
    present = subprocess.run(["bash", "-c", run, "x", str(tmp_path)], capture_output=True, text=True, timeout=5)
    assert present.stdout.splitlines()[-2:] == ["-f", f"{tmp_path}/docker-compose.runtime.prod.yml"]


def test_supervision_off_is_refused_while_the_runtime_layer_is_on(tmp_path):
    _, guard = _supervision_blocks()
    run = ('set -euo pipefail\nROOT="$1"\ndie() { echo "REFUSED: $*" >&2; exit 2; }\n' + guard
           + '\necho "SUPERVISOR REMOVED"')
    free = subprocess.run(["bash", "-c", run, "x", str(tmp_path)], capture_output=True, text=True, timeout=5)
    assert free.returncode == 0 and "SUPERVISOR REMOVED" in free.stdout                 # no layer: today's off
    (tmp_path / "docker-compose.runtime.prod.yml").write_text("services: {}\n")
    held = subprocess.run(["bash", "-c", run, "x", str(tmp_path)], capture_output=True, text=True, timeout=5)
    assert held.returncode == 2 and "SUPERVISOR REMOVED" not in held.stdout and "roll it back first" in held.stderr


if __name__ == "__main__":
    if sys.argv[1:] == ["--regenerate"]:
        GOLDEN.write_text(json.dumps({"source": "docker-compose.prod.yml, every service's rendered values",
                                      "services": _rendered(PROD.read_text())}, indent=1, sort_keys=True) + "\n")
        print(f"wrote {GOLDEN.relative_to(ROOT)}")
