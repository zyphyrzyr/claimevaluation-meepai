import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { STEP_MOTION } from '../lib/motion'
import { api, humanError, type CaseCreatePayload } from '../api'
import { cn } from '../lib/utils'
import EvidencePreview, { PreviewTarget } from './EvidencePreview'
import ConfirmDialog from './ConfirmDialog'

/**
 * 新建/编辑案件表单：从 NewCase 页面抽取的可复用组件。
 * - 创建模式（无 caseId）：底部「保存」（仅校验名称）/「保存并启动评估」（校验全部 *）
 * - 编辑模式（传 caseId + initial）：回填草稿字段，保存同样两按钮，启动评估前先落库最新改动
 * 不直接路由，结果由父级通过 onCreated(caseId, status) 决定后续行为。
 * 配色已迁到双主题 token（原硬编码配色全部去除）。
 *
 * 布局：拆成「基本信息 / 当事人 / 案情与证据 / 观点注入」四个分区（FORM_SECTIONS 单一事实源）。
 * 右侧工作区为「限高卡片 + 单块逐步显示」：同一时刻只渲染当前分区（activeStep），
 * 切换分区用 framer-motion 做旧块淡出上移 / 新块淡入下移的过渡；卡片自身限高、内部滚动，
 * 顶部标题与底部操作条固定（不随内容滚动）。左侧章节导航（SectionNav）是这些分区的步骤选择器。
 * 分区小标题（13px）保留在每块正文顶部，与导航互为呼应、不重复承担滚动锚点。
 * 不再套灰底大卡片——卡片与输入框同色会让输入框只剩一条浅边；改由「白页面 + 浅灰输入框」
 * 拉开层次，在「案件详情」页（白底）与「新建案件」抽屉（bg-canvas 白底）两个场景下都成立。
 */
export interface CaseFormInitial {
  name: string
  cause_type: string
  goal_type: string
  client_org: string
  defendant_name: string
  defendant_type: string
  case_description: string
  evidence_texts: string
  viewpoints: string[]
  evidence_files: { id: string; file_name: string; parse_status: string }[]
}

/**
 * 表单分区定义：分区标题与步骤的单一事实源。
 * 左侧章节导航（SectionNav）、窄屏兜底分段控件、单块逐步渲染都从这里取，
 * 新增/调整分区时只改这一处，导航、兜底控件与渲染自动同步。
 */
export const FORM_SECTIONS = [
  { id: 'form-basic', label: '基本信息' },
  { id: 'form-parties', label: '当事人' },
  { id: 'form-evidence', label: '案情与证据' },
  { id: 'form-viewpoints', label: '观点注入（可选）' },
] as const

/** 每个分区下参与必填校验的字段 key（用于校验失败自动跳到首个缺项块 + 标红） */
const SECTION_FIELDS: Record<string, string[]> = {
  'form-basic': ['name'],
  'form-parties': ['client_org', 'defendant_name'],
  'form-evidence': ['case_description', 'evidence_texts'],
  'form-viewpoints': [],
}

