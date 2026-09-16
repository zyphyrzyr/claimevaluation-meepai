import { useMemo, useState } from 'react'
import type { EvalEvent } from '../api'
import type { NodeState } from '../lib/tiers'
import { cn } from '../lib/utils'
import { StatusDot } from './ui/StatusDot'

/**
 * 评估过程时间线。
 *
 * 背景：后端原先只推 node_started / node_finished 两种粒度，界面上一个节点从开始到
 * 结束只能显示「计算中…」，用户看不到任何「在做什么」。现在后端会推 node_step
 * （节点内部步骤）与 mcp_call（企查查等外部调用），这里把它们按节点归组呈现：
 * 进行中的节点自动展开，已完成的收成「一句话结论 + 耗时」一行，可点开回看细节。
 *
 * 刻意不做成一闪而过的 toast：评估是几十秒级的长任务，用户需要的是「能回看的过程」，
 * 而不是「错过了就没了」的提示。
 */

export interface TraceItem {
  kind: 'step' | 'mcp'
  text: string
  detail?: string
  status?: string
  vendor?: string
}

export interface TraceGroup {
  node: string
  label: string
  status: NodeState
  durationMs?: number
  summary?: string
  items: TraceItem[]
}

/** 把 SSE 事件流折叠成「按节点归组」的过程记录 */
export function buildTrace(events: EvalEvent[], states: Record<string, NodeState>): TraceGroup[] {
  const groups: TraceGroup[] = []
  const index = new Map<string, TraceGroup>()

  const ensure = (node: string, label: string): TraceGroup => {
    let g = index.get(node)
    if (!g) {
      g = { node, label, status: (states[node] as NodeState) ?? 'waiting', items: [] }
      index.set(node, g)
      groups.push(g)
    }
    if (label) g.label = label
    return g
  }

  for (const e of events) {
    if (e.event === 'recall_done') {
      // 后端的 recall_done 是「无节点」事件（node 为空串），label 里带的是完整句子。
      // 直接用 label 当分组标题会变成「已自动召回 8 条…」这种句子做标题，所以固定一个组名，
      // 句子本身放到明细行里。
      const g = ensure('__recall__', '参考材料准备')
      g.status = (e.status as NodeState) ?? 'ok'
      g.items.push({
        kind: 'step',
        text: e.label || '已完成参考材料召回',
        status: e.status,
      })
    } else if (e.event === 'node_started') {
      ensure(e.node, e.label).status = 'running'
    } else if (e.event === 'node_step' || e.event === 'mcp_call') {
      ensure(e.node, e.label).items.push({
        kind: e.event === 'mcp_call' ? 'mcp' : 'step',
        text: e.text ?? '',
        detail: e.detail,
        status: e.status,
        vendor: e.vendor,
      })
    } else if (e.event === 'node_finished') {
      const g = ensure(e.node, e.label)
      g.status = (e.status as NodeState) ?? 'ok'
      g.durationMs = e.duration_ms
      g.summary = e.summary
    }
  }

  // 进行中的节点收尾：状态以父组件 states 为准（node_finished 可能还没到）
  for (const g of groups) {
    const s = states[g.node]
    if (s) g.status = s as NodeState
  }
  return groups
}

const STATUS_TEXT: Record<string, string> = {
  waiting: '等待中',
  running: '进行中',
  ok: '已完成',
  failed: '出错',
  blocked: '已拦截',
  partial: '部分完成',
  stale: '待重跑',
}

