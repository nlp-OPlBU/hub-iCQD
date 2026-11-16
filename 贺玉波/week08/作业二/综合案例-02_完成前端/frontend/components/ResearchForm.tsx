"use client";

import { useState } from "react";
import { api } from "@/lib/api";

export function ResearchForm({ onCreated }: { onCreated: (rid: string) => void }) {
  const [topic, setTopic] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = topic.trim().length > 0 && !submitting;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.startResearch(topic.trim());
      onCreated(res.research_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败，请稍后重试");
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      <label className="text-sm font-medium text-zinc-700" htmlFor="topic-input">
        研究主题
      </label>
      <input
        id="topic-input"
        type="text"
        value={topic}
        onChange={(e) => setTopic(e.target.value)}
        placeholder="请输入研究主题，例如：2026 年主流 Agent 框架对比"
        className="rounded-lg border border-zinc-300 px-4 py-2.5 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
      />
      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={!canSubmit}
        className="rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {submitting ? "提交中…" : "开始研究"}
      </button>
    </form>
  );
}
