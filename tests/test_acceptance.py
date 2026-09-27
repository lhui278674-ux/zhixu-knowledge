"""Integration acceptance with real local embeddings/reranker and isolated SQLite.
External API responses alone are mocked. Tests never read the Windows user API key.
"""
import io
import json
import os
import tempfile
import time
from pathlib import Path
import pytest
import httpx

REAL_HTTPX_CLIENT = httpx.Client

TEST_DATA = tempfile.TemporaryDirectory(prefix='zhixu-acceptance-')
os.environ['KB_DATA_DIR'] = TEST_DATA.name
os.environ['AIHUBMIX_API_KEY'] = 'test-only-not-a-real-key'
os.environ['USE_TF'] = '0'
from fastapi.testclient import TestClient
from backend.main import app
from backend import db, qa
from backend.config import ROOT
from backend.retrieval import models

HEADERS = {'X-KB-Request': '1'}
PASSWORD = 'AcceptanceOnly!2026'


def wait_question(client, qid, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        q = client.get('/api/qa/questions/' + qid).json()
        if q['status'] in qa.TERMINAL:
            return q
        time.sleep(0.1)
    raise AssertionError('question did not finish: ' + q['status'])


def ask(client, question, mode='evidence', scope=None):
    # Demo corpus is explicitly selected. An empty scope now means enterprise libraries only.
    selected = scope if scope is not None else [l['id'] for l in client.get('/api/libraries').json()]
    r = client.post('/api/qa/questions', json={'question': question, 'mode': mode, 'library_ids': selected}, headers=HEADERS)
    assert r.status_code == 200, r.text
    return wait_question(client, r.json()['id'])


def retrieve(client, question, scope=None):
    selected = scope if scope is not None else [l['id'] for l in client.get('/api/libraries').json()]
    return client.post('/api/qa/search', headers=HEADERS, json={'question': question, 'library_ids': selected})


@pytest.fixture(scope='module')
def environment():
    with TestClient(app) as admin:
        assert admin.get('/api/auth/status').json()['needs_setup']
        setup = {'initiator': {'username':'test_admin','password':PASSWORD,'display_name':'验收管理员'},
                 'reviewer': {'username':'test_reviewer','password':PASSWORD,'display_name':'复核管理员'}}
        assert admin.post('/api/auth/setup', headers=HEADERS, json=setup).status_code == 200
        assert admin.post('/api/auth/setup', headers=HEADERS, json=setup).status_code == 409
        employee = admin.post('/api/users', headers=HEADERS, json={'username':'test_employee','password':PASSWORD,'display_name':'验收员工','role':'employee'}).json()
        assert admin.post('/api/demo', headers=HEADERS).status_code == 200
        libraries = admin.get('/api/libraries').json()
        public = next(l for l in libraries if l['name'].startswith('员工'))['id']
        private = next(l for l in libraries if l['name'].startswith('研发'))['id']
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            docs = [d for l in libraries for d in admin.get(f'/api/libraries/{l["id"]}/documents').json()]
            if all(d['status'] == 'ready' for d in docs):
                break
            assert not any(d['status'] in {'parse_failed','model_unavailable'} for d in docs), docs
            time.sleep(0.2)
        assert len(docs) == 32 and all(d['status'] == 'ready' for d in docs)
        assert admin.put(f'/api/libraries/{public}/members/{employee["id"]}',headers=HEADERS).status_code == 200
        employee_client = TestClient(app)
        assert employee_client.post('/api/auth/login',json={'username':'test_employee','password':PASSWORD},headers=HEADERS).status_code == 200
        yield admin, employee_client, public, private, employee, docs


def test_auth_and_permission_boundary(environment):
    admin, emp, public, private, _, docs = environment
    assert models.state == 'ready'
    assert len(emp.get('/api/libraries').json()) == 1
    secret = next(d for d in docs if d['library_id'] == private)
    for path in [f'/api/libraries/{private}/documents',f'/api/documents/{secret["id"]}/source', f'/api/documents/{secret["id"]}/file']:
        assert emp.get(path).status_code == 404
    assert emp.get('/api/users').status_code == 403
    assert emp.post('/api/libraries',headers=HEADERS,json={'name':'forbidden'}).status_code == 403
    assert emp.post('/api/qa/search',headers=HEADERS,json={'question':'ORBIT-7429','library_ids':[private]}).status_code == 404
    assert emp.post('/api/qa/questions',headers=HEADERS,json={'question':'ORBIT-7429','library_ids':[private]}).status_code == 404
    results = retrieve(emp, '研发保密代号 ORBIT-7429 是什么？').json()['results']
    assert all(r['library_id'] == public and 'ORBIT-7429' not in r['text'] for r in results)
    assert admin.post('/api/libraries',json={'name':'csrf'}).status_code == 403
    assert admin.post('/api/libraries',headers={**HEADERS,'Origin':'https://untrusted.example'},json={'name':'csrf'}).status_code == 403
    unauth = TestClient(app)
    assert unauth.get('/api/libraries').status_code == 401
    assert unauth.get('/api/qa/questions').status_code == 401


@pytest.mark.parametrize('query,extension,locator',[
    ('差旅报销需要什么凭证？','pdf','page'),
    ('请假需要提前多久申请？','docx','paragraph'),
    ('餐费上限是多少？','xlsx','cells'),
    ('星舟项目的交付节点是什么？','pptx','slide'),
    ('新员工如何领取办公设备？','txt','paragraph'),
    ('员工需要新增知识库访问权限时应该找谁？','md','paragraph'),
])
def test_six_formats_real_retrieval(environment,query,extension,locator):
    _, emp, _, _, _, _ = environment
    results = retrieve(emp, query).json()['results']
    # Expanded policies intentionally repeat some facts across PDF and XLSX.
    # Verify the annotated format remains retrieved and cited, without requiring
    # one equally valid format to always outrank the other.
    matches = [r for r in results if r['name'].endswith('.'+extension) and r['locator']['type'] == locator]
    assert matches and matches[0]['score'] >= qa.MIN_SCORE, [(r['name'],r['score']) for r in results]
    q = ask(emp, query)
    assert q['status'] == 'completed', q
    matches = [c for c in q['citations'] if c['name'].endswith('.'+extension) and c['locator']['type'] == locator]
    assert matches, q
    c = matches[0]
    source = emp.get(f'/api/documents/{c["document_id"]}/source?chunk_id={c["chunk_id"]}').json()
    assert any(s['id'] == c['chunk_id'] and c['text'] == s['text'] for s in source['chunks'])


def test_clarify_no_answer_conflict(environment):
    _, emp, _, _, _, _ = environment
    assert ask(emp,'怎么办')['status'] == 'clarify'
    no_answer = ask(emp,'公司2028年在南极新建工厂的投资预算是多少？')
    assert no_answer['status'] == 'insufficient', no_answer
    assert no_answer['citations'] == []
    conflict = ask(emp,'出差住宿上限是多少？')
    assert conflict['status'] == 'conflict'
    assert '400元' in conflict['answer'] and '500元' in conflict['answer']
    assert len({c['document_id'] for c in conflict['citations']}) >= 2


def test_duplicate_delete_reindex_and_ocr(environment):
    admin, emp, public, _, _, docs = environment
    file = ROOT / 'demo/public/办公设备指引（虚构）.txt'
    r = admin.post(f'/api/libraries/{public}/documents',headers=HEADERS,files={'file':(file.name,file.read_bytes())})
    assert r.json()['duplicate'] is True
    content = '虚构验收资料。唯一编号 RENEW-8673，演示归档期限为17天。'.encode('utf-8')
    r = admin.post(f'/api/libraries/{public}/documents',headers=HEADERS,files={'file':('验收生命周期.txt',content)})
    did = r.json()['id']
    deadline = time.monotonic()+30
    while time.monotonic()<deadline and db.one('SELECT status FROM documents WHERE id=?',(did,))['status']!='ready':
        time.sleep(.1)
    old = db.rows('SELECT id FROM chunks WHERE document_id=?',(did,))
    assert old
    assert admin.post(f'/api/documents/{did}/reindex',headers=HEADERS).status_code==200
    assert not db.one('SELECT id FROM chunks WHERE id=?',(old[0]['id'],))
    assert emp.get(f'/api/documents/{did}/source?chunk_id={old[0]["id"]}').status_code==410
    deadline=time.monotonic()+30
    while time.monotonic()<deadline and db.one('SELECT status FROM documents WHERE id=?',(did,))['status']!='ready':
        time.sleep(.1)
    new=db.rows('SELECT id FROM chunks WHERE document_id=?',(did,))
    assert new and new[0]['id'] != old[0]['id']
    assert admin.delete(f'/api/documents/{did}',headers=HEADERS).status_code==200
    assert not db.rows('SELECT id FROM chunks WHERE document_id=?',(did,))
    results=retrieve(emp, 'RENEW-8673 归档期限').json()['results']
    assert all(c['document_id']!=did for c in results)
    from pypdf import PdfWriter
    stream=io.BytesIO();writer=PdfWriter();writer.add_blank_page(width=595,height=842);writer.write(stream)
    scanned=admin.post(f'/api/libraries/{public}/documents',headers=HEADERS,files={'file':('无文字扫描样例.pdf',stream.getvalue())}).json()['id']
    broken=admin.post(f'/api/libraries/{public}/documents',headers=HEADERS,files={'file':('损坏.docx',b'not a zip')}).json()['id']
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        states=[db.one('SELECT status FROM documents WHERE id=?',(d,))['status'] for d in (scanned,broken)]
        if states==['needs_ocr','parse_failed']: break
        time.sleep(.1)
    assert states==['needs_ocr','parse_failed']


def mock_upstream(monkeypatch, kind='valid', recorder=None):
    import httpx
    original=REAL_HTTPX_CLIENT
    def handle(request):
        payload=json.loads(request.content)
        sources=json.loads(payload['messages'][1]['content'])['sources']
        if recorder is not None:recorder.append(payload)
        assert 'ORBIT-7429' not in request.content.decode()
        assert 'test_employee' not in request.content.decode()
        assert 'test-only-not-a-real-key' not in request.content.decode()
        assert payload['model']=='coding-kimi-k3-free' and payload['stream'] is True
        assert len(sources)<=6 and all(len(s['text'])<=500 for s in sources)
        if kind=='429':return httpx.Response(429,headers={'retry-after':'60'},json={'error':'secret error not forwarded'})
        if kind=='down':return httpx.Response(503,text='upstream raw error not forwarded')
        result={'status':'answer','items':[{'source_id':sources[0]['source_id'],'quote':sources[0]['text'] if kind=='valid' else '这是一段不在任何证据中的编造结论。'}]}
        data=json.dumps({'choices':[{'delta':{'content':json.dumps(result,ensure_ascii=False)}}]},ensure_ascii=False)
        return httpx.Response(200,headers={'content-type':'text/event-stream'},text='data: '+data+'\n\ndata: [DONE]\n\n')
    monkeypatch.setattr(qa.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))


