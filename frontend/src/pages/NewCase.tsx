import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'

export default function NewCase() {
  const navigate = useNavigate()
  const [meta, setMeta] = useState<{ cause_types: string[]; goal_types: string[] }>({
    cause_types: ['商标侵权', '著作权侵权', '不正当竞争'],
    goal_types: ['要钱', '要名'],
  })
  const [form, setForm] = useState({
    name: '',
    cause_type: '商标侵权',
    goal_type: '要钱',
    client_org: '',
    defendant_name: '',
    case_description: '',
    evidence_texts: '',
  })
  const [viewpoints, setViewpoints] = useState<string[]>([''])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.meta().then(setMeta).catch(() => {})
  }, [])

  const set = (key: string) => (e: any) => setForm({ ...form, [key]: e.target.value })

  async function submit() {
    setError('')
    if (!form.name.trim() || !form.case_description.trim() || !form.client_org.trim()) {
      setError('案件名称、我司主体（原告）和案情描述为必填项')
      return
    }
    setSubmitting(true)
    try {
      const created = await api.createCase({
        ...form,
        viewpoints: viewpoints.filter((v) => v.trim()),
      })
      navigate(`/cases/${created.id}/evaluation`)
    } catch (e) {
      setError(String(e))
      setSubmitting(false)
    }
  }

  const inputCls =
    'w-full bg-white border border-ink/15 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-ember transition-colors'
  const labelCls = 'block text-sm font-medium mb-1.5'

  return (
    <div className="max-w-3xl">
      <h1 className="text-xl font-medium mb-1">新建案件</h1>
      <p className="text-sm text-ink/50 mb-6">填写案情与业务目标，可选注入你的观点（将注入全部评估环节）</p>

      <div className="space-y-5">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={labelCls}>案件名称 *</label>
            <input className={inputCls} value={form.name} onChange={set('name')}
              placeholder="如：XX 商标侵权主诉评估" />
          </div>
          <div>
            <label className={labelCls}>我司主体（原告）*</label>
            <input className={inputCls} value={form.client_org} onChange={set('client_org')}
              placeholder="原告公司名称（主诉评估须由权利人发起）" />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={labelCls}>案由</label>
            <select className={inputCls} value={form.cause_type} onChange={set('cause_type')}>
              {meta.cause_types.map((t) => <option key={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label className={labelCls}>业务目标（决定业务预期算法）</label>
            <div className="flex gap-2">
              {meta.goal_types.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setForm({ ...form, goal_type: t })}
                  className={`flex-1 text-sm py-2 rounded-lg border transition-colors ${
                    form.goal_type === t
                      ? 'border-ember bg-ember-pale text-ember-dark font-medium'
                      : 'border-ink/15 bg-white text-ink/60 hover:border-ink/30'
                  }`}
                >
                  {t === '要钱' ? '要钱（判赔规模 · 回款能力）' : '要名（判例价值）'}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div>
          <label className={labelCls}>被告名称</label>
          <input className={inputCls} value={form.defendant_name} onChange={set('defendant_name')}
            placeholder="用于企查查被告画像与回款能力评估" />
        </div>

        <div>
          <label className={labelCls}>案情描述 *</label>
          <textarea className={`${inputCls} h-36 resize-y`} value={form.case_description}
            onChange={set('case_description')}
            placeholder="侵权发现经过、涉案标识/作品、侵权形式、侵权规模（销量/店铺数）、已掌握的证据情况……" />
        </div>

        <div>
          <label className={labelCls}>证据材料文本</label>
          <textarea className={`${inputCls} h-24 resize-y`} value={form.evidence_texts}
            onChange={set('evidence_texts')}
            placeholder="粘贴证据清单或关键证据文本（文件上传解析将在 P2 提供）" />
        </div>

        <div className="bg-white border border-ink/10 rounded-xl p-4">
          <div className="flex items-center justify-between mb-2">
            <label className="text-sm font-medium">观点注入（可选）</label>
            <button
              type="button"
              onClick={() => setViewpoints([...viewpoints, ''])}
              className="text-xs text-ember hover:text-ember-dark"
            >
              + 添加一条
            </button>
          </div>
          <p className="text-xs text-ink/40 mb-3">
            你的判断将作为重要参考注入全部评估环节，并在报告中标注留痕
          </p>
          {viewpoints.map((v, i) => (
            <input
              key={i}
              className={`${inputCls} mb-2`}
              value={v}
              onChange={(e) => {
                const next = [...viewpoints]
                next[i] = e.target.value
                setViewpoints(next)
              }}
              placeholder="如：我认为被告是惯犯，应主张惩罚性赔偿"
            />
          ))}
        </div>

        {error && <div className="bg-red-50 text-red-700 text-sm rounded-lg p-3">{error}</div>}

        <button
          onClick={submit}
          disabled={submitting}
          className="w-full bg-ink hover:bg-ink-light disabled:opacity-50 text-white py-3 rounded-lg text-sm font-medium transition-colors"
        >
          {submitting ? '创建中…' : '创建案件并开始评估'}
        </button>
      </div>
    </div>
  )
}
