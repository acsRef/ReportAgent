import { Drawer as AntdDrawer } from 'antd'
import type { DrawerProps as AntdDrawerProps } from 'antd'

export type DrawerProps = AntdDrawerProps

/**
 * Atelier Drawer wrapper — antd's default Drawer already accepts className.
 */
export function Drawer(props: DrawerProps) {
  return <AntdDrawer {...props} />
}
