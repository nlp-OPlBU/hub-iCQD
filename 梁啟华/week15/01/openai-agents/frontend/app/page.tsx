"use client";

import type { FormEvent, KeyboardEvent } from "react";
import { useEffect, useRef, useState } from "react";

type EmotionLabel = "positive" | "negative" | "neutral";
type Strategy = "encouragement" | "relief" | "neutral";

interface EmotionInfo {
  label: EmotionLabel;
  confidence: number;
  reason: string;
}

interface ChatApiResponse {
  reply: string;
  emotion: EmotionInfo;
  strategy: Strategy;
  strategy_label: string;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  emotion?: EmotionInfo;
  strategyLabel?: string;
}

const FRAMEWORK = "OpenAI Agents";
const FRAMEWORK_TAG = "OPENAI AGENTS · DEEPSEEK";
const PIPELINE_NOTE =
  "后端先用 Runner 运行情感识别 Agent，再根据标签选择鼓励、疏导或陪伴 Agent，串行生成回复。";

const EMOTION_META: Record<
  EmotionLabel,
  { label: string; hint: string }
> = {
  positive: { label: "积极", hint: "积极情绪 · 正向鼓励" },
  negative: { label: "消极", hint: "消极情绪 · 情绪疏导" },
  neutral: { label: "中性", hint: "中性情绪 · 温和陪伴" },
};

const STARTERS = [
  "项目终于上线了，特别开心！",
  "最近事情好多，有点焦虑。",
  "今天很平静，没什么特别的。",
];

const WELCOME_MESSAGE: ChatMessage = {
  id: "welcome",
  role: "assistant",
  content:
    "你好，我是心语。你可以把此刻的心情写下来——我会先理解你的情绪，再陪你聊一聊。",
};

function createId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function isEmotion(value: unknown): value is EmotionInfo {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return (
    (candidate.label === "positive" ||
      candidate.label === "negative" ||
      candidate.label === "neutral") &&
    typeof candidate.confidence === "number" &&
    typeof candidate.reason === "string"
  );
}

function isChatApiResponse(value: unknown): value is ChatApiResponse {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.reply === "string" &&
    candidate.reply.trim().length > 0 &&
    isEmotion(candidate.emotion) &&
    (candidate.strategy === "encouragement" ||
      candidate.strategy === "relief" ||
      candidate.strategy === "neutral") &&
    typeof candidate.strategy_label === "string"
  );
}

function extractErrorDetail(payload: unknown): string | null {
  if (!payload || typeof payload !== "object" || !("detail" in payload)) {
    return null;
  }
  const detail = (payload as { detail?: unknown }).detail;
  return typeof detail === "string" ? detail : null;
}

