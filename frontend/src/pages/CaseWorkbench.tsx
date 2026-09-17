import { useEffect, useRef, useState } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { api, authApi, mootApi, humanError, runEvaluation, resumeEvaluation, type EvalEvent } from '../api'
import EvalRun, { EVAL_AXES, NODE_ORDER, type MootState } from './EvalRun'
import DecisionDashboard, { RESULT_SECTIONS } from './DecisionDashboard'
import NewCaseForm, { FORM_SECTIONS, type CaseFormInitial } from '../components/NewCaseForm'
import SectionNav from '../components/SectionNav'
import RunControlBar from '../components/RunControlBar'
import { useAuth } from '../auth/AuthProvider'

type Tab = 'detail' | 'run' | 'result'

const TABS: { key: Tab; label: string }[] = [
  { key: 'detail', label: '案件详情' },
  { key: 'run', label: '评估详情' },
  { key: 'result', label: '评估结果' },
]

// 评估已跑过（或正在跑）的状态：不应再显示"尚未开始评估"。
// 注意：红线拦截 blocked 也在此列——它已有结果（硬门禁命中、流程终止），只是 final 为 null。
// aborted = 用户手动终止并作废，仍视为"已触发过评估"（显示作废态而非空白）。
const EVALUATED_STATUSES = ['evaluating', 'partial', 'completed', 'blocked', 'aborted']

const STATUS_BADGE: Record<string, { text: string; dot: string }> = {
  draft: { text: '草稿', dot: 'bg-muted' },
  pending: { text: '待评估', dot: 'bg-warning' },
  evaluating: { text: '评估中', dot: 'bg-info' },
  partial: { text: '部分完成', dot: 'bg-warning' },
  completed: { text: '已完成', dot: 'bg-success' },
  blocked: { text: '红线拦截', dot: 'bg-danger' },
  aborted: { text: '已作废', dot: 'bg-danger' },
}

/**
 * 左侧章节导航的纵向定位：两个标签共用同一串类，保证位置完全一致。
 * mt 决定「自然位置」（未滚动时落在哪）＝ main 的 pt-5(20) + mt(25vh+48) = 25vh+68px，
 * 与左侧大导航首项（py-5 + text-xl 行高28 + py-5 + mt-25vh = 25vh+68px）恰好重合；
 * top 决定「粘住后停在哪」，必须等于自然位置，否则滚动到触发点时会跳一段。
 */
const NAV_STICKY = 'mt-[calc(25vh+3rem)] sticky top-[calc(25vh+4.25rem)]'

const EMPTY_MOOT: MootState = {
  mode: 'embedded',
  rounds: [],
  shownRounds: [],
  running: false,
  stopped: false,
  judge: null,
  scoresUpdated: null,
  error: '',
  pace: { speed: 2500, paused: false },
}

