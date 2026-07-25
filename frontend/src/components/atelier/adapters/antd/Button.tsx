/**
 * atelier-styled antd Button
 *
 * Drop-in replacement for `import { Button } from 'antd'` — same API
 * surface, with visual override via the adapter stylesheet.
 *
 * Variants:
 *   - `type="primary"`     → atelier-primary
 *   - `type="link"`        → atelier-quiet (text-only)
 *   - `type="text"`        → atelier-quiet
 *   - `type="dashed"`      → atelier-default
 *   - `type="default"`     → atelier-default
 *   - `type="danger"`      → atelier-danger
 *
 * Sizes:
 *   - `size="small"` → atelier-sm
 *   - `size="middle"`→ atelier-md
 *   - `size="large"` → atelier-lg
 */
import { forwardRef } from 'react'
import { Button as AntdButton } from 'antd'
import type { ButtonProps as AntdButtonProps } from 'antd'

import styles from './antdAdapter.module.css'

export type ButtonProps = AntdButtonProps & {
  /** alias for `type`; takes precedence when both are set. */
  variant?: 'primary' | 'default' | 'quiet' | 'danger' | 'text' | 'link' | 'dashed'
}

function mapVariant(type: ButtonProps['type'], variant: ButtonProps['variant']): string {
  if (variant) {
    return variant === 'quiet' ? 'quiet' : variant
  }
  switch (type) {
    case 'primary': return 'primary'
    case 'danger': return 'danger'
    case 'link':
    case 'text': return 'quiet'
    case 'dashed':
    case 'default':
    default: return 'default'
  }
}

function mapSize(size: ButtonProps['size'] | undefined): 'sm' | 'md' | 'lg' {
  if (size === 'small') return 'sm'
  if (size === 'large') return 'lg'
  return 'md'
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function AtelierButton(
  { type, variant, size, className, block, ...rest },
  ref,
) {
  const mapped = mapVariant(type, variant)
  const sz = mapSize(size)
  const cls = [
    styles['atl-btn'] ?? '',
    styles[`atl-btn--${mapped}`] ?? '',
    styles[`atl-btn--${sz}`] ?? '',
    block ? styles['atl-btn--block'] ?? '' : '',
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ')
  // antd Button accepts `block` natively in v6, so we forward it as a prop
  // and let the local stylesheet override the default rendering.
  return <AntdButton {...rest} ref={ref} type={type} className={cls} block={block} />
})
