"""Owner-only read/export of retained voice evidence, never model reconstruction."""
import re
from flask import current_app, request
from . import owner_api
from .security import json_error
from . import mc_voice_export


@owner_api
def download(cid):
    from . import api
    from .conversations import ConversationNotFound, ConversationStoreUnavailable
    principal = api._principal()
    store = api._conversations()
    def authorized():
        conv = store.get(cid, principal)
        if (conv.get('principal') != principal or conv.get('archived')
                or conv.get('record_mode', 'on_record') != 'on_record'
                or not conv.get('notes_floor')):
            raise ConversationNotFound(cid)
        return conv
    raw = request.args.get('before')
    if raw is not None and (len(raw) > 19 or not re.fullmatch(r'[1-9][0-9]*', raw)):
        return json_error('invalid', 'Invalid transcript cursor.', 422)
    try:
        conv = authorized()
        floor = api._conv_floor(conv)
        from .mc_voice_reveal import Hider
        process = api._voice_view()
        result = mc_voice_export.render(floor, before=int(raw) if raw is not None else None, scrub=api.scrub,
                                        withheld=Hider(floor, live=process["live"], direct=process["known"]).hidden)
        fresh = authorized()
        if any(fresh.get(k) != conv.get(k) for k in ('principal', 'notes_floor', 'scope')):
            raise ConversationNotFound(cid)
    except ConversationNotFound:
        return json_error('not_found', 'This on-record conversation is unavailable.', 404)
    except (ConversationStoreUnavailable, mc_voice_export.ExportUnavailable):
        return json_error('unavailable', 'Saved voice transcript is unavailable.', 503)
    response = current_app.response_class(result['text'], mimetype='text/plain')
    response.headers['Content-Disposition'] = 'attachment; filename="voice-transcript.txt"'
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
    response.headers['X-Transcript-Partial'] = str(result['truncated']).lower()
    if result['next_before'] is not None:
        response.headers['X-Transcript-Next-Before'] = str(result['next_before'])
    return response
