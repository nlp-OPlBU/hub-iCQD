import type { DraftBlock } from "@/lib/types";

export function DraftPreview({ draft }: { draft: DraftBlock[] }) {
  if (draft.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-zinc-200 bg-white px-5 py-10 text-center text-sm text-zinc-400">
        草稿尚未生成，检索开始后这里会实时显示正文段落
      </p>
    );
  }
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold text-zinc-500">正文草稿（实时累积）</h2>
      {draft.map((b, i) => (
        <article
          key={i}
          className="flex flex-col gap-1 rounded-xl border border-zinc-200 bg-white px-5 py-4"
        >
          <span className="text-xs text-zinc-400">
            第 {b.round} 轮 · {b.keyword}
          </span>
          <p className="text-sm leading-7 text-zinc-700">{b.text}</p>
        </article>
      ))}
    </section>
  );
}
