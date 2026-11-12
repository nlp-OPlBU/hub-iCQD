import type { ProcessStep, ResearchProcess } from "@/lib/types";

function stepText(step: ProcessStep): string {
  const d = step.detail;
  switch (step.type) {
    case "plan": {
      const keywords = Array.isArray(d.keywords) ? (d.keywords as string[]) : [];
      return `规划：${keywords.join("、") || "（无关键词）"}`;
    }
    case "search":
      return `检索「${d.keyword ?? ""}」→ ${d.results ?? 0} 条结果`;
    case "summarize":
      return `总结「${d.keyword ?? ""}」→ ${d.chars ?? 0} 字正文`;
    case "judge": {
      const newKws = Array.isArray(d.new_keywords) ? (d.new_keywords as string[]) : [];
      const tail = newKws.length ? `，补检：${newKws.join("、")}` : "";
      return `判断：${d.sufficient ? "信息足够" : "需要补检"}${d.reason ? `（${d.reason}）` : ""}${tail}`;
    }
    default:
      return step.type;
  }
}

export function ProgressPanel({
  process,
  draftCount,
  sourcesCount,
}: {
  process: ResearchProcess;
  draftCount: number;
  sourcesCount: number;
}) {
  return (
    <section className="flex flex-col gap-4 rounded-xl border border-zinc-200 bg-white p-5">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
        <span className="font-semibold text-zinc-900">第 {process.iterations} 轮</span>
        <span className="text-zinc-500">{process.search_queries.length} 个关键词</span>
        <span className="text-zinc-500">{draftCount} 段正文</span>
        <span className="text-zinc-500">{sourcesCount} 个来源</span>
      </div>
      <ol className="flex flex-col gap-2 border-l-2 border-zinc-100 pl-4">
        {process.steps.length === 0 && <li className="text-sm text-zinc-400">等待第一步…</li>}
        {process.steps.map((step, i) => (
          <li key={i} className="text-sm text-zinc-600">
            {stepText(step)}
          </li>
        ))}
      </ol>
    </section>
  );
}
