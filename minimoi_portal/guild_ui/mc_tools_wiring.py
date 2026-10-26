"""MC's tool path in the portal (docs/design/mc_tool_path_design_2026-10-21.md). Off unless the mount's
settings have MINIMOI_MC_TOOLS=1 with MINIMOI_MC_TOOLS_ENV=dev|prod, MINIMOI_MC_TOOLS_PRINCIPAL, the
executor credential's SHA-256 (MINIMOI_MC_TOOLS_TOKEN_SHA256) and DATABASE_URL, on the turn ledger.

Three pieces:
* ``binder``: at dispatch of a ledger-claimed owner turn, record the per-turn tool binding;
* ``attach``: start the internal tool listener on its own port (MINIMOI_MC_TOOLS_PORT, default 5011);
* ``proposals``: the owner's yes / no for MC's proposals (data-repair tickets, delegation, invites).

Guest rejects follow MINIMOI_MC_GUEST_ACTIONS=1. GitHub issue reads (public, no credential) are on with the tools
unless MINIMOI_MC_TOOLS_GITHUB=0. Issue comments (MINIMOI_MC_TOOLS_COMMENTS=1) and data-repair tickets
(MINIMOI_MC_TOOLS_TICKETS=1) need MINIMOI_MC_ISSUES_TOKEN_FILE: a private file holding a GitHub token that can only
write issues in MINIMOI_GITHUB_REPO. Without it MC is told they are off.
"""
from __future__ import annotations

import logging
import os
import uuid

FLAG = "MINIMOI_MC_TOOLS"
_log = logging.getLogger("guild_ui.mc_tools")


def settings(environ) -> dict | None:
    environ = environ or {}
    if environ.get(FLAG) != "1" or environ.get("MINIMOI_MC_TOOLS_ENV") not in ("dev", "prod"):
        return None
    principal = (environ.get("MINIMOI_MC_TOOLS_PRINCIPAL") or "").strip()
    dsn = os.environ.get("DATABASE_URL")
    if not principal or not dsn or environ.get("MINIMOI_MC_TURN_LEDGER") != "1":
        return None
    agents = tuple(a for a in (environ.get("MINIMOI_MC_TOOLS_AGENTS") or "mc-chat").split(",") if a)
    return {"environment": environ["MINIMOI_MC_TOOLS_ENV"], "principal": principal, "dsn": dsn, "agents": agents,
            "token_sha256": (environ.get("MINIMOI_MC_TOOLS_TOKEN_SHA256") or "").strip().lower(),
            "port": int(environ.get("MINIMOI_MC_TOOLS_PORT") or 5011),
            "invites": environ.get("MINIMOI_MC_TOOLS_INVITES") == "1",
            "tickets": environ.get("MINIMOI_MC_TOOLS_TICKETS") == "1",
            "comments": environ.get("MINIMOI_MC_TOOLS_COMMENTS") == "1",
            "github_read": environ.get("MINIMOI_MC_TOOLS_GITHUB", "1") != "0",
            "guest_rejects": environ.get("MINIMOI_MC_GUEST_ACTIONS") == "1",
            # Operator statement, not a check: mc-chat's gateway runs alone under its own OS user (deploy handoff §4).
            "isolated_runtime": environ.get("MINIMOI_MC_TOOLS_RUNTIME_ISOLATED") == "1",
            "standard_changes": _standard_changes(environ.get("MINIMOI_MC_TOOLS_STANDARD_CHANGES") or ""),
            "issues_token_file": environ.get("MINIMOI_MC_ISSUES_TOKEN_FILE") or "",
            "repo": environ.get("MINIMOI_GITHUB_REPO") or "",
            "delegation": environ.get("MINIMOI_MC_DELEGATION") == "1",
            # Operator-installed policy and its approval record (spec §8.1); absolute paths, outside any agent's reach.
            "policy_file": environ.get("MINIMOI_MC_POLICY_FILE") or "",
            "policy_authority_file": environ.get("MINIMOI_MC_POLICY_AUTHORITY_FILE") or ""}


_POLICY_SOURCES = {}


