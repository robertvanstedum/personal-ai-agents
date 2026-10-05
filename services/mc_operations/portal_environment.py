"""Run a reviewed Dev compose wrapper with the native relay caller token.

Never reads the OpenClaw operator configuration. The wrapper must include the
native overlay last; this helper only supplies its one interpolation variable.
"""
import argparse
import os
from pathlib import Path
import stat
import subprocess


def run(token_file: Path, command: list[str]) -> int:
    if not command:
        raise ValueError('command_required')
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(token_file, flags)
    with os.fdopen(fd, 'r') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('private_owner_token_required')
        raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError('oversized_caller_token')
        token = raw.strip()
    if not 32 <= len(token) <= 4096 or any(c.isspace() for c in token):
        raise ValueError('invalid_caller_token')
    env = dict(os.environ, MC_NATIVE_RELAY_TOKEN=token)
    # Compose may render interpolated secrets, including on failure.
    # The exit code is the only public output; inspect runtime separately.
    return subprocess.run(command, env=env, check=False,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    try:
        result = run(args.token_file, command)
    except (OSError, ValueError):
        raise SystemExit('Native MC portal environment unavailable; check private caller token and command.')
    raise SystemExit(result)
