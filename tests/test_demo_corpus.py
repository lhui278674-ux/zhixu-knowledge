"""Corpus export and version-one import upgrade, isolated from real account data."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def test_public_export_is_complete_and_matches_originals():
    manifest = json.loads((ROOT/'demo/manifest.json').read_text(encoding='utf-8'))
    payload = json.loads((ROOT/'frontend/public/demo-data.json').read_text(encoding='utf-8'))
    assert manifest['fictional'] and payload['fictional']
    assert len(manifest['documents']) == len(payload['documents']) == 32
    assert len(payload['libraries']) == 7 and len(payload['directories']) == 20
    assert {d['extension'] for d in payload['documents']} == {'pdf','docx','xlsx','pptx','txt','md'}
    assert sum(len(c['text']) for d in payload['documents'] for c in d['chunks']) > 35000
    for item, doc in zip(manifest['documents'], payload['documents']):
        original = ROOT/'demo'/item['path']
        exported = ROOT/'frontend/public'/doc['file_path']
        assert hashlib.sha256(original.read_bytes()).hexdigest() == item['sha256']
        assert original.read_bytes() == exported.read_bytes()
        assert '虚构' in doc['name'] and doc['chunks']
        assert all(c['locator']['label'] and c['text'].strip() for c in doc['chunks'])
    pdf = next(d for d in payload['documents'] if d['name'].startswith('出差管理'))
    assert any(c['locator']['page']==2 and '电子发票' in c['text'] for c in pdf['chunks'])


def test_old_demo_upgrade_preserves_ids_members_and_real_documents(tmp_path):
    script = r'''
from backend import db, demo
from backend.config import DATA
import json
db.init()
with db.connect() as c:
    c.execute("INSERT INTO users(id,username,display_name,password_hash,role,active,created_at) VALUES('employee','fixture','虚构员工','test','employee',1,1)")
    c.execute("INSERT INTO libraries VALUES('old-demo','员工手册 · 虚构演示','legacy',1,1)")
    c.execute("INSERT INTO libraries VALUES('real-library','合成非演示库','preserve',1,0)")
    c.execute("INSERT INTO members(library_id,user_id,permission) VALUES('old-demo','employee','submit')")
    c.execute("INSERT INTO documents(id,library_id,name,extension,hash,size,status,version,created_at) VALUES('old-doc','old-demo','办公设备指引（虚构）.txt','txt','legacy-hash',3,'ready','old-version',1)")
    c.execute("INSERT INTO documents(id,library_id,name,extension,hash,size,status,version,created_at) VALUES('real-doc','real-library','合成真实库.txt','txt','preserve-hash',8,'ready','keep-version',1)")
    c.execute("INSERT INTO settings VALUES('demo_installed','1')")
(DATA/'uploads/old-doc').write_bytes(b'old')
(DATA/'uploads/real-doc').write_bytes(b'preserve')
jobs=[]
demo.submit_document=lambda *job:jobs.append(job)
demo.install({'id':'employee'})
assert len(jobs)==32
assert db.one("SELECT id FROM libraries WHERE demo=1 AND name LIKE '员工手册%'")['id']=='old-demo'
assert db.one("SELECT version FROM documents WHERE id='old-doc'")['version']!='old-version'
assert db.one("SELECT permission FROM members WHERE library_id='old-demo'")['permission']=='submit'
assert db.one("SELECT version,hash FROM documents WHERE id='real-doc'")=={'version':'keep-version','hash':'preserve-hash'}
assert (DATA/'uploads/real-doc').read_bytes()==b'preserve'
assert db.one('SELECT COUNT(*) AS n FROM libraries')['n']==8
assert db.one('SELECT COUNT(*) AS n FROM documents')['n']==33
assert db.one('SELECT COUNT(*) AS n FROM directories')['n']==20
demo.install({'id':'employee'})
assert len(jobs)==32
assert db.one('SELECT COUNT(*) AS n FROM documents')['n']==33
with db.connect() as c: assert not c.execute('PRAGMA foreign_key_check').fetchall()
print('DEMO_UPGRADE_OK')
'''
    result=subprocess.run([sys.executable,'-c',script],cwd=ROOT,env={**os.environ,'KB_DATA_DIR':str(tmp_path),'PYTHONUTF8':'1'},capture_output=True,text=True,timeout=60)
    assert result.returncode==0, result.stdout+result.stderr
    assert 'DEMO_UPGRADE_OK' in result.stdout
