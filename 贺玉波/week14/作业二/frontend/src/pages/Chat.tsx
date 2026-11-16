import { useEffect, useRef, useState, type ClipboardEvent } from "react";

import { api, Citation, HistoryMessage, SessionItem } from "../api";
import Markdown from "../components/Markdown";

interface Message {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  /** 用户随问题一起发送的图片（data URL） */
  image?: string;
}

const MAX_IMAGE_BYTES = 4 * 1024 * 1024;

/** 读成本地 data URL 再随请求发送，避免多一次上传接口的往返。 */
function readImage(file: File, onOk: (url: string) => void, onErr: (msg: string) => void): void {
  if (!file.type.startsWith("image/")) {
    onErr("只能粘贴图片文件");
    return;
  }
  if (file.size > MAX_IMAGE_BYTES) {
    onErr("图片超过 4MB，请压缩后再试");
    return;
  }
  const reader = new FileReader();
  reader.onload = () => onOk(String(reader.result));
  reader.onerror = () => onErr("读取图片失败");
  reader.readAsDataURL(file);
}

function replaceLast(list: Message[], patch: Partial<Message>): Message[] {
  if (list.length === 0) return list;
  const next = list.slice();
  next[next.length - 1] = { ...next[next.length - 1], ...patch };
  return next;
}

/** 历史回放：带上助手消息当时的引用快照，来源卡片和图片才能重新显示。 */
function toMessages(items: HistoryMessage[]): Message[] {
  return items.map((item) => ({
    role: item.role === "user" ? "user" : "assistant",
    content: item.content,
    citations: item.citations && item.citations.length > 0 ? item.citations : undefined,
  }));
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const diff = Date.now() - date.getTime();
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  return `${Math.floor(diff / 86_400_000)} 天前`;
}

