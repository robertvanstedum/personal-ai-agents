from pathlib import Path
import hashlib,json,os,subprocess
import pytest
HERE=Path(__file__).parent
REPO=Path(__file__).resolve().parents[1]
DOCS=REPO/'planning-studio/initiatives/INIT-2026-0007-interaction-vision/documents'

def build(tmp_path,tamper=False):
    docs=tmp_path/'docs';docs.mkdir();lock=json.loads((REPO/'docker/cos-agent-a/mc-role-lock.json').read_bytes())
    for name in lock['sources']:(docs/name).write_bytes((DOCS/name).read_bytes())
    if tamper:(docs/'MASTER_CRAFTSMAN_PERSONA.md').write_bytes(b'changed')
    (tmp_path/'lock.json').write_text(json.dumps(lock))
    out=tmp_path/'AGENTS.md';receipt=tmp_path/'ROLE_LOAD.json'
    r=subprocess.run(['node',str(REPO/'docker/cos-agent-a/build-mc-role.mjs'),str(REPO/'docker/cos-agent-a/AGENTS.md'),str(docs),str(tmp_path/'lock.json'),str(out),str(receipt)],capture_output=True,timeout=5)
    return r,out,receipt,lock

def test_exact_canonical_hash_pointer_only_operations_preserved(tmp_path):
    r,out,receipt,lock=build(tmp_path);assert r.returncode==0
    text=out.read_text();assert 'SOUL.md owns standing personality'in text
    assert lock['sources']['MASTER_CRAFTSMAN_PERSONA.md']in text
    assert (DOCS/'MASTER_CRAFTSMAN_PERSONA.md').read_text()not in text
    for name in ('MASTER_CRAFTSMAN_JOB_DESCRIPTION.md','MASTER_CRAFTSMAN_WORKING_AGREEMENT_2026-10-12.md'):assert (DOCS/name).read_text()in text
    assert json.loads(receipt.read_bytes())['sources']==lock['sources']

def test_changed_canonical_refuses_even_when_only_reference_injected(tmp_path):
    r,out,receipt,lock=build(tmp_path,True);assert r.returncode!=0 and not out.exists()

def test_actual_role_and_handbook_combined_budget(tmp_path):
    r,out,receipt,lock=build(tmp_path);assert r.returncode==0
    final=tmp_path/'FINAL_AGENTS.md'
    r=subprocess.run(['node',str(REPO/'docker/cos-agent-a/build-mc-handbook.mjs'),str(out),str(REPO/'services/mc_operations/handbook'),str(final),str(receipt)],capture_output=True,timeout=5)
    assert r.returncode==0
    assert len(final.read_text())<=32000
    assert json.loads(receipt.read_bytes())['policy_chars']==len(final.read_text())


from pathlib import Path
import hashlib,json,subprocess
HERE=Path(__file__).parent
REPO=Path(__file__).resolve().parents[1]
DOCS=REPO/'planning-studio/initiatives/INIT-2026-0007-interaction-vision/documents'
def seed(tmp_path,tamper=False):
    src=tmp_path/'persona';src.write_bytes((DOCS/'MASTER_CRAFTSMAN_PERSONA.md').read_bytes()+(b'changed' if tamper else b''))
    output=tmp_path/'SOUL.md';output.write_bytes((REPO/'docker/cos-agent-a/SOUL.md').read_bytes())
    before=output.read_bytes()
    r=subprocess.run(['node',str(REPO/'docker/cos-agent-a/build-mc-soul.mjs'),str(src),str(REPO/'docker/cos-agent-a/mc-role-lock.json'),str(output)],capture_output=True,timeout=5)
    return r,output,before

def test_fresh_seed_exact_adoption_projection(tmp_path):
    r,out,before=seed(tmp_path);assert r.returncode==0,r.stderr
    raw=out.read_bytes();assert raw.startswith(before)
    start=raw.index(b'\nSupersedes the Scottish voice')+1;end=raw.index(b'\n<!-- MINIMOI_PARTNER_PERSONA_END')
    assert hashlib.sha256(raw[start:end]).hexdigest()=='dfa60e72b7158fcf303fa4f5fcf97386031cf2f46d262421127cff2f50420efc'
    assert len(raw.decode())<32000

def test_canonical_mismatch_leaves_seed_untouched(tmp_path):
    r,out,before=seed(tmp_path,True);assert r.returncode!=0 and out.read_bytes()==before

def test_existing_adaptations_preserved_by_actual_startup_script(tmp_path):
    # Execute actual shell loader with only pinned /opt paths redirected to synthetic fixtures.
    sh=(REPO/'docker/cos-agent-a/apply-config.sh').read_text()
    pinned=tmp_path/'pinned';pinned.mkdir();(pinned/'openclaw.json').write_text('{}');(pinned/'AGENTS.md').write_text('reviewed operations')
    sh=sh.replace('/opt/minimoi/cos-agent-a/',str(pinned)+'/')
    script=tmp_path/'apply.sh';script.write_text(sh)
    work=tmp_path/'workspace';work.mkdir();config=tmp_path/'openclaw.json'
    originals={'SOUL.md':b'Owner adapted soul\x0a','MEMORY.md':b'memory','USER.md':b'preferences'}
    for name,data in originals.items():(work/name).write_bytes(data)
    import os
    # Compose always provides a gateway key (master or CoS's own); without one the start script refuses.
    env={**os.environ,'OPENCLAW_CONFIG_PATH':str(config),'OPENCLAW_AGENT_POLICY_PATH':str(work/'AGENTS.md'),
         'MINIMOI_MODEL_GATEWAY_KEY':'sk-synthetic-master-0000000000','COS_GATEWAY_KEY_FILE':str(tmp_path/'no-cos-key')}
    for _ in range(2):subprocess.run(['/bin/sh',str(script),'/usr/bin/true'],env=env,check=True,capture_output=True,timeout=5)
    for name,data in originals.items():assert (work/name).read_bytes()==data
    docker=(REPO/'docker/Dockerfile.cos-agent-a').read_text()
    assert 'RUN node /opt/minimoi/cos-agent-a/build-mc-soul.mjs' in docker
    assert 'build-mc-soul' not in sh
