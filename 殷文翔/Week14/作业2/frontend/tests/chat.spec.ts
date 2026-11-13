import { test, expect } from '@playwright/test'

test('image preview, scoped question, stable retry, sources and safe Markdown', async ({ page }) => {
  const conversation = '11111111-1111-4111-8111-111111111111'
  const document = '22222222-2222-4222-8222-222222222222'
  const imageId = '33333333-3333-4333-8333-333333333333'
  const url = `/api/images/${imageId}/content`
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aB1sAAAAASUVORK5CYII=', 'base64')
  const image = { id: imageId, kind: 'user_upload', url, media_type: 'image/png',
    width: 1, height: 1, size_bytes: png.length, document_id: null, version: null, page: null }
  const source = { source_id: 'S1', document_id: document, version: 1, filename: '范围文档.pdf',
    page: 2, chunk_id: `${document}:1:2:0:0`, image_ids: [imageId], images: [
      { ...image, kind: 'document_image', document_id: document, version: 1, page: 2 },
    ] }
  const timestamp = '2026-11-13T07:00:00Z'
  let messages: unknown[] = []
  const submissions: { body: unknown; key: string | undefined }[] = []
  const requests: string[] = []
  page.on('request', request => requests.push(request.url()))
  await page.route('http://127.0.0.1:5174/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    let json: unknown
    if (path.endsWith('/health')) json = { status: 'ok' }
    else if (path.endsWith('/capabilities')) json = { vision_enabled: true, upload_max_files: 10,
      upload_max_file_bytes: 52428800, upload_max_request_bytes: 209715200,
      image_max_upload_files: 4, image_max_file_bytes: 10485760 }
    else if (path === '/api/conversations') json = { items: [
      { id: conversation, title: '验收会话', created_at: timestamp, updated_at: timestamp },
    ], total: 1, page: 1, page_size: 100 }
    else if (path === '/api/documents') json = { items: [
      { id: document, original_filename: '范围文档.pdf', current_searchable_version: 1 },
    ], total: 1, page: 1, page_size: 100 }
    else if (path.endsWith('/images')) json = { items: [image] }
    else if (path === url) { await route.fulfill({ contentType: 'image/png', body: png }); return }
    else if (path.endsWith('/messages') && request.method() === 'GET') {
      json = { items: messages, total: messages.length, page: 1, page_size: 100 }
    } else if (path.endsWith('/messages')) {
      submissions.push({ body: request.postDataJSON(), key: request.headers()['idempotency-key'] })
      if (submissions.length === 1) {
        await route.fulfill({ status: 503, json: { request_id: 'test', error: {
          code: 'CONNECTION_LOST', message: '连接中断，请重新获取结果', retryable: true, details: {},
        } } }); return
      }
      const common = { conversation_id: conversation, status: 'completed', error: null,
        created_at: timestamp, updated_at: timestamp }
      const user = { ...common, id: 'user1', role: 'user', sequence: 1, content: '', images: [image], sources: [] }
      const assistant = { ...common, id: 'assistant1', role: 'assistant', sequence: 2, images: [], sources: [source],
        content: `## 结论\n**100 件** [S1]\n\n![相关图](${url})\n\n<script>window.__xss=1</script><img src="https://evil.example/x" onerror="window.__xss=2"><a href="javascript:alert(1)">危险链接</a>\n\n![外部](https://evil.example/track.png)` }
      messages = [user, assistant]
      json = { user_message: user, assistant_message: assistant }
    } else { await route.fulfill({ status: 404, json: {} }); return }
    await route.fulfill({ json })
  })
  await page.goto('/#/chat')
  await expect(page.getByRole('button', { name: '发送问题' })).toBeVisible()
  await page.getByLabel('全部可检索文档').uncheck()
  await page.getByLabel('范围文档.pdf（可用版本 1）').check()
  await page.getByLabel('选择问题图片').setInputFiles({ name: 'photo.png', mimeType: 'image/png', buffer: png })
  await expect(page.getByAltText('待发送图片预览')).toBeVisible()
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.getByRole('alert')).toHaveText('连接中断，请重新获取结果')
  await page.getByRole('button', { name: '使用原请求获取结果' }).click()
  await expect(page.locator('.markdown strong')).toHaveText('100 件')
  await expect(page.getByText('[S1] 范围文档.pdf', { exact: true })).toBeVisible()
  await expect(page.getByText(`第 2 页 · 版本 1 · 文档 ${document}`)).toBeVisible()
  expect(submissions).toHaveLength(2)
  expect(submissions[0]).toEqual(submissions[1])
  expect(submissions[0]?.body).toEqual({ text: '', image_ids: [imageId], document_ids: [document] })
  expect(submissions[0]?.key).toMatch(/^[0-9a-f-]{36}$/)
  await expect(page.locator('.markdown img')).toHaveCount(1)
  await expect(page.locator('.markdown script, .markdown [onerror], .markdown a[href]:not(.citation)')).toHaveCount(0)
  await expect(page.getByLabel('查看来源 S1')).toHaveAttribute('href', `#/documents/${document}/view?version=1&page=2`)
  await expect(page.getByLabel('查看来源 S1')).toHaveAttribute('target', '_blank')
  await expect(page.locator('.sources article a').first()).toHaveAttribute('href', `#/documents/${document}/view?version=1&page=2`)
  expect(await page.evaluate(() => (window as Window & { __xss?: number }).__xss)).toBeUndefined()
  expect(requests.some(url => url.includes('evil.example'))).toBe(false)
  await expect(page.getByAltText('待发送图片预览')).toHaveCount(0)
  await page.reload()
  await expect(page.locator('.markdown strong')).toHaveText('100 件')
})
