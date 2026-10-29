"""S1-H: the authority service as a process (``mc-delegation-runner authority --host-config``, gate D's
``minimoi-authority-<env>.service``) against a real PostgreSQL store: over TLS on 127.0.0.1 with a throwaway
self-signed certificate, the real host client speaks to it; its own liveness and its sweeper beat apart; SIGUSR1 stops
the sweeper alone (liveness goes on, the watch raises ``runtime-sweeper-stale`` once); the read path answers a
reader; SIGTERM stops it cleanly. Nothing here runs as a service on the Mac: a subprocess of the test, removed after.

Opt-in: a disposable mc12_test_* database (tests/minimoi_actions/postgres_fixture_support.py)."""
import hashlib
import json
import os
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "minimoi_actions"))

from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402

from core.mc_delegation_dispatcher import door, server  # noqa: E402

MIGRATIONS = ("core/mc_delegation_dispatcher/sql/001_delegations.sql",
              "core/mc_delegation_dispatcher/sql/002_host_runtime_v2.sql",
              "core/minimoi_actions/sql/004_action_receipts.sql",
              "core/minimoi_actions/sql/005_action_grants.sql")
HOST_TOKEN, READER_TOKEN = "h" * 48, "r" * 48
FACTS = {"host_id": "mac-test-1", "bundle_digest": "sha256:" + "a" * 64, "roles": ["worker", "witness"],
         "availability": "intermittent", "grace": 900, "last_gap_s": 0, "announced": "online"}


