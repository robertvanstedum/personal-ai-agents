"""Trusted kept-owner-note seam. No routes, model inference, source fetch or new grant."""
import re
from core import library_intake_transport as transport
from domains.curator.library_intake import canonical_url

PREFIX=re.compile(r'^(?:please\s+)?save this to library\s*:\s*',re.I)
URL=re.compile(r'https?://[^\s]+',re.I)
FAIL={'status':'unconfirmed','reply':'Library filing was not confirmed. Check Library before another save. No source content was fetched.','operation':{'type':'library_intake','saved':False}}

def _unconfirmed(sent):
    """FAIL, saying whether the filing request had been sent (then its outcome is uncertain)."""
    out=dict(FAIL);out['operation']={**FAIL['operation'],'state':'uncertain' if sent else 'not_sent'}
    return out

def parse(text):
    if not isinstance(text,str):return None
    value=text.strip();match=PREFIX.match(value)
    if match:value=value[match.end():]
    elif not re.match(r'^https?://',value,re.I):return None
    lines=value.splitlines();candidate=lines[0].strip()if lines else ''
    if len(URL.findall(value))!=1 or not URL.fullmatch(candidate):raise ValueError('library_single_url_required')
    canonical_url(candidate)
    comment='\n'.join(lines[1:]).strip()
    if len(comment)>4000:raise ValueError('library_comment_bound')
    return candidate,comment

def host_command(text,*,environ,principal,origin,conversation,note_id,sender=transport.send):
    try:parsed=parse(text)
    except ValueError:return _unconfirmed(False)
    if parsed is None:return None
    sent=False
    try:
        profile=transport.configured(environ)
        if profile is None:return _unconfirmed(False)
        if (principal!=profile.owner or origin!=profile.origin or not isinstance(conversation,dict)or conversation.get('principal')!=principal or conversation.get('record_mode')!='on_record'or conversation.get('archived')):return _unconfirmed(False)
        # Caller must recheck kept note ownership and CSRF before invoking this seam.
        from utils.credential_scrub import scrub
        from .payment_scrub import scrub as payments
        clean=payments(scrub(text)[0]);parsed=parse(clean)
        if parsed is None:return _unconfirmed(False)
        value=transport.payload(profile,conversation['id'],note_id,*parsed)
        sent=True
        receipt=sender(profile,value)
        if not isinstance(receipt,dict)or receipt.get('source_content')!='not_fetched'or receipt.get('result')not in {'saved','already_saved_attached'}:return _unconfirmed(True)
        reply=('Already in Library; attached your supplied metadata.'if receipt['result']=='already_saved_attached'else'Saved to Library.')+' Source content was not fetched; tags remain unclassified.'
        return {'status':'saved','reply':reply,'operation':{'type':'library_intake','saved':True,'receipt':receipt}}
    except Exception:return _unconfirmed(sent)
