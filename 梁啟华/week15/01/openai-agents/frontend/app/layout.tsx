import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "心语 · OpenAI Agents 情感支持助手",
  description:
    "一个使用 OpenAI Agents SDK 串联情感识别与回应生成的两阶段情感支持应用，由 DeepSeek 提供模型能力。",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
