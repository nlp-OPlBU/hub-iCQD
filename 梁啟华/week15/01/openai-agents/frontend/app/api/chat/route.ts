import { NextResponse } from "next/server";

const API_BASE_URL = (process.env.API_BASE_URL ?? "http://127.0.0.1:8000").replace(
  /\/+$/,
  "",
);

type ErrorDetail = string | Array<{ msg?: string }> | undefined;

function extractDetail(payload: unknown): string | null {
  if (!payload || typeof payload !== "object" || !("detail" in payload)) {
    return null;
  }
  const detail = (payload as { detail?: ErrorDetail }).detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail) && typeof detail[0]?.msg === "string") {
    return detail[0].msg;
  }
  return null;
}

export async function POST(request: Request) {
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ detail: "请求体必须是合法的 JSON。" }, { status: 400 });
  }

  const message =
    payload && typeof payload === "object" && "message" in payload
      ? (payload as { message?: unknown }).message
      : undefined;

  if (typeof message !== "string" || !message.trim()) {
    return NextResponse.json({ detail: "消息不能为空。" }, { status: 400 });
  }
  if (message.length > 4000) {
    return NextResponse.json({ detail: "消息不能超过 4000 个字符。" }, { status: 400 });
  }

  try {
    const response = await fetch(`${API_BASE_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: message.trim() }),
      cache: "no-store",
      signal: AbortSignal.timeout(90_000),
    });

    const data: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      return NextResponse.json(
        { detail: extractDetail(data) ?? `后端服务返回错误（${response.status}）。` },
        { status: response.status },
      );
    }

    return NextResponse.json(data);
  } catch {
    return NextResponse.json(
      { detail: "无法连接后端服务，请确认 FastAPI 已启动且 API_BASE_URL 配置正确。" },
      { status: 502 },
    );
  }
}
