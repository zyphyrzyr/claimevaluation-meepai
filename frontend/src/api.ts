const BASE = '/api'

export interface ParseSummary {
  total: number
  ok: number
  failed: number
  skipped: number
  warnings: string[]
}

export interface CaseItem {
  id: string
  name: string
  cause_type: string
  goal_type: string | null
  status: string
  created_at: string
  parse_summary?: ParseSummary
  /** 原告（多个用「、」连接）。当事人存在 parties 表，未登记时为 null */
  plaintiff?: string | null
  /** 被告（多个用「、」连接）。未登记时为 null */
  defendant?: string | null
}

/** 案件列表分页信封（全库已有数百个案件，服务端分页 + 搜索） */
export interface CasePage {
  items: CaseItem[]
  total: number
  page: number
  page_size: number
}

export interface EvidenceFileMeta {
  id: string
  file_name: string
  parse_status: string
  parsed_text: string
  has_blob: boolean
  size: number | null
  preview_kind: 'pdf' | 'image' | 'html' | 'none'
}

export interface CaseCreatePayload {
  name: string
  case_description: string
  cause_type: string
  goal_type: string
  client_org?: string
  defendant_name?: string
  defendant_type?: string
  evidence_texts?: string
  viewpoints?: string[]
  draft?: boolean
}

export interface EvalEvent {
  event: string
  node: string
  label: string
  status?: string
  error?: string
  /** node_step：给人看的一句话（当前在做什么） */
  text?: string
  /** node_step / mcp_call：补充说明（缺口清单、规则命中、阶段结果等） */
  detail?: string
  /** mcp_call：外部数据源名称（企查查 / 北大法宝） */
  vendor?: string
  /** node_finished：本节点耗时 */
  duration_ms?: number
  /** node_finished：一句话结论 */
  summary?: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData
  const resp = await fetch(`${BASE}${path}`, {
    headers: isFormData ? undefined : { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!resp.ok) {
    const text = await resp.text()
    throw new Error(`${resp.status}: ${text}`)
  }
  return resp.json()
}

export const api = {
  meta: () => request<{ cause_types: string[]; goal_types: string[] }>('/cases/meta'),
  /**
   * 案件列表（服务端分页 + 关键词搜索）。
   *
   * q 按空格分词、多词 AND，匹配案件名称或原被告——搜索一定是**全库**搜，
   * 不是只搜当前页，否则「翻页」和「搜索」两个语义会互相打架。
   */
  listCases: (params?: { q?: string; page?: number; page_size?: number }) => {
    const s = new URLSearchParams()
    if (params?.q) s.set('q', params.q)
    if (params?.page) s.set('page', String(params.page))
    if (params?.page_size) s.set('page_size', String(params.page_size))
    const qs = s.toString()
    return request<CasePage>(`/cases${qs ? `?${qs}` : ''}`)
  },
  createCase: (payload: CaseCreatePayload | FormData) =>
    request<CaseItem>('/cases', {
      method: 'POST',
      body: payload instanceof FormData ? payload : JSON.stringify(payload),
    }),
  caseDetail: (id: string) =>
    request<{
      id: string
      name: string
      cause_type: string
      goal_type: string
      status: string
      case_description: string
      client_org: string
      evidence_files: { id: string; file_name: string; parse_status: string }[]
      context: any
    }>(`/cases/${id}`),
  updateDraft: (id: string, payload: CaseCreatePayload | FormData) =>
    request<CaseItem>(`/cases/${id}`, {
      method: 'PUT',
      body: payload instanceof FormData ? payload : JSON.stringify(payload),
    }),
  startEvaluation: (id: string) => request<CaseItem>(`/cases/${id}/start-evaluation`, { method: 'POST' }),
  deleteEvidenceFile: (id: string, fileId: string) =>
    request<{ ok: boolean }>(`/cases/${id}/evidence-files/${fileId}`, { method: 'DELETE' }),
  evidenceFileMeta: (id: string, fileId: string) =>
    request<EvidenceFileMeta>(`/cases/${id}/evidence-files/${fileId}`),
  // 二进制预览直连 URL：不经 request() 的 json 解析，直接给 <iframe>/<img> src
  evidenceFileRawUrl: (id: string, fileId: string) =>
    `${BASE}/cases/${id}/evidence-files/${fileId}/raw`,
  evidenceFilePreviewUrl: (id: string, fileId: string) =>
    `${BASE}/cases/${id}/evidence-files/${fileId}/preview`,
  uploadDescriptionText: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ filename: string; text: string; page_count: number }>(
      '/cases/upload-description-text',
      { method: 'POST', body: form },
    )
  },
  deleteCase: (id: string) =>
    request<{ ok: boolean }>(`/cases/${id}`, { method: 'DELETE' }),
  result: (id: string) => request<any>(`/evaluation/${id}/result`),
  rerun: (id: string, node: string, guidance: string) =>
    request<any>(`/evaluation/${id}/rerun`, {
      method: 'POST',
      body: JSON.stringify({ node, guidance }),
    }),
  // 评估三态控制（暂停 / 恢复 / 终止 / 状态查询）
  pauseEvaluation: (id: string) =>
    request<{ ok: boolean; paused: boolean }>(`/evaluation/${id}/pause`, { method: 'POST' }),
  stopEvaluation: (id: string) =>
    request<{ ok: boolean; stopped: boolean }>(`/evaluation/${id}/stop`, { method: 'POST' }),
  getRunState: (id: string) =>
    request<{ run: string }>(`/evaluation/${id}/run-state`),
  mootHistory: (id: string) => request<any>(`/moot/${id}`),
  /** 审计轨迹：观点注入、节点重跑、自动召回等留痕（此前只落库、无出口） */
  audit: (id: string) => request<any[]>(`/evaluation/${id}/audit`),
}

