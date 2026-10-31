"""Descriptive evidence only. Never grants admission or enables a tool."""
from . import catalogue

DESCRIBING_TOOLS = frozenset({"minimoi_describe_house", "minimoi_describe_domain", "minimoi_record_gap"})


def offered_list(value):
    """Host plugin metadata, outside model arguments. None means old/unverified plugin."""
    if value is None:
        return None
    if (type(value) is not list or len(value) > len(catalogue.TOOLS)
            or any(type(n) is not str or n not in catalogue.TOOLS for n in value)
            or len(set(value)) != len(value)):
        raise ValueError("offered_tools_invalid")
    return frozenset(value)


def snapshot(service, policy_tools, offered_tools):
    """Intersect the admitted policy, configured handler dependencies and plugin factory list.

    The plugin list describes its own offer to OpenClaw, not successful execution
    or permission to act. Every real call still crosses the full admission path.
    """
    gates = {
        "minimoi_recall": [(getattr(service,"recall_source",None) is not None,"recall_unavailable")],
        "minimoi_conversation_export": [(getattr(service,"export_conversation",None) is not None,"conversation_export_unavailable")],
        "minimoi_guest_request_reject": [(service.guest_rejects, "guest_actions_off")],
        "minimoi_github_issues": [(service.github is not None, "github_reader_unavailable")],
        "minimoi_issue_comment": [(service.comments, "issue_comments_unavailable")],
        "minimoi_data_repair_ticket": [(service.tickets, "issue_filer_unavailable")],
        "minimoi_issue_create": [(service.tickets, "issue_filer_unavailable")],
        "minimoi_issue_update": [(service.comments, "issue_updates_unavailable")],
        "minimoi_lesson_propose": [(service.lessons is not None, "lessons_unavailable")],
        "minimoi_keep_preference": [(service.lessons is not None, "lessons_unavailable"),
                                    (service.read_owner_note is not None, "owner_note_reader_unavailable")],
        "minimoi_revoke_preference": [(service.lessons is not None, "lessons_unavailable")],
        "minimoi_agent_ask": [(getattr(service, "agent_ask", None) is not None, "agent_requests_unavailable"),
                              (service.read_owner_note is not None, "owner_note_reader_unavailable")],
        "minimoi_record_gap": [(service.registers is not None, "work_register_unavailable"),
                               (service.read_owner_note is not None, "owner_note_reader_unavailable")],
        "minimoi_withdraw_gap": [(service.registers is not None, "work_register_unavailable")],
        "minimoi_publish_update": [(getattr(service, "activity_update", None) is not None, "activity_unavailable")],
        "minimoi_delegate_research": [(service.delegation, "delegation_off")],
        "minimoi_delegate_build": [(service.delegation, "delegation_off")],
        "minimoi_access_invite": [(service.invites, "invites_off"),
                                  (service.guests_lookup is not None, "guest_lookup_unavailable")],
    }
    from domains.guild.board_operations import operations
    for operation in operations():
        if operation.tool:
            gates[operation.tool] = [(getattr(service, "board", None) is not None, "board_adapter_unavailable")]
            if operation.effect != "read":
                gates[operation.tool].append((service.read_owner_note is not None, "owner_note_reader_unavailable"))
    gates["minimoi_clarify_action"] = [(getattr(service, "board", None) is not None, "board_adapter_unavailable"),
                                       (service.read_owner_note is not None, "owner_note_reader_unavailable")]
    proposals = ("guest_request_reject", "issue_comment", "issue_create", "issue_update", "data_repair_ticket", "lesson_propose",
                 "delegate_research", "access_invite", "delegate_build")
    for name in proposals:
        gates["minimoi_" + name].extend([(service.show is not None, "proposal_display_unavailable"),
                                      (service.read_shown is not None, "proposal_readback_unavailable")])
    if service.environment == "prod":
        for tool, kind in (("minimoi_guest_request_reject", "guest_reject"),
                           ("minimoi_access_invite", "access_invite")):
            gates[tool].append((bool(service.standard_changes.get(kind)) and service.comments,
                                "approved_change_record_unavailable"))
    out = {}
    for name in catalogue.TOOLS:
        reasons = []
        if name not in policy_tools:
            reasons.append({"layer": "policy", "reason": "not_allowed"})
        reasons.extend({"layer": "portal", "reason": why} for ok, why in gates.get(name, []) if not ok)
        if offered_tools is None:
            reasons.append({"layer": "plugin", "reason": "offer_unverified"})
        elif name not in offered_tools:
            reasons.append({"layer": "plugin", "reason": "not_offered"})
        disabled = any(r["reason"] != "offer_unverified" for r in reasons)
        out[name] = {"state": "disabled" if disabled else "unknown" if reasons else "enabled",
                     "reasons": reasons}
    return out
