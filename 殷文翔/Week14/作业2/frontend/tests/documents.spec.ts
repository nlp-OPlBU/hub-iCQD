import { test, expect } from '@playwright/test'
import { basicRoutes, document, documentId, image, message, paged, png } from './fixtures'
import type { DocumentRecord } from '../src/types/document'
import type { Message } from '../src/types/chat'

test('upload → pending → processing → completed → scoped image question → sources', async ({ page }) => {
  await page.clock.install()
  let documents: DocumentRecord[] = [], messages: Message[] = []
  let uploadCount = 0, imageUploadCount = 0
  await basicRoutes(page, async (route, path) => {
    const request = route.request()
    if (path === '/api/documents/batch') {
      uploadCount++
      expect(request.headers()['content-type']).toContain('multipart/form-data')
      expect(request.postDataBuffer()?.toString()).toContain('filename="lesson.pdf"')
      documents = [document('pending')]
      await route.fulfill({ json: { request_id: 'upload-test', accepted: 1, rejected: 0,
        results: [{ index: 0, filename: 'lesson.pdf', outcome: 'accepted', status_code: 202, document: documents[0] }] } })
    } else if (path === '/api/documents') await route.fulfill({ json: paged(documents) })
    else if (path.endsWith('/images')) {
      imageUploadCount++
      expect(request.postDataBuffer()?.toString()).toContain('filename="question.png"')
      await route.fulfill({ json: { items: [image] } })
    } else if (path.endsWith('/messages') && request.method() === 'POST') {
      expect(request.postDataJSON()).toEqual({ text: '购物车最多可以添加多少件商品？', image_ids: [image.id], document_ids: [documentId] })
      messages = [message({ id: 'user1', role: 'user', sequence: 1, content: '购物车最多可以添加多少件商品？', images: [image], sources: [] }), message()]
      await route.fulfill({ json: { user_message: messages[0], assistant_message: messages[1] } })
    } else if (path.endsWith('/messages')) await route.fulfill({ json: paged(messages) })
    else return false
    return true
  })
  await page.goto('/#/documents')
  await expect(page.getByText('还没有文档，选择 PDF 开始上传。')).toBeVisible()
  await page.getByLabel('选择 PDF 文件').setInputFiles({ name: 'lesson.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.7\n%%EOF') })
  await page.getByRole('button', { name: '上传 1 个文件' }).click()
  await expect(page.getByRole('region', { name: '逐文件上传结果' })).toContainText('1 个已接收，0 个被拒绝')
  await expect(page.locator('.task-status')).toHaveText('待解析')
  documents = [document('processing')]
  await page.clock.fastForward(3100)
  await expect(page.locator('.task-status')).toHaveText('解析中')
  documents = [document('completed')]
  await page.clock.fastForward(3100)
  await expect(page.locator('.task-status')).toHaveText('解析完成')
  await page.getByText('任务详情', { exact: true }).click()
  await expect(page.getByText('44444444-4444-4444-8444-444444444444', { exact: true })).toBeVisible()
  await page.getByRole('link', { name: '向 教材.pdf 提问' }).click()
  await expect(page.getByLabel('全部可检索文档')).not.toBeChecked()
  await expect(page.getByLabel('教材.pdf（可用版本 1）')).toBeChecked()
  await page.getByLabel('问题（可仅上传图片）').fill('购物车最多可以添加多少件商品？')
  await page.getByLabel('选择问题图片').setInputFiles({ name: 'question.png', mimeType: 'image/png', buffer: png })
  await expect(page.getByAltText('待发送图片预览')).toBeVisible()
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.locator('.markdown strong')).toHaveText('100 件')
  await expect(page.getByText('[S1] 教材.pdf', { exact: true })).toBeVisible()
  await expect(page.getByText(`第 2 页 · 版本 1 · 文档 ${documentId}`)).toBeVisible()
  await expect(page.locator('.sources img')).toBeVisible()
  await expect(page.getByLabel('查看来源 S1')).toHaveAttribute('href', `#/documents/${documentId}/view?version=1&page=2`)
  await expect(page.getByLabel('查看来源 S1')).toHaveAttribute('target', '_blank')
  expect(uploadCount).toBe(1); expect(imageUploadCount).toBe(1)
  await expect(page.getByAltText('待发送图片预览')).toHaveCount(0)
})

