import { useEffect, useMemo, useState } from 'react'
import { api, EvidenceFileMeta } from '../api'
import SlideOver from './SlideOver'

/** 待预览的目标：要么是已保存文件（有 fileId + caseId），要么是未保存的本地 File */
export type PreviewTarget = {
  fileName: string
  fileId?: string
  file?: File
} | null

export default function EvidencePreview({
  target,
  caseId,
  onClose,
}: {
  target: PreviewTarget
  caseId?: string
  onClose: () => void
}) {
  // 未保存的本地文件：用 blob URL 预览，关闭时务必回收，防内存泄漏
  const blobUrl = useMemo(() => (target?.file ? URL.createObjectURL(target.file) : null), [target])
  useEffect(() => {
    return () => {
      if (blobUrl) URL.revokeObjectURL(blobUrl)
    }
  }, [blobUrl])

  const [meta, setMeta] = useState<EvidenceFileMeta | null>(null)
  const [metaErr, setMetaErr] = useState('')

  useEffect(() => {
    setMeta(null)
    setMetaErr('')
    if (!target || target.file || !target.fileId || !caseId) return
    api
      .evidenceFileMeta(caseId, target.fileId)
      .then(setMeta)
      .catch((e) => setMetaErr(String(e)))
  }, [target, caseId])

  let body: React.ReactNode = null
  let footer: React.ReactNode = null

  if (target?.file) {
    const name = target.fileName.toLowerCase()
    const isPdf = target.file.type === 'application/pdf' || name.endsWith('.pdf')
    const isImg = target.file.type.startsWith('image/')
    if (isPdf) {
      body = <iframe src={blobUrl!} className="w-full h-full border-0" title={target.fileName} />
    } else if (isImg) {
      body = <img src={blobUrl!} className="w-full h-full object-contain" alt={target.fileName} />
    } else {
      // doc/docx 还没进后端、本机无法转换，提示保存后预览
      body = (
        <div className="p-6 text-sm text-muted">
          该文件尚未保存，保存草稿后即可在线预览。
        </div>
      )
    }
  } else if (target?.fileId && caseId) {
    if (metaErr) {
      body = <div className="p-6 text-sm text-danger">{metaErr}</div>
    } else if (!meta) {
      body = <div className="p-6 text-sm text-muted">加载中…</div>
    } else if (meta.preview_kind === 'pdf') {
      body = (
        <iframe
          src={api.evidenceFileRawUrl(caseId, target.fileId)}
          className="w-full h-full border-0"
          title={meta.file_name}
        />
      )
    } else if (meta.preview_kind === 'image') {
      body = (
        <img
          src={api.evidenceFileRawUrl(caseId, target.fileId)}
          className="w-full h-full object-contain"
          alt={meta.file_name}
        />
      )
    } else if (meta.preview_kind === 'html') {
      // textutil 导出的 HTML 是静态自包含内容，sandbox="" 禁脚本后展示即可
      body = (
        <iframe
          src={api.evidenceFilePreviewUrl(caseId, target.fileId)}
          className="w-full h-full border-0 bg-canvas"
          title={meta.file_name}
          sandbox=""
        />
      )
    } else {
      // none：旧版本文件无原件，回退解析文本
      body = (
        <div className="p-6 overflow-auto h-full">
          <div className="text-xs text-muted mb-2">
            该文件未保留原始版式（可能上传于旧版本），仅展示已解析文本：
          </div>
          <pre className="text-sm text-fg whitespace-pre-wrap break-words leading-relaxed">
            {meta.parsed_text || '（无解析文本）'}
          </pre>
        </div>
      )
    }
    footer = meta ? (
      <div className="flex items-center justify-between gap-3 border-t border-line bg-canvas px-6 py-4 shrink-0">
        <span className="text-xs text-muted truncate">
          {meta.parse_status === 'ok' ? '已解析' : '解析失败'} ·{' '}
          {meta.size != null ? `${(meta.size / 1024).toFixed(1)} KB` : '原件不可用'}
        </span>
        {meta.has_blob && (
          <a
            href={api.evidenceFileRawUrl(caseId, target.fileId)}
            download={meta.file_name}
            className="text-sm text-fg font-medium hover:opacity-90 shrink-0"
          >
            下载原件
          </a>
        )}
      </div>
    ) : null
  }

  return (
    <SlideOver
      open={!!target}
      onClose={onClose}
      title={target?.fileName ?? '文件预览'}
      widthClass="w-[52rem]"
      panelZClass="z-[60]"
      bodyClassName="p-0 flex flex-col"
    >
      <div className="flex flex-col flex-1 min-h-0 bg-surface">
        <div className="flex-1 min-h-0">{body}</div>
        {footer}
      </div>
    </SlideOver>
  )
}
