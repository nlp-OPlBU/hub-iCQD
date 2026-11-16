import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import CopyId from "../components/CopyId";
import { api, ChunkItem, DocumentItem } from "../api";
import { KIND_LABEL, STATUS_LABEL, statusClass } from "../labels";

const PAGE_SIZE = 200;

export default function DocumentDetailPage() {
  const { id = "" } = useParams();
  const [doc, setDoc] = useState<DocumentItem | null>(null);
  const [chunks, setChunks] = useState<ChunkItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [detail, rows] = await Promise.all([api.getDocument(id), api.chunks(id, PAGE_SIZE, 0)]);
      setDoc(detail);
      setChunks(rows);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function loadMore() {
    try {
      const rows = await api.chunks(id, PAGE_SIZE, chunks.length);
      setChunks((prev) => [...prev, ...rows]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载更多失败");
    }
  }

  if (loading) return <div className="muted">加载中…</div>;

  return (
    <section>
      <header className="page-head">
        <h1>{doc ? doc.filename : "文档详情"}</h1>
        <p className="muted">
          <Link className="link" to="/documents">
            ← 返回列表
          </Link>
          {doc && (
            <>
              {" · "}
              <span className="mono small">{doc.id}</span> <CopyId value={doc.id} label="复制文档 ID" />
            </>
          )}
        </p>
      </header>

      {error && <div className="alert error">{error}</div>}

      {doc && (
        <div className="card">
          <div className="card-title">
            基本信息
            <span className={`badge ${statusClass(doc.status)}`}>{STATUS_LABEL[doc.status] || doc.status}</span>
            {doc.status !== "done" && doc.status !== "failed" && (
              <span className="muted small">{doc.progress}%</span>
            )}
          </div>
          <dl className="kv">
            <dt>状态</dt>
            <dd>
              {STATUS_LABEL[doc.status] || doc.status}
              {doc.stage ? ` · ${doc.stage}` : ""}
            </dd>
            <dt>规模</dt>
            <dd>
              {doc.page_count ?? "?"} 页 · {doc.chunk_count} 块 / {doc.image_count} 图
            </dd>
            <dt>大小</dt>
            <dd>{(doc.size_bytes / 1024).toFixed(1)} KB · 尝试 {doc.attempts} 次</dd>
            <dt>创建时间</dt>
            <dd className="mono small">{new Date(doc.created_at).toLocaleString()}</dd>
            {doc.error_message && (
              <>
                <dt>错误</dt>
                <dd className="danger-text small">
                  {doc.error_code ? `[${doc.error_code}] ` : ""}
                  {doc.error_message}
                </dd>
              </>
            )}
          </dl>
          {doc.status === "failed" && (
            <button
              className="button ghost"
              onClick={async () => {
                await api.retryDocument(doc.id);
                void load();
              }}
            >
              重投
            </button>
          )}
        </div>
      )}

      <div className="card">
        <div className="card-title">
          分块 {chunks.length}
          <span className="muted small">chunk_id 可复制，用于标注 docs/eval/golden.jsonl</span>
        </div>
        {chunks.length === 0 && <div className="muted">还没有分块，等文档状态变成「已完成」。</div>}
        {chunks.map((chunk) => (
          <div className="hit" key={chunk.id}>
            <div className="hit-head">
              <b>#{chunk.idx}</b>
              <span className="badge neutral">{KIND_LABEL[chunk.kind] || chunk.kind}</span>
              <span className="chip">第 {chunk.page_no ?? "?"} 页</span>
              <span className="mono small">{chunk.id}</span>
              <CopyId value={chunk.id} />
            </div>
            {chunk.text && <div className="hit-text">{chunk.text}</div>}
            {chunk.image_path && (
              <a href={chunk.image_path} target="_blank" rel="noreferrer">
                <img className="hit-img" src={chunk.image_path} alt={`第 ${chunk.page_no ?? "?"} 页图片`} />
              </a>
            )}
          </div>
        ))}
        {chunks.length > 0 && chunks.length % PAGE_SIZE === 0 && (
          <button className="button ghost" style={{ marginTop: 12 }} onClick={() => void loadMore()}>
            加载更多
          </button>
        )}
      </div>
    </section>
  );
}
