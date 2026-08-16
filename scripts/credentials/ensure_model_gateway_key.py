#!/usr/bin/env python3
"""Ensure the ignored local env has an independent model-gateway key.

The value is generated with ``secrets``, written atomically with owner-only
permissions, and never printed. Existing non-empty values are preserved.
"""

import argparse
import os
from pathlib import Path
import secrets
import tempfile


VARIABLE_NAME = "MINIMOI_MODEL_GATEWAY_KEY"
RECEIPT_VARIABLE_NAME = "MINIMOI_MODEL_GATEWAY_RECEIPT_KEY"


def ensure_env_secret(
    env_path: Path,
    *,
    variable_name: str,
    value: str | None = None,
) -> str:
    """Atomically ensure one non-empty local secret without printing its value."""
    current = env_path.read_text() if env_path.exists() else ""
    lines = current.splitlines()
    empty_declaration_index = None
    for index, line in enumerate(lines):
        name, separator, existing_value = line.partition("=")
        if separator and name.strip() == variable_name:
            if existing_value.strip():
                return "existing"
            empty_declaration_index = index

    secret_value = value or f"sk-{secrets.token_urlsafe(48)}"
    declaration = f"{variable_name}={secret_value}"
    if empty_declaration_index is None:
        lines.append(declaration)
    else:
        lines[empty_declaration_index] = declaration
    updated = "\n".join(lines) + "\n"

    env_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{env_path.name}.",
        dir=env_path.parent,
        text=True,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as temporary_file:
            temporary_file.write(updated)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, env_path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return "created"


def ensure_gateway_key(env_path: Path) -> str:
    """Return ``existing`` or ``created`` after safely ensuring the key."""
    return ensure_env_secret(env_path, variable_name=VARIABLE_NAME)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    outcome = ensure_gateway_key(args.env_file.resolve())
    print(f"{VARIABLE_NAME}: {outcome}; value not displayed")
    receipt_outcome = ensure_env_secret(
        args.env_file.resolve(),
        variable_name=RECEIPT_VARIABLE_NAME,
    )
    print(f"{RECEIPT_VARIABLE_NAME}: {receipt_outcome}; value not displayed")


if __name__ == "__main__":
    main()
