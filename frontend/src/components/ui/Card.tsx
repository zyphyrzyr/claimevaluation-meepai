import type { HTMLAttributes } from 'react'
import { cn } from '../../lib/utils'

/** 基础卡片（双主题：bg-surface / 边框 line / 文字 fg，均走 token） */
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('rounded-xl border border-line bg-surface text-fg', className)}
      {...props}
    />
  )
}
