#!/usr/bin/env python3
"""Owner/MC reporting, independent of CI. Explicit read-only local operations."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.memory_ask import regression, runner


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=('catalog', 'history', 'report', 'compare'))
    p.add_argument('--root', type=Path)
    p.add_argument('--suite-id')
    p.add_argument('--run-id')
    p.add_argument('--first')
    p.add_argument('--second')
    a = p.parse_args(argv)
    try:
        if a.operation == 'catalog':
            print((Path(__file__).with_name('regression-catalog.json')).read_text())
            return 0
        if a.root is None:
            p.error('--root is required for private results')
        if a.operation == 'history':
            out = regression.history(a.root, suite_id=a.suite_id)
        elif a.operation == 'report':
            if not a.run_id:
                p.error('--run-id is required')
            print(regression.render_report(runner.load_run(a.root, a.run_id, include_details=True)))
            return 0
        else:
            if not a.first or not a.second:
                p.error('--first and --second are required')
            out = regression.compare_cases(runner.load_run(a.root, a.first, include_details=True),
                                           runner.load_run(a.root, a.second, include_details=True))
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    except (runner.Refused, OSError):
        print(json.dumps({'error': 'regression_report_refused', 'writes': 0, 'provider_calls': 0}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
