"""Authenticated retrieval evaluation; sends no requests to the answer provider.
Supply a labeled JSON set using evaluation/demo.json as its schema.
"""
import argparse
import getpass
import json
import os
import statistics
import time
from pathlib import Path
import httpx


def evaluate(client, dataset, library_ids=None):
    cases = []
    for case in dataset['cases']:
        started = time.perf_counter()
        response = client.post('/api/qa/search', json={'question':case['question'], 'library_ids':library_ids or []})
        response.raise_for_status()
        results = response.json()['results'][:6]
        latency = time.perf_counter() - started
        expected = case.get('expected', [])
        def matches(result, label):
            return result['name'] == label['name'] and all(result['locator'].get(k) == v for k,v in label.get('locator', {}).items())
        relevance = [any(matches(r,e) for e in expected) for r in results]
        recovered = sum(any(matches(r,e) for r in results) for e in expected)
        rr = next((1/(i+1) for i,v in enumerate(relevance) if v),0)
        report = {'question':case['question'],'latency_seconds':round(latency,3),
                  'recall_at_6':recovered/len(expected) if expected else None,
                  'precision_at_6':sum(relevance)/6 if expected else None, 'reciprocal_rank_at_6':rr if expected else None,
                  'ranking':[{'name':r['name'],'locator':r['locator'],'score':r['score']} for r in results]}
        if 'expect_status' in case:
            q = client.post('/api/qa/questions',json={'question':case['question'],'mode':'evidence','library_ids':library_ids or []})
            q.raise_for_status()
            qid=q.json()['id'];deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                answer=client.get('/api/qa/questions/'+qid).json()
                if answer['status'] not in {'queued','retrieving','generating','waiting_quota'}:break
                time.sleep(.2)
            report['guard_status']=answer['status'];report['guard_passed']=answer['status']==case['expect_status']
        cases.append(report)
    answerable = [c for c in cases if c['recall_at_6'] is not None]
    guards=[c for c in cases if 'guard_passed' in c]
    return {'dataset':dataset['name'],'fictional':dataset.get('fictional',False),'case_count':len(cases),
            'scope':'本次登录账号的授权知识库；真实本地模型；不调用 AIHubMix',
            'metrics':{'mean_recall_at_6':statistics.mean(c['recall_at_6'] for c in answerable) if answerable else None,
                       'mean_precision_at_6':statistics.mean(c['precision_at_6'] for c in answerable) if answerable else None,
                       'mrr_at_6':statistics.mean(c['reciprocal_rank_at_6'] for c in answerable) if answerable else None,
                       'guard_passed':sum(c['guard_passed'] for c in guards),'guard_count':len(guards),
                       'median_latency_seconds':statistics.median(c['latency_seconds'] for c in cases)},
            'limitation':'此数据集的结果不能推定其他资料、用户权限范围或真实企业上的检索准确率。', 'cases':cases}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--username',required=True)
    parser.add_argument('--dataset',default='evaluation/demo.json')
    parser.add_argument('--output',default='artifacts/retrieval-evaluation.json')
    parser.add_argument('--library',action='append',default=[])
    args=parser.parse_args()
    password=os.environ.get('KB_EVAL_PASSWORD') or getpass.getpass('本机账号密码（不会显示）：')
    with httpx.Client(base_url=args.url,headers={'X-KB-Request':'1'},timeout=60) as client:
        login=client.post('/api/auth/login',json={'username':args.username,'password':password})
        login.raise_for_status()
        result=evaluate(client,json.loads(Path(args.dataset).read_text('utf-8')),args.library)
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'dataset':result['dataset'],'metrics':result['metrics'],'output':str(output)},ensure_ascii=False))
