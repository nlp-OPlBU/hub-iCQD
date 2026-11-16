import type { Status } from "@/lib/types";

const LABELS: Record<Status, string> = {
  pending: "排队中",
  running: "研究中",
  completed: "已完成",
  failed: "失败",
};

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span
      data-status={status}
      className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium"
    >
      {LABELS[status]}
    </span>
  );
}
