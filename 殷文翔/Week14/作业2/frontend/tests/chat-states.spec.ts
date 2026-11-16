import { test, expect } from '@playwright/test'
import { basicRoutes, capabilities, image, message, paged, png, source } from './fixtures'
import type { Message } from '../src/types/chat'

test('vision disabled is explicit and text questions remain available', async ({ page }) => {
  let posted = 0
  let messages: Message[] = []
  await basicRoutes(page, async (route, path) => {
    if (path === '/api/capabilities') await route.fulfill({ json: { ...capabilities, vision_enabled: false } })
    else if (path.endsWith('/messages') && route.request().method() === 'POST') {
      posted++
      expect(route.request().postDataJSON().image_ids).toEqual([])
      messages = [message({ id: 'user1', role: 'user', content: '文字问题', sources: [] }), message({ sources: [], content: '依据不足' })]
      await route.fulfill({ json: { user_message: messages[0], assistant_message: messages[1] } })
    } else if (path.endsWith('/messages')) await route.fulfill({ json: paged(messages) })
    else return false
    return true
  })
  await page.goto('/#/chat')
  await expect(page.getByText(/视觉能力未就绪：当前服务未启用图片问答/)).toBeVisible()
  await expect(page.getByLabel('选择问题图片')).toBeDisabled()
  await page.getByLabel('问题（可仅上传图片）').fill('文字问题')
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.getByText('依据不足', { exact: true })).toBeVisible()
  expect(posted).toBe(1)
})

test('VISION_NOT_READY preserves draft, permits removing images and sending text', async ({ page }) => {
  let posted = 0
  await basicRoutes(page, async (route, path) => {
    if (!path.endsWith('/messages') || route.request().method() !== 'POST') return false
    posted++
    if (posted === 1) await route.fulfill({ status: 503, json: { request_id: 'vision', error: {
      code: 'VISION_NOT_READY', message: '视觉模型尚未就绪', retryable: false, details: {} } } })
    else {
      expect(route.request().postDataJSON().image_ids).toEqual([])
      await route.fulfill({ json: { user_message: message({ id: 'user1', role: 'user', sources: [] }), assistant_message: message() } })
    }
    return true
  })
  await page.goto('/#/chat')
  await page.getByLabel('问题（可仅上传图片）').fill('保留的问题')
  await page.getByLabel('选择问题图片').setInputFiles({ name: 'q.png', mimeType: 'image/png', buffer: png })
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.getByText(/视觉能力未就绪：当前服务未启用图片问答/)).toBeVisible()
  await expect(page.getByLabel('问题（可仅上传图片）')).toHaveValue('保留的问题')
  await expect(page.getByRole('button', { name: '使用原请求获取结果' })).toHaveCount(0)
  await page.getByRole('button', { name: '清除图片' }).click()
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect.poll(() => posted).toBe(2)
})

test('image validation, removal and preview URLs are released', async ({ page }) => {
  await page.addInitScript(() => {
    const created: string[] = [], revoked: string[] = []
    Object.assign(window, { previewUrls: { created, revoked } })
    const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL)
    URL.createObjectURL = value => { const url = create(value); created.push(url); return url }
    URL.revokeObjectURL = value => { revoked.push(value); revoke(value) }
  })
  await basicRoutes(page, async (route, path) => {
    if (path !== '/api/capabilities') return false
    await route.fulfill({ json: { ...capabilities, image_max_upload_files: 1, image_max_file_bytes: 100 } })
    return true
  })
  await page.goto('/#/chat')
  const file = { name: 'q.png', mimeType: 'image/png', buffer: png }
  await page.getByLabel('选择问题图片').setInputFiles([file, { ...file, name: 'second.png' }])
  await expect(page.getByRole('alert')).toContainText('最多选择 1 张图片')
  await page.getByLabel('选择问题图片').setInputFiles({ name: 'large.png', mimeType: 'image/png', buffer: Buffer.alloc(101) })
  await expect(page.getByRole('alert')).toContainText('超过单张大小限制')
  await page.getByLabel('选择问题图片').setInputFiles(file)
  await expect(page.getByAltText('待发送图片预览')).toBeVisible()
  await page.getByRole('button', { name: '移除图片 q.png' }).click()
  await expect(page.getByAltText('待发送图片预览')).toHaveCount(0)
  await page.getByLabel('选择问题图片').setInputFiles(file)
  await expect(page.getByAltText('待发送图片预览')).toBeVisible()
  await page.getByRole('link', { name: '文档管理', exact: true }).click()
  const urls = await page.evaluate(() => (window as Window & { previewUrls: { created: string[]; revoked: string[] } }).previewUrls)
  expect(urls.created).toHaveLength(2)
  expect([...urls.revoked].sort()).toEqual([...urls.created].sort())
})