def policy_source(cfg):
    """The one PolicySource for these files in this process, shared by the tool service and the owner's approvals,
    or None when the paths are not configured (then every tool is refused: no permissive fallback)."""
    from core.mc_tools.policy import PolicySource
    paths = (cfg.get("policy_file") or "", cfg.get("policy_authority_file") or "")
    if not all(os.path.isabs(p) for p in paths):
        return None
    if paths not in _POLICY_SOURCES:
        _POLICY_SOURCES[paths] = PolicySource(*paths)
    return _POLICY_SOURCES[paths]


def _standard_changes(value):
    """'guest_reject=SC-3@371,access_invite=SC-4@372' -> {kind: {"id", "ticket"}}: standard changes Robert pre-approved
    (change-control standard §5). Malformed entries are ignored, so a typo means 'no standard change', never wider."""
    import re
    out = {}
    for part in value.split(","):
        m = re.fullmatch(r"\s*(guest_reject|access_invite)=([A-Za-z0-9_.-]{1,40})@([1-9][0-9]{0,4})\s*", part)
        if m:
            out[m.group(1)] = {"id": m.group(2), "ticket": int(m.group(3))}
    return out


def _connect(dsn):
    import psycopg2
    return lambda: psycopg2.connect(dsn, connect_timeout=4, options="-c statement_timeout=5000 -c lock_timeout=3000")


class _NoBinding:
    """Tools off for this turn: nothing is recorded, no run is attached."""

    def __call__(self):
        return None

    def attach_run(self, run_id):
        return None

    def revoke(self):
        return "none"


NO_BINDING = _NoBinding()


class _TurnBinding:
    """Calling it records this turn's tool binding right before it is sent; ``attach_run`` then binds the exact
    runtime run the relay reports for it (spec v1.2 §7.1). Failures leave tools refused for this turn and never
    stop the turn itself."""

    def __init__(self, record, attach, revoke):
        self._record, self._attach, self._revoke = record, attach, revoke

    def __call__(self):
        self._record()

    def attach_run(self, run_id):
        self._attach(run_id)

    def revoke(self):
        """The owner's Stop: fenced in this process first (cannot fail), then recorded durably. Returns "revoked"
        when the durable record holds, "fenced" when only the in-process fence does (the listener in this process
        still refuses every call; StreamRun reports it honestly)."""
        return self._revoke()


def binder(services, ledger_turn, *, environ, principal, conversation_id, note_id, session_conversation_id):
    """This turn's binding (``_TurnBinding``); a no-op when tools are off, the ledger is off or the backend is
    not OpenClaw."""
    cfg = settings(environ)
    backend = getattr(services, "mc", None)
    if (cfg is None or ledger_turn is None or principal != cfg["principal"]
            or getattr(backend, "kind", "") != "openclaw" or not hasattr(backend, "session_user")):
        return NO_BINDING
    claim, connect = ledger_turn.claim, _connect(cfg["dsn"])
    # The exact agent this turn is sent to ("openclaw/mc-chat" -> "mc-chat"): only its calls can belong to the turn.
    runtime_agent = str(getattr(backend, "chat_model", "") or "").partition("/")[2]

    def bind():
        from core.mc_tools.binding import record
        try:
            conn = connect()
            try:
                with conn:
                    live = record(conn, ledger_id=claim.ledger_id, claim_generation=claim.generation,
                                  principal=principal, conversation_id=conversation_id, note_request_id=note_id,
                                  session_user=backend.session_user(session_conversation_id),
                                  runtime_agent=runtime_agent, environment=cfg["environment"],
                                  portal_epoch=PORTAL_EPOCH)
            finally:
                conn.close()
            if not live:
                _log.warning("mc tools: ledger %s not live at dispatch; tools refused for this turn", claim.ledger_id)
        except Exception:
            _log.exception("mc tools: binding for ledger %s not recorded; tools refused for this turn", claim.ledger_id)

    def attach(run_id):
        from core.mc_tools.binding import attach_run
        try:
            conn = connect()
            try:
                with conn:
                    bound = attach_run(conn, ledger_id=claim.ledger_id, claim_generation=claim.generation, run_id=run_id,
                                       environment=cfg["environment"])
            finally:
                conn.close()
            if not bound:
                _log.warning("mc tools: run for ledger %s not attached; tools refused for this turn", claim.ledger_id)
        except Exception:
            _log.exception("mc tools: run for ledger %s not attached; tools refused for this turn", claim.ledger_id)
    def revoke():
        from core.mc_tools.binding import revoke as revoke_turn
        from core.mc_tools.fence import fence
        fence(claim.ledger_id, claim.generation)
        try:
            conn = connect()
            try:
                with conn:
                    revoke_turn(conn, ledger_id=claim.ledger_id, claim_generation=claim.generation)
            finally:
                conn.close()
        except Exception:
            _log.error("mc tools: Stop for ledger %s not recorded durably; fenced in this process only", claim.ledger_id,
                       exc_info=True)
            return "fenced"
        return "revoked"
    return _TurnBinding(bind, attach, revoke)