def reset_quota():
    db.execute('DELETE FROM model_calls')
    db.execute("DELETE FROM settings WHERE key='model_cooldown'")


def test_generation_payload_validation_sse_and_disconnect(environment,monkeypatch):
    _,emp,_,_,_,_=environment
    reset_quota();calls=[];mock_upstream(monkeypatch,recorder=calls)
    q=ask(emp,'新员工如何领取办公设备？','ai')
    assert q['status']=='completed' and len(calls)==1
    c=q['citations'][0];assert c['quote'] in c['text']
    events=emp.get(f'/api/qa/questions/{q["id"]}/events').text
    assert 'event: citations' in events and 'event: delta' in events and 'event: done' in events
    records=db.rows('SELECT seq FROM events WHERE question_id=? ORDER BY seq',(q['id'],))
    replay=emp.get(f'/api/qa/questions/{q["id"]}/events',headers={'Last-Event-ID':str(records[-2]['seq'])}).text
    assert 'event: done' in replay and 'event: citations' not in replay
    reset_quota();mock_upstream(monkeypatch,'hallucination')
    rejected=ask(emp,'新员工如何领取办公设备？','ai')
    assert rejected['status']=='validation_failed' and '编造结论' not in rejected['answer']


def test_missing_key_unavailable_and_provider_rate_limit(environment,monkeypatch):
    _,emp,_,_,_,_=environment
    monkeypatch.delenv('AIHUBMIX_API_KEY')
    q=ask(emp,'新员工如何领取办公设备？','ai')
    assert q['status']=='model_unavailable' and q['citations']
    monkeypatch.setenv('AIHUBMIX_API_KEY','test-only-not-a-real-key')
    reset_quota();mock_upstream(monkeypatch,'down')
    q=ask(emp,'新员工如何领取办公设备？','ai')
    assert q['status']=='model_unavailable' and 'raw error' not in q['answer']
    reset_quota();mock_upstream(monkeypatch,'429')
    q=ask(emp,'新员工如何领取办公设备？','ai')
    assert q['status']=='rate_limited' and q['citations'] and '限流' in q['answer']
    assert any(h['id']==q['id'] for h in emp.get('/api/qa/questions').json())


