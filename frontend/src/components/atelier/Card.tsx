import { type CSSProperties, type ReactNode } from 'react'

interface Props {
  title?: ReactNode
  extra?: ReactNode
  children?: ReactNode
  size?: 'default' | 'small'
  hoverable?: boolean
  onClick?: () => void
  bodyStyle?: CSSProperties
  style?: CSSProperties
  className?: string
}

export default function Card({
  title,
  extra,
  children,
  size = 'default',
  hoverable,
  onClick,
  bodyStyle,
  style,
  className,
}: Props) {
  return (
    <div
      className={className}
      onClick={onClick}
      style={{
        position: 'relative',
        background: 'var(--paper)',
        border: '1px solid var(--line-2)',
        borderRadius: 10,
        padding: size === 'small' ? '12px 14px' : '18px 18px var(--sp-l)',
        boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--sp-m)',
        cursor: hoverable || onClick ? 'pointer' : undefined,
        transition: 'box-shadow 0.2s, transform 0.2s',
        ...style,
      }}
    >
      {title && (
        <div
          style={{
            font: '700 11px var(--font-ui)',
            color: 'var(--muted)',
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
            borderBottom: '1px solid var(--line)',
            paddingBottom: 'var(--r-s)',
            marginBottom: 'var(--sp-xs)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <span>{title}</span>
          {extra && <span>{extra}</span>}
        </div>
      )}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', ...bodyStyle }}>
        {children}
      </div>
    </div>
  )
}
