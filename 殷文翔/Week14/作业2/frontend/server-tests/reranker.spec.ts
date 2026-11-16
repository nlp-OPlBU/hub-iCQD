import { expect, test } from '@playwright/test'

test('real reranked image question, sources and insufficient evidence', async ({ page, request }) => {
  const document = process.env.RAG_RERANK_DOCUMENT
  test.skip(!process.env.RAG_SERVER_URL || !document || !process.env.RAG_SERVER_IMAGE,
    'Requires an existing completed test2.pdf and its page 4 phone table image')
  const search = await request.post('/api/search', {
    data: { text: '如何从表格数据里找到问题的答案？', document_ids: [document], limit: 6 },
  })
  expect(search.ok()).toBeTruthy()
  const hits = await search.json()
  expect(hits[0].source.page).toBe(3)
  expect(hits.every((hit: { rerank_status: string }) => hit.rerank_status === 'completed')).toBeTruthy()
  await page.goto(`/#/chat?document=${document}`)
  await expect(page.getByRole('button', { name: '新会话', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '新会话', exact: true }).click()
  await page.getByLabel('问题（可仅上传图片）').fill('请根据图中表格和文档说明，mate40 的价格是多少？引用文档来源。')
  await page.getByLabel('选择问题图片').setInputFiles(process.env.RAG_SERVER_IMAGE!)
  await page.getByRole('button', { name: '发送问题', exact: true }).click()
  const assistant = page.locator('.chat-message.assistant').last()
  await expect(assistant).toContainText('6999', { timeout: 150000 })
  await expect(assistant.locator('a.citation').first()).toBeVisible()
  await expect(assistant.locator('.markdown img').first()).toBeVisible()
  expect(await assistant.locator('.markdown img').first().evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0)
  const popupPromise = page.waitForEvent('popup')
  await assistant.locator('a.citation').first().click()
  const popup = await popupPromise
  await expect(popup).toHaveURL(/page=4/)
  await expect(popup.getByText('6999', { exact: false }).first()).toBeVisible()
  await popup.close()
  await page.getByLabel('问题（可仅上传图片）').fill('文档里提到火星银行2026年营业利润是多少？如果没有依据请明确说明。')
  await page.getByRole('button', { name: '发送问题', exact: true }).click()
  await expect(page.locator('.chat-message.assistant').last()).toContainText('依据不足', { timeout: 150000 })
  await expect(page.locator('.chat-message.assistant').last().locator('.sources')).toHaveCount(0)
  await page.screenshot({ path: 'test-results/server-reranker.png', fullPage: true })
})
