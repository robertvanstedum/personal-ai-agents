#!/usr/bin/env python3
"""
thread_expiry.py — Daily expiry check for research threads.

Marks threads as 'expired' when their `expires` date has passed and
the thread is still 'active'. Called by launchd daily (see
launchd/com.user.research-expiry.plist).

Usage:
  cd _NewDomains/research-intelligence
  python agent/thread_expiry.py
  python agent/thread_expiry.py --dry-run
"""

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THREADS_DIR = ROOT / 'data' / 'threads'

# Add agent dir to path for threads module
sys.path.insert(0, str(Path(__file__).resolve().parent))
from threads import load_thread, save_thread


def check_expiry(dry_run: bool = False) -> int:
    """
    Check all active threads and mark expired ones.
    Returns the number of threads transitioned to 'expired'.
    """
    if not THREADS_DIR.exists():
        print("No threads directory found — nothing to check.")
        return 0

    today = date.today().isoformat()
    changed = 0

    for thread_dir in sorted(THREADS_DIR.iterdir()):
        if not thread_dir.is_dir():
            continue
        topic = thread_dir.name
        try:
            thread = load_thread(topic)
            if thread is None:
                continue
            if thread.status != 'active':
                continue
            if not thread.expires:
                continue
            if thread.expires <= today:
                if dry_run:
                    print(f"[dry-run] Would expire: {topic} (expires: {thread.expires})")
                else:
                    thread.status = 'expired'
                    save_thread(topic, thread)
                    print(f"Expired: {topic} (expires: {thread.expires})")
                changed += 1
        except Exception as e:
            print(f"Error processing '{topic}': {e}", file=sys.stderr)

    suffix = " (dry run)" if dry_run else ""
    print(f"Expiry check complete{suffix}. {changed} thread(s) transitioned to 'expired'.")
    return changed


def main():
    parser = argparse.ArgumentParser(description='Daily expiry check for research threads')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print what would be expired without making changes')
    args = parser.parse_args()
    check_expiry(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
