import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';

const payload=JSON.parse(await readFile(new URL('../public/demo-data.json',import.meta.url),'utf8'));
const values=new Map();
globalThis.localStorage={getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
globalThis.fetch=async()=>({ok:true,json:async()=>structuredClone(payload)});
const bundle=await build({entryPoints:[fileURLToPath(new URL('../src/publicDemo.ts',import.meta.url))],bundle:true,write:false,platform:'node',format:'esm',define:{'import.meta.env.VITE_PUBLIC_DEMO':'"true"','import.meta.env.BASE_URL':'"./"'}});
const {demoApi}=await import('data:text/javascript;base64,'+Buffer.from(bundle.outputFiles[0].contents).toString('base64'));
const call=(path,body,method='POST')=>demoApi(path,{method,body:JSON.stringify(body)});

test('public demo: employee browsing, source locators and personal lists',async()=>{
  await assert.rejects(demoApi('/auth/me'),e=>e.status===401);
  await call('/auth/login',{persona:'employee'});
  const libraries=await demoApi('/libraries');
  assert.equal(libraries.length,5);
  assert(!libraries.some(l=>l.id==='demo-private'||l.id==='demo-finance'));
  await assert.rejects(demoApi('/libraries/demo-private/directories'),e=>e.status===404);
  const all=await demoApi('/portal/documents?limit=100');
  assert.equal(all.total,23);
  assert(!all.results.some(d=>d.name.includes('研发')));
  const hr=all.results.find(d=>d.extension==='docx'&&d.name.includes('人事'));
  const source=await demoApi('/documents/'+hr.id+'/source');
  assert(source.chunks.length>20);
  assert(source.chunks.every(c=>c.locator.label));
  await call('/favorites/'+hr.id,{},'PUT');
  assert((await demoApi('/portal/documents?view=favorites')).results.some(d=>d.id===hr.id));
  await call('/favorites/'+hr.id,{},'DELETE');
  assert(!(await demoApi('/portal/documents?view=favorites')).results.some(d=>d.id===hr.id));
  assert((await demoApi('/portal/documents?view=recent')).results.some(d=>d.id===hr.id));
  const bodySearch=await demoApi('/portal/documents?q='+encodeURIComponent('3个工作日')+'&field=content');
  assert(bodySearch.results.some(d=>d.id===hr.id&&d.locator&&d.snippet.includes('3个工作日')));
  const dirs=await demoApi('/libraries/demo-public/directories');
  const folder=dirs.find(f=>f.id===hr.directory_id);
  assert((await demoApi('/portal/documents?directory_id='+folder.id)).results.every(d=>d.directory_id===folder.id));
});

test('public demo: grounded answers, conflict, private scope and history ownership',async()=>{
  const response=await call('/qa/questions',{question:'新员工如何领取办公设备？',mode:'evidence',library_ids:[]});
  const answer=await demoApi('/qa/questions/'+response.id);
  assert.equal(answer.status,'completed');
  assert(answer.citations[0].name.includes('办公设备'));
  for(const c of answer.citations){const source=await demoApi('/documents/'+c.document_id+'/source?chunk_id='+c.chunk_id);assert(source.chunks.some(x=>x.id===c.chunk_id&&x.text===c.text))}
  const r=await call('/qa/questions',{question:'出差住宿上限是多少？',library_ids:[]});
  const conflict=await demoApi('/qa/questions/'+r.id);
  assert.equal(conflict.status,'conflict');assert(conflict.answer.includes('400元')&&conflict.answer.includes('500元'));
  await assert.rejects(call('/qa/questions',{question:'ORBIT-7429 发布日期？',library_ids:['demo-private']}),e=>e.status===404);
  assert((await demoApi('/qa/questions')).length>=2);
  await call('/auth/login',{persona:'initiator'});
  assert.equal((await demoApi('/qa/questions')).length,0);
  await assert.rejects(demoApi('/qa/questions/'+response.id),e=>e.status===404);
});

test('public demo: submission is absent from search until approval',async()=>{
  await call('/auth/login',{persona:'employee'});
  const form=new FormData();form.append('file',new File(['虚构审核场景。唯一标记 REVIEW-DEMO-8723；归档期限为17天。'],'审核场景（虚构）.txt')); 
  const submission=await demoApi('/libraries/demo-projects/submissions',{method:'POST',body:form});
  assert.equal((await demoApi('/portal/documents?q=REVIEW-DEMO-8723')).total,0);
  await assert.rejects(call('/submissions/'+submission.id+'/review',{approve:true}),e=>e.status===403);
  await call('/auth/login',{persona:'initiator'});
  await call('/submissions/'+submission.id+'/review',{approve:true,note:'虚构审核通过'});
  await assert.rejects(call('/submissions/'+submission.id+'/review',{approve:true}),e=>e.status===409);
  await call('/auth/login',{persona:'employee'});
  const search=await demoApi('/portal/documents?q=REVIEW-DEMO-8723');assert.equal(search.total,1);
  const response=await call('/qa/questions',{question:'REVIEW-DEMO-8723 归档期限是什么？',library_ids:['demo-projects']});
  const q=await demoApi('/qa/questions/'+response.id);assert.equal(q.status,'completed');assert(q.answer.includes('17天'));
});

test('public demo: registration grants, two-person role review and permission revocation',async()=>{
  await call('/auth/login',{persona:'initiator'});
  await call('/registrations/pending-employee/review',{approve:true,note:'演示',grants:{'demo-public':'view'}});
  assert((await demoApi('/users')).some(u=>u.id==='pending-employee'&&u.review_status==='approved'));
  const change=await call('/admin-changes',{action:'promote',target_id:'pending-employee',reason:'演示管理员变更'});
  await assert.rejects(call('/admin-changes/'+change.id+'/review',{approve:true}),e=>e.status===403);
  assert.equal((await demoApi('/users')).find(u=>u.id==='pending-employee').role,'employee');
  await call('/auth/login',{persona:'reviewer'});
  await call('/admin-changes/'+change.id+'/review',{approve:true});
  assert.equal((await demoApi('/users')).find(u=>u.id==='pending-employee').role,'admin');
  await call('/libraries/demo-projects/members/employee',{},'DELETE');
  await call('/auth/login',{persona:'employee'});
  assert(!(await demoApi('/libraries')).some(l=>l.id==='demo-projects'));
  await assert.rejects(demoApi('/libraries/demo-projects/directories'),e=>e.status===404);
  assert((await demoApi('/portal/documents?q=REVIEW-DEMO-8723')).total===0);
  const historical=(await demoApi('/qa/questions')).find(q=>q.question.includes('REVIEW-DEMO-8723'));
  const detail=await demoApi('/qa/questions/'+historical.id);assert.equal(detail.status,'source_changed');assert.equal(detail.citations.length,0);
});

test('public demo: fixed enterprise domain policy and all refusal cases',async()=>{
  await call('/auth/login',{persona:'initiator'});
  const cases=JSON.parse(await readFile(new URL('../../evaluation/domain.json',import.meta.url),'utf8')).cases;
  for(const c of cases.filter(c=>c.expect_status!=='allowed')){
    const r=await call('/qa/questions',{question:c.question,mode:'evidence'});
    const q=await demoApi('/qa/questions/'+r.id);
    assert.equal(q.status,c.expect_status,c.question);
    assert.equal(q.citations.length,0);
    assert(q.answer.startsWith(c.expect_status==='out_of_scope'?'我仅回答本企业业务':'请补充本企业的具体事项'));
  }
  const mixed=await call('/qa/questions',{question:'出差住宿上限是多少？顺便讲个笑话'});
  const q=await demoApi('/qa/questions/'+mixed.id);
  assert.equal(q.status,'conflict');assert(q.answer.includes('我仅回答本企业业务'));assert(!q.answer.includes('笑话'));
  const help=await call('/qa/questions',{question:'如何在这个系统上传文档？'});
  assert((await demoApi('/qa/questions/'+help.id)).answer.includes('来源：本系统使用说明'));
});
