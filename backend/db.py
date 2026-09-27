import sqlite3
from contextlib import contextmanager
from .config import DB


@contextmanager
def connect():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=30000')
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def init():
    with connect() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(
          id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
          password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','employee')),
          active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(
          token_hash TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
          expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS libraries(
          id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
          created_at REAL NOT NULL, demo INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS members(
          library_id TEXT REFERENCES libraries(id) ON DELETE CASCADE,
          user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
          PRIMARY KEY(library_id,user_id));
        CREATE TABLE IF NOT EXISTS documents(
          id TEXT PRIMARY KEY, library_id TEXT NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
          name TEXT NOT NULL, extension TEXT NOT NULL, hash TEXT NOT NULL, size INTEGER NOT NULL,
          status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', version TEXT NOT NULL,
          created_at REAL NOT NULL, chunk_count INTEGER NOT NULL DEFAULT 0,
          UNIQUE(library_id,hash));
        CREATE TABLE IF NOT EXISTS chunks(
          id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
          text TEXT NOT NULL, locator TEXT NOT NULL, ordinal INTEGER NOT NULL,
          vector BLOB NOT NULL, model TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);
        CREATE TABLE IF NOT EXISTS questions(
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), question TEXT NOT NULL,
          library_ids TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL,
          answer TEXT NOT NULL DEFAULT '', citations TEXT NOT NULL DEFAULT '[]',
          created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
          seq INTEGER PRIMARY KEY AUTOINCREMENT, question_id TEXT REFERENCES questions(id) ON DELETE CASCADE,
          kind TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS events_question ON events(question_id,seq);
        CREATE TABLE IF NOT EXISTS model_calls(
          id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, tokens INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(
          id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, user_id TEXT,
          action TEXT NOT NULL, target TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS login_attempts(at REAL NOT NULL, source TEXT NOT NULL);
        ''')
        # Additive, idempotent upgrade: existing identities, vectors and question IDs stay intact.
        additions = {
            'users': [('review_status', "TEXT NOT NULL DEFAULT 'approved'"),
                      ('review_note', "TEXT NOT NULL DEFAULT ''")],
            'members': [('permission', "TEXT NOT NULL DEFAULT 'view'")],
            'documents': [('directory_id', 'TEXT REFERENCES directories(id) ON DELETE SET NULL')],
        }
        c.executescript('''
        CREATE TABLE IF NOT EXISTS directories(
          id TEXT PRIMARY KEY, library_id TEXT NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
          parent_id TEXT REFERENCES directories(id) ON DELETE CASCADE,
          name TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS directories_library ON directories(library_id);
        CREATE TABLE IF NOT EXISTS favorites(
          user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
          document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
          created_at REAL NOT NULL, PRIMARY KEY(user_id,document_id));
        CREATE TABLE IF NOT EXISTS recent_views(
          user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
          document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
          viewed_at REAL NOT NULL, PRIMARY KEY(user_id,document_id));
        CREATE TABLE IF NOT EXISTS submissions(
          id TEXT PRIMARY KEY, library_id TEXT NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
          directory_id TEXT REFERENCES directories(id) ON DELETE SET NULL,
          user_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL, extension TEXT NOT NULL,
          hash TEXT NOT NULL, size INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          note TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
          reviewed_by TEXT REFERENCES users(id), reviewed_at REAL,
          document_id TEXT REFERENCES documents(id) ON DELETE SET NULL);
        CREATE INDEX IF NOT EXISTS submissions_status ON submissions(status);
        CREATE TABLE IF NOT EXISTS admin_changes(
          id TEXT PRIMARY KEY, action TEXT NOT NULL, target_id TEXT REFERENCES users(id),
          requested_by TEXT NOT NULL REFERENCES users(id), reviewer_id TEXT NOT NULL REFERENCES users(id),
          payload TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          created_at REAL NOT NULL, reviewed_at REAL, note TEXT NOT NULL DEFAULT '');
        ''')
        for table, columns in additions.items():
            existing = {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}
            for name, declaration in columns:
                if name not in existing:
                    c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
        # Existing multi-admin installations already have distinct accounts. Assign their
        # initial duties once; a single-admin installation uses the authenticated setup UI.
        admins = c.execute("SELECT id FROM users WHERE role='admin' AND active=1 AND review_status='approved' ORDER BY created_at,id").fetchall()
        if len(admins) >= 2 and not c.execute("SELECT 1 FROM settings WHERE key='governance_initiator'").fetchone():
            c.executemany('INSERT INTO settings(key,value) VALUES(?,?)',
                          [('governance_initiator', admins[0]['id']), ('governance_reviewer', admins[1]['id'])])
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('schema_version','2')")


def rows(sql, params=()):
    with connect() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def one(sql, params=()):
    r = rows(sql, params)
    return r[0] if r else None


def execute(sql, params=()):
    with connect() as c:
        c.execute(sql, params)


def audit(user_id, action, target):
    import time
    execute('INSERT INTO audit(at,user_id,action,target) VALUES(?,?,?,?)',
            (time.time(), user_id, action, target))
