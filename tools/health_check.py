#!/usr/bin/env python3
"""
health_check.py — confirm all mini-moi services are alive.

Run at the start of every session (especially after overnight / travel)
to confirm the laptop services survived and are responding.

Usage:
    python3 tools/health_check.py
"""

import sys
import urllib.request

CHECKS = [
    ("Curator  ", "http://localhost:8766/"),
    ("German   ", "http://localhost:8767/"),
    ("Portal   ", "http://localhost:5001/"),
]


def main():
    ok = True
    print("── Service health check ─────────────────────")
    for name, url in CHECKS:
        try:
            urllib.request.urlopen(url, timeout=3)
            print(f"  ✓ {name}  {url}")
        except Exception as e:
            print(f"  ✗ {name}  {url}  — {e}")
            ok = False
    print("─────────────────────────────────────────────")
    if ok:
        print("  All services up.")
    else:
        print("  ⚠️  One or more services not responding.")
        print("     Check launchd: launchctl list | grep vanstedum")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
