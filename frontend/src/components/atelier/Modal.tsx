import { useCallback, useEffect, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import Button from './Button'

interface Props {
  open: boolean
  onClose: () => void
  title?: string
  children?: ReactNode
  footer?: ReactNode
  onOk?: () => void
  confirmLoading?: boolean
  okText?: string
  cancelText?: string
}

export default function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  onOk,
  confirmLoading,
  okText = '确定',
  cancelText = '取消',
}: Props) {
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    panelRef.current?.focus()
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        onClose()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  const handleBackdropClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === e.currentTarget) onClose()
    },
    [onClose],
  )

  if (!open) return null

  const defaultFooter = footer ?? (
    <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
      <Button variant="default" onClick={onClose}>
        {cancelText}
      </Button>
      <Button variant="primary" onClick={onOk} loading={confirmLoading}>
        {okText}
      </Button>
    </div>
  )

  return createPortal(
    <div className="atelier-modal-root">
      <div className="atelier-modal-backdrop is-open" onClick={handleBackdropClick}>
        <div
          ref={panelRef}
          className="atelier-modal-panel"
          role="dialog"
          aria-modal="true"
          aria-label={title}
          tabIndex={-1}
        >
          {title && (
            <div className="atelier-modal-header">
              <span className="atelier-modal-title">{title}</span>
              <button className="atelier-modal-close" onClick={onClose} aria-label="关闭">
                ✕
              </button>
            </div>
          )}
          <div className="atelier-modal-body">{children}</div>
          <div className="atelier-modal-footer">{defaultFooter}</div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
