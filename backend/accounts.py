"""Account lifecycle and the two designated governance duties."""
import hashlib
import json
import sqlite3
import time
import uuid
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from . import db, qa
from .auth import admin, current, hash_password, session, verify_password

router = APIRouter(prefix='/api')


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r'^[a-zA-Z0-9_.-]+$')
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(default='管理员', min_length=1, max_length=40)


class Login(Credentials):
    portal: Literal['employee', 'admin'] = 'employee'


class Setup(BaseModel):
    initiator: Credentials
    reviewer: Credentials


class CompleteSetup(BaseModel):
    second_admin: Credentials
    current_duty: Literal['initiator', 'reviewer'] = 'initiator'


class NewUser(Credentials):
    role: Literal['employee', 'admin'] = 'employee'


class Decision(BaseModel):
    approve: bool
    note: str = Field(default='', max_length=300)


class RegistrationDecision(Decision):
    grants: dict[str, Literal['view', 'submit']] = Field(default_factory=dict)


class UserState(BaseModel):
    active: bool


class AdminChange(BaseModel):
    action: Literal['promote', 'demote', 'deactivate', 'reactivate', 'duties']
    target_id: str | None = None
    initiator_id: str | None = None
    reviewer_id: str | None = None
    reason: str = Field(min_length=1, max_length=300)


def clean_user(u):
    return {k: u[k] for k in ('id', 'username', 'display_name', 'role', 'active', 'review_status', 'review_note') if k in u}


def insert_user(c, body, role, status='approved'):
    uid = uuid.uuid4().hex
    try:
        c.execute('''INSERT INTO users(id,username,display_name,password_hash,role,active,created_at,review_status)
                     VALUES(?,?,?,?,?,1,?,?)''',
                  (uid, body.username, body.display_name, hash_password(body.password), role, time.time(), status))
    except sqlite3.IntegrityError:
        raise HTTPException(409, '账号已存在。') from None
    return uid


def audit_in(c, user_id, action, target):
    c.execute('INSERT INTO audit(at,user_id,action,target) VALUES(?,?,?,?)', (time.time(), user_id, action, target))


def duties_in(c):
    settings = dict(c.execute("SELECT key,value FROM settings WHERE key IN ('governance_initiator','governance_reviewer')").fetchall())
    return settings.get('governance_initiator'), settings.get('governance_reviewer')


def set_duties(c, initiator, reviewer):
    c.executemany('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
                  [('governance_initiator', initiator), ('governance_reviewer', reviewer)])


@router.get('/auth/status')
def auth_status():
    return {'needs_setup': not bool(db.one('SELECT id FROM users LIMIT 1'))}


@router.post('/auth/setup')
def setup(body: Setup, response: Response):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT id FROM users LIMIT 1').fetchone():
            raise HTTPException(409, '管理员已经建立，请登录。')
        if body.initiator.username == body.reviewer.username:
            raise HTTPException(409, '请输入两个不同的账号。')
        initiator = insert_user(c, body.initiator, 'admin')
        reviewer = insert_user(c, body.reviewer, 'admin')
        set_duties(c, initiator, reviewer)
        audit_in(c, initiator, 'setup_two_admins', reviewer)
    session(response, initiator)
    return clean_user(db.one('SELECT * FROM users WHERE id=?', (initiator,)))


@router.get('/governance')
def governance(user=Depends(admin)):
    with db.connect() as c:
        initiator, reviewer = duties_in(c)
    return {'needs_completion': not (initiator and reviewer), 'initiator_id': initiator, 'reviewer_id': reviewer}


@router.post('/auth/complete-setup')
def complete_setup(body: CompleteSetup, user=Depends(admin)):
    with qa.dispatch_lock, db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        initiator, reviewer = duties_in(c)
        admins = c.execute("SELECT id FROM users WHERE role='admin' AND active=1 AND review_status='approved'").fetchall()
        if initiator or reviewer or len(admins) != 1 or admins[0]['id'] != user['id']:
            raise HTTPException(409, '管理员设置已完成，或当前账号不能执行此设置。')
        second = insert_user(c, body.second_admin, 'admin')
        set_duties(c, user['id'] if body.current_duty == 'initiator' else second,
                   second if body.current_duty == 'initiator' else user['id'])
        audit_in(c, user['id'], 'complete_admin_setup', second)
    return {'ok': True}