export default function CaseWorkbench() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const auth = useAuth()
  const [claiming, setClaiming] = useState(false)

  const [detail, setDetail] = useState<any>(null)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState<Tab>('detail')
  const [activeStep, setActiveStep] = useState<string>(FORM_SECTIONS[0].id)
  const [defaultChosen, setDefaultChosen] = useState(false)
  const [refreshTick, setRefreshTick] = useState(0)

  // 评估运行时状态提升到容器层：确保 SSE 流不会被「标签切换」触发的子组件卸载中断
  const [phase, setPhase] = useState<'prep' | 'running' | 'paused' | 'done'>('prep')
  // 终止评估时持有的 AbortController：终止后主动切断本地读流
  const abortRef = useRef<AbortController | null>(null)
  const [states, setStates] = useState<Record<string, any>>({})
  const [finished, setFinished] = useState('')
  const [injectedInfo, setInjectedInfo] = useState('')
  const [result, setResult] = useState<any>(null)
  const [evalError, setEvalError] = useState('')
  // 评估过程事件流（node_step / mcp_call / 节点起止）：提升到容器层，理由同 states——
  // 标签切换会让子组件卸载，过程记录不能跟着丢。
  const [traceEvents, setTraceEvents] = useState<EvalEvent[]>([])

  // 模拟法庭运行时状态：同样必须在容器层。评估详情是「单块逐步」渲染，
  // 切一次轴就卸载一次 EvalRun——庭审跑到一半切去看法律可行性，回来就空了。
  const [moot, setMoot] = useState<MootState>(EMPTY_MOOT)

  // 揭示缓冲区：模型已生成的轮次存进 moot.rounds（raw），按 pace 逐条放出到
  // shownRounds 供 CourtRoom 渲染。这样"模型生成"与"屏幕显示"解耦——调慢速度、
  // 暂停、单步都不影响后端运行，只影响播放节奏，留出阅读思考时间。
  // paceRef 把最新的暂停状态喂给定时器 tick（定时器只在 speed 变化时重建）。
  const paceRef = useRef({ speed: EMPTY_MOOT.pace.speed, paused: EMPTY_MOOT.pace.paused })
  useEffect(() => {
    paceRef.current = moot.pace
  }, [moot.pace])
  useEffect(() => {
    const id = setInterval(() => {
      setMoot((prev) => {
        if (paceRef.current.paused) return prev
        if (prev.shownRounds.length >= prev.rounds.length) return prev
        const next = prev.rounds[prev.shownRounds.length]
        return { ...prev, shownRounds: [...prev.shownRounds, next] }
      })
    }, moot.pace.speed)
    return () => clearInterval(id)
  }, [moot.pace.speed])

  // 评估详情是否已经有可展示的轴：未评估时 EvalRun 只渲染一张「尚未开始评估」卡片，
  // 此时不给左侧导航，避免出现点不动的死链接。（与 EvalRun 的空状态判定同源）
  // 庭审已开过场（或正在开）：就算评估没跑过也要给导航，否则模拟法庭轴成了死链接
  const evalReady = Boolean(result) || phase !== 'prep' || moot.running || moot.rounds.length > 0
  // 当前展示的轴（单块逐步）：点击左侧导航 = 切换 activeAxis，与案件详情的 activeStep 同构
  const [activeAxis, setActiveAxis] = useState(EVAL_AXES[0].id)
  // 评估结果当前展示的块（可视化结果 / 详细结果），与上面两个同为「单块逐步」模型
  const [resultSection, setResultSection] = useState(RESULT_SECTIONS[0].id)

  // 已完成节点数（运行控制条显示「第 N / 7 步」用）。
  // 只排除「没轮到 / 正在跑」两种，其余状态无论 ok/partial/failed/blocked/stale 都算已出结果，
  // 后端将来新增终态名不用回来改这里。
  const doneCount = NODE_ORDER.filter(
    (n) => states[n] && states[n] !== 'running' && states[n] !== 'waiting',
  ).length

  const loadDetail = () => {
    if (!id) return
    api
      .caseDetail(id)
      .then((d) => {
        setDetail(d)
        setResult(null)
        setPhase('prep')
        setStates({})
        setFinished('')
        setInjectedInfo('')
        setEvalError('')
        setTraceEvents([])
      })
      .catch((e) => setError(humanError(e)))
  }

  useEffect(() => {
    loadDetail()
  }, [id, refreshTick])

  // 首次加载：默认停在「案件详情」（activeTab 初始态已是 detail，不再按 status 跳转到评估页）。
  // 仅负责拉取评估结果：只要状态表明评估已跑过就拉，不再要求 final 有值（红线拦截 case 的 final 为 null）。
  useEffect(() => {
    if (!detail || defaultChosen) return
    setDefaultChosen(true)
    if (id && EVALUATED_STATUSES.includes(detail.status)) {
      api.result(id).then(setResult).catch(() => {})
      // 已跑过的庭审：把历史记录填回场上，否则进来只看到「未进行」但系数已经回写过
      api
        .mootHistory(id)
        .then((h) => {
          if (!h?.transcript?.length) return
          setMoot((m) =>
            m.rounds.length
              ? m
              : {
                  ...m,
                  rounds: h.transcript,
                  shownRounds: h.transcript,
                  judge: {
                    correction_coefficient: h.correction_coeff,
                    defense_strength: 0,
                    judge_summary: '',
                    weak_points: [],
                    focus_points: [],
                    from_history: true,
                  },
                },
          )
        })
        .catch(() => {})
    }
    // 刷新页面时若后端评估仍在跑/已暂停（case.status 仍是 evaluating），
    // 重新接上 SSE 流并同步 phase，否则会丢失进度与「暂停/恢复」控制权。
    if (id && detail.status === 'evaluating') {
      api.getRunState(id).then((s) => {
        if (s.run === 'running' || s.run === 'paused') {
          setActiveTab('run')
          setPhase(s.run === 'paused' ? 'paused' : 'running')
          resumeEvaluation(id, onEvent).catch(() => {})
        } else if (s.run === 'interrupted') {
          // 上一轮评估的进程已经没了（服务重启或运行中异常），状态已被后端自愈修正。
          // 这里绝不能进 running 态：否则会挂出暂停/终止控制条，而一点暂停就是 404。
          setActiveTab('run')
          refreshDetailOnly()
        }
      }).catch(() => {})
    }
  }, [detail, defaultChosen, id])

  const refreshResult = () => {
    if (!id) return
    api.result(id).then(setResult).catch(() => {})
  }

  const onEvent = (e: EvalEvent) => {
    // 过程类事件先原样留档，供「评估过程」时间线与节点卡内的步骤列表消费
    if (
      e.event === 'node_step' ||
      e.event === 'mcp_call' ||
      e.event === 'node_started' ||
      e.event === 'node_finished' ||
      e.event === 'recall_done'
    ) {
      setTraceEvents((arr) => [...arr, e])
    }
    if (e.event === 'node_started') {
      setStates((s) => ({ ...s, [e.node]: 'running' }))
    } else if (e.event === 'node_finished') {
      setStates((s) => ({ ...s, [e.node]: (e.status as any) ?? 'ok' }))
      refreshResult()
    } else if (e.event === 'flow_blocked') {
      refreshResult()
    } else if (e.event === 'recall_done') {
      // 后台自动召回完成（方案 B）：材料注入全程后台化，这里只做信息展示
      setInjectedInfo(`${e.label ?? '自动召回完成'}（明细见审计轨迹）`)
    } else if (e.event === 'flow_finished') {
      setFinished(e.status ?? '')
      setPhase('done')
      refreshResult()
      refreshDetailOnly()
    } else if (e.event === 'flow_paused') {
      setPhase('paused')
    } else if (e.event === 'flow_resumed') {
      setPhase('running')
    } else if (e.event === 'flow_aborted') {
      setFinished('aborted')
      setPhase('done')
      refreshResult()
      refreshDetailOnly()
    } else if (e.event === 'flow_error') {
      setEvalError(e.error ?? '未知错误')
    }
  }

  // 方案 B：评估启动不再需要手动勾选材料——后端在 run 开头自动召回并留审计。
  // 前端只负责切到评估详情标签并接 SSE 流。
  const startEval = () => {
    if (!id) return
    setEvalError('')
    setInjectedInfo('')
    setStates({})
    setTraceEvents([])
    const ac = new AbortController()
    abortRef.current = ac
    setPhase('running')
    setActiveTab('run')
    setDetail((d: any) => (d ? { ...d, status: 'evaluating' } : d))
    refreshResult()
    runEvaluation(id, onEvent, ac.signal).catch((e) => setEvalError(String(e)))
  }

  /**
   * 前端 phase 与后端实际运行不一致时的兜底同步。
   *
   * 触发场景：页面以为在跑（phase='running'，控制条已挂出），但后端 RUNS 里早没有
   * 对应句柄了——上一轮运行已结束、或服务重启过。此时 pause/resume 会返回 404/409。
   * 与其把裸 JSON 甩给用户，不如重新问一次真实状态并把 phase 摆正。
   */
  const syncPhaseFromBackend = async () => {
    if (!id) return
    try {
      const s = await api.getRunState(id)
      if (s.run === 'running' || s.run === 'paused') {
        setPhase(s.run === 'paused' ? 'paused' : 'running')
      } else {
        // 没有可操控的运行了：退出运行态，控制条随之消失
        setPhase('done')
        refreshDetailOnly()
      }
    } catch {
      setPhase('done')
    }
    refreshResult()
  }

  // 暂停：后端在当前检查点挂起（已完成节点保留），前端切到 paused 态
  const pauseEval = () => {
    if (!id) return
    setEvalError('')
    api.pauseEvaluation(id).catch(() => {
      setEvalError('评估已不在运行中（可能已结束或服务重启过），已为你同步为实际状态')
      syncPhaseFromBackend()
    })
  }
  // 恢复：清后端暂停信号；若本标签页没有活跃流（刷新后）则顺带重接连流
  const resumeEval = () => {
    if (!id) return
    setEvalError('')
    resumeEvaluation(id, onEvent).catch(() => {
      setEvalError('评估已不在运行中（可能已结束或服务重启过），已为你同步为实际状态')
      syncPhaseFromBackend()
    })
  }
  // 终止：后端立即中止并清空本次结果（全部作废）；同时切断本地读流
  const stopEval = () => {
    if (!id) return
    api.stopEvaluation(id).catch((e) => setEvalError(String(e)))
    abortRef.current?.abort()
  }

  // ---- 模拟法庭：就地开庭 / 中止 -------------------------------------------
  //
  // 早期版本是 navigate 到 /cases/:id/moot 独立页，那一页还会把整个 <html>
  // 刷成暗色剧场，观感像跳去了另一个站点。现在庭审就在「评估详情 - 模拟法庭」
  // 这一轴里跑，配色沿用浅色 token，状态住在容器层。

  /** 开庭。mode: embedded = 评估后压力测试（系数回写）；standalone = 纯演练不回写 */
  const startMoot = async (mode: 'embedded' | 'standalone' = 'embedded') => {
    if (!id) return
    setMoot({ ...EMPTY_MOOT, mode, running: true })
    setActiveTab('run')
    setActiveAxis('eval-moot')

    const onEvent = (ev: any) => {
      if (ev.event === 'round') {
        setMoot((m) => ({ ...m, rounds: [...m.rounds, ev] }))
      } else if (ev.event === 'moot_finished') {
        setMoot((m) => ({ ...m, judge: ev }))
      } else if (ev.event === 'scores_updated') {
        setMoot((m) => ({ ...m, scoresUpdated: ev }))
        // 内嵌模式回写了系数与决策合成：刷新结果页与案件状态，别让用户看到旧分
        refreshResult()
        refreshDetailOnly()
      } else if (ev.event === 'moot_stopped') {
        // 中止：已说轮次留在场上，但不回写、不落库
        setMoot((m) => ({ ...m, running: false, stopped: true }))
        refreshResult()
      } else if (ev.event === 'moot_error') {
        setMoot((m) => ({ ...m, error: ev.error ?? '庭审失败' }))
      }
    }

    try {
      if (mode === 'standalone') {
        const d = detail ?? (await api.caseDetail(id))
        await mootApi.runStandalone(
          {
            case_description: d.case_description ?? '',
            cause_type: d.cause_type ?? '商标侵权',
            viewpoints: d.context?.user_viewpoints ?? [],
            case_id: id,
          },
          onEvent,
        )
      } else {
        await mootApi.runEmbedded(id, onEvent)
      }
    } catch (e) {
      setMoot((m) => ({ ...m, error: humanError(e) || String(e) }))
    } finally {
      setMoot((m) => ({ ...m, running: false }))
    }
  }

  /**
   * 中止庭审。
   *
   * 后端只在「取下一轮之前」检查停止标志——真实模式下一轮就是一次十秒级的
   * LLM 调用，调用中途打断不了。所以这里按下后不立刻清场，等 moot_stopped
   * 回来再收尾；对已经跑完的庭审调用是无害空操作（返回 stopped=false）。
   */
  const stopMoot = () => {
    if (!id) return
    mootApi.stop(id).catch(() => {})
  }

  /** 播放节奏：调速度（毫秒/轮） */
  const setMootPace = (speed: number) =>
    setMoot((m) => ({ ...m, pace: { ...m.pace, speed } }))
  /** 播放节奏：暂停/继续揭示（模型仍在跑） */
  const toggleMootPause = () =>
    setMoot((m) => ({ ...m, pace: { ...m.pace, paused: !m.pace.paused } }))
  /** 播放节奏：单步，立即放出下一轮（暂停时也有效） */
  const mootStep = () =>
    setMoot((m) => {
      if (m.shownRounds.length >= m.rounds.length) return m
      const next = m.rounds[m.shownRounds.length]
      return { ...m, shownRounds: [...m.shownRounds, next] }
    })

  const rerunNode = async (node: string, guidance: string) => {
    if (!id) return
    try {
      await api.rerun(id, node, guidance)
      await refreshResult()
    } catch (e) {
      setEvalError(String(e))
    }
  }

  // 仅刷新 detail，不复位运行时状态（评估结束后同步案件状态徽标用）
  const refreshDetailOnly = () => {
    if (!id) return
    api.caseDetail(id).then(setDetail).catch(() => {})
  }

  // 草稿在「案件详情」标签保存后，重新拉取并复位默认标签选择
  const onDraftSaved = () => {
    setDefaultChosen(false)
    setRefreshTick((t) => t + 1)
  }
  // 「保存并启动评估」：落库成功后直接进入评估运行（方案 B：无准备页，后台自动召回材料）
  const onDraftStarted = () => {
    startEval()
  }

  // ---- URL 直达：?tab=run&axis=eval-moot[&moot=embedded|standalone] ----
  //
  // 评估结果页的「启动模拟法庭」、以及新建案件后「仅开始模拟法庭」都靠它落位。
  // 用 searchParams 而不是跳转独立路由：同一个 /cases/:id 路由不会重挂载组件，
  // 已经跑起来的评估进度与庭审现场都不会因为这次跳转丢掉。

  // tab / axis 只负责「落在哪一屏」；每次 URL 变化都跟一次，允许外部反复指定
  useEffect(() => {
    const tab = searchParams.get('tab')
    const axis = searchParams.get('axis')
    if (tab === 'run' || tab === 'result' || tab === 'detail') setActiveTab(tab)
    if (axis === 'eval-moot') setActiveAxis('eval-moot')
  }, [searchParams])

  // moot=xxx 负责「顺带开庭」。用 ref 记已经处理过的参数值，避免 StrictMode
  // 下重复触发把同一场庭审开两遍。用完立刻把它从地址里抹掉（replace，不留历史），
  // 否则用户赛后刷新页面会莫名其妙又开一场——参数是一次性指令，不是持久状态。
  const mootParam = searchParams.get('moot')
  const mootAutoRef = useRef<string | null>(null)
  useEffect(() => {
    if (!id || !detail) return
    if (mootParam !== 'embedded' && mootParam !== 'standalone') return
    // 「已处理过」记的是**整条 query**而不是 moot 的值：同一次会话里
    // 「结果页 → 启动模拟法庭」可以点第二次（第二次点开的仍然是 moot=embedded），
    // 按值去重会把第二次静默吞掉。消费后参数会被抹掉，所以两次的 query 必然不同。
    const key = searchParams.toString()
    if (mootAutoRef.current === key) return
    mootAutoRef.current = key
    startMoot(mootParam)
    const rest = new URLSearchParams(searchParams)
    rest.delete('moot')
    navigate(`/cases/${id}?${rest}`, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mootParam, id, detail])

  /** 案件详情「仅开始模拟法庭」：就地开庭；若案件是刚新建的则换到它的地址再开 */
  const onDetailStartMoot = (cid: string) => {
    if (!cid) return
    if (cid !== id) {
      navigate(`/cases/${cid}?tab=run&axis=eval-moot&moot=standalone`)
      return
    }
    setActiveTab('run')
    setActiveAxis('eval-moot')
    startMoot('standalone')
  }

  if (error) return <div className="bg-[var(--danger-soft)] text-[var(--danger)] rounded-lg p-4 text-sm">{error}</div>
  if (!detail) return <div className="text-muted text-sm">加载中…</div>

  const status = detail.status
  const st = STATUS_BADGE[status] ?? STATUS_BADGE.pending
  const evaluated = EVALUATED_STATUSES.includes(status)

  const fileCount = detail.evidence_files?.length ?? 0
  const isPublic = detail.is_public === true

  const claim = async () => {
    if (!id) return
    setClaiming(true)
    try {
      await authApi.claimCase(id)
      await auth.refresh()
      setDefaultChosen(false)
      setRefreshTick((t) => t + 1)
    } catch {
      // 认领失败最常见的两种：没登录（401，拦截器已弹框）、已被别人认领（403）。
      // 两种都不需要额外提示——前者弹框里会说明，后者刷新后案件就看不见了。
    } finally {
      setClaiming(false)
    }
  }

  return (
    <div className="max-w-[67rem] mx-auto xl:grid xl:grid-cols-[9rem_minmax(0,1fr)] xl:gap-8">
      {/* 章节导航列：正好填进左侧边栏与内容区之间原先空着的那段留白。
          两个标签共用这一列，纵向定位也共用 NAV_STICKY，所以位置完全一致。

          粘性与偏移都挂在内层 SectionNav 上（本列只是占位容器，不参与定位）——
          注意不能挂到这个网格项上：网格项会被拉伸到整行高度，自身没有可粘行程，
          sticky 会退化成「一开始就贴在网格顶部」，导航直接跳到页面上方。
          实测（headless Chrome 1440x757，侧边栏首项 y=257）：
          mt/top 同值时 scroll≥20 后差值为 -20（往上蹿一段）；让 top 等于自然位置后全程差值 0。

          两种驱动模式共用同一个受控组件，只是喂进去的 active / onSelect 不同：
          · 案件详情：单块逐步表单 → active=activeStep，点击=切换显示哪一块（不滚动）
          · 评估详情：单块逐步（切轴） → active=activeAxis，点击=切换展示哪一个轴（不滚动）
          · 评估结果：单块逐步（切块） → active=resultSection，点击=切换「可视化结果/详细结果」
          三者都用 STEP_MOTION 做切换动画，交互完全一致；左侧列只负责定位与高亮。 */}
      <div className="hidden xl:block">
        {activeTab === 'detail' && (
          <SectionNav
            sections={FORM_SECTIONS}
            active={activeStep}
            onSelect={setActiveStep}
            className={NAV_STICKY}
          />
        )}
        {activeTab === 'run' && evalReady && (
          <SectionNav
            sections={EVAL_AXES}
            active={activeAxis}
            onSelect={setActiveAxis}
            className={NAV_STICKY}
          />
        )}
        {activeTab === 'result' && (
          <SectionNav
            sections={RESULT_SECTIONS}
            active={resultSection}
            onSelect={setResultSection}
            className={NAV_STICKY}
          />
        )}
      </div>

      <div className="min-w-0">
        {/* 面包屑：把「这是个案件工作台」降级为导航信息，不再占用页面标题 */}
        <nav className="flex items-center gap-2 text-xs text-muted mb-3">
          <button onClick={() => navigate('/workbench')} className="hover:text-fg transition-colors">
            案件列表
          </button>
          <span className="text-line">/</span>
          <span>案件工作台</span>
        </nav>

        {/* 主标题就是案件名：完整展示、可换行，不再截断 */}
        <div className="flex items-start gap-3">
          <h1 className="flex-1 min-w-0 text-2xl font-medium leading-snug break-words">{detail.name}</h1>
          <span className="mt-1.5 inline-flex items-center gap-1.5 shrink-0 rounded-full border border-line px-2.5 py-1 text-xs text-muted">
            <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
            {st.text}
          </span>
        </div>

        {/* 公共示例案件：先交代「这不是你的、改不了、怎么才能改」。
            不放进表单卡片里，是为了让它出现在页面顶部——用户一进来就看到，
            而不是填完一堆字段才发现保存按钮是灰的。 */}
        {isPublic && (
          <div className="flex flex-wrap items-center gap-3 mt-3 rounded-lg border border-line bg-surface px-3 py-2.5">
            <span className="text-xs text-muted">
              这是公共示例案件，所有人可见、只读。认领到自己账号后可编辑与评估。
            </span>
            <button
              onClick={claim}
              disabled={claiming}
              className="ml-auto text-xs text-fg underline hover:opacity-70 disabled:opacity-50 whitespace-nowrap"
            >
              {claiming ? '认领中…' : '认领到我的账号'}
            </button>
          </div>
        )}

        {/* 摘要带：把关键事实前置，进页面即可确认是哪个案子、什么目标、证据齐不齐 */}
        <div className="flex flex-wrap gap-2 mt-3">
          <SummaryChip label="案由" value={detail.cause_type} />
          <SummaryChip label="业务目标" value={detail.goal_type} />
          <SummaryChip label="证据" value={fileCount > 0 ? `${fileCount} 份文件` : '未上传'} />
        </div>

        {/* 标签导航：独立成行 + 下划线指示（原先只靠字重，选中态几乎看不出来） */}
        <div className="flex items-center gap-6 mt-6 border-b border-line">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setActiveTab(t.key)}
              className={`relative pb-3 text-sm transition-colors ${
                activeTab === t.key ? 'text-fg font-medium' : 'text-muted hover:text-fg'
              }`}
            >
              {t.label}
              {activeTab === t.key && (
                <span className="absolute -bottom-px left-0 right-0 h-[2px] rounded-full bg-fg" />
              )}
            </button>
          ))}
        </div>

        <div className="mt-6">
          {activeTab === 'detail' && (
            <CaseDetailTab
              detail={detail}
              evaluated={evaluated}
              activeStep={activeStep}
              onActiveStepChange={setActiveStep}
              onSaved={onDraftSaved}
              onStarted={onDraftStarted}
              onStartMoot={onDetailStartMoot}
              readOnly={isPublic}
            />
          )}
          {activeTab === 'run' && (
            <>
              {(phase === 'running' || phase === 'paused') && (
                <RunControlBar
                  className="mb-4"
                  phase={phase === 'paused' ? 'paused' : 'running'}
                  done={doneCount}
                  total={NODE_ORDER.length}
                  onPause={pauseEval}
                  onResume={resumeEval}
                  onStop={stopEval}
                />
              )}
              <EvalRun
              caseId={id!}
              result={result}
              states={states}
              phase={phase}
              finished={finished}
              error={evalError}
              activeAxis={activeAxis}
              onAxisChange={setActiveAxis}
              onStart={startEval}
              onViewResult={() => setActiveTab('result')}
              onRerun={rerunNode}
              onStartMoot={() => startMoot('embedded')}
              onStopMoot={stopMoot}
              moot={moot}
              onMootPaceChange={setMootPace}
              onMootTogglePause={toggleMootPause}
              onMootStep={mootStep}
              traceEvents={traceEvents}
              // finished 只在 SSE 事件里填过，刷新页面后为空；
              // 因此再兜一层后端结果状态，保证「终止后刷新」仍能说清为什么没有结果。
              voided={finished === 'aborted' || result?.status === 'aborted'}
            />
            </>
          )}
          {activeTab === 'result' && (
            <DecisionDashboard
              activeSection={resultSection}
              onSectionChange={setResultSection}
            />
          )}
        </div>

        {injectedInfo && (
          <div className="bg-[var(--info-soft)] text-[var(--info)] rounded-lg px-4 py-2 text-xs mt-4">
            {injectedInfo}
          </div>
        )}
      </div>
    </div>
  )
}

