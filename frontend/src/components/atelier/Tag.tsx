import type { CSSProperties, ReactNode } from 'react'

type TagTone = 'teal' | 'amber' | 'red' | 'green' | 'ink' | 'default'

interface Props {
  tone?: TagTone
  children?: ReactNode
  className?: string
  style?: CSSProperties
}

export default function Tag({ tone = 'default', children, className, style }: Props) {
  return (
    <span
      className={`atelier-tag atelier-tag--${tone}${className ? ' ' + className : ''}`}
      style={style}
    >
      {children}
    </span>
  )
}
