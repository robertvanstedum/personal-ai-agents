"""Owner-run provisioning. Writes credentials privately; never prints token values."""
import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, build_opener
from urllib.parse import urlsplit, quote
from roomctl import NoRedirect


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:18880')
    parser.add_argument('--owner-token-file',required=True)
    sub=parser.add_subparsers(dest='command',required=True)
    issue=sub.add_parser('issue');issue.add_argument('--request',required=True);issue.add_argument('--credential-file',required=True)
    sub.add_parser('list')
    revoke=sub.add_parser('revoke');revoke.add_argument('credential_id')
    args=parser.parse_args(); origin=urlsplit(args.url)
    if origin.scheme!='http' or origin.hostname not in {'localhost','127.0.0.1'} or origin.username or origin.password or origin.path not in {'','/'} or origin.query or origin.fragment:
        parser.error('Use the explicit loopback HTTP service')
    owner=Path(args.owner_token_file)
    if owner.is_symlink() or owner.stat().st_mode&0o077 or owner.stat().st_uid!=os.getuid():
        parser.error('Owner token file must be owner-private and not a symlink')
    output=None; fd=None
    if args.command=='issue':
        payload=json.loads(Path(args.request).read_text())
        # Reserve the destination before any remote effect; never overwrite a key.
        output=Path(args.credential_file)
        fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        path='/api/v1/platform/credentials'
    elif args.command=='list':
        payload=None;path='/api/v1/platform/credentials'
    else:
        payload={};path='/api/v1/platform/credentials/'+quote(args.credential_id,safe='')+'/revoke'
    request=Request(args.url.rstrip('/')+path,data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type':'application/json','Authorization':'Bearer '+owner.read_text().strip()})
    try:
        with build_opener(NoRedirect).open(request,timeout=15) as response: result=json.load(response)
        if output:
            token=result.pop('access_token')
            with os.fdopen(fd,'w') as stream:
                stream.write(token+'\n');stream.flush();os.fsync(stream.fileno())
        print(json.dumps(result,indent=2))
    except Exception:
        # A lost issue response may have created a key whose secret is unavailable.
        # No automatic retry or fallback. Owner reconciles credential audit first.
        print('Operation unconfirmed. Do not retry blindly; inspect platform credential audit.',file=__import__('sys').stderr)
        if fd is not None:
            try: os.close(fd)
            except OSError: pass
        raise SystemExit(1)


if __name__=='__main__': main()
