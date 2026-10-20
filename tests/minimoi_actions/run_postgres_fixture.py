"""Run harness tests against a disposable PostgreSQL process, without Docker.

Usage: <venv>/bin/python tests/minimoi_actions/run_postgres_fixture.py
       --postgres-bin /absolute/path/to/postgres/bin

Supply trusted, already-installed/unpacked PostgreSQL binaries. This script
downloads/installs nothing. The server has no TCP listener, uses a private Unix
socket and synthetic database, and is stopped before its temporary directory
is removed. No project database, environment file or application is loaded.
On the shared build Mac run this through the existing heavy_job.py lock.
"""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--postgres-bin', required=True, type=Path)
    args = parser.parse_args()
    binaries = args.postgres_bin.resolve(strict=True)
    for name in ('postgres', 'initdb', 'pg_ctl', 'createdb'):
        if not (binaries / name).is_file() or not os.access(binaries / name, os.X_OK):
            parser.error(f'missing executable: {name}')
    repo = Path(__file__).resolve().parents[2]
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('PG') and k not in {'DATABASE_URL', 'MC_BINDING_TEST_DSN'}}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    root = Path(tempfile.mkdtemp(prefix='mc12-pg-', dir='/private/tmp' if sys.platform == 'darwin' else None))
    stopped = False
    try:
        data = root / 'data'
        socket = root / 'socket'
        socket.mkdir(mode=0o700)
        def run(name, *argv, check=True):
            result = subprocess.run([str(binaries / name), *map(str, argv)], env=env,
                                    capture_output=True, text=True, timeout=30)
            if check and result.returncode:
                raise RuntimeError(f'{name} failed: {result.stderr[-2000:]}')
            return result
        print(run('postgres', '--version').stdout.strip(), flush=True)
        run('initdb', '-D', data, '-U', 'mc_fixture', '--auth-local=trust',
            '--auth-host=reject', '--no-locale', '--encoding=UTF8')
        try:
            # All option values below are constants or private mkdtemp paths.
            options = (f"-k {socket} -c listen_addresses='' -c unix_socket_permissions=0700 "
                       '-c shared_buffers=16MB -c max_connections=16 -c fsync=on')
            run('pg_ctl', '-D', data, '-l', root / 'server.log', '-o', options, '-w', 'start')
            run('createdb', '-h', socket, '-U', 'mc_fixture', 'mc12_test_bindings')
            env['MC_BINDING_TEST_DSN'] = f'host={socket} user=mc_fixture dbname=mc12_test_bindings'
            result = subprocess.run([sys.executable, '-m', 'unittest', 'discover',
                                     '-s', 'tests/minimoi_actions', '-v'], cwd=repo, env=env)
            return result.returncode
        finally:
            # Also handles a partially successful start; never remove a live
            # server's data directory if shutdown cannot be confirmed.
            run('pg_ctl', '-D', data, '-m', 'fast', '-w', 'stop', check=False)
            stopped = run('pg_ctl', '-D', data, 'status', check=False).returncode == 3
            if not stopped:
                raise RuntimeError(f'fixture shutdown unconfirmed; data retained at {root}')
    finally:
        if stopped:
            shutil.rmtree(root)


if __name__ == '__main__':
    raise SystemExit(main())
