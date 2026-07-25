import { forwardRef } from 'react'
import { Avatar as AntdAvatar } from 'antd'
import type { AvatarProps as AntdAvatarProps } from 'antd'

import styles from './antdAdapter.module.css'

export type AvatarProps = AntdAvatarProps

function mapSize(s: AvatarProps['size'] | number): 'sm' | 'md' | 'lg' {
  if (s === 'small' || s === 24) return 'sm'
  if (s === 'large' || s === 40) return 'lg'
  return 'md'
}

export const Avatar = forwardRef<HTMLSpanElement, AvatarProps>(function AtelierAvatar(
  { size, className, ...rest },
  ref,
) {
  const sz = mapSize(size)
  const cls = [
    styles['atl-avatar'] ?? '',
    `atelier-avatar--${sz}`,
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ')
  return <AntdAvatar {...rest} ref={ref} className={cls} />
})
