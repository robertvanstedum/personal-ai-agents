"""Private per-environment work cards and derived projections, never execution grants.

Caller identities/authorization are server-owned inputs, not card/model fields.
No automatic scheduling, tools, provider, Git or HTTP calls. Constructor is pure.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import platform
import subprocess
from uuid import UUID

PREFIX = {'mc':'MC','cos':'COS','claude-code':'CC','codex':'CDX','grok':'GRK','shared':'STD'}
KINDS = {'standing responsibility','rule','expectation','task'}
STATUSES = {'ongoing','open','proposed','closed','retired'}
ID = re.compile(r'(MC|COS|CC|CDX|GRK|STD)-[0-9]{4,8}\Z')
MAX_CARD = 16384
MAX_CARDS = 512
MAX_TOTAL = 2 << 20
REQUIRED = {'id','title','kind','status','owner','opened','closed','filed_by','authorized_by','references'}
OPTIONAL = {'applies_to','revision','supersedes','manual_help'}

class RegisterRefused(ValueError):pass
class RegisterUncertain(RegisterRefused):pass

def need(ok, code):
    if not ok:raise RegisterRefused(code)

def sha(raw):return hashlib.sha256(raw).hexdigest()
def encoded(value):return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
def text(value,limit):
    return isinstance(value,str) and 0<len(value)<=limit and '\x00'not in value

def read(path,limit=MAX_CARD):
    path=Path(path)
    for p in (path,*path.parents):need(not p.is_symlink(),'register_symlink_refused')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        a=os.fstat(fd);need(stat.S_ISREG(a.st_mode)and a.st_nlink==1 and a.st_size<=limit,'register_file_refused')
        raw=os.read(fd,limit+1);b=os.fstat(fd);c=path.lstat()
        identity=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
        need(len(raw)==a.st_size and identity(a)==identity(b)==identity(c),'register_file_changed')
        return raw
    finally:os.close(fd)

def sync(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)

def atomic(path,raw,*,exclusive=False):
    need(len(raw)<=MAX_TOTAL,'register_output_bound')
    fd,tmp=tempfile.mkstemp(prefix='.register-',dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
        if exclusive:
            os.link(tmp,path,follow_symlinks=False)
            os.unlink(tmp)
        else:
            need(not path.is_symlink(),'register_symlink_refused')
            os.replace(tmp,path)
        sync(path.parent)
    finally:
        if os.path.lexists(tmp):os.unlink(tmp)

def parse(raw):
    need(len(raw)<=MAX_CARD,'register_card_bound')
    value=raw.decode('utf8');need(value.startswith('---\n'),'register_frontmatter_missing')
    end=value.find('\n---\n',4);need(end>=0,'register_frontmatter_unclosed')
    # Existing cards use YAML; reject aliases and duplicate fields explicitly.
    import yaml
    class Strict(yaml.SafeLoader):pass
    def mapping(loader,node,deep=False):
        result={}
        for k,v in node.value:
            key=loader.construct_object(k,deep=deep)
            need(isinstance(key,str)and key not in result,'register_duplicate_or_complex_key')
            result[key]=loader.construct_object(v,deep=deep)
        return result
    Strict.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,mapping)
    head=value[4:end]
    try:
        need(not any(isinstance(t,(yaml.tokens.AliasToken,yaml.tokens.AnchorToken))for t in yaml.scan(head)),'register_yaml_alias_refused')
        meta=yaml.load(head,Loader=Strict)
    except yaml.YAMLError:raise RegisterRefused('register_yaml_invalid')from None
    if isinstance(meta,dict):
        for k in ('opened','closed'):
            if type(meta.get(k))is date:meta[k]=meta[k].isoformat()
    return meta,value[end+5:].lstrip('\n')

def dump(meta,body):
    # JSON is a YAML subset; stable bytes, no dependency on serializer versions.
    return b'---\n'+encoded(meta)+b'---\n\n'+body.encode()

def writer_domain():
    """Actual kernel boot identity; no configurable hostname/env override."""
    system=platform.system()
    if system=='Linux':
        with open('/proc/sys/kernel/random/boot_id','rb')as f:raw=f.read(129)
        need(len(raw)<=128,'register_writer_domain_invalid');raw=raw.decode().strip()
    elif system=='Darwin':
        try:raw=subprocess.run(['/usr/sbin/sysctl','-n','kern.bootsessionuuid'],capture_output=True,check=True,timeout=2).stdout.decode().strip()
        except (OSError,subprocess.SubprocessError,UnicodeError):raise RegisterRefused('register_writer_domain_unavailable')from None
    else:raise RegisterRefused('register_writer_domain_unsupported')
    try:identity=str(UUID(raw))
    except (ValueError,TypeError):raise RegisterRefused('register_writer_domain_invalid')from None
    return system+':'+identity

class RegisterStore:
    def __init__(self,root,*,environment,owner_authorizer=None,classes=None):
        self.root=Path(root);need(self.root.is_absolute()and '..'not in self.root.parts,'absolute_register_root_required')
        need(environment in {'dev','production'},'register_environment_required')
        self.environment=environment
        self.authorizer=owner_authorizer
        # Class membership is server configuration, never derived from cards.
        self.classes=classes or {'build-agents':('mc','claude-code','codex')}
        need(all(text(k,64)and set(v)<=set(PREFIX)-{'shared'}for k,v in self.classes.items()),'register_class_invalid')

    def initialize(self):
        need(not self.root.exists()and not self.root.is_symlink(),'register_root_exists')
        for p in self.root.parents:need(not p.is_symlink(),'register_symlink_refused')
        domain=writer_domain()
        self.root.mkdir(mode=0o700)
        atomic(self.root/'scope.json',encoded({'format':'minimoi.work-registers/1','environment':self.environment,'writer_domain':domain}))
        atomic(self.root/'.lock',b'')

    def _dir(self,path):
        path=Path(path);need(path==self.root or self.root in path.parents,'register_directory_outside_root')
        for part in reversed((path,*path.parents)):
            if part!=self.root and self.root not in part.parents:continue
            try:part.mkdir(mode=0o700)
            except FileExistsError:pass
            st=part.lstat();need(stat.S_ISDIR(st.st_mode)and not part.is_symlink()and st.st_uid==os.geteuid()and stat.S_IMODE(st.st_mode)==0o700,'private_register_directory_required')

    def _root(self,*,write=False):
        s=self.root.lstat();need(stat.S_ISDIR(s.st_mode)and not self.root.is_symlink()and s.st_uid==os.geteuid()and stat.S_IMODE(s.st_mode)==0o700,'private_register_root_required')
        scope=json.loads(read(self.root/'scope.json'))
        need(type(scope)is dict and set(scope)=={'format','environment','writer_domain'}and scope['format']=='minimoi.work-registers/1'and scope['environment']==self.environment and text(scope['writer_domain'],64),'register_scope_changed')
        if write:need(scope['writer_domain']==writer_domain(),'register_writer_domain_changed')

    @contextmanager
    def _lock(self,*,write=True):
        self._root(write=write);fd=os.open(self.root/'.lock',(os.O_RDWR if write else os.O_RDONLY)|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            s=os.fstat(fd);need(stat.S_ISREG(s.st_mode)and s.st_uid==os.geteuid()and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600,'register_lock_refused')
            try:fcntl.flock(fd,(fcntl.LOCK_EX if write else fcntl.LOCK_SH)|fcntl.LOCK_NB)
            except BlockingIOError:raise RegisterRefused('register_busy')from None
            yield
        finally:os.close(fd)

    def _validate(self,meta,body,register):
        need(type(meta)is dict and REQUIRED<=set(meta)<=REQUIRED|OPTIONAL,'register_fields_invalid')
        need(register in PREFIX and text(meta['id'],24)and ID.fullmatch(meta['id'])and meta['id'].split('-')[0]==PREFIX[register],'register_id_invalid')
        need(meta['owner']==PREFIX[register]and meta['kind']in KINDS and meta['status']in STATUSES,'register_lifecycle_invalid')
        need(text(meta['title'],200)and not any(ord(c)<32 for c in meta['title'])and text(meta['filed_by'],80)and text(meta['authorized_by'],500),'register_attribution_missing')
        need(isinstance(body,str)and 0<len(body)<=12000,'register_body_invalid')
        try:date.fromisoformat(meta['opened']);closed=date.fromisoformat(meta['closed'])if meta['closed']else None
        except (ValueError,TypeError):raise RegisterRefused('register_date_invalid')from None
        need((meta['status']in {'closed','retired'})==bool(closed),'register_closed_date_required')
        need(not closed or closed>=date.fromisoformat(meta['opened']),'register_date_order')
        need(type(meta.get('revision',1))is int and meta.get('revision',1)>0,'register_revision_invalid')
        need(type(meta.get('references'))is list and len(meta['references'])<=24 and all(text(v,1000)for v in meta['references']),'register_references_invalid')
        applies=meta.get('applies_to',register)
        applies=[applies]if isinstance(applies,str)else applies
        if type(applies)is list:
            aliases={v.casefold():k for k,v in PREFIX.items()}
            applies=[aliases.get(v.casefold(),v.casefold())if isinstance(v,str)else v for v in applies]
        need(type(applies)is list and 0<len(applies)<=12 and all(v in {*PREFIX,'robert','everyone',*self.classes}for v in applies),'register_scope_invalid')
        need('supersedes'not in meta or text(meta['supersedes'],24)and ID.fullmatch(meta['supersedes']),'register_supersession_invalid')
        need('manual_help'not in meta or type(meta['manual_help'])is bool,'register_manual_help_invalid')
        return {**meta,'revision':meta.get('revision',1),'applies_to':applies}

    def _cards(self):
        self._root();cards=[];total=0
        for register in PREFIX:
            folder=self.root/register/'entries'
            if not folder.exists():continue
            need(folder.is_dir()and not folder.is_symlink(),'register_entries_invalid')
            for path in sorted(folder.iterdir()):
                need(path.suffix=='.md'and len(cards)<MAX_CARDS,'register_card_count_or_type')
                raw=read(path);total+=len(raw);need(total<=MAX_TOTAL,'register_total_bound')
                meta,body=parse(raw);meta=self._validate(meta,body,register)
                need(path.name.startswith(meta['id']+'-'),'register_filename_mismatch')
                cards.append({'meta':meta,'body':body,'path':path,'sha256':sha(raw),'bytes':len(raw)})
        return self._relationships(cards)

    def _relationships(self,cards):
        ids={};titles={}
        for c in cards:
            m=c['meta'];need(m['id']not in ids,'register_duplicate_id');ids[m['id']]=c
            key=(m['owner'],' '.join(m['title'].casefold().split()));titles.setdefault(key,[]).append(m)
        for c in cards:
            m=c['meta'];seen={m['id']};prior=m.get('supersedes')
            while prior:
                need(prior in ids and prior not in seen,'register_supersession_missing_or_cycle');seen.add(prior);prior=ids[prior]['meta'].get('supersedes')
        for group in titles.values():
            if len(group)>1:
                links={m.get('supersedes')for m in group};need(all(m['id']in links or m.get('supersedes')in {x['id']for x in group}for m in group),'register_duplicate_title')
        return sorted(cards,key=lambda c:c['meta']['id'])

    def _covers(self,card,agent):
        scopes=card['meta']['applies_to']
        return agent in scopes or 'everyone'in scopes or any(agent in self.classes.get(v,())for v in scopes)

    def projection(self,agent,*,kind=None,status=None,entry_id=None):
        need(agent in set(PREFIX)|{'robert'},'register_agent_invalid')
        need(kind is None or kind in KINDS,'register_kind_invalid')
        need(status is None or status in STATUSES,'register_status_invalid')
        with self._lock(write=False):return self._projection(self._cards(),agent,kind=kind,status=status,entry_id=entry_id)

    def _projection(self,cards,agent,*,kind=None,status=None,entry_id=None):
        selected=[c for c in cards if (c['meta']['owner']==PREFIX.get(agent)or c['meta']['owner']=='STD'and self._covers(c,agent))and (not kind or c['meta']['kind']==kind)and (not status or c['meta']['status']==status)and (not entry_id or c['meta']['id']==entry_id)]
        return {'format':'minimoi.work-register-projection/1','environment':self.environment,'agent':agent,'authority':'Records only; no entry grants execution rights or expands Private/source permissions.','entries':[{'card':str(c['path']),'sha256':c['sha256'],**c['meta'],'body':c['body']}for c in selected]}

    def _outputs(self,cards):
        outputs=[]
        for agent in PREFIX:
            root=self.root/agent
            if not root.exists()and not any(c['meta']['owner']=='STD'and self._covers(c,agent)for c in cards):continue
            value=self._projection(cards,agent)
            markdown='# '+PREFIX[agent]+' work register\n\nGenerated from entry cards; do not edit. '+value['authority']+'\n\n'
            for c in value['entries']:markdown+=f"## {c['id']} — {c['title']}\n\n{c['kind']} · {c['status']} · revision {c['revision']}\nCard: {c['card']}\n\n{c['body']}\n\n"
            for path,raw in ((root/'register.json',encoded(value)),(root/'REGISTER.md',markdown.encode())):
                need(len(raw)<=MAX_TOTAL,'register_output_bound');outputs.append((path,raw))
        return outputs

    def _publish(self,cards):
        # Precompute and validate EVERY derived output before publishing any.
        outputs=self._outputs(cards)
        for path,raw in outputs:
            self._dir(path.parent);atomic(path,raw)

    def regenerate(self):
        with self._lock():cards=self._cards();self._publish(cards)

    def _receipt(self,prior,*,payload=None):
        need(type(prior)is dict and isinstance(prior.get('id'),str)and ID.fullmatch(prior['id']),'register_receipt_invalid')
        register=next(k for k,v in PREFIX.items()if v==prior['id'].split('-')[0])
        revision=prior.get('revision');digest=prior.get('sha256')
        need(type(revision)is int and revision>0 and isinstance(digest,str)and re.fullmatch('[a-f0-9]{64}',digest),'register_receipt_invalid')
        expected=self.root/register/'history'/prior['id']/(str(revision).zfill(8)+'-'+digest+'.md')
        path=Path(prior.get('path',''))
        need(prior.get('history_path')==str(expected)and path.parent==self.root/register/'entries'and path.name.startswith(prior['id']+'-')and path.suffix=='.md'and prior.get('environment')==self.environment,'register_receipt_path_invalid')
        if payload is not None:need(prior.get('request_sha256')==payload,'register_idempotency_conflict')
        need(sha(read(expected))==digest,'register_history_changed')
        return {**prior,'repeated':True}

    def operation(self,actor,request_id):
        need(actor in set(PREFIX)-{'shared'}and text(request_id,160),'register_actor_or_request_invalid')
        self._root();path=self.root/'receipts'/(sha((actor+'\0'+request_id).encode())+'.json')
        if not path.exists():return None
        return self._receipt(json.loads(read(path)))

    def save(self,actor,register,meta,body,*,request_id,expected_revision=None,entry_id=None):
        need(actor in set(PREFIX)-{'shared'} and register in PREFIX and text(request_id,160),'register_actor_or_request_invalid')
        need(expected_revision is None or type(expected_revision)is int and expected_revision>0,'register_revision_invalid')
        need(entry_id is None or isinstance(entry_id,str)and ID.fullmatch(entry_id),'register_id_invalid')
        need(type(meta)is dict,'register_fields_invalid')
        preflight={**meta,'id':entry_id or PREFIX[register]+'-0000','owner':PREFIX[register],'filed_by':actor,'revision':1 if expected_revision is None else expected_revision+1}
        self._validate(preflight,body,register)
        # A card's authorized_by, body or references can NEVER authorize this call.
        owner_ok=callable(self.authorizer)and self.authorizer(actor,register) is True
        need(actor==register or owner_ok or (meta.get('status')=='proposed'and entry_id is None and expected_revision is None),'register_other_owner_requires_new_proposal')
        request=sha((actor+'\0'+request_id).encode());payload=sha(encoded([actor,register,meta,body,expected_revision,entry_id]))
        with self._lock():
            receipts=self.root/'receipts';self._dir(receipts);receipt_path=receipts/(request+'.json')
            if receipt_path.exists():
                prior=json.loads(read(receipt_path));need(prior['request_sha256']==payload,'register_idempotency_conflict')
                return self._receipt(prior,payload=payload)
            pending=self.root/'attempts';self._dir(pending)
            need(not(pending/(request+'.json')).exists(),'register_prior_publication_uncertain')
            cards=self._cards();existing=next((c for c in cards if c['meta']['id']==entry_id),None)
            if entry_id:need(existing is not None and existing['meta']['owner']==PREFIX[register]and expected_revision==existing['meta']['revision'],'register_stale_revision')
            else:need(expected_revision is None,'register_create_revision_invalid')
            folder=self.root/register;self._dir(folder);entries=folder/'entries';self._dir(entries)
            if existing:identifier=entry_id;revision=expected_revision+1;path=existing['path']
            else:
                counter=folder/'sequence.json';highest=max([int(c['meta']['id'].split('-')[1])for c in cards if c['meta']['owner']==PREFIX[register]]or[0])
                if counter.exists():
                    saved=json.loads(read(counter));need(set(saved)=={'last'}and type(saved['last'])is int and saved['last']>=0,'register_sequence_invalid');highest=max(highest,saved['last'])
                need(type(highest)is int and highest<99999999,'register_sequence_invalid');number=highest+1
                identifier=PREFIX[register]+'-'+str(number).zfill(4);revision=1
                slug=re.sub(r'[^a-z0-9]+','-',str(meta.get('title','entry')).casefold()).strip('-')[:70]or'entry';path=entries/(identifier+'-'+slug+'.md')
            data={**meta,'id':identifier,'owner':PREFIX[register],'filed_by':actor,'revision':revision}
            validated=self._validate(data,body,register)
            if any(c['meta']['id']!=identifier and c['meta']['owner']==PREFIX[register]and c['meta']['title'].casefold().strip()==validated['title'].casefold().strip()and c['meta']['id']!=validated.get('supersedes')for c in cards):raise RegisterRefused('register_duplicate_title')
            if validated.get('supersedes'):need(any(c['meta']['id']==validated['supersedes']and c['meta']['status']in {'closed','retired'}for c in cards),'register_superseded_card_not_retired')
            raw=dump(validated,body);need(len(raw)<=MAX_CARD,'register_card_bound')
            prospective=self._relationships([c for c in cards if c['meta']['id']!=identifier]+[{'meta':validated,'body':body,'path':path,'sha256':sha(raw),'bytes':len(raw)}])
            need(len(prospective)<=MAX_CARDS and sum(c['bytes']for c in prospective)<=MAX_TOTAL,'register_prospective_capacity')
            self._outputs(prospective)
            if not existing:atomic(counter,encoded({'last':number}))
            history=folder/'history'/identifier;self._dir(history)
            if existing:
                oldpath=history/(str(existing['meta']['revision']).zfill(8)+'-'+existing['sha256']+'.md')
                if oldpath.exists():need(read(oldpath)==read(existing['path']),'register_history_changed')
                else:atomic(oldpath,read(existing['path']),exclusive=True)
            history_path=history/(str(revision).zfill(8)+'-'+sha(raw)+'.md')
            record={'id':identifier,'revision':revision,'path':str(path),'history_path':str(history_path),'sha256':sha(raw),'request_sha256':payload,'environment':self.environment,'repeated':False,'authority':'record_only','body_sha256':sha(body.encode())}
            atomic(pending/(request+'.json'),encoded(record),exclusive=True)
            try:
                atomic(history_path,raw,exclusive=True);atomic(path,raw)
                self._publish(self._cards())
                atomic(receipt_path,encoded(record),exclusive=True)
            except Exception:raise RegisterUncertain('register_publication_uncertain_inspect_card_before_retry')from None
            return record
