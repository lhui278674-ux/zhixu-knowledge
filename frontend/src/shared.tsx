import React, { createContext, useContext, useEffect, useRef } from 'react';
import { Layers, LoaderCircle, X, FileText } from 'lucide-react';
import {demoApi,isPublicDemo} from './publicDemo';
export {isPublicDemo} from './publicDemo';

export type User = { id:string; username:string; display_name:string; role:'admin'|'employee'; active:number; review_status:string; review_note?:string; created_at?:number };
export type Library = { id:string; name:string; description:string; document_count:number; member_count:number; demo:number; permission:'view'|'submit' };
export type Doc = { id:string; library_id:string; library_name?:string; name:string; extension:string; status:string; detail:string; size:number; chunk_count:number; created_at:number; directory_id:string|null; directory_name?:string; favorite:number; viewed_at?:number; snippet?:string; chunk_id?:string; locator?:{label:string} };
export type Directory = {id:string; library_id:string; parent_id:string|null; name:string; document_count:number};
export type Citation = { source_id:string; chunk_id:string; document_id:string; library_name:string; name:string; text:string; quote?:string; locator:{label:string;page?:number}; score:number };
export type Question = { id:string; question:string; answer:string; citations:Citation[]; mode:string; status:string; created_at:number; library_ids:string };
export type System = { embedding:string; detail:string; generation_configured:boolean; quota:{minute_used:number;day_used:number;token_reserved:number}; queue_count?:number };
export type Source = {document:Doc;chunks:{id:string;text:string;locator:{label:string;page?:number}}[];selected:string|null};
export type Submission = {id:string;library_id:string;library_name:string;name:string;extension:string;size:number;status:string;note:string;submitter_name:string;created_at:number;document_id:string|null;document_status:string|null};
export type Governance = {needs_completion:boolean;initiator_id:string|null;reviewer_id:string|null};
export type AdminChange = {id:string;action:string;target_id:string|null;target_name:string|null;requested_by:string;reviewer_id:string;requester_name:string;reviewer_name:string;reason:string;status:string;created_at:number;note:string;payload:string};
export type Audit = {id:number;at:number;actor_name:string|null;actor_username:string|null;action:string;target:string};

