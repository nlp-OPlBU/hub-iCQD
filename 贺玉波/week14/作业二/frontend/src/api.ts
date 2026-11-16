/** 后端接口封装：统一错误提取 + SSE 解析。 */

const BASE = "/api/v1";

export interface DocumentItem {
  id: string;
  filename: string;
  status: string;
  stage: string | null;
  progress: number;
  attempts: number;
  page_count: number | null;
  chunk_count: number;
  image_count: number;
  size_bytes: number;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface Hit {
  chunk_id: string;
  doc_id: string;
  doc_title?: string;
  kind: string;
  page_no: number;
  text: string;
  image_path: string | null;
  /** 图片说明（入库时生成，纯图片命中时才可能有） */
  caption?: string;
  /** 最终排序分：重排序可用时是交叉编码器分数，否则退回 RRF 分 */
  score: number;
  rrf_score?: number;
  rerank_score?: number | null;
  /** 各通道自己的原始分（向量余弦 / BM25），量纲不同，仅用于调试 */
  raw_scores?: Record<string, number>;
  sources: string[];
}

export interface Citation {
  index: number;
  doc_id: string;
  doc_title?: string;
  chunk_id: string;
  page_no: number;
  snippet: string;
  image_path: string | null;
  /** 图片在上下文里的编号，对应【图N】 */
  image_index?: number;
  score: number;
  sources: string[];
}

export interface ChatResult {
  session_id: string;
  answer: string;
  citations: Citation[];
  hits: Hit[];
  need_retrieval: boolean;
  /** 实际用于检索的 query：短问题会被补全指代后改写 */
  retrieval_query?: string | null;
}

export interface ChatPayload {
  question: string;
  session_id?: string;
  top_k?: number;
  doc_id?: string;
  /** 用户粘贴/上传的图片，data:image/...;base64 */
  image?: string;
  with_images?: boolean;
}

export interface ChatEvent {
  event: "meta" | "delta" | "done" | "error";
  data: any;
}

export interface SessionItem {
  session_id: string;
  title: string;
  message_count: number;
  updated_at: string;
}

/** 历史消息：助手回答带回当时的引用快照，用于还原来源卡片与图片。 */
export interface HistoryMessage {
  seq: number;
  role: string;
  content: string;
  citations: Citation[] | null;
}

export interface TraceItem {
  trace_id: string;
  entry: string | null;
  span_count: number;
  total_ms: number;
  max_ms: number;
  error_count: number;
  started_at: string;
}

export interface SpanItem {
  span: string;
  parent: string | null;
  dur_ms: number;
  status: string;
  start_ts: string;
  attrs: Record<string, unknown>;
}

export interface TraceNodeStat {
  span: string;
  count: number;
  total_ms: number;
  avg_ms: number;
  max_ms: number;
  error_count: number;
}

export interface TraceSummary {
  span_count: number;
  total_ms: number;
  error_count: number;
  slowest: { span: string; dur_ms: number; ref_id: string | null } | null;
  nodes: TraceNodeStat[];
}

export interface TraceDetail {
  trace_id: string;
  source: string;
  span_count: number;
  spans: SpanItem[];
  summary: TraceSummary;
}

export interface ChunkItem {
  id: string;
  idx: number;
  kind: string;
  page_no: number | null;
  text: string;
  image_path: string | null;
}

/** 最近一次请求的 trace_id（响应头 X-Request-Id），供链路追踪页一键复现。 */
export let lastTraceId = "";

function rememberTrace(response: Response): void {
  const id = response.headers.get("X-Request-Id");
  if (id) lastTraceId = id;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init);
  rememberTrace(response);
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      message = body?.error?.message || message;
    } catch {
      // 响应不是 JSON 时保留默认文案
    }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  upload(file: File): Promise<{ created: boolean; document: DocumentItem }> {
    const form = new FormData();
    form.append("file", file);
    return request("/documents", { method: "POST", body: form });
  },

  /** 带上传进度回调：fetch 拿不到上传进度，这里用 XHR 的 upload.onprogress。 */
  uploadWithProgress(
    file: File,
    onProgress?: (percent: number) => void,
  ): Promise<{ created: boolean; document: DocumentItem }> {
    const form = new FormData();
    form.append("file", file);
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BASE}/documents`);
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          onProgress?.(Math.round((event.loaded / event.total) * 100));
        }
      };
      xhr.onload = () => {
        const id = xhr.getResponseHeader("X-Request-Id");
        if (id) lastTraceId = id;
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText));
          } catch {
            reject(new Error("响应解析失败"));
          }
          return;
        }
        let message = `请求失败（${xhr.status}）`;
        try {
          message = JSON.parse(xhr.responseText)?.error?.message || message;
        } catch {
          // 响应不是 JSON 时保留默认文案
        }
        reject(new Error(message));
      };
      xhr.onerror = () => reject(new Error("网络错误，上传中断"));
      xhr.onabort = () => reject(new Error("上传已取消"));
      xhr.send(form);
    });
  },

  listDocuments(limit = 50): Promise<{ items: DocumentItem[]; total: number }> {
    return request(`/documents?limit=${limit}`);
  },

  getDocument(id: string): Promise<DocumentItem> {
    return request(`/documents/${id}`);
  },

  chunks(id: string, limit = 200, offset = 0): Promise<ChunkItem[]> {
    return request(`/documents/${id}/chunks?limit=${limit}&offset=${offset}`);
  },

  retryDocument(id: string): Promise<DocumentItem> {
    return request(`/documents/${id}/retry`, { method: "POST" });
  },

  search(
    query: string,
    topK = 10,
    withImages = true,
  ): Promise<{ took_ms: number; count: number; stages: Record<string, number>; hits: Hit[] }> {
    const params = new URLSearchParams({ q: query, top_k: String(topK), with_images: String(withImages) });
    return request(`/search?${params.toString()}`);
  },

  chat(payload: ChatPayload): Promise<ChatResult> {
    return request("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  history(sessionId: string): Promise<{ session_id: string; messages: HistoryMessage[] }> {
    return request(`/chat/${sessionId}/history`);
  },

  listSessions(limit = 30): Promise<{ items: SessionItem[] }> {
    return request(`/chat/sessions?limit=${limit}`);
  },

  /** 删除整个会话：消息与摘要一起清掉。 */
  deleteSession(sessionId: string): Promise<void> {
    return request(`/chat/${sessionId}`, { method: "DELETE" });
  },

  listTraces(limit = 20): Promise<{ items: TraceItem[] }> {
    return request(`/traces?limit=${limit}`);
  },

  getTrace(traceId: string): Promise<TraceDetail> {
    return request(`/traces/${traceId}`);
  },

  /** 流式问答：逐个事件回调，返回完整回答文本。 */
  async chatStream(
    payload: ChatPayload,
    onEvent: (event: ChatEvent) => void,
  ): Promise<string> {
    const response = await fetch(`${BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, stream: true }),
    });
    if (!response.ok || !response.body) {
      throw new Error(`流式请求失败（${response.status}）`);
    }
    rememberTrace(response);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let answer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const eventLine = block.split("\n").find((line) => line.startsWith("event:"));
        const dataLine = block.split("\n").find((line) => line.startsWith("data:"));
        if (!eventLine || !dataLine) continue;
        const event = eventLine.slice(6).trim() as ChatEvent["event"];
        const data = JSON.parse(dataLine.slice(5).trim());
        if (event === "delta") answer += data;
        onEvent({ event, data });
      }
    }
    return answer;
  },

  /** Prometheus 抓取口在根路径，不走 /api/v1。 */
  async metricsText(): Promise<string> {
    const response = await fetch("/metrics");
    if (!response.ok) throw new Error(`读取指标失败（${response.status}）`);
    return response.text();
  },
};
