import { useCallback, useEffect, useState } from "react";

import { api, type DocumentItem } from "../api";

interface Sample {
  name: string;
  type: string;
  labels: Record<string, string>;
  value: number;
}

interface Bucket {
  le: number;
  count: number;
}

interface HistRow {
  base: string;
  labels: Record<string, string>;
  count: number;
  sum: number;
  buckets: Bucket[];
}

interface StageRow {
  span: string;
  label: string;
  count: number;
  avgMs: number | null;
  p95Ms: number | null;
}

interface StageGroup {
  group: string;
  rows: StageRow[];
}

const TYPE_RE = /^#\s*TYPE\s+(\S+)\s+(\S+)/;
const SAMPLE_RE = /^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)$/;
const LABEL_RE = /([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"/g;

const HIST_SUFFIXES = ["_sum", "_count", "_bucket"] as const;

/** 业务阶段分组：只列叶子 span（父 span 会包含子阶段，重复计入会虚高）。 */
const STAGE_GROUPS: Array<{ group: string; stages: Array<{ span: string; label: string }> }> = [
  {
    group: "文档解析流程",
    stages: [
      { span: "ingest.parse", label: "MinerU 解析" },
      { span: "ingest.chunk", label: "切片与图片抽取" },
      { span: "ingest.index", label: "向量化入库" },
    ],
  },
  {
    group: "检索流程",
    stages: [
      { span: "search.embed", label: "查询向量化" },
      { span: "search.knn", label: "向量召回（文本+图片）" },
      { span: "search.bm25", label: "词法召回（BM25）" },
      { span: "search.rerank", label: "交叉编码器重排" },
      { span: "qa.retrieve", label: "检索合计（含以上阶段）" },
    ],
  },
  {
    group: "生成流程",
    stages: [
      { span: "qa.generate", label: "大模型回答" },
      { span: "chat.summarize", label: "会话标题摘要" },
    ],
  },
];

const IN_FLIGHT = new Set(["pending", "parsing", "chunking", "indexing"]);

function parse(text: string): Sample[] {
  const types: Record<string, string> = {};
  const samples: Sample[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith("#")) {
      const declared = TYPE_RE.exec(line);
      if (declared) types[declared[1]] = declared[2];
      continue;
    }
    const match = SAMPLE_RE.exec(line);
    if (!match) continue;
    const labels: Record<string, string> = {};
    if (match[2]) {
      LABEL_RE.lastIndex = 0;
      for (let item = LABEL_RE.exec(match[2]); item; item = LABEL_RE.exec(match[2])) {
        labels[item[1]] = item[2].replace(/\\(.)/g, "$1");
      }
    }
    samples.push({ name: match[1], type: types[match[1]] ?? "", labels, value: Number(match[3]) });
  }
  return samples;
}

function labelText(labels: Record<string, string>): string {
  return Object.entries(labels)
    .map(([key, value]) => `${key}=${value}`)
    .join(" · ");
}

function buildHistograms(samples: Sample[]): HistRow[] {
  const rows = new Map<string, HistRow>();
  for (const sample of samples) {
    if (sample.type !== "histogram") continue;
    let base = sample.name;
    let suffix: string = "";
    for (const candidate of HIST_SUFFIXES) {
      if (sample.name.endsWith(candidate)) {
        base = sample.name.slice(0, -candidate.length);
        suffix = candidate;
        break;
      }
    }
    if (!suffix) continue;
    // bucket 的 le 是标签维度，不参与序列分组
    const { le, ...labels } = sample.labels;
    const key = `${base}|${JSON.stringify(labels)}`;
    const row = rows.get(key) ?? { base, labels, count: 0, sum: 0, buckets: [] };
    if (suffix === "_sum") row.sum = sample.value;
    else if (suffix === "_count") row.count = sample.value;
    else if (le !== undefined) {
      const bound = le === "+Inf" ? Number.POSITIVE_INFINITY : Number(le);
      if (!Number.isNaN(bound)) row.buckets.push({ le: bound, count: sample.value });
    }
    rows.set(key, row);
  }
  return [...rows.values()];
}

/** 由 Prometheus 的累积桶估算分位值，返回桶上界（P95 是近似值）。 */
function quantile(row: HistRow, q: number): number | null {
  if (row.count <= 0) return null;
  const buckets = row.buckets
    .filter((bucket) => Number.isFinite(bucket.le))
    .sort((a, b) => a.le - b.le);
  if (buckets.length === 0) return null;
  const target = row.count * q;
  for (const bucket of buckets) {
    if (bucket.count >= target) return bucket.le;
  }
  return buckets[buckets.length - 1].le;
}

function mergeRows(rows: HistRow[]): { count: number; avg: number | null; p95: number | null } {
  const count = rows.reduce((sum, row) => sum + row.count, 0);
  const total = rows.reduce((sum, row) => sum + row.sum, 0);
  // 分位值取样本量最大的一条序列，避免把多个桶直接相加
  const dominant = rows.reduce<HistRow | null>(
    (best, row) => (best === null || row.count > best.count ? row : best),
    null,
  );
  return {
    count,
    avg: count > 0 ? total / count : null,
    p95: dominant ? quantile(dominant, 0.95) : null,
  };
}

function formatMs(value: number | null, approximate = false): string {
  if (value === null) return "—";
  const prefix = approximate ? "≈" : "";
  if (value >= 1000) return `${prefix}${(value / 1000).toFixed(1)} s`;
  return `${prefix}${value.toFixed(value < 100 ? 1 : 0)} ms`;
}

export default function MetricsPage() {
  const [samples, setSamples] = useState<Sample[]>([]);
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [docTotal, setDocTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [updatedAt, setUpdatedAt] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const [metricsResult, docsResult] = await Promise.allSettled([
      api.metricsText().then(parse),
      api.listDocuments(100),
    ]);
    if (metricsResult.status === "fulfilled") {
      setSamples(metricsResult.value);
    } else {
      setError(
        metricsResult.reason instanceof Error ? metricsResult.reason.message : "读取指标失败",
      );
    }
    // 文档统计来自数据库，即使 /metrics 挂了也应展示
    if (docsResult.status === "fulfilled") {
      setDocs(docsResult.value.items);
      setDocTotal(docsResult.value.total);
    } else {
      setDocs([]);
      setDocTotal(0);
    }
    setUpdatedAt(new Date().toLocaleTimeString());
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const histograms = buildHistograms(samples);
  const counters = samples.filter((sample) => sample.type === "counter");
  const gauges = samples.filter((sample) => sample.type === "gauge");

  const spanRows = (span: string): HistRow[] =>
    histograms.filter(
      (row) => row.base === "rag_span_duration_ms" && row.labels.span === span && row.labels.status === "ok",
    );

  const stageGroups: StageGroup[] = STAGE_GROUPS.map((group) => ({
    group: group.group,
    rows: group.stages.map((stage) => {
      const merged = mergeRows(spanRows(stage.span));
      return {
        span: stage.span,
        label: stage.label,
        count: merged.count,
        avgMs: merged.avg,
        p95Ms: merged.p95,
      };
    }),
  }));

  const doneCount = docs.filter((doc) => doc.status === "done").length;
  const failedCount = docs.filter((doc) => doc.status === "failed").length;
  const runningCount = docs.filter((doc) => IN_FLIGHT.has(doc.status)).length;
  const chunkTotal = docs.reduce((sum, doc) => sum + doc.chunk_count, 0);
  const imageTotal = docs.reduce((sum, doc) => sum + doc.image_count, 0);

  const kafkaSamples = gauges.filter((sample) => sample.name === "rag_kafka_consumer_lag");
  const kafkaLag =
    kafkaSamples.length === 0
      ? null
      : kafkaSamples.reduce((sum, sample) => sum + sample.value, 0);

  const retrieve = mergeRows(spanRows("qa.retrieve"));
  const generate = mergeRows(spanRows("qa.generate"));
  const chatEndToEnd = mergeRows(
    histograms.filter((row) => row.base === "rag_http_duration_ms" && row.labels.path === "/api/v1/chat"),
  );
  const serverErrors = counters
    .filter(
      (sample) =>
        sample.name === "rag_http_requests_total" && Number(sample.labels.status) >= 500,
    )
    .reduce((sum, sample) => sum + sample.value, 0);
  const hasTraffic = samples.length > 0;

  return (
    <section>
      <header className="page-head">
        <h1>指标监控</h1>
        <p className="muted">
          知识库统计读数据库（持久）；阶段耗时与队列来自 api 与 ingest-worker 两个进程的内存指标，
          经 Redis 合并展示，任一进程重启都会清零。显示「—」表示该阶段当前进程内还没有样本。
        </p>
      </header>

      <div className="form-row">
        <button className="button primary" disabled={loading} onClick={() => void load()}>
          {loading ? "刷新中…" : "刷新"}
        </button>
        {updatedAt && <span className="muted small">更新于 {updatedAt}</span>}
        <a className="muted small" href="/metrics" target="_blank" rel="noreferrer">
          查看原始文本 →
        </a>
      </div>

      {error && <div className="alert error">{error}</div>}

      <div className="card">
        <div className="card-title">
          知识库
          <span className="chip">共 {docTotal} 个文档</span>
        </div>
        <div className="metric-summary">
          <div>
            <div className="muted small">已入库</div>
            <div className="num mono">{doneCount}</div>
          </div>
          <div>
            <div className="muted small">处理中</div>
            <div className="num mono">{runningCount}</div>
          </div>
          <div>
            <div className="muted small">失败</div>
            <div className="num mono">{failedCount}</div>
          </div>
          <div>
            <div className="muted small">切片总数</div>
            <div className="num mono">{chunkTotal}</div>
          </div>
          <div>
            <div className="muted small">图片总数</div>
            <div className="num mono">{imageTotal}</div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-title">
          耗时与队列
          <span className="chip">api + worker 进程内 histogram</span>
        </div>
        <div className="metric-summary">
          <div>
            <div className="muted small">检索平均</div>
            <div className="num mono">{formatMs(retrieve.avg)}</div>
          </div>
          <div>
            <div className="muted small">检索 P95</div>
            <div className="num mono">{formatMs(retrieve.p95, true)}</div>
          </div>
          <div>
            <div className="muted small">大模型回答平均</div>
            <div className="num mono">{formatMs(generate.avg)}</div>
          </div>
          <div>
            <div className="muted small">问答端到端平均</div>
            <div className="num mono">{formatMs(chatEndToEnd.avg)}</div>
          </div>
          <div>
            <div className="muted small">Kafka 待消费</div>
            <div className="num mono">{kafkaLag === null ? "—" : kafkaLag}</div>
          </div>
          <div>
            <div className="muted small">HTTP 5xx</div>
            <div className={`num mono${serverErrors > 0 ? " danger-text" : ""}`}>{serverErrors}</div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-title">
          阶段耗时
          <span className="chip">按业务链路分组 · 单位自动换算</span>
        </div>
        {!hasTraffic ? (
          <div className="muted">暂无数据，先上传一个文档或在问答页提一个问题。</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>阶段</th>
                <th>次数</th>
                <th>平均</th>
                <th>P95</th>
              </tr>
            </thead>
            {stageGroups.map((group) => (
              <tbody key={group.group}>
                <tr>
                  <th colSpan={4} className="muted small">
                    {group.group}
                  </th>
                </tr>
                {group.rows.map((row) => (
                  <tr key={row.span}>
                    <td>
                      {row.label}
                      <span className="mono small muted"> · {row.span}</span>
                    </td>
                    <td
                      className={`mono${row.count === 0 ? " muted" : ""}`}
                      title={
                        row.count === 0
                          ? "当前进程内还没有该阶段的样本，触发一次对应操作后再刷新"
                          : undefined
                      }
                    >
                      {row.count === 0 ? "—" : row.count}
                    </td>
                    <td className="mono">{formatMs(row.avgMs)}</td>
                    <td className="mono">{formatMs(row.p95Ms, true)}</td>
                  </tr>
                ))}
              </tbody>
            ))}
          </table>
        )}
      </div>

      <div className="card">
        <details>
          <summary className="card-title" style={{ cursor: "pointer", marginBottom: 0 }}>
            原始 /metrics 指标（排查用）
          </summary>
          <div className="muted small" style={{ margin: "8px 0 12px" }}>
            共 {samples.length} 条序列（已合并 ingest-worker 经 Redis 上报的快照）。
            业务页看不到的指标在这里查，包括 HTTP 路由级流量与延迟。
          </div>

          <div className="card-title">耗时分布（histogram）</div>
          {histograms.length === 0 ? (
            <div className="muted">暂无数据。</div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>指标</th>
                  <th>标签</th>
                  <th>次数</th>
                  <th>平均</th>
                  <th>总计</th>
                </tr>
              </thead>
              <tbody>
                {[...histograms]
                  .sort((a, b) => b.sum - a.sum)
                  .map((row) => (
                    <tr key={`${row.base}-${labelText(row.labels)}`}>
                      <td className="mono">{row.base}</td>
                      <td className="mono small muted">{labelText(row.labels)}</td>
                      <td className="mono">{row.count}</td>
                      <td className="mono">
                        {row.count > 0 ? `${(row.sum / row.count).toFixed(1)} ms` : "—"}
                      </td>
                      <td className="mono">{row.sum.toFixed(0)} ms</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          )}

          <div className="card-title" style={{ marginTop: 16 }}>
            计数器与瞬时值
          </div>
          {counters.length + gauges.length === 0 ? (
            <div className="muted">暂无数据。</div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>类型</th>
                  <th>指标</th>
                  <th>标签</th>
                  <th>值</th>
                </tr>
              </thead>
              <tbody>
                {[...gauges.map((sample) => ({ sample, type: "gauge" })),
                 ...counters.map((sample) => ({ sample, type: "counter" }))].map(({ sample, type }) => (
                  <tr key={`${type}-${sample.name}-${labelText(sample.labels)}`}>
                    <td className="muted small">{type}</td>
                    <td className="mono">{sample.name}</td>
                    <td className="mono small muted">{labelText(sample.labels)}</td>
                    <td className="mono">{sample.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </details>
      </div>
    </section>
  );
}