/** 引用来源：单列卡片，图片配文字说明，末尾再汇总一次文档级来源。 */
function Sources({ citations }: { citations: Citation[] }) {
  const docs = new Map<string, { title: string; pages: number[] }>();
  for (const citation of citations) {
    const key = citation.doc_id || "unknown";
    const entry = docs.get(key) || { title: citation.doc_title || citation.doc_id.slice(0, 8), pages: [] };
    if (citation.page_no && !entry.pages.includes(citation.page_no)) entry.pages.push(citation.page_no);
    docs.set(key, entry);
  }

  return (
    <div className="sources">
      <div className="sources-head">引用来源 · {citations.length}</div>
      {citations.map((citation) => (
        <div className="source" key={citation.chunk_id || citation.index}>
          <div className="source-body">
            <div className="source-meta">
              <span className="source-index">[{citation.index}]</span>
              <span className="source-doc">{citation.doc_title || citation.doc_id.slice(0, 8)}</span>
              <span> · 第 {citation.page_no || "?"} 页</span>
              {citation.image_path && (
                <span className="badge info">
                  {citation.image_index ? `图${citation.image_index}` : "图片"}
                </span>
              )}
              {citation.sources.length > 0 && <span> · {citation.sources.join(" + ")}</span>}
            </div>
            {citation.image_path && (
              <a className="source-img-link" href={citation.image_path} target="_blank" rel="noreferrer">
                <img
                  className="source-img"
                  src={citation.image_path}
                  alt={`${citation.doc_title || "文档"} 第 ${citation.page_no || "?"} 页的图`}
                  loading="lazy"
                />
                <span className="source-img-hint">
                  {citation.image_index ? `对应资料里的【图${citation.image_index}】` : "来自上文所引页面"}，点击查看原图
                </span>
              </a>
            )}
            {citation.snippet && <div className="source-text">{citation.snippet}</div>}
          </div>
        </div>
      ))}

      <div className="source-docs">
        <div className="source-docs-title">文档来源</div>
        <ul>
          {[...docs.entries()].map(([docId, doc]) => (
            <li key={docId}>
              {doc.title}
              {doc.pages.length > 0 && (
                <span className="muted small"> · 第 {doc.pages.sort((a, b) => a - b).join("、")} 页</span>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default function ChatPage() {
  const [sessionId, setSessionId] = useState(() => localStorage.getItem("rag_session") || "");
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [question, setQuestion] = useState("");
  const [image, setImage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [useStream, setUseStream] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  // 只在首次挂载时恢复历史：之后 meta 事件会更新 sessionId，不能触发重新拉取覆盖当前对话
  const initialSession = useRef(sessionId);

  function refreshSessions() {
    api
      .listSessions()
      .then((result) => setSessions(result.items))
      .catch(() => {
        // 列表是辅助信息，失败时不打断问答
      });
  }

  useEffect(() => {
    refreshSessions();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    const id = initialSession.current;
    if (!id) return;
    let cancelled = false;
    api
      .history(id)
      .then((result) => {
        if (cancelled) return;
        setMessages(toMessages(result.messages));
      })
      .catch(() => {
        // 会话被清理或已过期时静默忽略，直接从空对话开始
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function rememberSession(id: string) {
    setSessionId(id);
    localStorage.setItem("rag_session", id);
  }

  function openSession(id: string) {
    if (id === sessionId) return;
    setError(null);
    setBusy(false);
    rememberSession(id);
    setMessages([]);
    api
      .history(id)
      .then((result) => {
        setMessages(toMessages(result.messages));
      })
      .catch((err) => setError(err instanceof Error ? err.message : "读取会话失败"));
  }

  async function send() {
    const text = question.trim();
    const shot = image;
    if ((!text && !shot) || busy) return;
    setQuestion("");
    setImage(null);
    setBusy(true);
    setError(null);
    setMessages((prev) => [
      ...prev,
      { role: "user", content: text || "（图片提问）", image: shot ?? undefined },
      { role: "assistant", content: "" },
    ]);
    const payload = {
      question: text,
      session_id: sessionId || undefined,
      image: shot ?? undefined,
    };

    try {
      if (useStream) {
        let answer = "";
        await api.chatStream(payload, (event) => {
          if (event.event === "meta") {
            rememberSession(event.data.session_id);
            setMessages((prev) => replaceLast(prev, { citations: event.data.citations }));
          } else if (event.event === "delta") {
            answer += event.data;
            setMessages((prev) => replaceLast(prev, { content: answer }));
          } else if (event.event === "error") {
            setError(event.data.message);
          }
        });
      } else {
        const result = await api.chat(payload);
        rememberSession(result.session_id);
        setMessages((prev) => replaceLast(prev, { content: result.answer, citations: result.citations }));
      }
      refreshSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : "问答失败");
      setMessages((prev) => replaceLast(prev, { content: "（本轮生成失败）" }));
    } finally {
      setBusy(false);
    }
  }

  function attachImage(file: File | null | undefined) {
    if (!file) return;
    readImage(
      file,
      (url) => {
        setImage(url);
        setError(null);
      },
      (message) => setError(message),
    );
  }

  function handlePaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const item = Array.from(event.clipboardData.items).find(
      (entry) => entry.kind === "file" && entry.type.startsWith("image/"),
    );
    const file = item?.getAsFile();
    if (!file) return;
    // 剪贴板里同时有文字时保留默认粘贴，只有纯图片才拦截
    if (!event.clipboardData.getData("text/plain")) event.preventDefault();
    attachImage(file);
  }

  async function deleteSession(id: string) {
    if (!window.confirm("删除这个会话？对话记录会一并清空，不可恢复。")) return;
    try {
      await api.deleteSession(id);
      setSessions((prev) => prev.filter((item) => item.session_id !== id));
      if (id === sessionId) resetSession();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除会话失败");
    }
  }

  function resetSession() {
    setMessages([]);
    setSessionId("");
    localStorage.removeItem("rag_session");
    refreshSessions();
  }

  return (
    <section className="chat-page">
      <header className="page-head">
        <h1>智能问答</h1>
        <p className="muted">回答只依据检索到的资料，编号引用可回查页码与原文片段。</p>
      </header>

      <div className="chat-layout">
        <div className="chat-main">
          <div className="chat-toolbar">
            <label className="switch">
              <input type="checkbox" checked={useStream} onChange={(event) => setUseStream(event.target.checked)} />
              流式输出
            </label>
            <span className="mono small muted">session: {sessionId ? sessionId.slice(0, 12) : "新建"}</span>
            <button className="button ghost" onClick={resetSession}>
              新会话
            </button>
          </div>

          {error && <div className="alert error">{error}</div>}

          <div className="chat-log">
            {messages.length === 0 && (
              <div className="muted">先上传文档，然后问点什么，例如「这份文档的核心结论是什么？」</div>
            )}
            {messages.map((message, index) => (
              <div key={index} className={`bubble ${message.role}`}>
                {message.role === "user" ? (
                  <div className="bubble-body">
                    {message.image && (
                      <img className="bubble-img" src={message.image} alt="用户上传的图片" />
                    )}
                    {message.content}
                  </div>
                ) : (
                  <>
                    {message.content ? <Markdown text={message.content} /> : <span className="muted">…</span>}
                    {message.citations && message.citations.length > 0 && <Sources citations={message.citations} />}
                  </>
                )}
              </div>
            ))}
            <div ref={bottomRef} />
          </div>

          <div className="chat-input">
            {image && (
              <div className="chat-attach">
                <img src={image} alt="待发送图片" />
                <span className="muted small">图片已附带，将随问题一起发送</span>
                <button className="button ghost small-btn" onClick={() => setImage(null)}>
                  移除
                </button>
              </div>
            )}
            <textarea
              value={question}
              placeholder="输入问题，Enter 发送，Shift+Enter 换行；可直接粘贴图片一起提问"
              onChange={(event) => setQuestion(event.target.value)}
              onPaste={handlePaste}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void send();
                }
              }}
            />
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              hidden
              onChange={(event) => {
                attachImage(event.target.files?.[0]);
                event.target.value = "";
              }}
            />
            <div className="chat-actions">
              <button className="button ghost" onClick={() => fileRef.current?.click()}>
                图片
              </button>
              <button
                className="button primary"
                disabled={busy || (!question.trim() && !image)}
                onClick={() => void send()}
              >
                {busy ? "生成中…" : "发送"}
              </button>
            </div>
          </div>
        </div>

        <aside className="chat-history">
          <div className="chat-history-head">
            <span>历史会话</span>
            <button className="button ghost small-btn" onClick={refreshSessions}>
              刷新
            </button>
          </div>
          <ul>
            {sessions.map((item) => (
              <li
                key={item.session_id}
                className={item.session_id === sessionId ? "active" : ""}
                onClick={() => openSession(item.session_id)}
              >
                <div className="chat-history-main">
                  <div className="chat-history-title ellipsis">{item.title}</div>
                  <div className="muted small">
                    {item.message_count} 条 · {formatTime(item.updated_at)}
                  </div>
                </div>
                <button
                  className="chat-history-del"
                  title="删除会话"
                  aria-label={`删除会话 ${item.title}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    void deleteSession(item.session_id);
                  }}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
          {sessions.length === 0 && <p className="muted small empty">还没有历史会话，问一句会自动创建。</p>}
        </aside>
      </div>
    </section>
  );
}
