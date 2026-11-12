"use client";

import { StatusBadge } from "@/components/StatusBadge";
import type { ResearchRecord } from "@/lib/types";

function formatTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function ResearchList({
  records,
  onSelect,
}: {
  records: ResearchRecord[];
  onSelect: (rid: string) => void;
}) {
  const sorted = [...records].sort((a, b) => b.created_at.localeCompare(a.created_at));

  if (sorted.length === 0) {
    return <p className="py-8 text-center text-sm text-zinc-400">暂无研究记录，从上方输入主题开始</p>;
  }

  return (
    <ul className="flex flex-col gap-2">
      {sorted.map((rec) => (
        <li key={rec.research_id}>
          <button
            type="button"
            onClick={() => onSelect(rec.research_id)}
            className="flex w-full items-center justify-between gap-3 rounded-lg border border-zinc-200 bg-white px-4 py-3 text-left transition hover:border-blue-300 hover:shadow-sm"
          >
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-zinc-800">
              {rec.topic}
            </span>
            <span className="shrink-0 text-xs text-zinc-400">{formatTime(rec.created_at)}</span>
            <StatusBadge status={rec.status} />
          </button>
        </li>
      ))}
    </ul>
  );
}