@router.post('/auth/register')
def register(body: Credentials, request: Request):
    throttle(request, 'register', 8)
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if not c.execute('SELECT id FROM users LIMIT 1').fetchone():
            raise HTTPException(409, '空间尚未初始化，请联系管理员。')
        uid = insert_user(c, body, 'employee', 'pending')
        audit_in(c, uid, 'registration_requested', uid)
    return {'id': uid, 'status': 'pending', 'message': '申请已提交，请等待审核后登录。'}


def throttle(request, action, limit):
    source = action + ':' + (request.client.host if request.client else 'local')
    now = time.time()
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM login_attempts WHERE at<?', (now - 300,))
        if c.execute('SELECT COUNT(*) FROM login_attempts WHERE source=?', (source,)).fetchone()[0] >= limit:
            raise HTTPException(429, '尝试过于频繁，请 5 分钟后再试。')
        c.execute('INSERT INTO login_attempts VALUES(?,?)', (now, source))


@router.post('/auth/login')
def login(body: Login, request: Request, response: Response):
    throttle(request, 'login', 15)
    u = db.one('SELECT * FROM users WHERE username=?', (body.username,))
    if not u or not verify_password(body.password, u['password_hash']):
        raise HTTPException(401, '账号或密码错误。')
    if u['review_status'] != 'approved':
        raise HTTPException(403, '注册申请正在审核。' if u['review_status'] == 'pending' else '注册申请未通过，请联系管理员。')
    if not u['active']:
        raise HTTPException(403, '账号已停用，请联系管理员。')
    if body.portal == 'admin' and u['role'] != 'admin':
        raise HTTPException(403, '请使用员工登录入口。')
    session(response, u['id'])
    return clean_user(u)


@router.post('/auth/logout')
def logout(request: Request, response: Response):
    token = request.cookies.get('kb_session', '')
    db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie('kb_session')
    return {'ok': True}


@router.get('/auth/me')
def me(user=Depends(current)):
    return user


@router.get('/users')
def users(user=Depends(admin)):
    return [clean_user(u) for u in db.rows('SELECT * FROM users ORDER BY created_at')]


@router.post('/users')
def create_user(body: NewUser, user=Depends(admin)):
    if body.role == 'admin':
        raise HTTPException(409, '请通过管理员变更申请设置管理员。')
    with db.connect() as c:
        uid = insert_user(c, body, 'employee')
        audit_in(c, user['id'], 'create_user', uid)
    return clean_user(db.one('SELECT * FROM users WHERE id=?', (uid,)))


@router.get('/registrations')
def registrations(user=Depends(admin)):
    return [clean_user(u) | {'created_at': u['created_at']} for u in
            db.rows("SELECT * FROM users WHERE review_status!='approved' ORDER BY created_at DESC")]


@router.post('/registrations/{uid}/review')
def review_registration(uid: str, body: RegistrationDecision, user=Depends(admin)):
    with qa.dispatch_lock, db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        target = c.execute("SELECT * FROM users WHERE id=? AND role='employee' AND review_status='pending'", (uid,)).fetchone()
        if not target:
            raise HTTPException(409, '申请不存在或已经处理。')
        if body.approve:
            for lid, permission in body.grants.items():
                if not c.execute('SELECT 1 FROM libraries WHERE id=?', (lid,)).fetchone():
                    raise HTTPException(404, '知识库不存在。')
                c.execute('INSERT INTO members(library_id,user_id,permission) VALUES(?,?,?)', (lid, uid, permission))
        c.execute('UPDATE users SET review_status=?,review_note=? WHERE id=?',
                  ('approved' if body.approve else 'rejected', body.note, uid))
        audit_in(c, user['id'], 'registration_approved' if body.approve else 'registration_rejected', uid)
    return {'ok': True}


@router.patch('/users/{uid}')
def user_state(uid: str, body: UserState, user=Depends(admin)):
    with qa.dispatch_lock, db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        target = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        if not target:
            raise HTTPException(404, '账号不存在。')
        if target['role'] == 'admin':
            raise HTTPException(409, '请提交管理员变更申请。')
        if target['review_status'] != 'approved':
            raise HTTPException(409, '请先处理注册申请。')
        c.execute('UPDATE users SET active=? WHERE id=?', (int(body.active), uid))
        c.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
        audit_in(c, user['id'], 'user_state', uid)
    return {'ok': True}


@router.get('/admin-changes')
def changes(user=Depends(admin)):
    return db.rows('''SELECT a.*,t.display_name target_name,r.display_name requester_name,
                      v.display_name reviewer_name FROM admin_changes a
                      LEFT JOIN users t ON t.id=a.target_id JOIN users r ON r.id=a.requested_by
                      JOIN users v ON v.id=a.reviewer_id ORDER BY a.created_at DESC LIMIT 100''')


