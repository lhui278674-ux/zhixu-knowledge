import json
import os
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from . import db
from .auth import allowed_ids, document_access
from .config import DATA, MODELS, EMBED_ID
from .parsers import parse, NeedsOCR, ParseError

log = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='local-index')


class ModelUnavailable(Exception):
    pass


class LocalModels:
    def __init__(self):
        self.lock = threading.RLock()
        self.encoder = self.reranker = None
        self.state = 'not_loaded'
        self.detail = '等待加载本地模型'

    def load(self):
        with self.lock:
            if self.encoder is not None:
                return
            self.state = 'loading'
            self.detail = '本地中文向量与重排模型正在加载'
            try:
                os.environ.setdefault('USE_TF', '0')
                import torch
                from transformers import AutoTokenizer, AutoModel, AutoModelForSequenceClassification
                torch.set_num_threads(max(1, min(4, torch.get_num_threads())))
                manifest = json.loads((MODELS / 'manifest.json').read_text('utf-8'))
                self.signature = EMBED_ID + '@' + manifest['embedding']['revision']
                self.et = AutoTokenizer.from_pretrained(MODELS / 'embedding', local_files_only=True)
                encoder = AutoModel.from_pretrained(MODELS / 'embedding', local_files_only=True, use_safetensors=True).eval()
                self.rt = AutoTokenizer.from_pretrained(MODELS / 'reranker', local_files_only=True)
                reranker = AutoModelForSequenceClassification.from_pretrained(
                    MODELS / 'reranker', local_files_only=True, use_safetensors=True).eval()
                self.encoder, self.reranker = encoder, reranker
                self.state, self.detail = 'ready', '中文向量与重排模型均在本机运行'
            except Exception:
                self.state, self.detail = 'unavailable', '本地模型未安装或加载失败，请运行 scripts/prepare-models.ps1。'
                raise ModelUnavailable(self.detail) from None

    def embed(self, texts, query=False):
        with self.lock:
            self.load()
            import torch
            result = []
            for i in range(0, len(texts), 16):
                batch = texts[i:i+16]
                if query:
                    batch = ['为这个句子生成表示以用于检索相关文章：' + t for t in batch]
                inputs = self.et(batch, padding=True, truncation=True, max_length=512, return_tensors='pt')
                with torch.inference_mode():
                    vectors = self.encoder(**inputs).last_hidden_state[:, 0]
                    result.append(torch.nn.functional.normalize(vectors, p=2, dim=1).numpy())
            return np.concatenate(result).astype('float32')

    def rerank(self, query, texts):
        with self.lock:
            self.load()
            import torch
            result = []
            for i in range(0, len(texts), 8):
                inputs = self.rt([(query, t) for t in texts[i:i+8]], padding=True, truncation=True,
                                 max_length=512, return_tensors='pt')
                with torch.inference_mode():
                    result.extend(torch.sigmoid(self.reranker(**inputs).logits.view(-1)).tolist())
            return result


models = LocalModels()


def process_document(doc_id, version):
    d = db.one('SELECT * FROM documents WHERE id=? AND version=?', (doc_id, version))
    if not d:
        return
    db.execute("UPDATE documents SET status='processing',detail='正在解析与建立本地索引' WHERE id=? AND version=?", (doc_id, version))
    try:
        chunks, warning = parse(DATA / 'uploads' / doc_id, d['extension'])
        vectors = models.embed([t for t, _ in chunks])
        with db.connect() as c:
            if not c.execute('SELECT id FROM documents WHERE id=? AND version=?', (doc_id, version)).fetchone():
                return
            c.execute('DELETE FROM chunks WHERE document_id=?', (doc_id,))
            c.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,?)',
                          [(uuid.uuid4().hex, doc_id, t, json.dumps(loc, ensure_ascii=False), i,
                            vectors[i].tobytes(), models.signature) for i, (t, loc) in enumerate(chunks)])
            c.execute("UPDATE documents SET status='ready',chunk_count=?,detail=? WHERE id=? AND version=?",
                      (len(chunks), warning or '解析与索引完成', doc_id, version))
    except NeedsOCR as e:
        state, detail = 'needs_ocr', str(e)
    except ParseError as e:
        state, detail = 'parse_failed', str(e)
    except ModelUnavailable as e:
        state, detail = 'model_unavailable', str(e)
    except Exception:
        state, detail = 'parse_failed', '文件解析失败，请检查文件是否损坏、格式是否与扩展名一致。'
    else:
        return
    db.execute('UPDATE documents SET status=?,detail=?,chunk_count=0 WHERE id=? AND version=?',
               (state, detail, doc_id, version))


def submit_document(doc_id, version):
    return executor.submit(process_document, doc_id, version)


def search(user, question, library_ids=None, limit=6):
    # SQL membership filtering runs BEFORE lexical/vector retrieval and reranking.
    permitted = allowed_ids(user)
    selected = permitted if library_ids is None else [x for x in library_ids if x in permitted]
    if not selected:
        return []
    placeholders = ','.join('?' for _ in selected)
    chunks = db.rows(f'''SELECT c.*,d.library_id,d.name,l.name library_name,d.version FROM chunks c
                        JOIN documents d ON d.id=c.document_id JOIN libraries l ON l.id=d.library_id
                        WHERE d.status='ready' AND d.library_id IN ({placeholders})''', selected)
    if not chunks:
        return []
    query_vector = models.embed([question], query=True)[0]
    chunks = [c for c in chunks if c['model'] == models.signature]
    if not chunks:
        raise ModelUnavailable('索引模型版本已变更，请重新索引文档。')
    import jieba
    from rank_bm25 import BM25Okapi
    jieba.setLogLevel(logging.ERROR)
    tokens = lambda t: [x.lower() for x in jieba.lcut(t) if x.strip() and len(x.strip()) > 1]
    corpus = [tokens(c['text']) for c in chunks]
    lexical = BM25Okapi(corpus).get_scores(tokens(question))
    matrix = np.stack([np.frombuffer(c['vector'], dtype='float32') for c in chunks])
    cosine = matrix @ query_vector
    fused = {}
    for scores in (cosine, lexical):
        for rank, i in enumerate(np.argsort(scores)[::-1][:20], 1):
            if scores is lexical and scores[i] <= 0:
                continue
            fused[int(i)] = fused.get(int(i), 0) + 1 / (60 + rank)
    candidates = sorted(fused, key=fused.get, reverse=True)[:16]
    scores = models.rerank(question, [chunks[i]['text'] for i in candidates])
    result = []
    for i, score in sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)[:limit]:
        c = chunks[i]
        result.append({'chunk_id': c['id'], 'document_id': c['document_id'], 'library_id': c['library_id'],
                       'library_name': c['library_name'], 'name': c['name'], 'text': c['text'],
                       'locator': json.loads(c['locator']), 'score': round(score, 5),
                       'cosine': round(float(cosine[i]), 5), 'version': c['version']})
    return result


def fresh_citations(user, citations):
    fresh = []
    for c in citations:
        try:
            d = document_access(user, c['document_id'])
            chunk = db.one('SELECT id FROM chunks WHERE id=?', (c['chunk_id'],))
            if d['status'] == 'ready' and d['version'] == c['version'] and chunk:
                fresh.append(c)
        except Exception:
            continue
    return fresh
