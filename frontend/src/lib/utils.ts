import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** shadcn 约定的 className 合并工具：去重冲突的 Tailwind 类，后者覆盖前者 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * 把 markdown 原文转成干净纯文本（仅用于知识库列表**展示**，不落库、不影响召回）。
 *
 * 覆盖报告生成器实际产出的语法：标题（# 前缀）、引用（> 前缀）、
 * 列表符号（减号 / 星号 / 数字点）、加粗、行内代码、GFM 表格（竖线分隔）、
 * 分隔线。对不含这些标记的纯文本幂等无害。
 */
export function markdownToPlainText(md: string): string {
  if (!md) return ''
  const out: string[] = []
  for (const raw of md.split('\n')) {
    let line = raw
    const t = line.trim()

    // GFM 表格分隔行（如 | --- | ---: |）：整行丢弃
    if (t.startsWith('|') && /^[\s|:.-]+$/.test(t)) continue
    // GFM 表格行：去首尾竖线，单元格间用两个空格分隔
    if (t.startsWith('|')) {
      const cells = t
        .replace(/^\|/, '')
        .replace(/\|$/, '')
        .split('|')
        .map((c) => c.trim())
        .filter((c) => c !== '')
      if (cells.length) out.push(cells.join('  '))
      continue
    }
    // 分隔线 --- / ***
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) continue
    // 标题 #
    line = line.replace(/^#{1,6}\s+/, '')
    // 引用 >
    line = line.replace(/^\s*>\s?/, '')
    // 列表符号（- / * / + / 1. / 1、），保留缩进
    line = line.replace(/^(\s*)([-*+]|\d+[.、])\s+/, '$1')
    // 复选框 [ ]
    line = line.replace(/^(\s*)\[[ xX]\]\s+/, '$1')
    // 加粗 ** 与行内代码 `
    line = line.replace(/\*\*(.+?)\*\*/g, '$1').replace(/`([^`]+)`/g, '$1')

    if (line.trim() === '') {
      if (out.length && out[out.length - 1] !== '') out.push('')
      continue
    }
    out.push(line)
  }
  while (out.length && out[0] === '') out.shift()
  while (out.length && out[out.length - 1] === '') out.pop()
  return out.join('\n')
}

