import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import * as echarts from 'echarts'
import { api } from '../api'

const LEVEL_STYLE: Record<string, { cls: string; text: string }> = {
  green: { cls: 'bg-green-50 text-green-800 border-green-200', text: '建议优先启动' },
  yellow: { cls: 'bg-amber-50 text-amber-800 border-amber-200', text: '补充短板后启动' },
  red: { cls: 'bg-red-50 text-red-800 border-red-200', text: '建议暂缓' },
  block: { cls: 'bg-red-100 text-red-900 border-red-300', text: '暂不建议起诉' },
}

function QuadrantChart({ legal, business }: { legal: number; business: number }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current)
    chart.setOption({
      grid: { left: 48, right: 24, top: 24, bottom: 40 },
      xAxis: {
        name: '法律可行性', min: 0, max: 100,
        splitLine: { lineStyle: { color: '#e5e7eb' } },
      },
      yAxis: {
        name: '业务预期', min: 0, max: 100,
        splitLine: { lineStyle: { color: '#e5e7eb' } },
      },
      series: [
        {
          type: 'scatter',
          symbolSize: 18,
          data: [[legal, business]],
          itemStyle: { color: '#d65938' },
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { color: '#0d1429', type: 'dashed', opacity: 0.3 },
            data: [{ xAxis: 50 }, { yAxis: 50 }],
            label: { show: false },
          },
          markArea: {
            silent: true,
            itemStyle: { color: 'rgba(214, 89, 56, 0.05)' },
            data: [[{ coord: [50, 50] }, { coord: [100, 100] }]],
          },
        },
      ],
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.dispose()
    }
  }, [legal, business])

  return <div ref={ref} className="w-full h-72" />
}

