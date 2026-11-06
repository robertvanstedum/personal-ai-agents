"""MiniMoi 1.2's production operator, on fakes for Docker and the gateway. The real readers of each file are run against
what it writes. Nothing here touches a host."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import textwrap
from uuid import uuid4

import pytest

from scripts.operations import minimoi_1_2_prod_bind as bind
from scripts.operations import minimoi_1_2_prod_setup as setup


@pytest.fixture(autouse=True)
def _keep_this_process_umask():
    """main() tightens the umask for the operator's own run. Left set, it changes what other tests' mkdir makes."""
    saved = os.umask(0o022)
    os.umask(saved)
    yield
    os.umask(saved)

REPO = Path(__file__).resolve().parents[1]
S = setup.SECRETS

ALL_DB_NAMES = sorted({*bind.WORKER_TABLES, *bind.BOARD_TABLES, *bind.BOARD_SEQUENCES})

FAKE_DOCKER = r'''#!PYTHON
import json, os, sys
base = os.environ["FAKE_DOCKER_DIR"]
argv = sys.argv[1:]
with open(base + "/argv.log", "a") as f:
    f.write(json.dumps(argv) + "\n")
config = json.load(open(base + "/config.json"))
def stdin():
    data = sys.stdin.read()
    with open(base + "/stdin.log", "a") as f:
        f.write(data + "\n--\n")
    return data
if argv[0] == "inspect":
    print(config.get("health", "healthy") if ".State.Health" in argv[2] else "true")
elif argv[:2] == ["exec", "-i"] and argv[2] == "minimoi-model-gateway":
    stdin()
    n = open(base + "/stdin.log").read().count("\n--\n")
    print("sk-fakegatewaykey" + "x" * 16 + str(n))
    print(json.dumps(config["key_facts"][min(n - 1, len(config["key_facts"]) - 1)]))
elif argv[:2] == ["exec", "-i"] and argv[2] == "postgres-ai-agents":
    sql = stdin()
    if "to_regclass" in sql:
        print("\n".join(config["tables"]))
    elif "pg_roles" in sql:
        print("1" if config.get("role_exists") else "")
    elif config.get("psql_exit"):
        sys.exit(config["psql_exit"])
elif argv[:2] == ["exec", "-i"] and argv[2] == "minimoi-records":
    stdin()
    print("observetoken" + "y" * 30)
elif argv[0] == "exec" and argv[1] == "minimoi-records":
    out = config["records_state"] + "/" + argv[argv.index("--out") + 1].rsplit("/", 1)[1]
    os.makedirs(out)
    teammate, worker = argv[argv.index("--teammate") + 1], argv[argv.index("--worker-principal") + 1]
    for name in (teammate, worker):
        open(out + "/" + name + ".token", "w").write("records-" + name + "-" + "z" * 30 + "\n")
    print(json.dumps({"hosted": {"installation_id": "inst-" + teammate}}))
elif argv[0] == "exec" and argv[1] == "minimoi-cos-agent-a":
    print(json.dumps(config["auth"][argv[argv.index("--agent") + 1]]))
else:
    sys.exit(9)
'''


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A prepared root on a host where this process is every consumer, with a fake docker."""
    me = (os.getuid(), os.getgid())
    monkeypatch.setattr(setup, "consumer", lambda rel: me)
    monkeypatch.setattr(setup, "ROOT", me)
    monkeypatch.setattr(setup, "SHARED_FOLDERS", {"cos/store": (0o2750, *me)})
    monkeypatch.setattr(setup, "_give", lambda path, uid, gid, rel: None)
    monkeypatch.setenv("MINIMOI_1_2_SETUP_APPROVED", "1")
    root = tmp_path / "root"
    root.mkdir()
    setup.apply(root)
    (root / ".env").write_text("UNRELATED=keepme\nMINIMOI_ACTION_NOTICES_AGENT_TOKENS=codex:" + "a" * 64 + "\n")
    (root / ".env").chmod(0o600)
    fake = tmp_path / "docker-fake"
    fake.mkdir()
    (fake / "docker").write_text(FAKE_DOCKER.replace("PYTHON", sys.executable, 1))
    (fake / "docker").chmod(0o755)
    monkeypatch.setenv("FAKE_DOCKER_DIR", str(fake))
    monkeypatch.setattr(bind, "DOCKER", str(fake / "docker"))
    state = tmp_path / "records-state"
    state.mkdir()
    config = {"tables": ALL_DB_NAMES, "records_state": str(state),
              "auth": {a: {"auth": {"oauth": {"profiles": []}}} for a in bind.COS_AGENTS},
              "key_facts": [{"max_budget": 20.0, "budget_duration": "30d", "models": sorted(s["models"])}
                            for s in bind.GATEWAY_KEYS.values()]}
    (fake / "config.json").write_text(json.dumps(config))
    inputs = tmp_path / "inputs.json"
    doc = bind.template()
    doc["approved_by"] = "robert"
    inputs.write_text(json.dumps(doc))
    inputs.chmod(0o600)
    binding = tmp_path / "records-production.json"
    binding.write_text(json.dumps({"inputs": {"state": str(state)}}))

    class Host:
        pass
    h = Host()
    h.root, h.fake, h.inputs, h.binding, h.state = root, fake, str(inputs), str(binding), state
    h.configure = lambda **kw: (fake / "config.json").write_text(json.dumps({**json.loads((fake / "config.json").read_text()), **kw}))
    h.argv = lambda: (fake / "argv.log").read_text() if (fake / "argv.log").exists() else ""
    h.apply = lambda only: bind.apply(root, only, h.inputs, h.binding)
    return h


def _values(root):
    """Every file written under the secrets tree."""
    return {str(p.relative_to(root)): p.read_text() for p in (root / S).rglob("*") if p.is_file() and p.name != ".bind.lock"}


# ---- G8: local ---------------------------------------------------------------------------------------------------------

def test_apply_needs_the_operators_word(host, monkeypatch):
    monkeypatch.delenv("MINIMOI_1_2_SETUP_APPROVED")
    with pytest.raises(SystemExit, match="MINIMOI_1_2_SETUP_APPROVED"):
        host.apply(["local"])
    assert not (host.root / bind.COS_BOUND).exists()


