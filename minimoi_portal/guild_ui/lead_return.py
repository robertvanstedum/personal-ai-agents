"""Opt-in MC helper return wiring using real owner, floor and admission stores."""
def install(app,services,environ):
    if environ.get('MC_HELPER_RETURN')!='1':return None
    if environ.get('MINIMOI_MC_TURN_LEDGER')!='1' or services.turn_ledger is None:
        raise ValueError('MC shared admission lane required')
    from minimoi_portal import auth
    from .conversations import conversations_of
    from core.lead_wake.hosts import MCHost
    from core.lead_wake.wiring import install as attach
    principal=environ['LEAD_RETURN_PRINCIPAL']
    account=environ['LEAD_RETURN_OWNER_ACCOUNT']
    def active():
        return any(u.get('username')==account and u.get('tier')=='owner' for u in auth.load_users())
    host=MCHost(conversations=conversations_of(services),floor=services.floor,ledger=services.turn_ledger,
        principal=principal,environment=environ['PARTNERSHIP_ENVIRONMENT'],access=lambda p:p==principal and active(),
        run_end=getattr(services.mc,'run_end_receipt',None))
    return attach(app,environ=environ,lead='mc',host=host,access=active)
