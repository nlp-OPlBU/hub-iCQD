import type { ConfidenceNote } from "@/lib/types";

const LEVEL_LABELS: Record<ConfidenceNote["overall"], string> = {
  high: "高",
  medium: "中",
  low: "低",
};

export function ConfidenceView({ confidence }: { confidence: ConfidenceNote }) {
  return (
    <section className="flex flex-col gap-3 rounded-xl border border-zinc-200 bg-white p-5">
      <div className="flex items-center gap-3">
        <span className="text-sm font-semibold text-zinc-900">置信度</span>
        <span data-level={confidence.overall} className="rounded-full bg-blue-100 px-2.5 py-0.5 text-xs font-medium text-blue-700">
          {LEVEL_LABELS[confidence.overall]}
        </span>
      </div>
      <p className="text-sm text-zinc-600">信息截止时间：{confidence.info_cutoff || "未知"}</p>
      <ul className="flex list-inside list-disc flex-col gap-1">
        {confidence.notes.map((note, i) => (
          <li key={i} className="text-sm text-zinc-600">
            {note}
          </li>
        ))}
      </ul>
    </section>
  );
}