def test_persistent_minute_queue_daily_cap_and_cancel(environment,monkeypatch):
    _,emp,_,_,_,_=environment
    reset_quota();mock_upstream(monkeypatch)
    # Seed the real persisted window when reservation begins. Slow CPU retrieval
    # must not expire the window before this quota behavior is exercised.
    reserve=qa.reserve_call
    primed=False
    def seed_then_reserve(tokens):
        nonlocal primed
        if not primed:
            primed=True
            with db.connect() as c:c.executemany('INSERT INTO model_calls(at,tokens) VALUES(?,?)',[(time.time()-58,100) for _ in range(5)])
        return reserve(tokens)
    monkeypatch.setattr(qa,'reserve_call',seed_then_reserve)
    q=ask(emp,'新员工如何领取办公设备？','ai')
    assert q['status']=='completed'
    assert any('waiting_quota' in e['payload'] for e in db.rows('SELECT payload FROM events WHERE question_id=?',(q['id'],)))
    assert qa.quota_state()['day_used']==6
    monkeypatch.setattr(qa,'reserve_call',reserve)
    reset_quota()
    with db.connect() as c:c.executemany('INSERT INTO model_calls(at,tokens) VALUES(?,?)',[(time.time()-120,100) for _ in range(100)])
    q=ask(emp,'新员工如何领取办公设备？','ai')
    assert q['status']=='rate_limited' and '24 小时' in q['answer']
    assert qa.quota_state()['day_used']==100
    reset_quota()
    with db.connect() as c:c.executemany('INSERT INTO model_calls(at,tokens) VALUES(?,?)',[(time.time(),100) for _ in range(5)])
    result=emp.post('/api/qa/questions',headers=HEADERS,json={'question':'新员工如何领取办公设备？','mode':'ai','library_ids':[l['id'] for l in emp.get('/api/libraries').json()]}).json()
    time.sleep(.8)
    assert emp.post(f'/api/qa/questions/{result["id"]}/cancel',headers=HEADERS).status_code==200
    assert wait_question(emp,result['id'])['status']=='cancelled'
    reset_quota()


