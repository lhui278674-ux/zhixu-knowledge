"""Document browsing, folders, personal lists, and reviewed contributions."""
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from . import db, qa
from .accounts import Decision, audit_in
from .auth import admin, allowed_ids, current, document_access, library_access, submission_access
from .config import DATA, MAX_FILE
from .retrieval import submit_document

router = APIRouter(prefix='/api')


async def read_upload(file):
    name = (file.filename or '').replace('\\', '/').split('/')[-1][:180]
    ext = Path(name).suffix.lower().lstrip('.')
    try:
        if ext not in {'pdf', 'docx', 'xlsx', 'pptx', 'txt', 'md'}:
            raise HTTPException(415, '仅支持 PDF、DOCX、XLSX、PPTX、TXT 和 Markdown。')
        content = await file.read(MAX_FILE + 1)
        if len(content) > MAX_FILE:
            raise HTTPException(413, '文件超过 25 MB 上限。')
        if not content:
            raise HTTPException(422, '文件内容为空。')
        return name, ext, content
    finally:
        await file.close()


def check_directory(lid, directory_id, c=None):
    if directory_id:
        sql = 'SELECT id FROM directories WHERE id=? AND library_id=?'
        found = c.execute(sql, (directory_id, lid)).fetchone() if c else db.one(sql, (directory_id, lid))
        if not found:
            raise HTTPException(404, '目录不存在。')


def subtree(lid, directory_id):
    check_directory(lid, directory_id)
    rows = db.rows('SELECT id,parent_id FROM directories WHERE library_id=?', (lid,))
    found = {directory_id}
    while True:
        children = {r['id'] for r in rows if r['parent_id'] in found}
        if children.issubset(found):
            return found
        found |= children


