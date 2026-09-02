import { useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '../lib/utils'
import type { MootRound } from '../api'

/**
 * 模拟法庭剧场模式（P3）
 * - 原告居左、被告居右、法官居中，三栏剧场布局
 * - 每回合按角色从对应方向滑入（framer-motion）
 * - 暗色 token 主题，由外层 MootCourt 切 data-theme="theater" 控制
 */

const ROLE_META: Record<
  string,
  {
    title: string
    badge: string
    border: string
    soft: string
    text: string
    from: { x?: number; y?: number; scale?: number }
  }
> = {
  plaintiff: {
    title: '原告',
    badge: 'bg-info text-white',
    border: 'border-info',
    soft: 'bg-[var(--info-soft)]',
    text: 'text-info',
    from: { x: -48 },
  },
  defendant: {
    title: '被告',
    badge: 'bg-warning text-white',
    border: 'border-warning',
    soft: 'bg-[var(--warning-soft)]',
    text: 'text-warning',
    from: { x: 48 },
  },
  judge: {
    title: '法官',
    badge: 'bg-brand text-white',
    border: 'border-brand',
    soft: 'bg-surface',
    text: 'text-fg',
    from: { y: -24, scale: 0.96 },
  },
}

export default function CourtRoom({
  rounds,
  running,
}: {
  rounds: MootRound[]
  running: boolean
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

  const Column = ({
    role,
    items,
    scrollRef,
  }: {
    role: string
    items: MootRound[]
    scrollRef: React.RefObject<HTMLDivElement>
  }) => {
    const meta = ROLE_META[role] ?? ROLE_META.plaintiff
    const isCenter = role === 'judge'
  return (
    <div className="flex flex-col min-h-0 rounded-xl border border-line bg-surface overflow-hidden">
        {/* Podium / Bench header */}
        <div className="shrink-0 px-4 py-3 border-b border-line bg-canvas flex items-center justify-between">
          <span className={cn('text-xs font-semibold px-2.5 py-1 rounded-full', meta.badge)}>
            {meta.title}
          </span>
          {running && items.length === 0 && (
            <span className="text-[10px] text-muted animate-pulse">准备中…</span>
          )}
        </div>

        {/* Scrollable round list */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-3 min-h-0">
          <AnimatePresence initial={false}>
            {items.map((r, i) => (
              <motion.div
                key={`${role}-${i}`}
                initial={{ opacity: 0, ...meta.from }}
                animate={{ opacity: 1, x: 0, y: 0, scale: 1 }}
                transition={{ type: 'spring', stiffness: 260, damping: 22 }}
                className={cn(
                  'rounded-xl border px-3.5 py-2.5 text-sm leading-relaxed whitespace-pre-wrap',
                  meta.border,
                  meta.soft,
                  meta.text,
                  isCenter ? 'text-center' : '',
                )}
              >
                <div className="flex items-center gap-2 mb-1 text-[10px] opacity-70">
                  <span className="font-medium">{r.role_name}</span>
                  <span className="text-muted">· {r.step_name}</span>
                </div>
                {r.content}
              </motion.div>
            ))}
          </AnimatePresence>

          {items.length === 0 && !running && (
            <div className="h-full flex items-center justify-center text-xs text-muted py-8">
              等待开庭
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-2xl border border-line bg-canvas p-5 min-h-[420px] max-h-[72vh] flex flex-col overflow-hidden shadow-lg">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-medium text-fg">庭审现场</h2>
        {running && (
          <div className="flex items-center gap-1.5 text-xs text-muted">
            <span className="w-1.5 h-1.5 rounded-full bg-brand animate-pulse" />
            <span className="w-1.5 h-1.5 rounded-full bg-brand animate-pulse [animation-delay:150ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-brand animate-pulse [animation-delay:300ms]" />
            庭审进行中
          </div>
        )}
      </div>

      {rounds.length === 0 && !running ? (
        <div className="flex-1 grid grid-cols-1 lg:grid-cols-3 gap-4">
          {(['plaintiff', 'judge', 'defendant'] as const).map((role) => {
            const meta = ROLE_META[role]
            return (
              <div
                key={role}
                className="flex flex-col rounded-xl border border-line bg-surface overflow-hidden"
              >
                <div className="px-4 py-3 border-b border-line bg-canvas">
                  <span className={cn('text-xs font-semibold px-2.5 py-1 rounded-full', meta.badge)}>
                    {meta.title}
                  </span>
                </div>
                <div className="flex-1 flex items-center justify-center text-xs text-muted py-10 animate-pulse">
                  等待开庭
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="flex-1 grid grid-cols-1 lg:grid-cols-3 gap-4 min-h-0">
          <Column role="plaintiff" items={grouped.plaintiff} scrollRef={leftRef} />
          <Column role="judge" items={grouped.judge} scrollRef={centerRef} />
          <Column role="defendant" items={grouped.defendant} scrollRef={rightRef} />
        </div>
      )}
    </div>
  )
}

