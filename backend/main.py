import asyncio
import hashlib
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import db, qa
from .accounts import router as account_router
from .portal import router as portal_router, check_directory, read_upload
from .auth import admin, current, document_access, library_access, allowed_ids
from .config import DATA, ROOT
from .retrieval import models, submit_document, search, ModelUnavailable


@asynccontextmanager
async def lifespan(app):
    db.init()
    for q in db.rows("SELECT id FROM questions WHERE status NOT IN (" + ','.join('?' for _ in qa.TERMINAL) + ')', list(qa.TERMINAL)):
        qa.finish(q['id'], 'interrupted', '服务已重启，先前任务已中断；问题已保存，可重试。', [])
    for d in db.rows("SELECT id,version FROM documents WHERE status IN ('queued','processing')"):
        submit_document(d['id'], d['version'])
    asyncio.get_running_loop().run_in_executor(None, warmup)
    yield


def warmup():
    try:
        models.load()
    except ModelUnavailable:
        pass


app = FastAPI(title='知序 · 企业知识库', version='0.2.0', lifespan=lifespan)


app.include_router(account_router)
app.include_router(portal_router)


@app.middleware('http')
async def boundary(request: Request, call_next):
    if request.url.path.startswith('/api') and request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        if request.headers.get('x-kb-request') != '1':
            return JSONResponse({'detail': '缺少同源请求标记。'}, status_code=403)
        origin = request.headers.get('origin')
        allowed = {f'http://127.0.0.1:{p}' for p in (8000, 5173)} | {f'http://localhost:{p}' for p in (8000, 5173)}
        if origin and origin not in allowed:
            return JSONResponse({'detail': '拒绝跨来源写入。'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    if request.url.path.startswith('/api'):
        response.headers['Cache-Control'] = 'no-store'
    return response


class NewLibrary(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default='', max_length=300)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    library_ids: list[str] = Field(default_factory=list, max_length=30)
    mode: Literal['ai', 'evidence'] = 'ai'


@app.get('/api/libraries')
def libraries(user=Depends(current)):
    ids = allowed_ids(user)
    return [r | {'permission': 'submit' if user['role'] == 'admin' else db.one('SELECT permission FROM members WHERE library_id=? AND user_id=?', (r['id'], user['id']))['permission']} for r in db.rows('''SELECT l.*, (SELECT COUNT(*) FROM documents d WHERE d.library_id=l.id) document_count,
                               (SELECT COUNT(*) FROM members m WHERE m.library_id=l.id) member_count FROM libraries l ORDER BY created_at''') if r['id'] in ids]


@app.post('/api/libraries')
def create_library(body: NewLibrary, user=Depends(admin)):
    lid = uuid.uuid4().hex
    db.execute('INSERT INTO libraries VALUES(?,?,?,?,0)', (lid, body.name, body.description, time.time()))
    db.audit(user['id'], 'create_library', lid)
    return {'id': lid}


@app.get('/api/libraries/{lid}/members')
def members(lid: str, user=Depends(admin)):
    library_access(user, lid)
    return db.rows('''SELECT u.id,u.username,u.display_name,m.permission FROM members m JOIN users u ON m.user_id=u.id
                     WHERE m.library_id=?''', (lid,))


class MemberPermission(BaseModel):
    permission: Literal['view', 'submit'] = 'view'


@app.put('/api/libraries/{lid}/members/{uid}')
def add_member(lid: str, uid: str, body: MemberPermission = MemberPermission(), user=Depends(admin)):
    library_access(user, lid)
    if not db.one("SELECT id FROM users WHERE id=? AND active=1 AND review_status='approved'", (uid,)):
        raise HTTPException(404, '账号不存在或已停用。')
    with qa.dispatch_lock:
        db.execute('INSERT INTO members(library_id,user_id,permission) VALUES(?,?,?) ON CONFLICT(library_id,user_id) DO UPDATE SET permission=excluded.permission', (lid, uid, body.permission))
    db.audit(user['id'], 'grant', lid+':'+uid)
    return {'ok': True}


@app.delete('/api/libraries/{lid}/members/{uid}')
def revoke(lid: str, uid: str, user=Depends(admin)):
    library_access(user, lid)
    with qa.dispatch_lock:
        db.execute('DELETE FROM members WHERE library_id=? AND user_id=?', (lid, uid))
    db.audit(user['id'], 'revoke', lid+':'+uid)
    return {'ok': True}


@app.get('/api/libraries/{lid}/documents')
def documents(lid: str, user=Depends(current)):
    library_access(user, lid)
    return db.rows('''SELECT d.*,EXISTS(SELECT 1 FROM favorites f WHERE f.user_id=? AND f.document_id=d.id) favorite FROM documents d WHERE d.library_id=? ORDER BY d.created_at DESC''', (user['id'], lid))


@app.post('/api/libraries/{lid}/documents')
async def upload(lid: str, file: UploadFile = File(...), directory_id: str | None = Form(None), user=Depends(admin)):
    library_access(user, lid)
    check_directory(lid, directory_id)
    name, ext, content = await read_upload(file)
    digest = hashlib.sha256(content).hexdigest()
    existing = db.one('SELECT id FROM documents WHERE library_id=? AND hash=?', (lid, digest))
    if existing:
        return {'id': existing['id'], 'duplicate': True, 'message': '同一知识库中已存在相同文件。'}
    did, version = uuid.uuid4().hex, uuid.uuid4().hex
    (DATA / 'uploads' / did).write_bytes(content)
    try:
        db.execute('''INSERT INTO documents(id,library_id,name,extension,hash,size,status,version,created_at,directory_id)
                      VALUES(?,?,?,?,?,?,?,?,?,?)''',
                   (did, lid, name[:180], ext, digest, len(content), 'queued', version, time.time(), directory_id or None))
    except Exception:
        (DATA / 'uploads' / did).unlink(missing_ok=True)
        existing = db.one('SELECT id FROM documents WHERE library_id=? AND hash=?', (lid, digest))
        if existing:
            return {'id': existing['id'], 'duplicate': True, 'message': '相同文件已在处理。'}
        raise
    submit_document(did, version)
    db.audit(user['id'], 'upload', did)
    return {'id': did, 'duplicate': False}


@app.delete('/api/documents/{did}')
def delete_document(did: str, user=Depends(admin)):
    document_access(user, did)
    with qa.dispatch_lock:
        db.execute('DELETE FROM documents WHERE id=?', (did,))
        (DATA / 'uploads' / did).unlink(missing_ok=True)
    db.audit(user['id'], 'delete_document', did)
    return {'ok': True}


@app.post('/api/documents/{did}/reindex')
def reindex(did: str, user=Depends(admin)):
    document_access(user, did)
    version = uuid.uuid4().hex
    with qa.dispatch_lock, db.connect() as c:
        c.execute('DELETE FROM chunks WHERE document_id=?', (did,))
        c.execute("UPDATE documents SET status='queued',version=?,detail='已清除旧索引，等待重建',chunk_count=0 WHERE id=?", (version, did))
    submit_document(did, version)
    db.audit(user['id'], 'reindex', did)
    return {'ok': True}


@app.get('/api/documents/{did}/source')
def source(did: str, chunk_id: str | None = None, user=Depends(current)):
    d = document_access(user, did)
    chunks = db.rows('SELECT id,text,locator,ordinal FROM chunks WHERE document_id=? ORDER BY ordinal', (did,))
    if chunk_id and not any(c['id'] == chunk_id for c in chunks):
        raise HTTPException(410, '引用片段已失效，请重新检索。')
    db.execute('INSERT INTO recent_views(user_id,document_id,viewed_at) VALUES(?,?,?) ON CONFLICT(user_id,document_id) DO UPDATE SET viewed_at=excluded.viewed_at', (user['id'], did, time.time()))
    return {'document': d, 'chunks': [{**c, 'locator': json.loads(c['locator'])} for c in chunks], 'selected': chunk_id}


@app.get('/api/documents/{did}/file')
def raw_file(did: str, user=Depends(current)):
    d = document_access(user, did)
    mime = 'application/pdf' if d['extension'] == 'pdf' else 'application/octet-stream'
    return FileResponse(DATA / 'uploads' / did, media_type=mime, filename=d['name'],
                        content_disposition_type='inline' if d['extension'] == 'pdf' else 'attachment')


@app.post('/api/qa/search')
def do_search(body: Question, user=Depends(current)):
    if body.library_ids and not set(body.library_ids).issubset(allowed_ids(user)):
        raise HTTPException(404, '检索范围包含不存在或未授权的知识库。')
    try:
        return {'results': search(user, body.question, body.library_ids or None)}
    except ModelUnavailable as e:
        raise HTTPException(503, str(e)) from None
    except Exception:
        raise HTTPException(503, '检索失败，请检查本地索引与模型状态。') from None


@app.post('/api/qa/questions')
def ask(body: Question, user=Depends(current)):
    if body.library_ids and not set(body.library_ids).issubset(allowed_ids(user)):
        raise HTTPException(404, '问答范围包含不存在或未授权的知识库。')
    with qa.dispatch_lock:
        pending = db.one("SELECT COUNT(*) n FROM questions WHERE status IN ('queued','retrieving','waiting_quota','generating')")['n']
        if pending >= 20:
            raise HTTPException(429, '队列已满（20 个问题），请等待当前问题完成后重试。')
        return {'id': qa.submit(user, body.question, body.library_ids, body.mode)}


@app.get('/api/qa/questions')
def history(user=Depends(current)):
    return [qa.visible_question(user, q) for q in db.rows('SELECT * FROM questions WHERE user_id=? ORDER BY created_at DESC LIMIT 80', (user['id'],))]


def owned(qid, user):
    q = db.one('SELECT * FROM questions WHERE id=? AND user_id=?', (qid, user['id']))
    if not q:
        raise HTTPException(404, '问题不存在。')
    return q


@app.get('/api/qa/questions/{qid}')
def question_state(qid: str, user=Depends(current)):
    return qa.visible_question(user, owned(qid, user))


@app.post('/api/qa/questions/{qid}/cancel')
def cancel(qid: str, user=Depends(current)):
    q = owned(qid, user)
    if q['status'] not in qa.TERMINAL:
        qa.finish(qid, 'cancelled', '已取消处理，问题仍保留在历史中。', [])
    return {'ok': True}


@app.get('/api/qa/questions/{qid}/events')
async def events(qid: str, request: Request, after: int = 0, user=Depends(current)):
    owned(qid, user)
    try:
        after = max(after, int(request.headers.get('last-event-id', '0')))
    except ValueError:
        raise HTTPException(400, '事件游标无效。')

    async def stream():
        cursor = after
        while not await request.is_disconnected():
            try:
                live = current(request)
            except HTTPException:
                yield 'event: done\ndata: {"status":"session_expired"}\n\n'
                return
            visible = qa.visible_question(live, owned(qid, live))
            if visible['status'] == 'source_changed':
                yield 'event: reset\ndata: ' + json.dumps({'answer': visible['answer'], 'citations': []}, ensure_ascii=False) + '\n\n'
                yield 'event: done\ndata: {"status":"source_changed"}\n\n'
                return
            for e in db.rows('SELECT * FROM events WHERE question_id=? AND seq>? ORDER BY seq', (qid, cursor)):
                cursor = e['seq']
                yield f'id: {cursor}\nevent: {e["kind"]}\ndata: {e["payload"]}\n\n'
            if visible['status'] in qa.TERMINAL:
                return
            yield ': heartbeat\n\n'
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})


@app.get('/api/system')
def system(user=Depends(current)):
    import os
    result = {'embedding': models.state, 'detail': models.detail,
              'generation_configured': bool(os.environ.get('AIHUBMIX_API_KEY', '').strip()), 'quota': qa.quota_state(),
              'model': 'coding-kimi-k3-free', 'local_storage': True}
    if user['role'] == 'admin':
        result['queue_count'] = db.one("SELECT COUNT(*) n FROM questions WHERE status IN ('queued','retrieving','waiting_quota','generating')")['n']
    return result


@app.post('/api/demo')
def demo(user=Depends(admin)):
    from .demo import install
    with qa.dispatch_lock:
        result = install(user)
    return result


@app.get('/api/audit')
def audit_log(user=Depends(admin)):
    return db.rows('SELECT a.*,u.display_name actor_name,u.username actor_username FROM audit a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.at DESC LIMIT 200')


dist = ROOT / 'frontend' / 'dist'
if dist.exists():
    app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')

    @app.get('/{path:path}')
    def frontend(path: str):
        if path.startswith('api/'):
            raise HTTPException(404, '接口不存在。')
        return FileResponse(dist / 'index.html')
