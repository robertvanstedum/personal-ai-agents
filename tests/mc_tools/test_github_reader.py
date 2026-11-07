"""Synthetic GitHub responses only: fixed repository, complete bounded reads."""
import base64
import json
import unittest
from core.mc_tools.github import GitHubIssues, API, DEFAULT_REPO, BODY_CHARS, TEXT_BYTES, valid_request
from core.mc_tools.catalogue import validate, ArgumentsRefused

REF = 'a' * 40
BASE = 'b' * 40


class Reply:
    def __init__(self, data, status=200):
        self.status_code, self.data = status, data
        self.content = json.dumps(data).encode()
    def json(self):
        return self.data


class GitHubTests(unittest.TestCase):
    def reader(self, respond):
        self.requests = []
        def get(url, **kwargs):
            self.requests.append((url, kwargs))
            self.assertTrue(url.startswith(API + '/repos/' + DEFAULT_REPO + '/') or url == API + '/search/issues')
            self.assertFalse(kwargs['allow_redirects'])
            self.assertNotIn('Authorization', kwargs['headers'])
            return respond(url, kwargs.get('params'))
        return GitHubIssues(get)

    def test_description_continuation_complete_and_hash_checked(self):
        body = 'α' * (BODY_CHARS * 2 + 1)
        reader = self.reader(lambda url, _: Reply([] if url.endswith('/comments') else {'state': 'open', 'title': 'Issue', 'body': body}))
        parts, args = [], {'number': 7}
        while args:
            state, message, data = reader.issue(**{k:v for k,v in args.items() if k != 'operation'})
            self.assertEqual(state, 'ok')
            self.assertIn('https://github.com/'+DEFAULT_REPO+'/issues/7', message)
            parts.append(data['text']); args=data['next']
        self.assertEqual(''.join(parts), body)
        self.assertEqual(len(parts), 3)
        self.assertTrue(data['complete'])
        self.assertEqual(reader.issue(7, offset=1)[0], 'source_changed')
        self.assertEqual(reader.issue(7, offset=1, digest='0'*64)[0], 'source_changed')
        self.assertEqual(reader.issue(7, offset=len(body)+1, digest=data['sha256'])[0], 'invalid_window')

    def test_pr_status_and_links_not_issue_status_guess(self):
        def response(url, _):
            if url.endswith('/comments'):return Reply([])
            if '/pulls/' in url:return Reply({'state':'closed','merged':True,'head':{'sha':REF},'base':{'sha':BASE}})
            return Reply({'pull_request':{'url':'untrusted'},'body':'spec','state':'closed','html_url':'https://evil.example','title':'PR'})
        state, msg, data = self.reader(response).issue(12)
        self.assertEqual((state,data['kind'],data['state']), ('ok','pull request','merged'))
        self.assertIn('/pull/12',msg);self.assertIn(REF,msg);self.assertNotIn('evil',msg)

    def test_search_links_and_fixed_repository(self):
        row={'number':12,'title':'PR','state':'closed','pull_request':{},'repository_url':API+'/repos/'+DEFAULT_REPO}
        # GitHub supplies a nonempty PR descriptor.
        row['pull_request']={'url':'ignored'}
        reader=self.reader(lambda *_:Reply({'items':[row]}))
        state,msg,data=reader.search('career')
        self.assertEqual(state,'ok');self.assertIn('/pull/12',msg);self.assertIn('read PR to verify merge',msg)
        row['repository_url']=API+'/repos/other/repo'
        self.assertEqual(reader.search('repo:other/repo')[0],'unreadable')

    def test_source_exact_ref_encoded_path_and_hash_continuation(self):
        path='docs/a file.md';raw=('source\n' * 1500).encode()
        reader=self.reader(lambda *_:Reply({'type':'file','path':path,'encoding':'base64','size':len(raw),'content':base64.b64encode(raw).decode()}))
        state,msg,data=reader.source(REF,path)
        self.assertEqual(state,'ok');self.assertIn('/blob/'+REF+'/docs/a%20file.md',msg)
        self.assertTrue(self.requests[0][0].endswith('/contents/docs/a%20file.md'))
        self.assertEqual(self.requests[0][1]['params'],{'ref':REF})
        self.assertEqual(data['next']['ref'],REF)
        nxt=data['next'];state,msg,page=reader.source(**{k:v for k,v in nxt.items() if k!='operation'})
        self.assertEqual(state,'ok');self.assertEqual(page['sha256'],data['sha256'])
        self.assertEqual(reader.source(REF,path,offset=4000,digest='0'*64)[0],'source_changed')

    def test_invalid_utf8_binary_directory_and_oversize_are_not_text(self):
        for payload in ([{'type':'file'}], {'type':'file','path':'a','encoding':'none','size':1,'content':''},
                        {'type':'file','path':'a','encoding':'base64','size':TEXT_BYTES+1,'content':''},
                        {'type':'file','path':'a','encoding':'base64','size':1,'content':'AA=='},
                        {'type':'file','path':'a','encoding':'base64','size':1,'content':'/w=='}):
            with self.subTest(payload=payload):self.assertEqual(self.reader(lambda *_:Reply(payload)).source(REF,'a')[0],'unreadable')

    def test_pr_file_pages_are_verified_against_head_and_base(self):
        rows=[{'filename':f'src/{n}.py','status':'modified'} for n in range(10)]
        def response(url,params):
            if url.endswith('/files'):return Reply(rows if params['page']==1 else [])
            return Reply({'head':{'sha':REF},'base':{'sha':BASE}})
        reader=self.reader(response)
        state,msg,data=reader.files(REF,number=12)
        self.assertEqual(state,'ok');self.assertEqual(data['scope'],'PR aggregate')
        self.assertEqual(data['next'],{'operation':'files','ref':REF,'number':12,'base_ref':BASE,'page':2})
        self.assertEqual([r[0].split('/')[-1] for r in self.requests],['12','files','12'])
        state,_,last=reader.files(REF,number=12,page=2,base_ref=BASE)
        self.assertEqual(state,'ok');self.assertTrue(last['complete'])
        self.assertEqual(reader.files(REF,number=12,page=2)[0],'source_changed')
        self.assertEqual(reader.files('c'*40,number=12)[0],'source_changed')
        self.assertEqual(reader.files(REF,number=12,base_ref='c'*40)[0],'source_changed')

    def test_changed_pr_during_read_refuses_page(self):
        reads=[]
        def response(url,_):
            if url.endswith('/files'):return Reply([])
            reads.append(1)
            return Reply({'head':{'sha':REF if len(reads)==1 else 'c'*40},'base':{'sha':BASE}})
        self.assertEqual(self.reader(response).files(REF,number=12)[0],'source_changed')

    def test_commit_files_not_claimed_as_whole_pr_and_cap_explicit(self):
        reader=self.reader(lambda *_:Reply({'sha':REF,'files':[{'filename':f'{i}.py'} for i in range(10)]}))
        state,_,data=reader.files(REF,page=300)
        self.assertEqual(state,'ok');self.assertEqual(data['scope'],'single commit changes')
        self.assertTrue(data['limit_reached']);self.assertFalse(data['complete']);self.assertIsNone(data['next'])
        self.assertTrue(self.requests[0][0].endswith('/commits/'+REF))
        self.assertEqual(self.requests[0][1]['params'],{'per_page':10,'page':300})

    def test_schema_and_operation_combinations_reject_paths_refs_and_limits(self):
        for path in ('/tmp/x','../x','a/../b','https://example.com/a','a%2fb','a\\b','a//b','a?b'):
            self.assertFalse(valid_request({'operation':'source','ref':REF,'path':path}),path)
        for args in ({'operation':'source','ref':'main','path':'a'}, {'number':1,'offset':-1}, {'operation':'files','ref':REF,'page':301}):
            with self.assertRaises(ArgumentsRefused):validate('minimoi_github_issues',args)
        for args in ({'number':1,'query':'xx'}, {'operation':'files','ref':REF,'path':'a'}, {'operation':'source','ref':REF}, {}):
            self.assertFalse(valid_request(args),args)
        for args in ({'number':1},{'query':'xx'},{'operation':'source','ref':REF,'path':'docs/spec.md'}):
            self.assertTrue(valid_request(validate('minimoi_github_issues',args)))

    def test_the_calls_seen_on_dev_through_the_real_schema_and_validator(self):
        # Exact arguments MC sent on Dev, 7 Nov: refused for carrying page beside query, then for having no query.
        for args in ({'query':'sort:created-desc','operation':'search','page':1},
                     {'query':'is:issue sort:created-desc','operation':'search','page':1}):
            self.assertTrue(valid_request(validate('minimoi_github_issues',args)),args)
        self.assertFalse(valid_request(validate('minimoi_github_issues',{'operation':'search','page':1})))
        for args in ({'operation':'latest'},{'operation':'latest','page':2}):
            self.assertTrue(valid_request(validate('minimoi_github_issues',args)),args)
        for args in ({'operation':'latest','query':'xx'},{'operation':'latest','number':1},{'operation':'latest','ref':REF}):
            self.assertFalse(valid_request(validate('minimoi_github_issues',args)),args)
        self.assertFalse(valid_request(validate('minimoi_github_issues',{'query':'xx repo:someone/else'})))
        self.assertFalse(valid_request(validate('minimoi_github_issues',{'query':'xx repo:someone/else','page':2})))
        with self.assertRaises(ArgumentsRefused):validate('minimoi_github_issues',{'operation':'list'})

    def test_latest_lists_the_newest_issues_of_the_fixed_repo_only(self):
        row = {'number':472,'state':'open','title':'Newest','created_at':'2026-11-04T00:53:46Z','updated_at':'2026-11-05T00:00:00Z',
               'repository_url':f'{API}/repos/{DEFAULT_REPO}'}
        reader = self.reader(lambda url,_: Reply({'total_count':193,'items':[row]}))
        state,message,data = reader.latest()
        self.assertEqual(state,'ok')
        url,kw = self.requests[0]
        self.assertTrue(url.endswith('/search/issues'))
        self.assertEqual(kw['params'],{'q':f'repo:{DEFAULT_REPO} is:issue','sort':'created','order':'desc','per_page':8})
        self.assertIn('#472',message);self.assertIn('opened 2026-11-04',message)
        self.assertIn('https://github.com/'+DEFAULT_REPO+'/issues/472',message)
        self.assertEqual(data['results'][0]['kind'],'issue')
        reader.latest(page=3);self.assertEqual(self.requests[-1][1]['params']['page'],3)
        wrong = dict(row,repository_url=f'{API}/repos/someone/else')
        self.assertEqual(self.reader(lambda url,_: Reply({'items':[wrong]})).latest()[0],'unreadable')

    def test_service_runs_latest_and_a_bad_call_says_what_would_fit(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        from unittest import mock
        from core.mc_tools import service as svc
        row = {'number':472,'state':'open','title':'Newest','created_at':'2026-11-04T00:00:00Z','updated_at':'2026-11-05T00:00:00Z',
               'repository_url':f'{API}/repos/{DEFAULT_REPO}'}
        s = svc.ToolService(lambda: None, principal='robert', environment='dev',
                            github=self.reader(lambda url,_: Reply({'total_count':193,'items':[row]})))
        cursor = mock.MagicMock()
        with mock.patch.object(svc.ToolService,'_tx',lambda self: nullcontext(None)), \
                mock.patch.object(svc.ToolService,'_bind',lambda self,conn,caller: SimpleNamespace(ledger_id=1,generation=1,conversation_id='c-1')), \
                mock.patch.object(svc,'_cursor',lambda conn: cursor):
            done = s._github_issues(validate('minimoi_github_issues',{'operation':'latest'}),None,None)
            self.assertEqual((done['status'],done['code']),('done','read'))
            self.assertIn('#472',done['message'])
            cursor.reset_mock();self.requests.clear()
            bad = s._github_issues(validate('minimoi_github_issues',{'operation':'search','page':1}),None,None)
        self.assertEqual((bad['status'],bad['code']),('refused','tool_arguments_invalid'))
        self.assertIn('"operation": "latest"',bad['message'])
        self.assertEqual(self.requests,[]);cursor.__enter__.assert_not_called()

    def test_search_keeps_working_with_and_without_page(self):
        row = {'number':69,'state':'open','title':'Guest','created_at':'2026-01-01T00:00:00Z','updated_at':'2026-02-01T00:00:00Z',
               'repository_url':f'{API}/repos/{DEFAULT_REPO}'}
        reader = self.reader(lambda url,_: Reply({'total_count':1,'items':[row]}))
        self.assertEqual(reader.search('guest')[0],'ok')
        self.assertEqual(self.requests[0][1]['params'],{'q':f'guest repo:{DEFAULT_REPO}','sort':'updated','per_page':8})
        self.assertEqual(reader.search('guest',page=2)[0],'ok')
        self.assertEqual(self.requests[-1][1]['params']['page'],2)

    def test_model_envelope_stays_under_listener_limit_and_large_text_refuses(self):
        body = '😀' * BODY_CHARS
        comments = [{'body': '😀' * 600} for _ in range(8)]
        reader = self.reader(lambda url, _: Reply(comments if url.endswith('/comments') else {'body': body}))
        state, message, data = reader.issue(1)
        envelope = {'status': 'done', 'code': 'read', 'message': message, 'data': data}
        self.assertLess(len(json.dumps(envelope, ensure_ascii=False).encode()), 65536)
        oversized = self.reader(lambda *_: Reply({'body': 'x' * (TEXT_BYTES + 1)}))
        self.assertEqual(oversized.issue(1)[0], 'too_large')
        self.assertFalse(valid_request({'query': 'career repo:other/repo'}))

    def test_error_response_is_not_reported_as_success(self):
        for status in (403,404,429,500):
            reader=self.reader(lambda *_:Reply({},status))
            self.assertNotEqual(reader.issue(1)[0],'ok')
            self.assertNotEqual(reader.source(REF,'a')[0],'ok')
