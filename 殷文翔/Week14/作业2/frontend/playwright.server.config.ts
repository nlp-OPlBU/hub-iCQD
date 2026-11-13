import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './server-tests',
  timeout: 240000,
  workers: 1,
  use: { baseURL: process.env.RAG_SERVER_URL, headless: true },
})
