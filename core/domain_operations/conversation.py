"""One non-effect framework tool for durable clarification; not a Board API route.

It records a question/candidates only. Resolution still requires a new owner
turn and one of the domain's ordinary admitted mutation tools.
"""
from domains.guild.board_operations import obj, REF

NAME = "minimoi_clarify_action"
TOOL = {"name": NAME, "capability": "guild.board.clarify", "description":
    "Ask one necessary clarification before a Board action. Persist the question and candidate note references; "
    "this writes no note and grants no future execution. Use only when content or target is materially ambiguous. "
    "On the immediate owner answer, pass the returned clarification_ref with the appropriate Board mutation. "
    "For an unrelated new request omit it; do not revive an expired question.",
    "parameters": obj({"capability": {"enum": ["guild.board.note.create", "guild.board.note.update", "guild.board.operation.undo"]},
                       "question": {"type": "string", "minLength": 1, "maxLength": 500},
                       "candidate_refs": {"type": "array", "maxItems": 20, "uniqueItems": True, "items": REF}},
                      ("capability", "question", "candidate_refs"))}
