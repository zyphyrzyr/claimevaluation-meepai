import { motion } from 'framer-motion'
import { cn } from '../lib/utils'
import CourtRoom from './CourtRoom'
import type { MootRound } from '../api'
import { exportUrls } from '../api'

/**
 * 模拟法庭面板（受控渲染层）
 *
 * 抽出来的唯一理由：庭审状态不能住在页面里。
 * 评估详情是「单块逐步」——切一次轴就卸载一次子组件，状态放在页面组件里，
 * 用户切到「法律可行性」看一眼再切回来，整场庭审就空了。
 * 所以轮次 / 进行中 / 中止 / 法官归纳全部由 CaseWorkbench 持有，这里只负责画。
 *
 * 副作用是它变得很好测：喂一堆 rounds 就能整屏渲染，不需要跑 SSE。
 */

export interface MootJudgeInfo {
  correction_coefficient: number
  defense_strength: number
  judge_summary: string
  weak_points: string[]
  focus_points: string[]
  /**
   * 这份归纳是从历史庭审记录里回填的（只有系数，没有正文）。
   * 用来把「历史记录没存正文」和「这次模型没吐出可读正文」分开说——
   * 两者都是空 summary，但用户看到的解释必须不一样。
   */
  from_history?: boolean
}

export interface MootScoresUpdated {
  before?: any
  after?: any
  correction_coeff?: number
}

/** 播放速度档位（每轮揭示间隔，毫秒）。中档 2500ms 为默认。 */
export const MOOT_PACE_OPTIONS: { label: string; speed: number }[] = [
  { label: '慢', speed: 4000 },
  { label: '中', speed: 2500 },
  { label: '快', speed: 1200 },
]

