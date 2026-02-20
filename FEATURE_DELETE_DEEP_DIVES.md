# Feature Request: Delete Deep Dive

**Status:** Requested (Feb 20, 2026)
**Priority:** Medium
**Requested by:** User

## Overview

Add the ability to delete deep dive analyses from the archive interface to allow cleanup of mistaken or low-quality deep dives.

## Use Cases

1. **Mistaken Deep Dives** - User accidentally triggered deep dive on wrong article
2. **Low-Quality Output** - AI generated poor analysis that's not worth keeping
3. **General Cleanup** - Periodic maintenance to remove outdated or irrelevant analyses
4. **Privacy** - Remove deep dives containing sensitive research interests

## Proposed Implementation

### UI Addition

**Location:** `interests/2026/deep-dives/index.html` (Deep Dive Archive page)

**Button:** Add trash icon (🗑️) next to each deep dive entry in the table

**Visual Design:**
- Small, subtle button (similar to existing action buttons)
- Gray color by default, red on hover
- Positioned in "Action" column (or add new column)

### User Flow

1. User clicks trash icon (🗑️) on a deep dive entry
2. Confirmation prompt appears: "Delete this deep dive? This cannot be undone."
3. If confirmed:
   - Remove entry from `index.html`
   - Optionally delete associated HTML/MD files
   - Show success toast notification
4. If canceled:
   - No action taken

### Backend Implementation

**Option 1: Client-Side Only (Simple)**
- JavaScript removes the `<tr>` from the table
- HTML file remains but no longer appears in index
- Pro: No server needed, instant feedback
- Con: Files remain on disk, index regeneration overwrites deletions

**Option 2: Server Endpoint (Robust)**
- Add `/delete_deepdive?hash_id=xxxxx` endpoint to `curator_server.py`
- Server removes entry from `curator_history.json` (set `deleted: true` flag)
- Optionally move HTML/MD to `.trash/` directory
- Regenerate index without deleted entries
- Pro: Persistent deletions, proper cleanup
- Con: Requires server running

**Recommendation:** Option 2 (Server Endpoint) for production

## Technical Details

### Files to Modify

**1. Deep Dive Index Template** (`curator_feedback.py` - `regenerate_deep_dives_index()`)
```html
<td class="col-actions">
    <a href="{html_rel_path}" class="btn-view">📄 View</a>
    <button class="btn-delete" onclick="deleteDive('{hash_id}')" title="Delete this deep dive">🗑️</button>
</td>
```

**2. JavaScript Function** (in index.html template)
```javascript
function deleteDive(hashId) {
    if (!confirm('Delete this deep dive? This cannot be undone.')) {
        return;
    }
    
    fetch('http://localhost:8765/delete_deepdive?hash_id=' + hashId)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Remove row from table
                document.querySelector(`tr[data-hash-id="${hashId}"]`).remove();
                showToast('Deep dive deleted', 'success');
            } else {
                showToast('Delete failed: ' + data.message, 'error');
            }
        });
}
```

**3. Server Endpoint** (`curator_server.py`)
```python
elif parsed.path == '/delete_deepdive':
    params = urllib.parse.parse_qs(parsed.query)
    hash_id = params.get('hash_id', [''])[0]
    
    if not hash_id:
        self.send_error(400, "Missing hash_id")
        return
    
    result = self.delete_deepdive(hash_id)
    
    self.send_response(200)
    self.send_header('Content-type', 'application/json')
    self.send_header('Access-Control-Allow-Origin', '*')
    self.end_headers()
    self.wfile.write(json.dumps(result).encode())

def delete_deepdive(self, hash_id):
    """Delete a deep dive from history and optionally remove files"""
    try:
        history_file = Path(__file__).parent / "curator_history.json"
        
        with open(history_file, 'r') as f:
            history = json.load(f)
        
        if hash_id not in history:
            return {'success': False, 'message': 'Article not found'}
        
        # Mark as deleted (or remove entry entirely)
        history[hash_id]['deleted'] = True
        history[hash_id]['deleted_at'] = datetime.now().isoformat()
        
        with open(history_file, 'w') as f:
            json.dump(history, f, indent=2)
        
        # Optionally move HTML/MD to trash
        # (implementation here)
        
        # Regenerate index without deleted entries
        from curator_feedback import regenerate_deep_dives_index
        regenerate_deep_dives_index()
        
        return {'success': True, 'message': 'Deep dive deleted'}
    except Exception as e:
        return {'success': False, 'message': str(e)}
```

**4. History Filter** (`curator_feedback.py` - `regenerate_deep_dives_index()`)
```python
# Filter out deleted deep dives
for hash_id, data in history.items():
    if data.get('deleted', False):
        continue  # Skip deleted entries
    
    if data.get('deep_dive_path'):
        # ... existing code
```

## Edge Cases

1. **Deleted file already gone** - Check file exists before trying to delete
2. **History corruption** - Validate JSON before/after modifications
3. **Server not running** - Show helpful error: "Server required for delete"
4. **Concurrent deletions** - Lock history file during modification
5. **Undo support** - Optional: soft delete with "deleted: true" flag + restore button

## Alternative: Soft Delete

Instead of removing files entirely:
- Set `deleted: true` flag in history
- Keep files on disk in case of accidental deletion
- Add "Show Deleted" toggle to view trash
- Add "Restore" button for deleted entries
- Periodic cleanup script to purge old deleted entries

## Testing Checklist

- [ ] Delete button appears on each deep dive row
- [ ] Confirmation prompt works correctly
- [ ] Server endpoint receives correct hash_id
- [ ] History file updated correctly
- [ ] Index regenerates without deleted entry
- [ ] Toast notification shows success/error
- [ ] Files optionally moved to .trash/
- [ ] Handle case where server not running
- [ ] Handle case where file already deleted
- [ ] Multiple deletions work correctly

## Implementation Estimate

**Simple (client-side only):** 30 minutes
**Robust (server endpoint):** 1-2 hours
**With soft delete/restore:** 2-3 hours

## Open Questions

1. **Hard vs soft delete?** Should files be permanently deleted or moved to trash?
2. **Confirmation style?** Browser `confirm()` dialog or custom modal?
3. **Batch delete?** Support selecting multiple entries for deletion?
4. **Undo support?** Should we keep deleted entries recoverable for some period?

## Status

🟡 **Awaiting Implementation** - Design documented, ready for development when prioritized

## Related Features

- Archive cleanup (auto-delete old deep dives)
- Export/backup deep dives
- Search/filter deep dives archive
- Batch operations (delete multiple at once)
