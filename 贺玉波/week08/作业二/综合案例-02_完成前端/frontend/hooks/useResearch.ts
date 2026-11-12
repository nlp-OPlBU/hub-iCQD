"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { ResearchRecord } from "@/lib/types";

const POLL_INTERVAL_MS = 2000;
const MAX_CONSECUTIVE_ERRORS = 3;
const TERMINAL: ResearchRecord["status"][] = ["completed", "failed"];

export interface UseResearchResult {
  record: ResearchRecord | null;
  loading: boolean;
  error: string | null;
  notFound: boolean;
}

export function useResearch(rid: string): UseResearchResult {
  const [record, setRecord] = useState<ResearchRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const errorsRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    // 切换 rid 时重置状态并清空 record，避免新研究的 URL 下短暂显示旧研究内容
    errorsRef.current = 0;
    setError(null);
    setNotFound(false);
    setLoading(true);
    setRecord(null);

    const fetchOnce = async () => {
      try {
        const rec = await api.getResearch(rid);
        if (cancelled) return;
        errorsRef.current = 0;
        setError(null);
        setRecord(rec);
        setLoading(false);
        if (TERMINAL.includes(rec.status) && timer) {
          clearInterval(timer);
          timer = null;
        }
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 404) {
          setNotFound(true);
          setLoading(false);
          if (timer) {
            clearInterval(timer);
            timer = null;
          }
          return;
        }
        errorsRef.current += 1;
        if (errorsRef.current >= MAX_CONSECUTIVE_ERRORS) {
          setError("连续多次获取研究状态失败，请检查后端是否运行");
          setLoading(false);
        }
      }
    };

    fetchOnce();
    timer = setInterval(fetchOnce, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [rid]);

  return { record, loading, error, notFound };
}
