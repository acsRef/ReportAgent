import { Tooltip as AntdTooltip } from 'antd'
import type { TooltipProps as AntdTooltipProps } from 'antd'

export type TooltipProps = TooltipProps

/**
 * Atelier Tooltip wrapper — antd's default Tooltip already accepts className,
 * so we forward the props and rely on the global adapter stylesheet for theming.
 */
export function Tooltip(props: TooltipProps) {
  return <AntdTooltip {...props} />
}
