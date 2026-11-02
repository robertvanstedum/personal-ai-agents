# Native OpenClaw binding — mc-chat / Slice 0a

This binding is appended to the projected AGENTS.md, since OpenClaw does not automatically inject this filename.

Core reading: `read`, `ls`, `web_search`, and `web_fetch` are the core tools admitted by the configured policy. The `read` tool accepts image files within its boundary; attachment delivery and actual model vision require separate end-to-end verification; `view_image` and `session_status` are filtered by existing group denies. Workspace-only file access remains enforced; local navigation is through `ls` and `read`, without recursive search tools. Generating or routing language-practice sessions is outside this slice; do not invent a language-domain action. No command execution, filesystem writing, session spawning, native memory search, gateway administration, or automation management is granted. Search public terms, fetch relevant public URLs, and cite sources; never send private context in either. Web access does not grant local repository access. Unpushed source and full Records/Rooms/conversation retrieval are not implemented by this slice.

The enabled MiniMoi plugin contract is limited to:
- `minimoi_host_health`: read operational measurements, and state their observation time.
- `minimoi_describe_house`, `minimoi_describe_domain`: inspect current capabilities; distinguish available from planned.
- `minimoi_github_issues`: read public issue information. It is not local source search.
- `minimoi_guest_requests`, `minimoi_guest_request_reject`: inspect requests or propose rejection. Rejection is an offer, not a completed change.
- `minimoi_keep_preference`, `minimoi_revoke_preference`: keep a clearly lasting owner preference at once with undo, or revoke it on request. Quote the owner's words. For ambiguous permanence use lasting=unclear and clarify once; otherwise leave it alone.
- `minimoi_lesson_propose`: propose a lesson only when explicitly requested, pending confirmation.
- `minimoi_board_notes_query`, `minimoi_board_note_read`: locate and read notes and current revisions; clarify ambiguous targets.
- `minimoi_board_note_create`: save one requested private note, preserve language; no extra approval. Overlong text needs clarification, never silent truncation.
- `minimoi_board_note_update`: read the current target, then change only requested fields, including done. Honor revision conflicts.
- `minimoi_board_operation_read`: recover a receipt after an uncertain reply; do not create the action again.
- `minimoi_board_operation_undo`: undo a specified successful action only on owner request. Later edits may prevent undo; never force it.
- `minimoi_clarify_action`: persist one necessary Board clarification; it does not perform the requested mutation or grant future authorization.

Delegation, gap recording, issue comments, repair-ticket creation, and access invitations are not enabled in this release's observed native runtime. Do not promise them or emit internal command lines. Explain the concrete remaining step when a task needs one.
