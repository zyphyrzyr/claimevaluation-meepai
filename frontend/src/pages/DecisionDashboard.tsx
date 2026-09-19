import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import * as echarts from 'echarts'
import { api, exportUrls, knowledgeApi } from '../api'
import RerunControl, { RerunResult } from '../components/RerunControl'
import { tierOf, recoveryTierOf, RECOVERY_TIER_LABEL } from '../lib/tiers'
import { damagesText } from '../lib/damagesWording'
import { STEP_MOTION } from '../lib/motion'
import { SEV_LABEL, signalMeaning } from '../components/Basis'

/**
 * 评估结果页。
 *
 * 呈现原则：只把「总分数」与「二维决策矩阵」做成图形化的两块，其余一律用纯文字讲清楚——
 * 哪个模块强、哪个模块弱、弱在哪、要补什么。
 * 之所以不再堆卡片：卡片只能摊出数字，读的人还得自己换算成结论；而结果页的任务正是给结论。
 *
 * 页面分两块，由左侧导航切换（复用 CaseWorkbench 的 SectionNav，与另两个标签交互一致）：
 *   · 可视化结果：总分数 + 二维决策矩阵
 *   · 详细结果：总体结论 → 证据 → 红线检查 → 各维度 → 待补 → 下一步
 * activeSection 不传时退化为「两块都显示」，组件仍可独立使用。
 */

/** 结果页的两块（单一事实源：左侧导航、窄屏分段控件、单块渲染都取自这里） */
export const RESULT_SECTIONS: { id: string; label: string }[] = [
  { id: 'result-visual', label: '可视化结果' },
  { id: 'result-detail', label: '详细结果' },
]


/** 法律可行性三维度 */
const LEGAL_DIMS = [
  { key: 'rights', label: '权利基础' },
  { key: 'infringement', label: '侵权认定' },
  { key: 'procedure', label: '诉讼程序' },
]

/** 业务预期子维度：要钱 = 判赔规模与回款能力的均衡水平；要名 = 判例价值 */
const BUSINESS_DIMS: Record<string, { key: string; label: string }[]> = {
  要钱: [
    { key: 'damages', label: '判赔规模' },
    { key: 'recovery', label: '回款能力' },
  ],
  要名: [{ key: 'precedent', label: '判例价值' }],
}

/** 档位用词与 ScoreBadge / lib/tiers 同源，避免本页另造一套强弱口径 */
const TIER_TEXT: Record<string, string> = { go: '强', patch: '待补强', block: '偏弱' }