class Folder(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    parent_id: str | None = None


class MoveDocument(BaseModel):
    directory_id: str | None = None


class LibraryEdit(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default='', max_length=300)


@router.patch('/libraries/{lid}')
def edit_library(lid: str, body: LibraryEdit, user=Depends(admin)):
    library_access(user, lid)
    db.execute('UPDATE libraries SET name=?,description=? WHERE id=?', (body.name, body.description, lid))
    db.audit(user['id'], 'edit_library', lid)
    return {'ok': True}


@router.delete('/libraries/{lid}')
def delete_library(lid: str, user=Depends(admin)):
    library_access(user, lid)
    with qa.dispatch_lock, db.connect() as c:
        documents = c.execute('SELECT id FROM documents WHERE library_id=?', (lid,)).fetchall()
        submissions = c.execute('SELECT id FROM submissions WHERE library_id=?', (lid,)).fetchall()
        c.execute('DELETE FROM libraries WHERE id=?', (lid,))
        audit_in(c, user['id'], 'delete_library', lid)
    for d in documents:
        (DATA / 'uploads' / d['id']).unlink(missing_ok=True)
    for s in submissions:
        (DATA / 'submissions' / s['id']).unlink(missing_ok=True)
    return {'ok': True}


@router.get('/libraries/{lid}/directories')
def directories(lid: str, user=Depends(current)):
    library_access(user, lid)
    return db.rows('''SELECT f.*,(SELECT COUNT(*) FROM documents d WHERE d.directory_id=f.id) document_count
                      FROM directories f WHERE f.library_id=? ORDER BY f.created_at,f.id''', (lid,))


@router.post('/libraries/{lid}/directories')
def create_folder(lid: str, body: Folder, user=Depends(admin)):
    library_access(user, lid)
    fid = uuid.uuid4().hex
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        check_directory(lid, body.parent_id, c)
        name = body.name.strip()
        if not name:
            raise HTTPException(422, '请输入目录名称。')
        if c.execute('SELECT 1 FROM directories WHERE library_id=? AND parent_id IS ? AND name=?',
                     (lid, body.parent_id, name)).fetchone():
            raise HTTPException(409, '同级目录名称已存在。')
        c.execute('INSERT INTO directories VALUES(?,?,?,?,?)', (fid, lid, body.parent_id, name, time.time()))
        audit_in(c, user['id'], 'create_directory', fid)
    return {'id': fid}


@router.patch('/directories/{fid}')
def edit_folder(fid: str, body: Folder, user=Depends(admin)):
    folder = db.one('SELECT * FROM directories WHERE id=?', (fid,))
    if not folder:
        raise HTTPException(404, '目录不存在。')
    library_access(user, folder['library_id'])
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        check_directory(folder['library_id'], body.parent_id, c)
        if body.parent_id in subtree(folder['library_id'], fid):
            raise HTTPException(409, '目录不能移入自身或子目录。')
        name = body.name.strip()
        if not name:
            raise HTTPException(422, '请输入目录名称。')
        if c.execute('SELECT 1 FROM directories WHERE library_id=? AND parent_id IS ? AND name=? AND id!=?',
                     (folder['library_id'], body.parent_id, name, fid)).fetchone():
            raise HTTPException(409, '同级目录名称已存在。')
        c.execute('UPDATE directories SET name=?,parent_id=? WHERE id=?', (name, body.parent_id, fid))
        audit_in(c, user['id'], 'edit_directory', fid)
    return {'ok': True}


@router.delete('/directories/{fid}')
def delete_folder(fid: str, user=Depends(admin)):
    folder = db.one('SELECT * FROM directories WHERE id=?', (fid,))
    if not folder:
        raise HTTPException(404, '目录不存在。')
    library_access(user, folder['library_id'])
    with db.connect() as c:
        c.execute('DELETE FROM directories WHERE id=?', (fid,))
        audit_in(c, user['id'], 'delete_directory', fid)
    return {'ok': True}


@router.patch('/documents/{did}')
def move_document(did: str, body: MoveDocument, user=Depends(admin)):
    doc = document_access(user, did)
    with db.connect() as c:
        check_directory(doc['library_id'], body.directory_id, c)
        c.execute('UPDATE documents SET directory_id=? WHERE id=?', (body.directory_id, did))
        audit_in(c, user['id'], 'move_document', did)
    return {'ok': True}


@router.get('/portal/documents')
@router.get('/documents/search')
def browse_documents(q: str = Query(default='', max_length=200),
                     field: Literal['all', 'title', 'content'] = 'all',
                     view: Literal['all', 'favorites', 'recent'] = 'all',
                     library_id: str | None = None, directory_id: str | None = None,
                     extension: Literal['pdf', 'docx', 'xlsx', 'pptx', 'txt', 'md'] | None = None,
                     offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
                     user=Depends(current)):
    permitted = allowed_ids(user)
    if library_id:
        library_access(user, library_id)
        permitted = [library_id]
    if directory_id and not library_id:
        raise HTTPException(422, '请指定目录所属的文库。')
    if not permitted:
        return {'results': [], 'total': 0}
    clauses = [f"d.library_id IN ({','.join('?' for _ in permitted)})"]
    params = [user['id'], user['id'], *permitted]
    if directory_id:
        scope = sorted(subtree(library_id, directory_id))
        clauses.append(f"d.directory_id IN ({','.join('?' for _ in scope)})")
        params.extend(scope)
    if extension:
        clauses.append('d.extension=?')
        params.append(extension)
    if view == 'favorites':
        clauses.append('f.document_id IS NOT NULL')
    if view == 'recent':
        clauses.append('r.document_id IS NOT NULL')
    term = q.strip()
    if term:
        title = 'instr(lower(d.name),lower(?))>0'
        content = "EXISTS(SELECT 1 FROM chunks c WHERE c.document_id=d.id AND d.status='ready' AND instr(lower(c.text),lower(?))>0)"
        clauses.append(title if field == 'title' else content if field == 'content' else f'({title} OR {content})')
        params.extend([term] * (2 if field == 'all' else 1))
    order = 'r.viewed_at DESC' if view == 'recent' else 'f.created_at DESC' if view == 'favorites' else 'd.created_at DESC,d.id'
    base = '''FROM documents d JOIN libraries l ON l.id=d.library_id
              LEFT JOIN directories dir ON dir.id=d.directory_id
              LEFT JOIN favorites f ON f.document_id=d.id AND f.user_id=?
              LEFT JOIN recent_views r ON r.document_id=d.id AND r.user_id=? WHERE ''' + ' AND '.join(clauses)
    total = db.one('SELECT COUNT(*) n ' + base, params)['n']
    rows = db.rows('''SELECT d.*,l.name library_name,dir.name directory_name,
                     f.document_id IS NOT NULL favorite,r.viewed_at ''' + base + ' ORDER BY ' + order + ' LIMIT ? OFFSET ?', params + [limit, offset])
    for row in rows:
        chunk = None
        if term and field != 'title':
            chunk = db.one("SELECT id,text,locator FROM chunks WHERE document_id=? AND instr(lower(text),lower(?))>0 ORDER BY ordinal LIMIT 1", (row['id'], term))
        if chunk:
            position = chunk['text'].lower().find(term.lower())
            start = max(0, position - 50)
            row.update({'snippet': ('…' if start else '') + chunk['text'][start:start + 180],
                        'chunk_id': chunk['id'], 'locator': json.loads(chunk['locator']), 'match': 'content'})
        else:
            row.update({'snippet': '', 'chunk_id': None, 'locator': None, 'match': 'title' if term else None})
    return {'results': rows, 'total': total}


@router.put('/favorites/{did}')
def favorite(did: str, user=Depends(current)):
    document_access(user, did)
    db.execute('INSERT OR IGNORE INTO favorites VALUES(?,?,?)', (user['id'], did, time.time()))
    return {'ok': True}


@router.delete('/favorites/{did}')
def unfavorite(did: str, user=Depends(current)):
    document_access(user, did)
    db.execute('DELETE FROM favorites WHERE user_id=? AND document_id=?', (user['id'], did))
    return {'ok': True}


@router.get('/submissions')
def submissions(user=Depends(current)):
    ids = allowed_ids(user)
    if not ids:
        return []
    params = ids[:]
    where = f"s.library_id IN ({','.join('?' for _ in ids)})"
    if user['role'] != 'admin':
        where += ' AND s.user_id=?'
        params.append(user['id'])
    return db.rows('''SELECT s.*,l.name library_name,u.display_name submitter_name,d.status document_status
                      FROM submissions s JOIN libraries l ON s.library_id=l.id JOIN users u ON s.user_id=u.id
                      LEFT JOIN documents d ON d.id=s.document_id WHERE ''' + where + ' ORDER BY s.created_at DESC LIMIT 200', params)


@router.post('/libraries/{lid}/submissions')
async def submit_file(lid: str, file: UploadFile = File(...), directory_id: str | None = Form(None), user=Depends(current)):
    submission_access(user, lid)
    check_directory(lid, directory_id)
    name, ext, content = await read_upload(file)
    sid = uuid.uuid4().hex
    digest = hashlib.sha256(content).hexdigest()
    with qa.dispatch_lock, db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        submission_access(user, lid)
        check_directory(lid, directory_id, c)
        existing = c.execute('SELECT id FROM documents WHERE library_id=? AND hash=?', (lid, digest)).fetchone()
        pending = c.execute("SELECT id FROM submissions WHERE library_id=? AND user_id=? AND hash=? AND status='pending'", (lid, user['id'], digest)).fetchone()
        if existing or pending:
            return {'id': (existing or pending)['id'], 'duplicate': True, 'message': '该资料已入库或已提交，请勿重复上传。'}
        try:
            (DATA / 'submissions' / sid).write_bytes(content)
            c.execute('''INSERT INTO submissions(id,library_id,directory_id,user_id,name,extension,hash,size,created_at)
                         VALUES(?,?,?,?,?,?,?,?,?)''',
                      (sid, lid, directory_id or None, user['id'], name, ext, digest, len(content), time.time()))
            audit_in(c, user['id'], 'submission_requested', sid)
        except Exception:
            (DATA / 'submissions' / sid).unlink(missing_ok=True)
            raise
    return {'id': sid, 'duplicate': False, 'message': '资料已提交，等待审核。'}


@router.get('/submissions/{sid}/file')
def submission_file(sid: str, user=Depends(current)):
    submission = db.one('SELECT * FROM submissions WHERE id=?', (sid,))
    if not submission:
        raise HTTPException(404, '资料不存在。')
    library_access(user, submission['library_id'])
    if user['role'] != 'admin' and submission['user_id'] != user['id']:
        raise HTTPException(404, '资料不存在。')
    return FileResponse(DATA / 'submissions' / sid, filename=submission['name'], media_type='application/octet-stream')


@router.post('/submissions/{sid}/review')
def review_submission(sid: str, body: Decision, user=Depends(admin)):
    job = None
    written = False
    try:
        with qa.dispatch_lock, db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            submission = c.execute('SELECT * FROM submissions WHERE id=?', (sid,)).fetchone()
            if not submission or submission['status'] != 'pending':
                raise HTTPException(409, '资料不存在或已经审核。')
            did = None
            if body.approve:
                owner = c.execute('SELECT * FROM users WHERE id=?', (submission['user_id'],)).fetchone()
                permission = c.execute("SELECT permission FROM members WHERE library_id=? AND user_id=?", (submission['library_id'], submission['user_id'])).fetchone()
                if not owner['active'] or owner['review_status'] != 'approved' or (owner['role'] != 'admin' and (not permission or permission['permission'] != 'submit')):
                    raise HTTPException(409, '提交者当前未开通资料提交，请更新授权或驳回此资料。')
                check_directory(submission['library_id'], submission['directory_id'], c)
                existing = c.execute('SELECT id FROM documents WHERE library_id=? AND hash=?', (submission['library_id'], submission['hash'])).fetchone()
                if existing:
                    did = existing['id']
                else:
                    did, version = sid, uuid.uuid4().hex
                    (DATA / 'uploads' / did).write_bytes((DATA / 'submissions' / sid).read_bytes())
                    written = True
                    c.execute('''INSERT INTO documents(id,library_id,directory_id,name,extension,hash,size,status,version,created_at)
                                 VALUES(?,?,?,?,?,?,?,'queued',?,?)''',
                              (did, submission['library_id'], submission['directory_id'], submission['name'], submission['extension'],
                               submission['hash'], submission['size'], version, time.time()))
                    job = did, version
            c.execute('UPDATE submissions SET status=?,note=?,reviewed_by=?,reviewed_at=?,document_id=? WHERE id=?',
                      ('approved' if body.approve else 'rejected', body.note, user['id'], time.time(), did, sid))
            audit_in(c, user['id'], 'submission_approved' if body.approve else 'submission_rejected', sid)
    except Exception:
        if written:
            (DATA / 'uploads' / sid).unlink(missing_ok=True)
        raise
    # Only the committed approval can enqueue parsing; restart recovery uses documents.
    if job:
        submit_document(*job)
    return {'ok': True, 'document_id': did}
