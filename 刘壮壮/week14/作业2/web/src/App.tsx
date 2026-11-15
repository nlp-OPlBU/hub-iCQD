import { useEffect, useRef, useState } from 'react'
import { Activity, BookOpen, CheckCircle2, ChevronDown, FileText, MessageSquare, Plus, RefreshCw, Upload, Trash2, RotateCcw, Paperclip, Send, Sparkles, LoaderCircle, AlertCircle, ThumbsUp, ThumbsDown, Zap } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import PdfPreview from './PdfPreview'
import type { Source, SourceLocation } from './api'
import { api, API, type Conversation, type Document, type Message, streamQuestion } from './api'

const SERVICE_NAMES: Record<string, string> = {
  sqlite: '数据库',
  elasticsearch: '检索',
  mineru: '解析',
  llm: '模型',
  kafka: '队列',
}
const SERVICE_ORDER = ['sqlite', 'elasticsearch', 'mineru', 'llm', 'kafka']

function formatCompact(value: number) {
  if (value >= 10000) return `${(value / 1000).toFixed(1)}k`
  return value.toLocaleString('zh-CN')
}

function formatBytes(bytes: number) {
  if (!bytes || bytes <= 0) return '0 B'
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function linkifyCitations(markdown: string, maxSource: number) {
  if (!maxSource || !markdown) return markdown
  return markdown
    .split(/(```[\s\S]*?```|`[^`\n]+`)/g)
    .map((part, idx) =>
      idx % 2 === 1
        ? part
        : part.replace(/(?<![!\]])\[(\d+)\](?![(\[])/g, (full, digits) => {
            const n = Number(digits)
            return n >= 1 && n <= maxSource ? `[${n}](#cite-${n})` : full
          })
    )
    .join('')
}

