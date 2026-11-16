import { useState } from "react";

/** 可复制 ID 的小按钮：评测标注、排障时都要拷 chunk_id / trace_id。 */
export default function CopyId({ value, label = "复制 ID" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      // 非 https 或浏览器拒绝剪贴板权限时静默失败，用户可以手动选中
    }
  }

  return (
    <button className="copy-id" type="button" title={`复制 ${value}`} onClick={() => void copy()}>
      {copied ? "已复制" : label}
    </button>
  );
}
