"use client";

import { useState } from "react";
import Link from "next/link";
import { ConfidenceView } from "@/components/ConfidenceView";
import { DraftPreview } from "@/components/DraftPreview";
import { ProgressPanel } from "@/components/ProgressPanel";
import { ReportHtmlView } from "@/components/ReportHtmlView";
import { ReportView } from "@/components/ReportView";
import { SourcesView } from "@/components/SourcesView";
import { StatusBadge } from "@/components/StatusBadge";
import { useResearch } from "@/hooks/useResearch";

type Tab = "report" | "html";

export function ResearchDetail({ id }: { id: string }) {
  const { record, loading, error, notFound } = useResearch(id);
  const [tab, setTab] = useState<Tab>("report");

  if (notFound) {
    return (
      <main className="mx-auto flex w-full max-w-[1440px] flex-col items-center gap-4 px-6 py-20">
        <p className="text-lg font-semibold text-zinc-700">研究不存在</p>
        <Link href="/" className="text-sm text-blue-600 hover:underline">
          返回首页
        </Link>
      </main>
    );
  }

  if (loading && !record) {
    return (
      <main className="mx-auto w-full max-w-[1440px] px-6 py-20 text-center text-sm text-zinc-400">
        加载中…
      </main>
    );
  }

  if (!record) {
    return error ? (
      <main className="mx-auto w-full max-w-[1440px] px-6 py-10">
        <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {error}
        </div>
      </main>
    ) : null;
  }

  const inProgress = record.status === "pending" || record.status === "running";
  const tabs: { key: Tab; label: string }[] = [
    { key: "report", label: "报告" },
    ...(record.report_html ? [{ key: "html" as Tab, label: "HTML 报告" }] : []),
  ];

  return (
    <main className="mx-auto flex w-full max-w-[1440px] flex-col gap-6 px-6 py-8 lg:px-10">
      <header className="flex flex-col gap-2">
        <div className="flex items-center gap-3">
          <h1 className="min-w-0 flex-1 truncate text-xl font-bold text-zinc-900">{record.topic}</h1>
          <StatusBadge status={record.status} />
        </div>
        {inProgress && (
          <p className="text-xs text-zinc-400">研究进行中，关闭页面不影响研究，后端继续执行</p>
        )}
      </header>

      {error && (
        <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {error}
        </div>
      )}

      {record.status === "failed" && record.error && (
        <div role="alert" className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">
          研究失败：{record.error}
        </div>
      )}

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_400px]">
        {/* 左：主内容——running/failed 实时草稿，completed 报告 */}
        <section className="flex min-w-0 flex-col gap-4">
          {(inProgress || record.status === "failed") && <DraftPreview draft={record.draft} />}

          {record.status === "completed" && (
            <>
              <div role="tablist" className="flex gap-2 border-b border-zinc-200">
                {tabs.map((t) => (
                  <button
                    key={t.key}
                    role="tab"
                    aria-selected={tab === t.key}
                    onClick={() => setTab(t.key)}
                    className={`px-3 py-2 text-sm font-medium transition ${
                      tab === t.key
                        ? "border-b-2 border-blue-600 text-blue-600"
                        : "text-zinc-500 hover:text-zinc-800"
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              {tab === "report" && record.report && <ReportView report={record.report} />}
              {tab === "html" && record.report_html && <ReportHtmlView html={record.report_html} />}
            </>
          )}
        </section>

        {/* 右：常驻侧栏——置信度 / 过程时间线 / 来源 */}
        <aside className="flex flex-col gap-4 lg:sticky lg:top-6">
          {record.confidence && <ConfidenceView confidence={record.confidence} />}
          {record.process && (
            <ProgressPanel
              process={record.process}
              draftCount={record.draft.length}
              sourcesCount={record.sources.length}
            />
          )}
          <SourcesView sources={record.sources} />
        </aside>
      </div>
    </main>
  );
}
