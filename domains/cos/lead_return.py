"""Opt-in CoS return wiring; owner access stays authoritative at the portal."""
def install(app,history,environ,bindings):
    if environ.get('COS_HELPER_RETURN')!='1':return None
    from core.lead_wake.hosts import CoSHost
    from domains.cos.binding import helper_review_settled
    from core.lead_wake.lane import configured_cos_lane
    from core.lead_wake.transport import AccessClient
    from core.lead_wake.wiring import install as attach
    from services.partnership_tasks.client import read_token
    principal=environ['LEAD_RETURN_PRINCIPAL']
    active=AccessClient(environ['LEAD_RETURN_OWNER_ACCESS_URL'],
                       lambda:read_token(environ['LEAD_RETURN_OWNER_ACCESS_TOKEN_FILE']))
    host=CoSHost(history=history,lane=configured_cos_lane(environ),principal=principal,
        environment=environ['PARTNERSHIP_ENVIRONMENT'],access=lambda p:p==principal and active(),
        settled=lambda source:helper_review_settled(bindings(),source,principal))
    return attach(app,environ=environ,lead='cos',host=host,access=active)
