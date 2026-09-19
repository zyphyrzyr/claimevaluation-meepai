/**
 * 判赔措辞（与后端 core/damages_wording.py 一一对应，改一处必须改另一处）
 *
 * p10 / p50 / p90 是概率分位数，字段名只该出现在数据契约里。此前界面虽然已经
 * 写成人话，但两处页面各写各的（「偏保守」vs「保守估计」），且没有一处解释过
 * 这三个数是什么意思——使用人只能看到三个金额，不知道该信哪一个。
 *
 * 这里统一：三档固定叫「保守估计 / 最可能 / 争取上限」，首次出现时随附一句
 * 解释（由调用方决定要不要带），并给出回报倍数的分母口径。
 */

export const DAMAGE_P10_LABEL = '保守估计'
export const DAMAGE_P50_LABEL = '最可能'
export const DAMAGE_P90_LABEL = '争取上限'

export const DAMAGE_P10_EXPLAIN = '十件同类案件里最差的一件也能拿到的水平'
export const DAMAGE_P50_EXPLAIN = '一半的同类案件判得比它多、一半比它少'
export const DAMAGE_P90_EXPLAIN = '十件里只有一件能超过，需要证据和庭审都顺利'

/** 模型返回的是英文枚举，界面上同样不该裸奔 */
export const SCALE_SUPPORT_LABEL: Record<string, string> = {
  high: '强',
  medium: '中',
  low: '弱',
}

export const COST_ITEMS_TEXT = '律师费、诉讼费、公证取证费等'
export const COST_RANGE_TEXT = '约 8–15 万元'

/** 整数不带小数尾巴，与后端 _fmt_num 同口径 */
export function fmtAmount(v: number | null | undefined): string | null {
  if (v == null || Number.isNaN(Number(v))) return null
  const n = Number(v)
  return `${Number.isInteger(n) ? n : n.toFixed(1)} 万元`
}

/**
 * 判赔正文，切成片段以便调用方给关键数字加粗。
 * 缺失的档位自动省略——模型偶尔只给 p50，硬凑三档会渲染出「— 万元」。
 */
export interface DamagePart {
  text: string
  bold?: boolean
}

export function damagesParts(d: any, withExplain = false): DamagePart[] {
  if (!d) return []
  const p10 = fmtAmount(d.p10)
  const p50 = fmtAmount(d.p50)
  const p90 = fmtAmount(d.p90)
  const parts: DamagePart[] = []

  if (p50) {
    parts.push({ text: '按同类案件的判赔水平推算，本案' })
    parts.push({ text: `${DAMAGE_P50_LABEL}拿到 ${p50}`, bold: true })
    parts.push({ text: '左右' })
  }
  if (p10 || p90) {
    const flanks: string[] = []
    if (p10) flanks.push(`${DAMAGE_P10_LABEL} ${p10}`)
    if (p90) flanks.push(`${DAMAGE_P90_LABEL} ${p90}`)
    parts.push({ text: `（${flanks.join('，')}）` })
  }
  if (p50) parts.push({ text: '。' })

  // 三档含义单独成句，不塞进括号里——括号套括号读起来是灾难
  if (withExplain && (p10 || p50 || p90)) {
    const gloss: string[] = []
    if (p10) gloss.push(`${DAMAGE_P10_LABEL}＝${DAMAGE_P10_EXPLAIN}`)
    if (p50) gloss.push(`${DAMAGE_P50_LABEL}＝${DAMAGE_P50_EXPLAIN}`)
    if (p90) gloss.push(`${DAMAGE_P90_LABEL}＝${DAMAGE_P90_EXPLAIN}`)
    parts.push({ text: `三档的含义：${gloss.join('；')}。` })
  }

  const m = d.return_multiple
  if (m != null && !Number.isNaN(Number(m))) {
    parts.push({ text: `相对预估的维权投入（${COST_ITEMS_TEXT}，${COST_RANGE_TEXT}），大致能收回 ` })
    parts.push({ text: `${Number(m)} 倍`, bold: true })
    parts.push({ text: '。' })
  }

  const scale = SCALE_SUPPORT_LABEL[d.scale_support]
  if (scale) {
    parts.push({
      text: `案情里交代的侵权规模对高判赔的支撑度为「${scale}」`
        + (d.scale_support === 'low' ? '——规模证据偏弱，判赔可能贴着下限走。' : '。'),
    })
  }
  return parts
}

/** 纯文本版（决策卡等不便渲染片段的场景） */
export function damagesText(d: any, withExplain = false): string {
  return damagesParts(d, withExplain).map((p) => p.text).join('')
}