export default function MootPanel({
  mode,
  rounds,
  rawRounds,
  running,
  stopped = false,
  judge,
  scoresUpdated,
  error,
  savedCoeff,
  canStart,
  canStartHint,
  onStart,
  onStop,
  caseId,
  pace,
  onPaceChange,
  onTogglePause,
  onStep,
}: {
  mode: 'embedded' | 'standalone'
  /** 已揭示（显示）的轮次：CourtRoom 渲染这些 */
  rounds: MootRound[]
  /** 模型已生成的全部轮次：用于「重新开庭/下载」判定与播放滞后提示 */
  rawRounds: MootRound[]
  running: boolean
  stopped?: boolean
  judge: MootJudgeInfo | null
  scoresUpdated: MootScoresUpdated | null
  error: string
  /** 已回写过的修正系数（历史庭审）：没有则按 1.0 计入 */
  savedCoeff?: number | null
  canStart: boolean
  /** 不可启动时的一句话原因（例如「尚未完成主诉评估」） */
  canStartHint?: string
  onStart: () => void
  onStop: () => void
  caseId?: string
  /** 播放节奏状态 */
  pace: { speed: number; paused: boolean }
  onPaceChange: (speed: number) => void
  onTogglePause: () => void
  onStep: () => void
}) {
  const isStandalone = mode === 'standalone'
  const coeff = judge?.correction_coefficient ?? savedCoeff
  const mootDone = coeff != null && coeff !== 1
  // 播放是否还有未揭示的轮次（模型已生成但屏幕还没放出）
  const pending = rawRounds.length > rounds.length
  // 法官归纳/判决书只在「跑完且播放排空」后出现，避免正文还没放完就提前弹出总结
  const showJudge = Boolean(judge) && !running && !pending

  return (
    <div className="space-y-4">
      {/* 头部：状态 + 控制。开庭与中止并排，中止只在庭审进行中出现 */}
      <div className="rounded-2xl border border-line bg-canvas p-4">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium text-fg">模拟法庭 · 庭审对抗</span>
              {isStandalone && (
                <span className="text-[10px] font-normal text-muted bg-surface border border-line px-2 py-0.5 rounded-full">
                  独立演练 · 不回写评分
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 mt-1.5 text-xs">
              <span
                className={cn(
                  'w-1.5 h-1.5 rounded-full shrink-0',
                  mootDone ? 'bg-[var(--success)]' : 'bg-[var(--warning)]',
                )}
              />
              {mootDone ? (
                <span className="text-fg">
                  已回写 · 修正系数 <b className="font-medium">{coeff}</b>
                  （法律可行性已按庭审结论修正）
                </span>
              ) : (
                <span className="text-muted">
                  {isStandalone
                    ? '以本案案情直接开庭，不跑评估、不回写评分'
                    : '未进行 · 修正系数 1.0（法律可行性暂未修正）'}
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {!running && (
              <button
                type="button"
                onClick={onStart}
                disabled={!canStart}
                className="bg-fg hover:opacity-90 text-canvas rounded-lg px-4 py-2 text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {rawRounds.length ? '重新开庭' : '开庭'}
              </button>
            )}
            {running && (
              <button
                type="button"
                onClick={onStop}
                className="border border-[var(--danger-line)] bg-[var(--danger-soft)] text-[var(--danger)] rounded-lg px-4 py-2 text-sm font-medium hover:opacity-80 transition-opacity"
              >
                中止庭审
              </button>
            )}
            {running && (
              <span className="text-xs text-muted">当前这轮说完后停止</span>
            )}
          </div>
        </div>

        {/* 播放节奏控制：进行中或仍有未揭示轮次时显示 */}
        {(running || pending) && (
          <div className="flex items-center gap-2 mt-3 pt-3 border-t border-line flex-wrap">
            <span className="text-xs text-muted">播放</span>
            {MOOT_PACE_OPTIONS.map((p) => {
              const on = pace.speed === p.speed
              return (
                <button
                  key={p.speed}
                  type="button"
                  onClick={() => onPaceChange(p.speed)}
                  className={cn(
                    'text-xs px-2.5 py-1 rounded-lg border transition-colors',
                    on
                      ? 'border-fg bg-surface text-fg font-medium'
                      : 'border-line text-muted hover:text-fg',
                  )}
                >
                  {p.label}
                </button>
              )
            })}
            <button
              type="button"
              onClick={onTogglePause}
              className={cn(
                'text-xs px-2.5 py-1 rounded-lg border transition-colors',
                pace.paused
                  ? 'border-fg bg-surface text-fg font-medium'
                  : 'border-line text-muted hover:text-fg',
              )}
            >
              {pace.paused ? '继续' : '暂停'}
            </button>
            <button
              type="button"
              onClick={onStep}
              className="text-xs px-2.5 py-1 rounded-lg border border-line text-muted hover:text-fg transition-colors"
            >
              单步
            </button>
            {pace.paused && (
              <span className="text-[10px] text-muted">已暂停 · 模型仍在跑，恢复后按节奏补齐</span>
            )}
          </div>
        )}

        {!canStart && !running && canStartHint && (
          <p className="text-xs text-muted mt-2">{canStartHint}</p>
        )}

        {!isStandalone && !running && rawRounds.length === 0 && (
          <p className="text-xs text-muted mt-2 leading-relaxed">
            评估完成后的庭审对抗演练：五步七轮对抗 → 法官归纳修正系数 → 回写并重算决策合成。
            未进行时法律可行性按系数 1.0 计算。
          </p>
        )}

        {rawRounds.length > 0 && !running && caseId && (
          <div className="flex gap-3 mt-3 pt-3 border-t border-line text-xs">
            <a href={exportUrls.transcriptDocx(caseId)} className="text-muted hover:text-fg transition-colors">
              下载庭审记录 Word
            </a>
            <a href={exportUrls.transcriptPdf(caseId)} className="text-muted hover:text-fg transition-colors">
              下载 PDF
            </a>
          </div>
        )}
      </div>

      {error && (
        <div className="bg-[var(--danger-soft)] text-[var(--danger)] rounded-lg p-3 text-sm">{error}</div>
      )}

      <CourtRoom rounds={rounds} rawCount={rawRounds.length} running={running} stopped={stopped} />

      {/* 法官归纳：判决书样式。只在跑完（或载入历史记录）且播放排空后出现，进行中不抢戏 */}
      {showJudge && (
        <div className="rounded-2xl border border-line bg-canvas overflow-hidden">
          <div className="px-4 py-3 border-b border-line flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 min-w-0">
              <span className="shrink-0 w-6 h-6 rounded-full bg-brand-soft text-fg flex items-center justify-center text-[11px] font-medium">
                审
              </span>
              <span className="text-sm font-medium text-fg truncate">法官归纳 · 判决书</span>
            </div>
            {judge!.correction_coefficient != null && (
              <span className="shrink-0 text-xs text-muted tabular-nums">
                修正系数 {judge!.correction_coefficient}
              </span>
            )}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-[1.55fr_1fr] gap-5 p-4">
            <div className="min-w-0">
              {judge!.judge_summary ? (
                <p className="text-sm leading-[1.9] text-muted whitespace-pre-wrap">
                  {judge!.judge_summary}
                </p>
              ) : (
                <p className="text-sm text-muted">
                  {judge!.from_history
                    ? '这场庭审是从历史记录回填的，当时只存了修正系数，没有存法官归纳正文。'
                    : '本次模型没有产出可读的法官归纳（返回值无法解析），因此修正系数按 1.0 计入、评分未修正。可重新开庭再试一次。'}
                </p>
              )}

              {judge!.weak_points?.length > 0 && (
                <div className="mt-4">
                  <div className="text-xs font-medium text-[var(--danger)] mb-1.5">原告薄弱点</div>
                  <ul className="space-y-1">
                    {judge!.weak_points.map((w, i) => (
                      <li key={i} className="text-[13px] text-muted leading-relaxed flex gap-2">
                        <span className="shrink-0 text-line">·</span>
                        <span>{w}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {judge!.focus_points?.length > 0 && (
                <div className="mt-3">
                  <div className="text-xs font-medium text-[var(--success)] mb-1.5">补强建议</div>
                  <ul className="space-y-1">
                    {judge!.focus_points.map((f, i) => (
                      <li key={i} className="text-[13px] text-muted leading-relaxed flex gap-2">
                        <span className="shrink-0 text-line">·</span>
                        <span>{f}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            <div className="space-y-3">
              <div className="rounded-xl border border-line bg-surface p-4 text-center">
                <div className="text-xs text-muted">修正系数（{isStandalone ? '仅演练' : '回写评分'}）</div>
                <div className="text-3xl font-medium text-fg mt-1.5 tabular-nums">
                  {judge!.correction_coefficient ?? '—'}
                </div>
                <div className="text-[10px] text-muted mt-1.5">范围 0.70 – 1.30</div>
              </div>

              {judge!.defense_strength > 0 && (
                <div className="rounded-xl border border-line bg-surface p-4">
                  <div className="text-xs text-muted">被告抗辩强度</div>
                  <div className="mt-2 flex items-center gap-3">
                    <div className="flex-1 h-1.5 bg-line rounded-full overflow-hidden">
                      <div
                        className="h-full bg-fg rounded-full"
                        style={{ width: `${judge!.defense_strength}%` }}
                      />
                    </div>
                    <span className="text-sm font-medium text-fg tabular-nums">
                      {judge!.defense_strength}
                    </span>
                  </div>
                </div>
              )}

              {scoresUpdated && (
                <motion.div
                  initial={{ scale: 0.94, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={{ type: 'spring', stiffness: 220, damping: 16 }}
                  className="rounded-xl border border-fg bg-surface p-4"
                >
                  <div className="text-xs text-muted mb-1.5">决策分已更新（系数回写）</div>
                  <div className="text-2xl font-medium text-fg tabular-nums">
                    {scoresUpdated.before?.final ?? '—'}
                    <span className="text-muted mx-2">→</span>
                    {scoresUpdated.after?.final ?? '—'}
                  </div>
                  <div className="text-[11px] text-muted mt-1 tabular-nums">
                    法律可行性 {scoresUpdated.before?.legal_feasibility ?? '—'} →{' '}
                    {scoresUpdated.after?.legal_feasibility ?? '—'}
                  </div>
                </motion.div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
