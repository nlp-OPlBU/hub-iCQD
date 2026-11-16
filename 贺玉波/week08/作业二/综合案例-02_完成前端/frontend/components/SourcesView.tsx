import type { Source } from "@/lib/types";

export function SourcesView({ sources }: { sources: Source[] }) {
  if (sources.length === 0) {
    return <p className="py-8 text-center text-sm text-zinc-400">暂无来源</p>;
  }
  return (
    <ul className="flex flex-col gap-2">
      {sources.map((s) => {
        const isSafe = /^https?:\/\//i.test(s.url);
        return (
        <li key={s.url} className="flex flex-col gap-1 rounded-lg border border-zinc-200 bg-white px-4 py-3">
          {isSafe ? (
            <a
              href={s.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm font-medium text-blue-600 hover:underline"
            >
              {s.title || s.url}
            </a>
          ) : (
            <span className="text-sm font-medium text-zinc-700">
              {s.title || s.url}
            </span>
          )}
          <span className="truncate text-xs text-zinc-400">{s.url}</span>
          <span className="flex items-center gap-2 text-xs text-zinc-500">
            {s.site_name && <span>{s.site_name}</span>}
            {s.accessed_at && <span>访问于 {s.accessed_at}</span>}
          </span>
        </li>
        );
      })}
    </ul>
  );
}
