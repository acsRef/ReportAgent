# Atelier · 工作室组件库

按 `docs/ui-style-guide.md` 设计的纯 HTML / CSS / JS 组件库，**不依赖任何框架或运行时**，可直接复制到任意项目使用。

## 文件

```text
docs/atelier/
├── index.html        # 73+ 组件 demo 总览
├── tokens.css        # 设计 token（与 docs/ui-style-guide.md 同步）
├── atelier.css       # 组件样式（CSS Module 风格命名 BEM）
├── atelier.js        # Toast / Notification / Modal / Drawer / Popconfirm / Dropdown / Tooltip / 键盘绑定等公共 API
├── gallery.js        # 在 [data-atom] 槽位里填充示例
├── README.md         # 本文件
├── MIGRATION.md      # 与 antd 的映射、改造清单
└── baseline/         # 像素基线 + 回归脚本（占位，待有环境后实际运行）
```

## 启动

```bash
node scripts/dev-server.cjs . 8766
# 访问 http://127.0.0.1:8766/atelier/index.html
```

或直接双击 `index.html` 在浏览器中打开（不依赖 HTTP 服务器；Chrome / Firefox / Safari 都可运行）。

## 组件清单（73+）

按 demo 的 7 个 section 划分：

| Section | 组件 |
| --- | --- |
| 01 基础原子 | Button / IconButton / Tag / Avatar / Chip / Tooltip / Badge / Divider |
| 02 表单 | TextField / TextArea / Checkbox / RadioGroup / SegmentedControl / Select / Slider / TagInput / Stepper / Rate / Color / Date·Time / Switch / Upload / Cascader / Transfer / TreeSelect / Autocomplete / MultiSelect |
| 03 状态与布局 | Spinner / Skeleton / Empty / Progress / Card / Panel / Paper / Stack / Grid / Section |
| 04 重型反馈 | Toast / Notification / Modal / Drawer / Popconfirm / Dropdown / ContextMenu |
| 05 数据展示 | KpiBlock（3/4/5 + Hero）/ Kpi+Sparkline / 17 种图表 / 4 种 Table 报告模式 / Empty / Loading / ReportPaper 紧凑版 / ReportPaper 完整版 |
| 06 导航 / 树 | Tabs / Steps / Breadcrumb / DescriptionList / Treeview / Pagination / ProgressCircle / Empty 变体 / Skeleton 变体 / BackToTop |
| 07 可访问性 | Focus Ring / CommandBar (⌘K) / Tabs 键盘 / Dialog 焦点圈 / Dropdown 键盘 / sr-only |

## 设计 token

`tokens.css` 是单一来源；与 [docs/ui-style-guide.md](../ui-style-guide.md) §2 一致。

主要变量：

| Group | Token | Value |
| --- | --- | --- |
| Surface | `--ink` / `--ink-2` / `--muted` / `--faint` / `--paper` / `--canvas` / `--rail` | 海军蓝 4 级 + 暖灰 3 级 |
| Accent | `--teal` / `--teal-deep` / `--teal-soft` / `--teal-pale` / `--amber` / `--red` / `--green` | 业务强调色 |
| Spacing | `--sp-xs/s/m/l/xl/2xl` | 4 / 8 / 12 / 16 / 24 / 32 |
| Radius | `--r-xs/s/m/l` | 4 / 6 / 8 / 12 |
| Shadow | `--shadow-card/soft/topbar` | 编辑部感阴影 |
| Font | `--font-display/ui/mono` | 中文宋体显示栈 + 无衬线 UI 栈 + 等宽 |
| Motion | `--t-fast/base/slow` | 120 / 200 / 320 ms |

所有组件 CSS 只用 `var(--token)`，不写裸 hex。

## a11y 演示

打开 demo 后，`07 / A11Y` 段提供 6 个可交互示例：

- **Focus Ring**：用 Tab 键在按钮、输入、链接之间循环，每个元素都显示统一的焦点环
- **CommandBar (⌘K)**：按 ⌘K / Ctrl+K 打开；输入过滤、方向键移动、Enter 触发、ESC 关闭
- **Tabs 键盘**：方向键 / Home / End 切换 Tab
- **Dialog 焦点圈**：Modal 打开后 Tab 在表单内循环
- **Dropdown 键盘**：方向键 / Enter / ESC
- **sr-only**：视觉显示与屏幕阅读器分离的写法示例

## 键盘可达性

| 行为 | 按键 |
| --- | --- |
| 主命令面板 | `⌘K` / `Ctrl+K` 打开；`Esc` 关闭 |
| Modal | `Esc` 关闭；`Tab` / `Shift+Tab` 在容器内循环；打开时自动聚焦第一个可聚焦元素；关闭时焦点回退到触发按钮 |
| Drawer | `Esc` 关闭 |
| Popconfirm | `Esc` 取消 |
| Dropdown / ContextMenu | 触发器上 `↑↓` 打开；`↑↓` 在项间移动；`Enter` 触发；`Esc` 关闭；外部点击关闭 |
| Tabs | `←` / `→` 切前/后；`Home` / `End` 跳首/尾 |
| CommandBar | 输入即时过滤；`↑↓` 移动；`Enter` 触发；`Esc` 关闭 |
| Tooltip | hover / focus 显示；`focus` + `blur` 切换 |
| 跳转链接 | 顶部 `.atelier-skip-link`，按 Tab 出现后回车跳到主内容 |

## 浏览器

已用 Chrome 自动化实测 73+ 组件渲染，**无 JavaScript 控制台错误**；可视对比覆盖 1440 桌面宽度。`table-flat / table-tree / table-indent / table-cross` 与原型中的 4 种报告模式一一对应。

## 后续

- 像素基线 + 回归脚本见 `baseline/`（占位，待有环境后实际运行）
- antd 适配层见 `frontend/src/components/atelier/adapters/antd/` 与 `MIGRATION.md`
- 移动到 React / Vue 组件库的迁移以 `MIGRATION.md` 中的"组件 API 描述"为单一来源
