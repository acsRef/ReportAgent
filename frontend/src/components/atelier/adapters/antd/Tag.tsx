/**
 * atelier-styled antd Tag
 */
import { forwardRef } from 'react'
import { Tag as AntdTag } from 'antd'
import type { TagProps as AntdTagProps } from 'antd'

import styles from './antdAdapter.module.css'

export type TagProps = AntdTagProps & {
  /** alias for `color`; takes precedence when both are set. */
  tone?: 'neutral' | 'teal' | 'amber' | 'red' | 'green' | 'ink'
}

const COLOR_TO_TONE: Record<string, TagProps['tone']> = {
  blue: 'teal',
  green: 'green',
  orange: 'amber',
  red: 'red',
  default: 'neutral',
  gold: 'amber',
  cyan: 'teal',
  purple: 'teal',
  magenta: 'teal',
  volcano: 'red',
  lime: 'green',
  gray: 'ink',
  grey: 'ink',
}

function mapTone(color: AntdTagProps['color'], tone: TagProps['tone']): TagProps['tone'] {
  if (tone) return tone
  if (typeof color === 'string' && color in COLOR_TO_TONE) return COLOR_TO_TONE[color]
  return 'neutral'
}

export const Tag = forwardRef<HTMLSpanElement, TagProps>(function AtelierTag(props, ref) {
  const { color, tone, className, ...rest } = props
  const t = mapTone(color, tone)
  const cls = [
    styles['atl-tag'] ?? '',
    styles[`atl-tag--${t}`] ?? '',
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ')
  return <AntdTag {...rest} ref={ref} className={cls} />
})
