import hashlib
import hmac
import secrets
import time
from fastapi import Depends, HTTPException, Request
from . import db


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + ':' + digest.hex()


def verify_password(password, stored):
    salt, digest = stored.split(':')
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return hmac.compare_digest(candidate.hex(), digest)


def session(response, user_id):
    token = secrets.token_urlsafe(32)
    db.execute('INSERT INTO sessions VALUES(?,?,?)',
               (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time() + 43200))
    response.set_cookie('kb_session', token, httponly=True, samesite='strict', max_age=43200)


def current(request: Request):
    token = request.cookies.get('kb_session', '')
    user = db.one('''SELECT u.id,u.username,u.display_name,u.role,u.active,u.review_status FROM sessions s
                     JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>? AND u.active=1
                     AND u.review_status='approved' ''',
                  (hashlib.sha256(token.encode()).hexdigest(), time.time()))
    if not user:
        raise HTTPException(401, '请登录，或会话已过期。')
    return user


def admin(user=Depends(current)):
    if user['role'] != 'admin':
        raise HTTPException(403, '仅管理员可执行此操作。')
    return user


def allowed_ids(user):
    if user['role'] == 'admin':
        return [r['id'] for r in db.rows('SELECT id FROM libraries')]
    return [r['library_id'] for r in db.rows('SELECT library_id FROM members WHERE user_id=?', (user['id'],))]


def question_library_ids(user, requested=None):
    permitted = set(allowed_ids(user))
    if requested:
        return [lid for lid in requested if lid in permitted]
    # Demo data is opt-in even when no enterprise library has been created yet.
    return [r['id'] for r in db.rows('SELECT id FROM libraries WHERE demo=0') if r['id'] in permitted]


def library_access(user, library_id):
    if library_id not in allowed_ids(user):
        raise HTTPException(404, '知识库不存在或未授权。')


def submission_access(user, library_id):
    library_access(user, library_id)
    if user['role'] != 'admin' and not db.one(
            "SELECT 1 FROM members WHERE library_id=? AND user_id=? AND permission='submit'",
            (library_id, user['id'])):
        raise HTTPException(403, '当前文库未开通资料提交。')


def document_access(user, doc_id):
    d = db.one('SELECT * FROM documents WHERE id=?', (doc_id,))
    if not d:
        raise HTTPException(404, '文档不存在或未授权。')
    library_access(user, d['library_id'])
    return d