def _guests_lookup(email):
    from minimoi_portal import auth
    for g in auth.list_guests():               # any login with this email, expired too: a grant would refuse it
        if (g.get("email") or "").lower() == email:
            return g.get("username") + (", expired" if g.get("expired") else "")
    return None


def _private_token(path):
    """The token from an owner-only regular file (no symlink), or None. Never logged."""
    import stat
    try:
        if not path or not os.path.isabs(path):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077 or st.st_size > 512:
                return None
            token = os.read(fd, 512).decode("ascii").strip()
        finally:
            os.close(fd)
        return token if 20 <= len(token) <= 255 and token.isprintable() and " " not in token else None
    except (OSError, UnicodeError):
        return None


def _repo(cfg):
    from .mc_context import repo as default_repo
    return default_repo({"MINIMOI_GITHUB_REPO": cfg["repo"]} if cfg["repo"] else {})


def _github_post(cfg, path, payload, key, http_post):
    """POST one issue write. A 201 carrying ``key`` (an int) is ``created``; an answer GitHub gives before writing
    anything (401/403/404/410/422) is ``refused``; anything else, including a timeout, is ``unknown``, never retried."""
    import requests
    from .mc_context import API
    token = _private_token(cfg["issues_token_file"])
    if token is None:
        return "refused", None
    try:
        r = (http_post or requests.post)(f"{API}/repos/{_repo(cfg)}{path}", json=payload, timeout=(5, 15),
                                         allow_redirects=False, headers={"Authorization": f"Bearer {token}",
                                                                         "Accept": "application/vnd.github+json",
                                                                         "X-GitHub-Api-Version": "2022-11-28"})
    except Exception:
        return "unknown", None
    if r.status_code in (401, 403, 404, 410, 422):
        return "refused", None
    try:
        value = r.json().get(key) if r.status_code == 201 else None
    except ValueError:
        value = None
    return ("created", value) if type(value) is int else ("unknown", None)


def issue_filer(cfg, *, http_post=None):
    """``file_issue(title, body) -> (state, number)`` for data-repair tickets, or None while tickets are off."""
    if not cfg["tickets"] or _private_token(cfg["issues_token_file"]) is None:
        return None
    return lambda title, body: _github_post(cfg, "/issues", {"title": title, "body": body}, "number", http_post)


def issue_commenter(cfg, *, http_post=None):
    """``comment_issue(number, body) -> (state, comment id)``, or None while comments are off."""
    if not cfg["comments"] or _private_token(cfg["issues_token_file"]) is None:
        return None

    def comment_issue(number, body):
        if type(number) is not int or not 1 <= number <= 99999:
            return "refused", None
        return _github_post(cfg, f"/issues/{number}/comments", {"body": body}, "id", http_post)
    return comment_issue


def github_reader(cfg, *, http_get=None):
    if not cfg["github_read"]:
        return None
    import requests
    from core.mc_tools.github import GitHubIssues
    return GitHubIssues(http_get or requests.get, repo=_repo(cfg))


def _guest_people():
    """Display names and emails of every guest login, expired too: names that must never appear in public text."""
    from minimoi_portal import auth
    return [f"{g.get('display_name') or ''} {g.get('email') or ''}" for g in auth.list_guests()]


def owns_actions(environ, principal) -> bool:
    """Whether MC's typed tools own its action intents for this owner in this deploy (spec v1.2 A15). Then, and for the
    whole deploy, no regex owns the same intent: the owner's guest-rejection and "Remember:" phrases, and MC's own
    JOB: / ASK / MEETING lines, act on nothing. One switch, fixed at start: never per sentence, never on a model
    failure. File delivery and the owner's other commands are unaffected."""
    try:
        cfg = settings(environ or {})
    except Exception:
        return False
    return cfg is not None and principal == cfg["principal"]


