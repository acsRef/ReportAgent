import type { CSSProperties, ReactNode } from 'react'

type TextType = 'secondary' | 'danger' | 'warning' | 'default'

interface TextProps {
  children?: ReactNode
  strong?: boolean
  type?: TextType
  style?: CSSProperties
  className?: string
}

const TEXT_COLORS: Record<TextType, string | undefined> = {
  secondary: 'var(--muted)',
  danger: 'var(--red)',
  warning: 'var(--amber)',
  default: undefined,
}

function Text({ children, strong, type = 'default', style, className }: TextProps) {
  const Tag = strong ? 'strong' : 'span'
  return (
    <Tag
      className={className}
      style={{
        color: TEXT_COLORS[type],
        fontWeight: strong ? 700 : undefined,
        ...style,
      }}
    >
      {children}
    </Tag>
  )
}

type TitleLevel = 1 | 2 | 3 | 4 | 5

interface TitleProps {
  children?: ReactNode
  level?: TitleLevel
  style?: CSSProperties
  className?: string
}

const TITLE_TAG: Record<TitleLevel, 'h1' | 'h2' | 'h3' | 'h4' | 'h5'> = {
  1: 'h1', 2: 'h2', 3: 'h3', 4: 'h4', 5: 'h5',
}

const TITLE_FONT: Record<TitleLevel, number> = {
  1: 28, 2: 22, 3: 18, 4: 16, 5: 14,
}

function Title({ children, level = 5, style, className }: TitleProps) {
  const Tag = TITLE_TAG[level]
  return (
    <Tag
      className={className}
      style={{
        fontFamily: 'var(--font-display)',
        fontSize: TITLE_FONT[level],
        fontWeight: 700,
        margin: 0,
        color: 'var(--ink)',
        ...style,
      }}
    >
      {children}
    </Tag>
  )
}

interface ParagraphProps {
  children?: ReactNode
  style?: CSSProperties
  className?: string
}

function Paragraph({ children, style, className }: ParagraphProps) {
  return (
    <p
      className={className}
      style={{ margin: 0, color: 'var(--ink-2)', fontSize: 14, lineHeight: 1.6, ...style }}
    >
      {children}
    </p>
  )
}

const Typography = { Text, Title, Paragraph }
export { Text, Title, Paragraph }
export default Typography
