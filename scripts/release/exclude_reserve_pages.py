#!/usr/bin/env python3
"""Exclude the Guild's reserve pages from a release tree (Guild 1.1: the Workbench is retained, not shipped).

The Workbench "wall", the old Build Queue page and the old Labs page are kept in reserve in git (tag
workbench-reserve-2026-10-07) and are never served in Guild 1.1: with MINIMOI_GUILD_RESERVE_PAGES off, their old
addresses redirect to the pages that replaced them. This script removes their templates and script from a copy of
minimoi_portal/guild_ui that is about to become a release image, so the files do not ship at all.

    python3 scripts/release/exclude_reserve_pages.py <path to a guild_ui directory>           # remove them
    python3 scripts/release/exclude_reserve_pages.py <path to a guild_ui directory> --check   # only verify they are absent

It touches nothing else and refuses a directory that is not a guild_ui tree. Exit 0: done / verified absent.
Exit 1: something is still present (--check). Exit 2: refused.
"""
import os
import sys

RESERVE = (
    "templates/guild_floor/bench.html",     # the Workbench wall page
    "templates/guild_floor/queue.html",     # the old Build Queue page (the Build Log replaced it)
    "templates/guild_floor/labs.html",      # the old Labs page (Prototype Lab replaced it)
    "static/js/bench.js",                   # the Workbench arrangement script
)


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    check = "--check" in argv
    if len(args) != 1:
        print(__doc__)
        return 2
    root = os.path.abspath(args[0])
    if not (os.path.isfile(os.path.join(root, "pages.py")) and os.path.isdir(os.path.join(root, "templates", "guild_floor"))):
        print(f"refused: {root} is not a guild_ui directory")
        return 2
    present = [r for r in RESERVE if os.path.exists(os.path.join(root, r))]
    if check:
        for r in present:
            print(f"STILL PRESENT {r}")
        print("reserve pages absent" if not present else f"{len(present)} reserve file(s) present")
        return 0 if not present else 1
    for r in present:
        os.unlink(os.path.join(root, r))
        print(f"removed {r}")
    print(f"{len(present)} reserve file(s) removed; {len(RESERVE) - len(present)} already absent")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
