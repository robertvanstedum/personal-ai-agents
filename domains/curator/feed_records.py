"""Normalize already parsed RSS records; networking belongs to the adapter."""
from datetime import datetime, timezone
import hashlib

def normalize_feed_entries(name, feed_entries):
    entries = []

    for entry in feed_entries[:50]:
        pub_date = None
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
            pub_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)

        # Generate stable hash ID from URL
        url = entry.get("link", "")
        hash_id = hashlib.md5(url.encode('utf-8')).hexdigest()[:5] if url else None

        entries.append({
            "hash_id": hash_id,  # Stable ID for history tracking
            "source": name,
            "title": entry.get("title", "No title"),
            "link": entry.get("link", ""),
            "summary": entry.get("summary", ""),
            "published": pub_date,
            "raw_entry": entry
        })

    # Cap high-volume feeds to prevent flooding the candidate pool
    if name == "arXiv q-fin":
        entries = entries[:15]

    return entries