function QuadrantChart({ legal, business, mid }: { legal: number | null | undefined; business: number | null | undefined; mid: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const valid = typeof legal === 'number' && typeof business === 'number'

  useEffect(() => {
    if (!ref.current || !valid) return
    const chart = echarts.init(ref.current)

    // echarts 的 canvas 渲染器无法解析 CSS 变量，必须取解析后的颜色值；
    // 同时监听 data-theme 变化，主题切换时重渲染以跟随双主题配色。
    const readTheme = () => {
      const cs = getComputedStyle(document.documentElement)
      const v = (n: string) => cs.getPropertyValue(n).trim()
      return {
        text: v('--text') || '#0d0d0d',
        muted: v('--text-muted') || '#6b7280',
        border: v('--border') || '#e5e7eb',
        brand: v('--brand') || '#0d0d0d',
      }
    }
    const withAlpha = (c: string, a: number) => {
      const m = c.replace('#', '')
      if (m.length === 6) {
        const r = parseInt(m.slice(0, 2), 16)
        const g = parseInt(m.slice(2, 4), 16)
        const b = parseInt(m.slice(4, 6), 16)
        return `rgba(${r}, ${g}, ${b}, ${a})`
      }
      return c
    }

    const render = () => {
      const c = readTheme()
      chart.setOption(
        {
          grid: { left: 56, right: 20, top: 16, bottom: 52 },
          xAxis: {
            name: '法律可行性', min: 0, max: 100,
            nameLocation: 'middle', nameGap: 28,
            nameTextStyle: { color: c.text },
            axisLine: { lineStyle: { color: c.border } },
            axisLabel: { color: c.muted },
            splitLine: { lineStyle: { color: c.border } },
          },
          yAxis: {
            name: '业务预期', min: 0, max: 100,
            nameLocation: 'middle', nameGap: 40, nameRotate: 90,
            nameTextStyle: { color: c.text },
            axisLine: { lineStyle: { color: c.border } },
            axisLabel: { color: c.muted },
            splitLine: { lineStyle: { color: c.border } },
          },
          series: [
            {
              type: 'scatter',
              symbolSize: 18,
              data: [[legal, business]],
              itemStyle: { color: c.brand },
              markLine: {
                silent: true,
                symbol: 'none',
                lineStyle: { color: c.text, type: 'dashed', opacity: 0.35 },
                // 中线用后端下发的 QUADRANT_AXIS_MID（与总分档位线同源），
                // 不再硬编码 50 —— 前端硬编码一份必然与后端 quadrant() 漂移
                data: [{ xAxis: mid }, { yAxis: mid }],
                label: {
                  show: false,
                },
              },
              markArea: {
                silent: true,
                itemStyle: { color: withAlpha(c.brand, 0.06) },
                data: [[{ coord: [mid, mid] }, { coord: [100, 100] }]],
              },
            },
          ],
        },
        true,
      )
    }

    render()
    const ro = new MutationObserver(render)
    ro.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      ro.disconnect()
      chart.dispose()
    }
  }, [legal, business, mid, valid])

  if (!valid) {
    return (
      <div className="w-full h-80 flex items-center justify-center text-muted text-sm">
        法律可行性或业务预期尚未产出分数，暂不输出矩阵定位
      </div>
    )
  }
  return <div ref={ref} className="w-full h-80" />
}