def _conversation_floor(services, principal, conversation_id):
    """The notes floor of the owner's own, unarchived conversation, or None."""
    from .conversations import conversations_of
    conv = conversations_of(services).get(conversation_id, principal)
    if not isinstance(conv, dict) or conv.get("principal") != principal or conv.get("archived"):
        return None
    base, nf = services.floor, conv.get("notes_floor")
    return base if not nf or nf == base.floor else base.for_floor(nf)


def proposal_shower(services, principal):
    """``show(conversation_id, note_key, text) -> bool``: the platform keeps a proposal, word for word, as its own note
    in that conversation, so Robert approves what the platform showed, not MC's retelling (review P1-1). The note key
    is the proposal's own: a repeat keeps one note, and different text under it is refused (spec §9.1)."""
    def show(conversation_id, note_key, text):
        from .stores import GUILD_PLATFORM
        try:
            floor = _conversation_floor(services, principal, conversation_id)
            return floor is not None and \
                floor.add_note(note_key, text, GUILD_PLATFORM, area="Guild", page="mc").outcome == "kept"
        except Exception:
            _log.exception("mc tools: proposal could not be shown")
            return False
    return show


# This portal process's tool-admission epoch (core.mc_tools.binding.start_portal_epoch): recorded before the listener
# accepts traffic; turns recorded by an earlier process are revoked then, and only this epoch's turns are admitted.
PORTAL_EPOCH = str(uuid.uuid4())

_HEALTH_DOMAINS = {"Curator": "curator", "Mein Deutsch": "german", "Meu Português": "portuguese", "Guild": "guild"}


def domain_health_reader(environ, origin):
    """``domain_health() -> {domain_id: {state, evidence, observed_at}}`` from the deployment's fixed liveness probe
    (adapters.domain_health): ok when /health answered as that service, down when it failed, unknown when this
    deployment does not check (A09: unavailable is told apart from absent). Never follows a caller URL."""
    def domain_health():
        from .. import config as portal_config
        from .adapters.domain_health import snapshot
        origins = {name: getattr(portal_config, name, None)
                   for name in ("CURATOR_BACKEND", "GERMAN_BACKEND", "PORTUGUESE_BACKEND")}
        out = {}
        for label, value in snapshot(origins, origin=origin, environ=environ):
            reason = getattr(value, "error", None) or getattr(value, "reason", None) or ""
            state = "ok" if value.ok else ("down" if "unavailable" in str(reason) or "failed" in str(reason)
                                           else "unknown")
            out[_HEALTH_DOMAINS.get(label, label)] = {"state": state, "evidence": value.evidence,
                                                      "observed_at": value.observed_at}
        return out
    return domain_health


def handoff_receiver(services, cfg, url_prefix=""):
    """``receive(body) -> acknowledgement`` for the Chief of Staff's handoffs (core.mc_tools.handoffs): one Guild
    conversation per transfer (the conversation store's idempotent create), the platform note under a key fixed by the
    transfer id, read back before acknowledging."""
    from core.mc_tools import handoffs
    from .conversations import conversations_of
    from .stores import GUILD_PLATFORM
    principal = cfg["principal"]

    def deliver(transfer_id, title, text):
        store = conversations_of(services)
        conv, repeated = store.create(principal, key=f"mc-handoff-{transfer_id}")
        if not repeated:
            conv = store.rename(conv["id"], principal, title)
        floor = _conversation_floor(services, principal, conv["id"])
        key = f"mc-handoff-{transfer_id}"
        if floor is None or floor.add_note(key, text, GUILD_PLATFORM, area="Guild", page="mc").outcome != "kept":
            raise handoffs.HandoffUnknown("handoff_unconfirmed")      # never "refused": the note may exist (H1)
        return conv["id"], key

    def read_back(conversation_id, note_ref):
        note = proposal_reader(services, principal)(conversation_id, note_ref)
        return note.get("text") if note else None

    def link_for(conversation_id):
        from urllib.parse import quote
        return f"{url_prefix}/guild/build?c={quote(str(conversation_id), safe='')}"

    def receive(body):
        return handoffs.receive(_connect(cfg["dsn"]), body, principal=principal, environment=cfg["environment"],
                                deliver=deliver, read_back=read_back, link_for=link_for)
    return receive


