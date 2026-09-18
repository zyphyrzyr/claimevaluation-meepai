import { useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '../lib/utils'
import type { MootRound } from '../api'

/**
 * 庭审现场（浅色，与评估详情同底色）。
 *
 * 布局：顶部「席位条」把身份只画一次（原告/法官/被告，当前发言方高亮），
 * 下面一条**单一宽时间线**按既定顺序排开七轮发言——彻底取代早期「三栏各自滚动」。
 * 这样：窄→单列不再三等分；长→一条自上而下扫读，不再三道高瘦滚动条；
 * 挤→身份在顶部、逐轮是带「第 n 轮 · 步骤 · 角色」标签的独立卡片。
 *
 * 两条设计约束：
 *  1. 不切全局主题（与早期反色剧场不同），剧场感靠席位高亮 + 角色色 + 入场动画。
 *  2. 状态全部受控：轮次由父级（CaseWorkbench）持有，这里只渲染。
 *     rounds = 已揭示（显示）的轮次；rawCount = 模型已生成的全部轮次，
 *     二者之差用来提示「模型已生成 X/7 · 正在陆续显示」，把播放与生成解耦。
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

/** 席位条三个角色的顺序：原告居左、法官居中、被告居右，呼应舞台对峙 */
const SEAT_ORDER = ['plaintiff', 'judge', 'defendant']

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
  rawCount,
}: {
  rounds: MootRound[]
  running: boolean
  /** 本次庭审被中止：不再等下一轮，但已说过的轮次保留在场上 */
  stopped?: boolean
  /** 模型已生成的全部轮次数（含尚未揭示的）；用于「正在陆续显示」提示 */
  rawCount?: number
}) {
  const scrollRef = useRef<HTMLDivElement>(null)

  // 新回合入场后，时间线滚动到底部
  useEffect(() => {
    if (!scrollRef.current) return
    scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [rounds.length])

  // 当前进行到的步骤：已显示轮次里最大的 step；一轮未出时是 0（进度条全灰）
  const currentStep = rounds.reduce((mx, r) => Math.max(mx, r.step ?? 0), 0)
  // 正在说话的人：下一轮的发言方。最后一轮说完（rounds 已满）时不再显示
  const next = running && rounds.length < MOOT_TOTAL_ROUNDS ? SPEAK_ORDER[rounds.length] : null
  // 是否还有"正在发言"的下一轮（时间线底部的发言指示器用）
  const speaking = next != null
  // 每个角色已显示的轮数（席位条上的「n 轮」）
  const countOf = (role: string) => rounds.filter((r) => r.role === role).length

  return (
    <div className="rounded-2xl border border-line bg-canvas overflow-hidden">
      {/* 顶部：五步进度 + 轮次计数。进度的「当前步」= 已显示轮次里最大的 step */}
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
        {/* 播放滞后提示：模型已生成的比显示的多，说明正在缓冲播放 */}
        {running && rawCount != null && rawCount > rounds.length && (
          <div className="mt-2 text-[10px] text-muted">
            模型已生成 {rawCount}/{MOOT_TOTAL_ROUNDS} · 正在陆续显示
          </div>
        )}
      </div>

      {stopped && (
        <div className="mx-4 mt-3 rounded-lg border border-[var(--danger-line)] bg-[var(--danger-soft)] px-3 py-2 text-xs text-[var(--danger)]">
          本次庭审已中止：当前这一轮说完后即停止，已说内容保留在场上，但不会回写修正系数、也不会记入评分。
        </div>
      )}

      {/* 席位条：身份只出现一次，当前发言方高亮（实心底色），并显示该角色已说轮数 */}
      <div className="flex gap-2 px-4 pt-3">
        {SEAT_ORDER.map((role) => {
          const meta = ROLE_META[role]
          const speaking = next?.role === role
          const c = countOf(role)
          return (
            <div
              key={role}
              className={cn(
                'flex-1 flex items-center gap-2 px-3 py-2 rounded-xl border transition-colors',
                speaking ? 'border-fg bg-surface' : 'border-line',
              )}
            >
              <span
                className={cn(
                  'shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-medium',
                  meta.avatarCls,
                )}
              >
                {meta.avatar}
              </span>
              <div className="min-w-0">
                <div className={cn('text-xs font-medium truncate', meta.nameCls)}>{meta.title}</div>
                <div className="text-[10px] text-muted">
                  {speaking ? '发言中' : c > 0 ? `${c} 轮` : '未发言'}
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* 中央剧本：单一宽时间线，按既定顺序自上而下铺开所有已揭示轮次 */}
      <div
        ref={scrollRef}
        className="px-4 py-3 max-h-[460px] overflow-y-auto space-y-2.5 min-h-0"
      >
        <AnimatePresence initial={false}>
          {rounds.map((r, i) => {
            const meta = ROLE_META[r.role] ?? ROLE_META.plaintiff
            const raw = isRawDump(r.content ?? '')
            const { claim, rest } = raw ? { claim: '', rest: '' } : splitClaim(r.content ?? '')
            return (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ type: 'spring', stiffness: 260, damping: 24 }}
                className="rounded-xl border border-line bg-surface px-3.5 py-3"
                style={{ borderLeftWidth: 3, borderLeftColor: meta.accent }}
              >
                <div className="flex items-center gap-2 mb-1.5">
                  <span className={cn('text-[11px] font-medium', meta.nameCls)}>
                    {r.role_name || meta.title}
                  </span>
                  <span className="text-[10px] text-muted">
                    第 {i + 1} 轮 · {r.step_name}
                  </span>
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

        {rounds.length === 0 && (
          <div className="py-10 text-center text-xs text-muted">等待开庭…</div>
        )}

        {speaking && <SpeakingDots label="发言中…" />}
      </div>
    </div>
  )
}
