import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'

test('real Nginx batch upload, polling and scoped image answer', async ({ page }) => {
  test.skip(!process.env.RAG_SERVER_URL || !process.env.RAG_SERVER_PDF || !process.env.RAG_SERVER_IMAGE,
    'Explicit server/PDF/image opt-in required; creates project documents and a conversation')
  await page.goto('/documents')
  await expect(page.getByRole('button', { name: '上传', exact: true })).toBeVisible()
  const pdf = readFileSync(process.env.RAG_SERVER_PDF!)
  const run = randomUUID().slice(0, 8)
  const firstName = `browser-${run}-a.pdf`, secondName = `browser-${run}-b.pdf`
  await page.getByLabel('选择 PDF 文件').setInputFiles([
    { name: firstName, mimeType: 'application/pdf', buffer: pdf },
    { name: secondName, mimeType: 'application/pdf', buffer: pdf },
  ])
  const accepted = page.waitForResponse(r => r.url().endsWith('/api/documents/batch') && r.request().method() === 'POST')
  await page.getByRole('button', { name: '上传 2 个文件', exact: true }).click()
  const response = await accepted
  expect((await response.json()).accepted).toBe(2)
  const card = page.locator('.document-card').filter({ has: page.getByRole('heading', { name: firstName, exact: true }) })
  await expect(card.getByText('可检索版本 v1', { exact: false })).toBeVisible({ timeout: 180000 })
  const second = page.locator('.document-card').filter({ has: page.getByRole('heading', { name: secondName, exact: true }) })
  await expect(second.getByText('可检索版本 v1', { exact: false })).toBeVisible({ timeout: 180000 })
  await card.getByRole('link', { name: `向 ${firstName} 提问` }).click()
  await expect(page.getByRole('button', { name: '新会话', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '新会话', exact: true }).click()
  await page.getByLabel('问题（可仅上传图片）').fill('图中底部流程中间的方框内写着什么？购物车最多可以添加多少件商品？')
  await page.getByLabel('选择问题图片').setInputFiles(process.env.RAG_SERVER_IMAGE!)
  await expect(page.getByAltText('待发送图片预览')).toBeVisible()
  await page.getByRole('button', { name: '发送问题', exact: true }).click()
  const assistant = page.locator('.chat-message.assistant').last()
  await expect(assistant).toContainText('问答技能', { timeout: 150000 })
  await expect(assistant).toContainText('100')
  await expect(assistant.locator('a.citation').first()).toBeVisible()
  await assistant.locator('a.citation').first().click()
  await expect(assistant).toContainText(firstName)
  await expect(assistant.locator('.markdown img').first()).toBeVisible()
  expect(await assistant.locator('.markdown img').first().evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0)
  await page.screenshot({ path: 'test-results/server-pipeline.png', fullPage: true })
})
