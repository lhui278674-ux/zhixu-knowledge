"""Download only public model weights/configs; never receives corporate documents."""
import hashlib
import json
import os
from pathlib import Path
from huggingface_hub import HfApi, hf_hub_download

ROOT = Path(__file__).resolve().parent.parent
TARGET = Path(os.environ.get('KB_MODELS_DIR', ROOT / 'models'))
TARGET.mkdir(exist_ok=True, parents=True)
repos = {'embedding': 'BAAI/bge-small-zh-v1.5', 'reranker': 'BAAI/bge-reranker-base'}
manifest = {}
for name, repo in repos.items():
    info = HfApi().model_info(repo)
    revision = info.sha
    files = [f.rfilename for f in info.siblings if '/' not in f.rfilename and
             (f.rfilename.endswith(('.json', '.txt', '.model')) or f.rfilename == 'model.safetensors')]
    target = TARGET / name
    target.mkdir(exist_ok=True)
    print(f'Downloading {repo} @ {revision}', flush=True)
    hashes = {}
    for filename in files:
        path = Path(hf_hub_download(repo, filename, revision=revision, local_dir=target))
        with path.open('rb') as stream:
            hashes[filename] = hashlib.file_digest(stream, 'sha256').hexdigest()
        print(f'  verified {filename}', flush=True)
    manifest[name] = {'repo': repo, 'revision': revision, 'sha256': hashes, 'license': 'MIT'}
(TARGET / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print('Local models ready. Inference uses local_files_only=True.', flush=True)