def _mc_registers(environ, principal, origin):
    """MC's own work register for this mount (mc_registers.configured_store), or None: then gaps are not recorded."""
    try:
        from .mc_registers import configured_store
        return configured_store(environ, principal, origin)
    except Exception:
        _log.warning("mc tools: work register unavailable; gap follow-ups are off")
        return None


def owner_note_reader(services, principal):
    """``read_owner_note(conversation_id, note_request_id) -> note | None``: Robert's own kept, on-record note in his
    own conversation (FloorStores.get_owner_note: owner author, on record, bounded). A preference is kept only from
    its exact words (spec §10.2). Raises when the store cannot answer."""
    def read_owner_note(conversation_id, note_request_id):
        floor = _conversation_floor(services, principal, conversation_id)
        return None if floor is None else floor.get_owner_note(note_request_id, principal)
    return read_owner_note


def conversation_exporter(services, principal):
    """Only this bound owned on-record floor, freshly authorized before and after the single-statement snapshot."""
    from .conversations import conversations_of
    from .mc_conversation_export import render
    def authorize(conversation_id):
        conv=conversations_of(services).get(conversation_id, principal, migrate_legacy=False)
        if (not isinstance(conv,dict) or conv.get('principal')!=principal or conv.get('archived') or
                conv.get('record_mode','on_record')!='on_record' or conv.get('legacy') or
                conv.get('notes_floor') != services.floor.floor + '/' + conversation_id):
            raise ValueError('export_snapshot_unavailable')
        return conv
    def export(conversation_id, note_request_id):
        conv=authorize(conversation_id)
        base=services.floor
        floor=base if base.floor==conv['notes_floor'] else base.for_floor(conv['notes_floor'])
        rows=floor.export_notes_snapshot(note_request_id,principal)
        fresh=authorize(conversation_id)
        if any(fresh.get(k)!=conv.get(k) for k in ('principal','notes_floor','scope','record_mode')):
            raise ValueError('export_snapshot_unavailable')
        return render(rows,boundary_request=note_request_id,principal=principal)
    export.authorize=authorize  # Recheck scope at final execution admission without recapturing the frozen bytes.

    return export


def proposal_reader(services, principal):
    """``read_shown(conversation_id, note_key) -> note | None``: the platform's own note under that key, read back from
    the owner's conversation, so an offer becomes approvable only once its card is really there (spec §9.1, A04).
    Raises when the store cannot answer: the caller then leaves the offer unapprovable, never guesses."""
    def read_shown(conversation_id, note_key):
        from .stores import GUILD_PLATFORM
        floor = _conversation_floor(services, principal, conversation_id)
        if floor is None:
            return None
        note = floor.get_note(note_key)
        return note if note is not None and note.get("who") == GUILD_PLATFORM.id else None
    return read_shown


def build_service(cfg, *, lessons_root=None, lessons_env=None, invites=None, show=None, read_shown=None,
                  read_owner_note=None, domain_health=None, registers=None, board=None, export_conversation=None, recall_source=None):
    from core.mc_tools.service import ToolService
    lessons = None
    if lessons_root and os.path.isabs(lessons_root) and lessons_env in ("dev", "prod"):
        from core.mc_lessons.ledger import LessonLedger
        lessons = LessonLedger(lessons_root, principal=cfg["principal"], environment=lessons_env)
    return ToolService(_connect(cfg["dsn"]), principal=cfg["principal"], environment=cfg["environment"],
                       allowed_agents=cfg["agents"], lessons=lessons, delegation=cfg["delegation"],
                       invites=cfg["invites"] if invites is None else invites,
                       tickets=issue_filer(cfg) is not None, comments=issue_commenter(cfg) is not None,
                       guest_rejects=cfg["guest_rejects"], github=github_reader(cfg), show=show, read_shown=read_shown,
                       isolated_runtime=cfg["isolated_runtime"], people=_guest_people,
                       standard_changes=cfg["standard_changes"],
                       guests_lookup=_guests_lookup, policy=policy_source(cfg), read_owner_note=read_owner_note,
                       domain_health=domain_health, registers=registers, board=board, export_conversation=export_conversation, recall_source=recall_source,
                       artifact_root=artifact_root(os.environ))


