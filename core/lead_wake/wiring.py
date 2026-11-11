"""Explicit opt-in runtime wiring shared by both leads. Disabled by default.

Deployment must mount the helpers' SAME spend directories and approved charters,
provide a separate commit-only token, a bounded model route, and current access.
Missing configuration refuses startup rather than silently weakening a gate.
"""
import json
import threading
from pathlib import Path
from core.lead_wake.inbox import Inbox
from core.lead_wake.publication import Publication
from core.lead_wake.runtime import Runtime
from core.lead_wake.review import GatewayReview,Route
from core.lead_wake.transport import blueprint,WakeDestination
from services.partnership_tasks.client import TaskClient,read_token
from services.partnership_tasks.charters import load_if_approved
from services.helper_seats.spend import Ledger


def install(app,*,environ,lead,host,access,start=True):
    env=environ
    if env.get(lead.upper()+'_HELPER_RETURN')!='1':return None
    principal=env['LEAD_RETURN_PRINCIPAL']
    environment=env['PARTNERSHIP_ENVIRONMENT']
    root=Path(env['LEAD_RETURN_STATE_DIR'])
    if not root.is_absolute():raise ValueError('absolute persistent return state required')
    charters=load_if_approved(env)
    if charters is None:raise ValueError('approved charters required')
    states=json.loads(env['LEAD_RETURN_HELPER_STATE_DIRS'])
    def ledger_for(charter):
        path=Path(states[charter.seat])
        if not path.is_absolute() or not (path/'spend').is_dir():
            raise ValueError('existing shared helper spend mount required')
        return Ledger(path/'spend',daily_cap=charter.daily_budget_usd)
    for charter in charters._by_seat.values():
        if lead in charter.leads:ledger_for(charter)
    reader=TaskClient(env['PARTNERSHIP_TASKS_URL'],lambda:read_token(env['LEAD_RETURN_READ_TOKEN_FILE']))
    committer=TaskClient(env['PARTNERSHIP_TASKS_URL'],lambda:read_token(env['LEAD_RETURN_COMMIT_TOKEN_FILE']))
    resolve=lambda op:reader.resolve_wake(op,principal=principal,lead=lead,environment=environment)
    inbox=Inbox(root/'inbox.sqlite',resolve)
    reviewer=GatewayReview(url=env['LEAD_RETURN_GATEWAY_URL'],key=read_token(env['LEAD_RETURN_GATEWAY_KEY_FILE']),
        route=Route(env['LEAD_RETURN_ROUTE_ID'],env['LEAD_RETURN_MODEL'],env['LEAD_RETURN_EFFORT']))
    runtime=Runtime(inbox=inbox,reader=reader,committer=committer,principal=principal,lead=lead,
        environment=environment,host=host,reviewer=reviewer,charters=charters,ledger_for=ledger_for,
        review_bound=float(env['LEAD_RETURN_CALL_BOUND_USD']),review_reserve=float(env['HELPER_LEAD_REVIEW_RESERVE_USD']))
    publication=Publication(journal=inbox,reader=reader,host=host,principal=principal,lead=lead,environment=environment)
    app.register_blueprint(blueprint(token=lambda:read_token(env['LEAD_RETURN_INGRESS_TOKEN_FILE']),
                                    inbox=WakeDestination(inbox,reader),publication=publication,access=access))
    stop=threading.Event()
    def consume():
        while not stop.is_set():
            with app.app_context():
                try:runtime.pass_once()
                except Exception:app.logger.warning('helper return pass unavailable')
            stop.wait(5)
    app.extensions['lead_return']={'runtime':runtime,'publication':publication,'stop':stop}
    if start:threading.Thread(target=consume,name=lead+'-helper-return',daemon=True).start()
    return runtime