test('per-file partial success, duplicate filenames, four states and stable retry key', async ({ page }) => {
  const failed = document('failed', '55555555-5555-4555-8555-555555555555', '失败.pdf')
  let docs = [document('pending'), document('processing', '66666666-6666-4666-8666-666666666666'),
    document('completed', '77777777-7777-4777-8777-777777777777'), failed]
  const retries: { key?: string; body: unknown }[] = []
  await basicRoutes(page, async (route, path) => {
    if (path === '/api/documents') await route.fulfill({ json: paged(docs) })
    else if (path === '/api/documents/batch') {
      expect(route.request().postDataBuffer()?.toString().match(/filename="same.pdf"/g)).toHaveLength(2)
      await route.fulfill({ json: { request_id: 'partial', accepted: 1, rejected: 1, results: [
        { index: 0, filename: 'same.pdf', outcome: 'accepted', status_code: 202, document: docs[0] },
        { index: 1, filename: 'same.pdf', outcome: 'rejected', status_code: 415,
          error: { code: 'INVALID_FILE_TYPE', message: 'PDF 文件标记无效', retryable: false, details: {} } },
      ] } })
    } else if (path.endsWith('/retry')) {
      retries.push({ key: route.request().headers()['idempotency-key'], body: route.request().postDataJSON() })
      if (retries.length === 1) await route.fulfill({ status: 503, json: { request_id: 'lost', error: {
        code: 'DATABASE_NOT_READY', message: '数据库暂不可用', retryable: true, details: {} } } })
      else {
        const updated = { ...failed, latest_version: 2, latest_task: { ...failed.latest_task, version: 2, status: 'pending' as const, stage: 'queued' as const, error: null } }
        docs = docs.map(item => item.id === failed.id ? updated : item)
        await route.fulfill({ status: 202, json: updated.latest_task })
      }
    } else return false
    return true
  })
  await page.goto('/#/documents')
  for (const status of ['待解析', '解析中', '解析完成', '解析失败']) await expect(page.locator('.task-status').getByText(status, { exact: true })).toBeVisible()
  const file = { name: 'same.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.7\n%%EOF') }
  await page.getByLabel('选择 PDF 文件').setInputFiles([file, { ...file, buffer: Buffer.from('invalid') }])
  await page.getByRole('button', { name: '上传 2 个文件' }).click()
  const results = page.getByRole('region', { name: '逐文件上传结果' })
  await expect(results).toContainText('1 个已接收，1 个被拒绝')
  await expect(results.locator('li')).toHaveCount(2)
  await expect(results).toContainText('PDF 文件标记无效（INVALID_FILE_TYPE）')
  await expect(page.getByText('失败原因：PDF 解析超时（MINERU_TIMEOUT）')).toBeVisible()
  await page.getByRole('button', { name: '重试解析' }).click()
  await page.getByRole('button', { name: '获取重试结果' }).click()
  expect(retries).toHaveLength(2); expect(retries[0]).toEqual(retries[1])
  expect(retries[0]?.body).toEqual({ expected_latest_version: 1 })
  expect(retries[0]?.key).toMatch(/^[0-9a-f-]{36}$/)
  await expect(page.locator('.document-card').filter({ hasText: '失败.pdf' })).toContainText('最新解析 v2')
  await expect(page.getByRole('button', { name: '重试解析' })).toHaveCount(0)
})

test('filter, pagination, stale-version retry and query recovery', async ({ page }) => {
  const queries: URLSearchParams[] = []
  let unavailable = false
  await basicRoutes(page, async (route, path) => {
    if (path === '/api/documents') {
      const query = new URL(route.request().url()).searchParams; queries.push(query)
      if (unavailable) await route.fulfill({ status: 503, json: { error: { code: 'DATABASE_NOT_READY', message: '列表暂不可用', retryable: true, details: {} }, request_id: 'test' } })
      else await route.fulfill({ json: { ...paged([document('failed')]), page: Number(query.get('page')), page_size: 20, total: query.get('status') ? 1 : 21 } })
    } else if (path.endsWith('/retry')) await route.fulfill({ status: 409, json: { error: { code: 'VERSION_CONFLICT', message: '文档已有更新的解析版本', retryable: false, details: {} }, request_id: 'test' } })
    else return false
    return true
  })
  await page.goto('/#/documents')
  await page.getByRole('button', { name: '下一页' }).click()
  await expect(page.getByText('第 2 / 2 页')).toBeVisible()
  await page.getByLabel('任务状态').selectOption('failed')
  await expect(page.getByText('第 1 / 1 页')).toBeVisible()
  expect(queries.at(-1)?.get('status')).toBe('failed')
  expect(queries.at(-1)?.get('page')).toBe('1')
  await page.getByRole('button', { name: '重试解析' }).click()
  await expect(page.getByRole('alert')).toContainText('文档已有更新的解析版本')
  await expect(page.getByRole('button', { name: '获取重试结果' })).toHaveCount(0)
  unavailable = true
  await page.getByRole('button', { name: '刷新文档' }).click()
  await expect(page.getByText(/列表暂不可用/)).toBeVisible()
  await expect(page.locator('.document-card')).toHaveCount(1)
  unavailable = false
  await page.getByRole('button', { name: '刷新文档' }).click()
  await expect(page.getByText(/列表暂不可用/)).toHaveCount(0)
})

test('upload response loss does not automatically replay the batch', async ({ page }) => {
  await page.clock.install()
  let uploads = 0
  await basicRoutes(page, async (route, path) => {
    if (path !== '/api/documents/batch') return false
    uploads++; await route.abort('failed'); return true
  })
  await page.goto('/#/documents')
  await page.getByLabel('选择 PDF 文件').setInputFiles({ name: 'file.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.7\n%%EOF') })
  await page.getByRole('button', { name: '上传 1 个文件' }).click()
  await expect(page.getByRole('alert')).toContainText('系统不会自动重传')
  await expect(page.getByRole('button', { name: '上传', exact: true })).toBeDisabled()
  await page.clock.fastForward(15000)
  expect(uploads).toBe(1)
})

test('document and source layouts fit a mobile viewport', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await basicRoutes(page, async (route, path) => {
    if (path === '/api/documents') await route.fulfill({ json: paged([document('failed', '55555555-5555-4555-8555-555555555555', '较长的文档名称用于验证移动端任务卡片内容换行与失败原因展示.pdf'), document()]) })
    else if (path.endsWith('/messages')) await route.fulfill({ json: paged([message()]) })
    else return false
    return true
  })
  await page.goto('/#/documents')
  await expect(page.locator('.document-card')).toHaveCount(2)
  expect(await page.evaluate(() => window.document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('documents-mobile.png'), fullPage: true })
  await page.getByRole('link', { name: '图文问答', exact: true }).click()
  await expect(page.locator('.sources article')).toBeVisible()
  expect(await page.evaluate(() => window.document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('chat-mobile.png'), fullPage: true })
})
