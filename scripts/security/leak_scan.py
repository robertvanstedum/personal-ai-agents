#!/usr/bin/env python3
"""Run the public leak scan. See core/leak_scan.py and issue #612."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.leak_scan import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