def artifact_root(environ):
    """Where MC's governed artifacts live, as the MC runtime names it (plan D4). Unset keeps the original Mac default
    (core/mc_tools/host_artifacts.ROOT); MC's own container on EC2 uses /opt/minimoi/mc/artifacts at the same path."""
    root = str(environ.get("MINIMOI_MC_ARTIFACT_ROOT", "") or "").strip()
    return root if root and os.path.isabs(root) and os.path.normpath(root) == root else None


def board_adapter(app, environ, services, cfg):
    if environ.get("MINIMOI_BOARD_ACTIONS") != "1" or environ.get("MINIMOI_BOARD_ENV") != cfg["environment"]:
        return None
    import hashlib
    import hmac
    from core.mc_tools.board_adapter import BoardAdapter
    secret = app.secret_key.encode() if isinstance(app.secret_key, str) else app.secret_key
    if not secret or getattr(services, "floor", None) is None:
        return None
    def item_allowed(principal, item):
        if principal != cfg["principal"]:
            return False
        value = services.queue.get_item(item)
        return value.ok and value.data is not None
    return BoardAdapter(floor=services.floor.floor, cursor_key=hmac.new(secret,b"minimoi-board-cursors-v1",hashlib.sha256).digest(),
                        item_allowed=item_allowed)


def attach(app, blueprint_name, services):
    """Start the tool listener for this mount (once per process). Nothing happens while tools are off."""
    environ = app.extensions[blueprint_name].get("feature_environ") or {}
    cfg = settings(environ)
    if cfg is None or app.extensions.get("mc_tools_listener"):
        return None
    import re
    if not re.fullmatch(r"[0-9a-f]{64}", cfg["token_sha256"]):
        _log.warning("mc tools: MINIMOI_MC_TOOLS_TOKEN_SHA256 missing or invalid; the tool listener stays off")
        return None
    from services.mc_tools.listener import create_app, serve
    lessons_env = environ.get("MINIMOI_MC_LESSONS_ENV") if environ.get("MINIMOI_MC_LESSONS") == "1" else None
    # Invites need the portal's own delivery (app.py); without it MC is told invites are off, never half-done.
    invites = cfg["invites"] and app.extensions.get("mc_invite_delivery") is not None
    from .mc_recall import Source as RecallSource
    recall_source = (lambda: RecallSource(services,cfg['principal'])) if environ.get('MINIMOI_MC_RECALL') == '1' and cfg['environment']=='dev' else None
    service = build_service(cfg, recall_source=recall_source, lessons_root=environ.get("MINIMOI_MC_LESSONS_DIR"), lessons_env=lessons_env,
                            board=board_adapter(app, environ, services, cfg),
                            invites=invites, show=proposal_shower(services, cfg["principal"]),
                            read_shown=proposal_reader(services, cfg["principal"]),
                            read_owner_note=owner_note_reader(services, cfg["principal"]),
                            export_conversation=conversation_exporter(services, cfg["principal"]),
                            domain_health=domain_health_reader(environ, app.extensions[blueprint_name].get("base_url", "")),
                            registers=_mc_registers(environ, cfg["principal"],
                                                    app.extensions[blueprint_name].get("base_url", "")))
    agent_worker_sha = (environ.get('MINIMOI_MC_AGENT_WORKER_TOKEN_SHA256') or '').strip().lower()
    enrollment_path = environ.get('MINIMOI_MC_AGENT_ENROLLMENT')
    if enrollment_path and re.fullmatch('[0-9a-f]{64}',agent_worker_sha):
        try:
            from core.agent_requests.direct_contact import Target, _read
            from core.mc_tools.agent_contact import AgentContact
            from pathlib import Path
            path = Path(enrollment_path)
            if not path.is_absolute() or any(p.is_symlink() for p in (path,*path.parents)):
                raise ValueError('agent_enrollment_path_invalid')
            enrolled = _read(path)
            if set(enrolled) != {'targets'} or type(enrolled['targets']) is not dict:
                raise ValueError('agent_enrollment_invalid')
            targets = {alias:Target(**target) for alias,target in enrolled['targets'].items()}
            authorize = conversation_exporter(services,cfg['principal']).authorize
            def allowed(principal,conversation):
                if principal != cfg['principal']:return False
                try:authorize(conversation);return True
                except Exception:return False
            approved_handoffs=None
            handoff_path=environ.get('MINIMOI_MC_APPROVED_HANDOFF_REGISTRY')
            if handoff_path:
                from core.mc_tools.approved_handoff import ApprovedHandoffs, load_registry
                approved_handoffs=ApprovedHandoffs(load_registry(handoff_path))
            service.agent_contact = AgentContact(targets,conversation_allowed=allowed,approved_handoffs=approved_handoffs,
                status_protocol_version=int(os.environ.get('MINIMOI_MC_STATUS_PROTOCOL_VERSION','1')))
            task_registry = environ.get('MINIMOI_MC_TASK_WATCH_REGISTRY')
            if task_registry:
                from core.mc_tools.task_watch import TaskWatch, registry
                service.task_watch = TaskWatch(registry(task_registry), service.agent_contact)
                service.task_watch.declarations(service)  # fail closed before serving

        except Exception:
            _log.warning('mc tools: agent enrollment unavailable; existing-chat tools remain disabled')
            agent_worker_sha = ''
    else:
        agent_worker_sha = ''
    # In the portal container the port is published to the host's loopback only; a portal run directly on a host must
    # not listen on the LAN, so outside a container the default is loopback (review P3-1).
    host = environ.get("MINIMOI_MC_TOOLS_BIND") or ("0.0.0.0" if os.path.exists("/.dockerenv") else "127.0.0.1")
    # Publish the approved policy before accepting owner turns. Lazy activation on
    # the first tool call would revoke that very turn after a policy deployment.
    # A genuine later policy change still revokes in-flight calls as before.
    try:
        from core.mc_tools.binding import start_portal_epoch, activate_policy
        compiled, problem = service.policy.current() if service.policy is not None else (None, "policy_not_configured")
        if compiled is None:
            _log.error("mc tools: startup policy unavailable (%s); the tool listener stays off", problem)
            return None
        conn = _connect(cfg["dsn"])()
        try:
            with conn:
                activate_policy(conn, environment=cfg["environment"], policy_version=compiled.version)
                start_portal_epoch(conn, environment=cfg["environment"], epoch=PORTAL_EPOCH)
        finally:
            conn.close()
    except Exception:
        _log.error("mc tools: startup policy/epoch not recorded; the tool listener stays off", exc_info=True)
        return None
    handoff_sha = (environ.get("MINIMOI_MC_HANDOFF_TOKEN_SHA256") or "").strip().lower()
    receiver = (handoff_receiver(services, cfg, app.extensions[blueprint_name].get("url_prefix", ""))
                if re.fullmatch(r"[0-9a-f]{64}", handoff_sha) else None)
    thread = serve(create_app(service, token_sha256=cfg["token_sha256"], handoff=receiver,
                              handoff_token_sha256=handoff_sha if receiver else None,
                              agent_worker_token_sha256=agent_worker_sha or None), host=host, port=cfg["port"])
    app.extensions["mc_tools_listener"] = thread
    _log.info("mc tools: listener on %s:%s (agents %s)", host, cfg["port"], ",".join(cfg["agents"]))
    if agent_worker_sha and environ.get("MINIMOI_MC_WORKER_PUBLIC") == "1":
        attach_worker_door(app, cfg["port"])
    return thread


