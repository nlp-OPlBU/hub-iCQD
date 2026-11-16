import { useState } from "react";

import CopyId from "../components/CopyId";
import { api, Hit } from "../api";
import { KIND_LABEL } from "../labels";

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(8);
  const [withImages, setWithImages] = useState(true);
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [tookMs, setTookMs] = useState(0);
  const [stages, setStages] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    const text = query.trim();
    if (!text || loading) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api.search(text, topK, withImages);
      setHits(result.hits);
      setTookMs(result.took_ms);
      setStages(result.stages || {});
    } catch (err) {
      setHits(null);
      setStages({});
      setError(err instanceof Error ? err.message : "检索失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section>
      <header className="page-head">
        <h1>检索调试</h1>
        <p className="muted">
          三路召回（文本向量 1024d + 图片向量 512d + BM25 词法）先经 RRF 融合，再用交叉编码器逐条重排序，
          低于分数门控的噪声会被直接丢弃；这里能看到每一层的结果与耗时。
        </p>
      </header>

      <div className="form-row">
        <input
          className="input grow"
          value={query}
          placeholder="输入检索词，例如「多模态检索是怎么做的」"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void run();
          }}
        />
        <label className="switch">
          top_k
          <input
            className="input"
            type="number"
            min={1}
            max={50}
            style={{ width: 72 }}
            value={topK}
            onChange={(event) => setTopK(Math.max(1, Math.min(50, Number(event.target.value) || 1)))}
          />
        </label>
        <label className="switch">
          <input type="checkbox" checked={withImages} onChange={(event) => setWithImages(event.target.checked)} />
          含图片
        </label>
        <button className="button primary" disabled={loading || !query.trim()} onClick={() => void run()}>
          {loading ? "检索中…" : "检索"}
        </button>
      </div>

      {error && <div className="alert error">{error}</div>}

      {hits && (
        <div className="card">
          <div className="card-title">
            命中 {hits.length} 条
            <span className="chip">总耗时 {tookMs} ms</span>
          </div>

          <div className="stage-row">
            {[
              ["向量化", stages.embed_ms],
              ["双路召回", stages.knn_ms],
              ["词法召回", stages.bm25_ms],
              ["RRF 融合", stages.fusion_ms],
              ["重排序", stages.rerank_ms],
            ].map(([label, ms]) => {
              const value = Number(ms) || 0;
              const ratio = tookMs > 0 ? Math.round((value / tookMs) * 100) : 0;
              return (
                <div className="stage" key={String(label)}>
                  <div className="stage-label">{label}</div>
                  <div className="stage-value">
                    {value} ms <span className="muted small">{ratio}%</span>
                  </div>
                  <div className="stage-track">
                    <i style={{ width: `${ratio}%` }} />
                  </div>
                </div>
              );
            })}
          </div>

          {hits.length === 0 && <div className="muted">没有命中，换一个问法或确认文档已经 done。</div>}
          {hits.map((hit, index) => (
            <div className="hit" key={hit.chunk_id}>
              <div className="hit-head">
                <b>#{index + 1}</b>
                <span className="badge info">{KIND_LABEL[hit.kind] || hit.kind}</span>
                <span className="chip">score {hit.score.toFixed(4)}</span>
                {typeof hit.rerank_score === "number" && (
                  <span className="chip">重排 {hit.rerank_score.toFixed(4)}</span>
                )}
                {typeof hit.rrf_score === "number" && <span className="chip">rrf {hit.rrf_score.toFixed(4)}</span>}
                <span className="chip">{hit.doc_title || hit.doc_id.slice(0, 8)}</span>
                <span className="chip">第 {hit.page_no || "?"} 页</span>
                {hit.raw_scores
                  ? Object.entries(hit.raw_scores).map(([channel, value]) => (
                      <span className="chip" key={channel} title="各通道原始分，量纲不可比">
                        {channel} {Number(value).toFixed(3)}
                      </span>
                    ))
                  : hit.sources.map((source) => (
                      <span className="chip" key={source}>
                        {source}
                      </span>
                    ))}
                <span className="mono small">{hit.chunk_id}</span>
                <CopyId value={hit.chunk_id} />
              </div>
              {hit.text && <div className="hit-text">{hit.text}</div>}
              {hit.image_path && (
                <a href={hit.image_path} target="_blank" rel="noreferrer">
                  <img className="hit-img" src={hit.image_path} alt="命中图片" />
                </a>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
