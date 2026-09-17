import { useEffect, useRef, useState } from 'react'
import { cn } from '../lib/utils'
import { AlertIcon, PauseIcon, PlayIcon, StopIcon } from './ui/Icons'

/**
 * 评估运行控制条（暂停 / 继续 / 终止）。
 *
 * 设计取向「静默线条型」：这条不是内容卡片，是状态行，
 * 所以背景用 bg-canvas（与页面同色）而不是 bg-surface，比下方的节点卡片更轻一档；
 * 按钮全部去掉描边，只在 hover 时浮出浅底——控件不抢正文注意力。
 * 层级靠「颜色」而非「边框」：暂停是 muted 灰（常规操作），终止是 danger 红字（危险操作）。
 *
 * 终止走「就地二次确认」而不是 window.confirm：
 * 原生弹窗会跳出产品视觉语言，用户读不到后果；这里把后果写在条上，
 * 并把危险态设成「会自动超时退回」，避免用户走开后一点就作废。
 */

/** 危险态自动退回时间：够读完一句后果说明，又不会长期卡住 */
const CONFIRM_TIMEOUT_MS = 5000

/** 三个按钮共用的静默线条底：无边框、无填充，hover 才浮出 */
const GHOST =
  'inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-current'

export default function RunControlBar({
  phase,
  done,
  total,
  onPause,
  onResume,
  onStop,
  className,
}: {
  phase: 'running' | 'paused'
  /** 已完成的节点数（由父级按 SSE 事件统计） */
  done: number
  /** 流程节点总数 */
  total: number
  onPause: () => void
  onResume: () => void
  onStop: () => void
  className?: string
}) {
  const [confirming, setConfirming] = useState(false)
  const timerRef = useRef<number | null>(null)

  // 危险态不能无限期停留：超时自动退回普通控制条。
  // 用户中途暂停/继续时也要清掉——那说明他已经离开「要终止」这条念头了。
  useEffect(() => {
    if (!confirming) return
    timerRef.current = window.setTimeout(() => setConfirming(false), CONFIRM_TIMEOUT_MS)
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    }
  }, [confirming, phase])

  // 第 N 步 = 已完成 + 当前正在跑的那一个；封顶到 total，避免最后一步溢出成 8/7
  const step = Math.min(done + 1, total)

  if (confirming) {
    return (
      <div
        className={cn(
          'flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-[var(--danger-line)] bg-canvas px-3.5 py-2',
          className,
        )}
      >
        <AlertIcon className="text-[var(--danger)]" />
        <span className="text-sm text-fg">终止后本次评估全部作废，已完成的节点结果不保留</span>
        <div className="ml-auto flex items-center gap-0.5">
          <button
            onClick={() => setConfirming(false)}
            className={cn(GHOST, 'text-muted hover:text-fg hover:bg-surface')}
          >
            取消
          </button>
          <button
            onClick={() => {
              setConfirming(false)
              onStop()
            }}
            className={cn(GHOST, 'bg-[var(--danger)] text-white hover:opacity-90')}
          >
            确认作废
          </button>
        </div>
      </div>
    )
  }

  return (
    <div
      className={cn(
        'flex items-center gap-2.5 rounded-lg border border-line bg-canvas px-3.5 py-2',
        className,
      )}
    >
      {/* 状态点承担「跑着还是停着」：运行中蓝色呼吸，暂停后转静态琥珀 */}
      {phase === 'running' ? (
        <span className="status-dot status-running" />
      ) : (
        <span className="status-dot bg-[var(--warning)]" />
      )}
      <span className="text-sm text-fg">{phase === 'running' ? '评估进行中' : '已暂停'}</span>
      <span className="text-xs text-muted">
        第 {step} / {total} 步{phase === 'paused' && '，可随时继续'}
      </span>

      <div className="ml-auto flex items-center gap-0.5">
        {phase === 'running' ? (
          <button
            onClick={onPause}
            title="暂停评估"
            className={cn(GHOST, 'text-muted hover:text-fg hover:bg-surface')}
          >
            <PauseIcon />
            暂停
          </button>
        ) : (
          <button
            onClick={onResume}
            title="继续评估"
            className={cn(GHOST, 'text-muted hover:text-fg hover:bg-surface')}
          >
            <PlayIcon />
            继续
          </button>
        )}
        <button
          onClick={() => setConfirming(true)}
          title="终止评估"
          className={cn(GHOST, 'text-[var(--danger)] hover:bg-[var(--danger-soft)]')}
        >
          <StopIcon />
          终止
        </button>
      </div>
    </div>
  )
}
