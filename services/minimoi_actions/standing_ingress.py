"""Dedicated standing-event listener (not a blueprint on the public portal).

Production wiring must supply approved producer policies, a bounded authorized
artifact resolver and a database factory, and keep the listener off public
reverse proxies. Default disabled. Owner cookies/global gateway tokens are
never accepted as producer authority. No application model call is made here.
"""

import hashlib
import secrets
from uuid import UUID

from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from core.minimoi_actions.context import ActionContractError
from core.minimoi_actions.standing import ProducerPolicy, StandingEvent

PATH = '/internal/mc/v1/standing/events'


def create_app(*, environment: str, policies: tuple[ProducerPolicy, ...],
               store, load_payload, enabled: bool = False) -> Flask:
    if environment not in ('dev', 'prod') or type(enabled) is not bool:
        raise ValueError('standing_service_config_invalid')
    if (type(policies) is not tuple or not policies
            or not all(type(p) is ProducerPolicy and p.environment == environment for p in policies)
            or len({p.producer_id for p in policies}) != len(policies)
            or len({p.token_sha256 for p in policies}) != len(policies)):
        raise ValueError('standing_service_policy_invalid')
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 64 * 1024

    def authorize():
        if not enabled:
            return None, (jsonify(error='standing_disabled'), 503)
        value = request.headers.get('Authorization', '')
        if not value.startswith('Bearer ') or not 32 <= len(value[7:]) <= 256:
            return None, (jsonify(error='producer_unauthorized'), 401)
        digest = hashlib.sha256(value[7:].encode('utf-8')).hexdigest()
        policy = next((p for p in policies if secrets.compare_digest(p.token_sha256, digest)), None)
        if policy is None:
            return None, (jsonify(error='producer_unauthorized'), 401)
        return policy, None

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify(error='standing_event_size_invalid'), 413

    @app.get('/healthz')
    def health():
        return jsonify(status='enabled' if enabled else 'disabled')

    @app.post(PATH)
    def post_event():
        policy, failure = authorize()
        if failure:
            return failure
        if request.mimetype != 'application/json':
            return jsonify(error='json_required'), 415
        try:
            event = StandingEvent(request.get_data(cache=False))
            event.destination(policy)  # reject scope before touching payloads/DB
            result, status = store.accept(event, policy, load_payload)
            return jsonify(result), status
        except RequestEntityTooLarge:
            raise
        except ActionContractError as exc:
            return jsonify(error=str(exc)), 400
        except Exception:
            # May include an uncertain commit: no automatic retry. Producer
            # must GET the same scoped event ID to reconcile before reposting.
            return jsonify(error='standing_ingress_unavailable'), 503

    @app.get(PATH + '/<event_id>')
    def get_event(event_id):
        policy, failure = authorize()
        if failure:
            return failure
        try:
            if str(UUID(event_id)) != event_id:
                return jsonify(error='event_not_found'), 404
        except ValueError:
            return jsonify(error='event_not_found'), 404
        try:
            result = store.get(event_id, policy)
            return (jsonify(result), 200) if result else (jsonify(error='event_not_found'), 404)
        except Exception:
            return jsonify(error='standing_ingress_unavailable'), 503

    return app
