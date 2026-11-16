import { test, expect } from '@playwright/test'

const id = '22222222-2222-4222-8222-222222222222'
const imageId = '33333333-3333-4333-8333-333333333333'
const imageUrl = `/api/images/${imageId}/content`
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aB1sAAAAASUVORK5CYII=', 'base64')

test('online viewer preserves cited version, highlights table text, and navigates pages', async ({ page }) => {
  const pageRequests: string[] = []
  await page.route('http://127.0.0.1:5174/api/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname
    let json: unknown
    if (path === '/api/health') json = { status: 'ok' }
    else if (path === `/api/documents/${id}`) json = { id, original_filename: '销售报告.pdf', latest_version: 2, current_searchable_version: 2 }
    else if (path.includes('/pages/')) {
      pageRequests.push(url.searchParams.get('version') || '')
      const current = Number(path.split('/').at(-1))
      json = { document_id: id, version: Number(url.searchParams.get('version')), filename: '销售报告.pdf', page: current, page_count: 3,
        blocks: [{ block_index: 0, evidence_type: 'table', text_origin: 'content', text: '苹果 | 100\n香蕉 | 50', bbox: [0.1, 0.2, 0.9, 0.8], images: [
          { id: imageId, url: imageUrl, page: current, document_id: id, version: 1 },
        ] }, { block_index: 1, evidence_type: 'text', text_origin: 'content', text: '<script>window.__xss=1</script>', bbox: null, images: [] }] }
    } else if (path.endsWith('/content')) {
      await route.fulfill({ contentType: 'application/pdf', body: '%PDF-1.4\n%%EOF' }); return
    } else if (path === imageUrl) {
      await route.fulfill({ contentType: 'image/png', body: png }); return
    } else { await route.fulfill({ status: 404, json: {} }); return }
    await route.fulfill({ json })
  })
  await page.goto(`/#/documents/${id}/view?version=1&page=2&block=0&offset=5&end=8`)
  await expect(page.getByRole('heading', { name: '销售报告.pdf' })).toBeVisible()
  await expect(page.getByText('解析版本 v1')).toBeVisible()
  await expect(page.locator('iframe')).toHaveAttribute('src', `/api/documents/${id}/content?version=1#page=2`)
  await expect(page.locator('.evidence-text mark')).toHaveText('100')
  await expect(page.getByAltText('第 2 页表格证据')).toBeVisible()
  await expect(page.locator('.evidence-block')).toHaveCount(1)
  await page.getByRole('link', { name: '查看本页全部证据' }).click()
  await expect(page.locator('.evidence-block')).toHaveCount(2)
  expect(await page.evaluate(() => (window as Window & { __xss?: number }).__xss)).toBeUndefined()
  await page.getByLabel('页码').fill('3')
  await page.getByRole('button', { name: '跳转' }).click()
  await expect(page.locator('iframe')).toHaveAttribute('src', `/api/documents/${id}/content?version=1#page=3`)
  await expect(page.locator('.evidence-text mark')).toHaveCount(0)
  await expect(page.getByRole('link', { name: '打开原始 PDF ↗' })).toHaveAttribute('href', `/api/documents/${id}/content?version=1#page=3`)
  expect(pageRequests.every(version => version === '1')).toBe(true)
})

test('viewer reports unavailable evidence and keeps the original PDF accessible', async ({ page }) => {
  await page.route('http://127.0.0.1:5174/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path === `/api/documents/${id}`) await route.fulfill({ json: { id, original_filename: '待解析.pdf', latest_version: 1, current_searchable_version: null } })
    else if (path.includes('/pages/')) await route.fulfill({ status: 409, json: { error: { code: 'DOCUMENT_NOT_PARSED', message: '此版本尚无可查看的解析证据' } } })
    else if (path.endsWith('/content')) await route.fulfill({ contentType: 'application/pdf', body: '%PDF-1.4\n%%EOF' })
    else await route.fulfill({ json: { status: 'ok' } })
  })
  await page.goto(`/#/documents/${id}/view`)
  await expect(page.getByRole('alert')).toHaveText('此版本尚无可查看的解析证据')
  await expect(page.getByRole('link', { name: '打开原始 PDF ↗' })).toBeVisible()
})

test('clicking an answer citation opens the cited document page in a new tab', async ({ page, context }) => {
  const { conversation, capabilities, document: record, message, paged, source } = await import('./fixtures')
  await context.route('http://127.0.0.1:5174/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    let json: unknown
    if (path === '/api/health') json = { status: 'ok' }
    else if (path === '/api/capabilities') json = capabilities
    else if (path === '/api/conversations') json = paged([conversation])
    else if (path === '/api/documents') json = paged([record()])
    else if (path === `/api/documents/${id}`) json = record()
    else if (path.endsWith('/messages')) json = paged([message({ content: '苹果销量100件。[S1]', sources: [{
      ...source, evidence_type: 'table', block_index: 0, char_offset: 5, char_end: 8,
    }] })])
    else if (path.includes('/pages/')) json = { document_id: id, version: 1, page: 2, page_count: 2, filename: '教材.pdf', blocks: [
      { block_index: 0, evidence_type: 'table', text_origin: 'content', text: '苹果 | 100', bbox: null, images: [] },
    ] }
    else if (path === imageUrl) { await route.fulfill({ contentType: 'image/png', body: png }); return }
    else if (path.endsWith('/content')) { await route.fulfill({ contentType: 'application/pdf', body: '%PDF-1.4\n%%EOF' }); return }
    else { await route.fulfill({ status: 404, json: {} }); return }
    await route.fulfill({ json })
  })
  await page.goto('/#/chat')
  const popup = context.waitForEvent('page')
  await page.getByLabel('查看来源 S1').click()
  const viewer = await popup
  await expect(viewer).toHaveURL(new RegExp(`/documents/${id}/view\\?version=1&page=2&block=0&offset=5&end=8$`))
  await expect(viewer.locator('.evidence-text mark')).toHaveText('100')
  await expect(viewer.locator('iframe')).toHaveAttribute('src', `/api/documents/${id}/content?version=1#page=2`)
  await viewer.close()
  await expect(page.locator('.markdown')).toContainText('苹果销量100件。')
})
