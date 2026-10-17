"""Host wiring for the reviewed controlled voice foundation; no registration here.

Host guard owns authentication/CSRF/record mode. Host conversation callback checks
ownership and returns its canonical identity. Dispatch must be request-independent;
never synthesize a Flask owner session to call an HTTP endpoint from the worker.
"""
from . import budget as shared_budget
from .controlled_api import blueprint
from .controlled_config import configuration
from .controlled_session import ControlledSession, Journal, SessionRefused


def create_host_blueprint(*, name, domain, guard, conversation, instructions,
                          dispatch, provider_factory, environ, budget=None, canonical_command_match=None, on_transcript=None):
    """Register only behind the host's explicit managed-realtime feature gate.

    `conversation(principal, requested)` must refuse missing/foreign/archived or
    Private conversations. No default owner ID is manufactured from client data.
    Each session gets its own provider/sideband. Existing shared daily budget is
    reused; schema setup is an explicit operator migration, never a web request.
    """
    if domain not in {'cos', 'mc'}:
        raise ValueError('invalid_voice_domain')
    ledger = budget if budget is not None else shared_budget.default_budget()
    journal = Journal(ledger._tx)

    def make_session(principal, requested):
        if not isinstance(principal, str) or not principal:
            raise SessionRefused('Voice owner unavailable')
        canonical = conversation(principal, requested)
        if not isinstance(canonical, str) or not 1 <= len(canonical) <= 160:
            raise SessionRefused('Voice conversation unavailable')
        # Deliberately no transcription allowance/default or floating-point dollars.
        raw = environ.get('MINIMOI_REALTIME_SESSION_ALLOWANCE_MICRO', '')
        if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal():
            raise SessionRefused('Realtime allowance unavailable')
        allowance = int(raw)
        if not 1 <= allowance <= 20_000_000:
            raise SessionRefused('Realtime allowance unavailable')
        journal.check_admission(principal, canonical)
        return ControlledSession(journal=journal, budget=ledger,
            provider=provider_factory(), principal=principal, conversation=canonical,
            allowance_micro=allowance, dispatch=dispatch, domain=domain,
            canonical_command_match=canonical_command_match, on_transcript=on_transcript)

    return blueprint(name=name, guard=guard, build_session=make_session,journal=journal,surface=domain,
                     build_config=lambda principal: configuration(instructions(principal)))
