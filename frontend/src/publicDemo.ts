import type {AdminChange, Audit, Citation, Directory, Doc, Library, Question, Submission, User} from './shared';

export const isPublicDemo = import.meta.env.VITE_PUBLIC_DEMO === 'true';
type Chunk = {id:string;text:string;locator:{label:string;page?:number}};
type DemoDoc = Doc & {chunks:Chunk[];file_path:string;file_text?:string};
type DemoSubmission = Submission & {user_id:string;directory_id:string|null;file_text:string};
type State = {
  version:number; libraries:Library[]; directories:Directory[]; documents:DemoDoc[];
  users:User[]; current:string|null; grants:Record<string,Record<string,string>>;
  favorites:Record<string,string[]>; recent:Record<string,Record<string,number>>;
  questions:(Question & {user_id:string})[]; submissions:DemoSubmission[];
  changes:AdminChange[]; audit:Audit[]; initiator:string; reviewer:string;
};
const storageKey = 'zhixu-public-demo-v2';
let state:State|undefined;
let loading:Promise<State>|undefined;
const now=()=>Date.now()/1000;
const id=()=>crypto.randomUUID();
function fail(message:string,status=400):never {throw Object.assign(new Error(message),{status})}
function persist(){try{localStorage.setItem(storageKey,JSON.stringify(state))}catch{/* browsing still works when storage is full */}}
function initial(payload:Pick<State,'version'|'libraries'|'directories'|'documents'>):State{
  const users:User[]=[
    {id:'employee',username:'demo_employee',display_name:'林舟',role:'employee',active:1,review_status:'approved'},
    {id:'initiator',username:'demo_initiator',display_name:'顾宁',role:'admin',active:1,review_status:'approved'},
    {id:'reviewer',username:'demo_reviewer',display_name:'周砚',role:'admin',active:1,review_status:'approved'},
    {id:'pending-employee',username:'demo_chen',display_name:'陈屿',role:'employee',active:0,review_status:'pending',created_at:1790380800},
  ];
  const pendingText='青禾项目培训回访（虚构）\n\n此资料由虚构员工林舟提交，审核前不加入文档搜索和问答。\n\n培训于 2026 年 11 月 12 日完成，覆盖客户管理员和一线坐席。培训包含工单分派、分类调整、报表导出与异常升级四项练习。\n\n回访问卷共收到 18 份，15 位参加者能独立完成全部练习，另 3 位需要补充报表导出示例。林舟负责在 11 月 16 日补发示例并组织答疑。\n\n待确认事项：客户管理员需补交正式值班表，交付负责人唐禾在 11 月 18 日核对告警联系人。所有人员、时间和记录均为虚构。';
  return {...payload, users,current:null,initiator:'initiator',reviewer:'reviewer',
    grants:{employee:{'demo-public':'submit','demo-company':'view','demo-it':'view','demo-delivery':'view','demo-projects':'submit'}},
    favorites:{employee:['doc-HR-2026-09']},recent:{employee:{'doc-HR-2026-09':1790380800,'doc-IT-USER-2026':1790294400}},
    questions:[],changes:[],audit:[{id:1,at:1790294400,actor_name:'顾宁',actor_username:'demo_initiator',action:'install_fictional_demo',target:'corpus_v2'}],
    submissions:[{id:'submission-training',library_id:'demo-projects',library_name:'项目与产品 · 虚构演示',name:'青禾项目培训回访（虚构）.txt',extension:'txt',size:new TextEncoder().encode(pendingText).length,status:'pending',note:'',submitter_name:'林舟',user_id:'employee',created_at:1790380800,directory_id:null,document_id:null,document_status:null,file_text:pendingText}]
  };
}
async function load():Promise<State>{
  if(state)return state;
  if(!loading)loading=(async()=>{
    const response=await fetch(import.meta.env.BASE_URL+'demo-data.json');
    if(!response.ok)fail('演示资料暂时无法加载，请刷新页面。',503);
    const payload=await response.json();
    try{const saved=JSON.parse(localStorage.getItem(storageKey)||'null');if(saved?.version===payload.version&&Array.isArray(saved.documents)&&Array.isArray(saved.users))state=saved}catch{}
    if(!state)state=initial(payload);
    return state!;
  })();
  return loading;
}
export function resetDemo(){localStorage.removeItem(storageKey);window.location.hash='/';window.location.reload()}
export function demoFileUrl(kind:'documents'|'submissions',documentId:string){
  if(!state)return '#';
  if(kind==='documents'){
    const doc=state.documents.find(d=>d.id===documentId);
    if(doc?.file_path)return import.meta.env.BASE_URL+doc.file_path.split('/').map(encodeURIComponent).join('/');
    if(doc?.file_text)return URL.createObjectURL(new Blob([doc.file_text],{type:'text/plain;charset=utf-8'}));
  }else{
    const doc=state.submissions.find(d=>d.id===documentId);
    if(doc)return URL.createObjectURL(new Blob([doc.file_text],{type:'text/plain;charset=utf-8'}));
  }
  return '#';
}
function current(s:State){const user=s.users.find(u=>u.id===s.current);if(!user||!user.active||user.review_status!=='approved')fail('请选择演示身份。',401);return user}
function admin(s:State){const u=current(s);if(u.role!=='admin')fail('请从管理员演示入口进入。',403);return u}
function can(s:State,lid:string){return current(s).role==='admin'||!!s.grants[current(s).id]?.[lid]}
function library(s:State,lid:string){const l=s.libraries.find(l=>l.id===lid);if(!l||!can(s,lid))fail('文库不存在或尚未开通。',404);return l}
function doc(s:State,did:string){const d=s.documents.find(d=>d.id===did);if(!d||!can(s,d.library_id))fail('文档不存在或尚未开通。',404);return d}
function decorated(s:State,d:DemoDoc){const {chunks:_,file_text:__,file_path:___,...record}=d;return {...record,library_name:s.libraries.find(l=>l.id===d.library_id)?.name,directory_name:s.directories.find(f=>f.id===d.directory_id)?.name,favorite:(s.favorites[current(s).id]||[]).includes(d.id)?1:0,viewed_at:s.recent[current(s).id]?.[d.id]}}
function log(s:State,action:string,target:string){const u=current(s);s.audit.unshift({id:(s.audit[0]?.id||0)+1,at:now(),actor_name:u.display_name,actor_username:u.username,action,target});persist()}
function validateFolder(s:State,lid:string,fid:string|null){if(fid&&!s.directories.some(f=>f.id===fid&&f.library_id===lid))fail('目录不存在。')}
function descendants(s:State,fid:string){const ids=new Set([fid]);let previous=0;while(previous!==ids.size){previous=ids.size;s.directories.forEach(f=>{if(f.parent_id&&ids.has(f.parent_id))ids.add(f.id)})}return ids}
function evidence(s:State,question:string,scope:string[]):{citations:Citation[];status:string;answer:string}{
  if(question.trim().length<5)return {status:'clarify',citations:[],answer:'请补充具体事项，例如项目名称、费用类型或操作场景。'};
  const docs=s.documents.filter(d=>d.status==='ready'&&can(s,d.library_id)&&(!scope.length||scope.includes(d.library_id)));
  const common=new Set(['什么','怎么','如何','需要','应该','多少','公司','员工','可以','是否','哪些','多久','申请','项目','资料']);
  const tokens=[...new Set([...question.matchAll(/[\p{Script=Han}]{2}|[a-zA-Z0-9][a-zA-Z0-9_-]{2,}/gu)].map(x=>x[0].toLowerCase()).filter(x=>!common.has(x)))];
  // Overlapping Chinese bigrams improve short, natural language queries.
  for(const run of question.match(/[\p{Script=Han}]+/gu)||[])for(let i=0;i<run.length-1;i++){const t=run.slice(i,i+2);if(!common.has(t)&&!tokens.includes(t))tokens.push(t)}
  const hints:Record<string,string>={'领取办公设备':'办公设备申请','请假':'提前一天','交付节点':'2026年10月15日','报销':'电子发票','访问权限':'直属主管','餐费':'餐费上限','ORBIT':'ORBIT-7429'};
  const hint=Object.entries(hints).find(([k])=>question.includes(k))?.[1];
  const pool=docs.flatMap(d=>d.chunks.map(c=>({d,c})));
  const frequency=new Map(tokens.map(t=>[t,pool.filter(x=>x.c.text.toLowerCase().includes(t)).length]));
  let scored=pool.map(x=>({...x,score:tokens.reduce((total,t)=>total+(x.c.text.toLowerCase().includes(t)?Math.log(1+pool.length/(1+(frequency.get(t)||0))):0),0)+(hint&&x.c.text.includes(hint)?20:0)})).filter(x=>x.score>=5).sort((a,b)=>b.score-a.score);
  const conflict=question.includes('住宿')&&(question.includes('上限')||question.includes('标准'));
  if(conflict){const one=pool.find(x=>x.c.text.includes('400元')&&x.c.text.includes('住宿'));const two=pool.find(x=>x.c.text.includes('500')&&x.d.extension==='xlsx'&&x.c.text.includes('住宿'));if(one&&two)scored=[{...one,score:30},{...two,score:30}]}
  const unique:typeof scored=[];for(const x of scored){if(unique.length===4)break;if(!unique.some(y=>y.d.id===x.d.id&&y.c.locator.label===x.c.locator.label))unique.push(x)}
  const citations=unique.map((x,i)=>({source_id:'S'+(i+1),chunk_id:x.c.id,document_id:x.d.id,library_name:library(s,x.d.library_id).name,name:x.d.name,text:x.c.text,quote:x.c.text,locator:x.c.locator,score:x.score}));
  const explicit=question.match(/(?:19|20)\d{2}(?=年)|\b[A-Z][A-Z0-9]*-\d[A-Z0-9-]*\b/g)||[];
  if(explicit.some(term=>!citations.some(c=>c.text.includes(term))))return {status:'insufficient',citations:[],answer:'没有找到包含所提年份或编号的文档依据，请核对事项后再提问。'};
  if(!citations.length)return {status:'insufficient',citations:[],answer:'没有找到足够的文档依据。请补充关键词，或改用文档搜索。'};
  return {status:conflict&&citations.length===2?'conflict':'completed',citations,answer:(conflict&&citations.length===2?'两份来源的住宿标准不一致：400元与500元。请核对制度版本后使用。\n\n':'证据摘录：\n\n')+citations.map(c=>`[${c.source_id}] ${c.text}`).join('\n\n')};
}

