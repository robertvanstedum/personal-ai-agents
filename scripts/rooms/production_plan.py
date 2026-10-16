"""Read-only admission/command plan; NEVER creates state, keys, networks or DBs.
A successful local plan is not production activation or a schema/custody proof.
"""
from __future__ import annotations
import argparse,hashlib,json,os,re,stat
from pathlib import Path
FIELDS={'image','state','portal_key','network','backup_receipt'}
IMAGE=re.compile(r'332704997792\.dkr\.ecr\.us-east-1\.amazonaws\.com/minimoi/portal@sha256:[a-f0-9]{64}\Z')
def require(ok,code):
 if not ok:raise ValueError(code)
def existing(path,*,directory=False):
 require(isinstance(path,str),'path_required');p=Path(path)
 require(p.is_absolute()and str(p)==path and '..'not in p.parts and 'minimoi-staging'not in p.parts and '_working'not in p.parts and not any(c in path for c in ',\n\r\x00'),'production_path_required')
 for part in (p,*p.parents):require(not part.is_symlink(),'symlink_refused')
 s=p.stat();require((stat.S_ISDIR(s.st_mode)if directory else stat.S_ISREG(s.st_mode))and not s.st_mode&0o077 and s.st_uid in {0,os.geteuid()},'private_existing_path_required')
 if not directory:require(s.st_nlink==1,'hardlink_refused')
 return p

def plan(inputs):
 require(isinstance(inputs,dict)and set(inputs)==FIELDS,'exact_input_fields_required')
 require(isinstance(inputs['image'],str)and IMAGE.fullmatch(inputs['image']),'immutable_portal_image_required')
 require(isinstance(inputs['network'],str)and re.fullmatch(r'minimoi-records-production-[a-z0-9-]{1,48}',inputs['network']),'explicit_production_network_required')
 state=existing(inputs['state'],directory=True);key=existing(inputs['portal_key']);backup=existing(inputs['backup_receipt'])
 require(key!=state and state not in key.parents,'separate_key_custody_required')
 # Metadata only: the existing runtime owns exact manifest/key/schema validation.
 for name in ('production-runtime.json','records.sqlite3','owner-key.txt','session-key.txt'):existing(str(state/name))
 env={'MINIMOI_RECORDS_IMAGE':inputs['image'],'MINIMOI_RECORDS_STATE':str(state),'MINIMOI_RECORDS_PORTAL_KEY':str(key),'MINIMOI_RECORDS_NETWORK':inputs['network']}
 return {'format':'records-production-package-plan/1','plan_only':True,'activated':False,'provisioned_here':False,'environment_references':env,'backup_receipt_reference':str(backup),'requires':['verify image code closure/config and architecture offline','verify existing network Internal=true and exact portal membership; no unrelated containers','verify services.records_runtime no-network preflight against prepared production manifest/keys/schema','review portal-only network/key/bridge profile activation delta','owner-approved production activation and signed-in/machine roundtrip acceptance'],
 'runtime_preflight_argv':['docker','run','--rm','--pull=never','--network=none','--read-only','--memory=384m','--memory-swap=384m','--cpus=0.5','--pids-limit=64','--cap-drop=ALL','--security-opt=no-new-privileges:true','--mount','type=bind,src='+str(state)+',dst=/var/lib/minimoi-records,readonly','--mount','type=bind,src='+str(key)+',dst=/run/secrets/rooms-bridge/portal-bridge.key,readonly',*[item for k,v in {'BASE_URL':'https://minimoi.ai','MINIMOI_ROLE':'production','MINIMOI_RECORDS_PROFILE':'production','MINIMOI_RECORDS_PRODUCTION':'1','RECORDS_PORTAL_ENV':'production','RECORDS_ALLOWED_HOSTS':'minimoi-records:18880','RECORDS_PORTAL_KEY_FILE':'/run/secrets/rooms-bridge/portal-bridge.key','PYTHONDONTWRITEBYTECODE':'1','AWS_EC2_METADATA_DISABLED':'true'}.items()for item in ('--env',k+'='+v)],inputs['image'],'python','-m','services.records_runtime','--data-dir','/var/lib/minimoi-records']}

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inputs',required=True);args=parser.parse_args();p=existing(args.inputs)
 require(p.stat().st_size<=16384,'inputs_too_large')
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  first=os.fstat(fd);raw=os.read(fd,16385);last=os.fstat(fd)
  require((first.st_dev,first.st_ino,first.st_size,first.st_mtime_ns)==(last.st_dev,last.st_ino,last.st_size,last.st_mtime_ns)and len(raw)==first.st_size,'inputs_changed')
 finally:os.close(fd)
 result=plan(json.loads(raw));result['input_sha256']=hashlib.sha256(raw).hexdigest();print(json.dumps(result,sort_keys=True))
if __name__=='__main__':main()