export default function HomePage() {
  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME_MESSAGE]);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sendingRef = useRef(false);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, isSending]);

  async function sendMessage(rawMessage: string) {
    const content = rawMessage.trim();
    if (!content || sendingRef.current) return;

    sendingRef.current = true;
    setIsSending(true);
    setError(null);
    setInput("");
    setMessages((current) => [
      ...current,
      { id: createId("user"), role: "user", content },
    ]);

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: content }),
      });
      const payload: unknown = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(
          extractErrorDetail(payload) ?? "请求失败，请稍后再试。",
        );
      }
      if (!isChatApiResponse(payload)) {
        throw new Error("后端返回的数据格式不符合预期。");
      }

      setMessages((current) => [
        ...current,
        {
          id: createId("assistant"),
          role: "assistant",
          content: payload.reply.trim(),
          emotion: payload.emotion,
          strategyLabel: payload.strategy_label,
        },
      ]);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "网络请求失败，请检查服务是否已启动。",
      );
    } finally {
      sendingRef.current = false;
      setIsSending(false);
      inputRef.current?.focus();
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void sendMessage(input);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      void sendMessage(input);
    }
  }

  function clearConversation() {
    if (sendingRef.current) return;
    setMessages([WELCOME_MESSAGE]);
    setInput("");
    setError(null);
    inputRef.current?.focus();
  }

  return (
    <div className="page-shell">
      <main className="app-grid">
        <section className="chat-card" aria-label="心语情感支持对话">
          <header className="chat-header">
            <div className="brand">
              <div className="brand-mark" aria-hidden="true">
                心
              </div>
              <div className="brand-copy">
                <p className="eyebrow">EMOTION SUPPORT · {FRAMEWORK_TAG}</p>
                <h1>
                  心语 <span className="framework-badge">{FRAMEWORK}</span>
                </h1>
              </div>
            </div>

            <div className="header-actions">
              <span className="model-pill">
                <span className="status-dot" aria-hidden="true" />
                DeepSeek
              </span>
              <button
                type="button"
                className="ghost-button"
                onClick={clearConversation}
                disabled={isSending || messages.length === 1}
              >
                清空
              </button>
            </div>
          </header>

          <div className="messages" aria-live="polite" aria-busy={isSending}>
            {messages.map((message) => (
              <article
                key={message.id}
                className={`message-row ${message.role}`}
              >
                {message.role === "assistant" && (
                  <div className="assistant-avatar" aria-hidden="true">
                    心
                  </div>
                )}
                <div className="message-stack">
                  <div className="message-bubble">{message.content}</div>
                  {message.role === "assistant" && message.emotion && (
                    <div className="process-trace">
                      <span
                        className={`emotion-chip ${message.emotion.label}`}
                        title={message.emotion.reason}
                      >
                        {EMOTION_META[message.emotion.label].label} ·{" "}
                        {Math.round(message.emotion.confidence * 100)}%
                      </span>
                      <span className="trace-arrow" aria-hidden="true">
                        →
                      </span>
                      <span className="strategy-chip">
                        {message.strategyLabel}
                      </span>
                    </div>
                  )}
                </div>
              </article>
            ))}

            {isSending && (
              <div className="message-row assistant" aria-label="正在生成回复">
                <div className="assistant-avatar" aria-hidden="true">
                  心
                </div>
                <div className="message-stack">
                  <div className="message-bubble typing-bubble">
                    <span className="typing-dots" aria-hidden="true">
                      <i />
                      <i />
                      <i />
                    </span>
                    <span>正在识别情绪并组织回应…</span>
                  </div>
                </div>
              </div>
            )}
            <div ref={endRef} />
          </div>

          <footer className="composer-area">
            {messages.length === 1 && !isSending && (
              <div className="starter-block">
                <p>不知道从哪里开始？可以试试：</p>
                <div className="starter-row">
                  {STARTERS.map((starter) => (
                    <button
                      key={starter}
                      type="button"
                      className="starter-chip"
                      onClick={() => void sendMessage(starter)}
                    >
                      {starter}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {error && (
              <div className="error-banner" role="alert">
                <span className="error-icon" aria-hidden="true">
                  !
                </span>
                <p>{error}</p>
              </div>
            )}

            <form className="composer" onSubmit={handleSubmit}>
              <div className="composer-box">
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="写下此刻的心情…"
                  aria-label="输入消息"
                  rows={2}
                  maxLength={4000}
                  autoFocus
                />
                <button
                  type="submit"
                  className="send-button"
                  disabled={!input.trim() || isSending}
                  aria-label="发送消息"
                >
                  {isSending ? (
                    <span className="send-spinner" aria-hidden="true" />
                  ) : (
                    <svg
                      viewBox="0 0 24 24"
                      width="20"
                      height="20"
                      fill="none"
                      aria-hidden="true"
                    >
                      <path
                        d="M5 12h13M13 6l6 6-6 6"
                        stroke="currentColor"
                        strokeWidth="1.8"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  )}
                </button>
              </div>
              <div className="composer-foot">
                <p>Enter 发送 · Shift + Enter 换行</p>
                <span className={input.length > 3600 ? "char-count near-limit" : "char-count"}>
                  {input.length}/4000
                </span>
              </div>
            </form>
          </footer>
        </section>

        <aside className="insight-panel" aria-label="应用说明">
          <section className="panel-card pipeline-card">
            <p className="panel-eyebrow">HOW IT WORKS</p>
            <h2>两阶段支持流程</h2>
            <ol className="pipeline-list">
              <li>
                <span className="step-index">01</span>
                <div>
                  <strong>情感识别</strong>
                  <p>判断用户的情绪倾向：积极、消极或中性。</p>
                </div>
              </li>
              <li>
                <span className="step-index">02</span>
                <div>
                  <strong>策略回复</strong>
                  <p>积极时鼓励，消极时疏导，中性时温和陪伴。</p>
                </div>
              </li>
            </ol>
            <div className="branch-list" aria-label="分支策略">
              <span className="branch positive">鼓励</span>
              <span className="branch negative">疏导</span>
              <span className="branch neutral">陪伴</span>
            </div>
            <p className="pipeline-note">{PIPELINE_NOTE}</p>
          </section>

          <section className="panel-card details-card">
            <p className="panel-eyebrow">UNDER THE HOOD</p>
            <dl className="tech-list">
              <div>
                <dt>模型</dt>
                <dd>DeepSeek Chat</dd>
              </div>
              <div>
                <dt>编排</dt>
                <dd>OpenAI Agents SDK</dd>
              </div>
              <div>
                <dt>前端</dt>
                <dd>Next.js + React</dd>
              </div>
              <div>
                <dt>后端</dt>
                <dd>FastAPI</dd>
              </div>
            </dl>
          </section>

          <p className="safety-note">
            心语提供的是日常情绪陪伴，不构成医疗或心理诊断。若你或身边的人正处于紧急危险中，请立即联系当地紧急服务或可信任的人。
          </p>
        </aside>
      </main>
    </div>
  );
}
