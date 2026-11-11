"""Opt-in CoS return wiring; owner access stays authoritative at the portal."""


def owner_accounts(environ):
    """The owner's principal plus the portal account ids CoS records his turns under.

    The task service names the owner LEAD_RETURN_PRINCIPAL ("robert"). CoS binds an
    owner turn to the signed portal subject, which is his auth.users id when he has
    one. LEAD_RETURN_OWNER_ACCOUNTS (comma separated) lists those ids. Any other
    account is not the owner here and gets no helper return.
    """
    principal=environ['LEAD_RETURN_PRINCIPAL']
    extra=[a.strip() for a in environ.get('LEAD_RETURN_OWNER_ACCOUNTS','').split(',') if a.strip()]
    return (principal,*[a for a in extra if a!=principal])


def lead_principal(account,environ):
    """The task service principal for a CoS binding's account, or the account unchanged."""
    return environ['LEAD_RETURN_PRINCIPAL'] if account in owner_accounts(environ) else account

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
        settled=lambda source:helper_review_settled(bindings(),source,owner_accounts(environ)))
    return attach(app,environ=environ,lead='cos',host=host,access=active)
