"""Read-only presentation projection; never infer runtime attestation from prose.

Recognizes the dedicated authenticated connector actor and matching committed
operation. Metadata remains declared; a formatted view grants no new authority.
"""
import json
from integration.cos_agent_responder import validate_evidence, canonical_uuid
from integration.cos_records_bridge import RoomBridgeError


def connector_view(db, event):
    if (event.get("actor") != "cos-dev" or event.get("actor_kind") != "agent"
            or event.get("kind") != "message" or event.get("context_class") != "agent_draft"):
        return None
    try:
        envelope = json.loads(event["body"])
        execution = envelope["execution"]
        request_id = canonical_uuid(execution["coordination_request_id"])
        validate_evidence(execution, request_id)
        origin = dict(source_application="cos_agent_responder", mode="agent_response",
                      agent_id="cos-agent-a", runtime="OpenClaw", execution_id=execution["openclaw_run_id"])
        if event.get("origin") != origin: return None
        if envelope.get("synthetic_only") is not True or envelope.get("tool_policy_enforced") is not False:
            return None
        sources = envelope["source_record_ids"]
        if not isinstance(sources, list) or len(sources)>500: return None
        for source in sources: canonical_uuid(source)
        row = db.execute("SELECT response FROM operations WHERE actor=? AND key=? AND room=?",
                         (event["actor"], "cos-agent-response:"+request_id, event["room"])).fetchone()
        if row is None: return None
        saved = json.loads(row["response"])
        result, receipt = saved["result"], saved["receipt"]
        if any(result.get(k) != event.get(k) for k in ("id", "room", "actor", "body", "kind", "context_class")):
            return None
        if receipt.get("actor") != event["actor"]: return None
        canonical_uuid(receipt["id"])
        return dict(type="agent_contribution", text=execution["text"],
                    warning="Synthetic test only · runtime tool policy is not enforced. Do not use private material.",
                    evidence=dict(coordination_request_id=request_id, runtime_id=execution["openclaw_run_id"],
                                  record_id=event["id"], receipt_id=receipt["id"], source_record_ids=sources,
                                  assurance="Authenticated posting identity and saved receipt; runtime metadata is declared, not independently attested."))
    except (ValueError, TypeError, KeyError, AttributeError, RoomBridgeError):
        return None
