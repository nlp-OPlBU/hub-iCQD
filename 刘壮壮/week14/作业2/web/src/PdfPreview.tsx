import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy } from 'pdfjs-dist'
import type { SourceLocation } from './api'
import { API } from './api'

GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString()

type PageLayout = {
  page: number
  width: number
  height: number
  naturalWidth: number
  naturalHeight: number
}

function normalizeText(value: string) {
  return value.replace(/\s+/g, '').toLowerCase()
}

function filterCrossPageSubstrings(locations: SourceLocation[]): SourceLocation[] {
  if (locations.length <= 1) return locations
  const normalized = locations.map(loc => normalizeText(loc.text || ''))
  const filtered = locations.filter((loc, i) => {
    const cur = normalized[i]
    if (!cur) return true
    return !locations.some((other, j) => i !== j && other.page !== loc.page && normalized[j].length > cur.length && normalized[j].includes(cur))
  })
  return filtered.length ? filtered : locations
}

function resolveTargetPage(page: number | null, locations: SourceLocation[], numPages?: number) {
  const raw = page && (!locations.length || locations.some(loc => loc.page === page))
    ? page
    : (locations[0]?.page || page || 1)
  return numPages ? Math.min(Math.max(1, raw), numPages) : Math.max(1, raw)
}

export default function PdfPreview({ uid, page, locations }: { uid: string; page: number | null; locations: SourceLocation[] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const hostRef = useRef<HTMLDivElement>(null)
  const highlightRef = useRef<HTMLDivElement>(null)
  const spacerRef = useRef<HTMLDivElement>(null)
  const needsAutoLocateRef = useRef(true)

  const effectiveLocations = useMemo(() => filterCrossPageSubstrings(locations), [locations])
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null)
  const [error, setError] = useState('')
  const [currentPage, setCurrentPage] = useState(() => resolveTargetPage(page, effectiveLocations))
  const [pageLayout, setPageLayout] = useState<PageLayout | null>(null)

  const matches = useMemo(
    () =>
      effectiveLocations
        .filter(loc => loc.page === currentPage && Array.isArray(loc.bbox) && loc.bbox.length === 4)
        .slice()
        .sort((a, b) => (a.bbox[1] ?? 0) - (b.bbox[1] ?? 0) || (a.bbox[0] ?? 0) - (b.bbox[0] ?? 0)),
    [effectiveLocations, currentPage]
  )

  useEffect(() => {
    let dead = false
    setError('')
    setPdf(null)
    setPageLayout(null)
    needsAutoLocateRef.current = true
    if (hostRef.current) delete hostRef.current.dataset.located
    const key = localStorage.getItem('rag-api-key')
    const task = getDocument({
      url: `${API}/api/v1/documents/${uid}/file`,
      httpHeaders: key ? { 'X-API-Key': key } : {},
      withCredentials: false,
    })
    task.promise
      .then(doc => {
        if (dead) return
        setPdf(doc)
        setCurrentPage(prev => Math.min(Math.max(1, prev), doc.numPages))
      })
      .catch(e => {
        if (!dead && e?.name !== 'AbortException') setError(e.message || 'PDF 加载失败')
      })
    return () => {
      dead = true
      void task.destroy()
    }
  }, [uid])

  useEffect(() => {
    needsAutoLocateRef.current = true
    if (hostRef.current) delete hostRef.current.dataset.located
    setCurrentPage(resolveTargetPage(page, effectiveLocations, pdf?.numPages))
  }, [uid, page, effectiveLocations, pdf])

  const alignHighlight = useCallback(() => {
    const host = hostRef.current
    const spacer = spacerRef.current
    if (!host || !spacer || pageLayout?.page !== currentPage) return
    if (!matches.length) {
      spacer.style.height = '0px'
      host.scrollTop = 0
      return
    }
    const highlight = highlightRef.current
    if (!highlight) return

    const hostRect = host.getBoundingClientRect()
    const highlightRect = highlight.getBoundingClientRect()
    const viewportHeight = host.clientHeight || hostRect.height || 640
    // Place the highlighted paragraph near the upper area of the PDF viewport (~9% from top, 52..76px),
    // giving clear breathing room below the toolbar without stopping at page-top (0px) or center (~50%).
    const desiredOffset = Math.round(Math.min(76, Math.max(52, viewportHeight * 0.09)))

    const hasDomRects = hostRect.height > 0 || highlightRect.height > 0 || highlightRect.top !== hostRect.top
    const highlightTopInPage = parseFloat(highlight.style.top || '0') || 0
    const highlightContentTop = hasDomRects
      ? highlightRect.top - hostRect.top + host.scrollTop - host.clientTop
      : 24 + highlightTopInPage

    const targetScrollTop = Math.max(0, Math.round(highlightContentTop - desiredOffset))
    const baseScrollHeight = Math.max(host.scrollHeight - spacer.offsetHeight, 24 + pageLayout.height + 24)
    const neededSpacer = Math.max(0, Math.ceil(targetScrollTop + viewportHeight - baseScrollHeight + 16))
    spacer.style.height = `${neededSpacer}px`
    host.scrollTop = targetScrollTop
    host.dataset.located = 'true'
  }, [matches, pageLayout, currentPage])

  useLayoutEffect(() => {
    if (!needsAutoLocateRef.current || pageLayout?.page !== currentPage) return
    alignHighlight()
  }, [alignHighlight, pageLayout, currentPage])

  useEffect(() => {
    if (!pdf) return
    let dead = false
    let renderTask: { cancel: () => void; promise: Promise<unknown> } | null = null
    let frameId = 0

    async function draw() {
      const host = hostRef.current
      const canvas = canvasRef.current
      if (!pdf || !host || !canvas) return
      try {
        const p = await pdf.getPage(currentPage)
        if (dead) return
        const rawHostWidth = host.clientWidth
        const innerWidth = rawHostWidth > 64 ? rawHostWidth - 32 : 892.5
        const natural = p.getViewport({ scale: 1 })
        const factor = Math.min(1.5, Math.max(0.4, innerWidth / natural.width))
        const viewport = p.getViewport({ scale: factor })
        const dpr = window.devicePixelRatio || 1

        canvas.width = Math.floor(viewport.width * dpr)
        canvas.height = Math.floor(viewport.height * dpr)
        canvas.style.width = `${viewport.width}px`
        canvas.style.height = `${viewport.height}px`

        setPageLayout({
          page: currentPage,
          width: viewport.width,
          height: viewport.height,
          naturalWidth: natural.width,
          naturalHeight: natural.height,
        })

        const ctx = canvas.getContext('2d')
        if (ctx) {
          renderTask = p.render({
            canvas,
            canvasContext: ctx,
            viewport,
            transform: dpr === 1 ? undefined : [dpr, 0, 0, dpr, 0, 0],
          })
          await renderTask.promise
        }
        if (!dead && needsAutoLocateRef.current) {
          frameId = requestAnimationFrame(() => {
            if (dead) return
            alignHighlight()
            needsAutoLocateRef.current = false
          })
        }
      } catch (e: any) {
        if (!dead && e?.name !== 'RenderingCancelledException' && e?.name !== 'AbortException') {
          setError((e as Error).message)
        }
      }
    }

    void draw()
    return () => {
      dead = true
      if (frameId) cancelAnimationFrame(frameId)
      renderTask?.cancel()
    }
  }, [pdf, currentPage])

  // Also finalize auto-locate when switching citations on the already-rendered page.
  useEffect(() => {
    if (!needsAutoLocateRef.current || pageLayout?.page !== currentPage) return
    const frameId = requestAnimationFrame(() => {
      alignHighlight()
      needsAutoLocateRef.current = false
    })
    return () => cancelAnimationFrame(frameId)
  }, [alignHighlight, pageLayout, currentPage])

  const changePage = (value: number) => {
    if (pdf && value >= 1 && value <= pdf.numPages) {
      needsAutoLocateRef.current = true
      if (hostRef.current) delete hostRef.current.dataset.located
      setCurrentPage(value)
    }
  }

  const readyLayout = pageLayout?.page === currentPage ? pageLayout : null

  return (
    <div className="pdf-reader">
      <nav className="pdf-toolbar" aria-label="PDF 页面导航">
        <button disabled={!pdf || currentPage <= 1} onClick={() => changePage(currentPage - 1)}>上一页</button>
        <label>
          第{' '}
          <input
            aria-label="跳转页码"
            type="number"
            min="1"
            max={pdf?.numPages || 1}
            value={currentPage}
            onChange={e => changePage(Number(e.target.value))}
          />{' '}
          页 / {pdf?.numPages || '…'} 页
        </label>
        <button disabled={!pdf || currentPage >= pdf.numPages} onClick={() => changePage(currentPage + 1)}>下一页</button>
        {matches.length > 0 && <span>{matches.length} 处高亮</span>}
      </nav>
      <div className="pdf-viewer" ref={hostRef}>
        {error ? (
          <p role="alert">{error}</p>
        ) : !pdf ? (
          <p>正在加载 PDF…</p>
        ) : (
          <>
            <div
              className="pdf-page"
              style={readyLayout ? { width: readyLayout.width, height: readyLayout.height } : undefined}
            >
              <canvas ref={canvasRef} />
              {readyLayout &&
                matches.map((loc, i) => {
                  const baseWidth = loc.page_width || readyLayout.naturalWidth || 595
                  const baseHeight = loc.page_height || readyLayout.naturalHeight || 842
                  const scaleX = readyLayout.width / baseWidth
                  const scaleY = readyLayout.height / baseHeight
                  const [x0, y0, x1, y1] = loc.bbox
                  return (
                    <div
                      key={`${loc.page}-${i}`}
                      className="pdf-highlight"
                      ref={i === 0 ? highlightRef : undefined}
                      style={{
                        left: x0 * scaleX,
                        top: y0 * scaleY,
                        width: Math.max(4, (x1 - x0) * scaleX),
                        height: Math.max(4, (y1 - y0) * scaleY),
                      }}
                    />
                  )
                })}
            </div>
            {matches.length ? (
              matches.map((loc, i) => (
                <div className="pdf-match-text" key={i}>
                  {loc.text}
                </div>
              ))
            ) : effectiveLocations.length ? (
              <div className="pdf-match-text">
                引用位置在第 {effectiveLocations[0].page} 页。当前查看第 {currentPage} 页，可用页码框直接跳转。
              </div>
            ) : (
              <div className="pdf-match-text">此引用尚无段落坐标，无法显示高亮。</div>
            )}
            <div ref={spacerRef} aria-hidden="true" style={{ height: 0 }} />
          </>
        )}
      </div>
    </div>
  )
}
