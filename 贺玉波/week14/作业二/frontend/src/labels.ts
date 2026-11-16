/** 前端展示用的枚举文案：后端返回英文枚举，集中在这里翻译。 */

export const STATUS_LABEL: Record<string, string> = {
  pending: "待解析",
  parsing: "解析中",
  chunking: "切块中",
  indexing: "向量化中",
  done: "已完成",
  failed: "失败",
};

export const KIND_LABEL: Record<string, string> = {
  text: "文本块",
  image: "图片块",
};

export function statusClass(status: string): string {
  if (status === "failed") return "danger";
  if (status === "done") return "ok";
  return "info";
}
