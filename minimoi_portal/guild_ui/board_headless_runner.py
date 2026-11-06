"""One private Board API process, separately provisioned from MC.

Run explicitly: python -m minimoi_portal.guild_ui.board_headless_runner
Requires BOARD_API_PROFILES_FILE, BOARD_API_CURSOR_KEY_FILE, BOARD_API_OWNER,
MINIMOI_BOARD_ENV and DATABASE_URL_FILE (an owner-only file holding the URL) or
DATABASE_URL. No secrets or identities are generated.
Default bind is loopback. The CoS companion may explicitly bind 0.0.0.0 on
an isolated Docker network with no published ports (operator verified).
Key file is owner-only JSON: {"profiles": [KeyProfile fields plus secret_hex]}.
A PostgreSQL session lock excludes concurrent workers; every request checks
that lock connection. Startup quarantine expires all prior transport nonces.
"""
import json
import os
from pathlib import Path
import stat
import threading
from contextlib import contextmanager

from core.domain_api.auth import KeyRegistry
from core.domain_api.contract import KeyProfile, require
from .board_headless import create_app
from .stores import FloorStores


def private_bytes(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and st.st_uid in {0, os.getuid()}
                and stat.S_IMODE(st.st_mode) & 0o077 == 0, "private_file_required")
        require(st.st_size <= 128 * 1024, "configuration_too_large")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(128 * 1024 + 1)
        require(len(raw) <= 128 * 1024, "configuration_too_large")
        return raw
    finally:
        os.close(fd)


class InstalledKeys(KeyRegistry):
    def __init__(self, path, lease):
        self.path, self.lease = path, lease
        super().__init__([])

    def refresh(self):
        from core.domain_api.contract import decode
        value = decode(private_bytes(self.path), maximum=128 * 1024)
        require(set(value) == {"profiles"} and type(value["profiles"]) is list, "key_file_invalid")
        profiles = []
        for row in value["profiles"]:
            row = dict(row)
            row["secret"] = bytes.fromhex(row.pop("secret_hex"))
            row["capabilities"] = frozenset(row["capabilities"])
            profiles.append(KeyProfile(**row))
        self.replace(profiles)

    @contextmanager
    def admission(self):
        with self._lock:
            self.lease.check()
            self.refresh()
            yield

    def lookup(self, key_id):
        # Called again at the transaction guard, so a replaced file is checked
        # immediately before the effect even if parsing/read work took time.
        with self._lock:
            self.refresh()
            return super().lookup(key_id)


class SingleProcessLease:
    def __init__(self, connect, *, owner, environment, floor="guild"):
        self.conn = connect()
        self.lock = threading.Lock()
        self.conn.autocommit = True
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))",
                            [json.dumps(["board-api-process-v1", owner, environment] + ([floor] if floor != "guild" else []))])
                require(cur.fetchone()[0], "board_api_already_running", 503)
        except Exception:
            self.conn.close()
            raise

    def check(self):
        with self.lock:
            with self.conn.cursor() as cur:
                cur.execute("SELECT 1")
                require(cur.fetchone() == (1,), "board_api_lease_lost", 503)

    def close(self):
        self.conn.close()


def listener(floor, value):
    require(value == "127.0.0.1" or (value == "0.0.0.0" and floor == "cos-board"), "board_bind_invalid")
    return value


def database_url(environ):
    """The URL from its private file when one is named (production: no password in the shared .env), else DATABASE_URL."""
    path = environ.get("DATABASE_URL_FILE")
    if not path:
        return environ["DATABASE_URL"]
    value = private_bytes(path).decode("utf-8").strip()
    require(value, "database_url_file_empty")
    return value


def main():
    import psycopg2
    from werkzeug.serving import make_server
    env, owner = os.environ["MINIMOI_BOARD_ENV"], os.environ["BOARD_API_OWNER"]
    require(env in {"dev", "prod"}, "environment_invalid")
    floor = os.environ.get("BOARD_API_FLOOR", "guild")
    require(floor in {"guild", "cos-board"}, "board_floor_invalid")
    bind = listener(floor, os.environ.get("BOARD_API_BIND", "127.0.0.1"))
    dsn = database_url(os.environ)
    def connect():
        return psycopg2.connect(dsn, connect_timeout=3, options="-c statement_timeout=3000 -c lock_timeout=2000")
    lease = SingleProcessLease(connect, owner=owner, environment=env, floor=floor)
    try:
        registry = InstalledKeys(os.environ["BOARD_API_PROFILES_FILE"], lease)
        registry.refresh()
        cursor_key = private_bytes(os.environ["BOARD_API_CURSOR_KEY_FILE"])
        store = FloorStores(lambda: dsn, floor=floor)
        from domains.guild.queue_store import QueueStore
        from .adapters.queue_reader import LiveBuildQueue
        queue_path = os.environ.get("BOARD_API_QUEUE_FILE")
        queue = LiveBuildQueue(QueueStore(queue_path)) if queue_path else None
        def item_allowed(principal, item):
            if queue is None or principal != owner:
                return False
            value = queue.get_item(item)
            return value.ok and value.data is not None
        app = create_app(store=store, owner=owner, environment=env, cursor_key=cursor_key,
                         registry=registry, item_allowed=item_allowed)
        # No listener destination comes from model text. The operator verifies Docker isolation.
        server = make_server(bind, int(os.environ.get("BOARD_API_PORT", "5013")), app, threaded=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
    finally:
        lease.close()


if __name__ == "__main__":
    main()
