const tableRowPattern = /^\s*\|.*\|\s*$/
const tableSeparatorPattern =
  /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/

/**
 * 模型偶尔会违反“只做分析”的约束输出 Markdown 表格。
 * 主回答只保留叙述文字，结构化数据统一交给结果预览和详情抽屉。
 */
export function analysisText(markdown: string): string {
  const lines = markdown.split(/\r?\n/)
  const removed = new Set<number>()

  for (let index = 0; index < lines.length; index += 1) {
    if (!tableSeparatorPattern.test(lines[index])) continue

    if (index > 0 && tableRowPattern.test(lines[index - 1])) {
      removed.add(index - 1)
    }
    removed.add(index)

    let rowIndex = index + 1
    while (rowIndex < lines.length && tableRowPattern.test(lines[rowIndex])) {
      removed.add(rowIndex)
      rowIndex += 1
    }
  }

  const result = lines.filter((_, index) => !removed.has(index)).join('\n')
  if (removed.size > 0) return result.trim()

  // 兼容模型把整张 Markdown 表格压缩成一行的情况。
  const compactSeparator = result.search(/\|\s*:?-{3,}:?\s*\|/)
  if (compactSeparator >= 0) {
    const tableStart = result.indexOf('|')
    return result.slice(0, Math.max(0, tableStart)).trim()
  }

  return result.trim()
}
