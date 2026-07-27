import type { ReactNode } from 'react'

type AvatarSize = 'sm' | 'default' | 'lg'

interface Props {
  size?: AvatarSize
  children?: ReactNode
  className?: string
}

export default function Avatar({ size = 'default', children, className }: Props) {
  const classes = [
    'atelier-avatar',
    size !== 'default' && `atelier-avatar--${size}`,
    className,
  ]
    .filter(Boolean)
    .join(' ')

  return <span className={classes}>{children}</span>
}
