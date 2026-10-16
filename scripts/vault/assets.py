#!/usr/bin/env python3
"""Owner-selected native custody. Counts/hash receipts only; no moves or extraction."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from core.vault_t1 import assets
from core.vault_t1.reader import VaultError

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True)
 sub=p.add_subparsers(dest='action',required=True)
 c=sub.add_parser('capture');c.add_argument('--source',required=True);c.add_argument('--source-instance',required=True);c.add_argument('--source-id',required=True);c.add_argument('--scope',default='robert');c.add_argument('--execute',action='store_true');c.add_argument('--expected-sha256');c.add_argument('--expected-bytes',type=int)
 sub.add_parser('verify')
 e=sub.add_parser('export');e.add_argument('--out',required=True);e.add_argument('--asset-id',action='append',required=True)
 a=p.parse_args()
 try:
  if a.action=='capture':
   if not a.execute:result={'prepared':True,'captured':False,'original_retained':True,'limit_bytes':assets.MAX_BYTES}
   else:result=assets.capture(a.root,a.source,source_instance=a.source_instance,source_id=a.source_id,scope=a.scope,expected_sha256=a.expected_sha256,expected_bytes=a.expected_bytes)
  elif a.action=='verify':result=assets.verify(a.root)
  else:
   m=assets.export(a.root,a.out,ids=a.asset_id);result={'format':m['format'],'counts':m['counts'],'complete':m['complete']}
  print(json.dumps(result,sort_keys=True));return 0 if result.get('ok',True) else 1
 except (VaultError,OSError) as exc:
  print(json.dumps({'ok':False,'code':exc.code if isinstance(exc,VaultError) else 'asset_io_failed'}));return 1
if __name__=='__main__':sys.exit(main())
