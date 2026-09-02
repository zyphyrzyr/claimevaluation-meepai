import type { ReactNode } from 'react'

export default function Tooltip({ children, content }: { children: ReactNode; content: string }) {
  if (!content) return <>{children}</>
  return (
    <div className="relative inline-block group">
      {children}
      <div className="absolute left-1/2 -translate-x-1/2 bottom-full mb-2 hidden group-hover:block z-50 pointer-events-none w-max max-w-xs">
        <div className="bg-fg text-canvas text-xs rounded-md px-3 py-2 shadow-lg text-left break-words">
          {content}
        </div>
      </div>
    </div>
  )
}