export default function NewCaseForm({
  onCreated,
  onStartMoot,
  caseId,
  initial,
  notice,
  activeStep,
  onActiveStepChange,
  navBreakpoint = 'xl',
  readOnly = false,
  /**
   * 是否需要「重新评估二次确认」。仅当案件「已有结果且非中止态」时为真——
   * 此时点「保存并启动评估」会清空前一轮结果，必须先让用户确认。
   * 由父级（CaseWorkbench → CaseDetailTab）根据案件状态算出后透传。
   */
  reEvalNeedsConfirm = false,
}: {
  onCreated: (caseId: string, status: string) => void
  onStartMoot?: (caseId: string) => void
  caseId?: string
  initial?: CaseFormInitial
  /** 页级提示（如「已有评估结果」），展示在底栏左侧；不传则整行留给操作按钮 */
  notice?: string
  /**
   * 是否需要「重新评估二次确认」。仅当案件「已有结果且非中止态」时为真——
   * 此时点「保存并启动评估」会清空前一轮结果，必须先让用户确认。
   * 由父级（CaseWorkbench → CaseDetailTab）根据案件状态算出后透传。
   */
  reEvalNeedsConfirm?: boolean
  /**
   * 只读展示（公共示例案件）。
   *
   * 用 `<fieldset disabled>` 包住整张卡片，让浏览器原生地禁用内部所有输入与按钮——
   * 逐个输入加 disabled 要改十几处，漏一个就会出现「能填但保存报错」。
   * 只读的原因由父级在卡片外说明（认领引导），不塞进这张卡里。
   */
  readOnly?: boolean
  /** 当前显示的分区块 id（由父级维护，受控） */
  activeStep?: string
  /** 切换分区块（导航点击 / 校验失败自动跳块时调用） */
  onActiveStepChange?: (id: string) => void
  /** 导航列隐藏的断点：整页用 xl，抽屉用 lg（决定窄屏横向分段兜底的隐藏时机） */
  navBreakpoint?: 'lg' | 'xl'
}) {
  const [meta, setMeta] = useState<{ cause_types: string[]; goal_types: string[] }>({
    cause_types: ['商标侵权', '著作权侵权', '不正当竞争'],
    goal_types: ['要钱', '要名'],
  })
  const [form, setForm] = useState(() =>
    initial
      ? {
          name: initial.name ?? '',
          cause_type: initial.cause_type ?? '商标侵权',
          goal_type: initial.goal_type ?? '要钱',
          client_org: initial.client_org ?? '',
          defendant_name: initial.defendant_name ?? '',
          defendant_type: initial.defendant_type ?? 'company',
          case_description: initial.case_description ?? '',
          evidence_texts: initial.evidence_texts ?? '',
        }
      : {
          name: '',
          cause_type: '商标侵权',
          goal_type: '要钱',
          client_org: '',
          defendant_name: '',
          defendant_type: 'company',
          case_description: '',
          evidence_texts: '',
        },
  )
  const [viewpoints, setViewpoints] = useState<string[]>(() =>
    initial?.viewpoints?.length ? initial.viewpoints : [''],
  )
  // 历史上传的文件（编辑草稿时从后端拉取，只读展示 + 可删除）
  const [savedFiles, setSavedFiles] = useState<CaseFormInitial['evidence_files']>(() =>
    initial?.evidence_files ?? [],
  )
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [invalid, setInvalid] = useState<Set<string>>(new Set())
  /** 当前预览的附件（null = 关闭）。已保存文件带 fileId，未保存的本地文件带 file 对象 */
  const [preview, setPreview] = useState<PreviewTarget>(null)
  const [files, setFiles] = useState<File[]>([])
  const [dragOver, setDragOver] = useState(false)
  const [busyDelFile, setBusyDelFile] = useState<string | null>(null)
  const [descImportBusy, setDescImportBusy] = useState(false)
  const [noticeDismissed, setNoticeDismissed] = useState(false)
  const [zipSummary, setZipSummary] = useState<{
    total: number
    ok: number
    failed: number
    skipped: number
    warnings: string[]
  } | null>(null)

  const scrollRef = useRef<HTMLDivElement>(null)
  const current = activeStep ?? FORM_SECTIONS[0].id

  // 切换分区时把滚动区复位到顶部，避免旧块的位置残留到新块
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 })
  }, [current])

  useEffect(() => {
    api.meta().then(setMeta).catch(() => {})
  }, [])

  const clearInvalid = (key: string) =>
    setInvalid((prev) => (prev.has(key) ? new Set([...prev].filter((k) => k !== key)) : prev))

  const set = (key: string) => (e: any) => {
    setForm({ ...form, [key]: e.target.value })
    clearInvalid(key)
    if (key === 'evidence_texts' && e.target.value.trim()) clearInvalid('evidence_texts')
  }

  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return
    const accepted = Array.from(incoming).filter((f) => {
      const name = f.name.toLowerCase()
      return (
        name.endsWith('.pdf') ||
        name.endsWith('.png') ||
        name.endsWith('.jpg') ||
        name.endsWith('.jpeg') ||
        name.endsWith('.doc') ||
        name.endsWith('.docx') ||
        name.endsWith('.zip')
      )
    })
    setFiles((prev) => [...prev, ...accepted])
    if (accepted.length) clearInvalid('evidence_texts')
  }
  const removeFile = (idx: number) => {
    setFiles((prev) => {
      const next = prev.filter((_, i) => i !== idx)
      if (next.length === 0 && !form.evidence_texts.trim()) setInvalid((p) => new Set(p).add('evidence_texts'))
      return next
    })
  }
  const removeViewpoint = (idx: number) => setViewpoints((prev) => prev.filter((_, i) => i !== idx))

  const removeSavedFile = async (fileId: string) => {
    if (!caseId) return
    setBusyDelFile(fileId)
    try {
      await api.deleteEvidenceFile(caseId, fileId)
      setSavedFiles((prev) => prev.filter((f) => f.id !== fileId))
    } catch (e) {
      setError(humanError(e))
    } finally {
      setBusyDelFile(null)
    }
  }

  /** 组装提交体：有文件走 multipart（evidence_files），否则 JSON；draft 控制后端存草稿还是正式建案。 */
  function buildPayload(draft: boolean): CaseCreatePayload | FormData {
    const base: CaseCreatePayload = {
      ...form,
      viewpoints: viewpoints.filter((v) => v.trim()),
      draft,
    }
    if (files.length > 0) {
      const formData = new FormData()
      formData.append('payload', JSON.stringify(base))
      files.forEach((f) => formData.append('evidence_files', f))
      return formData
    }
    return base
  }

  /**
   * 必填校验：返回首个缺项分区 id（'' 表示通过）。同时把缺项字段写入 invalid 用于标红。
   * 调用方据此自动跳到对应块并展示报错，缺项在别的块时用户也能直接看到标红。
   */
  const validateRequired = (): string => {
    const miss = new Set<string>()
    if (!form.name.trim()) miss.add('name')
    if (!form.client_org.trim()) miss.add('client_org')
    if (!form.defendant_name.trim()) miss.add('defendant_name')
    if (!form.case_description.trim()) miss.add('case_description')
    if (!form.evidence_texts.trim() && files.length === 0) miss.add('evidence_texts')
    if (miss.size === 0) {
      setInvalid(new Set())
      return ''
    }
    setInvalid(miss)
    const section = FORM_SECTIONS.find((s) => SECTION_FIELDS[s.id].some((k) => miss.has(k)))
    const labels: Record<string, string> = {
      name: '案件名称',
      client_org: '我司主体（原告）',
      defendant_name: '被告名称',
      case_description: '案情描述',
      evidence_texts: '证据材料',
    }
    setError(`必填项未填写：${[...miss].map((k) => labels[k]).join('、')}`)
    return section?.id ?? ''
  }

  /** 保存草稿：创建模式建 draft 案件；编辑模式 PUT 覆盖。仅校验案件名称。 */
  async function saveDraft() {
    setError('')
    if (!form.name.trim()) {
      setInvalid(new Set(['name']))
      setError('请至少填写案件名称')
      onActiveStepChange?.('form-basic')
      return
    }
    setSubmitting(true)
    try {
      const payload = buildPayload(true)
      if (caseId) {
        const updated = await api.updateDraft(caseId, payload)
        setZipSummary(updated.parse_summary ?? null)
        onCreated(caseId, 'draft')
      } else {
        const created = await api.createCase(payload)
        setZipSummary(created.parse_summary ?? null)
        onCreated(created.id, 'draft')
      }
      setSubmitting(false)
    } catch (e) {
      setError(humanError(e))
      setSubmitting(false)
    }
  }

  /** 开始评估：校验全部 * 字段。创建模式直接正式建案；编辑模式先落库最新改动再启动评估。
   *  若本案已有结果（reEvalNeedsConfirm 为真），先弹二次确认，确认后才真正清空前轮并重跑。 */
  async function startEval() {
    setError('')
    const miss = validateRequired()
    if (miss) {
      onActiveStepChange?.(miss)
      return
    }
    if (reEvalNeedsConfirm) {
      setShowReEvalConfirm(true)
      return
    }
    setSubmitting(true)
    try {
      await doStartEval()
    } catch (e) {
      setError(humanError(e))
      setSubmitting(false)
    }
  }

  /** 仅开始模拟法庭：不要求必填全，仅确保最新改动已落库，再交由父级进入模拟法庭步骤 */
  async function startMoot() {
    setError('')
    if (!form.name.trim()) {
      setInvalid(new Set(['name']))
      setError('请至少填写案件名称')
      onActiveStepChange?.('form-basic')
      return
    }
    setSubmitting(true)
    try {
      let cid = caseId
      if (!cid) {
        const created = await api.createCase(buildPayload(true))
        cid = created.id
      } else {
        await api.updateDraft(cid, buildPayload(true))
      }
      onStartMoot?.(cid)
    } catch (e) {
      setError(humanError(e))
      setSubmitting(false)
    }
  }

  /** 真正执行「落库 + 启动评估」：供首次启动与「二次确认后重跑」共用 */
  async function doStartEval() {
    if (caseId) {
      const updated = await api.updateDraft(caseId, buildPayload(true))
      setZipSummary(updated.parse_summary ?? null)
      await api.startEvaluation(caseId)
      onCreated(caseId, 'pending')
    } else {
      const created = await api.createCase(buildPayload(false))
      setZipSummary(created.parse_summary ?? null)
      onCreated(created.id, 'pending')
    }
  }

  /** 整体重新评估的二次确认弹窗状态 */
  const [showReEvalConfirm, setShowReEvalConfirm] = useState(false)

  // 输入框用 surface 浅灰底：页面/抽屉都是白底，浅灰底才能让输入框有明确边界
  const inputCls =
    'w-full bg-surface border border-line rounded-lg px-3 py-2 text-sm text-fg placeholder:text-muted focus:outline-none focus:border-fg transition-colors'
  const labelCls = 'block text-xs text-muted mb-1.5'
  /** 必填字段标红：用 !border-danger 强行覆盖 inputCls 里的 border-line */
  const fieldCls = (key: string) => cn(inputCls, invalid.has(key) && '!border-danger')

  const attachCount = savedFiles.length + files.length
  const descLen = form.case_description.length

  const sectionLabel = (id: string) => FORM_SECTIONS.find((s) => s.id === id)?.label ?? ''

  /** 单块渲染：每块顶部带 13px 小标题，与左侧导航呼应 */
  const renderSection = (id: string) => {
    const head = <h3 className="text-[13px] font-medium text-fg mb-4">{sectionLabel(id)}</h3>
    switch (id) {
      case 'form-basic':
        return (
          <>
            {head}
            <div>
              <label className={labelCls}>
                案件名称 <Req />
              </label>
              <input
                className={fieldCls('name')}
                value={form.name}
                onChange={set('name')}
                placeholder="如：XX 商标侵权主诉评估"
              />
            </div>
            {/* 案由与业务目标成对：一个决定法律依据，一个决定评估分支 */}
            <div className="grid grid-cols-2 gap-4 mt-4">
              <div>
                <label className={labelCls}>
                  案由 <Req />
                </label>
                <select className={inputCls} value={form.cause_type} onChange={set('cause_type')}>
                  {meta.cause_types.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className={labelCls}>
                  业务目标 <Req />
                </label>
                {/* 分段控件：选中态整块反白，避免原先两个按钮「长得像输入框」 */}
                <div className="flex rounded-lg border border-line overflow-hidden">
                  {meta.goal_types.map((t, i) => {
                    const on = form.goal_type === t
                    return (
                      <button
                        key={t}
                        type="button"
                        onClick={() => setForm({ ...form, goal_type: t })}
                        className={cn(
                          'flex-1 px-3 py-[7px] text-sm transition-colors',
                          i > 0 && 'border-l border-line',
                          on
                            ? 'bg-fg text-canvas font-medium'
                            : 'bg-surface text-muted hover:text-fg',
                        )}
                      >
                        {t === '要钱' ? '要钱（判赔规模 · 回款能力）' : '要名（判例价值）'}
                      </button>
                    )
                  })}
                </div>
              </div>
            </div>
          </>
        )

      case 'form-parties':
        return (
          <>
            {head}
            {/* 原告/被告天然成对照，同行呈现便于核对主体是否写反 */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={labelCls}>
                  我司主体（原告） <Req />
                </label>
                <input
                  className={fieldCls('client_org')}
                  value={form.client_org}
                  onChange={set('client_org')}
                  placeholder="原告公司名称（主诉评估须由权利人发起）"
                />
              </div>
              <div>
                <label className={labelCls}>
                  被告名称 <Req />
                </label>
                <input
                  className={fieldCls('defendant_name')}
                  value={form.defendant_name}
                  onChange={set('defendant_name')}
                  placeholder="用于企查查被告画像与回款能力评估"
                />
              </div>
            </div>
          </>
        )

      case 'form-evidence':
        return (
          <div className="flex flex-col h-full">
            {head}
            <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_20rem] gap-6 lg:items-stretch lg:flex-1 lg:min-h-0">
              {/* 左列：案情描述 + 证据材料文本 */}
              <div className="min-w-0 lg:flex lg:flex-col lg:gap-5">
                <div className="lg:flex-1 lg:min-h-0 lg:flex lg:flex-col">
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-xs text-muted">
                      案情描述 <Req />
                    </label>
                    <div className="flex items-center gap-3">
                      {descLen > 0 && (
                        <span className="text-xs text-muted">{descLen.toLocaleString()} 字</span>
                      )}
                      <label className="flex items-center gap-1 text-xs text-muted hover:text-fg cursor-pointer transition-colors">
                        <input
                          type="file"
                          accept=".txt,.md,.docx,.pdf"
                          className="hidden"
                          disabled={descImportBusy}
                          onChange={async (e) => {
                            const file = e.target.files?.[0]
                            if (!file) return
                            setDescImportBusy(true)
                            setError('')
                            try {
                              const res = await api.uploadDescriptionText(file)
                              const marker = `\n\n--- 来自「${res.filename}」---\n`
                              setForm((prev) => {
                                const base = prev.case_description.trim()
                                const next = base ? base + marker + res.text : res.text
                                if (next.trim()) clearInvalid('case_description')
                                return { ...prev, case_description: next }
                              })
                            } catch (err) {
                              setError(String(err))
                            } finally {
                              setDescImportBusy(false)
                              e.target.value = ''
                            }
                          }}
                        />
                        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24">
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8m-5-5 5 5m-5-5v5h5"
                          />
                        </svg>
                        {descImportBusy ? '导入中…' : '从文件导入'}
                      </label>
                    </div>
                  </div>
                  <textarea
                    className={cn(fieldCls('case_description'), 'h-40 resize-y lg:h-auto lg:flex-1 lg:min-h-0 lg:resize-none w-full')}
                    value={form.case_description}
                    onChange={set('case_description')}
                    placeholder="侵权发现经过、涉案标识/作品、侵权形式、侵权规模（销量/店铺数）、已掌握的证据情况……"
                  />
                </div>

                {/* min-h 是地板值：卡片最矮时（clamp 下限 24rem）两个框也各约 5 行，
                    不会再被压成两行；案情描述那侧保持 min-h-0，空间紧张时优先让它让位。 */}
                <div className="mt-5 lg:mt-0 lg:flex-1 lg:min-h-[7rem] lg:flex lg:flex-col">
                  <label className={labelCls}>
                    证据材料文本 <Req />
                  </label>
                  <textarea
                    className={cn(fieldCls('evidence_texts'), 'h-28 resize-y lg:h-auto lg:flex-1 lg:min-h-0 lg:resize-none w-full')}
                    value={form.evidence_texts}
                    onChange={set('evidence_texts')}
                    placeholder="粘贴证据清单或关键证据文本；上传文件的解析文本会自动追加到此处"
                  />
                </div>
              </div>

              {/* 右列：证据附件卡片（点击行可预览，列表区内部滚动） */}
              <div className="min-w-0 mt-5 lg:mt-0 lg:flex lg:flex-col bg-surface border border-line rounded-lg lg:overflow-hidden">
                <div className="flex items-center justify-between px-3 py-2 border-b border-line lg:shrink-0">
                  <span className="text-xs text-muted">
                    证据附件{attachCount > 0 ? `（${attachCount}）` : ''}
                  </span>
                </div>

                <div className="lg:flex-1 lg:min-h-0 lg:overflow-y-auto">
                  {attachCount > 0 && (
                  <ul className="divide-y divide-line">
                    {savedFiles.map((f) => (
                      <li
                        key={f.id}
                        onClick={() => setPreview({ fileName: f.file_name, fileId: f.id })}
                        className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-line transition-colors"
                      >
                        <FileStatusBadge
                          ok={f.parse_status === 'ok'}
                          label={f.parse_status === 'ok' ? '已解析' : '解析失败'}
                        />
                        <span className="flex-1 min-w-0 truncate text-sm text-fg">{f.file_name}</span>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation()
                            removeSavedFile(f.id)
                          }}
                          disabled={busyDelFile === f.id}
                          className="text-xs text-muted hover:text-danger shrink-0 transition-colors"
                        >
                          {busyDelFile === f.id ? '删除中…' : '删除'}
                        </button>
                      </li>
                    ))}
                    {files.map((f, i) => (
                      <li
                        key={`${f.name}-${i}`}
                        onClick={() => setPreview({ fileName: f.name, file: f })}
                        className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-line transition-colors"
                      >
                        <FileStatusBadge ok={null} label="待解析" />
                        <span className="flex-1 min-w-0 truncate text-sm text-fg">{f.name}</span>
                        <span className="text-xs text-muted shrink-0">{(f.size / 1024).toFixed(1)} KB</span>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation()
                            removeFile(i)
                          }}
                          className="text-xs text-muted hover:text-danger shrink-0 transition-colors"
                        >
                          移除
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                </div>

                <div className="lg:shrink-0 lg:p-3 lg:border-t lg:border-line lg:space-y-2">
                  <label
                    className={cn(
                      'flex items-center justify-center gap-2 rounded-lg border border-dashed px-4 py-3 cursor-pointer transition-colors',
                      dragOver ? 'border-fg bg-canvas' : 'border-line hover:border-fg hover:bg-canvas',
                    )}
                  onDragOver={(e) => {
                    e.preventDefault()
                    setDragOver(true)
                  }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={(e) => {
                    e.preventDefault()
                    setDragOver(false)
                    addFiles(e.dataTransfer.files)
                  }}
                >
                  <input
                    type="file"
                    multiple
                    accept=".pdf,.png,.jpg,.jpeg,.doc,.docx,.zip"
                    className="hidden"
                    onChange={(e) => addFiles(e.target.files)}
                  />
                  <svg
                    className="w-4 h-4 text-muted shrink-0"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    viewBox="0 0 24 24"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 16V4m0 0L8 8m4-4 4 4M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
                  </svg>
                  <span className="text-xs text-muted">
                    拖拽或点击上传 PDF / 图片 / DOC(X) / ZIP
                  </span>
                </label>

                {zipSummary && (
                  <div className="mt-2 lg:mt-0 rounded-lg border border-line bg-canvas px-3 py-2.5 text-sm">
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-medium text-fg">文件解析摘要</span>
                      <button
                        type="button"
                        onClick={() => setZipSummary(null)}
                        className="text-xs text-muted hover:text-fg"
                      >
                        清除
                      </button>
                    </div>
                    <div className="flex gap-3 text-xs text-muted mb-1.5">
                      <span>总计 {zipSummary.total}</span>
                      <span className="text-[var(--success)]">成功 {zipSummary.ok}</span>
                      {zipSummary.failed > 0 && (
                        <span className="text-[var(--danger)]">失败 {zipSummary.failed}</span>
                      )}
                      {zipSummary.skipped > 0 && <span>跳过 {zipSummary.skipped}</span>}
                    </div>
                    {zipSummary.warnings.length > 0 && (
                      <ul className="space-y-0.5 text-xs text-muted list-disc list-inside">
                        {zipSummary.warnings.map((w, i) => (
                          <li key={i}>{w}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
                </div>
              </div>
            </div>
          </div>
        )

      case 'form-viewpoints':
        return (
          <>
            {head}
            <p className="text-xs text-muted mb-3">
              你的判断将作为重要参考注入全部评估环节，并在报告中标注留痕
            </p>
            {viewpoints.map((v, i) => (
              <div key={i} className="flex items-center gap-2 mb-2">
                <input
                  className={inputCls}
                  value={v}
                  onChange={(e) => {
                    const next = [...viewpoints]
                    next[i] = e.target.value
                    setViewpoints(next)
                  }}
                  placeholder="如：我认为被告是惯犯，应主张惩罚性赔偿"
                />
                <button
                  type="button"
                  onClick={() => removeViewpoint(i)}
                  className="text-xs text-muted hover:text-danger shrink-0 transition-colors"
                >
                  删除
                </button>
              </div>
            ))}
            <button
              type="button"
              onClick={() => setViewpoints([...viewpoints, ''])}
              className="text-xs text-muted hover:text-fg transition-colors"
            >
              + 添加一条
            </button>
          </>
        )

      default:
        return null
    }
  }

  // fieldset 只做「整体禁用」用，不参与布局：外层调用点都是普通的 min-w-0 容器，
  // 多一层块级包装不改变卡片本身的限高与撑满链（卡片自带 clamp 高度）。
  return (
    <>
    <fieldset disabled={readOnly} className="min-w-0 border-0 p-0 m-0">
    <div
      className="rounded-xl border border-line bg-canvas flex flex-col overflow-hidden"
      style={{ height: 'clamp(24rem, calc(100vh - 14rem), 46rem)' }}
    >
      {/* 窄屏兜底：导航列在 xl/lg 以下隐藏，这里用横向分段控件补上切换入口，否则其余块无法访问 */}
      <div
        className={cn(
          'flex flex-wrap gap-2 p-3 border-b border-line',
          navBreakpoint === 'lg' ? 'lg:hidden' : 'xl:hidden',
        )}
      >
        {FORM_SECTIONS.map((s) => {
          const on = current === s.id
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => onActiveStepChange?.(s.id)}
              className={cn(
                'px-3 py-1.5 rounded-lg text-sm border transition-colors',
                on
                  ? 'bg-fg text-canvas border-fg font-medium'
                  : 'bg-surface text-muted border-line hover:text-fg',
              )}
            >
              {s.label}
            </button>
          )
        })}
      </div>

      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto px-6 py-5">
        {error && (
          <div className="bg-[var(--danger-soft)] text-[var(--danger)] text-sm rounded-lg p-3 mb-4">
            {error}
          </div>
        )}
        <AnimatePresence mode="wait">
          {/* 案情与证据块内部靠 h-full + flex 撑满高度（左列两个 textarea 按比例分配、右列卡片列表滚动），
              这需要本层有「确定高度」，否则子元素的 height:100% 无法解析、整条撑满链塌掉，
              textarea 退回浏览器默认的两行高。因此仅对该块开启 h-full。
              切换动画来自 lib/motion.ts 的 STEP_MOTION，与评估详情共用同一套参数。 */}
          <motion.div
            key={current}
            className={cn(current === 'form-evidence' && 'h-full')}
            {...STEP_MOTION}
          >
            {renderSection(current)}
          </motion.div>
        </AnimatePresence>
      </div>

      {/* 底栏：卡片固定底栏（不再 sticky，因为卡片自身限高、底栏是 flex 子项，始终可见） */}
      <div className="flex flex-wrap items-center gap-3 border-t border-line bg-canvas px-6 py-4">
        {notice && !noticeDismissed && (
          <div className="flex items-center gap-2 min-w-0 text-xs text-[var(--warning)]">
            <span className="truncate">{notice}</span>
            <button
              type="button"
              onClick={() => setNoticeDismissed(true)}
              aria-label="关闭提示"
              className="shrink-0 text-muted hover:text-fg"
            >
              ✕
            </button>
          </div>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={startMoot}
            disabled={submitting || readOnly}
            className="text-sm text-muted hover:text-fg disabled:opacity-50 whitespace-nowrap transition-colors"
          >
            仅开始模拟法庭
          </button>
          <button
            type="button"
            onClick={saveDraft}
            disabled={submitting || readOnly}
            className="px-4 py-2.5 rounded-lg border border-line text-fg hover:bg-surface disabled:opacity-50 text-sm font-medium transition-colors"
          >
            {submitting ? '保存中…' : '保存'}
          </button>
          <button
            type="button"
            onClick={startEval}
            disabled={submitting || readOnly}
            className="px-5 py-2.5 rounded-lg bg-fg text-canvas hover:opacity-90 disabled:opacity-50 text-sm font-medium transition-colors"
          >
            {submitting ? '处理中…' : caseId ? '保存并启动评估' : '创建案件并开始评估'}
          </button>
        </div>
      </div>

      {/* 附件预览：右侧滑出面板（复用 SlideOver），层级高于抽屉本体；点击附件行时弹出 */}
      <EvidencePreview target={preview} caseId={caseId} onClose={() => setPreview(null)} />

    </div>
    </fieldset>

    {/* 整体重新评估二次确认：放在 fieldset 外，避免被只读态的 disabled 牵连 */}
    <ConfirmDialog
      open={showReEvalConfirm}
      title="重新评估确认"
      message="本案已有评估结果。确认重新评估后，前一轮的全部评估结果将被清空，并从头开始新一轮评估（此操作不可撤销）。"
      confirmText="确认重新评估"
      cancelText="取消"
      danger
      onConfirm={async () => {
        setShowReEvalConfirm(false)
        setSubmitting(true)
        try {
          await doStartEval()
        } catch (e) {
          setError(humanError(e))
          setSubmitting(false)
        }
      }}
      onCancel={() => setShowReEvalConfirm(false)}
    />
    </>
  )
}

/** 必填标记 */
function Req() {
  return <span className="text-[var(--danger)]">*</span>
}

/** 附件状态徽标：固定宽度成列，避免每行徽标长短不一导致的参差 */
function FileStatusBadge({ ok, label }: { ok: boolean | null; label: string }) {
  return (
    <span
      className={cn(
        'shrink-0 w-[3.75rem] text-center text-[10px] px-1.5 py-0.5 rounded',
        ok === true && 'bg-[var(--success-soft)] text-[var(--success)]',
        ok === false && 'bg-[var(--danger-soft)] text-[var(--danger)]',
        ok === null && 'bg-line text-muted',
      )}
    >
      {label}
    </span>
  )
}
