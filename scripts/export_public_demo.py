"""Export only the authored fictional corpus; never open the application database."""
import hashlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.parsers import parse


def export():
    manifest = json.loads((ROOT / 'demo/manifest.json').read_text(encoding='utf-8'))
    output = ROOT / 'frontend/public'
    output.mkdir(parents=True, exist_ok=True)
    libraries, directories, documents = [], [], []
    for item in manifest['libraries']:
        libraries.append({'id': 'demo-' + item['key'], 'name': item['name'],
                          'description': item['description'], 'demo': 1})
    for item in manifest['documents']:
        path = ROOT / 'demo' / item['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
        chunks, warning = parse(path, path.suffix[1:])
        did = 'doc-' + item['code']
        lid = 'demo-' + item['library']
        parent = None
        for folder in item['directory'].split('/'):
            found = next((d for d in directories if d['library_id'] == lid and d['parent_id'] == parent and d['name'] == folder), None)
            if not found:
                found = {'id': f'folder-{len(directories)+1}', 'library_id': lid, 'parent_id': parent, 'name': folder}
                directories.append(found)
            parent = found['id']
        documents.append({'id': did, 'library_id': lid, 'name': path.name, 'extension': path.suffix[1:],
                          'status': 'ready', 'detail': warning, 'size': path.stat().st_size,
                          'chunk_count': len(chunks), 'created_at': 1790294400, 'directory_id': parent,
                          'file_path': 'demo-files/' + item['path'], 'favorite': 0,
                          'chunks': [{'id': f'{did}-chunk-{i}', 'text': text, 'locator': loc}
                                     for i, (text, loc) in enumerate(chunks, 1)]})
        destination = output / 'demo-files' / item['path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    payload = {'version': manifest['version'], 'fictional': True, 'notice': manifest['notice'],
               'libraries': libraries, 'directories': directories, 'documents': documents}
    (output / 'demo-data.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    metrics = {'documents': len(documents), 'libraries': len(libraries), 'directories': len(directories),
               'formats': dict(Counter(d['extension'] for d in documents)),
               'characters': sum(sum(len(c['text']) for c in d['chunks']) for d in documents),
               'chunks': sum(d['chunk_count'] for d in documents),
               'smallest_document_characters': min(sum(len(c['text']) for c in d['chunks']) for d in documents),
               'per_document': [{'name': d['name'], 'characters': sum(len(c['text']) for c in d['chunks']),
                                 'chunks': d['chunk_count']} for d in documents]}
    (output / 'demo-metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in metrics.items() if k != 'per_document'}, ensure_ascii=False))


if __name__ == '__main__':
    export()
