"""Opt-in owner control routes. Registration is a separate reviewed dev step.

Reuse the portal's owner guard and CSRF control, not an independent identity
scheme. Principal/environment and the worker channel are server configuration;
neither can be selected by JSON. Pause remains usable off-record, like Stop.
"""
from datetime import datetime, timezone
import json

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from core.minimoi_actions.context import ActionContractError, _identifier
from core.minimoi_actions.requests import _object, _constant
from core.minimoi_actions.standing_control import ControlRequest, effective_status
from .security import json_guard, check_stop


def create_blueprint(*, store, owner_guard, current_user, environment, read_ack,
                     publish, base_url=None, enabled=False):
    if environment not in ('dev', 'prod') or type(enabled) is not bool:
        raise ValueError('control_configuration_invalid')
    bp = Blueprint('mc_standing_control', __name__)
    guarded = json_guard(owner_guard, current_user)

    def principal():
        user = current_user()
        value = user.get('username') if isinstance(user, dict) else None
        if not _identifier(value):
            raise ActionContractError('control_principal_unavailable')
        return value

    def status(who):
        desired = store.desired(who, environment)
        try:
            acknowledged = read_ack(who, environment)
        except Exception:
            acknowledged = None
        return effective_status(desired, acknowledged, now=datetime.now(timezone.utc))

    @bp.route('/api/v1/mc/standing/control', methods=['GET', 'POST'])
    @guarded
    def control():
        if not enabled:
            return jsonify(error='standing_control_disabled'), 503
        try:
            who = principal()
            if request.method == 'POST':
                request.max_content_length = 4096
                failure = check_stop(base_url)
                if failure is not None:
                    return failure
                raw = request.get_data(cache=False)
                try:
                    doc = json.loads(raw.decode('utf-8'), object_pairs_hook=_object, parse_constant=_constant)
                    if type(doc) is not dict or set(doc) != {'request_id', 'action', 'expected_version'}:
                        raise ActionContractError('control_request_invalid')
                    command = ControlRequest(**doc)
                except (UnicodeError, ValueError, TypeError, RecursionError):
                    raise ActionContractError('control_request_invalid') from None
                store.command(who, environment, command)
                try:
                    publish(who, environment)
                except Exception:
                    # Command is durable. Export failure leaves desired state
                    # pending and is recoverable by replay/export; never claim
                    # that the command was not saved or that pause is effective.
                    pass
            result = status(who)
            return jsonify(result), 202 if result['state'] == 'pending' else 200
        except RequestEntityTooLarge:
            return jsonify(error='control_request_too_large'), 413
        except ActionContractError as exc:
            code = str(exc)
            return jsonify(error=code), 409 if code in ('control_version_conflict', 'control_request_reused') else 400
        except Exception:
            return jsonify(error='standing_control_unavailable'), 503

    return bp