/**
 * 输出物导出
 *
 * 用普通链接下载而不是 fetch + blob：走 fetch 的话中文文件名要靠前端自己
 * 从 Content-Disposition 里解析，而 header 里的 RFC 5987 编码各家浏览器
 * 处理不一致，很容易下载出一串 %E5%86%B3%E7%AD%96… 的名字。交给浏览器
 * 原生下载最稳。
 *
 * 注：「决策备忘录」页面与其定稿快照（memo / snapshot / versions / transcript
 * 四个端点）已整体下架——需要历史定稿的场景已不存在，导出改为直接给当前状态。
 */
export const exportUrls = {
  /** 评估结果 Word（内容源与结果页同源：同一份 markdown） */
  resultDocx: (id: string) => `${BASE}/report/${id}/result.docx`,
  /** 评估结果 PDF（与 Word 同源，便于直接转发与打印） */
  resultPdf: (id: string) => `${BASE}/report/${id}/result.pdf`,
  transcriptDocx: (id: string) => `${BASE}/report/${id}/transcript.docx`,
  transcriptPdf: (id: string) => `${BASE}/report/${id}/transcript.pdf`,
}

/** 通用 SSE POST：逐事件回调 */
async function ssePost(path: string, body: any, onEvent: (e: any) => void): Promise<void> {
  const resp = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => '')
    throw new Error(`${resp.status}: ${text}`)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      const line = chunk.trim()
      if (line.startsWith('data:')) {
        onEvent(JSON.parse(line.slice(5)))
      }
    }
  }
}

export interface MootRound {
  step: number
  step_name: string
  role: string
  role_name: string
  content: string
}

export interface StandaloneMootPayload {
  case_description: string
  cause_type?: string
  viewpoints?: string[]
  plaintiff_points?: string
  case_id?: string
}

export const mootApi = {
  /** 内嵌模式：案件内庭审对抗（系数回写 + 决策合成重算） */
  runEmbedded: (caseId: string, onEvent: (e: any) => void) =>
    ssePost(`/moot/${caseId}/run`, {}, onEvent),
  /** 独立模式：手动组料纯演练，不回写评分 */
  runStandalone: (payload: StandaloneMootPayload, onEvent: (e: any) => void) =>
    ssePost('/moot/standalone', payload, onEvent),
}

/** 通用 SSE 读取：把流按 \n\n 切分，逐条回调（与后端 `data: ` 约定一致） */
async function readSSE(resp: Response, onEvent: (e: any) => void): Promise<void> {
  if (!resp.body) throw new Error('无响应流')
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      const line = chunk.trim()
      if (line.startsWith('data:')) {
        onEvent(JSON.parse(line.slice(5)))
      }
    }
  }
}

