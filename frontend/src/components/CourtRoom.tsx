import { useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '../lib/utils'
import type { MootRound } from '../api'

/**
 * 庭审现场：原告居左、法官居中、被告居右的三栏剧场（浅色，与评估详情同底色）。
 *
 * 两条设计约束：
 *  1. **不切全局主题**。早期版本由页面 setTheme('theater') 把整个 <html> 刷成暗色，
 *     视觉上像跳去了另一个站点。现在庭审区与评估详情同用 token 浅色，
 *     剧场感靠「席位 + 发言气泡 + 进度条」表达，不靠反色。
 *  2. **状态全部受控**。轮次、进行中、是否已中止都由父级（CaseWorkbench）持有，
 *     这里只渲染——切换评估轴会卸载子组件，状态放在本组件里会整场丢。
 */

/** 七轮发言的既定顺序（后端 procedure 固定）：下一轮还没到时，用它标出「谁在说话」 */
export const SPEAK_ORDER: { role: string; step: number }[] = [
  { role: 'plaintiff', step: 1 },
  { role: 'defendant', step: 2 },
  { role: 'plaintiff', step: 3 },
  { role: 'defendant', step: 3 },
  { role: 'plaintiff', step: 4 },
  { role: 'defendant', step: 4 },
  { role: 'judge', step: 5 },
]
export const MOOT_TOTAL_ROUNDS = SPEAK_ORDER.length

/** 五个庭审步骤（后端 STEP_NAMES 同口径，界面上带序号，方便与进度条对上） */
export const MOOT_STEPS: { n: number; name: string }[] = [
  { n: 1, name: '开庭陈述' },
  { n: 2, name: '被告答辩' },
  { n: 3, name: '举证质证' },
  { n: 4, name: '法庭辩论' },
  { n: 5, name: '法官归纳' },
]

const ROLE_META: Record<
  string,
  {
    title: string
    seat: string
    avatar: string
    accent: string
    avatarCls: string
    nameCls: string
    from: { x?: number; y?: number; scale?: number }
  }
> = {
  plaintiff: {
    title: '原告',
    seat: '原告席',
    avatar: '原',
    accent: 'var(--info)',
    avatarCls: 'bg-[var(--info-soft)] text-[var(--info)]',
    nameCls: 'text-[var(--info)]',
    from: { x: -28 },
  },
  defendant: {
    title: '被告',
    seat: '被告席',
    avatar: '被',
    accent: 'var(--warning)',
    avatarCls: 'bg-[var(--warning-soft)] text-[var(--warning)]',
    nameCls: 'text-[var(--warning)]',
    from: { x: 28 },
  },
  judge: {
    title: '法官',
    seat: '审判席',
    avatar: '审',
    accent: 'var(--brand)',
    avatarCls: 'bg-brand-soft text-fg',
    nameCls: 'text-fg',
    from: { y: -16, scale: 0.97 },
  },
}

/**
 * 首句当「主张」加粗：庭审发言普遍是「先抛结论、再展开理由」，
 * 把第一句单独拎出来，扫一眼就能抓住这一轮在争什么；剩下的正文用更松的行距铺开。
 * 只有一句话时整段都当主张，不强行拆。
 */
function splitClaim(content: string): { claim: string; rest: string } {
  const m = content.match(/^(.{6,60}?[。！？!?])\s*([\s\S]*)$/)
  if (!m) return { claim: '', rest: content }
  const rest = m[2].trim()
  if (!rest) return { claim: content, rest: '' }
  return { claim: m[1], rest: m[2] }
}

/**
 * 法官节点要求结构化输出，模型偶尔会返回解析不了的 JSON，此时 round 的 content
 * 就是那个 dict 的字符串形式（`{error: JSON 解析失败, raw: '...'}`）。
 * 把它当成一句「正常发言」铺开，既难看又误导（用户会以为法官真说了这些）。
 * 识别出来单独降级呈现——仍然如实展示，只是不冒充发言。
 */
function isRawDump(content: string): boolean {
  const s = (content ?? '').trim()
  return s.startsWith('{') && /error['"]?\s*[:：]/.test(s.slice(0, 160))
}

function SpeakingDots({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-1.5 pt-1 text-[11px] text-muted">
      <span className="flex items-end gap-[3px]">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="court-dot w-1 h-1 rounded-full bg-fg"
            style={{ animationDelay: `${i * 160}ms` }}
          />
        ))}
      </span>
      {label}
    </div>
  )
}

