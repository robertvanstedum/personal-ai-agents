"""Launch the pinned MiniMoi relay against the existing loopback OpenClaw.

The portal gets a dedicated caller token; the personal operator token remains
inside this process environment. No native gateway endpoint is exposed publicly.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat


def launch(config, token_file, source, expected_hash, node):
    for path in (config, token_file):
        if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_mode & 0o077:
            raise ValueError('private_regular_files_required')
    doc=json.loads(config.read_text())
    gateway=doc.get('gateway',{})
    if gateway.get('bind')!='loopback' or gateway.get('auth',{}).get('mode')!='token':
        raise ValueError('loopback_token_gateway_required')
    if gateway.get('http',{}).get('endpoints',{}).get('chatCompletions',{}).get('enabled') is not True:
        raise ValueError('native_http_not_enabled')
    if 'mc-agent' not in doc.get('agents',{}).get('entries',{}):
        raise ValueError('mc_not_configured')
    token=gateway['auth']['token']
    caller=token_file.read_text().strip()
    if not isinstance(token,str) or len(token)<16 or len(caller)<32 or token==caller:
        raise ValueError('invalid_credentials')
    if hashlib.sha256(source.read_bytes()).hexdigest()!=expected_hash:
        raise ValueError('relay_changed')
    env=dict(os.environ,MC_RELAY_TOKEN=caller,MC_OPENCLAW_GATEWAY_TOKEN=token,
             MC_RELAY_SOURCE=str(source.resolve()),MC_RELAY_SOURCE_SHA256=expected_hash,
             MC_RELAY_TARGET='http://127.0.0.1:18789',MC_RELAY_PORT='18796')
    wrapper=Path(__file__).with_name('native-relay.mjs')
    os.execve(node,[node,str(wrapper)],env)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--token-file',type=Path,required=True)
    p.add_argument('--relay-source',type=Path,required=True)
    p.add_argument('--source-sha256',required=True)
    p.add_argument('--node',required=True)
    a=p.parse_args()
    try:launch(a.config,a.token_file,a.relay_source,a.source_sha256,a.node)
    except (ValueError,OSError,KeyError):raise SystemExit('MC relay unavailable: configuration or pinned source check failed')
