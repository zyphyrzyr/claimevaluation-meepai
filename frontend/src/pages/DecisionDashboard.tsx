import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import * as echarts from 'echarts'
import { api } from '../api'
import RerunControl, { RerunResult } from '../components/RerunControl'

const LEVEL_STYLE: Record<string, { cls: string; text: string }> = {
  green: { cls: 'bg-[var(--success-soft)] text-[var(--success)] border-[var(--success-soft)]', text: '建议优先启动' },
  yellow: { cls: 'bg-[var(--warning-soft)] text-[var(--warning)] border-[var(--warning-soft)]', text: '补充短板后启动' },
  red: { cls: 'bg-[var(--danger-soft)] text-[var(--danger)] border-[var(--danger-soft)]', text: '建议暂缓' },
  block: { cls: 'bg-[var(--danger-soft)] text-[var(--danger)] border-[var(--danger-soft)]', text: '暂不建议起诉' },
}

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
          grid: { left: 48, right: 24, top: 24, bottom: 40 },
          xAxis: {
            name: '法律可行性', min: 0, max: 100,
            nameTextStyle: { color: c.text },
            axisLine: { lineStyle: { color: c.border } },
            axisLabel: { color: c.muted },
            splitLine: { lineStyle: { color: c.border } },
          },
          yAxis: {
            name: '业务预期', min: 0, max: 100,
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
                  show: true, position: 'insideEndTop', fontSize: 10,
                  color: c.muted, formatter: `${mid}`,
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
      <div className="w-full h-72 flex items-center justify-center text-muted text-sm">
        法律可行性或业务预期尚未产出分数，暂不输出矩阵定位
      </div>
    )
  }
  return <div ref={ref} className="w-full h-72" />
}

