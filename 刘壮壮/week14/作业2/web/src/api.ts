export const API = import.meta.env.VITE_API_BASE_URL || ''
const idempotency=()=>crypto.randomUUID()

export type Document = { document_uid:string; filename:string; file_size:number; status:string; updated_at:string }
export type SourceLocation = {page:number;page_width:number;page_height:number;bbox:number[];text:string}
export type Source = { locations?:SourceLocation[]; document_name:string; page_number:number|null; chunk_id:string; content_snippet:string; asset_id:number|null; document_uid?:string|null; image_url?:string|null }
export type Attachment = { attachment_uid:string; filename:string; mime_type:string; url:string }
export type Message = { message_uid:string; role:'user'|'assistant'; content:string; status:string; status_text?:string; attachments?:Attachment[]; sources?:Source[] }
export type Conversation = { conversation_uid:string; title:string|null; is_pinned:number; updated_at:string }

async function request<T>(path:string, init?:RequestInit):Promise<T> {
  const headers=new Headers(init?.headers);const key=localStorage.getItem('rag-api-key');if(key)headers.set('X-API-Key',key)
  const response=await fetch(`${API}${path}`,{...init,headers})
  const body=await response.json()
  if(!response.ok || body.success===false) throw new Error(body.error?.message || `Request failed (${response.status})`)
  return body.data as T
}

export const api={
  documents:()=>request<{items:Document[]}>('/api/v1/documents?page=1&page_size=100'),
  upload:async(files:File[])=>{const data=new FormData();files.forEach(f=>data.append('files',f));return request('/api/v1/documents',{method:'POST',body:data,headers:{'Idempotency-Key':idempotency()}})},
  reparse:(uid:string)=>request(`/api/v1/documents/${uid}/reparse`,{method:'POST',headers:{'Idempotency-Key':idempotency()}}),
  deleteDocument:(uid:string)=>request(`/api/v1/documents/${uid}`,{method:'DELETE',headers:{'Idempotency-Key':idempotency()}}),
  conversations:()=>request<{items:Conversation[]}>('/api/v1/conversations?page=1&page_size=100'),
  createConversation:()=>request<Conversation>('/api/v1/conversations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}),
  deleteConversation:(uid:string)=>request(`/api/v1/conversations/${uid}`,{method:'DELETE'}),
  messages:(uid:string)=>request<{items:Message[]}>(`/api/v1/conversations/${uid}/messages`),
  settings:()=>request<any>('/api/v1/settings'),
  saveSettings:(value:any)=>request('/api/v1/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)}),
  pin:(uid:string,is_pinned:boolean)=>request(`/api/v1/conversations/${uid}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({is_pinned})}),
  clearKnowledge:()=>request('/api/v1/knowledge',{method:'DELETE'}),
  clearConversations:()=>request('/api/v1/conversations',{method:'DELETE'}),
  feedback:(uid:string,rating:'up'|'down'|null)=>request(`/api/v1/messages/${uid}/feedback`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({rating})}),
  exportUrl:(uid:string)=>`${API}/api/v1/conversations/${uid}/export.md`,
  exportConversation:async(uid:string)=>{const headers=new Headers();const key=localStorage.getItem('rag-api-key');if(key)headers.set('X-API-Key',key);const response=await fetch(`${API}/api/v1/conversations/${uid}/export.md`,{headers});if(!response.ok)throw new Error('导出失败');return response.blob()},
  stats:()=>request<any>('/api/v1/stats/usage'),
  knowledgeStats:()=>request<{total:number;completed:number;processing:number;failed:number;bytes:number}>('/api/v1/knowledge/stats'),
  health:()=>request<any>('/api/v1/health/dependencies'),
  searchConversations:(q:string)=>request<{items:Conversation[]}>(`/api/v1/conversations/search?q=${encodeURIComponent(q)}`),
}

export async function streamQuestion(path:string, content:string, images:File[], onEvent:(event:string,data:any)=>void, options:{document_uids:string[],style:string,depth:string,instruction:string}) {
  const body=new FormData();body.append('content',content);images.forEach(image=>body.append('images',image));if(options.document_uids.length)body.append('document_uids',options.document_uids.join(','));body.append('style',options.style);body.append('depth',options.depth);body.append('instruction',options.instruction)
  const headers=new Headers({Accept:'text/event-stream','Idempotency-Key':idempotency()});const key=localStorage.getItem('rag-api-key');if(key)headers.set('X-API-Key',key)
  const response=await fetch(`${API}${path}`,{method:'POST',body,headers})
  if(!response.ok || !response.body){let message=`Request failed (${response.status})`;try{message=(await response.json()).error?.message||message}catch{};throw new Error(message)}
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer=''
  while(true){const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});
    const events=buffer.split(/\r?\n\r?\n/);buffer=events.pop()||''
    for(const eventText of events){const event=eventText.match(/^event:\s*(.+)$/m)?.[1];const data=eventText.match(/^data:\s*(.+)$/m)?.[1];if(event&&data){try{onEvent(event,JSON.parse(data))}catch{}}}
    if(done)break
  }
}