def test_revoke_hides_history_sources_and_stops_retrieval(environment):
    admin,emp,public,_,employee,_=environment
    q=ask(emp,'新员工如何领取办公设备？')
    assert q['citations']
    assert admin.delete(f'/api/libraries/{public}/members/{employee["id"]}',headers=HEADERS).status_code==200
    history=emp.get(f'/api/qa/questions/{q["id"]}').json()
    assert history['status']=='source_changed' and history['citations']==[]
    assert q['citations'][0]['text'] not in history['answer']
    source=q['citations'][0]
    assert emp.get(f'/api/documents/{source["document_id"]}/source').status_code==404
    assert emp.post('/api/qa/search',headers=HEADERS,json={'question':'设备'}).json()['results']==[]
    replay=emp.get(f'/api/qa/questions/{q["id"]}/events').text
    assert 'event: reset' in replay and source['text'] not in replay
    assert admin.patch(f'/api/users/{employee["id"]}',headers=HEADERS,json={'active':False}).status_code==200
    assert emp.get('/api/qa/questions').status_code==401


def test_local_model_failure_is_explicit(environment,monkeypatch):
    admin,_,_,_,_,_=environment
    from backend.retrieval import ModelUnavailable
    def unavailable(*args,**kwargs):
        raise ModelUnavailable('本地模型不可用，请检查模型文件。')
    monkeypatch.setattr(models,'embed',unavailable)
    response=retrieve(admin, '差旅报销需要什么凭证？')
    assert response.status_code==503 and '本地模型不可用' in response.json()['detail']
    q=ask(admin,'差旅报销需要什么凭证？')
    assert q['status']=='model_unavailable' and '本地模型不可用' in q['answer']


def test_all_six_upload_endpoints(environment):
    admin,_,_,_,_,_=environment
    library=admin.post('/api/libraries',headers=HEADERS,json={'name':'六格式上传接口验收（虚构）'}).json()['id']
    uploaded=[]
    for file in (ROOT/'demo/public').iterdir():
        response=admin.post(f'/api/libraries/{library}/documents',headers=HEADERS,files={'file':(file.name,file.read_bytes())})
        assert response.status_code==200 and response.json()['duplicate'] is False
        uploaded.append(response.json()['id'])
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        docs=admin.get(f'/api/libraries/{library}/documents').json()
        if all(d['status']=='ready' for d in docs):break
        time.sleep(.1)
    assert len(docs)==6 and {d['extension'] for d in docs}=={'pdf','docx','xlsx','pptx','txt','md'}
    assert all(d['status']=='ready' and d['chunk_count']>0 for d in docs)
    queries={'pdf':'差旅报销需要什么凭证？','docx':'请假需要提前多久申请？','xlsx':'餐费上限是多少？',
             'pptx':'星舟项目的交付节点是什么？','txt':'新员工如何领取办公设备？','md':'员工需要新增知识库访问权限时应该找谁？'}
    for ext,query in queries.items():
        response=admin.post('/api/qa/search',headers=HEADERS,json={'question':query,'library_ids':[library]})
        assert response.status_code==200 and any(r['name'].endswith('.'+ext) for r in response.json()['results'])
