import { useEffect, useState } from "react";

import { api, lastTraceId, SpanItem, TraceItem, TraceSummary } from "../api";

const STAGE_ORDER = ["parse", "retrieval", "generate", "other"] as const;
const STAGE_LABEL: Record<string, string> = {
  parse: "文档解析",
  retrieval: "检索召回",
  generate: "大模型回答",
  other: "其他",
};

/** 按 span 名前缀归类：解析 / 检索 / 生成，其余归"其他"。 */
function stageOf(name: string): string {
  if (/^(ingest|mineru)\./.test(name)) return "parse";
  if (/^(search\.|qa\.retrieve)/.test(name)) return "retrieval";
  if (/^(qa\.generate|llm\.)/.test(name)) return "generate";
  return "other";
}

/** 请求路径 -> 人话，表格里一眼知道这次请求在干什么。 */
function entryLabel(entry: string | null): string {
  if (!entry) return "—";
  if (entry.startsWith("/api/v1/chat")) return "问答";
  if (entry.startsWith("/api/v1/search")) return "检索";
  if (entry.startsWith("/api/v1/documents")) return "文档";
  if (entry.startsWith("/api/v1/traces")) return "追踪";
  if (entry.startsWith("/api/v1/")) return entry.slice(8);
  return entry;
}

function clock(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString("zh-CN", { hour12: false });
}

