import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 本地开发时把 /api 代理到远端 8080，避免 CORS；
// 交付前 `npm run build`，把 dist 同步到服务器由 FastAPI 直接托管。
// 用 loadEnv 读取 VITE_API_TARGET，避免依赖 @types/node 的 process 声明。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const target = env.VITE_API_TARGET || "http://127.0.0.1:8080";

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": { target, changeOrigin: true },
        "/static": { target, changeOrigin: true },
        "/metrics": { target, changeOrigin: true },
      },
    },
    build: {
      outDir: "dist",
      sourcemap: false,
    },
  };
});
