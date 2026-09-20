# INC004-A — typed transcript format, not yet the store exporter

Implements the rendering/validation portion of the published Transcript Contract
1.0. `transcript_format.SCHEMA` is a Draft2020-12 JSON Schema, assembled from local
definitions; no remote schema resolution. `validate` adds within-snapshot identity,
sequence, participant, note-chain and link checks. `render(snapshot, snapshot_at=...)`
returns UTF-8 `transcript.json` and `transcript.md` bytes from one copied input.

Scope is deliberately narrower than full INC004/R1: this module does not read
SQLite, assign an origin or revision, publish files, install routes, import into
PostgreSQL/Neo4j, or establish authorization. The next slice must obtain a single
authorized database snapshot with store-owned source identity/revision and map
legacy records honestly. Until then the existing UI exports are unchanged.

Only full-owner scope is supported by this first typed contract. Do not represent
a filtered snapshot as complete. Sequence gaps mean missing sequence numbers in
this session, not necessarily lost submissions (the source sequence is global).
The caller declares unknown legacy timestamps and any capture omissions.

Markdown body text uses dynamic fenced literal blocks: original multiline text
stays intact, and HTML/links/headings inside it are not executed as markup.
Inline labels are escaped. Notes and references are separate; reference targets
are displayed as inert data, never fetched. JSON-like connector body strings
remain literal text; this module never infers execution evidence from them.

Speaker display labels remain escaped for safe display. Each attribution header
also contains the exact machine identity, e.g. `speaker=cos-agent-a`, inside the
existing bracket group. Notes similarly include `author=cos-agent-a`. Literal
search for those IDs therefore finds attribution lines without an index or extra
blocks. Actor identities are validated locally against registration's lowercase
letter/digit/underscore/hyphen alphabet and 60-character limit, including rejection
of trailing newlines. JSON retains exact display names and is the structured
source for searches of arbitrary display-name punctuation; Markdown's unescaped
machine identities and literal message bodies support ordinary text search.

Verified provenance fields are shape/link evidence requirements, not a verifier:
callers must establish verification separately. A valid document does not grant
authority, approve decisions or prove an actual model ran. The renderer does not
screen arbitrary text for secrets; capture/export authorization remains upstream.

JSON bytes are deterministic under collection reordering. Markdown includes the
caller-supplied snapshot time; publication must reuse that captured time, not
substitute the time of each render. Generated-at and file digests belong to the
later publication manifest. Snapshot revision, through-sequence and state are
included in the readable header. No automatic publication is claimed here.
