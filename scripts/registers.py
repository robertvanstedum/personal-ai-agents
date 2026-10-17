#!/usr/bin/env python3
"""Explicit local work-card operator. No default root or live initialization."""
import argparse,json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.work_registers import RegisterStore


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True);p.add_argument('--environment',choices=['dev','production'],required=True)
    sub=p.add_subparsers(dest='action',required=True);sub.add_parser('init');sub.add_parser('project')
    query=sub.add_parser('query');query.add_argument('agent');query.add_argument('--id');query.add_argument('--status');query.add_argument('--kind')
    add=sub.add_parser('file');add.add_argument('--actor',required=True);add.add_argument('--register',required=True);add.add_argument('--request-id',required=True);add.add_argument('--card',required=True);add.add_argument('--id');add.add_argument('--revision',type=int)
    a=p.parse_args();store=RegisterStore(a.root,environment=a.environment)
    if a.action=='init':store.initialize();result={'initialized':True,'environment':a.environment}
    elif a.action=='project':store.regenerate();result={'projected':True,'environment':a.environment}
    elif a.action=='query':result=store.projection(a.agent,kind=a.kind,status=a.status,entry_id=a.id)
    else:
        from core.work_registers import parse,read
        meta,body=parse(read(a.card));result=store.save(a.actor,a.register,meta,body,request_id=a.request_id,entry_id=a.id,expected_revision=a.revision)
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))
if __name__=='__main__':main()
