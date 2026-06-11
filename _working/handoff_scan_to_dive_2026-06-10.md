# Handoff — Scan → Deeper Dive (one-click)
*mini-moi · Curator*
*Authored: 2026-06-10 — Claude.ai · Minimal build, no redesign*

---

## What it does

Adds a single "→ Generate Deeper Dive" link on the scan detail page.
One click creates a thread from the scan and immediately runs the dive.
Thread appears on Desk. Dive appears in Scans & Dives under DIVES.

---

## Backend — one new route

```python
@app.route("/curator/scan/<int:scan_id>/dive", methods=["POST"])
def scan_to_dive(scan_id):
    # 1. Load the scan
    scan = get_scan(scan_id)  # existing scan lookup

    # 2. Auto-generate thread slug from article title
    import re
    slug = re.sub(r'[^a-z0-9]+', '-', scan.title.lower()).strip('-')[:40]
    # Ensure uniqueness — append scan_id if slug already exists
    if thread_exists(slug):
        slug = f"{slug}-{scan_id}"

    # 3. Create the thread record (same as manual thread creation)
    thread_id = create_thread(slug=slug, source_scan_id=scan_id)

    # 4. Add scan article as the thread's first session
    add_session_from_scan(thread_id=thread_id, scan=scan)

    # 5. Run generate_dive.py (same as Desk → Generate Deeper Dive flow)
    generate_dive(thread_id=thread_id)

    # 6. Redirect to Scans & Dives — dive will appear under DIVES
    return redirect(url_for("curator_scans_dives"))
```

Use the exact function names from the existing codebase — the above is pseudocode
showing intent. Read the file before writing.

---

## UI — one link on scan detail page

Add to the scan detail template, alongside existing action links:

```html
{% if not scan.has_dive %}
<form method="POST" action="/curator/scan/{{ scan.id }}/dive" style="display:inline;">
  <button type="submit"
          style="background:none; border:none; cursor:pointer;
                 color:#C68A5E; font-family:inherit; font-size:inherit;">
    → Generate Deeper Dive
  </button>
</form>
{% endif %}
```

Hide the link once a dive exists for this scan (`scan.has_dive` or equivalent).

---

## Desk behaviour

Thread appears in NOT YET RUN on creation (or ACTIVE if session is added before
the Desk page refreshes). No special handling needed — the auto-created thread
is a normal thread and the Desk renders it the same way as any other.

Robert can rename the slug, add more sessions, or leave it as a one-off.

---

## Definition of done

- [ ] Scan detail page shows "→ Generate Deeper Dive" link
- [ ] Clicking it creates a thread visible on Desk
- [ ] Dive output appears in Scans & Dives under DIVES
- [ ] Link hidden after dive exists for that scan
- [ ] Existing scan and dive flows unaffected
- [ ] Robert confirms it works end-to-end on one real scan

---

## Commit

```bash
git add [curator route file] [scan detail template]
git commit -m "feat: scan → deeper dive one-click — creates thread and runs dive from scan page"
git push origin main
```

---

*Scan → Dive · minimal · 2026-06-10*
