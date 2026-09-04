# Presentation slides — render and visual review record (2026-09-04)

The five slides served at `/presentation` are PNG renders of a five-slide
PowerPoint deck. Binary images cannot be text-scanned for retired names, so this
record and the checksum manifest are the release evidence for what they show.

## Source and render

| Item | Value |
|---|---|
| Source deck | `iot-connect-demo-presentation-5-slide-2026-09-04.pptx` in the retained design package's `presentation/pptx/` folder, a renamed copy of the 2026-09-01 five-slide agenda deck; the original file is preserved unchanged |
| Edit | slide 1 title only: "IoT Connect — Enterprise IoT Activation and Billing". No other slide text changed |
| Renderer | Microsoft PowerPoint (macOS) → PDF, then `pdftoppm -png -r 96` |
| Output | 1280 × 700 px per slide (slide size 13.33 × 7.29 in at 96 dpi), identical to the previous assets' dimensions |
| Files | `app/static/iotconnect/presentation/slide-1.png … slide-5.png` |
| Manifest | `app/static/iotconnect/presentation/CHECKSUMS.sha256` (SHA-256, five entries) — verified by `make scan` and by `tests/test_standalone_packaging.py` |

All five slides were re-rendered from the one source so that typography is
consistent across the deck: the previous renders had used a substitute font for
the deck's typeface, and replacing slide 1 alone would have left a visible
mismatch. Slides 2–5 carry no product name and their text is unchanged.

## Visual inspection (Claude Code, 2026-09-04)

| Slide | Inspected | Result |
|---|---|---|
| 1 | new render, side by side with the previous slide 1 | Title reads "IoT Connect — Enterprise IoT Activation and Billing"; all body text, the decision-request panel and the four metrics are intact; no clipping, overflow or overlap |
| 2 | new render, side by side with the previous slide 2 | Same content ("Meeting agenda"); only the typeface rendering differs |
| 3–5 | new renders | Content matches the source deck; no product name present |

Any change to a slide requires: re-render from the source deck, visual
inspection recorded here, and a regenerated `CHECKSUMS.sha256`.
