import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, DocumentItem } from "../api";
import { STATUS_LABEL, statusClass } from "../labels";

const TERMINAL = new Set(["done", "failed"]);

export default function DocumentsPage() {
  const [items, setItems] = useState<DocumentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const result = await api.listDocuments();
      setItems(result.items);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 有未完成的文档时轮询，全部收尾后自动停止
  useEffect(() => {
    if (items.length > 0 && items.every((item) => TERMINAL.has(item.status))) return;
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [items, load]);

  async function retry(id: string) {
    try {
      await api.retryDocument(id);
      void load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "重投失败");
    }
  }

  return (
    <section>
      <header className="page-head">
        <h1>文档列表</h1>
        <p className="muted">状态由状态机驱动，只允许合法迁移；失败的文档可以手动重投。</p>
      </header>

      {error && <div className="alert error">{error}</div>}

      <div className="card">
        {loading ? (
          <div className="muted">加载中…</div>
        ) : items.length === 0 ? (
          <div className="muted">还没有文档，先去「上传文档」传一份 PDF。</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>文件名</th>
                <th>状态</th>
                <th>块 / 图</th>
                <th>尝试</th>
                <th>创建时间</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <Link className="ellipsis link" title={item.filename} to={`/documents/${item.id}`}>
                      {item.filename}
                    </Link>
                    <div className="mono small muted">{item.id.slice(0, 12)}</div>
                  </td>
                  <td>
                    <span className={`badge ${statusClass(item.status)}`}>
                      {STATUS_LABEL[item.status] || item.status}
                    </span>
                    {item.status !== "done" && item.status !== "failed" && (
                      <div className="progress">
                        <span style={{ width: `${Math.max(4, Math.min(100, item.progress))}%` }} />
                      </div>
                    )}
                    {item.error_message && <div className="small danger-text ellipsis">{item.error_message}</div>}
                  </td>
                  <td className="mono">
                    {item.chunk_count} / {item.image_count}
                  </td>
                  <td className="mono">{item.attempts}</td>
                  <td className="mono small">{new Date(item.created_at).toLocaleString()}</td>
                  <td>
                    {item.status === "failed" && (
                      <button className="button ghost" onClick={() => void retry(item.id)}>
                        重投
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
