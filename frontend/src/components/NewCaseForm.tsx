import { useEffect, useState } from 'react'
import { api, CaseCreatePayload } from '../api'

/**
 * 新建/编辑案件表单：从 NewCase 页面抽取的可复用组件。
 * - 创建模式（无 caseId）：底部「保存草稿」（仅校验名称）/「开始评估」（校验全部 *）
 * - 编辑模式（传 caseId + initial）：回填草稿字段，保存同样两按钮，启动评估前先落库最新改动
 * 不直接路由，结果由父级通过 onCreated(caseId, status) 决定后续行为。
 * 配色已迁到双主题 token（原硬编码配色全部去除）。
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

export default function NewCaseForm({
  onCreated,
  onStartMoot,
  caseId,
  initial,
}: {
  onCreated: (caseId: string, status: string) => void
  onStartMoot?: (caseId: string) => void
  caseId?: string
  initial?: CaseFormInitial
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
  const [files, setFiles] = useState<File[]>([])
  const [dragOver, setDragOver] = useState(false)
  const [busyDelFile, setBusyDelFile] = useState<string | null>(null)

  useEffect(() => {
    api.meta().then(setMeta).catch(() => {})
  }, [])

  const set = (key: string) => (e: any) => setForm({ ...form, [key]: e.target.value })

  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return
    const accepted = Array.from(incoming).filter((f) => {
      const name = f.name.toLowerCase()
      return (
        name.endsWith('.pdf') ||
        name.endsWith('.png') ||
        name.endsWith('.jpg') ||
        name.endsWith('.jpeg')
      )
    })
    setFiles((prev) => [...prev, ...accepted])
  }
  const removeFile = (idx: number) => setFiles((prev) => prev.filter((_, i) => i !== idx))
  const removeViewpoint = (idx: number) => setViewpoints((prev) => prev.filter((_, i) => i !== idx))

  const removeSavedFile = async (fileId: string) => {
    if (!caseId) return
    setBusyDelFile(fileId)
    try {
      await api.deleteEvidenceFile(caseId, fileId)
      setSavedFiles((prev) => prev.filter((f) => f.id !== fileId))
    } catch (e) {
      setError(String(e))
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

  const validateRequired = (): boolean => {
    if (
      !form.name.trim() ||
      !form.client_org.trim() ||
      !form.defendant_name.trim() ||
      !form.case_description.trim()
    ) {
      setError('案件名称、我司主体（原告）、被告名称和案情描述为必填项')
      return false
    }
    // 证据：文本或上传文件至少其一（编辑模式下已上传的文件不在此计数，由后端按 EvidenceFile 兜底）
    if (!form.evidence_texts.trim() && files.length === 0) {
      setError('证据材料文本或上传文件为必填项')
      return false
    }
    return true
  }

  /** 保存草稿：创建模式建 draft 案件；编辑模式 PUT 覆盖。仅校验案件名称。 */
  async function saveDraft() {
    setError('')
    if (!form.name.trim()) {
      setError('请至少填写案件名称')
      return
    }
    setSubmitting(true)
    try {
      const payload = buildPayload(true)
      if (caseId) {
        await api.updateDraft(caseId, payload)
        onCreated(caseId, 'draft')
      } else {
        const created = await api.createCase(payload)
        onCreated(created.id, 'draft')
      }
      setSubmitting(false)
    } catch (e) {
      setError(String(e))
      setSubmitting(false)
    }
  }

  /** 开始评估：校验全部 * 字段。创建模式直接正式建案；编辑模式先落库最新改动再启动评估。 */
  async function startEval() {
    setError('')
    if (!validateRequired()) return
    setSubmitting(true)
    try {
      if (caseId) {
        await api.updateDraft(caseId, buildPayload(true))
        await api.startEvaluation(caseId)
        onCreated(caseId, 'pending')
      } else {
        const created = await api.createCase(buildPayload(false))
        onCreated(created.id, 'pending')
      }
    } catch (e) {
      setError(String(e))
      setSubmitting(false)
    }
  }

  /** 仅开始模拟法庭：不要求必填全，仅确保最新改动已落库，再交由父级进入模拟法庭步骤 */
  async function startMoot() {
    setError('')
    if (!form.name.trim()) {
      setError('请至少填写案件名称')
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
      setError(String(e))
      setSubmitting(false)
    }
  }

  const inputCls =
    'w-full bg-surface border border-line rounded-lg px-3 py-2 text-sm text-fg placeholder:text-muted focus:outline-none focus:border-fg transition-colors'
  const labelCls = 'block text-sm font-medium mb-1.5 text-fg'

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className={labelCls}>案件名称 *</label>
          <input
            className={inputCls}
            value={form.name}
            onChange={set('name')}
            placeholder="如：XX 商标侵权主诉评估"
          />
        </div>
        <div>
          <label className={labelCls}>案由 *</label>
          <select className={inputCls} value={form.cause_type} onChange={set('cause_type')}>
            {meta.cause_types.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <label className={labelCls}>业务目标 *</label>
        <div className="flex gap-2">
          {meta.goal_types.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setForm({ ...form, goal_type: t })}
              className={`flex-1 text-sm py-2 rounded-lg border transition-colors ${
                form.goal_type === t
                  ? 'border-fg bg-surface text-fg font-medium'
                  : 'border-line bg-canvas text-muted hover:border-fg'
              }`}
            >
              {t === '要钱' ? '要钱（判赔规模 · 回款能力）' : '要名（判例价值）'}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className={labelCls}>我司主体（原告）*</label>
          <input
            className={inputCls}
            value={form.client_org}
            onChange={set('client_org')}
            placeholder="原告公司名称（主诉评估须由权利人发起）"
          />
        </div>
        <div>
          <label className={labelCls}>被告名称 *</label>
          <input
            className={inputCls}
            value={form.defendant_name}
            onChange={set('defendant_name')}
            placeholder="用于企查查被告画像与回款能力评估"
          />
        </div>
      </div>

      <div>
        <label className={labelCls}>案情描述 *</label>
        <textarea
          className={`${inputCls} h-36 resize-y`}
          value={form.case_description}
          onChange={set('case_description')}
          placeholder="侵权发现经过、涉案标识/作品、侵权形式、侵权规模（销量/店铺数）、已掌握的证据情况……"
        />
      </div>

      <div>
        <label className={labelCls}>证据材料文本 *</label>
        <textarea
          className={`${inputCls} h-24 resize-y`}
          value={form.evidence_texts}
          onChange={set('evidence_texts')}
          placeholder="粘贴证据清单或关键证据文本；也可点击下方上传 PDF/图片，解析后的文本会自动追加到此处"
        />

        {savedFiles.length > 0 && (
          <div className="mt-3">
            <p className="text-xs text-muted mb-2">已上传文件（保存草稿时解析入库，可直接删除）</p>
            <ul className="space-y-2">
              {savedFiles.map((f) => (
                <li
                  key={f.id}
                  className="flex items-center justify-between bg-surface border border-line rounded-lg px-3 py-2"
                >
                  <div className="min-w-0 flex items-center gap-2">
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded shrink-0 ${
                        f.parse_status === 'ok'
                          ? 'bg-[var(--success-soft)] text-[var(--success)]'
                          : 'bg-[var(--danger-soft)] text-[var(--danger)]'
                      }`}
                    >
                      {f.parse_status === 'ok' ? '已解析' : '解析失败'}
                    </span>
                    <p className="text-sm text-fg truncate">{f.file_name}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeSavedFile(f.id)}
                    disabled={busyDelFile === f.id}
                    className="text-xs text-muted hover:text-danger ml-3 shrink-0"
                  >
                    {busyDelFile === f.id ? '删除中…' : '删除'}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-3">
          <label
            className={`flex flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-6 cursor-pointer transition-colors ${
              dragOver
                ? 'border-fg bg-surface'
                : 'border-line bg-canvas hover:border-fg hover:bg-surface'
            }`}
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
              accept=".pdf,.png,.jpg,.jpeg"
              className="hidden"
              onChange={(e) => addFiles(e.target.files)}
            />
            <span className="text-sm text-fg">点击或拖拽上传证据文件</span>
            <span className="text-xs text-muted mt-1">
              支持 PDF / PNG / JPG / JPEG，多文件可选
            </span>
          </label>

          {files.length > 0 && (
            <ul className="mt-3 space-y-2">
              {files.map((f, i) => (
                <li
                  key={`${f.name}-${i}`}
                  className="flex items-center justify-between bg-surface border border-line rounded-lg px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="text-sm text-fg truncate">{f.name}</p>
                    <p className="text-xs text-muted">
                      {(f.size / 1024).toFixed(1)} KB
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeFile(i)}
                    className="text-xs text-muted hover:text-danger ml-3 shrink-0"
                  >
                    移除
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="bg-surface border border-line rounded-xl p-4">
        <div className="flex items-center justify-between mb-2">
          <label className="text-sm font-medium text-fg">观点注入（可选）</label>
          <button
            type="button"
            onClick={() => setViewpoints([...viewpoints, ''])}
            className="text-xs text-muted hover:text-fg"
          >
            + 添加一条
          </button>
        </div>
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
              className="text-xs text-muted hover:text-danger shrink-0"
            >
              删除
            </button>
          </div>
        ))}
      </div>

      {error && <div className="bg-danger-soft text-danger text-sm rounded-lg p-3">{error}</div>}

      <div className="flex gap-3">
        <button
          type="button"
          onClick={saveDraft}
          disabled={submitting}
          className="flex-1 border border-line text-fg hover:bg-surface disabled:opacity-50 py-3 rounded-lg text-sm font-medium transition-colors"
        >
          {submitting ? '保存中…' : '保存草稿'}
        </button>
        <button
          type="button"
          onClick={startEval}
          disabled={submitting}
          className="flex-[2] bg-fg text-canvas hover:opacity-90 disabled:opacity-50 py-3 rounded-lg text-sm font-medium transition-colors"
        >
          {submitting ? '处理中…' : caseId ? '保存并启动评估' : '创建案件并开始评估'}
        </button>
        <button
          type="button"
          onClick={startMoot}
          disabled={submitting}
          className="flex-1 border border-brand/40 text-brand hover:bg-brand/5 disabled:opacity-50 py-3 rounded-lg text-sm font-medium transition-colors whitespace-nowrap"
        >
          {submitting ? '处理中…' : '仅开始模拟法庭'}
        </button>
      </div>
    </div>
  )
}
