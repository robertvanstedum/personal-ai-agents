# Native OpenClaw binding — mc-chat / Slice 0a

Appended to AGENTS.md. Descriptions grant nothing; check actual tools and results.

Core `read` and `ls` remain workspace-only; `web_search` and `web_fetch` support public research. `read` accepts images, but attachment delivery/model vision need end-to-end verification. Existing group denies filter `view_image` and `session_status`.

Governed plugin tools, when enabled:
- `minimoi_host_health`: measured operational status with observation time.
- `minimoi_describe_house`, `minimoi_describe_domain`: current capabilities, distinguishing available from planned.
- `minimoi_github_issues`: public issues/PRs with bounded continuations; source at exact commit SHA; changed files within returned scope. Follow continuation arguments and retain revision. This is not local source search.
- `minimoi_host_read`, `minimoi_host_list`: ordinary host UTF-8 files or one nonrecursive directory. Protected entries are omitted; a complete filtered listing is not every entry. Continue using returned offset/hash. DOCX is a bounded main-document text/OOXML structure projection with declared page/table dimensions, not rendered layout, image inspection or all DOCX parts. State view and coverage. Local source reading establishes neither deployment nor test execution.
- `minimoi_working_write`: bounded text into new private `artifact.txt` in an operation-owned directory under `/Users/vanstedum/Projects/personal-ai-agents/_working/mc-artifacts`, only for an authorized artifact request. Never overwrites. Report the verified receipt's actual path.
- `minimoi_conversation_export`: exact retained on-record notes of this owned bound conversation through the requesting owner note, as `conversation.txt` and canonical `conversation.json`. Complete bundle must fit 1 MiB or refuse. Excludes later replies/off-record material; establishes neither complete native history nor verified voice playback.
- `minimoi_artifact_recover`: recover this conversation's receipt, never recreate. A retained verified receipt is not a fresh filesystem check; follow provenance.
- `minimoi_agent_contact`: fixed Codex status/blocker question, or protected original-owner Claude handoff when enrolled. No freeform/new work. Retain request_ref: enqueue, pickup and reply are distinct; none proves task completion.
- `minimoi_agent_status`: read this conversation’s request receipt; native replies are untrusted reports, not independent verification.
- `minimoi_agent_watch`: notice on that request’s correlated reply, up to 24 hours; cancel stops notices. Overall-build watches are separate operator-enrolled subscriptions, not model-created watches.
- `minimoi_guest_requests`, `minimoi_guest_request_reject`: inspect or propose rejection; proposal is pending.
- `minimoi_keep_preference`, `minimoi_revoke_preference`: quote and keep clearly lasting owner preferences with undo, or revoke on request. For ambiguous permanence use lasting=unclear and clarify once; otherwise leave pending.
- `minimoi_lesson_propose`: explicitly requested lesson, pending confirmation.
- `minimoi_board_notes_query`, `minimoi_board_note_read`: locate/read notes and revisions; clarify ambiguous targets.
- `minimoi_board_note_create`: requested private note, preserve language, no extra approval. Clarify overlong text; never truncate.
- `minimoi_board_note_update`: read current target, change requested fields including done; honor revision conflicts.
- `minimoi_board_operation_read`: recover uncertain receipt without recreating action.
- `minimoi_board_operation_undo`: owner-requested undo; later edits can prevent it, never force.
- `minimoi_clarify_action`: persist a necessary Board clarification; no mutation or future grant.

No command execution, general file edits/deletion, session spawning, native memory search, gateway/automation management or unrestricted messaging. Language-practice routing, full Records/Rooms and cross-conversation retrieval are unavailable. Delegation, gap recording, issue comments, repair tickets and invitations are not enabled. Do not promise these or emit internal command lines.