function fmtDuration(ms?: number): string {
  if (!ms || ms < 0) return ''
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function EvalTrace({
  events,
  states,
  phase,
}: {
  events: EvalEvent[]
  states: Record<string, NodeState>
  phase: 'prep' | 'running' | 'done'
}) {
  const groups = useMemo(() => buildTrace(events, states), [events, states])
  const [collapsed, setCollapsed] = useState<boolean | null>(null)
  const [openNodes, setOpenNodes] = useState<Record<string, boolean>>({})

  const runningNode = groups.find((g) => g.status === 'running')?.node ?? ''
  // 进行中默认展开面板；跑完默认收起（想回看再点开），用户手动操作后不再自动切换
  const isCollapsed = collapsed ?? phase === 'done'

  const total = groups.reduce((n, g) => n + g.items.length, 0)
  const totalMs = groups.reduce((n, g) => n + (g.durationMs ?? 0), 0)

  if (groups.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-line bg-surface px-5 py-4 text-sm text-muted">
        这次评估没有捕获到过程记录（可能是在其它页面启动的，或刚才刷新过页面）。
        重新跑一次评估就能看到它逐步在做什么。
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-line bg-surface overflow-hidden">
      <button
        type="button"
        onClick={() => setCollapsed(!isCollapsed)}
        className="w-full flex items-center gap-3 px-5 py-3.5 text-left hover:bg-canvas transition-colors"
      >
        <span className="text-sm font-medium text-fg">评估过程</span>
        {phase === 'running' ? (
          <span className="text-xs text-[var(--info)]">进行中 · 已在 {groups.length} 个环节留下记录</span>
        ) : (
          <span className="text-xs text-muted">
            共 {total} 步
            {totalMs > 0 && ` · 累计耗时 ${fmtDuration(totalMs)}`}
          </span>
        )}
        <span className="flex-1" />
        <span className="text-xs text-muted">{isCollapsed ? '展开回看' : '收起'}</span>
      </button>

      {!isCollapsed && (
        <div className="px-5 pb-4 space-y-1">
          {groups.map((g) => {
            const open = openNodes[g.node] ?? (phase === 'running' && g.node === runningNode)
            return (
              <div key={g.node} className="border-t border-line pt-3 first:border-t-0">
                <button
                  type="button"
                  onClick={() => setOpenNodes((s) => ({ ...s, [g.node]: !open }))}
                  className="w-full flex items-center gap-2.5 text-left"
                >
                  <StatusDot status={g.status} />
                  <span className="text-sm font-medium text-fg">{g.label}</span>
                  <span className="text-xs text-muted">{STATUS_TEXT[g.status] ?? g.status}</span>
                  <span className="flex-1" />
                  {g.durationMs != null && g.durationMs > 0 && (
                    <span className="text-xs text-muted">{fmtDuration(g.durationMs)}</span>
                  )}
                </button>

                {!open && g.summary && (
                  <p className="text-sm text-muted mt-1.5 ml-[18px]">{g.summary}</p>
                )}

                {open && (
                  <ul className="mt-2 ml-[18px] space-y-1.5">
                    {g.items.map((it, i) => (
                      <li key={i} className="text-sm">
                        <div className="flex gap-2 items-start">
                          {it.kind === 'mcp' ? (
                            <span className="shrink-0 mt-0.5 px-1.5 py-0.5 rounded border border-line text-[11px] text-muted">
                              {it.vendor ?? '外部数据'}
                            </span>
                          ) : (
                            <span
                              className={cn(
                                'shrink-0 mt-[7px] w-1.5 h-1.5 rounded-full',
                                it.status === 'failed'
                                  ? 'bg-[var(--danger)]'
                                  : it.status === 'blocked'
                                    ? 'bg-[var(--danger)]'
                                    : it.status === 'warning'
                                      ? 'bg-[var(--warning)]'
                                      : 'bg-line',
                              )}
                            />
                          )}
                          <span className={cn('text-muted', it.kind === 'mcp' && 'text-fg')}>{it.text}</span>
                        </div>
                        {it.detail && (
                          <p className="text-xs text-muted mt-0.5 ml-[14px] whitespace-pre-line">
                            {it.detail}
                          </p>
                        )}
                      </li>
                    ))}
                    {g.items.length === 0 && (
                      <li className="text-sm text-muted">（这一步没有留下细节）</li>
                    )}
                  </ul>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
