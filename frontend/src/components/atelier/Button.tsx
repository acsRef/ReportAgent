import type { ButtonHTMLAttributes, ReactNode } from 'react'

type ButtonVariant = 'primary' | 'default' | 'quiet' | 'danger'
type ButtonSize = 'sm' | 'default' | 'lg'

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  block?: boolean
  loading?: boolean
  children?: ReactNode
}

export default function Button({
  variant = 'default',
  size = 'default',
  block,
  loading,
  children,
  className,
  ...rest
}: Props) {
  const classes = [
    'atelier-btn',
    variant !== 'default' && `atelier-btn--${variant}`,
    size !== 'default' && `atelier-btn--${size}`,
    block && 'atelier-btn--block',
    className,
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <button className={classes} aria-busy={loading || undefined} {...rest}>
      {children}
    </button>
  )
}
