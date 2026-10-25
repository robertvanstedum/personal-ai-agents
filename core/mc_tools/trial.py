"""M1.4 trial evidence from the records themselves (spec v1.2 §16.4; A24 one-offer measurement).

Counts distinct owner requests (owner turns) whose action request was blocked by an existing unrelated pending offer,
against all owner requests that asked for an action, with their references. Transport retries are one admission (the
host call id is the admission key) and a turn counts once. It recommends nothing and changes no policy.
"""
from __future__ import annotations

ACTION_TOOLS = ("minimoi_guest_request_reject", "minimoi_issue_comment", "minimoi_data_repair_ticket",
                "minimoi_lesson_propose", "minimoi_delegate_research", "minimoi_access_invite",
                "minimoi_keep_preference", "minimoi_revoke_preference")


def one_offer_report(conn, *, principal: str, since) -> dict:
    with conn.cursor() as cur:
        cur.execute("""SELECT a.ledger_id, a.claim_generation, t.note_request_id, t.conversation_id, a.tool,
                a.tool_call_id, a.result_code, a.admitted_at
            FROM guild.mc_tool_admissions a JOIN guild.mc_tool_turns t
              ON t.ledger_id = a.ledger_id AND t.claim_generation = a.claim_generation
            WHERE t.principal = %s AND a.admitted_at >= %s AND a.tool = ANY(%s)
            ORDER BY a.admitted_at""", (principal, since, list(ACTION_TOOLS)))
        rows = cur.fetchall()
    requests, blocked = {}, {}
    for ledger_id, gen, note, conv, tool, call, code, at in rows:
        key = (ledger_id, gen)
        requests.setdefault(key, {"note": note, "conversation": conv, "tools": set()})["tools"].add(tool)
        if code == "offer_pending":
            blocked.setdefault(key, {"note": note, "conversation": conv, "tool": tool, "call": call,
                                     "at": at.isoformat() if hasattr(at, "isoformat") else str(at)})
    return {"action_requests": len(requests), "blocked_by_pending_offer": len(blocked),
            "blocked": list(blocked.values()),
            "requests": [{"note": v["note"], "conversation": v["conversation"], "tools": sorted(v["tools"])}
                         for v in requests.values()]}
