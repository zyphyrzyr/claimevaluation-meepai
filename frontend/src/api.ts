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