export default function DecisionDashboard({
  activeSection,
  onSectionChange,
}: {
  /** 当前块 id（由 CaseWorkbench 的左侧导航驱动）；不传 = 两块都显示 */
  activeSection?: string
  /** 窄屏兜底分段控件用（导航列在 xl 以下整列隐藏） */
  onSectionChange?: (id: string) => void
} = {}) {
  const { id } = useParams<{ id: string }>()
  const [data, setData] = useState<any>(null)
  const [error, setError] = useState('')
  const [busyNode, setBusyNode] = useState<string>('')
  // 下载评估结果：一个按钮展开选格式，展开态与收起遮罩都挂在这里
  const [showExport, setShowExport] = useState(false)
  // 沉淀到经验库需要案件名与案由，结果接口里没有这两项，单独取一次
  const [caseMeta, setCaseMeta] = useState<any>(null)
  const [depositing, setDepositing] = useState(false)
  const [deposited, setDeposited] = useState(false)
  const [depositError, setDepositError] = useState('')

  // 两块高度差很大（可视化约一屏、详细可达数屏），切块后停在旧位置会让人以为没反应；
  // 另两个标签内容高度相近所以没做这件事，这里补上。
  useEffect(() => {
    if (activeSection != null) window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [activeSection])

  const reload = () => {
    if (id) api.result(id).then(setData).catch((e) => setError(String(e)))
  }
  useEffect(reload, [id])

  useEffect(() => {
    // 取不到案件名不影响看结果，所以失败静默——沉淀时退回「本案」兜底
    if (id) api.caseDetail(id).then(setCaseMeta).catch(() => {})
  }, [id])

  const rerun = async (node: string, guidance: string, cascade = true): Promise<RerunResult> => {
    if (!id) throw new Error('缺少案件 ID')
    setBusyNode(node)
    try {
      const r = await api.rerun(id, node, guidance, cascade)
      reload()
      return {
        effect: r.effect ?? '',
        stale_nodes: r.stale_nodes ?? [],
        rerun_nodes: r.rerun_nodes ?? [],
        cascade: r.cascade,
      }
    } finally {
      setBusyNode('')
    }
  }

  // 仅本节点模式下列出的「将被标参考」的下游维度（用于重跑控件确认提示）。
  // 与后端 DOWNSTREAM 一致：evidence_review 影响全部法律+业务维度；rights 影响侵权认定；
  // infringement / procedure / business 的下游只剩决策合成（纯规则、瞬时重算、不会 stale）。
  const downstreamLabelsOf = (node: string): string[] => {
    if (node === 'evidence_review') {
      const biz = isMoney ? ['判赔规模', '回款能力'] : ['判例价值']
      return ['权利基础', '侵权认定', '诉讼程序', ...biz, '业务预期']
    }
    if (node === 'rights') return ['侵权认定']
    return []
  }

  if (error) return <div className="bg-[var(--danger-soft)] text-[var(--danger)] rounded-lg p-4 text-sm">{error}</div>
  if (!data) return <div className="text-muted text-sm">加载中…</div>

  const { scores, confidence, recommendation, evidence, red_flags, dimension_results,
          defendant_profile, thresholds, goal_type } = data

  // 缺失/失效维度保持 null，不要静默归 0：后端已对 NaN 做「missing≠0」处理，
  // 前端若写成 ?? 0 会和「评估未完成」的结论自相矛盾，并把失效维度错误定位到 (0,70)。
  const legal = scores?.legal_feasibility ?? null
  const business = scores?.business_expectation ?? null
  const final = scores?.final
  const t = {
    go: thresholds?.go ?? 78,
    patch: thresholds?.patch ?? 62,
    quadrant_mid: thresholds?.quadrant_mid ?? 78,
    power_mean_p: thresholds?.power_mean_p ?? -0.5,
    recovery_base: thresholds?.recovery_base ?? 78,
    recovery_ok: thresholds?.recovery_ok ?? 70,
    recovery_weak: thresholds?.recovery_weak ?? 45,
  }
  const mid = t.quadrant_mid

  const dr = (node: string) => dimension_results?.[node]?.result ?? {}
  const num = (v: any) => (v == null || v === '' ? '—' : v)
  const tierWord = (score: any) => {
    const tr = tierOf(score, t)
    return tr ? TIER_TEXT[tr] : '未评分'
  }
  // 回款能力是概率型指标，档位锚在「记录干净」的基准分上，不能套决策分的 62/78
  const recoveryTierWord = (score: any) => {
    const rt = recoveryTierOf(score, t)
    return rt ? RECOVERY_TIER_LABEL[rt] : '未评分'
  }

  // 受控（左侧导航驱动）时只渲染当前块；不受控时两块都渲染
  const show = (sid: string) => activeSection == null || activeSection === sid

  const bizDims = BUSINESS_DIMS[goal_type] ?? BUSINESS_DIMS['要钱']
  const isMoney = goal_type !== '要名'
  const staleCount = Object.values(dimension_results ?? {})
    .filter((d: any) => d?.status === 'stale').length
  const recoveryRedFlags: string[] = defendant_profile?.metrics?.red_flags ?? dr('recovery').red_flags ?? []
  const recoveryGreenFlags: string[] = defendant_profile?.metrics?.green_flags ?? dr('recovery').green_flags ?? []
  // 回款能力：后端该节点只产出 recovery_ability，**不产出 score**（memo 生成器用的也是它）。
  // 此前按 score 取，页面上会渲染成「— 分（未评分）」，紧接着又写「能收回约 78%」，自相矛盾。
  const recoveryAbility = defendant_profile?.recovery_ability ?? dr('recovery').recovery_ability ?? null
  const gaps: any[] = evidence?.gap_list ?? []
  const extraEvidence: any[] = evidence?.extra_evidence ?? []
  const missingDims: string[] = dr('synthesize').missing ?? []
  const coeff = data.correction_coeff
  const mootDone = coeff != null && coeff !== 1

  // 总档位（口径与后端 quadrant / 推荐档一致）
  const levelText = recommendation?.level === 'block'
    ? '暂不建议起诉（命中程序性红线）'
    : recommendation?.recommendation
      ?? (final == null ? '待评估' : final >= t.go ? '建议优先启动' : final >= t.patch ? '补充短板后启动' : '建议暂缓')

  // 法律可行性里最弱的一项：幂平均取短板，它决定整体上限
  const legalRank = LEGAL_DIMS
    .map(({ key, label }) => ({ label, score: dr(key).score }))
    .filter((x) => typeof x.score === 'number')
    .sort((a, b) => a.score - b.score)

  const quadrantName = () => {
    if (typeof legal !== 'number' || typeof business !== 'number') return ''
    if (legal >= mid && business >= mid) return '右上「双强」区——法律与业务都站得住，属于优先启动的形态'
    if (legal >= mid && business < mid) return '右下象限——法律上站得住，但经济回报偏弱，问题不在能不能赢，而在值不值得打'
    if (legal < mid && business >= mid) return '左上象限——回报空间大，但法律依据偏弱，先把权利与侵权证据补扎实'
    return '左下「双弱」区——法律与经济两头都不足，建议先解决红线与证据问题，暂不启动'
  }

  /**
   * 把本次结论沉淀进案件经验库。
   *
   * 原先这个入口在「决策备忘录」页，该页下架后挪到这里。
   * 沉淀的是「结论 + 分数 + 置信度 + 主要短板」这几句，而不是整篇报告：
   * 经验库是给后续案件召回参考的，需要的是可复用的判断，不是文档全文。
   */
  const depositToKnowledge = async () => {
    if (!id || depositing || deposited) return
    setDepositing(true)
    setDepositError('')
    try {
      const name = caseMeta?.name ?? '本案'
      const cause = caseMeta?.cause_type ?? ''
      const content = [
        `结论：${levelText}（主诉决策分 ${num(final)}）`,
        `法律可行性 ${num(legal)} 与业务预期 ${num(business)} 的均衡水平 ${num(final)}；置信度 ${num(confidence)}%。`,
        recommendation?.reason ? `· ${recommendation.reason}` : '',
        evidence?.completeness != null
          ? `· 证据完整度 ${num(evidence.completeness)}%，${gaps.length} 项缺口待补。`
          : '',
      ].filter(Boolean).join('\n')
      await knowledgeApi.deposit(id, `${name} 评估结论${cause ? `（${cause}）` : ''}`, content)
      setDeposited(true)
    } catch (e) {
      // 不把整页切成错误态：沉淀失败不该让人看不到评估结果
      setDepositError(`沉淀失败：${String(e)}`)
    } finally {
      setDepositing(false)
    }
  }

  return (
    <div className="space-y-5">
      {/* 窄屏兜底：导航列在 xl 以下整列隐藏，用横向分段控件补切换入口 */}
      {onSectionChange && (
        <div className="flex flex-wrap gap-2 xl:hidden">
          {RESULT_SECTIONS.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => onSectionChange(s.id)}
              className={
                activeSection === s.id
                  ? 'px-3 py-1.5 rounded-lg text-sm border bg-fg text-canvas border-fg font-medium transition-colors'
                  : 'px-3 py-1.5 rounded-lg text-sm border bg-surface text-muted border-line hover:text-fg transition-colors'
              }
            >
              {s.label}
            </button>
          ))}
        </div>
      )}

      {/* 单块逐步：一次只渲染当前块，切换动画与「案件详情」「评估详情」同款 */}
      <AnimatePresence mode="wait">
        <motion.div key={activeSection ?? 'all'} {...STEP_MOTION}>
          {/* ① 可视化结果 */}
          {show('result-visual') && (
            <div className="space-y-5">
              {staleCount > 0 && (
                <div className="text-xs text-[var(--warning)] bg-[var(--warning-soft)] border border-[var(--warning-soft)] rounded-lg px-3 py-1.5">
                  {staleCount} 个维度结果已失效（上游被重跑），请重跑后采信
                </div>
              )}

              {/* ① 总分数：大号分数 + 档位刻度条（保留刻度条，去掉大面积色块底） */}
              <div className="rounded-xl border border-line bg-surface p-5">
                <div className="flex items-start justify-between gap-4 flex-wrap">
                  <div className="min-w-0 flex-1">
                    <div className="text-xs text-muted">主诉决策分</div>
                    <div className="text-lg font-medium mt-1">{levelText}</div>
                    {recommendation?.reason && (
                      <div className="text-sm text-muted mt-1">{recommendation.reason}</div>
                    )}
                    {/* 档位刻度条：0–100，标出补充短板线(patch) 与优先启动线(go)，并用圆点标当前分 */}
                    <div className="mt-3 pt-4">
                      <div className="relative h-2 rounded-full bg-[var(--border)]">
                        <span className="absolute top-1/2 -translate-y-1/2 h-4 w-px bg-[var(--text-muted)]" style={{ left: `${t.patch}%` }} />
                        <span className="absolute -top-4 text-[10px] text-muted -translate-x-1/2" style={{ left: `${t.patch}%` }}>{t.patch}</span>
                        <span className="absolute top-1/2 -translate-y-1/2 h-4 w-px bg-[var(--text-muted)]" style={{ left: `${t.go}%` }} />
                        <span className="absolute -top-4 text-[10px] text-muted -translate-x-1/2" style={{ left: `${t.go}%` }}>{t.go}</span>
                        {final != null && (
                          <span
                            className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-3 h-3 rounded-full bg-[var(--brand)] ring-2 ring-white/70"
                            style={{ left: `${final}%` }}
                          />
                        )}
                      </div>
                      <div className="text-[10px] text-muted mt-1.5 flex justify-between">
                        <span>&lt; {t.patch} 暂缓</span>
                        <span>{t.patch}–{t.go - 1} 补充短板后启动</span>
                        <span>≥ {t.go} 优先启动</span>
                      </div>
                    </div>
                  </div>
                  <div className="text-right shrink-0">
                    <div className="text-4xl font-semibold leading-none">{num(final)}</div>
                    <div className="text-xs text-muted mt-1">满分 100</div>
                  </div>
                </div>
              </div>

              {/* ② 二维决策矩阵 */}
              <div className="rounded-xl border border-line bg-surface p-5">
                <h2 className="text-sm font-medium mb-2">二维决策矩阵</h2>
                {final != null ? (
                  <QuadrantChart legal={legal} business={business} mid={mid} />
                ) : (
                  <div className="h-72 flex items-center justify-center text-muted text-sm">
                    评估未完成，暂不输出矩阵定位
                  </div>
                )}
                <p className="text-xs text-muted mt-3 leading-relaxed">
                  横轴为法律可行性、纵轴为业务预期，虚线与浅色区的中线是 {mid} 分；右上象限代表两者都较强。
                  {quadrantName() && `本案落在${quadrantName()}。`}
                </p>
              </div>
            </div>
          )}

          {/* ② 详细结果：全部为纯文字，不再使用卡片 */}
          {show('result-detail') && (
            <section className="space-y-6">
              <div>
                <h2 className="text-sm font-medium mb-2">总体结论</h2>
                <p className="text-sm text-muted leading-relaxed">
                  法律可行性 {num(legal)} 分、业务预期 {num(business)} 分，按短板效应合出主诉决策分{' '}
                  <b className="text-fg">{num(final)}</b> 分，落在「{levelText}」区间
                  （≥{t.go} 优先启动，{t.patch}–{t.go - 1} 补充短板后启动，低于 {t.patch} 暂缓）。
                  {typeof legal === 'number' && typeof business === 'number' && (
                    legal === business
                      ? '两侧旗鼓相当，提升任一侧都能拉动总分。'
                      : `两侧相比，${
                          legal > business
                            ? `业务预期更弱（${num(business)} 对 ${num(legal)}）`
                            : `法律可行性更弱（${num(legal)} 对 ${num(business)}）`
                        }；因为合成取的是短板，优先改善较弱的一侧，总分提升最明显。`
                  )}
                  {missingDims.length > 0 && ` 另有${missingDims.join('、')}未产出分数，不参与合成，结论完整度因此打折。`}
                  {mootDone && ` 法律可行性已按模拟法庭结论乘修正系数 ${coeff}。`}
                  {recommendation?.level === 'block' && ' 本案命中程序性红线——据规则优先于评分，应先解决红线问题。'}
                </p>
              </div>

              <div>
                <h2 className="text-sm font-medium mb-2">证据</h2>
                <p className="text-sm text-muted leading-relaxed">
                  结论置信度 <b className="text-fg">{num(confidence)}%</b>，由证据完整度 {num(evidence?.completeness)}% 决定，
                  与得分分开计算——证据越全，这个结论越值得信。
                  {typeof confidence === 'number' && confidence < 50 && ' 当前置信度偏低，建议先补证再据此决策。'}
                </p>
              </div>

              <div>
                <h2 className="text-sm font-medium mb-2">红线检查</h2>
                {(red_flags ?? []).length === 0 ? (
                  <p className="text-sm text-muted">暂无红线检查结果。</p>
                ) : (
                  <>
                    <p className="text-sm text-muted leading-relaxed">
                      共执行 {(red_flags ?? []).length} 条程序性规则：
                      {(red_flags ?? []).filter((r: any) => r.severity === 'pass').length} 条通过、
                      {(red_flags ?? []).filter((r: any) => r.severity === 'warning').length} 条警示、
                      {(red_flags ?? []).filter((r: any) => r.severity === 'block').length} 条拦截。
                      {(red_flags ?? []).some((r: any) => r.severity === 'block')
                        ? '命中拦截即流程终止，后续维度不再有意义——应先解决下列问题。'
                        : '没有命中硬性障碍，评估可以继续。'}
                    </p>
                    <ul className="mt-1.5 space-y-1">
                      {(red_flags ?? [])
                        .filter((r: any) => r.severity !== 'pass')
                        .map((r: any, i: number) => (
                          <li key={i} className="text-sm text-muted leading-relaxed">
                            · <b className="text-fg">{SEV_LABEL[r.severity] ?? r.severity}</b>
                            ：{r.rule_name}——{r.result}
                            {r.reason ? `。${r.reason}` : ''}
                          </li>
                        ))}
                    </ul>
                  </>
                )}
              </div>

              <div>
                <h2 className="text-sm font-medium mb-2">
                  法律可行性 {num(legal)} 分 · {tierWord(legal)}
                </h2>
                <ul className="space-y-1.5">
                  {LEGAL_DIMS.map(({ key, label }) => {
                    const r = dr(key)
                    const sc = r.score
                    let extra = ''
                    if (key === 'rights') {
                      const strs: string[] = r.risks ?? []
                      if (strs.length) extra = ` 主要风险：${strs[0]}。`
                    } else if (key === 'infringement') {
                      const unmet = (r.elements ?? []).filter((e: any) => e.status && e.status !== '满足')
                      if (unmet.length) extra = ` 其中「${unmet[0].name}」尚未满足——${unmet[0].analysis}。`
                    } else if (key === 'procedure') {
                      const high = (r.risks ?? []).filter((x: any) => x?.level === 'high')
                      if (high.length) extra = ` 其中「${high[0].item}」风险较高——${high[0].detail}。`
                    }
                    return (
                      <li key={key} className="text-sm text-muted leading-relaxed">
                        <b className="text-fg">{label}</b> {num(sc)} 分（{tierWord(sc)}）：{r.analysis ?? '—'}
                        {extra}
                      </li>
                    )
                  })}
                </ul>
                {legalRank.length > 1 && (
                  <p className="text-sm text-muted leading-relaxed mt-1.5">
                    三个维度按短板效应合成，最弱的一项决定整体上限：当前最弱的是
                    <b className="text-fg">{legalRank[0].label}（{num(legalRank[0].score)} 分）</b>
                    ，先补它收益最大。
                  </p>
                )}
              </div>

              <div>
                <h2 className="text-sm font-medium mb-2">
                  业务预期 {num(business)} 分 · {tierWord(business)}
                </h2>
                {isMoney ? (
                  <ul className="space-y-1.5">
                    <li className="text-sm text-muted leading-relaxed">
                      <b className="text-fg">判赔规模</b> {num(dr('damages').score)} 分（{tierWord(dr('damages').score)}）：
                      {damagesText(dr('damages'))}
                      {dr('damages').analysis ? ` ${dr('damages').analysis}` : ''}
                      {dr('damages').scale_support === 'low' && ' 规模证据偏弱，判赔可能贴着下限走。'}
                    </li>
                    <li className="text-sm text-muted leading-relaxed">
                      <b className="text-fg">回款能力</b> {num(recoveryAbility)} 分（{recoveryTierWord(recoveryAbility)}）：
                      赢了官司不等于拿得到钱。按被告的工商状态与涉诉记录，胜诉后实际能收回款项的可能性约为{' '}
                      <b className="text-fg">{num(recoveryAbility)}%</b>。
                      {dr('recovery').analysis ? ` ${dr('recovery').analysis}` : ''}
                      {recoveryRedFlags.length > 0 || recoveryGreenFlags.length > 0
                        ? ` 本次命中 ${recoveryRedFlags.length} 项不利迹象、${recoveryGreenFlags.length} 项有利迹象：`
                        : ` 本次没有命中任何加扣分项，${num(recoveryAbility)} 分是规则表对「公开记录查不到问题」的默认取值（基准 ${num(t.recovery_base)} 分），不代表已核实被告具备偿付能力。`}
                      {(recoveryRedFlags.length > 0 || recoveryGreenFlags.length > 0) && (
                        <ul className="mt-1 space-y-0.5">
                          {recoveryRedFlags.map((f, i) => (
                            <li key={`r${i}`} className="text-xs text-muted">· {signalMeaning(f, 'red')}</li>
                          ))}
                          {recoveryGreenFlags.map((f, i) => (
                            <li key={`g${i}`} className="text-xs text-muted">· {signalMeaning(f, 'green')}</li>
                          ))}
                        </ul>
                      )}
                    </li>
                  </ul>
                ) : (
                  <p className="text-sm text-muted leading-relaxed">
                    <b className="text-fg">判例价值</b> {num(dr('precedent').score)} 分（{tierWord(dr('precedent').score)}）：
                    「要名」目标下不看钱，直接以规则影响力衡量。本案首案指数 {num(dr('precedent').first_case_index)}，
                    影响层级为「{num(dr('precedent').influence_level)}」
                    {dr('precedent').influence_level === '行业级'
                      ? '——胜诉的影响会溢出到整个行业，适合作为对外宣传与行业博弈的筹码。'
                      : '——影响主要限于本案或本区域。'}
                    {dr('precedent').analysis ? ` ${dr('precedent').analysis}` : ''}
                  </p>
                )}
                <p className="text-sm text-muted leading-relaxed mt-1.5">
                  {isMoney
                    ? '判赔规模与回款能力同样按短板效应合成：判得再多、收不回来也白搭；收得回来但判得太少，同样不划算。'
                    : '「要名」下的业务预期直接取判例价值本身，不像「要钱」那样把判赔规模与回款能力聚合起来。'}
                </p>
              </div>

              <div>
                <h2 className="text-sm font-medium mb-2">需要补充什么（{gaps.length} 项缺口）</h2>
                {gaps.length > 0 ? (
                  <ul className="space-y-1.5">
                    {gaps.map((g: any) => (
                      <li key={g.id} className="text-sm text-muted leading-relaxed">
                        · {g.suggestion}
                        <span className="text-xs">（依据：{g.basis}；{g.reason}）</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-muted">证据要件齐备，没有发现明显缺口。</p>
                )}
                {extraEvidence.length > 0 && (
                  <p className="text-sm text-muted leading-relaxed mt-1.5">
                    清单外另有系统识别到的材料：{extraEvidence.map((e: any) => `${e.name}（${e.value}）`).join('；')}。
                  </p>
                )}
              </div>

              <div>
                <h2 className="text-sm font-medium mb-2">下一步建议</h2>
                <p className="text-sm text-muted leading-relaxed">
                  {gaps.length > 0
                    ? `优先补齐上述 ${gaps.length} 项缺口（补证会同时抬高各维度得分与置信度）；`
                    : '证据要件已齐备，可把精力放在法律论证与和解谈判上；'}
                  {legalRank.length > 1 && `法律可行性侧先改善最弱的「${legalRank[0].label}」；`}
                  {isMoney && '业务预期侧先解决回款与判赔规模的短板；'}
                  修正后可单独重跑对应节点，决策合成立即重算。尚未跑过模拟法庭的话，也可先跑一轮看法律可行性能否承压。
                </p>
              </div>

              {/* 节点级重跑：以行内文字链接呈现，避免重新引入卡片 */}
              <div>
                <h2 className="text-sm font-medium mb-2">单独重跑某个节点</h2>
                <p className="text-xs text-muted mb-2">
                  重跑只重算该节点及其下游（被牵连的维度会标记为失效），引导意见会写入案件观点、影响后续所有节点。
                </p>
                <div className="space-y-2">
                  {LEGAL_DIMS.map(({ key, label }) => (
                    <RerunControl
                      key={key}
                      compact
                      variant="link"
                      label={label}
                      busy={busyNode !== ''}
                      downstreamLabels={downstreamLabelsOf(key)}
                      hint={key === 'rights'
                        ? '重跑后「侵权认定」将标记失效（其结论依赖权利基础），决策合成立即重算。'
                        : '重跑后决策合成立即重算；法律可行性三维度按分层幂平均重新聚合。'}
                      onRerun={(g, cascade) => rerun(key, g, cascade)}
                    />
                  ))}
                  <RerunControl
                    compact
                    variant="link"
                    label="业务预期"
                    busy={busyNode !== ''}
                    downstreamLabels={downstreamLabelsOf('business')}
                    hint={`重跑将同时重算${bizDims.map((d) => d.label).join('、')}，并瞬时重算决策合成。`}
                    onRerun={(g, cascade) => rerun('business', g, cascade)}
                  />
                  <RerunControl
                    compact
                    variant="link"
                    label="证据盘点"
                    busy={busyNode !== ''}
                    downstreamLabels={downstreamLabelsOf('evidence_review')}
                    hint="补齐证据后可重跑证据盘点；其下游的权利基础、侵权认定、诉讼程序、业务预期与决策合成都会被标记失效。"
                    onRerun={(g, cascade) => rerun('evidence_review', g, cascade)}
                  />
                </div>
                </div>
            </section>
          )}
        </motion.div>
      </AnimatePresence>

      {/* ④ 下一步动作：放在切换区之外，两块下方都可见 */}
      <div className="flex flex-wrap items-center gap-3">
        {/* 下载评估结果：一个按钮展开选格式。Word 与 PDF 同源同内容，区别只在用途
            （Word 便于修改归档，PDF 便于直接转发）。用原生链接下载，中文文件名最稳。 */}
        <div className="relative">
          <button
            type="button"
            onClick={() => setShowExport((v) => !v)}
            className="border border-line text-fg px-4 py-2 rounded-lg text-sm hover:bg-surface transition-colors"
          >
            下载评估结果 {showExport ? '▴' : '▾'}
          </button>
          {showExport && (
            <>
              {/* 点空白处收起：铺一层透明遮罩，比全局监听 click 更省事且不会误伤 */}
              <div className="fixed inset-0 z-10" onClick={() => setShowExport(false)} />
              <div className="absolute left-0 top-full mt-1 z-20 w-40 rounded-lg border border-line bg-surface shadow-lg overflow-hidden">
                <a
                  href={exportUrls.resultDocx(String(id))}
                  onClick={() => setShowExport(false)}
                  className="block px-3 py-2 text-sm text-fg hover:bg-canvas transition-colors"
                >
                  Word（.docx）
                </a>
                <a
                  href={exportUrls.resultPdf(String(id))}
                  onClick={() => setShowExport(false)}
                  className="block px-3 py-2 text-sm text-fg hover:bg-canvas border-t border-line transition-colors"
                >
                  PDF
                </a>
              </div>
            </>
          )}
        </div>

        {/* 沉淀到经验库：原在「决策备忘录」页，该页下架后挪到这里 */}
        <button
          type="button"
          onClick={depositToKnowledge}
          disabled={depositing || deposited}
          className="border border-line text-muted px-4 py-2 rounded-lg text-sm hover:text-fg transition-colors disabled:opacity-50"
        >
          {depositing ? '沉淀中…' : deposited ? '已沉淀到经验库' : '沉淀到经验库'}
        </button>
      </div>
      {depositError && <div className="text-xs text-[var(--danger)]">{depositError}</div>}
    </div>
  )
}