WORKER_PATH = "/internal/mc/v1/agent-worker"


def attach_worker_door(app, port, *, opener=None, clock=None, per_minute=120):
    """The Mac agent worker's way in from outside the host (plan 2c): ONE path on the portal's public port, passed
    unchanged to the internal tool listener, which alone authenticates the worker's own token. Nothing else of the
    listener is reachable; bodies and replies are bounded as the worker bounds them; a crude per-address rate limit
    caps guessing. Off unless MINIMOI_MC_WORKER_PUBLIC=1 (and a worker credential hash is configured)."""
    import time
    import urllib.error
    import urllib.request
    from flask import Response, request
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    clock = clock or time.monotonic
    seen = {}

    def worker_door():
        now, who = clock(), request.headers.get("X-Real-IP") or request.remote_addr or "-"
        window = [t for t in seen.get(who, ()) if now - t < 60]
        if len(window) >= per_minute:
            return Response('{"error":"worker_rate_limited"}', 429, mimetype="application/json")
        seen[who] = window + [now]
        if len(seen) > 1000:
            seen.clear()
        auth = request.headers.get("Authorization", "")
        if request.mimetype != "application/json" or not auth.startswith("Bearer ") or (request.content_length or 0) > 16384:
            return Response('{"error":"worker_request_invalid"}', 400, mimetype="application/json")
        body = request.get_data(cache=False)
        if len(body) > 16384:
            return Response('{"error":"worker_request_invalid"}', 400, mimetype="application/json")
        req = urllib.request.Request(f"http://127.0.0.1:{int(port)}{WORKER_PATH}", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": auth})
        try:
            with opener.open(req, timeout=10) as r:
                status, raw = r.status, r.read(65537)
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read(65537)
        except Exception:
            return Response('{"error":"worker_door_unavailable"}', 503, mimetype="application/json")
        if len(raw) > 65536:
            return Response('{"error":"worker_response_bound"}', 502, mimetype="application/json")
        return Response(raw, status, mimetype="application/json")

    app.add_url_rule(WORKER_PATH, "mc_agent_worker_door", worker_door, methods=["POST"])
    _log.info("mc tools: agent worker door open on the public port (%s only)", WORKER_PATH)


# ── the owner's Confirm / Cancel ──────────────────────────────────────────────

def invite_runner(send_email, notify, *, db=None):
    """``invite(proposal, ref)`` for ToolProposals: grant through guest_requests.grant (the hardened state
    machine), and hand back a ``deliver()`` that emails the one-time password to the invitee."""
    def invite(p, ref):
        from minimoi_portal import guest_requests as gr
        pay = p["payload"]
        out = gr.grant(ref, duration_days=pay["days"], db=db)
        if out.status == "unknown":
            return "unknown", {}, None
        if out.status != "done":
            return "conflict", {"error": (out.verified_state or {}).get("error") or out.status}, None
        state = out.verified_state
        secret, generation = out.delivery_secret, state["credential_generation"]
        guest = {"email": pay["email"], "display_name": pay["name"], "username": state["username"]}

        def deliver():
            sent = bool(send_email(guest, temp_password=secret)) if send_email else False
            if sent:
                gr.confirm_delivery(ref, generation)
            if notify:
                notify(guest, email_sent=sent, temp_password=secret)     # as the Operate grant path does
            return sent
        return "done", {"username": state["username"]}, deliver
    return invite


def proposals(environ, *, start_research, delivery, lessons=None, read_shown=None, start_build=None):
    """The ToolProposals handler for this mount, or None when tools are off."""
    cfg = settings(environ)
    if cfg is None:
        return None
    from core.mc_tools.proposals import ToolProposals
    invite = invite_runner(*delivery) if cfg["invites"] and delivery else None
    return ToolProposals(_connect(cfg["dsn"]), principal=cfg["principal"], environment=cfg["environment"],
                         start_research=start_research if cfg["delegation"] else None, invite=invite,
                         start_build=start_build if cfg["delegation"] else None,
                         file_issue=issue_filer(cfg), comment_issue=issue_commenter(cfg),
                         guest_rejects=cfg["guest_rejects"], lessons=lessons, read_shown=read_shown,
                         policy=policy_source(cfg))


def saved_artifacts(environ, principal, conversation_id):
    """For an owner's download request: files(refs) -> [{name, content}] from MC's verified artifact records (bytes
    kept in the database with their sha256), only this principal's, this environment's and this conversation's.
    None when MC tools are off (then a model packet naming artifacts is refused)."""
    cfg = settings(environ)
    if cfg is None:
        return None

    def files(refs):
        import hashlib as _hashlib, json as _json
        from core.minimoi_actions.receipts import _cursor
        out = []
        conn = _connect(cfg["dsn"])()
        try:
            with conn, _cursor(conn) as cur:
                for ref in refs:
                    cur.execute("""SELECT payload, payload_sha256, state FROM guild.mc_host_artifacts WHERE operation_id=%s
                        AND principal_id=%s AND environment=%s AND conversation_id=%s""",
                        (ref, principal, cfg["environment"], conversation_id))
                    row = cur.fetchone()
                    payload = bytes(row["payload"]) if row else b""
                    if (row is None or row["state"] != "verified"
                            or _hashlib.sha256(payload).hexdigest() != row["payload_sha256"]):
                        return []
                    for f in _json.loads(payload).get("files", []):
                        out.append({"name": f["name"] if len(refs) == 1 else ref[:8] + "-" + f["name"],
                                    "content": f["text"]})
        finally:
            conn.close()
        return out
    return files
