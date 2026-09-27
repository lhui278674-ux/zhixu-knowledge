import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('KB_DATA_DIR', ROOT / 'data')).resolve()
MODELS = Path(os.environ.get('KB_MODELS_DIR', ROOT / 'models')).resolve()
DATA.mkdir(parents=True, exist_ok=True)
(DATA / 'uploads').mkdir(exist_ok=True)
(DATA / 'submissions').mkdir(exist_ok=True)
DB = DATA / 'knowledge.db'
MODEL = 'coding-kimi-k3-free'
BASE_URL = 'https://aihubmix.com/v1'
MAX_FILE = 25 * 1024 * 1024
MAX_CHUNKS = 4000
EMBED_ID = 'BAAI/bge-small-zh-v1.5'
RERANK_ID = 'BAAI/bge-reranker-base'
