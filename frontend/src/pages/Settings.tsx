import { useEffect, useState } from 'react'
import { ProviderInfo, SettingsSnapshot, settingsApi } from '../api'

/**
 * 高级设置 —— LLM 供应商切换
 *
 * 这一页刻意**没有密钥输入框**，也拿不到密钥：
 * 产品以 SaaS 形态交付，调用用的是我们的 key，用户付费买的是能力不是凭证。
 * 密钥只存在于服务端环境变量（将来是 KMS），这里只显示末 4 位掩码和
 * 「去哪个环境变量里配」的提示。
 */

const COST_BADGE: Record<string, string> = {
  low: 'bg-[var(--success-soft)] text-[var(--success)]',
  medium: 'bg-[var(--warning-soft)] text-[var(--warning)]',
  high: 'bg-[var(--danger-soft)] text-[var(--danger)]',
}

const SOURCE_LABEL: Record<string, string> = {
  default: '默认（未在 .env 指定）',
  file: '.env 文件',
  env: '环境变量（优先级高于 .env，设置页改不动）',
}

export default function Settings() {
  const [snap, setSnap] = useState<SettingsSnapshot | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [pending, setPending] = useState('')
  const [busy, setBusy] = useState(false)
  const [ping, setPing] = useState<{ id: string; ok: boolean; text: string } | null>(null)

  const load = () => {
    settingsApi
      .providers()
      .then(setSnap)
      .catch((e) => setError(String(e)))
  }

  useEffect(load, [])

  const select = async (provider: ProviderInfo) => {
    if (!snap || provider.id === snap.current.id) return
    if (!provider.available) {
      setError(
        `${provider.label} 尚未配置专属密钥：请在服务端设置环境变量 ${provider.primary_key_env}` +
          `后重启服务。通用槽里的那把 key 属于别的厂商，拿去调会返回 401，` +
          `所以未配好专属密钥的供应商不允许切换。`,
      )
      return
    }
    setBusy(true)
    setError('')
    setNotice('')
    setPending(provider.id)
    try {
      const res = await settingsApi.select(provider.id)
      setNotice(`已切换到 ${provider.label}，即刻生效，无需重启。`)
      if (res.warnings?.length) setNotice((n) => n + ' ' + res.warnings.join(' '))
      load()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
      setPending('')
    }
  }

  const runPing = async (provider: ProviderInfo) => {
    setPing({ id: provider.id, ok: false, text: '自检中…' })
    try {
      const r = await settingsApi.test(provider.id)
      setPing({
        id: provider.id,
        ok: r.ok,
        text: r.skipped
          ? r.skipped
          : r.ok
            ? `${r.model} 响应正常 · ${r.latency_ms}ms${r.reply ? ` · ${r.reply}` : ''}`
            : r.error ?? '失败',
      })
    } catch (e) {
      setPing({ id: provider.id, ok: false, text: String(e) })
    }
  }

  if (!snap) {
    return <div className="text-sm text-muted">{error || '加载中…'}</div>
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-medium">高级设置</h1>
        <p className="text-sm text-muted mt-1">
          切换后端大模型供应商。密钥由服务端环境变量提供，本页不展示也不接收密钥。
        </p>
      </div>

      {error && <div className="bg-[var(--danger-soft)] text-[var(--danger)] rounded-lg p-3 text-sm">{error}</div>}
      {notice && <div className="bg-[var(--success-soft)] text-[var(--success)] rounded-lg p-3 text-sm">{notice}</div>}

      {snap.warnings.map((w, i) => (
        <div key={i} className="bg-[var(--warning-soft)] text-[var(--warning)] rounded-lg p-3 text-sm">
          {w}
        </div>
      ))}

      {/* 当前生效 */}
      <div className="bg-surface rounded-xl border border-line p-5">
        <div className="flex items-baseline justify-between mb-4">
          <h2 className="text-sm font-medium">当前供应商</h2>
          <span className="text-xs text-muted">
            来源：{SOURCE_LABEL[snap.selection_source] ?? snap.selection_source}
          </span>
        </div>

        <div className="grid sm:grid-cols-2 gap-x-8 gap-y-2 text-sm">
          <Row label="请求地址" value={snap.current.effective_base_url ?? snap.current.base_url} />
          <Row label="强模型（侵权认定 / 法官）" value={snap.current.effective_strong_model ?? snap.current.strong_model} />
          <Row label="普通模型（其余节点）" value={snap.current.effective_fast_model ?? snap.current.fast_model} />
          <Row
            label="JSON 模式白名单"
            value={(snap.current.effective_json_mode_models ?? snap.current.json_mode_models).join('、') || '（无）'}
          />
          <Row label="长度字段名" value={snap.current.effective_max_tokens_param ?? snap.current.max_tokens_param} />
          <Row
            label="密钥"
            value={
              snap.key_masked
                ? `已配置 ${snap.key_masked}（来源 ${snap.current.key_source}）`
                : `未配置 —— 设置环境变量 ${snap.current.primary_key_env}`
            }
            warn={!snap.key_masked}
          />
          <Row label="运行模式" value={snap.mock ? 'Mock（离线演示）' : '真实模式'} />
          <Row
            label="Mock 由谁决定"
            value={
              snap.mock_controlled_by === 'env'
                ? '环境变量 USE_MOCK（start.sh 会 export，设置页改不动）'
                : '.env 文件'
            }
          />
        </div>

        {snap.current.notes && (
          <p className="text-xs text-muted mt-4 leading-relaxed border-t border-line pt-3">
            {snap.current.notes}
          </p>
        )}
      </div>

      {/* 可选项 */}
      <div className="bg-surface rounded-xl border border-line p-5">
        <h2 className="text-sm font-medium mb-1">可选供应商</h2>
        <p className="text-xs text-muted mb-4">
          新增供应商只需在后端 providers.py 加一条预设，本页自动出现。
        </p>
        <div className="space-y-3">
          {snap.providers.map((p) => {
            const active = p.id === snap.current.id
            return (
              <div
                key={p.id}
                className={`rounded-lg border p-4 transition-colors ${
                  active ? 'border-brand bg-surface' : 'border-line'
                }`}
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium text-sm">{p.label}</span>
                  <span className={`text-[11px] px-2 py-0.5 rounded-full ${COST_BADGE[p.cost_tier] ?? 'bg-surface text-muted'}`}>
                    {p.cost_tier_label}
                  </span>
                  <span
                    className={`text-[11px] px-2 py-0.5 rounded-full ${
                      p.available ? 'bg-[var(--success-soft)] text-[var(--success)]' : 'bg-surface text-muted'
                    }`}
                  >
                    {p.available ? `密钥已配置 ${p.key_masked}` : '未配置密钥'}
                  </span>
                  {active && (
                    <span className="text-[11px] px-2 py-0.5 rounded-full bg-brand text-white">
                      当前
                    </span>
                  )}
                  <div className="flex-1" />
                  <button
                    onClick={() => runPing(p)}
                    disabled={!active}
                    className="text-xs text-muted hover:text-brand disabled:opacity-30"
                    title={active ? '向该供应商发一个最小请求' : '仅当前供应商可自检'}
                  >
                    连通性自检
                  </button>
                  <button
                    onClick={() => select(p)}
                    disabled={active || busy}
                    className="text-xs bg-fg hover:bg-fg text-white px-3 py-1 rounded-md disabled:opacity-30 transition-colors"
                  >
                    {busy && pending === p.id ? '切换中…' : active ? '使用中' : '切换'}
                  </button>
                </div>

                <div className="text-xs text-muted mt-2 font-mono break-all">
                  {p.base_url} · 强 {p.strong_model} / 普通 {p.fast_model}
                </div>

                {!p.available && (
                  <div className="text-xs text-muted mt-1">
                    配置方式：服务端设置环境变量 <code className="bg-surface px-1 rounded">{p.primary_key_env}</code>
                    {p.console_url && (
                      <>
                        {' '}
                        ·{' '}
                        <a href={p.console_url} target="_blank" rel="noreferrer" className="text-brand hover:underline">
                          获取密钥
                        </a>
                      </>
                    )}
                  </div>
                )}

                {ping && ping.id === p.id && (
                  <div
                    className={`text-xs mt-2 rounded-md px-2 py-1 ${
                      ping.ok ? 'bg-[var(--success-soft)] text-[var(--success)]' : 'bg-surface text-muted'
                    }`}
                  >
                    {ping.text}
                  </div>
                )}

                {p.notes && <p className="text-xs text-muted mt-2 leading-relaxed">{p.notes}</p>}
              </div>
            )
          })}
        </div>
        <p className="text-xs text-muted mt-4">
          切换仅写入 <code className="bg-surface px-1 rounded">{snap.env_path}</code> 的 LLM_* 项，
          不触碰任何密钥行；配置每次调用重读，改完即刻生效。
        </p>
      </div>
    </div>
  )
}

function Row({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="flex gap-3 py-1 border-b border-line">
      <span className="text-muted w-44 shrink-0">{label}</span>
      <span className={`break-all ${warn ? 'text-brand' : ''}`}>{value}</span>
    </div>
  )
}