export default function TracePage() {
  const [traces, setTraces] = useState<TraceItem[]>([]);
  const [traceId, setTraceId] = useState(lastTraceId);
  const [spans, setSpans] = useState<SpanItem[] | null>(null);
  const [summary, setSummary] = useState<TraceSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function load(id?: string) {
    const target = (id ?? traceId).trim();
    if (!target) return;
    setTraceId(target);
    setLoading(true);
    setError(null);
    try {
      const detail = await api.getTrace(target);
      setSpans(detail.spans);
      setSummary(detail.summary);
    } catch (err) {
      setSpans(null);
      setSummary(null);
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  async function refreshList() {
    try {
      const result = await api.listTraces(20);
      setTraces(result.items);
      return result.items;
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载 trace 列表失败");
      return [];
    }
  }

  useEffect(() => {
    let cancelled = false;
    api
      .listTraces(20)
      .then((result) => {
        if (cancelled) return;
        setTraces(result.items);
        const first = lastTraceId || result.items[0]?.trace_id;
        if (first) void load(first);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "加载 trace 列表失败"));
    return () => {
      cancelled = true;
    };
    // 只在首屏加载一次；后面用刷新按钮
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // http_request 只是容器，实际耗时都在业务 span 上，瀑布图里不展示
  const visible = (spans ?? []).filter((span) => span.span !== "http_request");
  const base = visible.length > 0 ? Math.min(...visible.map((span) => new Date(span.start_ts).getTime())) : 0;
  const total =
    visible.length > 0
      ? Math.max(...visible.map((span) => new Date(span.start_ts).getTime() - base + span.dur_ms), 1)
      : 1;

  // 阶段耗时只累加叶子 span，避免父 span（如 qa.retrieve）与子 span 重复计数
  const childNames = new Set(visible.map((span) => span.parent).filter(Boolean) as string[]);
  const stageMs = STAGE_ORDER.reduce(
    (acc, key) => {
      acc[key] = visible
        .filter((span) => stageOf(span.span) === key && !childNames.has(span.span))
        .reduce((sum, span) => sum + span.dur_ms, 0);
      return acc;
    },
    {} as Record<string, number>,
  );
  const grandTotal = STAGE_ORDER.reduce((sum, key) => sum + stageMs[key], 0);

  const slowestAvg = summary ? Math.max(1, ...summary.nodes.map((node) => node.avg_ms)) : 1;

  // 用 parent 名找上级，给瀑布图做缩进，父子关系一眼能看出来
  const byName = new Map(visible.map((span) => [span.span, span]));
  function depthOf(span: SpanItem): number {
    let depth = 0;
    let parent = span.parent;
    while (parent && depth < 8) {
      depth += 1;
      parent = byName.get(parent)?.parent ?? null;
    }
    return depth;
  }

  return (
    <section>
      <header className="page-head">
        <h1>链路追踪</h1>
        <p className="muted">
          每个请求带 <span className="mono">X-Request-Id</span>，全链路 span 记录父子关系与耗时。下面是最近的请求，点一行看瀑布图。
        </p>
      </header>

      <div className="card">
        <div className="card-title row-between">
          <span>最近的请求（{traces.length}）</span>
          <button className="button ghost small-btn" onClick={() => void refreshList()}>
            刷新
          </button>
        </div>
        {traces.length === 0 && <div className="muted small">还没有 trace。先去提一个问题或上传一份文档。</div>}
        {traces.length > 0 && (
          <table className="table clickable">
            <thead>
              <tr>
                <th>时间</th>
                <th>入口</th>
                <th>span</th>
                <th>总耗时</th>
                <th>最慢</th>
                <th>错误</th>
                <th>trace_id</th>
              </tr>
            </thead>
            <tbody>
              {traces.map((item) => (
                <tr
                  key={item.trace_id}
                  className={item.trace_id === traceId ? "selected" : ""}
                  onClick={() => void load(item.trace_id)}
                >
                  <td className="mono small">{clock(item.started_at)}</td>
                  <td>
                    <span className="badge info">{entryLabel(item.entry)}</span>
                  </td>
                  <td>{item.span_count}</td>
                  <td className="mono">{item.total_ms} ms</td>
                  <td className="mono">{item.max_ms} ms</td>
                  <td>{item.error_count > 0 ? <span className="badge danger">{item.error_count}</span> : "-"}</td>
                  <td className="mono small muted">{item.trace_id.slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="trace-input">
        <input
          value={traceId}
          placeholder="也可以直接粘贴 trace_id"
          onChange={(event) => setTraceId(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void load();
          }}
        />
        <button className="button primary" disabled={loading || !traceId.trim()} onClick={() => void load()}>
          {loading ? "查询中…" : "查询"}
        </button>
      </div>

      {error && <div className="alert error">{error}</div>}

      {spans && spans.length > 0 && (
        <div className="card">
          <div className="card-title">
            本次请求业务耗时 {grandTotal} ms · 业务 span {visible.length} 个
            {summary && (summary.error_count ?? 0) > 0 && <span className="badge danger"> {summary.error_count} 个错误</span>}
            {summary?.slowest && (
              <span className="muted small">
                {" "}
                最慢：{summary.slowest.span} {summary.slowest.dur_ms} ms
              </span>
            )}
          </div>

          <div className="stage-row">
            {STAGE_ORDER.filter((key) => stageMs[key] > 0).map((key) => {
              const ratio = grandTotal > 0 ? Math.round((stageMs[key] / grandTotal) * 100) : 0;
              return (
                <div className="stage" key={key}>
                  <div className="stage-label">{STAGE_LABEL[key]}</div>
                  <div className="stage-value">
                    {stageMs[key]} ms <span className="muted small">{ratio}%</span>
                  </div>
                  <div className="stage-track">
                    <i style={{ width: `${ratio}%` }} />
                  </div>
                </div>
              );
            })}
          </div>

          <ul className="waterfall">
            {visible.map((span, index) => {
              const offset = new Date(span.start_ts).getTime() - base;
              const left = (offset / total) * 100;
              const width = Math.max(0.5, (span.dur_ms / total) * 100);
              return (
                <li key={`${span.span}-${index}`}>
                  <span className="waterfall-label mono" style={{ paddingLeft: `${depthOf(span) * 12}px` }}>
                    {span.span}
                  </span>
                  <span className="waterfall-track">
                    <span
                      className={`waterfall-bar ${span.status === "error" ? "error" : ""}`}
                      style={{ left: `${left}%`, width: `${width}%` }}
                    />
                  </span>
                  <span className="mono small">{span.dur_ms} ms</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {summary && summary.nodes.filter((node) => node.span !== "http_request").length > 0 && (
        <div className="card">
          <div className="card-title">节点延迟排行（按均值）</div>
          <table className="table">
            <thead>
              <tr>
                <th>节点</th>
                <th>次数</th>
                <th>累计</th>
                <th>均值</th>
                <th>最大</th>
                <th>占比</th>
              </tr>
            </thead>
            <tbody>
              {summary.nodes
                .filter((node) => node.span !== "http_request")
                .map((node) => (
                <tr key={node.span}>
                  <td className="mono">{node.span}</td>
                  <td>{node.count}</td>
                  <td className="mono">{node.total_ms} ms</td>
                  <td className="mono">{node.avg_ms} ms</td>
                  <td className="mono">{node.max_ms} ms</td>
                  <td>
                    <span className="waterfall-track">
                      <span className="waterfall-bar" style={{ left: 0, width: `${(node.avg_ms / slowestAvg) * 100}%` }} />
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {spans && spans.length === 0 && <div className="muted">该 trace 没有 span 记录。</div>}
    </section>
  );
}