export default function DecisionDashboard() {
  const { id } = useParams<{ id: string }>()
  const [data, setData] = useState<any>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (id) api.result(id).then(setData).catch((e) => setError(String(e)))
  }, [id])

  if (error) return <div className="bg-red-50 text-red-700 rounded-lg p-4 text-sm">{error}</div>
  if (!data) return <div className="text-ink/40 text-sm">加载中…</div>

  const { scores, confidence, recommendation, evidence, red_flags, dimension_results, defendant_profile } = data
  const level = LEVEL_STYLE[recommendation?.level] ?? LEVEL_STYLE.yellow
  const legal = scores?.legal_feasibility ?? 0
  const business = scores?.business_expectation ?? 0
  const final = scores?.final

  const dims = [
    { key: 'rights', label: '权利基础' },
    { key: 'infringement', label: '侵权认定' },
    { key: 'procedure', label: '诉讼程序' },
  ]

  return (
    <div className="space-y-5">
      <h1 className="text-xl font-medium">决策仪表盘</h1>

      {/* 结论 banner */}
      <div className={`rounded-xl border p-5 ${level.cls}`}>
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <div className="text-lg font-medium">{recommendation?.recommendation ?? '待评估'}</div>
            <div className="text-sm mt-1 opacity-80">{recommendation?.reason}</div>
          </div>
          <div className="text-right">
            <div className="text-3xl font-semibold">{final ?? '—'}</div>
            <div className="text-xs opacity-70">主诉决策分（法律可行性 × 业务预期）</div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* 二维矩阵 */}
        <div className="lg:col-span-2 bg-white rounded-xl border border-ink/10 p-5">
          <h2 className="text-sm font-medium mb-2">二维决策矩阵</h2>
          {final != null ? (
            <QuadrantChart legal={legal} business={business} />
          ) : (
            <div className="h-72 flex items-center justify-center text-ink/40 text-sm">
              评估未完成，暂不输出矩阵定位
            </div>
          )}
        </div>

        {/* 置信度 + 回款 */}
        <div className="space-y-5">
          <div className="bg-white rounded-xl border border-ink/10 p-5">
            <h2 className="text-sm font-medium">置信度（独立输出，不参与乘法）</h2>
            <div className="mt-3 text-3xl font-semibold">{confidence ?? '—'}%</div>
            <p className="text-xs text-ink/50 mt-2">
              由证据完整度（{evidence?.completeness ?? 0}%）决定。
              {confidence != null && confidence < 50 && ' 置信度偏低，建议先补证再决策。'}
            </p>
          </div>

          {defendant_profile?.recovery_ability != null && (
            <div className="bg-white rounded-xl border border-ink/10 p-5">
              <h2 className="text-sm font-medium">回款能力（被告偿付能力）</h2>
              <div className="mt-3 text-3xl font-semibold">
                {defendant_profile.recovery_ability}
                <span className="text-sm text-ink/40 font-normal"> / 100</span>
              </div>
              <div className="mt-2 space-y-1 text-xs">
                {(defendant_profile.metrics?.red_flags ?? []).map((f: string) => (
                  <div key={f} className="text-red-600">▼ {f}</div>
                ))}
                {(defendant_profile.metrics?.green_flags ?? []).map((f: string) => (
                  <div key={f} className="text-green-600">▲ {f}</div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 维度分 + 红线 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="bg-white rounded-xl border border-ink/10 p-5">
          <h2 className="text-sm font-medium mb-3">法律可行性子维度</h2>
          <div className="space-y-3">
            {dims.map(({ key, label }) => {
              const d = dimension_results?.[key] ?? {}
              const score = d.result?.score
              return (
                <div key={key} className="flex items-center gap-3">
                  <span className="text-sm w-20">{label}</span>
                  <div className="flex-1 h-2 bg-ink-pale rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full ${
                        d.status === 'failed' ? 'bg-red-300' : 'bg-ink'
                      }`}
                      style={{ width: `${score ?? 0}%` }}
                    />
                  </div>
                  <span className="text-sm w-16 text-right">
                    {d.status === 'failed' ? <span className="text-red-600 text-xs">失败</span> : score ?? '—'}
                  </span>
                </div>
              )
            })}
            <div className="text-xs text-ink/40 pt-1">
              对抗检验修正系数：{data.correction_coeff ?? 1.0}（模拟法庭 P2 接入后产出）
            </div>
          </div>
        </div>

        <div className="bg-white rounded-xl border border-ink/10 p-5">
          <h2 className="text-sm font-medium mb-3">硬门禁（红线检查）</h2>
          <div className="space-y-2">
            {(red_flags ?? []).map((r: any, i: number) => (
              <div key={i} className="flex items-start gap-2 text-sm">
                <span className={`mt-0.5 w-2 h-2 rounded-full shrink-0 ${
                  r.severity === 'block' ? 'bg-red-600' : r.severity === 'warning' ? 'bg-amber-500' : 'bg-green-500'
                }`} />
                <div>
                  <span className="font-medium">{r.rule_name}</span>
                  <span className="text-ink/50 ml-2 text-xs">{r.reason}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 证据缺口清单 */}
      <div className="bg-white rounded-xl border border-ink/10 p-5">
        <h2 className="text-sm font-medium mb-3">
          证据缺口清单（{evidence?.gap_list?.length ?? 0} 项待补）
        </h2>
        {evidence?.gap_list?.length ? (
          <div className="space-y-2">
            {evidence.gap_list.map((g: any) => (
              <div key={g.id} className="flex items-start gap-3 text-sm border-b border-ink/5 pb-2">
                <span className={`text-xs px-2 py-0.5 rounded-full shrink-0 ${
                  g.status === 'missing' ? 'bg-red-50 text-red-700' : 'bg-amber-50 text-amber-700'
                }`}>
                  {g.status === 'missing' ? '缺失' : '不足'}
                </span>
                <div>
                  <div>{g.suggestion}</div>
                  <div className="text-xs text-ink/40 mt-0.5">{g.basis} · {g.reason}</div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-ink/50">证据准备充足，无明显缺口</p>
        )}
        {evidence?.extra_evidence?.length > 0 && (
          <div className="mt-3 pt-3 border-t border-ink/10">
            <div className="text-xs font-medium text-ink/60 mb-1">清单外发现（AI 识别）</div>
            {evidence.extra_evidence.map((e: any, i: number) => (
              <div key={i} className="text-sm text-ink/70">· {e.name}：{e.value}</div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
