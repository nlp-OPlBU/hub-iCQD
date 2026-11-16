import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";

type UploadItem = {
  key: string;
  name: string;
  state: "uploading" | "done" | "failed";
  percent: number;
  detail: string;
};

const PDF_ONLY = /\.pdf$/i;
// 与后端 STORAGE__MAX_UPLOAD_MB 一致，提前拦掉，省一次白传
const MAX_BYTES = 200 * 1024 * 1024;
// 最多 3 个并发：兼顾传大文件的吞吐，又不至于把后端和带宽打满
const CONCURRENCY = 3;

export default function UploadPage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<UploadItem[]>([]);

  async function uploadOne(file: File, key: string) {
    try {
      const result = await api.uploadWithProgress(file, (percent) => {
        setItems((prev) =>
          prev.map((item) =>
            item.key === key && item.state === "uploading"
              ? { ...item, percent, detail: `上传中… ${percent}%` }
              : item,
          ),
        );
      });
      setItems((prev) =>
        prev.map((item) =>
          item.key === key
            ? {
                ...item,
                state: "done",
                percent: 100,
                detail: result.created
                  ? `已入队（${result.document.id}）`
                  : `已存在，复用（${result.document.id}）`,
              }
            : item,
        ),
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : "上传失败";
      setItems((prev) =>
        prev.map((item) => (item.key === key ? { ...item, state: "failed", detail: message } : item)),
      );
    }
  }

  async function uploadMany(fileList: FileList | null) {
    const files = Array.from(fileList ?? []);
    if (!files.length) return;
    const pdfs = files.filter((file) => PDF_ONLY.test(file.name));
    const rejected = files.filter((file) => !PDF_ONLY.test(file.name));
    const oversized = pdfs.filter((file) => file.size > MAX_BYTES);
    const accepted = pdfs.filter((file) => file.size <= MAX_BYTES);

    const notices: string[] = [];
    if (rejected.length) {
      notices.push(`已跳过非 PDF 文件：${rejected.map((file) => file.name).join("、")}`);
    }
    if (oversized.length) {
      notices.push(`已跳过超过 200MB 的文件：${oversized.map((file) => file.name).join("、")}`);
    }
    if (!accepted.length) {
      setError(notices.join("；") || null);
      return;
    }

    const base = Date.now();
    const queue = accepted.map((file, index) => ({ file, key: `${base}-${index}` }));
    setError(notices.length ? notices.join("；") : null);
    setBusy(true);
    setItems(
      queue.map(({ file, key }) => ({
        key,
        name: file.name,
        state: "uploading",
        percent: 0,
        detail: "上传中…",
      })),
    );

    // 固定 3 条车道，每条车道依次取下一个任务；认领和自增之间没有 await，不会重复取
    let cursor = 0;
    const lanes = Math.min(CONCURRENCY, queue.length);
    await Promise.all(
      Array.from({ length: lanes }, async () => {
        while (cursor < queue.length) {
          const current = queue[cursor];
          cursor += 1;
          await uploadOne(current.file, current.key);
        }
      }),
    );
    setBusy(false);
  }

  const doneCount = items.filter((item) => item.state === "done").length;

  return (
    <section>
      <header className="page-head">
        <h1>上传文档</h1>
        <p className="muted">
          上传后立即返回，解析与向量化在后台异步完成（Kafka → worker → MinerU → 向量库）。
        </p>
      </header>

      <div
        className={`dropzone ${dragging ? "dragging" : ""} ${busy ? "busy" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          void uploadMany(event.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
      >
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          multiple
          hidden
          onChange={(event) => {
            void uploadMany(event.target.files);
            event.target.value = ""; // 允许再次选择同一批文件
          }}
        />
        <div className="dropzone-title">{busy ? "上传中…" : "拖拽一个或多个 PDF 到这里，或点击选择"}</div>
        <div className="muted small">
          支持批量选择，最多 3 个并发上传；单文件 ≤ 200MB、≤ 1000 页；同一文件重复上传按 sha256 去重
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}

      {items.length > 0 && (
        <div className="card">
          <div className="card-title">
            本次上传成功 {doneCount}/{items.length}
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>文件</th>
                <th style={{ width: 200 }}>传输进度</th>
                <th>结果</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.key}>
                  <td className="mono">{item.name}</td>
                  <td>
                    <div className="progress" role="progressbar" aria-valuenow={item.percent}>
                      <div
                        className={`progress-fill ${
                          item.state === "failed" ? "failed" : item.state === "done" ? "done" : ""
                        }`}
                        style={{ width: `${item.percent}%` }}
                      />
                    </div>
                    <span className="muted small">{item.percent}%</span>
                  </td>
                  <td>
                    <span
                      className={`badge ${
                        item.state === "done" ? "ok" : item.state === "failed" ? "danger" : "info"
                      }`}
                    >
                      {item.state === "done" ? "已提交" : item.state === "failed" ? "失败" : "上传中"}
                    </span>
                    <span className="muted small"> {item.detail}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {doneCount > 0 && (
            <Link className="link" to="/documents">
              去文档列表看解析进度 →
            </Link>
          )}
        </div>
      )}
    </section>
  );
}
