import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "深度研究助手",
  description: "输入研究主题，自动检索、迭代、综合，产出带来源引用的研究报告",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">{children}</body>
    </html>
  );
}