test('unknown explicit scope never expands to all documents', async ({ page }) => {
  let submitted = 0
  await basicRoutes(page, async (route, path) => {
    if (!path.endsWith('/messages') || route.request().method() !== 'POST') return false
    submitted++; await route.fulfill({ json: {} }); return true
  })
  await page.goto('/#/chat?document=aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
  await page.getByLabel('问题（可仅上传图片）').fill('范围问题')
  await expect(page.getByRole('alert')).toContainText('指定文档暂不可检索')
  await expect(page.getByRole('button', { name: '发送问题' })).toBeDisabled()
  expect(submitted).toBe(0)
  await page.getByRole('button', { name: '清空选择' }).click()
  await page.getByLabel('教材.pdf（可用版本 1）').check()
  await expect(page.getByRole('button', { name: '发送问题' })).toBeEnabled()
})

test('empty explicit selection blocks sending', async ({ page }) => {
  await basicRoutes(page)
  await page.goto('/#/chat')
  await page.getByLabel('问题（可仅上传图片）').fill('范围问题')
  await page.getByLabel('全部可检索文档').uncheck()
  await expect(page.getByRole('alert')).toContainText('请至少选择一份可检索文档')
  await expect(page.getByRole('button', { name: '发送问题' })).toBeDisabled()
  await page.getByLabel('教材.pdf（可用版本 1）').check()
  await expect(page.getByRole('button', { name: '发送问题' })).toBeEnabled()
})

test('persisted failure and unsafe URLs in all rendering paths are handled', async ({ page }) => {
  const requests: string[] = []
  page.on('request', request => requests.push(request.url()))
  const evil = { ...image, url: 'https://evil.example/tracker.png' }
  const content = `# 安全标题\n\n**正文** [S1]\n\n![有效图](${image.url})\n\n![外部](https://evil.example/md.png)\n\n![内嵌](data:image/svg+xml;base64,PHN2Zz4=)\n\n<a href="javascript:alert(1)" data-source-target="question">危险</a><img src="/api/images/../../secret" onerror="window.__xss=1"><iframe src="https://evil.example/frame"></iframe><svg onload="window.__xss=2"></svg>`
  await basicRoutes(page, async (route, path) => {
    if (!path.endsWith('/messages')) return false
    const messages: Message[] = [message({ content, images: [evil], sources: [{ ...source, images: [...source.images, evil] }] }),
      message({ id: 'failed', sequence: 4, status: 'failed', content: '', sources: [], error: { code: 'MODEL_TIMEOUT', message: '模型响应超时，请稍后重新提问', retryable: true, details: {} } })]
    await route.fulfill({ json: paged(messages) }); return true
  })
  await page.goto('/#/chat')
  await expect(page.locator('.markdown h1')).toHaveText('安全标题')
  await expect(page.locator('.markdown img')).toHaveCount(1)
  await expect(page.locator('.sources img')).toHaveCount(1)
  await expect(page.getByRole('alert')).toContainText('MODEL_TIMEOUT')
  await expect(page.locator('.markdown iframe, .markdown svg, .markdown script, [onerror], [onload]')).toHaveCount(0)
  await expect(page.locator('.markdown [data-source-target]:not(.citation)')).toHaveCount(0)
  expect(requests.some(url => url.includes('evil.example') || url.includes('/secret'))).toBe(false)
  expect(await page.evaluate(() => (window as Window & { __xss?: number }).__xss)).toBeUndefined()
})

test('uncertain image upload prevents automatic duplicate submission', async ({ page }) => {
  let imageUploads = 0, questions = 0
  await basicRoutes(page, async (route, path) => {
    if (path.endsWith('/images')) {
      imageUploads++
      await route.fulfill({ status: 503, json: { request_id: 'test', error: { code: 'IMAGE_UPLOAD_COMMIT_UNCERTAIN', message: '图片提交结果待核对，勿自动重传', retryable: false, details: { image_ids: [image.id] } } } })
    } else if (path.endsWith('/messages') && route.request().method() === 'POST') {
      questions++; await route.fulfill({ json: {} })
    } else return false
    return true
  })
  await page.goto('/#/chat')
  await page.getByLabel('选择问题图片').setInputFiles({ name: 'q.png', mimeType: 'image/png', buffer: png })
  await page.getByRole('button', { name: '发送问题' }).click()
  await expect(page.getByText(/图片上传结果无法确认，已停止重复上传/)).toBeVisible()
  await expect(page.getByRole('button', { name: '发送问题' })).toBeDisabled()
  expect(imageUploads).toBe(1); expect(questions).toBe(0)
})