export default function CourtRoom({
  rounds,
  running,
  stopped = false,
}: {
  rounds: MootRound[]
  running: boolean
  /** 本次庭审被中止：不再等下一轮，但已说过的轮次保留在场上 */
  stopped?: boolean
}) {
  const leftRef = useRef<HTMLDivElement>(null)
  const centerRef = useRef<HTMLDivElement>(null)
  const rightRef = useRef<HTMLDivElement>(null)

  // 新回合入场后，三栏各自滚动到底部
  useEffect(() => {
    const scroll = (el?: HTMLDivElement | null) => {
      if (!el) return
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    }
    scroll(leftRef.current)
    scroll(centerRef.current)
    scroll(rightRef.current)
  }, [rounds.length])

  const grouped: Record<string, MootRound[]> = {
    plaintiff: rounds.filter((r) => r.role === 'plaintiff'),
    defendant: rounds.filter((r) => r.role === 'defendant'),
    judge: rounds.filter((r) => r.role === 'judge'),
  }

  // 当前进行到的步骤：已发言轮次里最大的 step；一轮未出时是 0（进度条全灰）
  const currentStep = rounds.reduce((mx, r) => Math.max(mx, r.step ?? 0), 0)
  // 正在说话的人：下一轮的发言方。最后一轮说完（rounds 已满）时不再显示
  const next = running && rounds.length < MOOT_TOTAL_ROUNDS ? SPEAK_ORDER[rounds.length] : null

  const Seat = ({
    role,
    items,
    scrollRef,
  }: {
    role: string
    items: MootRound[]
    scrollRef: React.RefObject<HTMLDivElement>
  }) => {
    const meta = ROLE_META[role] ?? ROLE_META.plaintiff
    const speaking = next?.role === role
    return (
      <div className="flex flex-col min-h-0 rounded-xl border border-line bg-canvas overflow-hidden">
        {/* 席位条：谁坐在这 + 说了几轮 */}
        <div className="shrink-0 px-3.5 py-2.5 border-b border-line flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span
              className={cn(
                'shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-medium',
                meta.avatarCls,
              )}
            >
              {meta.avatar}
            </span>
            <span className="text-xs font-medium text-fg truncate">{meta.title}</span>
            <span className="text-[10px] text-muted truncate hidden sm:inline">· {meta.seat}</span>
          </div>
          <span className={cn('shrink-0 text-[10px] tabular-nums', meta.nameCls)}>
            {items.length > 0 ? `${items.length} 轮` : '未发言'}
          </span>
        </div>

        {/* 发言区：空态压到 150px，别让未开庭的页面顶出一大片空白 */}
        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto p-3 space-y-2.5 min-h-0"
          style={{ maxHeight: 420 }}
        >
          <AnimatePresence initial={false}>
            {items.map((r, i) => {
              const raw = isRawDump(r.content ?? '')
              const { claim, rest } = raw ? { claim: '', rest: '' } : splitClaim(r.content ?? '')
              return (
                <motion.div
                  key={`${role}-${i}`}
                  initial={{ opacity: 0, ...meta.from }}
                  animate={{ opacity: 1, x: 0, y: 0, scale: 1 }}
                  transition={{ type: 'spring', stiffness: 260, damping: 24 }}
                  className="rounded-xl border border-line bg-surface px-3.5 py-3"
                  style={{ borderLeftWidth: 3, borderLeftColor: meta.accent }}
                >
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className={cn('text-[11px] font-medium', meta.nameCls)}>
                      {r.role_name || meta.title}
                    </span>
                    <span className="text-[10px] text-muted">{r.step_name}</span>
                  </div>
                  {claim && (
                    <div className="text-[13px] font-medium text-fg leading-relaxed">{claim}</div>
                  )}
                  {rest && (
                    <div className="mt-1 text-[13px] leading-[1.9] text-muted whitespace-pre-wrap">
                      {rest}
                    </div>
                  )}
                  {raw && (
                    <div className="mt-1.5 rounded-lg border border-line bg-canvas px-3 py-2">
                      <div className="text-[10px] text-muted mb-1">
                        模型原始返回（未能解析成结构化归纳）
                      </div>
                      <pre className="text-[11px] leading-relaxed text-muted whitespace-pre-wrap break-all font-mono max-h-40 overflow-auto">
                        {r.content}
                      </pre>
                    </div>
                  )}
                </motion.div>
              )
            })}
          </AnimatePresence>

          {items.length === 0 && (
            <div className="h-[150px] flex items-center justify-center text-xs text-muted">
              等待发言
            </div>
          )}

          {speaking && <SpeakingDots label="发言中…" />}
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-2xl border border-line bg-canvas overflow-hidden">
      {/* 顶部：五步进度 + 轮次计数。进度的「当前步」= 已发言轮次里最大的 step */}
      <div className="px-4 pt-4 pb-3 border-b border-line">
        <div className="flex items-center justify-between gap-3 mb-2.5">
          <h2 className="text-sm font-medium text-fg">庭审现场</h2>
          <div className="flex items-center gap-2 text-xs text-muted">
            <span className="tabular-nums">
              第 {rounds.length} / {MOOT_TOTAL_ROUNDS} 轮
            </span>
            {running && (
              <span className="flex items-end gap-[3px]" aria-label="庭审进行中">
                {[0, 1, 2].map((i) => (
                  <span
                    key={i}
                    className="court-dot w-1.5 h-1.5 rounded-full bg-fg"
                    style={{ animationDelay: `${i * 160}ms` }}
                  />
                ))}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-start gap-1.5">
          {MOOT_STEPS.map((s) => {
            // 当前步只在「还在跑」时才闪；跑完了第 5 步也算已完成，否则最后一步会永远在闪
            const state =
              currentStep > s.n
                ? 'done'
                : currentStep === s.n && currentStep > 0
                  ? running
                    ? 'active'
                    : 'done'
                  : 'todo'
            return (
              <div key={s.n} className="flex-1 min-w-0">
                <div
                  className={cn(
                    'h-1 rounded-full transition-colors',
                    state === 'done' && 'bg-fg',
                    state === 'active' && 'bg-fg/50 animate-pulse',
                    state === 'todo' && 'bg-line',
                  )}
                />
                <div
                  className={cn(
                    'mt-1.5 text-[10px] leading-none truncate',
                    state === 'todo' ? 'text-muted' : 'text-fg',
                  )}
                >
                  {s.n} {s.name}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {stopped && (
        <div className="mx-4 mt-3 rounded-lg border border-[var(--danger-line)] bg-[var(--danger-soft)] px-3 py-2 text-xs text-[var(--danger)]">
          本次庭审已中止：当前这一轮说完后即停止，已说内容保留在场上，但不会回写修正系数、也不会记入评分。
        </div>
      )}

      {/* 三栏剧场：法官居中，两侧原被告对峙 */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.05fr_1fr] gap-3 p-4">
        <Seat role="plaintiff" items={grouped.plaintiff} scrollRef={leftRef} />
        <Seat role="judge" items={grouped.judge} scrollRef={centerRef} />
        <Seat role="defendant" items={grouped.defendant} scrollRef={rightRef} />
      </div>
    </div>
  )
}
