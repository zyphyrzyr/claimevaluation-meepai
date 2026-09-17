import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { RunModeInfo, settingsApi } from '../api'

/**
 * 页脚运行模式提示 —— 「屏幕上这些结论，是真模型跑出来的还是演示数据？」
 *
 * 这一行是从「高级设置」页搬下来的。那一页已经移出导航、只留作运维入口，
 * 但「当前是真实模式还是 Mock」不该跟着页面一起消失：演示时它是最需要
 * 一眼确认的判断，而 Mock 数据看起来和真实输出别无二致。
 */
export default function RunModeBadge() {
  const [info, setInfo] = useState<RunModeInfo | null>(null)
  const { pathname } = useLocation()

  // 跟随路由重新拉取：在运维页切完供应商后回到业务页，页脚要是还显示旧模型，
  // 就成了另一个「显示的和跑的不是一套」——正是这次要消掉的那类错觉。
  useEffect(() => {
    let alive = true
    settingsApi
      .mode()
      .then((d) => alive && setInfo(d))
      // 拿不到就不渲染——页脚不是报错的地方，接口故障不该在每一页底部留一行红字
      .catch(() => alive && setInfo(null))
    return () => {
      alive = false
    }
  }, [pathname])

  return <RunModeLine info={info} />
}

/**
 * 纯展示层，与取数解耦。
 *
 * 拆出来是为了能被单独渲染断言：SSR 不执行 useEffect，取数版在服务端渲染里
 * 永远是 null，三种状态的配色和文案就无从校验了。
 */
export function RunModeLine({ info }: { info: RunModeInfo | null }) {
  if (!info) return null

  const detail = `${info.provider_label} · ${info.base_url}\n强模型 ${info.strong_model} / 普通模型 ${info.fast_model}`

  // 真实模式却没密钥：一调就炸，不能安安静静地显示「真实模式」
  const broken = !info.mock && !info.key_configured

  const dot = info.mock
    ? 'bg-[var(--warning)]'
    : broken
      ? 'bg-[var(--danger)]'
      : 'bg-[var(--success)]'

  const text = info.mock
    ? 'Mock 模式 · 演示数据（非真实模型输出）'
    : broken
      ? '真实模式 · 未配置密钥，调用会失败'
      : `真实模式 · ${info.strong_model}`

  const tone = info.mock
    ? 'text-[var(--warning)]'
    : broken
      ? 'text-[var(--danger)]'
      : 'text-muted'

  return (
    <div className="flex items-center justify-center gap-1.5 text-xs" title={detail}>
      <span className={`w-1.5 h-1.5 rounded-full ${dot}`} />
      <span className={tone}>{text}</span>
    </div>
  )
}
