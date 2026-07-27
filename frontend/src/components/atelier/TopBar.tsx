import type { ReactNode } from 'react'

interface Props {
  brand: string
  subtitle?: string
  children?: ReactNode
  className?: string
}

export default function TopBar({ brand, subtitle, children, className }: Props) {
  return (
    <header className={`atelier-topbar${className ? ' ' + className : ''}`}>
      <div className="atelier-topbar__brand">
        <span className="atelier-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none">
            <path d="M4 17V8m5 9V4m5 13v-6m5 6V7" stroke="white" strokeWidth="2.1" strokeLinecap="round" />
            <path d="M3 20h18" stroke="white" strokeWidth="1.5" opacity=".55" />
          </svg>
        </span>
        <span className="atelier-brand__copy">
          <span className="atelier-brand__name">{brand}</span>
          {subtitle && <span className="atelier-brand__sub">{subtitle}</span>}
        </span>
      </div>
      {children}
    </header>
  )
}
