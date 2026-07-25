import { forwardRef } from 'react'
import { Spin as AntdSpin } from 'antd'
import type { SpinProps as AntdSpinProps } from 'antd'

import styles from './antdAdapter.module.css'

export type SpinProps = AntdSpinProps

function mapSize(s: SpinProps['size']): 'sm' | 'md' | 'lg' {
  if (s === 'small') return 'sm'
  if (s === 'large') return 'lg'
  return 'md'
}

export const Spin = forwardRef<HTMLDivElement, SpinProps>(function AtelierSpin(
  { size, className, ...rest },
  ref,
) {
  const sz = mapSize(size)
  const cls = [
    'atelier-spinner',
    `atelier-spinner--${sz}`,
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ')
  return (
    <span className={cls} aria-busy="true">
      <AntdSpin {...rest} ref={ref} size={size} />
    </span>
  )
})
