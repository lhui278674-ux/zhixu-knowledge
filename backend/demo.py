import hashlib
import json
import time
import uuid
from . import db
from .config import DATA, ROOT
from .retrieval import submit_document


def install(user):
    manifest_path = ROOT / 'demo' / 'manifest.json'
    if not manifest_path.exists():
        raise RuntimeError('演示样例不存在，请运行 scripts/create_demo.py')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    current = db.one("SELECT value FROM settings WHERE key='demo_corpus_version'")
    if current and current['value'] == str(manifest['version']):
        return {'message': '虚构演示已是最新版本。'}
    jobs = []
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        # Recheck after the write lock so concurrent imports remain idempotent.
        current = c.execute("SELECT value FROM settings WHERE key='demo_corpus_version'").fetchone()
        if current and current['value'] == str(manifest['version']):
            return {'message': '虚构演示已是最新版本。'}
        for library in manifest['libraries']:
            existing = c.execute('SELECT id FROM libraries WHERE demo=1 AND name=? ORDER BY created_at LIMIT 1',
                                 (library['name'],)).fetchone()
            lid = existing['id'] if existing else uuid.uuid4().hex
            if not existing:
                c.execute('INSERT INTO libraries VALUES(?,?,?,?,1)',
                          (lid, library['name'], library['description'], time.time()))
            for item in (d for d in manifest['documents'] if d['library'] == library['key']):
                path = (ROOT / 'demo' / item['path']).resolve()
                if not path.is_relative_to((ROOT / 'demo').resolve()):
                    raise RuntimeError('演示文件路径不合法。')
                content = path.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                if digest != item['sha256']:
                    raise RuntimeError('演示文件校验失败，请重新生成演示资料。')
                parent = None
                for folder in item['directory'].split('/'):
                    row = c.execute('SELECT id FROM directories WHERE library_id=? AND parent_id IS ? AND name=?',
                                    (lid, parent, folder)).fetchone()
                    fid = row['id'] if row else uuid.uuid4().hex
                    if not row:
                        c.execute('INSERT INTO directories VALUES(?,?,?,?,?)', (fid, lid, parent, folder, time.time()))
                    parent = fid
                old = c.execute('SELECT id,hash FROM documents WHERE library_id=? AND name=? ORDER BY created_at LIMIT 1',
                                (lid, path.name)).fetchone()
                if old and old['hash'] == digest:
                    continue
                did, version = (old['id'] if old else uuid.uuid4().hex), uuid.uuid4().hex
                (DATA / 'uploads' / did).write_bytes(content)
                if old:
                    c.execute('DELETE FROM chunks WHERE document_id=?', (did,))
                    c.execute("UPDATE documents SET hash=?,size=?,status='queued',detail='',version=?,chunk_count=0,directory_id=? WHERE id=?",
                              (digest, len(content), version, parent, did))
                else:
                    c.execute('''INSERT INTO documents(id,library_id,name,extension,hash,size,status,version,created_at,directory_id)
                                 VALUES(?,?,?,?,?,?,?,?,?,?)''',
                              (did, lid, path.name, path.suffix[1:], digest, len(content), 'queued', version, time.time(), parent))
                jobs.append((did, version))
        c.execute("INSERT OR REPLACE INTO settings VALUES('demo_installed','1')")
        c.execute("INSERT OR REPLACE INTO settings VALUES('demo_corpus_version',?)", (str(manifest['version']),))
    for job in jobs:
        submit_document(*job)
    db.audit(user['id'], 'install_fictional_demo', f"corpus_v{manifest['version']}")
    return {'message': f"已更新 {len(manifest['libraries'])} 个虚构演示文库、{len(manifest['documents'])} 份资料，文档开始处理。"}
