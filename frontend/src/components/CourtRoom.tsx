import { useEffect, useRef } from 'react'
import type { MootRound } from '../api'

/**
 * 庭审直播组件：原告/被告/法官三色气泡
 * - plaintiff：左侧，蓝
 * - defendant：右侧，琥珀
 * - judge：通栏，深色
 */

const ROLE_STYLE: Record<string, { bubble: string; badge: string; align: string }> = {
  plaintiff: {
    bubble: 'bg-sky-50 border-sky-200 text-sky-950',
    badge: 'bg-sky-600 text-white',
    align: 'self-start',
  },
  defendant: {
    bubble: 'bg-amber-50 border-amber-200 text-amber-950',
    badge: 'bg-amber-600 text-white',
    align: 'self-end',
  },
  judge: {
    bubble: 'bg-ink text-white border-ink',
    badge: 'bg-white text-ink',
    align: 'self-stretch',
  },
}

export default function CourtRoom({
  rounds,
  running,
}: {
  rounds: MootRound[]
  running: boolean
}) {
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [rounds.length])

  return (
    <div className="bg-ink-pale/40 rounded-xl border border-ink/10 p-5 min-h-[320px] flex flex-col gap-4 max-h-[60vh] overflow-y-auto">
      {rounds.length === 0 && (
        <div className="flex-1 flex items-center justify-center text-ink/40 text-sm">
          {running ? '法庭准备中，请稍候…' : '尚未开庭'}
        </div>
      )}
      {rounds.map((r, i) => {
        const style = ROLE_STYLE[r.role] ?? ROLE_STYLE.plaintiff
        return (
          <div key={i} className={`flex flex-col ${style.align} max-w-[88%]`}>
            <div className="flex items-center gap-2 mb-1">
              <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${style.badge}`}>
                {r.role_name}
              </span>
              <span className="text-[10px] text-ink/40">{r.step_name}</span>
            </div>
            <div className={`rounded-xl border px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap ${style.bubble}`}>
              {r.content}
            </div>
          </div>
        )
      })}
      {running && rounds.length > 0 && (
        <div className="flex items-center gap-1.5 self-center text-ink/40 text-xs py-1">
          <span className="w-1.5 h-1.5 rounded-full bg-ember animate-pulse" />
          <span className="w-1.5 h-1.5 rounded-full bg-ember animate-pulse [animation-delay:150ms]" />
          <span className="w-1.5 h-1.5 rounded-full bg-ember animate-pulse [animation-delay:300ms]" />
          对方正在思考…
        </div>
      )}
      <div ref={endRef} />
    </div>
  )
}