/** SSE：启动评估并逐事件回调 */
export async function runEvaluation(
  caseId: string,
  onEvent: (e: EvalEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${BASE}/evaluation/${caseId}/run`, { method: 'POST', signal })
  if (!resp.ok || !resp.body) throw new Error(`评估启动失败: ${resp.status}`)
  await readSSE(resp, onEvent)
}

/**
 * SSE：恢复评估。
 * - 后端已持有活跃流（同一标签页未刷新）：仅发信号、不返回流（返回 200 JSON），
 *   现有流会承接后续事件，无需本地再读。
 * - 流已断开（刷新页面后）：后端返回新的 SSE 流，本函数继续消费。
 */
export async function resumeEvaluation(
  caseId: string,
  onEvent: (e: EvalEvent) => void,
): Promise<void> {
  const resp = await fetch(`${BASE}/evaluation/${caseId}/resume`, { method: 'POST' })
  if (!resp.ok) throw new Error(`恢复评估失败: ${resp.status}`)
  const ct = resp.headers.get('content-type') || ''
  if (ct.includes('text/event-stream') && resp.body) {
    await readSSE(resp, onEvent)
  }
}

// ============================================================
// 知识库（P3 RAG：全局经验库 / 案件材料库双集合分库）
// ============================================================

export interface KnowledgeEntryItem {
  id: string
  scope: 'global' | 'case'
  case_id: string | null
  source_type: string
  source_type_label: string
  title: string
  content?: string
  snippet?: string
  chunk_count: number
  created_at: string
}

export interface SearchHit extends KnowledgeEntryItem {
  score: number
  matched_chunk: string
}

export const knowledgeApi = {
  info: () => request<{ backend: string; embedding: string }>('/knowledge/info'),
  entries: (params: { scope?: string; case_id?: string }) => {
    const q = new URLSearchParams()
    if (params.scope) q.set('scope', params.scope)
    if (params.case_id) q.set('case_id', params.case_id)
    return request<KnowledgeEntryItem[]>(`/knowledge/entries?${q}`)
  },
  createEntry: (payload: { scope: string; case_id?: string; source_type: string; title: string; content: string }) =>
    request<{ ok: boolean; id: string }>('/knowledge/entries', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  deleteEntry: (id: string) => request<{ ok: boolean }>(`/knowledge/entries/${id}`, { method: 'DELETE' }),
  search: (payload: { query: string; case_id?: string; scope?: string; top_k?: number }) =>
    request<SearchHit[]>('/knowledge/search', { method: 'POST', body: JSON.stringify(payload) }),
  caseEntries: (caseId: string) => request<KnowledgeEntryItem[]>(`/knowledge/cases/${caseId}/entries`),
  ingest: (caseId: string, evidence_texts: string) =>
    request<{ ok: boolean; ingested: number }>(`/knowledge/cases/${caseId}/ingest`, {
      method: 'POST',
      body: JSON.stringify({ evidence_texts }),
    }),
  // 手动勾选注入接口已随「评估准备」页一并移除（方案 B：注入由后端自动召回完成），
  // 对应后端 POST /knowledge/cases/{id}/inject 亦已删除。
  deposit: (caseId: string, title: string, content: string) =>
    request<{ ok: boolean; id: string }>(`/knowledge/cases/${caseId}/deposit`, {
      method: 'POST',
      body: JSON.stringify({ title, content }),
    }),
}

// ============================================================
// 运行设置（高级设置：LLM 供应商切换）
// ============================================================

export interface ProviderInfo {
  id: string
  label: string
  base_url: string
  strong_model: string
  fast_model: string
  json_mode_models: string[]
  max_tokens_param: string
  cost_tier: string
  cost_tier_label: string
  console_url: string
  notes: string
  builtin: boolean
  /** 该供应商自己的密钥环境变量名，用于提示「去哪儿配」 */
  primary_key_env: string
  /** 该供应商是否已配好密钥 */
  available: boolean
  key_source: string | null
  /** 只含末 4 位的掩码；真实密钥永远不会下发到前端 */
  key_masked: string | null
  /** 以下为当前生效值（可能被 .env 手写项覆盖，与预设值不同） */
  effective_base_url?: string
  effective_strong_model?: string
  effective_fast_model?: string
  effective_json_mode_models?: string[]
  effective_max_tokens_param?: string
}

export interface SettingsSnapshot {
  current: ProviderInfo
  /** default / file / env —— env 表示被环境变量顶住，设置页改不动 */
  selection_source: string
  providers: ProviderInfo[]
  mock: boolean
  mock_controlled_by: string
  warnings: string[]
  key_masked: string | null
  env_path: string
}

export interface PingResult {
  ok: boolean
  model?: string
  latency_ms?: number
  reply?: string
  error?: string
  skipped?: string
}

/**
 * 运行模式摘要（页脚那一行用）。
 *
 * 刻意不含任何密钥信息，连掩码都没有——它只需要回答「跑的是真模型还是演示数据」。
 */
export interface RunModeInfo {
  mock: boolean
  provider_id: string
  provider_label: string
  base_url: string
  strong_model: string
  fast_model: string
  /** 真实模式下这个为 false 表示一调就炸（缺 LLM_API_KEY），页脚要据此报警 */
  key_configured: boolean
}

export const settingsApi = {
  providers: () => request<SettingsSnapshot>('/settings/providers'),
  mode: () => request<RunModeInfo>('/settings/mode'),
  select: (provider_id: string) =>
    request<{ ok: boolean; provider_id: string; warnings: string[] }>('/settings/provider', {
      method: 'POST',
      body: JSON.stringify({ provider_id }),
    }),
  test: (provider_id: string) =>
    request<PingResult>(`/settings/providers/${provider_id}/test`, { method: 'POST' }),
}

// ============================================================
// 伴随式追问顾问（唯一对话 Agent，全程悬浮）
// ============================================================

export interface AdvisorMessage {
  role: 'user' | 'assistant'
  content: string
  recall?: { id: string; title: string; score: number }[]
}

export const advisorApi = {
  history: (caseId: string) => request<{ messages: AdvisorMessage[] }>(`/advisor/${caseId}/history`),
  /** SSE 对话：recall → delta* → done */
  chat: (caseId: string, question: string, onEvent: (e: any) => void) =>
    ssePost(`/advisor/${caseId}/chat`, { question }, onEvent),
}
