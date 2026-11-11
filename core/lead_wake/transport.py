"""Authenticated identifier-only wake/publication transport, disabled unless wired.

Endpoint identity is operator-pinned. No model text, principal, scope, destination,
or action credentials are accepted from a sender. Recipient re-resolves records.
"""
import hmac
import requests
from urllib.parse import urlsplit
from core.lead_wake.inbox import Conflict, StaleWake
from services.partnership_tasks.outbox import DestinationUnreachable, HarnessRefused

PATH='/internal/lead-return/v1'


def blueprint(*, token, inbox, publication, access=None):
    from flask import Blueprint, request, jsonify
    bp=Blueprint('lead_return',__name__)
    @bp.post(PATH+'/access/check')
    def access_check():
        wanted=token() if callable(token) else token
        if not wanted or not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+wanted):
            return jsonify(error='not_authenticated'),401
        try:
            active=bool(access and access())
        except Exception:
            return jsonify(error='access_unavailable'),503
        return jsonify(active=active)
    @bp.post(PATH+'/<channel>/<action>')
    def receive(channel,action):
        wanted=token() if callable(token) else token
        supplied=request.headers.get('Authorization','')
        if not wanted or not hmac.compare_digest(supplied,'Bearer '+wanted):
            return jsonify(error='not_authenticated'),401
        if request.content_length is None or request.content_length>4096:
            return jsonify(error='invalid_request'),400
        body=request.get_json(silent=True)
        keys={'operation_id','attempt'}
        if action=='deliver':keys|={'payload_digest','task_id','kind'}
        if (action not in ('deliver','lookup') or channel not in ('wake','publication')
                or not isinstance(body,dict) or set(body)!=keys):
            return jsonify(error='invalid_request'),400
        target=inbox if channel=='wake' else publication
        try:
            result=getattr(target,action)(**body)
        except (Conflict,StaleWake,ValueError):
            return jsonify(error='binding_refused'),409
        except Exception:
            return jsonify(error='temporarily_unavailable'),503
        response=jsonify(result)
        response.headers['Cache-Control']='no-store'
        return response
    return bp


class Port:
    def __init__(self, *, read_outbox, endpoints, channel, session=None):
        if channel not in ('wake','publication') or set(endpoints)-{'mc','cos'}:
            raise ValueError('invalid return transport')
        for url,token in endpoints.values():
            parsed=urlsplit(url)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('invalid recipient endpoint')
        self.read_outbox,self.endpoints,self.channel=read_outbox,endpoints,channel
        self.session=session or requests.Session()
        if hasattr(self.session,'trust_env'):self.session.trust_env=False

    def _call(self,action,body):
        row=self.read_outbox(body['operation_id'])
        if not row or row['packet']['lead'] not in self.endpoints:
            raise DestinationUnreachable()
        url,token=self.endpoints[row['packet']['lead']]
        secret=token() if callable(token) else token
        try:
            response=self.session.post(url.rstrip('/')+PATH+'/'+self.channel+'/'+action,
                json=body,headers={'Authorization':'Bearer '+secret},timeout=(3,15),allow_redirects=False)
            if response.status_code==409:raise HarnessRefused('return_binding_refused')
            if response.status_code!=200:raise DestinationUnreachable()
            result=response.json()
            if not isinstance(result,dict):raise ValueError('invalid receipt')
            return result
        except (requests.RequestException,ValueError):
            raise DestinationUnreachable() from None

    def deliver(self,operation_id,payload_digest,*,task_id,kind,attempt):
        return self._call('deliver',dict(operation_id=operation_id,payload_digest=payload_digest,
                                       task_id=task_id,kind=kind,attempt=attempt))

    def lookup(self,operation_id,*,attempt):
        return self._call('lookup',dict(operation_id=operation_id,attempt=attempt))


def configured_ports(env,reader):
    if env.get('PARTNERSHIP_HELPER_RETURN_TRANSPORT')!='1':return None,None
    from services.partnership_tasks.client import read_token
    endpoints={}
    for lead in ('mc','cos'):
        url=env['PARTNERSHIP_'+lead.upper()+'_RETURN_URL']
        path=env['PARTNERSHIP_'+lead.upper()+'_RETURN_TOKEN_FILE']
        endpoints[lead]=(url,lambda p=path:read_token(p))
    read=lambda op:reader.get('outbox',op)
    return (Port(read_outbox=read,endpoints=endpoints,channel='wake'),
            Port(read_outbox=read,endpoints=endpoints,channel='publication'))


class AccessClient:
    """CoS rechecks the pinned portal owner account before disclosure."""
    def __init__(self,url,token):
        self.url,self.token=url.rstrip('/')+PATH+'/access/check',token
        self.session=requests.Session()
        self.session.trust_env=False
    def __call__(self):
        secret=self.token() if callable(self.token) else self.token
        r=self.session.post(self.url,json={},headers={'Authorization':'Bearer '+secret},
                            timeout=(3,5),allow_redirects=False)
        return r.status_code==200 and r.json().get('active') is True


class WakeDestination:
    def __init__(self,inbox,reader):self.inbox,self.reader=inbox,reader
    def _attempt(self,operation_id,attempt):
        from core.lead_wake.publication import Publication
        import re
        if not isinstance(operation_id,str) or not re.fullmatch(r'OB-wake-[0-9a-f]{40}',operation_id):
            raise ValueError('invalid wake id')
        row=self.reader.get('outbox',operation_id)
        if not row or row['operation']!='wake_lead' or row['destination']!='lead_wake':
            raise StaleWake('wake unavailable')
        Publication.attempted(row,attempt)
    def deliver(self,operation_id,payload_digest,*,task_id,kind,attempt):
        self._attempt(operation_id,attempt)
        return self.inbox.deliver(operation_id,payload_digest,task_id=task_id,kind=kind,attempt=attempt)
    def lookup(self,operation_id,*,attempt):
        self._attempt(operation_id,attempt)
        return self.inbox.lookup(operation_id,attempt=attempt)
