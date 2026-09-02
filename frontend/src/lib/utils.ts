import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** shadcn 约定的 className 合并工具：去重冲突的 Tailwind 类，后者覆盖前者 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
