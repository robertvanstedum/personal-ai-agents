# Spec 158 delivery plan — Rooms light beta → production v0.9

**Registered:** 2026-09-21T04:19:30.915208+00:00. **Production build queue:** #158. **Contract:** [Spec 158](https://minimoi.ai/guild/build/spec/spec_158_collaboration_rooms_cos_beta_v09_2026-09-21.md).

**Owner direction:** Robert wants a light beta this week, learned through the second client presentation and real build-task handoffs, followed by a few days of use. Target production v0.9 by Sunday 27 September 2026, US Central, or sooner if the release evidence supports it. This delivery direction supplements the v0.6 design contract; it does not require every future milestone before useful beta use.

## What the beta is for

Robert and the current agents can use MiniMoi Rooms as the shared record for two real workflows: presentation 2 and implementation/review handoffs. Work can still happen in native chats and coding clients. Bring available outputs, reasoning and artifacts back to the right session, with actual authorship, requests, acknowledgments and receipts. A separate executive/status session can discuss explicitly shared work without interrupting its working session.

## First usable slice

- A0: existing MiniMoi login → new/extended account permission enforcement → Rooms access → session/operation grants. Robert is the only human configured; deny/revoke/outage tests use fixtures, not another real teammate.
- A1: usable desktop navigation, compact composer, Inbox Answer, named next actor, and supported client read/post/receipt across the two work sessions. Preserve existing meetings, records and receipts.
- Use already-supported authenticated import/file/reference paths for presentation material while better portal filing is built. Verify the actual path before calling it supported. Show source coverage, declared speakers, audience and size limitations. If no usable safe path exists, that gap is part of beta readiness, not something Robert must silently work around.
- CoS features are enabled only as far as their actual permission, runtime and audience evidence supports. Real private content must not enter the existing synthetic-only model path. The beta can support real human/client record work without claiming full CoS reasoning or autonomous convening is ready.

First beta acceptance is a real presentation checkpoint/artifact and an actual build-review request/result/acknowledgment through the supported paths, plus permission checks. A visually attractive empty room is not sufficient.

## Sequence and targets

| Window | Deliver and observe |
|---|---|
| Early week, target September 21–22 | Implement/review A0+A1 and the minimum verified artifact/checkpoint path. Deploy to dev, then run Robert’s first presentation/build walkthrough. If the permission work is larger, report the actual impact; do not substitute an owner shortcut. |
| Following few days | Use the beta for presentation 2 and build work. Fix observed failures and friction in small independently reviewed increments. Record issues and outcomes in the room; do not rely on Robert to relay reviews between agents. |
| By September 27, earlier if ready | Freeze the v0.9 release scope, complete independent review and release checks, and promote the reviewed candidate through the authorized production process. No calendar date alone overrides a failed permission, preservation or critical workflow check. |

Codex owns implementation and deployment coordination. Claude Code is the preferred independent reviewer; a review requires a concrete packet and explicit request, not a stale pointer or an assumed background poll. Log receipt and disposition when each actual result arrives. Robert supplies real workflow feedback when using the beta; routine implementation choices should not interrupt him.

## What we learn

For each real use record: whether the intended destination and audience were clear; whether the message/artifact was durably saved; whether the correct person saw and acted on a request; whether its result was acknowledged without manual relay; whether Robert could find the material again; and any unexplained model call, cost, stale status or permission failure. Keep a small evidence log of actual sessions, operations, defects, fixes and retests. State observations plainly; do not invent uptime or reliability percentages from a few sessions.

## v0.9 release boundary

v0.9 is the production-ready scope demonstrated by these workflows, not shorthand for every future feature in the full spec. Freeze an explicit capability list and known limitations. A2 standing read, A3 delegated portal filing, exact-record search, managed launch and full CoS reasoning can enter only as their own verified slices are ready. Keep CoS progress active after the platform contract is stable, but do not let unfinished later automation prevent useful human/client record work.

Before promotion: applicable automated tests pass; independent review findings are resolved or explicitly dispositioned; the real presentation/build workflows have several days of useful beta evidence with critical issues fixed; deployed bytes/config match the reviewed candidate; existing sessions/receipts survive; account/domain/session permission and revocation checks pass; backup/restore and rollback are documented and exercised appropriately; model routes and supported runtime limits are truthful. Do not carry dev-only identity assumptions or credentials into production. Record remaining limitations in the release notes through the repository’s authorized documentation process.

This plan records a target, not a scheduled unattended deployment, proof the beta is ready, or approval of an as-yet-unreviewed production diff. Development work proceeds under Robert’s standing authorization; production promotion follows the applicable approval and reviewed-diff process.
