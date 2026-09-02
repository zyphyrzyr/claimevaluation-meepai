import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { api, mootApi, exportUrls } from '../api'
import type { MootRound } from '../api'
import CourtRoom from '../components/CourtRoom'
import { Card } from '../components/ui/Card'
import { useTheme } from '../theme/ThemeProvider'

interface JudgeInfo {
  correction_coefficient: number
  defense_strength: number
  judge_summary: string
  weak_points: string[]
  focus_points: string[]
}

export default function MootCourt() {
  const { id } = useParams<{ id: string }>()
  const { setTheme } = useTheme()

  // 模拟法庭 = 暗色剧场例外：进入切 theater，离开恢复 light（P2 双主题机制）
  useEffect(() => {
    setTheme('theater')
    return () => setTheme('light')
  }, [setTheme])

  const [rounds, setRounds] = useState<MootRound[]>([])
  const [running, setRunning] = useState(false)
  const [judge, setJudge] = useState<JudgeInfo | null>(null)
  const [scoresUpdated, setScoresUpdated] = useState<any>(null)
  const [error, setError] = useState('')
  const [result, setResult] = useState<any>(null)

  useEffect(() => {
    if (!id) return
    Promise.all([api.result(id), api.mootHistory(id)])
      .then(([res, hist]) => {
        setResult(res)
        if (hist?.transcript?.length) {
          setRounds(hist.transcript)
          setJudge({
            correction_coefficient: hist.correction_coeff,
            defense_strength: 0,
            judge_summary: '',
            weak_points: [],
            focus_points: [],
          })
        }
      })
      .catch((e) => setError(String(e)))
  }, [id])

  const start = async () => {
    if (!id || running) return
    setRunning(true)
    setError('')
    setRounds([])
    setJudge(null)
    setScoresUpdated(null)
    try {
      await mootApi.runEmbedded(id, (ev) => {
        if (ev.event === 'round') {
          setRounds((prev) => [...prev, ev])
        } else if (ev.event === 'moot_finished') {
          setJudge(ev)
        } else if (ev.event === 'scores_updated') {
          setScoresUpdated(ev)
        } else if (ev.event === 'moot_error') {
          setError(ev.error)
        }
      })
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-medium text-fg">模拟法庭 · 对抗压力测试</h1>
          <p className="text-sm text-muted mt-1">
            原告 / 被告 / 法官三 Agent 五步庭审；法官归纳产出修正系数，回写评分
          </p>
        </div>
        <div className="flex items-center gap-3">
          {result?.scores?.final != null && (
            <div className="text-sm text-muted">
              当前决策分 <span className="font-semibold text-fg">{result.scores.final}</span>
            </div>
          )}
          <button
            onClick={start}
            disabled={running || result?.scores?.final == null}
            className="bg-brand text-canvas px-5 py-2 rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {running ? '庭审进行中…' : rounds.length ? '重新开庭' : '开庭'}
          </button>
          {rounds.length > 0 && (
            <>
              <a
                href={exportUrls.transcriptDocx(id!)}
                className="border border-line text-fg px-4 py-2 rounded-lg text-sm hover:bg-surface transition-colors"
              >
                下载庭审记录 Word
              </a>
              <a
                href={exportUrls.transcriptPdf(id!)}
                className="border border-line text-fg px-4 py-2 rounded-lg text-sm hover:bg-surface transition-colors"
              >
                下载 PDF
              </a>
            </>
          )}
        </div>
      </div>

      {result?.scores?.final == null && !running && (
        <div className="bg-[var(--warning-soft)] text-[var(--warning)] border border-[var(--warning-soft)] rounded-lg p-4 text-sm">
          该案件尚未完成主诉评估。模拟法庭（内嵌模式）需要先完成评估，
          <Link to={`/cases/${id}/evaluation`} className="underline font-medium">去评估</Link>
          ；或使用
          <Link to="/moot" className="underline font-medium">独立演练模式</Link>
          直接开庭。
        </div>
      )}

      {error && <div className="bg-[var(--danger-soft)] text-[var(--danger)] rounded-lg p-4 text-sm">{error}</div>}

      <CourtRoom rounds={rounds} running={running} />

      {/* 法官归纳 + 系数回写 */}
      {judge && !running && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          <Card className="lg:col-span-2 p-5">
            <h2 className="text-sm font-medium mb-3 text-fg">法官归纳</h2>
            {judge.judge_summary ? (
              <p className="text-sm leading-relaxed text-muted">{judge.judge_summary}</p>
            ) : (
              <p className="text-sm text-muted">（历史庭审记录，法官归纳详见备忘录）</p>
            )}
            {judge.weak_points?.length > 0 && (
              <div className="mt-4">
                <div className="text-xs font-medium text-[var(--danger)] mb-1">原告薄弱点</div>
                {judge.weak_points.map((w, i) => (
                  <div key={i} className="text-sm text-muted">· {w}</div>
                ))}
              </div>
            )}
            {judge.focus_points?.length > 0 && (
              <div className="mt-3">
                <div className="text-xs font-medium text-[var(--success)] mb-1">补强建议</div>
                {judge.focus_points.map((f, i) => (
                  <div key={i} className="text-sm text-muted">· {f}</div>
                ))}
              </div>
            )}
          </Card>

          <div className="space-y-5">
            <Card className="p-5 text-center">
              <div className="text-xs text-muted">修正系数（回写评分）</div>
              <div className="text-4xl font-semibold text-brand mt-2">
                {judge.correction_coefficient}
              </div>
              <div className="text-xs text-muted mt-2">范围 0.7 – 1.3</div>
            </Card>
            {judge.defense_strength > 0 && (
              <Card className="p-5">
                <div className="text-xs text-muted">被告抗辩强度</div>
                <div className="mt-1 flex items-center gap-3">
                  <div className="flex-1 h-2 bg-line rounded-full overflow-hidden">
                    <div className="h-full bg-fg rounded-full" style={{ width: `${judge.defense_strength}%` }} />
                  </div>
                  <span className="text-sm font-medium text-fg">{judge.defense_strength}</span>
                </div>
              </Card>
            )}
            {scoresUpdated && (
              <motion.div
                key={scoresUpdated.after?.final ?? 'none'}
                initial={{ scale: 0.92, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={{ type: 'spring', stiffness: 220, damping: 14 }}
              >
                <Card className="p-5 border-brand shadow-[0_0_24px_rgba(190,124,255,0.22)]">
                  <div className="text-xs text-muted mb-2">决策分已更新（系数回写）</div>
                  <div className="text-2xl font-semibold text-fg">
                    {scoresUpdated.before?.final ?? '—'}
                    <span className="text-brand mx-2">→</span>
                    {scoresUpdated.after?.final ?? '—'}
                  </div>
                  <div className="text-xs text-muted mt-1">
                    法律可行性 {scoresUpdated.before?.legal_feasibility} → {scoresUpdated.after?.legal_feasibility}
                  </div>
                </Card>
              </motion.div>
            )}
          </div>
        </div>
      )}

      {rounds.length > 0 && !running && (
        <div className="flex gap-3 text-sm">
          <Link
            to={`/cases/${id}/dashboard`}
            className="text-brand hover:underline font-medium"
          >
            返回决策仪表盘 →
          </Link>
          <Link to={`/cases/${id}/report`} className="text-muted hover:underline">
            查看决策备忘录
          </Link>
        </div>
      )}
    </div>
  )
}