def test_registration_and_separate_portals(environment):
    admin, _, public, private, _, _ = environment
    applicant = TestClient(app)
    credentials = {'username': 'registered_staff', 'password': PASSWORD, 'display_name': '注册员工'}
    response = applicant.post('/api/auth/register', headers=HEADERS, json=credentials)
    assert response.status_code == 200
    uid = response.json()['id']
    assert response.json()['status'] == 'pending'
    assert applicant.get('/api/auth/me').status_code == 401
    assert applicant.post('/api/auth/login', headers=HEADERS, json=credentials).status_code == 403
    # An active edit and an extra role field cannot bypass registration review.
    assert admin.patch('/api/users/' + uid, headers=HEADERS, json={'active': True, 'role': 'admin'}).status_code == 409
    assert admin.post('/api/registrations/' + uid + '/review', headers=HEADERS,
                      json={'approve': True, 'grants': {public: 'view'}}).status_code == 200
    assert admin.post('/api/registrations/' + uid + '/review', headers=HEADERS, json={'approve': True}).status_code == 409
    assert applicant.post('/api/auth/login', headers=HEADERS, json=credentials | {'portal': 'admin'}).status_code == 403
    assert applicant.post('/api/auth/login', headers=HEADERS, json=credentials | {'portal': 'employee'}).status_code == 200
    assert [l['id'] for l in applicant.get('/api/libraries').json()] == [public]
    assert applicant.get('/api/libraries/' + private + '/directories').status_code == 404
    for endpoint in ('/registrations', '/admin-changes', '/governance', '/audit'):
        assert applicant.get('/api' + endpoint).status_code == 403
    assert admin.post('/api/users', headers=HEADERS,
                      json={'username': 'direct_admin', 'password': PASSWORD, 'role': 'admin'}).status_code == 409
    # Rejected applicants remain unable to enter either portal.
    rejected = applicant.post('/api/auth/register', headers=HEADERS,
                              json={'username': 'rejected_staff', 'password': PASSWORD}).json()['id']
    assert admin.post('/api/registrations/' + rejected + '/review', headers=HEADERS,
                      json={'approve': False, 'note': '演示驳回'}).status_code == 200
    assert TestClient(app).post('/api/auth/login', headers=HEADERS,
                               json={'username': 'rejected_staff', 'password': PASSWORD}).status_code == 403


def test_two_person_admin_changes_and_duty_transfer(environment):
    admin, _, _, _, _, _ = environment
    reviewer = TestClient(app)
    assert reviewer.post('/api/auth/login', headers=HEADERS,
                         json={'username': 'test_reviewer', 'password': PASSWORD, 'portal': 'admin'}).status_code == 200
    governance = admin.get('/api/governance').json()
    initiator_id, reviewer_id = governance['initiator_id'], governance['reviewer_id']
    for uid in (initiator_id, reviewer_id):
        assert admin.patch('/api/users/' + uid, headers=HEADERS, json={'active': False}).status_code == 409
        assert admin.post('/api/admin-changes', headers=HEADERS,
                          json={'action': 'deactivate', 'target_id': uid, 'reason': '不能直接停用职责账号'}).status_code == 409
    target = admin.post('/api/users', headers=HEADERS,
                        json={'username': 'future_admin', 'password': PASSWORD, 'display_name': '新管理员'}).json()['id']
    proposal = {'action': 'promote', 'target_id': target, 'reason': '测试新增管理员'}
    assert reviewer.post('/api/admin-changes', headers=HEADERS, json=proposal).status_code == 403
    cid = admin.post('/api/admin-changes', headers=HEADERS, json=proposal).json()['id']
    assert admin.post('/api/admin-changes/' + cid + '/review', headers=HEADERS, json={'approve': True}).status_code == 403
    assert db.one('SELECT role FROM users WHERE id=?', (target,))['role'] == 'employee'
    assert reviewer.post('/api/admin-changes/' + cid + '/review', headers=HEADERS, json={'approve': True}).status_code == 200
    assert reviewer.post('/api/admin-changes/' + cid + '/review', headers=HEADERS, json={'approve': True}).status_code == 409
    third = TestClient(app)
    assert third.post('/api/auth/login', headers=HEADERS,
                      json={'username': 'future_admin', 'password': PASSWORD, 'portal': 'admin'}).status_code == 200
    assert third.post('/api/admin-changes', headers=HEADERS, json=proposal).status_code == 403
    cid = admin.post('/api/admin-changes', headers=HEADERS,
                     json={'action': 'deactivate', 'target_id': target, 'reason': '测试停用'}).json()['id']
    assert third.post('/api/admin-changes/' + cid + '/review', headers=HEADERS, json={'approve': True}).status_code == 403
    assert reviewer.post('/api/admin-changes/' + cid + '/review', headers=HEADERS, json={'approve': True}).status_code == 200
    assert third.get('/api/auth/me').status_code == 401
    cid = admin.post('/api/admin-changes', headers=HEADERS,
                     json={'action': 'reactivate', 'target_id': target, 'reason': '测试恢复'}).json()['id']
    assert reviewer.post('/api/admin-changes/' + cid + '/review', headers=HEADERS, json={'approve': True}).status_code == 200
    cid = admin.post('/api/admin-changes', headers=HEADERS,
                     json={'action': 'demote', 'target_id': target, 'reason': '测试转为员工'}).json()['id']
    assert reviewer.post('/api/admin-changes/' + cid + '/review', headers=HEADERS, json={'approve': True}).status_code == 200
    assert db.one('SELECT role FROM users WHERE id=?', (target,))['role'] == 'employee'
    # Both governance duties are themselves reviewed; the former initiator loses that duty.
    cid = admin.post('/api/admin-changes', headers=HEADERS,
                     json={'action': 'duties', 'initiator_id': reviewer_id, 'reviewer_id': initiator_id, 'reason': '交换职责'}).json()['id']
    assert reviewer.post('/api/admin-changes/' + cid + '/review', headers=HEADERS, json={'approve': True}).status_code == 200
    assert admin.post('/api/admin-changes', headers=HEADERS, json=proposal).status_code == 403
    cid = reviewer.post('/api/admin-changes', headers=HEADERS,
                        json={'action': 'duties', 'initiator_id': initiator_id, 'reviewer_id': reviewer_id, 'reason': '恢复测试职责'}).json()['id']
    assert admin.post('/api/admin-changes/' + cid + '/review', headers=HEADERS, json={'approve': True}).status_code == 200