export class ApiError extends Error { constructor(message:string,public status:number){super(message)} }
export async function api<T>(path:string, options:RequestInit={}):Promise<T> {
  if(isPublicDemo){try{return await demoApi<T>(path,options)}catch(e){throw new ApiError((e as Error).message,(e as {status?:number}).status||400)}}
  const r=await fetch('/api'+path,{...options,headers:{'X-KB-Request':'1',...(options.body&&!(options.body instanceof FormData)?{'Content-Type':'application/json'}:{}),...options.headers}});
  if(!r.ok){let message='服务暂时不可用，请稍后重试。';try{const j=await r.json();message=typeof j.detail==='string'?j.detail:'请检查输入内容和字段长度。'}catch{}throw new ApiError(message,r.status)}
  return r.json();
}
export const post=<T,>(path:string,body:unknown={})=>api<T>(path,{method:'POST',body:JSON.stringify(body)});
export const patch=<T,>(path:string,body:unknown)=>api<T>(path,{method:'PATCH',body:JSON.stringify(body)});
export const date=(n:number)=>new Date(n*1000).toLocaleDateString('zh-CN');
export const time=(n:number)=>new Date(n*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
export const size=(n:number)=>n>=1024*1024?(n/1024/1024).toFixed(1)+' MB':(n/1024).toFixed(1)+' KB';
export const terminal=['completed','insufficient','clarify','conflict','model_unavailable','rate_limited','source_changed','validation_failed','cancelled','interrupted','retrieval_failed','out_of_scope'];
export const statusLabel:Record<string,string>={active:'已启用',inactive:'已停用',approved:'已通过',rejected:'已驳回',pending:'待审核',queued:'等待处理',processing:'解析中',ready:'可检索',needs_ocr:'需要 OCR',parse_failed:'解析失败',model_unavailable:'服务不可用',retrieving:'检索中',waiting_quota:'限流排队',generating:'正在生成',completed:'已完成',insufficient:'缺少依据',clarify:'需要补充',conflict:'来源冲突',rate_limited:'达到限额',source_changed:'引用已失效',validation_failed:'引用校验未通过',cancelled:'已取消',interrupted:'任务已中断',retrieval_failed:'检索失败',out_of_scope:'超出服务范围'};
export const actionLabels:Record<string,string>={promote:'新增管理员',demote:'转为员工',deactivate:'停用管理员',reactivate:'启用管理员',duties:'调整治理职责'};

export function Mark(){return <span className="brand-mark"><Layers size={22}/></span>}
export function Badge({status}:{status:string}){return <span className={'badge '+(['ready','completed','active','approved'].includes(status)?'good':['pending','queued','processing','retrieving','waiting_quota','generating'].includes(status)?'pending':'warning')}><i/>{statusLabel[status]||status}</span>}
export function Busy(){return <LoaderCircle size={18} className="spin"/>}
export function Empty({title,detail,action}:{title:string;detail?:string;action?:React.ReactNode}){return <div className="empty-state"><span><FileText size={30}/></span><h3>{title}</h3>{detail&&<p>{detail}</p>}{action}</div>}
export function PageHeading({eyebrow,title,description,actions}:{eyebrow?:string;title:string;description?:string;actions?:React.ReactNode}){return <div className="page-heading"><div>{eyebrow&&<span className="eyebrow">{eyebrow}</span>}<h1>{title}</h1>{description&&<p>{description}</p>}</div><div className="heading-actions">{actions}</div></div>}
export function Modal({title,onClose,children,wide=false}:{title:string;onClose:()=>void;children:React.ReactNode;wide?:boolean}){
  const ref=useRef<HTMLElement>(null);const close=useRef(onClose);close.current=onClose;
  useEffect(()=>{const previous=document.activeElement as HTMLElement;const el=ref.current!;
    const focusable=()=>Array.from(el.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')).filter(x=>x.offsetParent!==null);
    (el.querySelector<HTMLElement>('input:not(:disabled),textarea:not(:disabled),select:not(:disabled)')||focusable()[0]||el).focus();
    function key(e:KeyboardEvent){if(e.key==='Escape'){close.current();return}if(e.key==='Tab'){const list=focusable();const first=list[0],last=list[list.length-1];if(!first){e.preventDefault();return}if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}}
    document.addEventListener('keydown',key);return()=>{document.removeEventListener('keydown',key);previous?.focus()};
  },[]);
  return <div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}><section ref={ref} tabIndex={-1} className={'modal '+(wide?'wide':'')} role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2><button className="icon-button" aria-label="关闭弹窗" onClick={onClose}><X size={21}/></button></header>{children}</section></div>
}

type WorkspaceValue={user:User;libs:Library[];navigate:(path:string)=>void;notify:(s:string)=>void;refresh:()=>Promise<void>;openSource:(id:string,chunk?:string)=>Promise<void>};
export const Workspace=createContext<WorkspaceValue>(null!);
export const useWorkspace=()=>useContext(Workspace);
export function libraryUrl(id:string,admin=false){return (admin?'/admin/libraries':'/employee/documents')+'?library='+id}
export const fileAccept='.pdf,.docx,.xlsx,.pptx,.txt,.md';

export function FolderOptions({directories}:{directories:Directory[]}){
  function visit(parent:string|null,depth:number):React.ReactNode{return directories.filter(f=>f.parent_id===parent).map(f=><React.Fragment key={f.id}><option value={f.id}>{'　'.repeat(depth)+f.name}</option>{visit(f.id,depth+1)}</React.Fragment>)}
  return <><option value="">根目录</option>{visit(null,0)}</>;
}

export function Highlight({text,query}:{text:string;query:string}){const i=text.toLocaleLowerCase().indexOf(query.toLocaleLowerCase());if(!query||i<0)return <>{text}</>;return <>{text.slice(0,i)}<mark>{text.slice(i,i+query.length)}</mark>{text.slice(i+query.length)}</>}
