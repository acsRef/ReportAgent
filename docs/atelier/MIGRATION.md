# MIGRATION — antd → Atelier 迁移清单

> 本文件是 **“下一步怎么把现有 antd 页面改造成 atelier 视觉”** 的可执行清单。
>
> 在 [docs/atelier/index.html](index.html) demo 中，每个组件都已经在原型视觉中验证可用。
> 此处不删 antd 依赖；只列出每个 antd 组件对应的 atelier 组件、需要做的改造与风险。

## 0. 迁移原则

1. **不要一次性删 antd**。用 `adapters/antd/` 命名导出代理到 antd 组件，但通过 atelier 主题与 CSS Module 局部覆盖让它们视觉与原型一致。
2. **先适配，再替换**。某页面全部用上 `adapters/antd` 后，再把该页面 import 改为 `atelier` 真正组件，**逐页面**逐步移除 antd 调用。
3. **业务逻辑不动**。只动 props 名称（如 `type="primary"` → `variant="primary"`）、CSS、事件。
4. **测试先行**。每个组件替换前先加 `vitest` 单元测试覆盖事件与 a11y。

## 1. API 映射表（核心 16 个）

| antd | atelier | 关键 props 变化 | 风险 |
| --- | --- | --- | --- |
| `<Button type="primary">` | `<Button variant="primary">` | `type → variant`、`danger → variant=danger` | 低 |
| `<Button type="link">` | `<Button variant="quiet">` | 同上 | 低 |
| `<Tag color="...">` | `<Tag tone="...">` | `color="teal" → tone="teal"`；`color="red" → tone="red"` | 低 |
| `<Input />` `<Input.TextArea />` | `<TextField />` `<TextArea />` | `value/onChange` 保持；`prefix` 改为 `startAdornment` | 低 |
| `<Input.Password />` | `<TextField type="password" />` | 同上 | 低 |
| `<Select />` | `<Select />` | options / value / onChange 保持；`mode` 改为 `multiple` 单独 | 中（需自实现 popup） |
| `<Checkbox.Group>` | `<CheckboxGroup />` | `value/onChange` 保持 | 中 |
| `<Radio.Group>` | `<RadioGroup />` | 同上 | 中 |
| `<Slider />` | `<Slider />` | `value/onChange` 保持；`range` 暂未实现 | 低 |
| `<Empty />` | `<Empty />` | `description` 改为 slot/children | 低 |
| `<Spin />` | `<Spinner />` | `tip` 改为 label | 低 |
| `<Modal />` | `<Modal />` | `onCancel` 保持；`visible` 改为 `open`；footer 改为 actions 数组 | 中（focus trap 需自实现，已在 demo） |
| `<Drawer />` | `<Drawer />` | 同上 | 中 |
| `<Popconfirm />` | `<Popconfirm />` | `title / onConfirm / onCancel` 保持 | 中（placement 简化） |
| `<Dropdown menu={items}>` | `<Dropdown items={...} />` | `menu → items`，`key → key` | 中 |
| `<App.useApp() + message.x>` | `useToast()` | 5 处全部替换 | 中（需 Toast Provider） |

## 2. 现有页面改造顺序

按 `frontend/src/pages/` 列表推进：

| 页面 | 当前状态 | 改造难度 | 建议阶段 |
| --- | --- | --- | --- |
| `LoginPage.tsx` | 单表单 + 默认主题 | 简单 | 阶段 A |
| `WorkbenchPage.tsx` | 三栏 + 大量 antd | 中 | 阶段 B |
| `RequirementCardView.tsx` | Radio.Group / Checkbox.Group | 中 | 阶段 B |
| `ReportPaper.tsx` | 4 区块 | 简单（已无 antd） | 阶段 B |
| `TemplateLibraryPage.tsx` | Form + Modal + Popconfirm | 中 | 阶段 C |
| `SecureReportPage.tsx` | Layout + Spin + Tag | 简单 | 阶段 C |
| `HistoryPage.tsx` | Typography / Empty | 简单 | 阶段 C |
| `TemplateCenter.tsx` | Card + Row + Modal + Form | 中（legacy） | 阶段 D（Phase 8 删除时） |
| `ChatPage.tsx` | Select + Modal + Form | 中（legacy） | 阶段 D |
| `views/ChatView.tsx` | 大量 inline | 中（legacy） | 阶段 D |
| `views/RunningView.tsx` | Spin + Text | 简单（legacy） | 阶段 D |
| `views/ReportView.tsx` | Card + Skeleton + Typography | 简单（legacy） | 阶段 D |
| `StandaloneReportPage.tsx` | Typography | 简单（legacy） | 阶段 D |

## 3. 阶段 A：基础替换（LoginPage）