def test_document_center_search_folders_and_personal_lists(environment):
    admin, _, public, private, _, _ = environment
    user = admin.post('/api/users', headers=HEADERS,
                      json={'username': 'browse_staff', 'password': PASSWORD, 'display_name': '浏览员工'}).json()['id']
    assert admin.put(f'/api/libraries/{public}/members/{user}', headers=HEADERS, json={'permission': 'view'}).status_code == 200
    emp = TestClient(app)
    assert emp.post('/api/auth/login', headers=HEADERS, json={'username': 'browse_staff', 'password': PASSWORD}).status_code == 200
    root = admin.post(f'/api/libraries/{public}/directories', headers=HEADERS, json={'name': '制度'}).json()['id']
    child = admin.post(f'/api/libraries/{public}/directories', headers=HEADERS, json={'name': '差旅', 'parent_id': root}).json()['id']
    foreign = admin.post(f'/api/libraries/{private}/directories', headers=HEADERS, json={'name': '保密'}).json()['id']
    assert admin.patch('/api/directories/' + root, headers=HEADERS, json={'name': '制度', 'parent_id': child}).status_code == 409
    assert admin.post(f'/api/libraries/{public}/directories', headers=HEADERS, json={'name': '跨库', 'parent_id': foreign}).status_code == 404
    assert emp.post(f'/api/libraries/{public}/directories', headers=HEADERS, json={'name': '非法'}).status_code == 403
    response = emp.get('/api/documents/search', params={'q': '电子发票', 'field': 'content'}).json()
    assert response['total'] > 0
    d = next(d for d in response['results'] if d['extension'] == 'pdf')
    assert d['chunk_id'] and d['locator']['type'] == 'page' and '电子发票' in d['snippet']
    assert emp.get('/api/documents/search', params={'q': '电子发票', 'field': 'title'}).json()['total'] == 0
    assert emp.get('/api/documents/search', params={'q': 'ORBIT-7429'}).json()['total'] == 0
    assert emp.get('/api/documents/search', params={'library_id': private, 'q': 'ORBIT-7429'}).status_code == 404
    assert admin.patch('/api/documents/' + d['id'], headers=HEADERS, json={'directory_id': child}).status_code == 200
    assert admin.patch('/api/documents/' + d['id'], headers=HEADERS, json={'directory_id': foreign}).status_code == 404
    result = emp.get('/api/portal/documents', params={'library_id': public, 'directory_id': root}).json()
    assert [x['id'] for x in result['results']] == [d['id']]
    assert emp.put('/api/favorites/' + d['id'], headers=HEADERS).status_code == 200
    assert emp.get('/api/documents/' + d['id'] + '/source', params={'chunk_id': d['chunk_id']}).status_code == 200
    assert [x['id'] for x in emp.get('/api/portal/documents?view=favorites').json()['results']] == [d['id']]
    assert [x['id'] for x in emp.get('/api/portal/documents?view=recent').json()['results']] == [d['id']]
    assert admin.get('/api/portal/documents?view=favorites').json()['total'] == 0
    assert admin.delete(f'/api/libraries/{public}/members/{user}', headers=HEADERS).status_code == 200
    for view in ('all', 'favorites', 'recent'):
        assert emp.get('/api/portal/documents', params={'view': view}).json()['total'] == 0
    assert emp.get('/api/documents/' + d['id'] + '/source').status_code == 404
    assert emp.get('/api/libraries/' + public + '/directories').status_code == 404
    assert emp.get('/api/submissions').json() == []
    assert admin.delete('/api/directories/' + root, headers=HEADERS).status_code == 200
    assert db.one('SELECT directory_id FROM documents WHERE id=?', (d['id'],))['directory_id'] is None
    assert admin.get('/api/documents/' + d['id'] + '/source').status_code == 200


