import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'

interface DropdownItem {
  key: string
  label?: string
  divider?: boolean
  onClick?: () => void
  icon?: ReactNode
}

interface Props {
  items: DropdownItem[]
  children: ReactNode
  placement?: 'bottom-start' | 'bottom-end' | 'top' | 'top-start' | 'top-end'
}

export default function Dropdown({ items, children, placement = 'bottom-start' }: Props) {
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const triggerRef = useRef<HTMLSpanElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const visibleItems = items.filter((it) => !it.divider)
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([])

  const close = useCallback(() => {
    setOpen(false)
    setActiveIndex(-1)
  }, [])

  useEffect(() => {
    if (!open) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        close()
        triggerRef.current?.focus()
        return
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setActiveIndex((prev) => {
          const next = prev < visibleItems.length - 1 ? prev + 1 : 0
          itemRefs.current[next]?.focus()
          return next
        })
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setActiveIndex((prev) => {
          const next = prev > 0 ? prev - 1 : visibleItems.length - 1
          itemRefs.current[next]?.focus()
          return next
        })
      }
      if (e.key === 'Enter' && activeIndex >= 0 && activeIndex < visibleItems.length) {
        visibleItems[activeIndex]?.onClick?.()
        close()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, activeIndex, visibleItems, close])

  useEffect(() => {
    if (!open) return
    function onMouseDown(e: MouseEvent) {
      if (
        menuRef.current &&
        !menuRef.current.contains(e.target as Node) &&
        triggerRef.current &&
        !triggerRef.current.contains(e.target as Node)
      ) {
        close()
      }
    }
    document.addEventListener('mousedown', onMouseDown)
    return () => document.removeEventListener('mousedown', onMouseDown)
  }, [open, close])

  function placeMenu() {
    if (!menuRef.current || !triggerRef.current) return {}
    const r = triggerRef.current.getBoundingClientRect()
    const pop = menuRef.current.getBoundingClientRect()
    const vw = window.innerWidth
    let top = r.bottom + 6
    let left = r.left
    if (placement === 'bottom-end') left = r.right - pop.width
    if (placement === 'top') top = r.top - pop.height - 6
    if (placement === 'top-start') { top = r.top - pop.height - 6; left = r.left }
    if (placement === 'top-end') { top = r.top - pop.height - 6; left = r.right - pop.width }
    if (left + pop.width > vw - 8) left = vw - pop.width - 8
    if (left < 8) left = 8
    if (top < 8) top = r.bottom + 6
    return { top, left }
  }

  function handleTriggerClick() {
    setOpen((prev) => !prev)
    setActiveIndex(-1)
  }

  const pos = open ? placeMenu() : {}

  return (
    <>
      <span
        ref={triggerRef}
        onClick={handleTriggerClick}
        style={{ cursor: 'pointer', display: 'inline-flex', alignItems: 'center' }}
      >
        {children}
      </span>
      {open && (
        <div
          ref={menuRef}
          className="atelier-popover is-open"
          role="menu"
          style={{ position: 'fixed', ...pos, zIndex: 90 }}
        >
          {items.map((it, i) => {
            if (it.divider) {
              return <div key={`div-${i}`} className="atelier-popover__divider" />
            }
            const itemIndex = visibleItems.indexOf(it)
            return (
              <button
                key={it.key}
                ref={(el) => { itemRefs.current[itemIndex] = el }}
                className="atelier-popover__item"
                role="menuitem"
                onClick={() => {
                  it.onClick?.()
                  close()
                }}
                onMouseEnter={() => setActiveIndex(itemIndex)}
              >
                {it.icon && <span>{it.icon}</span>}
                {it.label}
              </button>
            )
          })}
        </div>
      )}
    </>
  )
}
