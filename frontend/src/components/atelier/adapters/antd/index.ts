/**
 * Atelier antd adapter layer
 *
 * 每个导出与 antd 同名，函数体直接转发到 antd 组件。视觉由
 * `antdAdapter.module.css` 局部覆盖到 atelier 设计 token。
 *
 * 目的：
 * 1. 不删 antd 依赖即可让现有 antd 页面立即具备 atelier 视觉；
 * 2. 后续在 MIGRATION.md 各阶段把 antd 组件替换为真 atelier 组件时，
 *    只需改 import 路径即可，调用方代码无感。
 *
 * ⚠ 本适配层不复制 antd 全部 props（实现成本太高），只覆盖最常用的
 * 几个高频 prop（variant / tone / size / etc.）。其余 prop 透传。
 */
export {
  ConfigProvider as ConfigProvider,
  App as AntdApp,
} from 'antd'

export { Button } from './Button'
export { Tag } from './Tag'
export { Empty } from './Empty'
export { Spin } from './Spin'
export { Avatar } from './Avatar'
export { Tooltip } from './Tooltip'
export { Dropdown } from './Dropdown'
export { Drawer } from './Drawer'

export type { ButtonProps } from 'antd'
export type { TagProps } from 'antd'
export type { EmptyProps } from 'antd'
export type { SpinProps } from 'antd'
