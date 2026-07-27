import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import Button from './Button'

interface Props {
  title: string
  description?: string
  children: ReactNode
  onConfirm?: () => void
  onCancel?: () => void
  okText?: string
  cancelText?: string
}

export default function Popconfirm({
  title,
  description,
  children,
  onConfirm,
  onCancel,
  okText = '确定',
  cancelText = '取消',
}: Props) {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLSpanElement>(null)
  const popRef = useRef<HTMLDivElement>(null)

  const close = useCallback(() => {
    setOpen(false)
  }, [])

  useEffect(() => {
    if (!open) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        close()
        triggerRef.current?.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, close])

  useEffect(() => {
    if (!open) return
    function onMouseDown(e: MouseEvent) {
      if (
        popRef.current &&
        !popRef.current.contains(e.target as Node) &&
        triggerRef.current &&
        !triggerRef.current.contains(e.target as Node)
      ) {
        close()
      }
    }
    document.addEventListener('mousedown', onMouseDown)
    return () => document.removeEventListener('mousedown', onMouseDown)
  }, [open, close])

  function placePop() {
    if (!popRef.current || !triggerRef.current) return {}
    const r = triggerRef.current.getBoundingClientRect()
    const pop = popRef.current.getBoundingClientRect()
    const vw = window.innerWidth
    let top = r.bottom + 6
    let left = r.left + (r.width - pop.width) / 2
    if (left + pop.width > vw - 8) left = vw - pop.width - 8
    if (left < 8) left = 8
    return { top, left }
  }

  function handleTriggerClick() {
    setOpen((prev) => !prev)
  }

  function handleConfirm() {
    onConfirm?.()
    close()
  }

  function handleCancel() {
    onCancel?.()
    close()
  }

  const pos = open ? placePop() : {}

  return (
    <>
      <span
        ref={triggerRef}
        onClick={handleTriggerClick}
        style={{ cursor: 'pointer', display: 'inline-flex', alignItems: 'center' }}
      >
        {children}
      </span>
      {open &&
        createPortal(
          <div className="atelier-popover-root">
            <div ref={popRef} className="atelier-popconfirm" style={{ position: 'fixed', ...pos, zIndex: 90 }}>
              <div className="atelier-popconfirm__title">{title}</div>
              {description && <div className="atelier-popconfirm__body">{description}</div>}
              <div className="atelier-popconfirm__actions">
                <Button variant="default" size="sm" onClick={handleCancel}>
                  {cancelText}
                </Button>
                <Button variant="primary" size="sm" onClick={handleConfirm}>
                  {okText}
                </Button>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
  )
}
