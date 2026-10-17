"""Owner-directed intake seam into CURRENT Curator Library preferences.
No fetch, model, metadata inference, bookmarks/deep dives or additional database.
Called only after the host's authenticated owner/record-mode checks.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit,urlunsplit
from . import preferences_store as prefs

class IntakeRefused(ValueError):pass

def need(ok,code):
    if not ok:raise IntakeRefused(code)

def canonical_url(value):
    need(isinstance(value,str)and 0<len(value)<=3000 and not any(ord(c)<33 for c in value),'library_url_invalid')
    try:
        u=urlsplit(value);need(u.scheme.lower()in {'http','https'}and u.hostname and not u.username and not u.password,'library_url_invalid')
        host=u.hostname.lower();host='['+host+']'if ':'in host else host
        port=u.port
        if port and (u.scheme.lower(),port)not in {('https',443),('http',80)}:host+=':'+str(port)
        return urlunsplit((u.scheme.lower(),host,u.path or'/',u.query,u.fragment))
    except ValueError:raise IntakeRefused('library_url_invalid')from None

def words(value,limit,*,optional=False):
    return value is None and optional or isinstance(value,str)and (optional or len(value)>0)and len(value)<=limit and '\x00'not in value

def labels(values):
    need(type(values)is list and len(values)<=20 and all(words(v,60)for v in values),'library_labels_invalid')
    need(all(v.strip() for v in values),'library_labels_invalid')
    return sorted(set(v.strip()for v in values))

class LibraryIntake:
    def __init__(self,preferences_path,*,history_path):
        self.history_path=history_path # trusted existing curator_server.HISTORY_PATH
        self.path=preferences_path # trusted configured Curator _DATA_DIR, not model args

    def capture(self,*,principal,on_record,operation_id,url,comment='',title=None,publisher=None,publication_date=None,sections=None,tags=None,source_claims=None,interpretation=None):
        need(principal=='robert'and on_record is True,'library_owner_on_record_required')
        need(words(operation_id,160),'library_operation_id_required')
        need(words(comment,4000,optional=True)and words(title,200,optional=True)and words(publisher,200,optional=True)and words(publication_date,80,optional=True),'library_metadata_invalid')
        need(words(source_claims,6000,optional=True)and words(interpretation,6000,optional=True),'library_attribution_invalid')
        canonical=canonical_url(url);sections=labels(sections or['Unclassified']);tags=labels(tags or[])
        # Missing source metadata remains missing; URL/host are not claimed as a
        # fetched title/publisher or successful source retrieval.
        request={'url':canonical,'original_url':url,'comment':comment,'title':title,'publisher':publisher,'publication_date':publication_date,'sections':sections,'tags':tags,'source_claims':source_claims,'interpretation':interpretation}
        fingerprint=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest();key=hashlib.sha256(operation_id.encode()).hexdigest();now=datetime.now(timezone.utc).isoformat()
        with prefs.locked(self.path):
            data=prefs.load(self.path);history=data['feedback_history'];matches=[]
            operations=data.setdefault('owner_library_operations',{})
            need(type(operations)is dict and len(operations)<=4096,'library_operations_invalid')
            if key in operations:
                prior=operations[key];need(prior['request_sha256']==fingerprint,'library_idempotency_conflict')
                prefs.confirm(self.path,data.original_sha256)
                return {**prior,'repeated':True}
            need(len(operations)<4096,'library_operation_bound')
            for day in history.values():
                need(type(day)is dict,'library_existing_schema_invalid')
                for kind in ('liked','saved'):
                    rows=day.get(kind,[]);need(type(rows)is list,'library_existing_schema_invalid')
                    for row in rows:
                        need(type(row)is dict,'library_existing_schema_invalid')
                        if row.get('url'):
                            try:same=canonical_url(row['url'])==canonical
                            except IntakeRefused:same=False
                            if same:matches.append(row)
            bookmarks=prefs.library_history(self.history_path)
            bookmarked=next(((identity,row)for identity,row in bookmarks.items()if isinstance(row,dict)and row.get('bookmarked')and row.get('url')and self._same_url(row['url'],canonical)),None)
            # Existing Library API already merges by URL; annotate its oldest
            # canonical preference entry instead of a new duplicate row.
            entry=matches[0]if matches else None;duplicate=entry is not None or bookmarked is not None
            if entry is None:
                identity=str(bookmarked[0])if bookmarked else 'owner-'+hashlib.sha256(canonical.encode()).hexdigest()[:24]
                entry={'article_id':identity,'url':bookmarked[1]['url']if bookmarked else canonical,'title':title or (bookmarked[1].get('title')if bookmarked else None)or canonical,'source':publisher or'Unknown publisher','category':'owner_intake','timestamp':now,'your_words':comment,'saved_from':'owner_library_intake'}
                history.setdefault(now[:10],{'liked':[],'saved':[]}).setdefault('saved',[]).append(entry)
            intake=entry.setdefault('owner_intake',{'revision':0,'operations':{},'comments':[],'annotations':[]})
            need(type(intake)is dict and set(intake)=={'revision','operations','comments','annotations'}and type(intake['revision'])is int and type(intake['operations'])is dict and type(intake['comments'])is list and type(intake['annotations'])is list,'library_intake_state_invalid')
            if key in intake['operations']:
                prior=intake['operations'][key];need(prior['request_sha256']==fingerprint,'library_idempotency_conflict')
                prefs.confirm(self.path,data.original_sha256)
                return {**prior,'repeated':True}
            need(len(intake['operations'])<512 and len(intake['comments'])<512 and len(intake['annotations'])<512,'library_item_history_bound')
            revision=intake['revision']+1
            if comment:intake['comments'].append({'by':principal,'at':now,'text':comment})
            intake['annotations'].append({'submitted_by':principal,'metadata_origin':'owner_supplied_not_fetched','at':now,'title':title,'publisher':publisher,'publication_date':publication_date,'sections':sections,'tags':tags,'source_claims':source_claims,'agent_interpretation':interpretation,'source_content':'not_fetched','original_url':url})
            intake['revision']=revision
            receipt={'result':'already_saved_attached'if duplicate else'saved','article_id':entry.get('article_id')or entry.get('url'),'url':canonical,'revision':revision,'path':str(self.path),'library_link':'/app/curator/curator_library.html','request_sha256':fingerprint,'source_content':'not_fetched','tags_status':'descriptive_correctable','tags':tags,'sections':sections,'metadata_origin':'owner_supplied_not_fetched','repeated':False}
            intake['operations'][key]=receipt
            operations[key]=receipt
            prefs.write(self.path,data)
            return receipt

    @staticmethod
    def _same_url(value,canonical):
        try:return canonical_url(value)==canonical
        except IntakeRefused:return False
