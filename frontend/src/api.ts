const BASE = '/api'

export interface CaseItem {
  id: string
  name: string
  cause_type: string
  goal_type: string | null
  status: string
  created_at: string
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
}

export interface EvalEvent {
  event: string
  node: string
  label: string
  status?: string
  error?: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
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
  listCases: () => request<CaseItem[]>('/cases'),
  createCase: (payload: CaseCreatePayload) =>
    request<CaseItem>('/cases', { method: 'POST', body: JSON.stringify(payload) }),
  caseDetail: (id: string) => request<any>(`/cases/${id}`),
  result: (id: string) => request<any>(`/evaluation/${id}/result`),
  rerun: (id: string, node: string, guidance: string) =>
    request<any>(`/evaluation/${id}/rerun`, {
      method: 'POST',
      body: JSON.stringify({ node, guidance }),
    }),
  mootHistory: (id: string) => request<any>(`/moot/${id}`),
  memo: (id: string) => request<any>(`/report/${id}/memo`),
  snapshot: (id: string) => request<any>(`/report/${id}/snapshot`, { method: 'POST' }),
  versions: (id: string) => request<any[]>(`/report/${id}/versions`),
  version: (id: string, version: number) => request<any>(`/report/${id}/versions/${version}`),
  transcript: (id: string) => request<any>(`/report/${id}/transcript`),
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
}

export const mootApi = {
  /** 内嵌模式：案件内压力测试（系数回写 + 决策合成重算） */
  runEmbedded: (caseId: string, onEvent: (e: any) => void) =>
    ssePost(`/moot/${caseId}/run`, {}, onEvent),
  /** 独立模式：手动组料纯演练，不回写评分 */
  runStandalone: (payload: StandaloneMootPayload, onEvent: (e: any) => void) =>
    ssePost('/moot/standalone', payload, onEvent),
}

/** SSE：启动评估并逐事件回调 */
export async function runEvaluation(
  caseId: string,
  onEvent: (e: EvalEvent) => void,
): Promise<void> {
  const resp = await fetch(`${BASE}/evaluation/${caseId}/run`, { method: 'POST' })
  if (!resp.ok || !resp.body) throw new Error(`评估启动失败: ${resp.status}`)
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
  stale: boolean
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
  inject: (caseId: string, entry_ids: string[]) =>
    request<{ ok: boolean; injected: number }>(`/knowledge/cases/${caseId}/inject`, {
      method: 'POST',
      body: JSON.stringify({ entry_ids }),
    }),
  deposit: (caseId: string, title: string, content: string) =>
    request<{ ok: boolean; id: string }>(`/knowledge/cases/${caseId}/deposit`, {
      method: 'POST',
      body: JSON.stringify({ title, content }),
    }),
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