export async function demoApi<T>(path:string,options:RequestInit={}):Promise<T>{
  const s=await load();const url=new URL(path,'https://demo.invalid');const p=url.pathname;const query=url.searchParams;const method=options.method||'GET';
  const body=typeof options.body==='string'?JSON.parse(options.body):{};
  const result=await (async():Promise<unknown>=>{
    if(p==='/auth/status')return {needs_setup:false};
    if(p==='/auth/login'){
      const u=s.users.find(u=>u.id===body.persona);if(!u||!u.active||u.review_status!=='approved')fail('演示身份不可用，请重置演示。',403);
      s.current=u.id;persist();return u;
    }
    if(p==='/auth/logout'){s.current=null;persist();return {ok:true}}
    const u=current(s);
    if(p==='/auth/me')return u;
    if(p==='/libraries'&&method==='GET')return s.libraries.filter(l=>can(s,l.id)).map(l=>({...l,permission:u.role==='admin'?'submit':s.grants[u.id]?.[l.id],document_count:s.documents.filter(d=>d.library_id===l.id).length,member_count:Object.values(s.grants).filter(g=>g[l.id]).length}));
    if(p==='/portal/documents'){
      const lid=query.get('library_id');if(lid)library(s,lid);const q=(query.get('q')||'').toLowerCase();const field=query.get('field')||'all';const fid=query.get('directory_id');const folders=fid?descendants(s,fid):null;
      let records=s.documents.filter(d=>can(s,d.library_id)&&(!lid||d.library_id===lid)&&(!query.get('extension')||d.extension===query.get('extension'))&&(!folders||!!d.directory_id&&folders.has(d.directory_id))).map(d=>{
        const title=field!=='content'&&d.name.toLowerCase().includes(q);const chunk=field!=='title'?d.chunks.find(c=>c.text.toLowerCase().includes(q)):undefined;
        if(q&&!title&&!chunk)return null;
        return {...decorated(s,d),...(q&&chunk?{snippet:chunk.text.slice(Math.max(0,chunk.text.toLowerCase().indexOf(q)-55),Math.max(0,chunk.text.toLowerCase().indexOf(q)-55)+200),chunk_id:chunk.id,locator:chunk.locator}:{})};
      }).filter((d):d is NonNullable<typeof d>=>!!d);
      if(query.get('view')==='favorites')records=records.filter(d=>d.favorite);
      if(query.get('view')==='recent')records=records.filter(d=>d.viewed_at).sort((a,b)=>(b.viewed_at||0)-(a.viewed_at||0));
      const start=Number(query.get('offset')||0),limit=Number(query.get('limit')||30);return {total:records.length,results:records.slice(start,start+limit)};
    }
    let match=p.match(/^\/libraries\/([^/]+)\/directories$/);
    if(match){const l=library(s,match[1]);if(method==='GET')return s.directories.filter(f=>f.library_id===l.id).map(f=>({...f,document_count:s.documents.filter(d=>d.directory_id===f.id).length}));admin(s);validateFolder(s,l.id,body.parent_id);const f={id:id(),library_id:l.id,parent_id:body.parent_id||null,name:String(body.name).trim(),document_count:0};if(!f.name)fail('请输入目录名称。');s.directories.push(f);log(s,'create_directory',f.id);return f}
    match=p.match(/^\/directories\/([^/]+)$/);
    if(match){admin(s);const f=s.directories.find(f=>f.id===match![1]);if(!f)fail('目录不存在。',404);if(method==='DELETE'){const ids=descendants(s,f.id);s.directories=s.directories.filter(f=>!ids.has(f.id));s.documents.forEach(d=>{if(d.directory_id&&ids.has(d.directory_id))d.directory_id=null});s.submissions.forEach(d=>{if(d.directory_id&&ids.has(d.directory_id))d.directory_id=null});log(s,'delete_directory',f.id)}else{validateFolder(s,f.library_id,body.parent_id);if(body.parent_id&&descendants(s,f.id).has(body.parent_id))fail('上级目录不能是自身或子目录。');Object.assign(f,{name:body.name,parent_id:body.parent_id||null});log(s,'edit_directory',f.id)}return {ok:true}}
    match=p.match(/^\/documents\/([^/]+)\/source$/);
    if(match){const d=doc(s,match[1]);s.recent[u.id]||={};s.recent[u.id][d.id]=now();persist();const selected=query.get('chunk_id');if(selected&&!d.chunks.some(c=>c.id===selected))fail('原文位置不存在。',404);return {document:decorated(s,d),chunks:d.chunks,selected}}
    match=p.match(/^\/favorites\/([^/]+)$/);
    if(match){const d=doc(s,match[1]);s.favorites[u.id]||=[];s.favorites[u.id]=s.favorites[u.id].filter(x=>x!==d.id);if(method==='PUT')s.favorites[u.id].push(d.id);persist();return {ok:true}}
    match=p.match(/^\/documents\/([^/]+)(\/reindex)?$/);
    if(match){admin(s);const d=doc(s,match[1]);if(match[2]){log(s,'reindex',d.id);return {ok:true}}if(method==='DELETE'){s.documents=s.documents.filter(x=>x.id!==d.id);log(s,'delete_document',d.id)}else{validateFolder(s,d.library_id,body.directory_id);d.directory_id=body.directory_id;log(s,'move_document',d.id)}return {ok:true}}
    match=p.match(/^\/libraries\/([^/]+)\/(documents|submissions)$/);
    if(match&&method==='POST'){
      const l=library(s,match[1]);const direct=match[2]==='documents';if(direct)admin(s);else if(u.role!=='admin'&&s.grants[u.id]?.[l.id]!=='submit')fail('尚未开通资料提交。',403);
      const form=options.body as FormData;const file=form.get('file') as File;const fid=String(form.get('directory_id')||'')||null;validateFolder(s,l.id,fid);
      if(!file||!['txt','md'].includes(file.name.split('.').pop()?.toLowerCase()||''))fail('在线演示支持提交 TXT、MD；六种格式导入可在本地完整版本体验。');if(file.size>1024*1024)fail('在线演示文件上限为 1 MB。');const text=await file.text();if(!text.trim())fail('文件内容为空。');
      const record:DemoSubmission={id:id(),library_id:l.id,library_name:l.name,name:file.name,extension:file.name.split('.').pop()!.toLowerCase(),size:file.size,status:'pending',note:'',submitter_name:u.display_name,user_id:u.id,created_at:now(),directory_id:fid,document_id:null,document_status:null,file_text:text};
      if(direct){const d=makeDocument(record);s.documents.push(d);log(s,'upload',d.id);return {id:d.id,duplicate:false}}
      s.submissions.unshift(record);log(s,'submission_requested',record.id);return {id:record.id,duplicate:false};
    }
    if(p==='/submissions')return s.submissions.filter(d=>can(s,d.library_id)&&(u.role==='admin'||d.user_id===u.id));
    match=p.match(/^\/submissions\/([^/]+)\/review$/);
    if(match){admin(s);const sub=s.submissions.find(x=>x.id===match![1]);if(!sub)fail('资料不存在。',404);if(sub.status!=='pending')fail('资料已审核。',409);sub.status=body.approve?'approved':'rejected';sub.note=body.note||'';if(body.approve){const d=makeDocument(sub);s.documents.push(d);sub.document_id=d.id;sub.document_status='ready'}log(s,body.approve?'submission_approved':'submission_rejected',sub.id);return {ok:true}}
    if(p==='/users'&&method==='GET'){admin(s);return s.users}
    if(p==='/users'&&method==='POST'){admin(s);if(s.users.some(x=>x.username===body.username))fail('账号已存在。');const record:User={id:id(),username:body.username,display_name:body.display_name,role:'employee',active:1,review_status:'approved',created_at:now()};s.users.push(record);log(s,'create_user',record.id);return record}
    match=p.match(/^\/users\/([^/]+)$/);
    if(match){admin(s);const target=s.users.find(x=>x.id===match![1]);if(!target)fail('账号不存在。',404);if(target.role==='admin')fail('管理员状态请通过管理员变更办理。');target.active=body.active?1:0;log(s,'user_state',target.id);return {ok:true}}
    if(p==='/registrations'){admin(s);return s.users.filter(u=>u.id==='pending-employee'||u.review_status!=='approved')}
    match=p.match(/^\/registrations\/([^/]+)\/review$/);
    if(match){admin(s);const target=s.users.find(x=>x.id===match![1]);if(!target||target.review_status!=='pending')fail('申请不存在或已处理。',409);target.review_status=body.approve?'approved':'rejected';target.active=body.approve?1:0;target.review_note=body.note||'';if(body.approve){for(const lid of Object.keys(body.grants||{}))library(s,lid);s.grants[target.id]=body.grants||{}}log(s,body.approve?'registration_approved':'registration_rejected',target.id);return {ok:true}}
    match=p.match(/^\/libraries\/([^/]+)\/members(?:\/([^/]+))?$/);
    if(match){admin(s);library(s,match[1]);if(!match[2])return s.users.filter(x=>s.grants[x.id]?.[match![1]]).map(x=>({...x,permission:s.grants[x.id][match![1]]}));const target=s.users.find(x=>x.id===match![2]);if(!target||target.role!=='employee'||target.review_status!=='approved')fail('员工账号不可用。');s.grants[target.id]||={};if(method==='DELETE')delete s.grants[target.id][match[1]];else{if(!['view','submit'].includes(body.permission))fail('权限类型无效。');s.grants[target.id][match[1]]=body.permission}log(s,method==='DELETE'?'revoke':'grant',target.id);return {ok:true}}
    if(p==='/libraries'&&method==='POST'){admin(s);const l={id:id(),name:body.name,description:body.description,demo:1,permission:'submit',document_count:0,member_count:0} as Library;s.libraries.push(l);log(s,'create_library',l.id);return l}
    match=p.match(/^\/libraries\/([^/]+)$/);
    if(match){admin(s);const l=library(s,match[1]);if(method==='DELETE'){s.libraries=s.libraries.filter(x=>x.id!==l.id);s.documents=s.documents.filter(d=>d.library_id!==l.id);s.directories=s.directories.filter(f=>f.library_id!==l.id);s.submissions=s.submissions.filter(x=>x.library_id!==l.id);Object.values(s.grants).forEach(g=>delete g[l.id]);log(s,'delete_library',l.id)}else{Object.assign(l,{name:body.name,description:body.description});log(s,'edit_library',l.id)}return {ok:true}}
    if(p==='/governance'){admin(s);return {needs_completion:false,initiator_id:s.initiator,reviewer_id:s.reviewer}}
    if(p==='/admin-changes'&&method==='GET'){admin(s);return s.changes}
    if(p==='/admin-changes'&&method==='POST'){
      admin(s);if(u.id!==s.initiator)fail('当前演示身份不是变更申请人。',403);if(!String(body.reason||'').trim())fail('请填写变更原因。');
      const target=s.users.find(x=>x.id===body.target_id);if(body.action!=='duties'&&!target)fail('请选择目标账号。');
      if(body.action==='duties'){if(body.initiator_id===body.reviewer_id)fail('请选择两位不同的管理员。');for(const uid of [body.initiator_id,body.reviewer_id])if(!s.users.some(x=>x.id===uid&&x.role==='admin'&&x.active))fail('治理账号不可用。')}
      if(['demote','deactivate'].includes(body.action)&&[s.initiator,s.reviewer].includes(body.target_id))fail('请先调整治理职责。');
      const change:AdminChange={id:id(),action:body.action,target_id:target?.id||null,target_name:target?.display_name||null,requested_by:u.id,reviewer_id:s.reviewer,requester_name:u.display_name,reviewer_name:s.users.find(x=>x.id===s.reviewer)!.display_name,reason:body.reason,status:'pending',created_at:now(),note:'',payload:JSON.stringify({initiator_id:body.initiator_id,reviewer_id:body.reviewer_id})};s.changes.unshift(change);log(s,'admin_change_requested',change.id);return change;
    }
    match=p.match(/^\/admin-changes\/([^/]+)\/review$/);
    if(match){admin(s);const change=s.changes.find(x=>x.id===match![1]);if(!change||change.status!=='pending')fail('变更不存在或已处理。',409);if(change.reviewer_id!==u.id||change.requested_by===u.id)fail('请切换到指定的复核演示身份。',403);if(body.approve){const target=s.users.find(x=>x.id===change.target_id);if(change.action==='duties'){const payload=JSON.parse(change.payload);s.initiator=payload.initiator_id;s.reviewer=payload.reviewer_id}else if(target){if(['demote','deactivate'].includes(change.action)&&[s.initiator,s.reviewer].includes(target.id))fail('请先调整治理职责。');if(change.action==='promote')target.role='admin';if(change.action==='demote')target.role='employee';if(change.action==='deactivate')target.active=0;if(change.action==='reactivate')target.active=1}}change.status=body.approve?'approved':'rejected';change.note=body.note||'';log(s,body.approve?'admin_change_approved':'admin_change_rejected',change.id);return {ok:true}}
    if(p==='/audit'){admin(s);return s.audit}
    if(p==='/system'){admin(s);return {embedding:'ready',detail:'浏览器证据检索',generation_configured:false,quota:{minute_used:0,day_used:0,token_reserved:0},queue_count:0}}
    if(p==='/demo'){admin(s);return {message:'已加载 7 个文库、32 份虚构资料。可通过顶部重置演示恢复原始资料。'}}
    if(p==='/qa/questions'&&method==='GET')return s.questions.filter(q=>q.user_id===u.id).map(q=>({...q,citations:q.citations.filter(c=>s.documents.some(d=>d.id===c.document_id&&can(s,d.library_id)))}));
    if(p==='/qa/questions'&&method==='POST'){const question=String(body.question||'').trim();if(!question||question.length>1000)fail('请输入 1–1000 字的问题。');const scope=body.library_ids||[];scope.forEach((lid:string)=>library(s,lid));const answer=evidence(s,question,scope);const q={id:id(),user_id:u.id,question,...answer,mode:'evidence',created_at:now(),library_ids:JSON.stringify(scope)};s.questions.unshift(q);persist();return {id:q.id}}
    match=p.match(/^\/qa\/questions\/([^/]+)$/);
    if(match){const q=s.questions.find(q=>q.id===match![1]&&q.user_id===u.id);if(!q)fail('问答记录不存在。',404);if(q.citations.some(c=>!s.documents.some(d=>d.id===c.document_id&&can(s,d.library_id))))return {...q,status:'source_changed',answer:'引用文档已移除或文库授权已变更，请重新提问。',citations:[]};return q}
    fail('此操作请在本地完整版本体验。',400);
  })();
  return structuredClone(result) as T;
}
function makeDocument(sub:DemoSubmission):DemoDoc{
  const did=id();const chunks:Chunk[]=[];sub.file_text.split(/\n\s*\n/).forEach((text,i)=>{for(let start=0;start<text.length;start+=420)if(text.slice(start,start+500).trim())chunks.push({id:`${did}-chunk-${chunks.length+1}`,text:text.slice(start,start+500).trim(),locator:{label:`第 ${i+1} 段`}})});
  return {id:did,library_id:sub.library_id,name:sub.name,extension:sub.extension,size:sub.size,status:'ready',detail:'',chunk_count:chunks.length,created_at:now(),directory_id:sub.directory_id,favorite:0,chunks,file_path:'',file_text:sub.file_text};
}
