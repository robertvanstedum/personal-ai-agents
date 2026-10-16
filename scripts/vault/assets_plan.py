#!/usr/bin/env python3
"""Explicit private inventory selection -> read-only custody plan; no capture."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.vault_t1 import asset_plan
from core.vault_t1.reader import VaultError


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({'ok': False, 'code': 'asset_plan_arguments_invalid'}))
        raise SystemExit(2)


def main(argv=None):
    p = Parser(description=__doc__)
    p.add_argument('--inventory', required=True)
    p.add_argument('--inventory-sha256', required=True)
    p.add_argument('--selection', required=True, help='Private JSON with exact old inventory IDs and optional explicit current paths')
    p.add_argument('--source-root', required=True)
    p.add_argument('--source-instance', required=True)
    p.add_argument('--root', required=True, help='Existing private custody store with existing capture lock')
    p.add_argument('--out', help='Optional new0600 receipt under existing owned0700 directory; never overwritten')
    a = p.parse_args(argv)
    try:
        inventory = asset_plan.load_inventory(a.inventory, a.inventory_sha256)
        ids, paths = asset_plan.load_selection(a.selection)
        result = asset_plan.plan(inventory, ids, source_root=a.source_root,
                                 source_instance=a.source_instance, custody_root=a.root,
                                 source_paths=paths, inventory_sha256=a.inventory_sha256)
        if a.out:
            if asset_plan._absolute(a.out).is_relative_to(asset_plan._absolute(a.root)):
                raise VaultError('asset_plan_receipt_inside_custody')
            asset_plan.write_receipt(a.out, result)
        print(json.dumps({'ok': True, 'captured': False,
                          'admitted_for_review': result['admitted_for_review'],
                          'selected_files': result['selected_files'],
                          'current_selected_bytes': result['current_selected_bytes'],
                          'proposed_captures': result['proposed_captures'],
                          'counts': result['counts']}, sort_keys=True))
        return 0 if result['admitted_for_review'] else 1
    except (VaultError, OSError) as exc:
        # Never emit source paths, parser exception text, original names/content.
        print(json.dumps({'ok': False, 'code': exc.code if isinstance(exc, VaultError) else 'asset_plan_io_failed'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