def test_reviewed_submission_is_not_parsed_before_approval(environment):
    admin, _, public, private, _, _ = environment
    uid = admin.post('/api/users', headers=HEADERS,
                     json={'username': 'contributing_staff', 'password': PASSWORD, 'display_name': '资料员工'}).json()['id']
    admin.put(f'/api/libraries/{public}/members/{uid}', headers=HEADERS, json={'permission': 'view'})
    emp = TestClient(app)
    assert emp.post('/api/auth/login', headers=HEADERS, json={'username': 'contributing_staff', 'password': PASSWORD}).status_code == 200
    content = '青禾公司员工实验资料。青禾专属流程代码为 QH-99271，设备领取需填写青禾设备申请单。'.encode()
    files = {'file': ('青禾资料（虚构）.txt', content)}
    assert emp.post(f'/api/libraries/{public}/submissions', headers=HEADERS, files=files).status_code == 403
    assert emp.post(f'/api/libraries/{public}/documents', headers=HEADERS, files=files).status_code == 403
    admin.put(f'/api/libraries/{public}/members/{uid}', headers=HEADERS, json={'permission': 'submit'})
    sid = emp.post(f'/api/libraries/{public}/submissions', headers=HEADERS, files=files).json()['id']
    assert emp.post(f'/api/libraries/{public}/submissions', headers=HEADERS, files=files).json()['duplicate']
    assert not db.one('SELECT id FROM documents WHERE id=?', (sid,))
    assert not db.one('SELECT id FROM chunks WHERE document_id=?', (sid,))
    assert emp.get('/api/documents/search?q=QH-99271').json()['total'] == 0
    assert not any('QH-99271' in r['text'] for r in emp.post('/api/qa/search', headers=HEADERS, json={'question': 'QH-99271'}).json()['results'])
    assert emp.post('/api/submissions/' + sid + '/review', headers=HEADERS, json={'approve': True}).status_code == 403
    assert emp.get('/api/documents/' + sid + '/source').status_code == 404
    assert admin.post('/api/submissions/' + sid + '/review', headers=HEADERS, json={'approve': True}).status_code == 200
    assert admin.post('/api/submissions/' + sid + '/review', headers=HEADERS, json={'approve': True}).status_code == 409
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and db.one('SELECT status FROM documents WHERE id=?', (sid,))['status'] not in {'ready', 'parse_failed', 'model_unavailable'}:
        time.sleep(.1)
    assert db.one('SELECT status FROM documents WHERE id=?', (sid,))['status'] == 'ready'
    result = emp.get('/api/documents/search?q=QH-99271&field=content').json()['results'][0]
    assert result['id'] == sid and result['chunk_id']
    question = ask(emp, '青禾专属流程代码是什么？')
    assert any(c['document_id'] == sid for c in question['citations'])
    assert emp.get('/api/documents/' + sid + '/source', params={'chunk_id': result['chunk_id']}).status_code == 200
    # Rejected and revoked submissions never create document/index rows.
    rejected = emp.post(f'/api/libraries/{public}/submissions', headers=HEADERS,
                        files={'file': ('驳回.txt', '未审核唯一内容 REJECT-99272'.encode())}).json()['id']
    assert admin.post('/api/submissions/' + rejected + '/review', headers=HEADERS, json={'approve': False, 'note': '资料不完整'}).status_code == 200
    assert not db.one('SELECT id FROM documents WHERE id=?', (rejected,))
    assert emp.get('/api/documents/search?q=REJECT-99272').json()['total'] == 0
    revoked = emp.post(f'/api/libraries/{public}/submissions', headers=HEADERS,
                       files={'file': ('撤权.txt', '未审核撤权资料 REVOKE-99273'.encode())}).json()['id']
    admin.put(f'/api/libraries/{public}/members/{uid}', headers=HEADERS, json={'permission': 'view'})
    assert admin.post('/api/submissions/' + revoked + '/review', headers=HEADERS, json={'approve': True}).status_code == 409
    assert not db.one('SELECT id FROM documents WHERE id=?', (revoked,))
    assert emp.get('/api/documents/search?q=REVOKE-99273').json()['total'] == 0