@unittest.skipUnless(DSN and shutil.which("openssl"), "requires disposable MC_BINDING_TEST_DSN and openssl")
class AuthorityServiceOnPostgres(PostgresFixture, unittest.TestCase):
    migrations = MIGRATIONS

    def setUp(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""TRUNCATE guild.mc_delegations, guild.mc_delegation_runners, guild.mc_delegation_events,
                guild.mc_delegation_reservations CASCADE""")
        self.tmp = Path(tempfile.mkdtemp(prefix="authority-"))
        t = self.tmp
        subprocess.run([shutil.which("openssl"), "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout",
                        str(t / "key.pem"), "-out", str(t / "cert.pem"), "-days", "1", "-subj", "/CN=127.0.0.1",
                        "-addext", "subjectAltName=IP:127.0.0.1"], check=True, capture_output=True)
        values = json.loads((ROOT / "tests" / "mc_delegation_dispatcher" / "authority.test_values.json").read_text())
        values.update(sweeper={"sweep_s": 1, "gap_threshold_s": 4},
                      hosts={"contact_interval_s": 1, "contact_timeout_s": 3, "renew_s": 2, "offline_hold_s": 5})
        (t / "authority.json").write_text(json.dumps(values))
        (t / "dev.dsn").write_text(DSN)
        os.chmod(t / "dev.dsn", 0o600)
        (t / "creds.json").write_text(json.dumps({
            "schema": server.CREDENTIALS_SCHEMA, "environment": "dev",
            "hosts": {"mac-test-1": hashlib.sha256(HOST_TOKEN.encode()).hexdigest()},
            "readers": {"collector": {"sha256": hashlib.sha256(READER_TOKEN.encode()).hexdigest(),
                                      "role": "observer"}}}))
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        host = {"schema": "minimoi.host.v1", "host_id": "staging-test", "environment": "dev", "principal": "robert",
                "roles": ["authority", "worker", "witness"], "expected_availability": "always_on",
                "unreachable_grace_s": 600, "service_manager": "systemd",
                "authority": {"endpoint_id": "dev-authority", "url": door.DOORS[1],
                              "credential_ref": "keychain:minimoi_runtime/dev", "values": str(t / "authority.json")},
                "capabilities": ["actor:fakework"], "concurrency": 1, "artifact_root": str(t / "artifacts"),
                "workspace_root": str(t / "work"), "mirrors": {"r": str(t / "m")},
                "interpreter": {"path": sys.executable, "version": "%d.%d" % sys.version_info[:2]},
                "bundle_root": str(ROOT),
                "door": {"listen": f"127.0.0.1:{self.port}", "tls_cert": str(t / "cert.pem"),
                         "tls_key": str(t / "key.pem"), "dsn_file": str(t / "dev.dsn"),
                         "credentials": str(t / "creds.json")}}
        (t / "host.yaml").write_text(json.dumps(host))
        env = {"HOME": str(t / "home"), "PATH": "/usr/bin:/bin"}
        self.proc = subprocess.Popen([sys.executable, "-m", "core.mc_delegation_runner.cli", "authority",
                                      "--host-config", str(t / "host.yaml")], cwd=ROOT, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        ctx = ssl.create_default_context(cafile=str(t / "cert.pem"))
        real = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx)).open
        port = self.port

        def opener(req, timeout):
            local = urllib.request.Request(f"https://127.0.0.1:{port}{door.PATH}", data=req.data,
                                           method=req.get_method(), headers=dict(req.header_items()))
            return real(local, timeout=timeout)
        self.client = door.HttpDispatcherV2(door.DOORS[1], HOST_TOKEN, t / "artifacts", opener=opener)
        self.ctx = ctx
        for _ in range(100):
            try:
                self.client.heartbeat("mac-test-1:runner", "online", ["actor:fakework"], FACTS)
                return
            except door.DoorUnreachable:
                time.sleep(0.2)
            except OSError:
                time.sleep(0.2)
        self.fail("the authority service never answered: " + self.proc.stdout.read1(4000).decode(errors="replace"))

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(10)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self):
        from psycopg2.extras import RealDictCursor
        with self.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""SELECT runner_id, EXTRACT(EPOCH FROM clock_timestamp() - seen_at) AS age
                FROM guild.mc_delegation_runners""")
            return {r["runner_id"]: float(r["age"]) for r in cur.fetchall()}

    def events(self, kind):
        from psycopg2.extras import RealDictCursor
        with self.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT body FROM guild.mc_delegation_events WHERE kind=%s", (kind,))
            return [r["body"] for r in cur.fetchall()]

    def test_the_service_answers_beats_apart_stops_its_sweeper_alone_and_stops_cleanly(self):
        self.assertIsNone(self.client.claim("mac-test-1:runner", ["actor:fakework"]))     # nothing queued
        time.sleep(2)
        ages = self.rows()
        self.assertLess(ages["authority/dev/robert"], 3)
        self.assertLess(ages["sweeper/dev/robert"], 3)
        self.assertLess(ages["mac-test-1:runner"], 5)
        with self.assertRaises(door.DoorRefused):                       # another host's id: refused by the door
            self.client.heartbeat("staging-test:runner", "online", ["actor:fakework"], FACTS)
        req = urllib.request.Request(f"https://127.0.0.1:{self.port}{server.STATE_PATH}",
                                     headers={"Authorization": "Bearer " + READER_TOKEN})
        with urllib.request.urlopen(req, context=self.ctx, timeout=10) as r:
            doc = json.loads(r.read())
        self.assertEqual(doc["schema"], "minimoi.runtime.state.v1")    # published by its sweeper
        os.kill(self.proc.pid, signal.SIGUSR1)                          # E11: the sweeper alone
        time.sleep(8)
        ages = self.rows()
        self.assertGreater(ages["sweeper/dev/robert"], 4)
        self.assertLess(ages["authority/dev/robert"], 3)                  # liveness went on: no outage
        self.assertEqual(self.events("authority.suspended"), [])
        stale = [b for b in self.events("notice.raised") if b["key"].startswith("runtime-sweeper-stale:")]
        self.assertEqual(len(stale), 1)                                  # raised once by the watch
        self.assertEqual(self.client.heartbeat("mac-test-1:runner", "online", ["actor:fakework"], FACTS)["state"],
                         "recorded")                                    # the door still serves
        self.proc.send_signal(signal.SIGTERM)
        self.assertEqual(self.proc.wait(20), 0)
        out = self.proc.stdout.read().decode()
        self.assertIn('"state": "serving"', out)
        self.assertNotIn(HOST_TOKEN, out)


if __name__ == "__main__":
    unittest.main()