/** 摘要带上的单个事实标签（无值时不渲染，避免出现「业务目标：—」这类空壳） */
function SummaryChip({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 text-xs">
      <span className="text-muted">{label}</span>
      <span className="text-fg">{value}</span>
    </span>
  )
}

// ---------------------------------------------------------------------------
// 案件详情标签：始终为可编辑表单（与新建案件页共用 NewCaseForm，布局同步加分）
// evaluated 时把「建议重新评估」提示交给表单吸底操作条，不再用满宽色块压住页首
// ---------------------------------------------------------------------------
function CaseDetailTab({
  detail,
  evaluated,
  activeStep,
  onActiveStepChange,
  onSaved,
  onStarted,
  onStartMoot,
  readOnly = false,
}: {
  detail: any
  evaluated: boolean
  activeStep: string
  onActiveStepChange: (id: string) => void
  onSaved: () => void
  onStarted: () => void
  onStartMoot: (caseId: string) => void
  /** 公共示例案件：表单整体禁用，改不了也跑不了评估 */
  readOnly?: boolean
}) {
  const initial: CaseFormInitial = {
    name: detail.name ?? '',
    cause_type: detail.cause_type ?? '商标侵权',
    goal_type: detail.goal_type ?? '要钱',
    client_org: detail.client_org ?? '',
    defendant_name: detail.context?.defendant_info?.name ?? '',
    defendant_type: detail.context?.defendant_info?.type ?? 'company',
    case_description: detail.case_description ?? '',
    evidence_texts: detail.context?.defendant_info?.evidence_texts ?? '',
    viewpoints: detail.context?.user_viewpoints ?? [],
    evidence_files: detail.evidence_files ?? [],
  }
  return (
    <NewCaseForm
      caseId={detail.id}
      initial={initial}
      activeStep={activeStep}
      onActiveStepChange={onActiveStepChange}
      readOnly={readOnly}
      notice={evaluated ? '已有评估结果，修改后建议重新评估' : undefined}
      onCreated={(cid, status) => {
        if (status === 'pending') onStarted()
        else onSaved()
      }}
      onStartMoot={onStartMoot}
    />
  )
}

