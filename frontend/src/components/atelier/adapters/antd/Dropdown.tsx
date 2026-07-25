import { Dropdown as AntdDropdown } from 'antd'
import type { DropdownProps as AntdDropdownProps } from 'antd'

export type DropdownProps<T = unknown> = AntdDropdownProps<T>

/**
 * Atelier Dropdown wrapper — antd's default Dropdown already accepts className.
 */
export function Dropdown<T = unknown>(props: DropdownProps<T>) {
  return <AntdDropdown<T> {...props} />
}
