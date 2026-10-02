"""Inspection is the default; only an explicit command creates a fixture store."""
import argparse
import json
from .access import FixtureAuthority
from .adapters import UNAVAILABLE
from .contracts import ContractError
from .retrieval import capabilities
from .store import Store


def main():
    parser=argparse.ArgumentParser(description='Offline P0–P2 fixture foundation; live activation disabled')
    parser.add_argument('command',choices=['inspect','init-fixture'],default='inspect',nargs='?')
    parser.add_argument('--output-root',help='Explicit absolute disposable store path')
    args=parser.parse_args()
    try:
        if args.command=='init-fixture':
            if not args.output_root:
                parser.error('--output-root is required')
            Store(args.output_root,FixtureAuthority({})).initialize()
            result={'initialized':args.output_root,'synthetic':True}
        elif args.output_root:
            result=Store(args.output_root,FixtureAuthority({})).inspect()
        else:
            result={'capabilities':capabilities(),'adapters':UNAVAILABLE,'live_activation':'disabled','default':'inspection; no writes'}
        print(json.dumps(result,sort_keys=True,indent=2))
    except (ContractError,OSError):
        parser.exit(2,'Unavailable or invalid fixture operation; no source content is printed.\n')


if __name__=='__main__':
    main()
