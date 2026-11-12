import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发环境把 /api、/static 代理到后端，避免跨域；
// 如果后端不在本机，设置 VITE_API_BASE_URL 指向实际地址即可。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/static': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