- `pages/LoginPage.tsx`
  - 替换 `App.useApp() + message.error` → `useToast()` + `<Toast.Provider>` 包裹。
  - 替换 `Input` 与 `Input.Password` → atelier `TextField`。
  - `Button` 改用 `adapters/antd` 的 `Button`（视觉已经 atelier 化），或直接用 atelier `Button`。
  - 登录卡片背景用 `--paper`、表单 label 使用 `atelier-field__label`。
- 配套 `frontend/src/stores/authStore.ts`：仅消费 token，不动逻辑。

## 4. 阶段 B：工作台核心（WorkbenchPage + RequirementCardView）

- `pages/WorkbenchPage.tsx`
  - `App.useApp() + message.success/error` → `useToast()`。
  - `Layout.Header` → 自定义 `TopBar`（深海军蓝背景 + 4 tab）。
  - `Empty / Tag / Avatar / Dropdown` → atelier 对应组件。
  - 进度阶段改为 `ProgressCircle` + `Stepper` 组合。
- `components/workbench/RequirementCardView.tsx`
  - `Radio.Group` / `Radio.Button` → `<RadioGroup kind="pill" />`。
  - `Checkbox.Group` → `<CheckboxGroup />`。
  - 整张卡片用 `<RequirementCard status="missing|complete|locked" />` 封装。

## 5. 阶段 C：模板中心 + 报告查看 + 历史

- `pages/TemplateLibraryPage.tsx`
  - 替换 `Modal` / `Popconfirm` 为 atelier 组件。
  - `Form.useForm + rules` 改用 react-hook-form 包装的 `<Form>`，或继续用 antd `<Form>` 但通过 `classNames` 与 `styles` 控制视觉。
- `pages/SecureReportPage.tsx` 与 `pages/HistoryPage.tsx`
  - 用 atelier `Empty` / `ReportPaper`。
  - `Layout` → 普通 `<header>` / `<main>` 标签。

## 6. 阶段 D：legacy 页面（与 Phase 8 协同）

- 直接迁移到 atelier 组件后整页删除旧代码。
- 不再保留 legacy `Card / Row / Col / Form / Modal / Select`，避免 antd 在仓库任何位置继续被引用。

## 7. 组件级风险

| 风险 | 缓解 |
| --- | --- |
| `<Select>` 弹出层 keyboard a11y（方向键 / 翻页 / 首字母跳转） | 阶段 C 之前完成 `useListbox` hook；先于 ReportForm 之外使用 |
| `<Modal>` focus trap 边界 | 使用 demo 现有的 `useFocusTrap`，先在 LoginPage 实测 |
| `<Popconfirm>` placement 与 popover 重叠 | 平台归一化到 12 个 placement |
| `<DatePicker>` / `<TimePicker>` | 暂时保留 antd 组件，但通过 `classNames/styles` 控制视觉；不进入 atelier v1 范围 |
| `<Table>` 4 种报告模式 | 已用纯 HTML `<table>` + CSS Module 实现，可直接替换 |
| 图表 17 种 | 短期仍用 SVG 手写；中期评估 `echarts-for-react` 并以 atelier 主题重写 theme |
| `<App.useApp>` 多入口 | 顶层注入 `<Toast.Provider>` 一次，业务页用 `useToast()` |

## 8. 视觉一致性验收清单

每个页面改造后，必须用以下方式检查：

- [ ] 1440 桌面：原型色板（teal `#087f73`、amber `#b36c0d`、red `#b94a48`、ink `#10243e`、paper `#fffefb`、canvas `#f3f3ee`）
- [ ] 1180 平板：右栏折叠；工作台两栏
- [ ] 880 移动：左右栏折叠；中央铺满
- [ ] 浏览器控制台 0 warning（`App.useApp` static warning 等）
- [ ] `npm run lint` / `tsc -b` 0 错误
- [ ] `vitest run` 0 失败
- [ ] `playwright smoke` 9 步全绿
- [ ] 视觉对比 `baseline/` 下基线图片差异 < 0.1%

## 9. 进度跟踪

每个阶段 commit 至少 1 个，并附 diff review：

| 阶段 | 起始 commit | 主要改动 |
| --- | --- | --- |
| A | TBD | `LoginPage` + `authStore` + 新增 `useToast` |
| B | TBD | `WorkbenchPage` + `RequirementCardView` + `TopBar` |
| C | TBD | `TemplateLibraryPage` + `SecureReportPage` + `HistoryPage` |
| D | TBD | 删除 legacy，删除 `antd` 依赖 |

## 10. 仍然依赖 antd 的部分（阶段 D 之前保留）

- `Form.useForm + rules` 校验（仅在 LoginPage / TemplateLibraryPage 局部使用，阶段 D 全部迁移到 react-hook-form）
- `DatePicker` / `TimePicker`（未在 atelier 范围；可继续用 antd + atelier 主题包装）
- `Carousel` / `Cascader` / `TreeSelect`（atelier 已提供 HTML 骨架，antd 复杂行为可阶段 D 决策）