export default function App(){
  const [docs,setDocs]=useState<Document[]>([]),[chats,setChats]=useState<Conversation[]>([]),[active,setActive]=useState(''),[messages,setMessages]=useState<Message[]>([])
  const [text,setText]=useState(''),[images,setImages]=useState<File[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState(''),[uploading,setUploading]=useState(false)
  const [scope,setScope]=useState<string[]>([]),[prefs,setPrefs]=useState({style:'concise',depth:'standard',language:'zh-CN',instruction:''})
  const [overview,setOverview]=useState<any>(null),[overviewOpen,setOverviewOpen]=useState(false),[overviewLoading,setOverviewLoading]=useState(false),[overviewUpdatedAt,setOverviewUpdatedAt]=useState('')
  const [prefsSaved,setPrefsSaved]=useState(false),[sourcePreview,setSourcePreview]=useState<{uid:string,page:number|null,name:string,locations:SourceLocation[]}|null>(null)
  const pdfRef=useRef<HTMLInputElement>(null),imageRef=useRef<HTMLInputElement>(null),bottom=useRef<HTMLDivElement>(null),overviewRef=useRef<HTMLDivElement>(null)
  async function loadDocs(){try{setDocs((await api.documents()).items)}catch(e){setError((e as Error).message)}}
  async function loadChats(){try{const all=(await api.conversations()).items;setChats(all);if(!active&&all[0])setActive(all[0].conversation_uid)}catch(e){setError((e as Error).message)}}
  async function loadMessages(id:string){try{setMessages((await api.messages(id)).items)}catch(e){setError((e as Error).message)}}
  async function refreshOverview(silent=false){
    if(!silent)setOverviewLoading(true)
    try{
      const [statsRes,healthRes,knowledgeRes]=await Promise.allSettled([
        api.stats(),
        api.health(),
        typeof (api as any).knowledgeStats==='function'?(api as any).knowledgeStats():Promise.resolve(null),
      ])
      setOverview((prev:any)=>({
        stats:statsRes.status==='fulfilled'?statsRes.value:(prev?.stats||{questions:0,answers:0,tokens:0}),
        health:healthRes.status==='fulfilled'?healthRes.value:(prev?.health||{services:{sqlite:'ok'}}),
        knowledge:knowledgeRes.status==='fulfilled'?knowledgeRes.value:(prev?.knowledge||null),
      }))
      if(statsRes.status==='fulfilled'||healthRes.status==='fulfilled'){
        setOverviewUpdatedAt(new Date().toLocaleTimeString('zh-CN',{hour12:false}))
      }
    }finally{
      if(!silent)setOverviewLoading(false)
    }
  }
  useEffect(()=>{void loadDocs();void loadChats();void api.settings().then(setPrefs).catch(()=>{});void refreshOverview(true)},[])
  useEffect(()=>{if(!uploading&&!docs.some(d=>d.status==='pending'||d.status==='processing'))return;const t=window.setInterval(()=>{if(document.visibilityState!=='hidden'){void loadDocs();void refreshOverview(true)}},5000);return()=>clearInterval(t)},[uploading,docs])
  useEffect(()=>{
    if(!overviewOpen)return
    const onPointerDown=(e:MouseEvent)=>{if(overviewRef.current&&!overviewRef.current.contains(e.target as Node))setOverviewOpen(false)}
    const onKeyDown=(e:KeyboardEvent)=>{if(e.key==='Escape')setOverviewOpen(false)}
    document.addEventListener('mousedown',onPointerDown)
    document.addEventListener('keydown',onKeyDown)
    return()=>{document.removeEventListener('mousedown',onPointerDown);document.removeEventListener('keydown',onKeyDown)}
  },[overviewOpen])
  const openSource=(s?:Source)=>{if(s?.document_uid)setSourcePreview({uid:s.document_uid,page:s.page_number,name:s.document_name,locations:s.locations||[]})}
  async function exportChat(){if(!active)return;try{const blob=await api.exportConversation(active);const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='conversation.md';a.click();URL.revokeObjectURL(url)}catch(e){setError((e as Error).message)}}
  useEffect(()=>{if(active)void loadMessages(active);else setMessages([])},[active])
  useEffect(()=>{const target=bottom.current;if(typeof target?.scrollIntoView==='function')target.scrollIntoView({behavior:'smooth'})},[messages])
  async function createChat(){try{const c=await api.createConversation();await loadChats();setActive(c.conversation_uid)}catch(e){setError((e as Error).message)}}
  async function upload(files:FileList|null){if(!files?.length)return;setUploading(true);try{await api.upload(Array.from(files));await loadDocs();void refreshOverview(true)}catch(e){setError((e as Error).message)}finally{setUploading(false);if(pdfRef.current)pdfRef.current.value=''}}
  async function stream(path:string,question:string,attachments:File[],retryId?:string){setBusy(true);setError('');const optimisticUserUid=`pending-user-${crypto.randomUUID()}`,optimisticAssistantUid=`pending-assistant-${crypto.randomUUID()}`;let assistantUid=retryId||optimisticAssistantUid;if(retryId)setMessages(old=>old.map(m=>m.message_uid===retryId?{...m,content:'',status:'streaming',status_text:'正在准备重新生成…',sources:[]}:m));else setMessages(old=>[...old,{message_uid:optimisticUserUid,role:'user',content:question,status:'completed',attachments:attachments.map((file,i)=>({attachment_uid:`local-${i}-${file.name}`,filename:file.name,mime_type:file.type,url:URL.createObjectURL(file)}))},{message_uid:optimisticAssistantUid,role:'assistant',content:'',status:'streaming',status_text:attachments.length?'正在上传图片…':'正在检索知识库…'}]);
    try{await streamQuestion(path,question,attachments,(type,data)=>{
      if(type==='message_start'){assistantUid=data.message_id;if(!retryId)setMessages(old=>old.map(m=>m.message_uid===optimisticUserUid?{...m,message_uid:data.user_message_id||optimisticUserUid}:m.message_uid===optimisticAssistantUid?{...m,message_uid:assistantUid,status_text:'正在检索知识库…'}:m))}
      if(type==='progress')setMessages(old=>old.map(m=>m.message_uid===(retryId||assistantUid)?{...m,status_text:data.message||'正在处理…'}:m))
      if(type==='content_delta')setMessages(old=>old.map(m=>m.message_uid===(retryId||assistantUid)?{...m,content:m.content+data.delta,status_text:'正在生成回答…'}:m))
      if(type==='sources'){const src=(data.items||[]).map((s:any)=>({...s,image_url:s.image_url||s.images?.[0]?.url}));setMessages(old=>old.map(m=>m.message_uid===(retryId||assistantUid)?{...m,sources:src}:m))}
      if(type==='message_complete')setMessages(old=>old.map(m=>m.message_uid===(retryId||assistantUid)?{...m,status:'completed',status_text:undefined}:m))
      if(type==='error'){setError(data.message);setMessages(old=>old.map(m=>m.message_uid===(retryId||assistantUid)?{...m,status:'failed',status_text:'回答失败，请重试'}:m))}
    },{document_uids:scope,style:prefs.style,depth:prefs.depth,instruction:prefs.instruction})}catch(e){setError((e as Error).message);setMessages(old=>old.map(m=>m.message_uid===(retryId||assistantUid)?{...m,status:'failed',status_text:'发送失败，请重试'}:m))}finally{setBusy(false);setImages([]);if(active)void loadMessages(active);void refreshOverview(true)}}
  async function submit(){const q=text.trim();if(!q||busy)return;if(!active){await createChat();return}setText('');const picked=images.slice();await stream(`/api/v1/conversations/${active}/messages`,q,picked)}
  function handleComposerPaste(event:React.ClipboardEvent<HTMLTextAreaElement>){
    const pasted=Array.from(event.clipboardData.items).filter(item=>item.kind==='file'&&['image/jpeg','image/png','image/webp'].includes(item.type)).map(item=>item.getAsFile()).filter((file):file is File=>Boolean(file))
    if(!pasted.length)return
    event.preventDefault()
    const allowed=pasted.filter(file=>file.size<=10*1024*1024)
    if(allowed.length<pasted.length)setError('图片大小不能超过 10 MB')
    setImages(current=>[...current,...allowed].slice(0,5))
    if(currentImageCount()+allowed.length>5)setError('最多添加 5 张图片')
  }
  function currentImageCount(){return images.length}
  function handleComposerKeyDown(event:React.KeyboardEvent<HTMLTextAreaElement>){
    if((event.metaKey||event.ctrlKey)&&event.shiftKey&&event.key.toLowerCase()==='i'){event.preventDefault();imageRef.current?.click();return}
    if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();void submit()}
  }
  async function retry(m:Message){const index=messages.indexOf(m),question=[...messages.slice(0,index)].reverse().find(x=>x.role==='user');if(question&&active)await stream(`/api/v1/messages/${m.message_uid}/retry`,question.content,[],m.message_uid)}
  async function removeDoc(d:Document){if(!confirm(`删除 ${d.filename}？`))return;try{await api.deleteDocument(d.document_uid);await loadDocs();void refreshOverview(true)}catch(e){setError((e as Error).message)}}
  async function removeChat(c:Conversation){try{await api.deleteConversation(c.conversation_uid);if(active===c.conversation_uid)setActive('');await loadChats();void refreshOverview(true)}catch(e){setError((e as Error).message)}}
  const rawServices:Record<string,string>=overview?.health?.services||{sqlite:'ok'}
  const serviceEntries=[...SERVICE_ORDER.filter(k=>k in rawServices),...Object.keys(rawServices).filter(k=>!SERVICE_ORDER.includes(k))].map(k=>[k,rawServices[k]] as const)
  const okServices=serviceEntries.filter(([,v])=>v==='ok').length,errorServices=serviceEntries.filter(([,v])=>v==='error').length,totalServices=serviceEntries.length
  const docTotal=overview?.knowledge?.total??docs.length
  const docReady=overview?.knowledge?.completed??docs.filter(d=>d.status==='completed').length
  const docProcessing=overview?.knowledge?.processing??docs.filter(d=>d.status==='pending'||d.status==='processing').length
  const docBytes=overview?.knowledge?.bytes??docs.reduce((sum,d)=>sum+(d.file_size||0),0)
  const questions=overview?.stats?.questions??0,answers=overview?.stats?.answers??0,tokens=overview?.stats?.tokens??0
  const inputTokens=overview?.stats?.input_tokens??0,outputTokens=overview?.stats?.output_tokens??0,llmFailures=overview?.stats?.llm_failures??0
  const statusSummary=errorServices>0?`${errorServices} 项服务异常`:overview?(okServices===totalServices?`服务正常 ${okServices}/${totalServices}`:`API 已连接 · ${okServices}/${totalServices}`):'API 已连接'
  return <div className="shell"><aside className="sidebar"><div className="brand"><span className="brand-icon"><BookOpen size={19}/></span><span><b>知页</b><small>多模态知识库</small></span></div><input aria-label="API Key" type="password" placeholder="API Key（可选）" onChange={e=>{if(e.target.value)localStorage.setItem('rag-api-key',e.target.value);else localStorage.removeItem('rag-api-key')}}/>
    <button className="new-chat" onClick={createChat}><Plus size={17}/>新建对话</button><section><div className="section-heading">最近对话 <span>{chats.length}</span></div><input aria-label="搜索对话" placeholder="搜索对话" onChange={async e=>{const q=e.target.value;if(q.length>1){try{setChats((await api.searchConversations(q)).items)}catch{}}else void loadChats()}}/>{chats.map(c=><div className={`chat-item ${active===c.conversation_uid?'selected':''}`} key={c.conversation_uid}><button onClick={()=>setActive(c.conversation_uid)}><MessageSquare size={15}/><span>{c.title||'新对话'}</span></button><button className="small-icon" title="置顶/取消置顶" onClick={()=>api.pin(c.conversation_uid,!c.is_pinned).then(loadChats)}>{c.is_pinned?'★':'☆'}</button><button className="small-icon" aria-label="删除对话" onClick={()=>removeChat(c)}><Trash2 size={14}/></button></div>)}<button className="danger-action" onClick={()=>{if(confirm('清空全部历史对话？此操作不可撤销。'))api.clearConversations().then(()=>{setActive('');setChats([]);setMessages([]);void refreshOverview(true)}).catch(e=>setError(e.message))}}><Trash2 size={14}/>清空全部对话</button></section>
    <section className="documents"><div className="section-heading">知识库文档 <span>{docs.length}</span></div><button className="upload" disabled={uploading} onClick={()=>pdfRef.current?.click()}>{uploading?<LoaderCircle className="spin" size={16}/>:<Upload size={16}/>}上传 PDF<input ref={pdfRef} hidden type="file" accept="application/pdf,.pdf" multiple onChange={e=>upload(e.target.files)}/></button>{docs.map(d=><div className="doc-item" key={d.document_uid}><input aria-label={`检索范围 ${d.filename}`} type="checkbox" disabled={d.status!=='completed'} checked={scope.includes(d.document_uid)} onChange={e=>setScope(old=>e.target.checked?[...old,d.document_uid]:old.filter(x=>x!==d.document_uid))}/><FileText size={16}/><span className="doc-name"><b>{d.filename}</b><small><i className={`dot ${d.status}`}/>{({pending:'待解析',processing:'解析中',completed:'已完成',failed:'解析失败'} as any)[d.status]||d.status}</small></span><button className="small-icon" title="重新解析" disabled={d.status==='processing'} onClick={()=>api.reparse(d.document_uid).then(()=>{void loadDocs();void refreshOverview(true)}).catch(e=>setError(e.message))}><RotateCcw size={13}/></button><button className="small-icon" title="删除文档" onClick={()=>removeDoc(d)}><Trash2 size={13}/></button></div>)}{!docs.length&&<p className="empty-note">上传 PDF 开始构建知识库</p>}<button className="danger-action" onClick={()=>{if(confirm('清空知识库？此操作不可撤销。'))api.clearKnowledge().then(()=>{void loadDocs();void refreshOverview(true)}).catch(e=>setError(e.message))}}><Trash2 size={14}/>清空知识库</button></section><div className="sidebar-bottom"><i className="dot completed"/>本地知识库</div></aside>
    <main className="main"><header><div className="header-heading"><small>WORKSPACE / KNOWLEDGE</small><h1>{chats.find(c=>c.conversation_uid===active)?.title||'知识库问答'}</h1></div>
      <div className="overview-anchor" ref={overviewRef}>
        <button type="button" className={`overview-trigger ${errorServices>0?'has-error':''} ${overviewOpen?'is-open':''}`} aria-expanded={overviewOpen} aria-haspopup="dialog" aria-controls="system-overview-popover" onClick={()=>{const next=!overviewOpen;setOverviewOpen(next);if(next)void refreshOverview(true)}}>
          <span className="overview-trigger-status"><i className={`dot ${errorServices>0?'failed':'completed'}`}/><b>{statusSummary}</b></span>
          <span className="overview-trigger-divider" aria-hidden="true"/>
          <span className="overview-trigger-meta">文档 {docReady}/{docTotal} · 提问 {questions} · Token {formatCompact(tokens)}</span>
          <span className="overview-trigger-action"><span>系统概况</span><ChevronDown size={14} className="overview-chevron"/></span>
        </button>
        {overviewOpen&&<section id="system-overview-popover" className="overview-popover" role="dialog" aria-label="系统概况">
          <div className="overview-popover-head">
            <div className="overview-popover-title"><span className="overview-title-icon"><Activity size={15}/></span><div><b>系统运行概况</b><small>{overviewUpdatedAt?`最近同步 ${overviewUpdatedAt}`:'实时运行指标'}</small></div></div>
            <button type="button" className="overview-refresh-btn" disabled={overviewLoading} onClick={()=>void refreshOverview(false)}><RefreshCw size={13} className={overviewLoading?'spin':''}/><span>刷新</span></button>
          </div>
          <div className="overview-kpi-grid">
            <div className="overview-kpi-card">
              <span className="kpi-icon kpi-docs"><FileText size={16}/></span>
              <div className="kpi-body"><span>知识库文档</span><b>{docReady}<em> / {docTotal} 篇</em></b><small>{docProcessing>0?`${docProcessing} 篇正在解析`:docTotal>0?`已就绪 · ${formatBytes(docBytes)}`:'尚未上传 PDF'}</small></div>
            </div>
            <div className="overview-kpi-card">
              <span className="kpi-icon kpi-asks"><MessageSquare size={16}/></span>
              <div className="kpi-body"><span>累计提问</span><b>{questions.toLocaleString('zh-CN')}</b><small>{chats.length>0?`当前保留 ${chats.length} 个会话`:'发起对话后自动累计'}</small></div>
            </div>
            <div className="overview-kpi-card">
              <span className="kpi-icon kpi-answers"><CheckCircle2 size={16}/></span>
              <div className="kpi-body"><span>已完成回答</span><b>{answers.toLocaleString('zh-CN')}</b><small>{llmFailures>0?`${llmFailures} 次调用异常`:questions>0?`问答完成率 ${Math.min(100,Math.round((answers/questions)*100))}%`:'流式回答服务就绪'}</small></div>
            </div>
            <div className="overview-kpi-card">
              <span className="kpi-icon kpi-tokens"><Zap size={16}/></span>
              <div className="kpi-body"><span>Token 用量</span><b>{tokens.toLocaleString('zh-CN')}</b><small>{inputTokens||outputTokens?`输入 ${formatCompact(inputTokens)} · 输出 ${formatCompact(outputTokens)}`:tokens>0?'累计模型消耗':'暂无模型消耗'}</small></div>
            </div>
          </div>
          <div className="overview-services">
            <div className="overview-services-head"><span>核心依赖状态</span><em className={`overview-health-badge ${errorServices>0?'is-error':'is-ok'}`}>{errorServices>0?`${errorServices} 项异常`:`${okServices}/${totalServices} 运行正常`}</em></div>
            <div className="overview-service-grid">
              {serviceEntries.map(([k,v])=><div className={`overview-service-tile ${v==='ok'?'is-ok':v==='error'?'is-error':'is-idle'}`} key={k}><span className="service-tile-name"><i className={`dot ${v==='ok'?'completed':v==='error'?'failed':''}`}/>{SERVICE_NAMES[k]||k}</span><small>{v==='ok'?'正常':v==='error'?'异常':'未配置'}</small></div>)}
            </div>
          </div>
        </section>}
      </div>
    </header>
      {error&&<div role="alert" className="error"><AlertCircle size={17}/>{error}<button onClick={()=>setError('')}>关闭</button></div>}
      <div className="messages">{!active||!messages.length?<div className="welcome"><span className="welcome-icon"><Sparkles size={22}/></span><small>YOUR KNOWLEDGE, CONNECTED</small><h2>让文档里的知识<br/><em>开始对话。</em></h2><p>上传 PDF，提问时可以附上图片。回答会展示引用来源。</p><div className="prompt-suggestions"><button onClick={()=>setText("请总结这份文档的核心内容")}>总结文档核心内容</button><button onClick={()=>setText("列出文档中的关键结论和依据")}>提取关键结论</button><button onClick={()=>setText("针对这份资料，给我一个学习提纲")}>生成学习提纲</button></div>{!active&&<button className="primary" onClick={createChat}><Plus size={16}/>开始新对话</button>}</div>:messages.map(m=>{const distinctSources=(m.sources||[]).filter((s,idx,arr)=>!s.chunk_id||arr.findIndex(x=>x.chunk_id===s.chunk_id)===idx);const renderedContent=m.role==='assistant'?linkifyCitations(m.content||(m.status==='streaming'?(m.status_text||'思考中…'):''),distinctSources.length):(m.content||'');return <article className="message" key={m.message_uid}><span className="avatar">{m.role==='assistant'?<Sparkles size={16}/>:<span>你</span>}</span><div className="message-content"><div className="message-title"><b>{m.role==='assistant'?'知页助手':'你'}</b>{m.status==='streaming'&&<small>{m.status_text||'正在回答…'}</small>}</div>{m.attachments?.map(a=><img className="user-image" key={a.attachment_uid} src={a.url.startsWith('blob:')?a.url:`${API}${a.url}`} alt={a.filename}/>)}<ReactMarkdown remarkPlugins={[remarkGfm]} components={{a:({href,children,...props})=>{if(href?.startsWith('#cite-')){const idx=Number(href.slice(6))-1,target=distinctSources[idx];if(target?.document_uid)return <a href={href} className="citation-chip" onClick={e=>{e.preventDefault();openSource(target)}}>[{children}]</a>;return <span>[{children}]</span>}return <a href={href} target="_blank" rel="noreferrer" {...props}>{children}</a>}}}>{renderedContent}</ReactMarkdown>{m.sources?.length? <div className="sources"><small>参考来源</small>{m.sources.map((s,i)=><div className="source" key={i}><button className="source-link" disabled={!s.document_uid} onClick={()=>openSource(s)}><b>{s.document_name}{s.page_number?` · 第 ${s.page_number} 页`:''}</b><span>{s.locations?.length?'定位并高亮 ↗':'打开 PDF（暂无高亮数据） ↗'}</span></button><p onClick={()=>openSource(s)} style={s.document_uid?{cursor:'pointer'}:undefined}>{s.content_snippet}</p>{s.image_url&&<img src={`${API}${s.image_url}`} alt="文档来源图片" loading="lazy"/>}</div>)}</div>:null}{m.role==='assistant'&&m.status!=='streaming'&&<div className="message-tools"><button aria-label="重新生成回答" className="retry" disabled={busy} onClick={()=>retry(m)}><RotateCcw size={14}/>重新生成</button><button aria-label="赞同回答" className="retry" onClick={()=>api.feedback(m.message_uid,'up').catch(e=>setError(e.message))}><ThumbsUp size={14}/></button><button aria-label="不赞同回答" className="retry" onClick={()=>api.feedback(m.message_uid,'down').catch(e=>setError(e.message))}><ThumbsDown size={14}/></button></div>}</div></article>})}<div ref={bottom}/></div>
      {active&&<footer><details className="preference-panel"><summary><Sparkles size={14}/>回答偏好</summary><div className="preference-controls"><label>风格 <select value={prefs.style} onChange={e=>setPrefs(p=>({...p,style:e.target.value}))}><option value="concise">简洁</option><option value="detailed">详细</option><option value="teaching">教学</option><option value="technical">技术分析</option></select></label><label>深度 <select value={prefs.depth} onChange={e=>setPrefs(p=>({...p,depth:e.target.value}))}><option value="brief">简要</option><option value="standard">标准</option><option value="deep">深入</option></select></label><input aria-label="自定义回答偏好" placeholder="自定义回答要求" value={prefs.instruction} onChange={e=>setPrefs(p=>({...p,instruction:e.target.value}))}/><button onClick={()=>api.saveSettings(prefs).then(()=>{setPrefsSaved(true);window.setTimeout(()=>setPrefsSaved(false),2000)}).catch(e=>setError(e.message))}>保存偏好</button>{prefsSaved&&<span className="saved-hint">已保存</span>}<button onClick={exportChat}>导出 Markdown</button></div></details><small><Sparkles size={13}/>回答会结合所选文档检索生成</small><div className="composer"><textarea aria-label="输入问题" placeholder="输入问题… Enter 发送，Shift+Enter 换行" value={text} onChange={e=>setText(e.target.value)} onKeyDown={handleComposerKeyDown} onPaste={handleComposerPaste}/>{images.length>0&&<div className="picked">{images.map((f,i)=><span key={i}>{f.name}<button aria-label={`移除 ${f.name}`} onClick={()=>setImages(x=>x.filter((_,j)=>i!==j))}>×</button></span>)}</div>}<div className="composer-actions"><button className="attach" aria-label="附加图片" title="选择图片（Ctrl/⌘ + Shift + I）" onClick={()=>imageRef.current?.click()}><Paperclip size={17}/>图片</button><input ref={imageRef} hidden type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={e=>{setImages(old=>[...old,...Array.from(e.target.files||[])].slice(0,5));if(imageRef.current)imageRef.current.value=''}}/><span>Enter 发送 · Shift + Enter 换行 · Ctrl/⌘ + Shift + I 添加图片</span><button className="send" aria-label="发送问题" disabled={busy||!text.trim()} onClick={submit}>{busy?<LoaderCircle className="spin" size={17}/>:<Send size={17}/>}</button></div></div><p className="disclaimer">AI 回答可能有误，请结合来源核对。</p></footer>}
      {sourcePreview&&<div className="pdf-overlay" role="dialog" aria-modal="true" aria-label="文档原文"><div className="pdf-dialog"><div className="pdf-header"><b>{sourcePreview.name}{sourcePreview.page?` · 第 ${sourcePreview.page} 页`:''}</b><button onClick={()=>setSourcePreview(null)}>关闭</button></div><PdfPreview uid={sourcePreview.uid} page={sourcePreview.page} locations={sourcePreview.locations}/></div></div>}
    </main></div>
}