/** 单个维度条：ok / failed / stale 三态 */
function DimBar({ label, dim }: { label: string; dim: any }) {
  const score = dim?.result?.score
  const status = dim?.status ?? 'waiting'
  const stale = status === 'stale'
  return (
    <div>
      <div className="flex items-center gap-3">
        <span className="text-sm w-20 shrink-0">{label}</span>
        <div className="flex-1 h-2 bg-surface rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full ${
              stale ? 'bg-[var(--warning)]' : status === 'failed' ? 'bg-[var(--danger)]' : 'bg-fg'
            }`}
            style={{ width: `${score ?? 0}%` }}
          />
        </div>
        <span className="text-sm w-16 text-right shrink-0">
          {stale ? (
            <span className="text-[var(--warning)] text-xs">失效</span>
          ) : status === 'failed' ? (
            <span className="text-[var(--danger)] text-xs">失败</span>
          ) : (
            score ?? '—'
          )}
        </span>
      </div>
      {stale && dim?.stale_reason && (
        <div className="ml-[92px] mt-1 text-[11px] text-[var(--warning)]/80">
          {dim.stale_reason} —— 结果已过期，建议重跑后采信
        </div>
      )}
    </div>
  )
}

export default function DecisionDashboard() {
  const { id } = useParams<{ id: string }>()
  const [data, setData] = useState<any>(null)
  const [error, setError] = useState('')
  const [busyNode, setBusyNode] = useState<string>('')

  const reload = () => {
    if (id) api.result(id).then(setData).catch((e) => setError(String(e)))
  }
  useEffect(reload, [id])

  const rerun = async (node: string, guidance: string): Promise<RerunResult> => {
    if (!id) throw new Error('缺少案件 ID')
    setBusyNode(node)
    try {
      const r = await api.rerun(id, node, guidance)
      reload()
      return { effect: r.effect ?? '', stale_nodes: r.stale_nodes ?? [] }
    } finally {
      setBusyNode('')
    }
  }

  if (error) return <div className="bg-[var(--danger-soft)] text-[var(--danger)] rounded-lg p-4 text-sm">{error}</div>
  if (!data) return <div className="text-muted text-sm">加载中…</div>

  const { scores, confidence, recommendation, evidence, red_flags, dimension_results,
          defendant_profile, thresholds, goal_type } = data
  const level = LEVEL_STYLE[recommendation?.level] ?? LEVEL_STYLE.yellow
  // 缺失/失效维度保持 null，不要静默归 0：后端已对 NaN 做「missing≠0」处理，
  // 前端若写成 ?? 0 会和「评估未完成」的结论自相矛盾，并把失效维度错误定位到 (0,70)。
  const legal = scores?.legal_feasibility ?? null
  const business = scores?.business_expectation ?? null
  const final = scores?.final
  const mid = thresholds?.quadrant_mid ?? 78
  const go = thresholds?.go ?? 78
  const patch = thresholds?.patch ?? 62

  const bizDims = BUSINESS_DIMS[goal_type] ?? BUSINESS_DIMS['要钱']
  const staleCount = Object.values(dimension_results ?? {})
    .filter((d: any) => d?.status === 'stale').length

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <h1 className="text-xl font-medium">决策仪表盘</h1>
        {staleCount > 0 && (
          <div className="text-xs text-[var(--warning)] bg-[var(--warning-soft)] border border-[var(--warning-soft)] rounded-lg px-3 py-1.5">
            {staleCount} 个维度结果已失效（上游被重跑），请重跑后采信
          </div>
        )}
      </div>

      {/* 结论 banner */}
      <div className={`rounded-xl border p-5 ${level.cls}`}>
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <div className="text-lg font-medium">{recommendation?.recommendation ?? '待评估'}</div>
            <div className="text-sm mt-1 opacity-80">{recommendation?.reason}</div>
            <div className="text-xs mt-2 opacity-60">
              档位：≥ {go} 优先启动 · {patch}–{go - 1} 补充短板后启动 · &lt; {patch} 暂缓
              {thresholds?.power_mean_p != null && `（分层幂平均 p=${thresholds.power_mean_p}）`}
            </div>
          </div>
          <div className="text-right">
            <div className="text-3xl font-semibold">{final ?? '—'}</div>
            <div className="text-xs opacity-70">主诉决策分</div>
            <div className="text-[11px] opacity-50 mt-0.5">
              法律 {legal ?? '—'} 与 业务 {business ?? '—'} 的均衡水平
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* 二维矩阵 */}
        <div className="lg:col-span-2 bg-surface rounded-xl border border-line p-5">
          <h2 className="text-sm font-medium mb-2">二维决策矩阵</h2>
          {final != null ? (
            <QuadrantChart legal={legal} business={business} mid={mid} />
          ) : (
            <div className="h-72 flex items-center justify-center text-muted text-sm">
              评估未完成，暂不输出矩阵定位
            </div>
          )}
        </div>

        {/* 置信度 + 回款 */}
        <div className="space-y-5">
          <div className="bg-surface rounded-xl border border-line p-5">
            <h2 className="text-sm font-medium">置信度（独立输出，不参与均衡）</h2>
            <div className="mt-3 text-3xl font-semibold">{confidence ?? '—'}%</div>
            <p className="text-xs text-muted mt-2">
              由证据完整度（{evidence?.completeness ?? 0}%）决定。
              {confidence != null && confidence < 50 && ' 置信度偏低，建议先补证再决策。'}
            </p>
          </div>

          {defendant_profile?.recovery_ability != null && (
            <div className="bg-surface rounded-xl border border-line p-5">
              <h2 className="text-sm font-medium">回款能力（被告偿付能力）</h2>
              <div className="mt-3 text-3xl font-semibold">
                {defendant_profile.recovery_ability}
                <span className="text-sm text-muted font-normal"> / 100</span>
              </div>
              <div className="mt-2 space-y-1 text-xs">
                {(defendant_profile.metrics?.red_flags ?? []).map((f: string) => (
                  <div key={f} className="text-[var(--danger)]">▼ {f}</div>
                ))}
                {(defendant_profile.metrics?.green_flags ?? []).map((f: string) => (
                  <div key={f} className="text-[var(--success)]">▲ {f}</div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* 法律可行性子维度 + 节点级重跑 */}
        <div className="bg-surface rounded-xl border border-line p-5">
          <h2 className="text-sm font-medium mb-3">法律可行性子维度</h2>
          <div className="space-y-3">
            {LEGAL_DIMS.map(({ key, label }) => (
              <DimBar key={key} label={label} dim={dimension_results?.[key]} />
            ))}
            <div className="text-xs text-muted pt-1">
              对抗检验修正系数：{data.correction_coeff ?? 1.0}
              {data.correction_coeff && data.correction_coeff !== 1 && '（模拟法庭已回写）'}
            </div>
          </div>
          <div className="mt-3 pt-3 border-t border-line space-y-2">
            {LEGAL_DIMS.map(({ key, label }) => (
              <RerunControl
                key={key}
                compact
                label={label}
                busy={busyNode !== ''}
                hint={key === 'rights'
                  ? '重跑后「侵权认定」将标记失效（其结论依赖权利基础），决策合成立即重算。'
                  : '重跑后决策合成立即重算；法律可行性三维度按分层幂平均重新聚合。'}
                onRerun={(g) => rerun(key, g)}
              />
            ))}
          </div>
        </div>

        {/* 业务预期子维度 */}
        <div className="bg-surface rounded-xl border border-line p-5">
          <h2 className="text-sm font-medium mb-1">业务预期子维度</h2>
          <p className="text-[11px] text-muted mb-3">
            {goal_type === '要钱' ? '要钱路径：判赔规模与回款能力的均衡水平' : '要名路径：判例价值'}
          </p>
          <div className="space-y-3">
            {bizDims.map(({ key, label }) => (
              <DimBar key={key} label={label} dim={dimension_results?.[key]} />
            ))}
          </div>
          <RerunControl
            label="业务预期"
            busy={busyNode !== ''}
            hint="重跑将同时重算判赔规模、回款能力（或判例价值），并瞬时重算决策合成。"
            onRerun={(g) => rerun('business', g)}
          />
        </div>

        {/* 硬门禁 */}
        <div className="bg-surface rounded-xl border border-line p-5">
          <h2 className="text-sm font-medium mb-3">红线检查</h2>
          <div className="space-y-2">
            {(red_flags ?? []).map((r: any, i: number) => (
              <div key={i} className="flex items-start gap-2 text-sm">
                <span className={`mt-0.5 w-2 h-2 rounded-full shrink-0 ${
                  r.severity === 'block' ? 'bg-[var(--danger)]' : r.severity === 'warning' ? 'bg-[var(--warning-soft)]0' : 'bg-[var(--success-soft)]0'
                }`} />
                <div>
                  <span className="font-medium">{r.rule_name}</span>
                  <span className="text-muted ml-2 text-xs">{r.reason}</span>
                </div>
              </div>
            ))}
            {!(red_flags ?? []).length && (
              <p className="text-sm text-muted">暂无红线检查结果</p>
            )}
          </div>
        </div>
      </div>

      {/* 证据缺口清单 */}
      <div className="bg-surface rounded-xl border border-line p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium">
            证据缺口清单（{evidence?.gap_list?.length ?? 0} 项待补）
          </h2>
          <RerunControl
            label="证据盘点"
            busy={busyNode !== ''}
            hint="补齐证据后可重跑证据盘点；其下游的权利基础、侵权认定、诉讼程序、业务预期与决策合成都会被标记失效。"
            onRerun={(g) => rerun('evidence_review', g)}
          />
        </div>
        {evidence?.gap_list?.length ? (
          <div className="space-y-2">
            {evidence.gap_list.map((g: any) => (
              <div key={g.id} className="flex items-start gap-3 text-sm border-b border-line pb-2">
                <span className={`text-xs px-2 py-0.5 rounded-full shrink-0 ${
                  g.status === 'missing' ? 'bg-[var(--danger-soft)] text-[var(--danger)]' : 'bg-[var(--warning-soft)] text-[var(--warning)]'
                }`}>
                  {g.status === 'missing' ? '缺失' : '不足'}
                </span>
                <div>
                  <div>{g.suggestion}</div>
                  <div className="text-xs text-muted mt-0.5">{g.basis} · {g.reason}</div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted">证据准备充足，无明显缺口</p>
        )}
        {evidence?.extra_evidence?.length > 0 && (
          <div className="mt-3 pt-3 border-t border-line">
            <div className="text-xs font-medium text-muted mb-1">清单外发现（AI 识别）</div>
            {evidence.extra_evidence.map((e: any, i: number) => (
              <div key={i} className="text-sm text-muted">· {e.name}：{e.value}</div>
            ))}
          </div>
        )}
      </div>

      {/* 下一步动作 */}
      <div className="flex flex-wrap gap-3">
        <button
          onClick={() => { window.location.href = `/cases/${id}/moot` }}
          className="bg-brand text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-fg transition-colors"
        >
          启动模拟法庭（压力测试）
        </button>
        <a
          href={`/cases/${id}/report`}
          className="border border-line text-fg px-4 py-2 rounded-lg text-sm hover:bg-surface transition-colors"
        >
          查看决策备忘录
        </a>
      </div>
    </div>
  )
}
