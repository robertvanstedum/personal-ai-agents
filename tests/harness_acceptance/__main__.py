"""``harness-acceptance`` (spec v0.5 §9.2): ``python -m harness_acceptance run --plan host_runtime_v2 --evidence DIR``,
run from ``tests/`` (or ``PYTHONPATH=tests``)."""
import sys

from harness_acceptance.controller import main

sys.exit(main())
