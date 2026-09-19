import { useState, type ReactNode } from 'react'

// 本文件集中导出「判断依据」区块相关的通用组件与工具，
// 供 EvalRun（评估详情）与 DecisionDashboard（评估结果）复用，避免两处各写一份而走歪。

/** 红线规则严重度的中文标签 */
export const SEV_LABEL: Record<string, string> = { pass: '通过', warning: '警示', block: '拦截' }

/**
 * 标题旁的「为什么这么算」小问号。
 *
 * 判断依据的正文要留给「本案发生了什么」，方法论（公式、规则定义、口径）
 * 搬到这里悬停可见——它每个案子都一样，印在正文里只会把案件事实挤掉。
 */
export function Hint({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  return (
    <span className="relative inline-flex align-middle">
      <button
        type="button"
        aria-label="算法说明"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen((v) => !v)}
        className="w-3.5 h-3.5 rounded-full border border-line text-[9px] leading-none
                   text-muted hover:text-fg hover:border-fg transition-colors
                   inline-flex items-center justify-center"
      >
        ?
      </button>
      {open && (
        <span
          className="absolute left-0 top-5 z-20 w-64 rounded-md border border-line
                     bg-surface p-2 text-[11px] leading-relaxed text-muted shadow-sm"
        >
          {text}
        </span>
      )}
    </span>
  )
}

/**
 * 判断依据的通用区块。布局为「左侧固定标签 + 右侧正文」的定义列表，
 * 替代原先一片左竖线堆叠的写法——多类依据（证据 / 评分 / 规则 / 外部数据）横向对齐后更好扫读。
 * 窄屏（sm 以下）自动折成上下两行，避免标签挤掉正文宽度。
 *
 * hint：算法说明，显示在标题旁的小问号里，不占正文。
 */
export function Basis({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 sm:flex-row sm:gap-4">
      <div className="shrink-0 text-xs font-medium text-fg sm:w-24 sm:pt-0.5 flex items-center gap-1">
        <span>{title}</span>
        {hint ? <Hint text={hint} /> : null}
      </div>
      <div className="min-w-0 flex-1 text-sm text-muted leading-relaxed">{children}</div>
    </div>
  )
}

/**
 * 判断依据面板，默认收起：用户先看到结论，想知道「这句话是怎么来的」时再点开，
 * 页面因此更短、更好扫。
 */
export function BasisList({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-3 pt-3 border-t border-line">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 text-left group"
      >
        <span className="text-xs font-medium text-muted group-hover:text-fg transition-colors">
          判断依据
        </span>
        <span className="flex-1" />
        <span className="text-xs text-muted">{open ? '收起' : '展开'}</span>
      </button>
      {open && <div className="mt-3 space-y-3">{children}</div>}
    </div>
  )
}

/** 红线规则严重度的色块（与证据要件状态标签共用一套配色） */
export function sevPill(s?: string) {
  if (s === 'pass') return 'bg-[var(--success-soft)] text-[var(--success)]'
  if (s === 'warning') return 'bg-[var(--warning-soft)] text-[var(--warning)]'
  return 'bg-[var(--danger-soft)] text-[var(--danger)]'
}

/**
 * 把企查查规则命中的信号翻译成自然语言。
 *
 * 后端返回的是「失信被执行(2条)」这类压缩记号，直接铺在界面上等于让用户自己换算倍数。
 * 这里统一补上「这句话在说什么、对回款意味着什么」，让不懂规则表的人也能看懂。
 */
export function signalMeaning(flag: string, kind: 'red' | 'green'): string {
  if (/主体存续异常/.test(flag))
    return `${flag}：被告已注销、清算或进入破产重整，主体实质上已不存在，即便胜诉也无从执行，回款直接归零。`
  if (/三项全有/.test(flag))
    return '失信、被执行、限制高消费三项同时命中，是典型的「老赖」特征，回款希望降到很低。'
  if (/实控人.*失信/.test(flag))
    return `${flag}：实际控制人本人有失信记录，责任人与公司绑定紧密、个人信用受损，回款保障被削弱。`
  if (/失信被执行/.test(flag))
    return '被告被法院列为失信被执行人，说明对方有履行能力却拒不履行，回款希望大幅缩水。'
  if (/终本/.test(flag))
    return `${flag}：已有案件因查无可执行财产而「终结本次执行」，说明此前就找不到财产，回款希望进一步下降。`
  if (/被执行/.test(flag))
    return `${flag}：存在多次被执行记录，债务纠纷反复发生、履约意愿差，回款难度明显上升。`
  if (/严重违法/.test(flag)) return '被告被列入严重违法失信名单，经营与信用状况都很差，不利于事后收款。'
  if (/经营异常/.test(flag)) return '被告被列入经营异常名录，经营状态不稳定、甚至可能联系不上，回款存在变数。'
  if (/限高/.test(flag)) return '被告被限制高消费，说明法院已认定其有履行能力而不履行，回款希望下降。'
  if (/冻结|质押|抵押/.test(flag))
    return `${flag}：名下核心资产已被冻结或质押，可供执行的财产不多，回款希望下降。`
  if (/上市公司/.test(flag)) return '被告是上市公司，信息公开透明、偿付能力有据可查，回款把握相应提高。'
  if (/公开财务数据/.test(flag)) return '被告披露了公开财务数据，经营规范、资产状况可查，回款更有保障。'
  if (/可追溯资产/.test(flag))
    return `${flag}：实际控制人名下有可追溯的资产，未来有明确的执行标的，回款希望略增。`
  if (/控制.*家企业/.test(flag))
    return `${flag}：被告本人名下控制着实体企业，确有财产可供执行，回款希望提高。`
  return kind === 'red'
    ? `存在不利迹象「${flag}」，会拉低回款可能性。`
    : `存在有利迹象「${flag}」，会提高回款可能性。`
}
