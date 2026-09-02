import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { AdvisorMessage, advisorApi } from '../api'

/**
 * 伴随式追问顾问（§6.4 唯一对话 Agent）
 * 全程悬浮于案件页面右下角：评估前/中/后均可提问
 * 每次提问自动召回 本案材料库 + 全局经验库，召回出处展示在回答上方
 */

function useCaseIdFromPath(): string | null {
  const location = useLocation()
  const m = location.pathname.match(/^\/cases\/([^/]+)/)
  return m ? m[1] : null
}

export default function AdvisorPanel() {
  const caseId = useCaseIdFromPath()
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<AdvisorMessage[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [recall, setRecall] = useState<{ id: string; title: string; score: number }[]>([])
  const [error, setError] = useState('')
  const bodyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!caseId) return
    setMessages([])
    setRecall([])
    advisorApi.history(caseId)
      .then((r) => setMessages(r.messages ?? []))
      .catch(() => {})
  }, [caseId])

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight })
  }, [messages, recall, open])

  if (!caseId) return null   // 仅案件页面显示

  const send = async () => {
    const question = input.trim()
    if (!question || streaming) return
    setInput('')
    setError('')
    setStreaming(true)
    setRecall([])
    setMessages((m) => [...m, { role: 'user', content: question }, { role: 'assistant', content: '' }])
    try {
      await advisorApi.chat(caseId, question, (e) => {
        if (e.event === 'recall') {
          setRecall(e.refs ?? [])
        } else if (e.event === 'delta') {
          setMessages((m) => {
            const copy = [...m]
            copy[copy.length - 1] = {
              ...copy[copy.length - 1],
              content: copy[copy.length - 1].content + e.text,
            }
            return copy
          })
        } else if (e.event === 'error') {
          setError(e.error ?? '顾问服务异常')
        }
      })
    } catch (e) {
      setError(String(e))
    } finally {
      setStreaming(false)
    }
  }

  return (
    <>
      {/* 悬浮按钮 */}
      <button
        onClick={() => setOpen(!open)}
        className="fixed bottom-6 right-6 z-50 w-14 h-14 rounded-full bg-fg hover:opacity-90 text-canvas shadow-lg flex items-center justify-center transition-colors print:hidden"
        title="伴随式追问顾问"
      >
        {open ? (
          <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
            <path strokeLinecap="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        ) : (
          <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round"
              d="M8 10h8M8 14h5M21 12a9 9 0 11-3.5-7.1L21 3l-1 4.5c.6 1.3 1 2.8 1 4.5z" />
          </svg>
        )}
      </button>

      {/* 对话窗 */}
      {open && (
        <div className="fixed bottom-24 right-6 z-50 w-[380px] max-h-[70vh] flex flex-col bg-surface rounded-2xl border border-line shadow-xl overflow-hidden print:hidden">
          <div className="bg-surface text-fg px-4 py-3 border-b border-line">
            <div className="text-sm font-medium">追问顾问</div>
            <div className="text-[11px] text-white/50 mt-0.5">
              任意阶段可提问 · 自动召回本案材料与经验库
            </div>
          </div>

          <div ref={bodyRef} className="flex-1 overflow-y-auto p-4 space-y-3 min-h-[200px]">
            {messages.length === 0 && (
              <div className="text-xs text-muted text-center py-8 leading-relaxed">
                例：「我们的商标注册证能覆盖被告的商品吗？」<br />
                「杭州地区类似案件一般判多少？」<br />
                「缺口清单里哪项最影响评分？」
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={m.role === 'user' ? 'flex justify-end' : ''}>
                <div className={`text-sm leading-relaxed whitespace-pre-wrap max-w-[90%] rounded-xl px-3 py-2 ${
                  m.role === 'user'
                    ? 'bg-fg text-canvas'
                    : 'bg-canvas text-fg'
                }`}>
                  {m.content || (streaming && i === messages.length - 1 ? '…' : '')}
                </div>
              </div>
            ))}
            {recall.length > 0 && (
              <div className="text-[11px] text-muted border border-dashed border-line rounded-lg px-3 py-2">
                <span className="font-medium">知识库召回：</span>
                {recall.map((r, i) => (
                  <span key={i}>
                    {i > 0 && '；'}《{r.title}》（{(r.score * 100).toFixed(0)}%）
                  </span>
                ))}
              </div>
            )}
            {error && <div className="text-xs text-danger">{error}</div>}
          </div>

          <div className="border-t border-line p-3 flex gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && send()}
              placeholder="输入问题…"
              disabled={streaming}
              className="flex-1 border border-line rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-fg disabled:opacity-50"
            />
            <button
              onClick={send}
              disabled={streaming || !input.trim()}
              className="bg-fg hover:opacity-90 text-canvas px-4 rounded-lg text-sm disabled:opacity-40 transition-colors"
            >
              {streaming ? '…' : '发送'}
            </button>
          </div>
        </div>
      )}
    </>
  )
}
