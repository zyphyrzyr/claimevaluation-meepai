/**
 * 时间展示。
 *
 * 后端存的是本地时间（`datetime.now()`），序列化成不带时区的 ISO 串
 * （`2026-09-14T17:06:43.501211`）。JS 对不带时区的 ISO 按**本地时间**解析，
 * 与后端的写入语义一致，所以这里直接 new Date 即可，不要补 Z——
 * 补了会让「刚刚入库」变成「8 小时前」。
 */

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前 / 更早则回落到日期 */
export function relativeTime(iso: string, now: Date = new Date()): string {
  if (!iso) return ''
  const t = new Date(iso)
  if (Number.isNaN(t.getTime())) return ''

  const diff = now.getTime() - t.getTime()
  // 负数（时钟偏移 / 服务器时间略快）按「刚刚」处理，别显示「-3 分钟前」
  if (diff < MINUTE) return '刚刚'
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)} 分钟前`
  if (diff < DAY) return `${Math.floor(diff / HOUR)} 小时前`
  if (diff < 30 * DAY) return `${Math.floor(diff / DAY)} 天前`
  return fmtDate(iso)
}

/** 绝对日期 YYYY-MM-DD，用于超过 30 天的条目 */
export function fmtDate(iso: string): string {
  const t = new Date(iso)
  if (Number.isNaN(t.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())}`
}

/** 精确到分钟 YYYY-MM-DD HH:mm，用于展开后的元数据行 */
export function fmtDateTime(iso: string): string {
  const d = fmtDate(iso)
  if (!d) return ''
  const t = new Date(iso)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d} ${p(t.getHours())}:${p(t.getMinutes())}`
}
