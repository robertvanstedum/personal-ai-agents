import sqlite3
import pytest
from floor_helpers import load_portal, staging, write_headers
from minimoi_portal.guild_ui.library import canonical_url, database, save, listing

API='/guild-next/api/v1/library/links'
PAGE='/guild-next/guild/improve'

def payload(**kw):
    return {'url':'https://EXAMPLE.com:443/article?q=a#part','title':'Database options','tags':['SQL',' SQL '], 'note':'Compare before choosing', 'revision':0, 'idempotency_key':'library-save-0001', **kw}

def test_save_dedup_edit_conflict_search_and_guards(staging):
    c=staging.owner(); headers=write_headers(staging.csrf(c))
    first=c.post(API,json=payload(),headers=headers)
    assert first.status_code==200
    assert first.json['url']=='https://example.com/article?q=a#part'
    assert c.post(API,json=payload(),headers=headers).json==first.json
    assert c.post(API,json=payload(idempotency_key='library-other-01'),headers=headers).status_code==409
    assert c.post(API,json=payload(title='Different'),headers=headers).status_code==409
    edit=payload(revision=1,title='Database update',tags=['storage'],idempotency_key='library-edit-001')
    assert c.post(API,json=edit,headers=headers).json['revision']==2
    assert c.post(API,json={**edit,'idempotency_key':'library-stale-01'},headers=headers).status_code==409
    assert b'Database update' in c.get(PAGE+'?q=update&tag=storage').data
    assert b'No matching references.' in c.get(PAGE+'?tag=sql').data
    assert c.post(API,json=payload(),headers=write_headers(staging.csrf(c),mode='off_record')).status_code==409
    assert c.post(API,json=payload()).status_code==403
    for client in (staging.guest(),staging.client()):
        assert client.post(API,json=payload()).status_code in (401,403)
        assert client.get(PAGE).status_code in (302,403)

@pytest.mark.parametrize('url',['javascript:alert(1)','https://u:p@example.com','file:///etc/passwd','https://example.com:99999/','https://example.com/ a'])
def test_unsafe_links_rejected(url):
    with pytest.raises(ValueError): canonical_url(url)

def test_meaningful_query_and_fragments_preserved():
    assert canonical_url('https://example.com/?a=1&b=2') != canonical_url('https://example.com/?a=2&b=2')
    assert canonical_url('https://example.com/#one') != canonical_url('https://example.com/#two')


def test_owner_isolation_provenance_and_safe_render(staging):
    c=staging.owner(); headers=write_headers(staging.csrf(c))
    assert c.post(API,json=payload(title='<script>bad</script>',note='<img src=x onerror=bad()>'),headers=headers).status_code==200
    html=c.get(PAGE).get_data(as_text=True)
    assert '<script>bad</script>' not in html and '&lt;script&gt;bad&lt;/script&gt;' in html
    with staging.app.test_request_context(PAGE):
        # Blueprint binding is set by routing in this test context.
        db=database()
        try:
            assert listing(db,'another-owner')[0]==[]
            rows=db.execute('SELECT * FROM links').fetchall()
            assert len(rows)==1 and rows[0]['original_url']=='https://EXAMPLE.com:443/article?q=a#part'
        finally: db.close()
