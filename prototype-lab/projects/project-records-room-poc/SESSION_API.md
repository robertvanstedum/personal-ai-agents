# Room/session API — schema 5 development slice

Use only a new synthetic data directory. This change has not been activated
against the existing development records, and no production deployment is authorized.

All calls require existing authentication. Writes require a stable
Idempotency-Key; retry uncertain writes with the same key and same payload.

1. POST `/api/v2/rooms` with `title` and `purpose` creates a quiet parent.
2. POST `/api/v2/rooms/{parent}/sessions` with `title`, `purpose`, optional `mode`
   (conversation/meeting/bridge), and `recording_acknowledged: true` opens a session.
3. Invite participants through `/api/v1/rooms/{session}/members`. Invitations
   apply ONLY to that session. Parent metadata is shared, so do not put private
   sibling-specific information in parent title or purpose.
4. Read `/api/v2/rooms/{parent}` for authorized child sessions. Use
   `/api/v2/sessions/{session}` or the v1 alias for a session snapshot.
5. Existing v1 message, note, artifact, export, state and membership endpoints
   continue using session IDs. State changes still require the current version
   and a checkpoint. Close is terminal; start a new child session afterward.

Legacy POST `/api/v1/rooms` creates a parent plus its first session, sharing an
ID for stable compatibility. Replay of a pre-migration receipt returns its
original bytes, without injecting parent metadata into historical receipts.

Migration is transactional. Schema 4 gets one parent per old session. Existing
IDs, events, notes, documents, artifact links, memberships and operations are
not rewritten. New-session creation is owner-only, each begins with only the
owner as a member, and session parents cannot be changed.

This is API/storage separation, not full H1 acceptance: browser navigation,
bounded scheduling/mandates, actual agent execution, independent preservation
and the other acceptance cases still require subsequent work and review.
