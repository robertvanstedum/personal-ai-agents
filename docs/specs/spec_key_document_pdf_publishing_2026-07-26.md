# Key-document PDF publishing

**Status:** Approved and in build  
**Date:** 2026-07-26  
**Parent work:** Repository Application Reorganization Phase 2, item 140

## Purpose

The repository root keeps four maintained documents for visitors and operators:

- `README.md`
- `ARCHITECTURE.md`
- `OPERATIONS.md`
- `ROADMAP.md`

Each document also has a formatted, shareable PDF beside it. Markdown remains the
only editable source. The PDFs are generated publications and must not drift from
their sources.

## Permanent root pairs

| Source | Publication |
|---|---|
| `README.md` | `README.pdf` |
| `ARCHITECTURE.md` | `ARCHITECTURE.pdf` |
| `OPERATIONS.md` | `OPERATIONS.pdf` |
| `ROADMAP.md` | `ROADMAP.pdf` |

Each Markdown source links to its PDF. README links to the other three maintained
documents as it does today.

## Publishing command

The repository-owned command is:

```bash
python3 scripts/docs/publish_key_docs.py
```

Supporting renderer code, styles, lockfiles, and Python requirements live under
`scripts/docs/`. No private Codex or Claude tooling and no hosted rendering
service are required.

Before the first run on a machine:

```bash
npm ci --prefix scripts/docs
npx --prefix scripts/docs playwright install chromium
python3 -m pip install -r scripts/docs/requirements.txt
```

## Rendering contract

The publisher:

1. Reads the four root Markdown files.
2. Converts GitHub-flavored Markdown to HTML.
3. Converts every Mermaid block to an SVG in a local headless browser.
4. Applies the shared mini-moi print stylesheet.
5. Produces letter-sized PDFs with page numbers, a repository footer, working
   links, restrained color, readable tables, and protected page breaks around
   diagrams and images.
6. Embeds source and renderer SHA-256 values in PDF metadata.
7. Records source hash, renderer hash, diagram count, page count, and PDF hash in
   `scripts/docs/key-docs-manifest.json`.
8. Verifies that the browser rendered exactly as many diagrams as the source
   contains before accepting a PDF.

## Push and deployment gate

The non-writing check is:

```bash
python3 scripts/docs/publish_key_docs.py --check
```

It verifies:

- all four PDFs exist;
- every embedded source hash matches its Markdown source;
- every embedded renderer hash matches the checked-in renderer;
- the manifest matches the source, renderer, diagram count, page count, and
  actual PDF bytes;
- each PDF contains pages and extractable text.

The existing GitHub `test` job runs this check. A stale or missing publication
therefore blocks the image build and production deployment. GitHub Actions does
not silently modify a branch or create a follow-up commit. The author runs the
publishing command and commits the source and publication together.

## Visual verification

For initial implementation and meaningful stylesheet changes:

1. Render every PDF page to PNG.
2. Inspect every page for clipping, overlap, missing images, broken typography,
   blank areas, and poor page breaks.
3. Inspect each Mermaid diagram at readable size.
4. Regenerate and repeat until no visual defects remain.

Routine text-only document changes still require `--check`; the publishing
command refreshes the PDFs before commit.

## Failure and recovery

- Publishing writes to a temporary directory and replaces root PDFs only after
  each document renders and validates.
- A failed document leaves its previously committed PDF untouched.
- Reverting the source, four PDFs, manifest, and publishing-tool commit restores
  the prior documentation set. No application runtime or production data is
  involved.

## Acceptance

- A clean checkout can generate all four PDFs with the documented process.
- All 18 current Mermaid diagrams appear as diagrams, not source code.
- The four PDF links work on GitHub.
- A deliberate Markdown edit makes `--check` fail.
- Republishing makes `--check` pass.
- The full application test suite and documentation check pass.
