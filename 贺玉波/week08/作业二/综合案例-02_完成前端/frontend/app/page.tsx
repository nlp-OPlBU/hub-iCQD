"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ResearchForm } from "@/components/ResearchForm";
import { ResearchList } from "@/components/ResearchList";
import { api } from "@/lib/api";
import type { ResearchRecord } from "@/lib/types";

export default function HomePage() {
  const router = useRouter();
  const [backendUp, setBackendUp] = useState<boolean | null>(null); // null = 探测中
  const [records, setRecords] = useState<ResearchRecord[]>([]);

  const loadList = useCallback(async () => {
    try {
      setRecords(await api.listResearch());
    } catch {
      // 列表加载失败不阻塞页面
    }
  }, []);

  const checkHealth = useCallback(async () => {
    setBackendUp(null);
    try {
      await api.health();
      setBackendUp(true);
      // 后端恢复后顺手刷新历史列表（重试按钮的期望行为）
      await loadList();
    } catch {
      setBackendUp(false);
    }
  }, [loadList]);

  useEffect(() => {
    checkHealth();
    loadList();
  }, [checkHealth, loadList]);

  const handleCreated = (rid: string) => router.push(`/research/${rid}`);
  const handleSelect = (rid: string) => router.push(`/research/${rid}`);

  return (
    <main className="mx-auto flex w-full max-w-[1440px] flex-col gap-8 px-6 py-10 lg:px-10">
      <section className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-zinc-900">深度研究助手</h1>
        <p className="text-sm text-zinc-500">输入研究主题，自动检索、迭代、综合，产出带来源引用的研究报告</p>
      </section>

      {backendUp === false && (
        <div role="alert" className="flex items-center justify-between gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <span>后端未启动，请运行 conda activate study &amp;&amp; uvicorn backend.app:app --port 8000</span>
          <button type="button" onClick={checkHealth} className="shrink-0 rounded-md border border-amber-400 px-3 py-1 text-xs hover:bg-amber-100">
            重试
          </button>
        </div>
      )}
      {backendUp === true && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          后端已连接
        </div>
      )}

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[400px_minmax(0,1fr)]">
        <section className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm lg:sticky lg:top-6">
          <ResearchForm onCreated={handleCreated} />
        </section>

        <section className="flex min-w-0 flex-col gap-3">
          <h2 className="text-sm font-semibold text-zinc-500">历史研究</h2>
          <ResearchList records={records} onSelect={handleSelect} />
        </section>
      </div>
    </main>
  );
}
