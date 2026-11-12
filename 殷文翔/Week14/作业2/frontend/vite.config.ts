import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

const projectRoot = fileURLToPath(new URL('..', import.meta.url))

export default defineConfig(({ mode }) => {
  // Only these development values are loaded; browser bundles receive VITE_* only.
  const env = loadEnv(mode, projectRoot, ['VITE_', 'APP_HOST', 'APP_PORT'])
  return {
    plugins: [vue()],
    envDir: projectRoot,
    server: {
      host: '127.0.0.1',
      proxy: {
        '/api': {
          target: env.VITE_DEV_API_TARGET || `http://${env.APP_HOST || '127.0.0.1'}:${env.APP_PORT || '8001'}`,
          changeOrigin: true,
        },
      },
    },
  }
})
