import { test, expect } from '@playwright/test'
import { basicRoutes, message, paged } from './fixtures'

for (const slowInitial of [false, true]) {
  test(`document polling stops on navigation, initial request delayed=${slowInitial}`, async ({ page }) => {
    await page.clock.install()
    let count = 0, aborted = 0, settled = false
    page.on('requestfailed', request => {
      const url = new URL(request.url())
      if (url.pathname === '/api/documents' && url.searchParams.get('page_size') === '20') aborted++
    })
    let release = () => {}
    const gate = new Promise<void>(resolve => { release = resolve })
    await basicRoutes(page, async (route, path) => {
      if (path !== '/api/documents' || new URL(route.request().url()).searchParams.get('page_size') !== '20') return false
      count++
      if (slowInitial) await gate
      await route.fulfill({ json: paged([]) }).catch(() => {})
      settled = true
      return true
    })
    await page.goto('/#/documents')
    await expect.poll(() => count).toBe(1)
    if (!slowInitial) {
      await expect(page.getByText('还没有文档，选择 PDF 开始上传。')).toBeVisible()
      await page.clock.fastForward(3100)
      await expect.poll(() => count).toBe(2)
    }
    await page.getByRole('link', { name: '图文问答', exact: true }).click()
    await expect(page.getByRole('heading', { name: '文档图文问答' })).toBeVisible()
    if (slowInitial) await expect.poll(() => aborted).toBe(1)
    release()
    await expect.poll(() => settled).toBe(true)
    const countAtExit = count
    await page.clock.fastForward(20000)
    expect(count).toBe(countAtExit)
  })

  test(`chat polling stops on navigation, initial request delayed=${slowInitial}`, async ({ page }) => {
    await page.clock.install()
    let count = 0, aborted = 0, settled = false
    page.on('requestfailed', request => { if (new URL(request.url()).pathname.endsWith('/messages')) aborted++ })
    let release = () => {}
    const gate = new Promise<void>(resolve => { release = resolve })
    await basicRoutes(page, async (route, path) => {
      if (!path.endsWith('/messages')) return false
      count++
      if (slowInitial) await gate
      await route.fulfill({ json: paged([message({ status: 'pending', content: '', sources: [] })]) }).catch(() => {})
      settled = true
      return true
    })
    await page.goto('/#/chat')
    await expect.poll(() => count).toBe(1)
    if (!slowInitial) {
      await expect(page.getByText('正在检索并生成回答…可刷新会话查看进度。')).toBeVisible()
      await page.clock.fastForward(3100)
      await expect.poll(() => count).toBe(2)
    }
    await page.getByRole('link', { name: '文档管理', exact: true }).click()
    await expect(page.getByRole('heading', { name: '上传文档，建立知识库' })).toBeVisible()
    if (slowInitial) await expect.poll(() => aborted).toBe(1)
    release()
    await expect.poll(() => settled).toBe(true)
    const countAtExit = count
    await page.clock.fastForward(20000)
    expect(count).toBe(countAtExit)
  })
}

test('pending assistant becomes completed through polling', async ({ page }) => {
  await page.clock.install()
  let completed = false
  await basicRoutes(page, async (route, path) => {
    if (!path.endsWith('/messages')) return false
    await route.fulfill({ json: paged([completed ? message() : message({ status: 'pending', content: '', sources: [] })]) })
    return true
  })
  await page.goto('/#/chat')
  await expect(page.getByText('正在检索并生成回答…可刷新会话查看进度。')).toBeVisible()
  completed = true
  await page.clock.fastForward(3100)
  await expect(page.locator('.markdown strong')).toHaveText('100 件')
})
