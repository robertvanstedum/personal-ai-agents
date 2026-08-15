#!/usr/bin/env python3
"""Persist COS Agent A's ignored local settings without revealing its token."""

import argparse
import os
from pathlib import Path

from ensure_model_gateway_key import ensure_env_secret


VARIABLE_NAME = "COS_AGENT_A_GATEWAY_TOKEN"
SEED_ENV_NAME = "COS_AGENT_A_GATEWAY_TOKEN_SEED"
BACKEND_VARIABLE_NAME = "COS_BACKEND_TYPE"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    seed = os.environ.get(SEED_ENV_NAME) or None
    outcome = ensure_env_secret(
        args.env_file.resolve(),
        variable_name=VARIABLE_NAME,
        value=seed,
    )
    print(f"{VARIABLE_NAME}: {outcome}; value not displayed")
    backend_outcome = ensure_env_secret(
        args.env_file.resolve(),
        variable_name=BACKEND_VARIABLE_NAME,
        value="openclaw",
    )
    print(f"{BACKEND_VARIABLE_NAME}: {backend_outcome}; configured for Agent A")


if __name__ == "__main__":
    main()