def test_legacy_single_admin_migration_preserves_records(tmp_path):
    """Use a subprocess so the old fixture cannot replace a live queue's DB."""
    import sqlite3
    import subprocess
    import sys
    old = tmp_path / 'knowledge.db'
    c = sqlite3.connect(old)
    c.executescript('''
    CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,display_name TEXT NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL);
    CREATE TABLE libraries(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,created_at REAL NOT NULL,demo INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE members(library_id TEXT REFERENCES libraries(id),user_id TEXT REFERENCES users(id),PRIMARY KEY(library_id,user_id));
    CREATE TABLE documents(id TEXT PRIMARY KEY,library_id TEXT REFERENCES libraries(id),name TEXT,extension TEXT,hash TEXT,size INTEGER,status TEXT,detail TEXT DEFAULT '',version TEXT,created_at REAL,chunk_count INTEGER DEFAULT 0,UNIQUE(library_id,hash));
    CREATE TABLE chunks(id TEXT PRIMARY KEY,document_id TEXT REFERENCES documents(id),text TEXT,locator TEXT,ordinal INTEGER,vector BLOB,model TEXT);
    CREATE TABLE questions(id TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id),question TEXT,library_ids TEXT,mode TEXT,status TEXT,answer TEXT DEFAULT '',citations TEXT DEFAULT '[]',created_at REAL,updated_at REAL);
    INSERT INTO users VALUES('legacy-admin','legacy_admin','旧管理员','preserved-hash','admin',1,1);
    INSERT INTO users VALUES('legacy-emp','legacy_emp','旧员工','preserved-employee-hash','employee',1,2);
    INSERT INTO libraries VALUES('legacy-lib','旧库','原始库',1,0);
    INSERT INTO members VALUES('legacy-lib','legacy-emp');
    INSERT INTO documents VALUES('legacy-doc','legacy-lib','旧.txt','txt','preserved-file-hash',7,'ready','','original-version',1,1);
    INSERT INTO chunks VALUES('legacy-chunk','legacy-doc','原始正文','{}',0,X'01020304','preserved-model');
    INSERT INTO questions VALUES('legacy-question','legacy-emp','旧问题','["legacy-lib"]','evidence','completed','旧回答','[]',1,1);
    ''')
    before = {table: c.execute('SELECT * FROM ' + table).fetchall() for table in ('users','libraries','members','documents','chunks','questions')}
    c.close()
    env = os.environ.copy() | {'KB_DATA_DIR': str(tmp_path)}
    code = '''from backend import db
db.init()
db.init()
assert db.one("SELECT review_status FROM users WHERE id='legacy-emp'")['review_status']=='approved'
assert db.one("SELECT permission FROM members")['permission']=='view'
assert not db.one("SELECT 1 FROM settings WHERE key='governance_initiator'")
from backend.auth import hash_password
db.execute("UPDATE users SET password_hash=? WHERE id='legacy-admin'", (hash_password('MigrationOnly!2026'),))
from fastapi.testclient import TestClient
from backend.main import app
client=TestClient(app)
headers={'X-KB-Request':'1'}
assert client.post('/api/auth/login',headers=headers,json={'username':'legacy_admin','password':'MigrationOnly!2026','portal':'admin'}).status_code==200
assert client.get('/api/governance').json()['needs_completion']
body={'second_admin':{'username':'second_admin','password':'MigrationOnly!2026','display_name':'复核管理员'}}
assert client.post('/api/auth/complete-setup',headers=headers,json=body).status_code==200
assert client.post('/api/auth/complete-setup',headers=headers,json=body).status_code==409
assert not client.get('/api/governance').json()['needs_completion']
db.execute("UPDATE users SET password_hash='preserved-hash' WHERE id='legacy-admin'")
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(old) as c:
        for table, original in before.items():
            columns = len(original[0])
            current = c.execute('SELECT * FROM ' + table).fetchall()
            assert {tuple(r[:columns]) for r in current}.issuperset(set(original)), table
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []
def test_six_formats_employee_submission_pipeline(environment):
    admin, _, _, _, _, _ = environment
    lid = admin.post('/api/libraries', headers=HEADERS, json={'name': '员工六格式审核（虚构）'}).json()['id']
    uid = admin.post('/api/users', headers=HEADERS,
                     json={'username': 'six_format_staff', 'password': PASSWORD}).json()['id']
    admin.put(f'/api/libraries/{lid}/members/{uid}', headers=HEADERS, json={'permission': 'submit'})
    emp = TestClient(app)
    assert emp.post('/api/auth/login', headers=HEADERS,
                    json={'username': 'six_format_staff', 'password': PASSWORD}).status_code == 200
    submissions = []
    for path in sorted((ROOT / 'demo/public').iterdir()):
        result = emp.post(f'/api/libraries/{lid}/submissions', headers=HEADERS,
                          files={'file': (path.name, path.read_bytes())})
        assert result.status_code == 200
        submissions.append(result.json()['id'])
    assert len(submissions) == 6
    assert admin.get(f'/api/libraries/{lid}/documents').json() == []
    assert emp.get('/api/documents/search', params={'q': '申请', 'library_id': lid}).json()['total'] == 0
    assert emp.post('/api/qa/search', headers=HEADERS,
                    json={'question': '申请', 'library_ids': [lid]}).json()['results'] == []
    for sid in submissions:
        assert admin.post(f'/api/submissions/{sid}/review', headers=HEADERS, json={'approve': True}).status_code == 200
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        docs = admin.get(f'/api/libraries/{lid}/documents').json()
        if len(docs) == 6 and all(d['status'] == 'ready' for d in docs):
            break
        time.sleep(.1)
    assert {d['extension'] for d in docs} == {'pdf', 'docx', 'xlsx', 'pptx', 'txt', 'md'}
    assert all(d['status'] == 'ready' for d in docs)
    for d in docs:
        assert emp.get(f'/api/documents/{d["id"]}/source').json()['chunks']
