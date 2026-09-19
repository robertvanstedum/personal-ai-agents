# Connected work: local craft, central continuity

Edition 1.0 · 19 September 2026 · Current directional baseline; see [status and authority](README.md).

## 1. Purpose and executive roles

Mini-moi must preserve Robert's thinking and practical work across sessions, agents and machines. Local agents remain valuable because they can operate on local files. Production Chief of Staff must be able to follow authorized work while it is underway, not merely read accepted outputs afterward. A laptop is a place of productive work, not automatically a test environment or an always-on host.

Robert is owner and decision-maker. CoS provides personal continuity across domains. The to-be Master Craftsman provides technical stewardship. These are the executive roles; the local agents are contributors operating under bounded mandates, not a second local executive team. Development and testing of mini-moi use designated dev environments before production promotion.

Local ownership and offline usefulness draw on REF-001; this is alignment with selected local-first principles, not a claim of complete local-first implementation or a requirement for CRDTs.

## 2. Domain ownership and places of work

| Domain / area | Responsibility |
|---|---|
| CoS / Planning Studio | Intake, thinking, research, drafts, planning and continuity; cross-domain initiatives with named stewards |
| Guild / Workshop | Implementation, code review, testing and maintained capabilities |
| Guild / Prototype Lab | Experiments, demonstrations and evaluation; prototype metadata and a stable repository home |

**Planning Studio is under CoS by Robert's explicit direction.** This supersedes the earlier Guild-governed ownership formulation. Guild supplies technical review, build quality and promotion standards, not general ownership of Planning Studio. Domain ownership does not automatically grant access to every initiative or move its files.

Guild's existing Build / Operate / Improve / Experiment organization remains useful. Workshop primarily supports Build; Prototype Lab fits Experiment. A prototype can live at `prototype-lab/projects/<name>/` with purpose, owner, initiative link, status, run instructions, data restrictions and successor links. Tags provide discovery. No additional service or subdomain is required merely to establish the area. A deployed project in that directory is not disposable simply because of its location.

CoS's existing Record / Track / Store labels are placeholders, not architectural constraints. Agenda, priorities, vision and professional concerns need later information design. Career is an enduring subject area for resumes, opportunities, case studies and presentations; a resume does not belong exclusively to one presentation initiative. Planning Studio is where work on it happens, not a replacement for its subject identity.

## 3. Shared records and bounded meetings

Rooms are persistent topic containers. Sessions are deliberately opened, bounded recorded discussions; quiet rooms do not run agents. One-to-one thinking, multi-agent review and incident bridges share the record contract. Future headless coordination is a separate capability, not implied by room membership.

Transcripts preserve accepted contributions. Notes, build notes, recorded rationale, decisions and next steps are separately attributed/versioned and linked to source coverage. Hidden model reasoning and unsubmitted drafts are not captured. Formal Work dispositions and execution approvals remain separate from statements made in discussion.

One logical collection supports CoS and Master Craftsman through authorized views. It does not mean one physical database, one blanket permission or duplicate executive-owned stores. Authored source/design files keep their canonical versioned-file identity. Accepted session events and their Records notes have SQLite authority, with derived exports. References connect these record classes; an export does not silently transfer authority. This scopes the amendment to the old file-only charter rule.

The native UI remains a supported testing and fallback interface. Telegram and optional open-source collaboration products are surfaces with their own adapters. A channel message is not automatically an authoritative write. REF-005 supports provenance distinctions; REF-007 supplies a limited channel/business-authorization analogy, not receipt equivalence.

## 4. Central continuity without two writers

Local capture works without central connectivity. Authorized central read access is the first integration priority after the export/access prerequisites: one-way transfer, acknowledgment, declared origin/revision/coverage and snapshot age. It supports inspection and briefing, not commenting back into a room. No development-route artifact may be cited as evidence of production participation.

Central write participation is a separately reviewed authenticated service path, with receipts and explicit authority. A central PostgreSQL read projection cannot become a second writer of local transcript rows. Until that integration is accepted, production room participation and laptop-off writing are unavailable, not presumed from a dev demo. Release reports must state separately whether local rooms, central reading and central writing are delivered.

Historical source identity remains stable across restores and hosting changes. Current write authority/location is separate. A later authority-transfer operation needs an owner decision, fenced old writer, verified new writer, authority generation and receipt. If the old writer cannot be fenced, fail closed or use an explicitly reviewed disaster-recovery procedure; do not start both. New centrally authored records can have a distinct central origin without rewriting earlier local records.

## 5. Four operational claims

| Operation | Meaning | Evidence |
|---|---|---|
| Backup | Recoverability of inventoried material, including drafts | Consistent snapshot, off-device verification and performed restore |
| Export | Readable portable session snapshot | Valid JSON/Markdown bundle, manifest, coverage and revision |
| Synchronization | Authorized availability elsewhere | Destination acknowledgment, checkpoint, current access and freshness |
| Promotion | Accepted version at an authoritative destination | Robert's disposition, exact artifact identity and destination receipt |

Operations may overlap; each records its own pending/success/failure/uncertainty. There is no implicit global pipeline. Explicit dependencies reference exact versions/hashes; missing dependencies stay pending or fail visibly. A database backup need not await export; export-dependent synchronization cannot claim success before the referenced bundle exists. Promotion can fail even when a person is waiting.

Sources and drafts are not automatically promoted, but still need backup and may be authorized for central visibility. Sensitivity and transfer approval are separate from migration disposition. Unknown sources default deny. Restricted material stays denied until reviewed under explicit handling rules. Reference metadata may itself be sensitive; send only authorized information, including a minimal withheld-evidence indicator when necessary.

## 6. Reuse, experiments and preservation

Use the existing `pattern` and `promotion` lifecycle concepts for extracting reusable capabilities from initiatives. The public general capability is separate from private content/recipes. Record owner, version, provenance, interface, tests, licensing/privacy cleanup and destination. Promotion does not itself approve public release or production deployment.

Pin runtime and skill versions, resolved dependencies and symlink targets; preserve recoverable dependency sources as permitted. Demonstrate rebuilding outside a warm cache. Apply this to Records itself, not just presentation tools. A package-name list or cache path is insufficient.

Prototype Lab findings may lead to a reviewed implementation, reusable pattern or retirement. No experiment bypasses reviewed build and release gates. Do not force a directory move before the next useful build simply to express the conceptual organization.

SQLite adoption adds an operational responsibility: consistent snapshots (REF-002), protected off-device copies, usable restore tests (REF-003), inventory and independent missed-job observation. Copying a live DB into a sync folder or merely writing a status signal proves neither backup nor notification delivery.

## 7. Scope and sequencing

Build Records & Rooms against the detailed contract: local durable records, readable live/final artifacts, truthful agent participation and preservation. Design the central read slice early and deliver it at the first safe increment after its prerequisites; do not queue it behind the broad harness. It does not waive private-source gates or authorize a production change.

Later work separately proves central writes, laptop-off continuity, authority transfer, unattended worker/reviewer orchestration, additional workshops and alternative surfaces. Full H1 remains tracked and unpassed. There is no calendar promise. See the operations/acceptance document for ordered drops and gates.