def test_check_changes_nothing_and_never_prints_a_value(host, capsys):
    host.apply(["local"])
    before = sorted((str(p), p.stat().st_mtime_ns) for p in host.root.rglob("*"))
    capsys.readouterr()
    assert bind.main(["check", "--root", str(host.root), "--inputs", host.inputs]) == 0
    out = capsys.readouterr().out
    assert sorted((str(p), p.stat().st_mtime_ns) for p in host.root.rglob("*")) == before
    report = json.loads(out)
    assert report["ok"] and report["inputs"] == "valid" and all(report["agree"].values())
    for rel, value in _values(host.root).items():
        for piece in re.findall(r"[A-Za-z0-9_-]{24,}", value):
            assert piece not in out, rel


def test_local_writes_private_new_tokens_and_the_real_readers_accept_them(host):
    outcome = host.apply(["local"])
    assert bind.COS_BOUND in outcome["written"]
    tokens = [(host.root / rel).read_text() for rel in (bind.COS_BOUND, bind.TASKS_COS, bind.TASKS_PLATFORM, bind.PORTAL_MC, bind.MC_RELAY)]
    assert len(set(tokens)) == len(tokens) and all(re.fullmatch(r"[A-Za-z0-9_-]{32,256}", t) for t in tokens)
    for rel in (bind.COS_BOUND, bind.BINDINGS, bind.GRANTS, bind.RUNNER_SEATS, bind.BOARD_KEYS, bind.BOARD_PROFILE, bind.COS_SERVICE_COPY):
        assert stat.S_IMODE((host.root / rel).stat().st_mode) == 0o600

    from services.partnership_tasks.auth import Bindings, Grants
    from services.partnership_tasks.client import read_token
    from services.partnership_tasks.service import TaskService
    bindings = Bindings.load(host.root / bind.BINDINGS)
    now = "2026-11-06T12:00:00+00:00"
    for seat, rel in bind.SEAT_TOKENS.items():
        found = bindings.resolve(read_token(host.root / rel), now)
        assert found.seat == seat and found.environment == "prod" and not found.is_owner
    assert not any("mc.token" in rel and "partnership/" in rel and "portal" not in rel for rel in setup.BY_OPERATOR)
    Grants(json.loads((host.root / bind.GRANTS).read_text()))
    assert json.loads((host.root / bind.RUNNER_SEATS).read_text()) == {}
    store = host.root / setup.STATE / "partnership/store"
    assert (store / "journal/journal.jsonl").is_file()
    assert TaskService(store, "prod", bindings).journal.head()["seq"] == 0


def test_the_board_pair_loads_on_both_sides(host):
    host.apply(["local"])
    from domains.cos.board_tool import load_profile
    from minimoi_portal.guild_ui.board_headless_runner import InstalledKeys

    class Lease:
        def check(self):
            pass
    registry = InstalledKeys(str(host.root / bind.BOARD_KEYS), Lease())
    registry.refresh()
    profile = load_profile(host.root / bind.BOARD_PROFILE)
    held = registry.lookup(profile.key.key_id)
    assert held.secret == profile.key.secret and held.capabilities == profile.key.capabilities
    assert held.principal == "robert" and profile.key.environment == "prod"


def test_what_the_tool_holds_and_the_contracts_stay_equal():
    from domains.cos.board_tool import OPS
    assert set(bind.BOARD_CAPABILITIES) == {"guild.board." + spec[2] for spec in OPS.values()}
    command = json.loads((REPO / "contracts/partnership/v1/command.schema.json").read_text())
    assert set(command["$defs"]["kind"]["enum"]) == bind.COMMAND_KINDS


def test_pairs_stay_equal_and_hashes_go_where_the_bindings_read_them(host):
    host.apply(["local"])
    assert (host.root / bind.COS_SERVICE_COPY).read_text() == (host.root / bind.COS_SERVICE).read_text()
    env = (host.root / ".env").read_text()
    digest = hashlib.sha256((host.root / bind.COS_BOUND).read_bytes()).hexdigest()
    assert f"MINIMOI_COS_TOOL_LISTENER_TOKEN_SHA256={digest}\n" in env
    digest = hashlib.sha256((host.root / bind.MC_SERVICE).read_bytes()).hexdigest()
    assert f"MINIMOI_MC_ROOMS_TOKEN_SHA256={digest}\n" in env
    assert "MINIMOI_PARTNERSHIP_OWNER_SUBJECT=owner:robert\n" in env
    assert "UNRELATED=keepme\n" in env
    assert stat.S_IMODE((host.root / ".env").stat().st_mode) == 0o600
    # No secret went to .env: every main service loads that file.
    for path in (bind.COS_BOUND, bind.PORTAL_MC, bind.MC_SERVICE, bind.NOTICES):
        assert (host.root / path).read_text().strip() not in env


def test_the_owner_subject_is_the_form_the_portal_signs():
    assert 'subject=f"owner:{owner}"' in (REPO / "minimoi_portal/guild_ui/tasks_owner_api.py").read_text()


@pytest.mark.skipif(shutil.which("node") is None, reason="no node on this host")
def test_the_cos_bound_token_passes_the_plugins_own_reader(host):
    host.apply(["local"])
    script = textwrap.dedent(f"""
        import {{tokenFromFile}} from {json.dumps(str(REPO / 'docker/cos-agent-a/plugins/cos-bound/shared.mjs'))};
        console.log(tokenFromFile({json.dumps(str(host.root / bind.COS_BOUND))}).length);
    """)
    done = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-300:]
    assert done.stdout.strip() == "64"


def test_nothing_is_overwritten_on_a_second_run(host):
    host.apply(["local"])
    first = _values(host.root)
    env = (host.root / ".env").read_text()
    again = host.apply(["local"])
    assert _values(host.root) == first and (host.root / ".env").read_text() == env
    assert again["written"] == [] and bind.COS_BOUND in again["kept"]


