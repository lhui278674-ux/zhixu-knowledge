import React,{useCallback,useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ArrowDownToLine,ArrowLeft,ArrowRight,BookOpen,Check,ChevronRight,ClipboardCheck,FileText,History,Home,LayoutDashboard,LogOut,Menu,MessageSquare,PanelLeftClose,Search,Settings2,ShieldCheck,Star,Upload,Users,X} from 'lucide-react';
import {Auth} from './auth';
import {AdminHome,AuditPage,Contributions,GovernancePage,MembersPage,Registrations,ServicePage,UsersPage} from './admin';
import {Chat,QuestionHistory} from './chat';
import {DocumentCenter,PortalHome} from './documents';
import {api,ApiError,Badge,Busy,Library,libraryUrl,Mark,Modal,post,Source,User,Workspace} from './shared';
import './style.css';
import {demoFileUrl,isPublicDemo,resetDemo} from './publicDemo';
const routeLocation=()=>isPublicDemo?(window.location.hash.slice(1)||'/'):(window.location.pathname+window.location.search);

function App(){
  const [narrow,setNarrow]=useState(()=>window.matchMedia('(max-width:680px)').matches);
  useEffect(()=>{const media=window.matchMedia('(max-width:680px)');const changed=()=>setNarrow(media.matches);media.addEventListener('change',changed);return()=>media.removeEventListener('change',changed)},[]);
  const [location,setLocation]=useState(routeLocation);const path=location.split('?')[0];const params=new URLSearchParams(location.split('?')[1]||'');
  const [user,setUser]=useState<User|null>(null);const [setup,setSetup]=useState(false);const [loading,setLoading]=useState(true);const [startupError,setStartupError]=useState('');const [libs,setLibs]=useState<Library[]>([]);const [notice,setNotice]=useState('');const [collapsed,setCollapsed]=useState(false);const [mobileOpen,setMobileOpen]=useState(false);const [search,setSearch]=useState('');const [source,setSource]=useState<Source|null>(null);const sourceTicket=useRef(0);
  const navigate=useCallback((p:string)=>{window.history.pushState({},'',isPublicDemo?'#'+p:p);setLocation(p);setMobileOpen(false)},[]);
  const notify=useCallback((s:string)=>setNotice(s),[]);
  useEffect(()=>{const back=()=>{setLocation(routeLocation());setMobileOpen(false)};window.addEventListener('popstate',back);window.addEventListener('hashchange',back);return()=>{window.removeEventListener('popstate',back);window.removeEventListener('hashchange',back)}},[]);
  async function boot(){setLoading(true);setStartupError('');try{const [s,u]=await Promise.all([api<{needs_setup:boolean}>('/auth/status'),api<User>('/auth/me').catch(e=>{if(e.status===401)return null;throw e})]);setSetup(s.needs_setup);setUser(u)}catch(e){setStartupError((e as Error).message)}finally{setLoading(false)}}
  useEffect(()=>{boot()},[]);
  const refresh=useCallback(async()=>{try{const [u,l]=await Promise.all([api<User>('/auth/me'),api<Library[]>('/libraries')]);setUser(previous=>JSON.stringify(previous)===JSON.stringify(u)?previous:u);setLibs(previous=>JSON.stringify(previous)===JSON.stringify(l)?previous:l)}catch(e){if(e instanceof ApiError&&e.status===401){setUser(null);setLibs([]);setSource(null);sourceTicket.current++;notify('会话已过期，请重新登录。')}throw e}},[notify]);
  useEffect(()=>{if(!user)return;refresh().catch(e=>notify(e.message));const timer=setInterval(()=>refresh().catch(()=>{}),6000);return()=>clearInterval(timer)},[user?.id,refresh]);
  useEffect(()=>{if(!notice)return;const timer=setTimeout(()=>setNotice(''),6500);return()=>clearTimeout(timer)},[notice]);
  useEffect(()=>{if(user){if(path==='/'||path.endsWith('/login')||path.endsWith('/register'))navigate(path.startsWith('/admin')&&user.role==='admin'?'/admin':'/employee');else if(path.startsWith('/admin')&&user.role!=='admin')navigate('/employee')}},[user?.role,path,navigate]);
  const openSource=useCallback(async(id:string,chunk?:string)=>{const ticket=++sourceTicket.current;const result=await api<Source>(`/documents/${id}/source`+(chunk?'?chunk_id='+encodeURIComponent(chunk):''));if(ticket===sourceTicket.current)setSource(result)},[]);
  const closeSource=()=>{sourceTicket.current++;setSource(null)};
  useEffect(()=>{if(!source)return;let live=true;const timer=setInterval(()=>api<Source>(`/documents/${source.document.id}/source`+(source.selected?'?chunk_id='+source.selected:'')).catch(e=>{if(live){setSource(null);notify(e.message)}}),5000);return()=>{live=false;clearInterval(timer)}},[source?.document.id,source?.selected]);
  useEffect(()=>{if(!source?.selected)return;requestAnimationFrame(()=>document.getElementById('chunk-'+source.selected)?.scrollIntoView({block:'center',behavior:'smooth'}))},[source?.selected]);
  async function logout(){try{await post('/auth/logout');setUser(null);setLibs([]);closeSource();navigate(path.startsWith('/admin')?'/admin/login':'/employee/login')}catch(e){notify((e as Error).message)}}
  if(loading)return <div className="loading"><Mark/><Busy/>正在打开知序…</div>;
  if(startupError)return <div className="startup-error"><Mark/><h1>暂时无法连接知序</h1><p>{startupError}</p><button className="primary" onClick={boot}>重新连接</button></div>;
  if(!user)return <Auth key={path+String(setup)} path={path} setup={setup} navigate={navigate} onLogin={(u,portal)=>{setUser(u);setSetup(false);navigate(portal==='admin'?'/admin':'/employee')}}/>;
  if(path.startsWith('/admin')&&user.role!=='admin')return <div className="loading"><Busy/></div>;
  const admin=path.startsWith('/admin');
  const navigation=admin?[
    {path:'/admin',label:'工作台总览',icon:LayoutDashboard},
    {path:'/admin/libraries',label:'文库管理',icon:BookOpen},
    {path:'/admin/registrations',label:'员工注册',icon:Users},
    {path:'/admin/submissions',label:'资料入库审核',icon:ClipboardCheck},
    {path:'/admin/members',label:'文库授权',icon:ShieldCheck},
    {path:'/admin/users',label:'成员账号',icon:Users},
    {path:'/admin/governance',label:'管理员变更',icon:ShieldCheck},
    {path:'/admin/audit',label:'操作记录',icon:History},
    {path:'/admin/service',label:'服务状态',icon:Settings2}
  ]:[
    {path:'/employee',label:'知识门户',icon:Home},
    {path:'/employee/documents',label:'全部文档',icon:BookOpen},
    {path:'/employee/favorites',label:'我的收藏',icon:Star},
    {path:'/employee/recent',label:'最近查看',icon:History},
    {path:'/employee/chat',label:'问问知序',icon:MessageSquare},
    {path:'/employee/history',label:'问答历史',icon:History},
    {path:'/employee/submissions',label:'我的提交',icon:Upload}
  ];
  const currentTitle=navigation.find(n=>n.path===path)?.label||(admin?'管理员工作台':'员工知识门户');
  let content:React.ReactNode;
  switch(path){
    case '/employee':content=<PortalHome/>;break;
    case '/employee/documents':content=<DocumentCenter params={params}/>;break;
    case '/employee/favorites':content=<DocumentCenter params={params} view="favorites"/>;break;
    case '/employee/recent':content=<DocumentCenter params={params} view="recent"/>;break;
    case '/employee/chat':content=<Chat questionId={params.get('question')||''}/>;break;
    case '/employee/history':content=<QuestionHistory/>;break;
    case '/employee/submissions':content=<Contributions/>;break;
    case '/admin':content=<AdminHome/>;break;
    case '/admin/libraries':content=<DocumentCenter params={params} admin/>;break;
    case '/admin/registrations':content=<Registrations/>;break;
    case '/admin/submissions':content=<Contributions admin/>;break;
    case '/admin/members':content=<MembersPage/>;break;
    case '/admin/users':content=<UsersPage/>;break;
    case '/admin/governance':content=<GovernancePage/>;break;
    case '/admin/audit':content=<AuditPage/>;break;
    case '/admin/service':content=<ServicePage/>;break;
    default:content=<div className="page-content"><h1>页面不存在</h1><button className="primary" onClick={()=>navigate(admin?'/admin':'/employee')}>返回首页</button></div>;
  }
  return <Workspace.Provider value={{user,libs,navigate,notify,refresh,openSource}}><div className={'app-shell '+(admin?'admin-shell':'employee-shell')+(collapsed?' collapsed':'')+(mobileOpen?' mobile-open':'')}>
    {mobileOpen&&<button className="sidebar-scrim" aria-label="关闭导航" onClick={()=>setMobileOpen(false)}/>}
    <aside className="sidebar" inert={narrow&&!mobileOpen} aria-hidden={narrow&&!mobileOpen?true:undefined}><div className="sidebar-brand"><button className="brand" onClick={()=>navigate(admin?'/admin':'/employee')}><Mark/><span>知序<small>ZHIXU</small></span></button><button className="icon-button sidebar-toggle" aria-label={collapsed?'展开侧栏':'收起侧栏'} onClick={()=>setCollapsed(v=>!v)}><PanelLeftClose size={17}/></button></div><div className="workspace-label">{admin?'管理员工作台':'员工知识门户'}</div><nav aria-label={admin?'管理员导航':'员工导航'}>{navigation.map(n=><button className={'nav-item '+(path===n.path?'selected':'')} key={n.path} title={n.label} aria-label={n.label} aria-current={path===n.path?'page':undefined} onClick={()=>navigate(n.path)}><n.icon size={18}/><span>{n.label}</span>{path===n.path&&<i/>}</button>)}</nav>
      {!admin&&<div className="sidebar-libraries"><div className="sidebar-section"><span>知识文库</span><button aria-label="查看全部文库" onClick={()=>navigate('/employee/documents')}><ArrowRight size={14}/></button></div>{libs.slice(0,7).map(l=><button className={'sidebar-library '+(params.get('library')===l.id?'selected':'')} title={l.name} key={l.id} onClick={()=>navigate(libraryUrl(l.id))}><BookOpen size={15}/><span>{l.name}</span></button>)}</div>}
      <div className="sidebar-bottom">{user.role==='admin'&&<button className="portal-switch" title={admin?'打开员工门户':'打开管理工作台'} onClick={()=>navigate(admin?'/employee':'/admin')}>{admin?<BookOpen size={17}/>:<LayoutDashboard size={17}/>}<span>{admin?'员工门户':'管理员工作台'}</span><ArrowRight size={14}/></button>}<div className="profile"><span className="avatar">{user.display_name.slice(0,1)}</span><div><strong>{user.display_name}</strong><small>{user.role==='admin'?'管理员':'员工'}</small></div><button className="icon-button" aria-label="退出登录" title="退出登录" onClick={logout}><LogOut size={17}/></button></div><button className="collapsed-toggle icon-button" aria-label="展开侧栏" onClick={()=>setCollapsed(false)}><ArrowRight size={17}/></button></div>
    </aside><main className="main"><header className="topbar"><div className="breadcrumbs"><button className="icon-button mobile-menu" aria-label="打开导航" onClick={()=>setMobileOpen(true)}><Menu size={21}/></button><span>{admin?'管理空间':'知识空间'}</span><ChevronRight size={14}/><strong>{currentTitle}</strong></div>{!admin&&<form className="global-search" onSubmit={e=>{e.preventDefault();navigate('/employee/documents?q='+encodeURIComponent(search))}}><Search size={16}/><input aria-label="统一搜索" placeholder="搜索文档" value={search} onChange={e=>setSearch(e.target.value)} maxLength={200}/><button aria-label="搜索文档"><ArrowRight size={16}/></button></form>}<span className="topbar-profile"><span className="avatar small">{user.display_name[0]}</span><span>{user.display_name}</span></span></header>{isPublicDemo&&<div className="public-demo-bar"><span>虚构演示 · 浏览器证据摘录</span><div><a href="https://github.com/lhui278674-ux/zhixu-knowledge" target="_blank" rel="noreferrer">项目源码</a><button onClick={resetDemo}>重置演示</button></div></div>}<div className={'page-body '+(path==='/employee/chat'?'chat-page':'')} key={path}>{content}</div></main>
    {notice&&<div className="toast" role="status"><Check size={17}/><span>{notice}</span><button aria-label="关闭提示" onClick={()=>setNotice('')}><X size={16}/></button></div>}
    {source&&<Modal title="原文预览" wide onClose={closeSource}><div className="source-header"><span className={'file-icon '+source.document.extension}>{source.document.extension.toUpperCase()}</span><div><h3>{source.document.name}</h3><Badge status={source.document.status}/></div></div><div className="source-toolbar"><span>{source.chunks.length} 处原文位置</span><a className="secondary" href={`${isPublicDemo?demoFileUrl('documents',source.document.id):'/api/documents/'+source.document.id+'/file'}${source.document.extension==='pdf'?'#page='+(source.chunks.find(c=>c.id===source.selected)?.locator.page||1):''}`} target="_blank" rel="noreferrer"><ArrowDownToLine size={16}/>{source.document.extension==='pdf'?'打开 PDF 原始页':'下载原文件'}</a></div><div className="source-layout"><aside className="source-outline"><strong>原文位置</strong>{source.chunks.map(c=><button className={c.id===source.selected?'selected':''} key={c.id} onClick={()=>setSource(s=>s?{...s,selected:c.id}:s)}>{c.locator.label}</button>)}</aside><div className="source-scroll">{source.chunks.map(c=><article id={'chunk-'+c.id} key={c.id} className={'source-chunk '+(c.id===source.selected?'highlighted':'')}><span>{c.locator.label}{c.id===source.selected?' · 当前引用':''}</span><p>{c.text}</p></article>)}{!source.chunks.length&&<div className="empty-state"><FileText size={30}/><h3>暂无可预览的文字</h3><p>{source.document.detail}</p></div>}</div></div></Modal>}
  </div></Workspace.Provider>;
}
createRoot(document.getElementById('root')!).render(<App/>);
