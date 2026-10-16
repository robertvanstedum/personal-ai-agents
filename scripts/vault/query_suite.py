"""Operator-selected saved-suite runner. Default is offline validation/plan.
Real questions/results never printed. No schedule, service or graph activation.
CLI execution is one operator attestation of exact bytes + a reference;
it DOES NOT verify that Robert confirmed the gold questions. That stays pending.
The host library can separately verify a genuine owner confirmation receipt;
it is not a suite boolean and does not create or change the Ask caller's grant.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import stat
import sys
import urllib.parse
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from core.memory_ask import consumer,runner


def local_source_version():
    # Exact local runner/retrieval implementation, including uncommitted bytes.
    # This identifies this process, never the separately deployed Ask server.
    from scripts.vault.runtime_install import runtime_files
    repo = Path(__file__).resolve().parents[2]
    hashes = {str(p.relative_to(repo)): runner.sha(p.read_bytes()) for p in runtime_files(repo)}
    return runner.sha(runner.encoded(hashes))


def read_suite(path,*,private=False):
    if private:
        directory=runner.private_root(Path(path).absolute().parent);os.close(directory)
    f=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        st=os.fstat(f)
        if not stat.S_ISREG(st.st_mode) or st.st_size>runner.MAX_SUITE:raise runner.Refused('suite_file')
        if private and (st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode) not in (0o600,0o400)):raise runner.Refused('suite_not_private')
        data=os.read(f,runner.MAX_SUITE+1)
        if len(data)>runner.MAX_SUITE:raise runner.Refused('suite_size')
        return data
    finally:os.close(f)


def operator_profile(name,environ):
    # Only operator-selected existing endpoint/token configuration. The suite
    # cannot select this configuration or introduce a URL/credential/grant.
    config=consumer.Config.from_env(environ)
    url=consumer.endpoint(config)
    if url is None:raise runner.Refused('bad_config')
    host=urllib.parse.urlsplit(url).hostname
    local=consumer._loopback(host)
    if name=='local-curated' and not local:raise runner.Refused('local_profile_requires_loopback')
    if name not in ('local-curated','approved-remote-curated'):raise runner.Refused('unknown_profile')
    return runner.Profile(name,config,local_only_allowed=name=='local-curated',
                          permissions_fingerprint='unknown',
                          metadata={'retrieval':'existing_curated_ask',
                                    'local_source_sha256':local_source_version(),
                                    'ask_server_source_version':None,
                                    'prompt_version':None,'profile_binding':'operator_selected'})


def main(argv=None,*,environ=None,execute=runner.run):
    ap=argparse.ArgumentParser()
    ap.add_argument('operation',choices=('plan','run','compare'),nargs='?',default='plan')
    ap.add_argument('--suite');ap.add_argument('--root');ap.add_argument('--first');ap.add_argument('--second')
    ap.add_argument('--profile',choices=('local-curated','approved-remote-curated'))
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--attested-suite-sha256');ap.add_argument('--attested-confirmation-reference')
    args=ap.parse_args(argv)
    try:
        if args.operation=='compare':
            if not all((args.root,args.first,args.second)):raise runner.Refused('compare_arguments')
            result=runner.compare(runner.load_run(args.root,args.first),runner.load_run(args.root,args.second))
        else:
            if not args.suite:raise runner.Refused('suite_required')
            raw=read_suite(args.suite,private=args.operation=='run' and args.execute);suite=runner.validate(raw)
            if args.operation!='run' or not args.execute:
                rows=runner.plan(suite)
                result={'operation':'offline_plan','suite_hash':runner.sha(raw),'cases':len(rows),'unsupported':sum(bool(r['unsupported']) for r in rows),'live_calls':0}
            else:
                if not all((args.root,args.profile,args.attested_suite_sha256,args.attested_confirmation_reference)):raise runner.Refused('operator_confirmation_required')
                profile=operator_profile(args.profile,os.environ if environ is None else environ)
                result=execute(raw,profile=profile,root=args.root,confirmation_status='operator_attested_not_gold_verified',
                    verify_confirmation=lambda h,ref,p:h==args.attested_suite_sha256 and ref==args.attested_confirmation_reference and p==args.profile)
        print(json.dumps(result,sort_keys=True));return 0
    except Exception as exc:
        # No raw exception, path, query, response, token or source passage.
        print(json.dumps({'error':str(exc) if isinstance(exc,runner.Refused) and str(exc) in {'operator_confirmation_required','owner_confirmation_required','disk_low','bad_config','suite_required','compare_arguments','local_profile_requires_loopback'} else 'runner_refused'}));return 2

if __name__=='__main__':raise SystemExit(main())
