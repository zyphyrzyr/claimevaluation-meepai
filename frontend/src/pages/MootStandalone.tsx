import { useState } from 'react'
import { mootApi } from '../api'
import type { MootRound } from '../api'
import CourtRoom from '../components/CourtRoom'

/**
 * 独立模拟法庭：不建案、不评估、不回写评分
 * 手动组料（案情 + 我方主张要点）→ 三 Agent 对抗 → 演练报告
 */

interface DrillReport {
  weak_points: string[]
  focus_points: string[]
  defense_strength: number
  judge_summary: string
}

const CAUSE_TYPES = ['商标侵权', '著作权侵权', '不正当竞争']

export default function MootStandalone() {
  const [description, setDescription] = useState('')
  const [causeType, setCauseType] = useState(CAUSE_TYPES[0])
  const [points, setPoints] = useState('')
  const [rounds, setRounds] = useState<MootRound[]>([])
  const [running, setRunning] = useState(false)
  const [report, setReport] = useState<DrillReport | null>(null)
  const [error, setError] = useState('')

  const start = async () => {
    if (!description.trim() || running) return
    setRunning(true)
    setError('')
    setRounds([])
    setReport(null)
    try {
      await mootApi.runStandalone(
        { case_description: description, cause_type: causeType, plaintiff_points: points },
        (ev) => {
          if (ev.event === 'round') {
            setRounds((prev) => [...prev, ev])
          } else if (ev.event === 'moot_finished') {
            setReport({
              weak_points: ev.weak_points ?? [],
              focus_points: ev.focus_points ?? [],
              defense_strength: ev.defense_strength ?? 0,
              judge_summary: ev.judge_summary ?? '',
            })
          } else if (ev.event === 'moot_error') {
            setError(ev.error)
          }
        },
      )
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-medium">独立模拟法庭</h1>
        <p className="text-sm text-ink/50 mt-1">
          诉前对抗演练：输入案情即可开庭，看 AI 被告如何抗辩、我方链条哪里薄弱。
          不做评估、不回写评分，产出演练报告。
        </p>
      </div>

      {/* 组料表单 */}
      <div className="bg-white rounded-xl border border-ink/10 p-5 space-y-4">
        <div>
          <label className="text-sm font-medium">案情描述 *</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={4}
            placeholder="例：我司为某美术作品著作权人，发现某网店未经授权将作品印制在 T 恤上销售…"
            className="mt-1.5 w-full border border-ink/15 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-ember"
          />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="text-sm font-medium">案由</label>
            <select
              value={causeType}
              onChange={(e) => setCauseType(e.target.value)}
              className="mt-1.5 w-full border border-ink/15 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-ember"
            >
              {CAUSE_TYPES.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-sm font-medium">我方主张要点（可选）</label>
            <input
              value={points}
              onChange={(e) => setPoints(e.target.value)}
              placeholder="例：被告销量巨大，应从高判赔"
              className="mt-1.5 w-full border border-ink/15 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-ember"
            />
          </div>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-xs text-ink/40">
            独立演练不建案、不评分；如需回写修正系数请走案件内嵌模式
          </span>
          <button
            onClick={start}
            disabled={running || !description.trim()}
            className="bg-ember text-white px-5 py-2 rounded-lg text-sm font-medium hover:bg-ember-dark disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {running ? '庭审进行中…' : '开庭演练'}
          </button>
        </div>
      </div>

      {error && <div className="bg-red-50 text-red-700 rounded-lg p-4 text-sm">{error}</div>}

      {(rounds.length > 0 || running) && <CourtRoom rounds={rounds} running={running} />}

      {/* 演练报告 */}
      {report && !running && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          <div className="lg:col-span-2 bg-white rounded-xl border border-ink/10 p-5">
            <h2 className="text-sm font-medium mb-3">法官归纳</h2>
            <p className="text-sm leading-relaxed text-ink/80">{report.judge_summary}</p>
            {report.weak_points.length > 0 && (
              <div className="mt-4">
                <div className="text-xs font-medium text-red-700 mb-1">我方薄弱点</div>
                {report.weak_points.map((w, i) => (
                  <div key={i} className="text-sm text-ink/70">· {w}</div>
                ))}
              </div>
            )}
            {report.focus_points.length > 0 && (
              <div className="mt-3">
                <div className="text-xs font-medium text-green-700 mb-1">庭前补强建议</div>
                {report.focus_points.map((f, i) => (
                  <div key={i} className="text-sm text-ink/70">· {f}</div>
                ))}
              </div>
            )}
          </div>
          <div className="bg-white rounded-xl border border-ink/10 p-5">
            <div className="text-xs text-ink/50">对方抗辩强度</div>
            <div className="mt-1 flex items-center gap-3">
              <div className="flex-1 h-2 bg-ink-pale rounded-full overflow-hidden">
                <div className="h-full bg-ink rounded-full" style={{ width: `${report.defense_strength}%` }} />
              </div>
              <span className="text-sm font-medium">{report.defense_strength}</span>
            </div>
            <p className="text-xs text-ink/40 mt-3">
              独立演练不产出修正系数（无评分可修正）。若需系数回写主诉评估，
              请在完成评估的案件内启动内嵌模拟法庭。
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