def validate_change(c, body, initiator, reviewer):
    if body.action == 'duties':
        if not body.initiator_id or not body.reviewer_id or body.initiator_id == body.reviewer_id:
            raise HTTPException(409, '请选择两位不同的管理员。')
        for uid in (body.initiator_id, body.reviewer_id):
            if not c.execute("SELECT 1 FROM users WHERE id=? AND role='admin' AND active=1 AND review_status='approved'", (uid,)).fetchone():
                raise HTTPException(409, '职责账号必须为已启用的管理员。')
        if (body.initiator_id, body.reviewer_id) == (initiator, reviewer):
            raise HTTPException(409, '职责没有变化。')
        return None
    target = c.execute('SELECT * FROM users WHERE id=?', (body.target_id,)).fetchone()
    if not target or target['review_status'] != 'approved':
        raise HTTPException(404, '账号不存在或尚未通过审核。')
    if body.action == 'promote':
        if target['role'] != 'employee' or not target['active']:
            raise HTTPException(409, '请选择已启用的员工。')
    else:
        if target['role'] != 'admin':
            raise HTTPException(409, '请选择管理员。')
        if body.action in {'demote', 'deactivate'} and target['id'] in (initiator, reviewer):
            raise HTTPException(409, '请先完成该账号的职责交接。')
        if body.action == 'deactivate' and not target['active'] or body.action == 'reactivate' and target['active']:
            raise HTTPException(409, '账号状态没有变化。')
    return dict(target)


@router.post('/admin-changes')
def request_change(body: AdminChange, user=Depends(admin)):
    with qa.dispatch_lock, db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        initiator, reviewer = duties_in(c)
        if not initiator or not reviewer:
            raise HTTPException(409, '请先完成管理员设置。')
        if user['id'] != initiator:
            raise HTTPException(403, '当前账号未分配变更申请职责。')
        target = validate_change(c, body, initiator, reviewer)
        if c.execute("SELECT 1 FROM admin_changes WHERE status='pending'").fetchone():
            raise HTTPException(409, '请先处理待复核的管理员变更。')
        payload = body.model_dump() | {'old_duties': [initiator, reviewer],
                   'old_target': {k: target[k] for k in ('role', 'active')} if target else None}
        cid = uuid.uuid4().hex
        c.execute('''INSERT INTO admin_changes(id,action,target_id,requested_by,reviewer_id,payload,reason,created_at)
                     VALUES(?,?,?,?,?,?,?,?)''',
                  (cid, body.action, body.target_id if target else None, user['id'], reviewer,
                   json.dumps(payload), body.reason, time.time()))
        audit_in(c, user['id'], 'admin_change_requested', cid)
    return {'id': cid}


@router.post('/admin-changes/{cid}/review')
def review_change(cid: str, body: Decision, user=Depends(admin)):
    with qa.dispatch_lock, db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        change = c.execute('SELECT * FROM admin_changes WHERE id=?', (cid,)).fetchone()
        if not change or change['status'] != 'pending':
            raise HTTPException(409, '申请不存在或已经处理。')
        initiator, reviewer = duties_in(c)
        if user['id'] == change['requested_by'] or user['id'] != change['reviewer_id'] or user['id'] != reviewer:
            raise HTTPException(403, '当前账号不能复核此申请。')
        if body.approve:
            payload = json.loads(change['payload'])
            if payload['old_duties'] != [initiator, reviewer]:
                raise HTTPException(409, '职责已变更，请重新提交申请。')
            proposal = AdminChange(**payload)
            target = validate_change(c, proposal, initiator, reviewer)
            if target and payload['old_target'] != {k: target[k] for k in ('role', 'active')}:
                raise HTTPException(409, '账号状态已变更，请重新提交申请。')
            if proposal.action == 'duties':
                set_duties(c, proposal.initiator_id, proposal.reviewer_id)
            elif proposal.action in {'promote', 'demote'}:
                c.execute('UPDATE users SET role=? WHERE id=?',
                          ('admin' if proposal.action == 'promote' else 'employee', target['id']))
            else:
                c.execute('UPDATE users SET active=? WHERE id=?', (int(proposal.action == 'reactivate'), target['id']))
            if target:
                c.execute('DELETE FROM sessions WHERE user_id=?', (target['id'],))
        c.execute('UPDATE admin_changes SET status=?,reviewed_at=?,note=? WHERE id=?',
                  ('approved' if body.approve else 'rejected', time.time(), body.note, cid))
        audit_in(c, user['id'], 'admin_change_approved' if body.approve else 'admin_change_rejected', cid)
    return {'ok': True}
