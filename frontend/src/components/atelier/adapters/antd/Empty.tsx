import { forwardRef } from 'react'
import { Empty as AntdEmpty } from 'antd'
import type { EmptyProps as AntdEmptyProps } from 'antd'

import styles from './antdAdapter.module.css'

export type EmptyProps = AntdEmptyProps

export const Empty = forwardRef<HTMLDivElement, EmptyProps>(function AtelierEmpty(
  { className, ...rest },
  ref,
) {
  const cls = [styles['atl-empty'] ?? '', className ?? ''].filter(Boolean).join(' ')
  return <AntdEmpty {...rest} ref={ref} className={cls} />
})