def test_a_symlink_is_refused_and_nothing_is_written_through_it(host, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = host.root / bind.COS_BOUND
    target.symlink_to(outside / "stolen")
    with pytest.raises(SystemExit, match="unsafe|symlink"):
        host.apply(["local"])
    assert list(outside.iterdir()) == []
    assert target.is_symlink()


def test_a_different_hash_in_env_is_refused_never_overwritten(host):
    (host.root / ".env").write_text("MINIMOI_COS_TOOL_LISTENER_TOKEN_SHA256=" + "b" * 64 + "\n")
    with pytest.raises(SystemExit, match="different value"):
        host.apply(["local"])
    assert (host.root / ".env").read_text() == "MINIMOI_COS_TOOL_LISTENER_TOKEN_SHA256=" + "b" * 64 + "\n"


def test_the_runbooks_bare_owner_subject_is_refused(host):
    (host.root / ".env").write_text("MINIMOI_PARTNERSHIP_OWNER_SUBJECT=robert\n")
    with pytest.raises(SystemExit, match="MINIMOI_PARTNERSHIP_OWNER_SUBJECT"):
        host.apply(["local"])


def test_bindings_that_dont_match_their_tokens_are_refused(host):
    host.apply(["local"])
    rows = json.loads((host.root / bind.BINDINGS).read_text())
    rows[0]["token_sha256"] = "c" * 64
    os.unlink(host.root / bind.BINDINGS)
    bind.write_new(host.root, bind.BINDINGS, json.dumps(rows).encode())
    with pytest.raises(SystemExit, match="match the tokens"):
        host.apply(["local"])
    assert bind.check(host.root)["ok"] is False


def test_each_file_goes_to_its_consumers_uid_and_a_refused_owner_leaves_nothing(tmp_path, monkeypatch):
    assert setup.consumer(bind.COS_BOUND) == (1000, 1000) and setup.consumer(bind.BINDINGS) == (0, 0)
    (tmp_path / bind.COS_BOUND).parent.mkdir(parents=True)
    given = []
    monkeypatch.setattr(bind, "_fchown", lambda fd, uid, gid: given.append((uid, gid)))
    assert bind.write_new(tmp_path, bind.COS_BOUND, b"value") is True
    assert given == [(1000, 1000)]
    assert bind.write_new(tmp_path, bind.COS_BOUND, b"other") is False
    assert (tmp_path / bind.COS_BOUND).read_bytes() == b"value"

    def cannot(fd, uid, gid):
        raise PermissionError
    monkeypatch.setattr(bind, "_fchown", cannot)
    rel = bind.COS_BOUND + ".second"
    monkeypatch.setattr(setup, "consumer", lambda rel: (0, 0))
    with pytest.raises(SystemExit, match="could not give"):
        bind.write_new(tmp_path, rel, b"value")
    assert sorted(p.name for p in (tmp_path / bind.COS_BOUND).parent.iterdir()) == ["token"]


def test_it_refuses_before_setup_has_run(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMOI_1_2_SETUP_APPROVED", "1")
    with pytest.raises(SystemExit, match="setup"):
        bind.apply(tmp_path, ["gateway"], "", "")


def test_it_refuses_a_dev_root(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMOI_1_2_SETUP_APPROVED", "1")
    with pytest.raises(SystemExit, match="Dev"):
        bind.apply(tmp_path / "minimoi-staging", ["gateway"], "", "")


# ---- inputs: fail closed ------------------------------------------------------------------------------------------------

def _doc(**changes):
    doc = bind.template()
    doc["approved_by"] = "robert"
    doc.update(changes)
    return doc


def _seat(**changes):
    seats = _doc()["seats"]
    seats["mc"] = {**seats["mc"], **changes}
    return seats


@pytest.mark.parametrize("doc,field", [
    (bind.template(), "approved_by"),
    (_doc(environment="dev"), "environment"),
    (_doc(owner_principal="someone-else"), "owner_principal"),
    (_doc(runner_seats=["codex"]), "runner_seats"),
    (_doc(board={"numeric_user_id": -1}), "numeric_user_id"),
    (_doc(seats=_seat(origin="owner_session")), "owner authority"),
    (_doc(seats=_seat(operations=["task.teleport"])), "operations"),
    (_doc(seats=_seat(operations=[])), "operations"),
    (_doc(seats=_seat(scopes=["shared:x"], read_scopes=["personal"])), "cannot read"),
    (_doc(grants=[{"grant_id": "g", "seats": ["nobody"], "scopes": ["personal"], "expires_at": None}]), "grants"),
    ({k: v for k, v in _doc().items() if k != "grants"}, "exactly"),
    ({**_doc(), "extra": 1}, "exactly"),
])
def test_a_bad_input_file_fails_closed_naming_the_field(doc, field):
    with pytest.raises(SystemExit, match=field):
        bind.validate_inputs(doc)


def test_a_reviewed_template_is_valid():
    assert bind.validate_inputs(_doc())


@pytest.mark.parametrize("kind", ["world_readable", "symlink", "absent", "not_json", "repeated_key"])
def test_an_unsafe_input_file_is_refused(tmp_path, kind):
    path = tmp_path / "inputs.json"
    if kind == "symlink":
        real = tmp_path / "real.json"
        real.write_text(json.dumps(_doc()))
        real.chmod(0o600)
        path.symlink_to(real)
    elif kind != "absent":
        text = {"not_json": "{", "repeated_key": '{"schema": 1, "schema": 2}'}.get(kind, json.dumps(_doc()))
        path.write_text(text)
        path.chmod(0o644 if kind == "world_readable" else 0o600)
    with pytest.raises(SystemExit, match="refused"):
        bind.load_inputs(str(path))


def test_the_template_goes_to_stdout_with_no_secret(capsys):
    assert bind.main(["inputs-template"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["approved_by"] == "" and doc["runner_seats"] == []


# ---- G8: database ---------------------------------------------------------------------------------------------------------

def test_the_role_is_least_privilege_and_its_password_is_never_sent_or_printed(host, capsys):
    outcome = host.apply(["db"])
    assert bind.DB_URL in outcome["written"]
    url = (host.root / bind.DB_URL).read_text()
    match = re.fullmatch(r"postgresql://partnership_worker:([0-9a-f]{64})@postgres:5432/personal_agents\n", url)
    password = match.group(1)
    assert stat.S_IMODE((host.root / bind.DB_URL).stat().st_mode) == 0o600
    sent = (host.fake / "stdin.log").read_text()
    assert password not in sent and password not in host.argv() and password not in capsys.readouterr().out
    assert "SCRAM-SHA-256$4096:" in sent
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS" in sent
    grants = [line for line in sent.splitlines() if line.startswith("GRANT")]
    assert not any(word in line for line in grants for word in ("DELETE", " ALL ", "TRUNCATE"))
    assert "GRANT SELECT ON guild.mc_delegation_runners TO partnership_worker;" in grants


def test_the_verifier_answers_a_real_scram_exchange():
    """RFC 7677's test vector: a server holding this verifier accepts that client's proof and returns its signature."""
    salt = base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ==")
    verifier = bind.scram_verifier("pencil", salt=salt)
    head, stored_server = verifier.split("$", 2)[1:]
    assert head == "4096:W22ZaJ0SNY7soEsUEjb6gQ==" and verifier.startswith("SCRAM-SHA-256$")
    stored, server = (base64.b64decode(p) for p in stored_server.split(":"))
    nonce = "rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0"
    auth = f"n=user,r=rOprNGfwEbeRWgbNEkqO,r={nonce},s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096,c=biws,r={nonce}".encode()
    proof = base64.b64decode("dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=")
    signature = hmac.new(stored, auth, "sha256").digest()
    client_key = bytes(a ^ b for a, b in zip(proof, signature))
    assert hashlib.sha256(client_key).digest() == stored
    assert base64.b64encode(hmac.new(server, auth, "sha256").digest()).decode() == "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="


def test_the_grants_cover_every_table_the_workers_code_names():
    named = set()
    for rel in ("core/mc_delegation_dispatcher/store.py", "core/minimoi_actions/grants.py", "services/partnership_tasks/runner_door.py"):
        named |= set(re.findall(r"(?:FROM|INTO|UPDATE|JOIN)\s+(guild\.[a-z_]+)", (REPO / rel).read_text()))
    assert named <= set(bind.WORKER_TABLES), sorted(named - set(bind.WORKER_TABLES))


def test_missing_tables_stop_it_with_their_names_and_nothing_made(host):
    host.configure(tables=sorted(set(ALL_DB_NAMES) - {"guild.mc_task_fences", "guild.mc_task_operations"}))
    with pytest.raises(SystemExit, match="mc_task_fences.*mc_task_operations"):
        host.apply(["db"])
    assert not (host.root / bind.DB_URL).exists()
    assert "CREATE ROLE" not in (host.fake / "stdin.log").read_text()


def test_an_existing_role_without_its_file_is_refused(host):
    host.configure(role_exists=True)
    with pytest.raises(SystemExit, match="password is unknown"):
        host.apply(["db"])
    assert not (host.root / bind.DB_URL).exists()


def test_a_failed_role_writes_no_file(host):
    host.configure(psql_exit=3)
    with pytest.raises(SystemExit, match="psql exit 3"):
        host.apply(["db"])
    assert not (host.root / bind.DB_URL).exists()


def test_an_existing_db_url_is_kept_and_no_role_is_touched(host):
    for rel in (bind.DB_URL, bind.BOARD_DB_URL):
        bind.write_new(host.root, rel, b"postgresql://kept\n")
    assert {bind.DB_URL, bind.BOARD_DB_URL} <= set(host.apply(["db"])["kept"])
    assert (host.root / bind.DB_URL).read_text() == "postgresql://kept\n"
    assert not (host.fake / "stdin.log").exists()


# ---- the Board's own role ---------------------------------------------------------------------------------------------------

def test_the_board_role_is_least_privilege_and_its_password_is_never_sent_or_printed(host, capsys):
    outcome = host.apply(["db"])
    assert bind.BOARD_DB_URL in outcome["written"] and "database role cos_board" in outcome["written"]
    url = (host.root / bind.BOARD_DB_URL).read_text()
    password = re.fullmatch(r"postgresql://cos_board:([0-9a-f]{64})@postgres:5432/personal_agents\n", url).group(1)
    assert stat.S_IMODE((host.root / bind.BOARD_DB_URL).stat().st_mode) == 0o600
    sent = (host.fake / "stdin.log").read_text()
    assert password not in sent and password not in host.argv() and password not in capsys.readouterr().out
    script = next(part for part in sent.split("\n--\n") if "CREATE ROLE cos_board" in part)
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT" in script and "partnership_worker" not in script
    grants = sorted(line for line in script.splitlines() if line.startswith("GRANT"))
    assert grants == ["GRANT CONNECT ON DATABASE personal_agents TO cos_board;",
                      "GRANT SELECT, INSERT ON guild.board_operations TO cos_board;",
                      "GRANT SELECT, INSERT, UPDATE ON guild.floor_board_meta TO cos_board;",
                      "GRANT SELECT, INSERT, UPDATE ON guild.floor_postits TO cos_board;",
                      "GRANT USAGE ON SCHEMA guild TO cos_board;",
                      "GRANT USAGE ON SEQUENCE guild.floor_postits_id_seq TO cos_board;"]


def test_the_board_reads_the_file_the_layer_names(host):
    import yaml
    from minimoi_portal.guild_ui.board_headless_runner import database_url
    host.apply(["db"])
    assert database_url({"DATABASE_URL_FILE": str(host.root / bind.BOARD_DB_URL)}).startswith("postgresql://cos_board:")
    board = yaml.safe_load((REPO / "docker-compose.minimoi-1-2-production.yml").read_text())["services"]["cos-board"]
    assert board["environment"]["DATABASE_URL_FILE"] == "/run/board-secrets/db.url"
    assert any(v["target"] == "/run/board-secrets" and v["source"].endswith("/cos-board") for v in board["volumes"])
    assert bind.BOARD_DB_URL in setup.BY_OPERATOR and setup.BY_OPERATOR.count(bind.BOARD_DB_URL) == 1
    assert "/cos-board/db.url" not in (host.root / ".env").read_text()


def test_a_missing_board_table_makes_neither_role(host):
    host.configure(tables=sorted(set(ALL_DB_NAMES) - {"guild.board_operations"}))
    with pytest.raises(SystemExit, match="board_operations"):
        host.apply(["db"])
    assert not (host.root / bind.DB_URL).exists() and not (host.root / bind.BOARD_DB_URL).exists()
    assert "CREATE ROLE" not in (host.fake / "stdin.log").read_text()


def test_the_board_role_is_made_when_only_the_workers_exists(host):
    bind.write_new(host.root, bind.DB_URL, b"postgresql://kept\n")
    outcome = host.apply(["db"])
    assert bind.DB_URL in outcome["kept"] and bind.BOARD_DB_URL in outcome["written"]
    sent = (host.fake / "stdin.log").read_text()
    assert "CREATE ROLE cos_board" in sent and "CREATE ROLE partnership_worker" not in sent


def test_the_board_grants_are_exactly_what_the_board_apis_path_does(tmp_path):
    """The real BoardActions on the migrations' own tables, every operation board_headless can reach. Only SQLite can run
    here, so the statements are read from SQLite's trace. Postgres's own rules are in the comments on BOARD_TABLES."""
    sys.path.insert(0, str(REPO / "tests/guild/shop_floor"))
    from floor_db_helpers import SqliteFloor
    from minimoi_portal.guild_ui.board_actions import BoardActions, BoardContext
    statements = []
    store = SqliteFloor(tmp_path / "board").store("cos-board")
    real = store._connect

    def traced(url):
        conn = real(url)
        conn.set_trace_callback(statements.append)
        return conn
    store._connect = traced
    caps = frozenset(bind.BOARD_CAPABILITIES)
    svc = BoardActions(store, owner="robert", environment="prod", cursor_key=b"k" * 32)
    ctx = BoardContext("robert", "prod", "cos-board", "cos", "headless:test", caps)
    op = lambda: str(uuid4())   # noqa: E731
    made_op, edit_op = op(), op()
    made = svc.create(ctx, made_op, {"text": "first note", "label": None})
    note = made["receipt"]["target_ref"]
    edited = svc.update(ctx, edit_op, note, made["receipt"]["after_revision"], {"text": "changed", "done": True})
    assert edited["code"] == "updated"
    assert svc.update(ctx, op(), note, edited["receipt"]["after_revision"], {"done": False})["code"] == "updated"
    assert svc.read(ctx, note)["text"] == "changed"
    svc.query(ctx, query="changed", state="all")
    assert svc.result(ctx, edit_op)["state"] == "verified"
    assert svc.undo(ctx, op(), made_op, 1)["code"] == "revision_conflict"
    second = svc.create(ctx, op(), {"text": "second note"})
    assert svc.undo(ctx, op(), second["receipt"]["operation_id"], second["receipt"]["after_revision"])["code"] == "undone"

    used = {}
    for sql in statements:
        for verb, pattern in (("SELECT", r"\bFROM\s+(\w+\.\w+)"), ("INSERT", r"\bINSERT INTO\s+(\w+\.\w+)"), ("UPDATE", r"\bUPDATE\s+(\w+\.\w+)"),
                              ("DELETE", r"\bDELETE FROM\s+(\w+\.\w+)")):
            for table in re.findall(pattern, sql):
                if not (verb == "SELECT" and sql.lstrip().upper().startswith("DELETE")):
                    used.setdefault(table, set()).add(verb)
    assert used == {table: set(privileges.split(", ")) for table, privileges in bind.BOARD_TABLES.items()}


def test_the_board_api_never_takes_the_browsers_paths_or_a_database_queue():
    headless = (REPO / "minimoi_portal/guild_ui/board_headless.py").read_text()
    assert "ui_write" not in headless and "empty_trash" not in headless
    layer = (REPO / "docker-compose.minimoi-1-2-production.yml").read_text()
    assert "BOARD_API_QUEUE_FILE" not in layer
    queue = (REPO / "domains/guild/queue_store.py").read_text()
    assert "psycopg" not in queue and "guild." not in re.sub(r"domains\.guild\.\w+", "", queue)
    # The sequence is the default PostgreSQL gives this table's BIGSERIAL id.
    assert "id              BIGSERIAL PRIMARY KEY" in (REPO / "minimoi_portal/guild_ui/sql/001_floor_b1.sql").read_text()
    assert bind.BOARD_SEQUENCES == ("guild.floor_postits_id_seq",)


# ---- G8, G15: gateway keys ------------------------------------------------------------------------------------------------

def test_every_key_is_capped_at_20_and_only_in_a_0600_file(host, capsys):
    outcome = host.apply(["gateway"])
    assert set(bind.GATEWAY_KEYS) <= set(outcome["written"])
    programs = (host.fake / "stdin.log").read_text().split("\n--\n")
    assert sum("max_budget" in p for p in programs) == 4
    out = capsys.readouterr().out
    for rel in bind.GATEWAY_KEYS:
        path = host.root / rel
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        value = path.read_text()
        assert bind.KEY_FORM.fullmatch(value) and value not in host.argv() and value not in out
        assert value not in (host.root / ".env").read_text()
    assert bind.KEY_CAP == 20.0 and bind.KEY_DURATION == "30d"
    for argv in map(json.loads, host.argv().splitlines()):
        assert not any(a.startswith("sk-") for a in argv)


def test_the_keys_reach_only_models_the_callers_ask_for_and_production_has():
    source = (REPO / "services/rooms_worker/discussion.py").read_text()
    assert '"minimoi-" + self.teammate + "-agent"' in source
    assert bind.GATEWAY_KEYS[f"{S}/guildhall-cos/discussion-gateway.token"]["models"] == ["minimoi-cos-agent"]
    assert bind.GATEWAY_KEYS[f"{S}/guildhall-mc/discussion-gateway.token"]["models"] == ["minimoi-mc-agent"]
    prod = (REPO / "services/model_gateway/litellm.prod.yaml").read_text()
    for spec in bind.GATEWAY_KEYS.values():
        for model in spec["models"]:
            assert f"- model_name: {model}\n" in prod


def test_an_answer_without_the_cap_writes_nothing(host):
    host.configure(key_facts=[{"max_budget": None, "budget_duration": "30d", "models": ["minimoi-cos-agent"]}])
    with pytest.raises(SystemExit, match="capped"):
        host.apply(["gateway"])
    assert not any((host.root / rel).exists() for rel in bind.GATEWAY_KEYS)


def test_an_unhealthy_gateway_makes_no_key(host):
    host.configure(health="starting")
    with pytest.raises(SystemExit, match="not healthy"):
        host.apply(["gateway"])
    assert not (host.fake / "stdin.log").exists()


def test_a_second_run_mints_nothing(host):
    host.apply(["gateway"])
    first = {rel: (host.root / rel).read_text() for rel in bind.GATEWAY_KEYS}
    calls = len(host.argv().splitlines())
    assert set(bind.GATEWAY_KEYS) <= set(host.apply(["gateway"])["kept"])
    assert len(host.argv().splitlines()) == calls
    assert {rel: (host.root / rel).read_text() for rel in bind.GATEWAY_KEYS} == first


def _run_keygen(monkeypatch, info, master="sk-master-key-value-000000"):
    """The program as the gateway runs it, on a fake gateway HTTP."""
    import contextlib
    import io
    import urllib.request as u
    urls, deleted = [], []
    key = "sk-newkey" + "n" * 20

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        urls.append(req.full_url)
        if req.full_url.endswith("/key/generate"):
            return Reply(json.dumps({"key": key, "token": "hashed-id"}).encode())
        if "/key/info" in req.full_url:
            return Reply(json.dumps({"info": info}).encode())
        deleted.append(json.loads(req.data))
        return Reply(b"{}")
    monkeypatch.setattr(u, "urlopen", urlopen)
    monkeypatch.setenv("LITELLM_MASTER_KEY", master)
    out = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out):
        try:
            exec(bind.keygen_program(["minimoi-cos-agent"], 10, "a-"), {"__name__": "__main__"})
        except SystemExit as stop:
            code = stop.code
    return code, out.getvalue(), urls, deleted, key


def test_the_gateway_program_keeps_only_a_key_that_reads_back_capped(monkeypatch):
    good = {"max_budget": 20.0, "budget_duration": "30d", "models": ["minimoi-cos-agent"]}
    code, out, urls, deleted, key = _run_keygen(monkeypatch, good)
    assert code == 0 and out.splitlines()[0] == key and deleted == []
    # The key never goes in a URL: query strings reach the gateway's access log.
    assert all(key not in url for url in urls)


@pytest.mark.parametrize("info", [{"max_budget": None, "budget_duration": "30d", "models": ["minimoi-cos-agent"]},
                                  {"max_budget": 500.0, "budget_duration": "30d", "models": ["minimoi-cos-agent"]},
                                  {"max_budget": 20.0, "budget_duration": None, "models": ["minimoi-cos-agent"]},
                                  {"max_budget": 20.0, "budget_duration": "30d", "models": []}])
def test_the_gateway_program_deletes_an_uncapped_key(monkeypatch, info):
    code, out, _, deleted, key = _run_keygen(monkeypatch, info)
    assert code == 3 and key not in out and deleted == [{"keys": [key]}]


def test_voice_keys_are_read_from_their_private_file(tmp_path):
    from core.realtime_voice.summary_gateway import GatewaySummary
    file = tmp_path / "voice.key"
    file.write_text("sk-voicekey\n")
    file.chmod(0o600)
    name = "MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_COS_FILE"
    assert GatewaySummary("voice-summary-cos", environ={name: str(file)})._key == "sk-voicekey"
    assert GatewaySummary("voice-summary-cos", environ={"MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_COS": "sk-env", name: str(file)})._key == "sk-env"
    assert not GatewaySummary("voice-summary-cos", environ={}).available()
    # An empty variable, as docker-compose.prod.yml's scheduler line renders it, doesn't hide the file.
    assert GatewaySummary("voice-summary-cos", environ={"MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_COS": "", name: str(file)})._key == "sk-voicekey"
    from core.realtime_voice.intent_gateway import GatewayIntent    # the same key; direct_host builds both from it
    person = {"name": "Robert", "short": "Robert"}
    assert GatewayIntent("voice-summary-cos", person, environ={name: str(file)})._key == "sk-voicekey"
    file.chmod(0o644)
    assert not GatewaySummary("voice-summary-cos", environ={name: str(file)}).available()
    link = tmp_path / "link"
    file.chmod(0o600)
    link.symlink_to(file)
    assert not GatewaySummary("voice-summary-cos", environ={name: str(link)}).available()


# ---- G8: Records --------------------------------------------------------------------------------------------------------

def test_records_tokens_come_from_records_and_the_outbox_is_gone(host):
    outcome = host.apply(["records"])
    for spec in bind.RECORDS_TEAMMATES.values():
        for field in ("teammate_file", "worker_file", "observe_file"):
            assert spec[field] in outcome["written"]
            assert stat.S_IMODE((host.root / spec[field]).stat().st_mode) == 0o600
    assert (host.root / f"{S}/guildhall-cos/rooms-worker-cos.token").read_text().startswith("records-rooms-worker-cos-")
    assert list(host.state.iterdir()) == []
    assert "observetoken" not in host.argv()
    programs = (host.fake / "stdin.log").read_text()
    assert 'scope="observe"' in programs and "inst-cos" in programs and "inst-mc" in programs


def test_records_names_match_what_the_workers_read():
    cos = (REPO / "services/rooms_worker/cos.py").read_text()
    worker = (REPO / "services/rooms_worker/worker.py").read_text()
    assert "secrets/'rooms-worker-cos.token'" in cos and "secrets/'cos.token'" in cos
    assert 'secrets / "rooms-worker.token"' in worker and 'secrets / "mc.token"' in worker
    manage = (REPO / "domains/records/manage.py").read_text()
    for flag in ("--data-dir", "--out", "--teammate", "--label", "--worker-principal", "--days"):
        assert flag in manage


def test_partial_records_tokens_are_refused(host):
    bind.write_new(host.root, bind.RECORDS_TEAMMATES["cos"]["worker_file"], b"x" * 40)
    with pytest.raises(SystemExit, match="partly"):
        host.apply(["records"])


def test_records_binding_absent_writes_nothing(host, tmp_path):
    with pytest.raises(SystemExit, match="state folder"):
        bind.apply(host.root, ["records"], "", str(tmp_path / "absent.json"))
    assert not (host.root / bind.RECORDS_TEAMMATES["mc"]["teammate_file"]).exists()


# ---- G14: CoS's three agents ---------------------------------------------------------------------------------------------

def _profile(pid="openai:robert", status="ok"):
    return {"auth": {"oauth": {"profiles": [{"profileId": pid, "provider": "openai", "type": "oauth", "status": status,
                                              "access": "SECRET-ACCESS-VALUE"}]}}}


def test_cos_auth_reports_all_three_by_name_and_never_a_credential(host, capsys):
    host.configure(auth={a: _profile() for a in bind.COS_AGENTS})
    assert bind.main(["cos-auth"]) == 0
    out = capsys.readouterr().out
    assert "SECRET-ACCESS-VALUE" not in out
    report = json.loads(out)
    assert report["ok"] and report["robert_runs"] == []
    pinned = json.loads((REPO / "docker/cos-agent-a/openclaw.json").read_text())["agents"]["entries"]
    assert set(report["agents"]) == set(pinned)


def test_cos_auth_names_the_login_robert_runs_for_a_blind_agent_and_copies_nothing(host, capsys):
    host.configure(auth={"cos-agent-a": _profile(), "cos-meeting": _profile(status="expired"), "cos-closed": _profile()})
    assert bind.main(["cos-auth"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert not report["ok"]
    assert report["robert_runs"] == ["sudo docker exec -it minimoi-cos-agent-a node /app/openclaw.mjs models auth login "
                                     "--agent cos-meeting --provider openai --device-code"]
    calls = [json.loads(line) for line in host.argv().splitlines()]
    assert all(call[:2] == ["exec", "minimoi-cos-agent-a"] and "status" in call for call in calls)


def test_cos_auth_refuses_two_different_profiles(host, capsys):
    host.configure(auth={"cos-agent-a": _profile("openai:a"), "cos-meeting": _profile("openai:b"), "cos-closed": _profile("openai:a")})
    assert bind.main(["cos-auth"]) == 1
    assert json.loads(capsys.readouterr().out)["same_profile"] is False


# ---- G13: notices -------------------------------------------------------------------------------------------------------

def test_g13_the_workers_hash_reaches_the_notices_setting_beside_the_others(host):
    host.apply(["local"])
    from minimoi_portal.action_waiting.settings import parse_agent_tokens
    from services.partnership_tasks.auth import token_sha256
    line = [l for l in (host.root / ".env").read_text().splitlines() if l.startswith("MINIMOI_ACTION_NOTICES_AGENT_TOKENS=")]
    assert len(line) == 1
    tokens = parse_agent_tokens(line[0].split("=", 1)[1])
    worker = token_sha256((host.root / bind.NOTICES).read_text().strip())
    assert tokens[worker] == "partnership" and tokens["a" * 64] == "codex"


def test_g13_a_different_partnership_hash_is_refused(host):
    (host.root / ".env").write_text("MINIMOI_ACTION_NOTICES_AGENT_TOKENS=partnership:" + "d" * 64 + "\n")
    with pytest.raises(SystemExit, match="different partnership hash"):
        host.apply(["local"])


@pytest.mark.parametrize("value,ok", [
    ("", True), ("partnership:" + "a" * 64, True),
    ("claude-code:" + "a" * 64 + ",codex:" + "b" * 64 + ",partnership:" + "c" * 64, True),
    ("claude-code:" + "a" * 64 + ",codex:" + "b" * 64, True),
    ("somebody:" + "a" * 64, False), ("partnership:short", False)])
def test_g13_the_notices_command_takes_the_workers_hash(value, ok):
    script = (REPO / "scripts/operations/deploy_mc_container_ec2.sh").read_text()
    (pattern,) = re.findall(r'\[\[ -z "\$hashes" \|\| "\$hashes" =~ (\S+) \]\]', script)
    done = subprocess.run(["bash", "-c", f'[[ -z "$1" || "$1" =~ {pattern} ]]', "bash", value])
    assert (done.returncode == 0) is ok


# ---- host alone ---------------------------------------------------------------------------------------------------------

def test_the_operator_runs_on_a_host_from_its_two_files(tmp_path):
    alone = tmp_path / "alone"
    alone.mkdir()
    shutil.copy(Path(bind.__file__), alone)
    shutil.copy(Path(setup.__file__), alone)
    root = tmp_path / "root"
    root.mkdir()
    done = subprocess.run([sys.executable, "-I", str(alone / "minimoi_1_2_prod_bind.py"), "check", "--root", str(root)],
                          capture_output=True, text=True, cwd=alone)
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert report["ok"] and set(report["counts"]) == {"missing"}


# ---- G16: backup --------------------------------------------------------------------------------------------------------

BACKUP = REPO / "scripts/operations/minimoi_1_2_backup.sh"
FAKE_BACKUP_DOCKER = r'''#!/bin/bash
echo "$*" >> "$FAKE_DIR/argv.log"
case "$1" in
  inspect)
    case "$*" in *"{{.Image}}"*) echo sha256:postgresimage ;; *"{{.State.Status}}"*) echo running ;; *) cat "$FAKE_DIR/mc_running" 2>/dev/null || echo true ;; esac ;;
  stop) echo false > "$FAKE_DIR/mc_running"; echo stopped >> "$FAKE_DIR/events" ;;
  start) echo true > "$FAKE_DIR/mc_running"; echo started >> "$FAKE_DIR/events" ;;
  volume) [[ "$3" != "$FAKE_NO_VOLUME" ]] ;;
  rm) echo removed >> "$FAKE_DIR/events" ;;
  run)
    case "$*" in
      *"--entrypoint tar"*) echo "volume bytes" ;;
      *"--network none -e POSTGRES_HOST_AUTH_METHOD=trust"*) echo container; echo "$*" >> "$FAKE_DIR/restore_run" ;;
      *) exit 9 ;;
    esac ;;
  exec)
    shift
    while [[ "$1" == -* ]]; do shift; done
    target=$1; shift
    case "$1" in
      pg_dump) echo "dump of ${@: -1}" ;;
      pg_isready|createdb) ;;
      pg_restore) cat > /dev/null ;;
      psql)
        case "$*" in
          *to_regclass*) ;;
          *count*) if [[ "$target" == *restore* ]]; then cat "$FAKE_DIR/restored_rows"; else echo 5; fi ;;
        esac ;;
      *) exit 9 ;;
    esac ;;
  *) exit 9 ;;
esac
'''


@pytest.fixture
def backup_env(tmp_path):
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "docker").write_text(FAKE_BACKUP_DOCKER)
    (fake / "docker").chmod(0o755)
    (fake / "restored_rows").write_text("5\n")
    root = tmp_path / "minimoi"
    (root / "auth").mkdir(parents=True)
    (root / "auth/users.json").write_text('{"secret": "SECRET-IN-AUTH"}')
    (root / "data").mkdir()
    (root / ".env").write_text("A_SECRET=SECRET-IN-ENV\n")
    (root / "cos_memory.md").write_text("memory\n")

    def run(*args, **env):
        return subprocess.run(["bash", str(BACKUP), *args], capture_output=True, text=True,
                              env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "MINIMOI_ROOT": str(root),
                                   "FAKE_DIR": str(fake), "FAKE_NO_VOLUME": "", **env})
    return run, fake, root, tmp_path / "backup"


def test_backup_receipts_sizes_and_hashes_and_prints_no_secret(backup_env):
    run, fake, root, out = backup_env
    done = run("backup", str(out))
    assert done.returncode == 0, done.stderr
    receipt = (out / "receipt.txt").read_text()
    for name in ("personal_agents.dump", "litellm_keys.dump", "files.tgz", "minimoi-mc-state.tgz", "minimoi-mc-auth.tgz",
                 "minimoi-mc-claude-home.tgz", "minimoi-mc-relay-state.tgz"):
        data = (out / name).read_bytes()
        assert f"{name} size={len(data)} sha256={hashlib.sha256(data).hexdigest()}" in receipt
        assert stat.S_IMODE((out / name).stat().st_mode) == 0o600
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    assert "SECRET-IN" not in done.stdout + done.stderr + receipt
    assert "not_on_this_host=cos_voice" in receipt
    assert "MC chat is down" in done.stdout
    assert (fake / "events").read_text().split() == ["stopped", "started"]


def test_a_failed_volume_copy_still_starts_mc_again(backup_env):
    run, fake, _, out = backup_env
    done = run("backup", str(out), FAKE_NO_VOLUME="minimoi-mc-claude-home")
    assert done.returncode != 0
    assert (fake / "events").read_text().split() == ["stopped", "started"]


def test_backup_never_writes_over_another(backup_env):
    run, _, _, out = backup_env
    out.mkdir()
    (out / "old").write_text("x")
    done = run("backup", str(out))
    assert done.returncode == 2 and "not empty" in done.stderr
    assert sorted(p.name for p in out.iterdir()) == ["old"]


def test_restore_proof_is_throwaway_networkless_on_the_same_image_and_compares(backup_env):
    run, fake, _, out = backup_env
    assert run("backup", str(out)).returncode == 0
    done = run("restore-proof", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    started = (fake / "restore_run").read_text()
    assert "--network none" in started and "sha256:postgresimage" in started
    proof = (out / "restore-proof.txt").read_text()
    assert "rows guild.mc_delegations at_dump=5 restored=5 live=5" in proof and "files auth=same" in proof
    assert "removed" in (fake / "events").read_text().split()
    assert not (out / ".restore-files").exists()
    assert "SECRET-IN" not in done.stdout + done.stderr


@pytest.mark.parametrize("rows", ["4\n", "6\n"])
def test_restore_proof_fails_on_a_wrong_row_count(backup_env, rows):
    run, fake, _, out = backup_env
    assert run("backup", str(out)).returncode == 0
    (fake / "restored_rows").write_text(rows)
    done = run("restore-proof", str(out))
    assert done.returncode == 1 and "DIFFERENCES" in done.stdout


def test_restore_proof_fails_on_a_changed_file_and_names_no_contents(backup_env):
    run, _, root, out = backup_env
    assert run("backup", str(out)).returncode == 0
    (root / "auth/users.json").write_text('{"secret": "SECRET-CHANGED"}')
    done = run("restore-proof", str(out))
    assert done.returncode == 1 and "files auth=differs" in done.stdout
    assert "SECRET" not in done.stdout + done.stderr


def test_restore_proof_refuses_a_damaged_backup(backup_env):
    run, fake, _, out = backup_env
    assert run("backup", str(out)).returncode == 0
    (out / "files.tgz").write_bytes(b"damaged")
    done = run("restore-proof", str(out))
    assert done.returncode == 2 and "no longer matches" in done.stderr
    assert not (fake / "restore_run").exists()


def test_backup_names_match_production():
    prod = (REPO / "docker-compose.prod.yml").read_text()
    assert "container_name: postgres-ai-agents" in prod and "POSTGRES_DB=personal_agents" in prod
    for path in ("cos_memory.md", "cos_reminders", "cos_voice", "gateway-keys.env"):
        assert path in prod
    mc = (REPO / "docker-compose.mc.prod.yml").read_text()
    script = BACKUP.read_text()
    volumes = re.findall(r"name: (minimoi-mc-[a-z-]+)\}", mc)
    assert len(volumes) == 4 and all(volume in script for volume in volumes)
    assert "container_name: minimoi-mc-agent" in mc
    assert 'S3_BUCKET="s3://minimoi-backups"' in (REPO / "scripts/backup_s3.sh").read_text()
    assert "set -x" not in re.sub(r"#.*", "", script)
