#!/usr/bin/env python3
"""
thread_expiry.py — Daily expiry check for research threads.

Marks threads as 'expired' when their `expires` date has passed and
the thread is still 'active'. Called by launchd daily (see
launchd/com.user.research-expiry.plist).

Usage:
  cd <repository-root>
  python agent/thread_expiry.py
  python agent/thread_expiry.py --dry-run
"""

import argparse
import sys
from datetime import date
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from domains.curator.research.paths import configured
PATHS = configured()
ROOT = PATHS.code
DATA_ROOT = PATHS.data
STATE_ROOT = PATHS.state


THREADS_DIR = DATA_ROOT / 'threads'

# Add agent dir to path for threads module
from domains.curator.research.agent.threads import load_thread, save_thread


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
