# ReportAgent UI Style Guide

> 本规范基于已确认的 HTML 原型 [`docs/intelligent-analysis-workbench.html`](../intelligent-analysis-workbench.html)。所有前端实现必须按照本规范与原型的视觉与交互一致，Ant Design 仅作为无障碍与交互能力，视觉服从原型。

## 1. Design Principles

1. **Editorial / atelier tone** — 页面像编辑部分析工作室，而不是默认企业 BI 仪表盘。
2. **Paper canvas** — 暖灰画布 + 纸张白卡片 + 细网格，弱化冷感。
3. **Navy + teal as the only accent family** — 深海军蓝表达结构、克制；青绿色表达分析结果、关键操作。
4. **Typography does the work** — 中文宋体显示标题承载品牌与编辑感，无衬线作为数据/操作界面。
5. **Imperfection by design** — 轻网格、纸张阴影、左边小色条、不齐整整的间距让分析感更真实。
6. **Ant Design is invisible** — 所有 Ant Design 控件必须重塑成原型同源视觉。
7. **Information density with breathing room** — 高信息密度但通过留白与分组避免拥挤。

## 2. Color Tokens

以 CSS variables 形式落地，禁止使用魔法色值。

### 2.1 Surface and structure

| Token | Value | 用途 |
| --- | --- | --- |
| `--ink` | `#10243e` | 深海军蓝，文字、按钮、卡片强调 |
| `--ink-2` | `#293d53` | 次级文字、轴、辅助线 |
| `--muted` | `#68798a` | 第三层文字、描述 |
| `--faint` | `#95a19e` | 占位、细分隔线 |
| `--paper` | `#fffefb` | 卡片背景 / 纸张白 |
| `--canvas` | `#f3f3ee` | 主画布，暖灰 |
| `--rail` | `#eaede9` | 侧栏背景 |
| `--line` | `#dde2de` | 普通分隔线 |
| `--line-2` | `#cdd5d0` | 强调分隔线 |

### 2.2 Accent and status

| Token | Value | 用途 |
| --- | --- | --- |
| `--teal` | `#087f73` | 主操作按钮、关键指示、状态条 |
| `--teal-deep` | `#06665e` | 主操作悬停/按下 |
| `--teal-soft` | `#dff2ed` | 强调卡片底色 |
| `--teal-pale` | `#f2faf7` | 当前选中态底色 |
| `--amber` | `#b36c0d` | 异常/需要补充指示 |
| `--amber-soft` | `#fff0d6` | 异常卡片底色 |
| `--red` | `#b94a48` | 错误/失败指示 |
| `--red-soft` | `#fae7e4` | 错误卡片底色 |
| `--green` | `#23836f` | 完成状态指示 |

## 3. Typography

### 3.1 字体栈

- **Display / Heading** — `"Songti SC", "STSong", "Noto Serif CJK SC", Georgia, serif`
- **UI / Body / Data** — `"Avenir Next", "PingFang SC", "Microsoft YaHei", sans-serif`
- **Mono** — `"SFMono-Regular", Consolas, monospace`

### 3.2 排版层级

| 角色 | 字号 / 行高 | 字重 | 字体栈 | 用途 |
| --- | --- | --- | --- | --- |
| Hero report title | 26–32 / 1.22 | 700 | Serif | 报告标题 |
| Section title | 17 / 1.25 | 700 | Serif | 章节标题（01 / SCALE） |
| Subsection | 13 / 1.35 | 700 | Serif | 子标题 |
| Body emphasis | 12 / 1.6 | 650 | Serif | 报告段落强调 |
| UI label | 11 / 1.4 | 700 | Sans | 表单/按钮 label |
| Body | 11 / 1.55 | 400 | Sans | 默认文本 |
| Caption / meta | 9–10 / 1.4 | 600 | Sans | 辅助说明 |
| Numeric data | tabular-nums | 600 | Sans | 数字/同比 |

### 3.3 文本颜色

- 标题：`--ink`
- 正文：`--ink-2`
- 描述/次要：`--muted`
- 占位/弱化：`--faint`
- 链接/重点：`--teal-deep`
- 警告：警告用 `--amber`，错误用 `--red`

## 4. Spacing, Radius, Shadow

| Token | Value | 用途 |
| --- | --- | --- |
| Spacing XS | 4 | 标签与正文 |
| Spacing S | 8 | 行内间距 |
| Spacing M | 12 | 卡片内边距起步 |
| Spacing L | 16 | 卡片内边距默认 |
| Spacing XL | 24 | 区段间距 |
| Spacing 2XL | 32 | 大区块间距 |
| Radius XS | 4 | 标签/输入 |
| Radius S | 6 | 按钮 |
| Radius M | 8 | 卡片 |
| Radius L | 12 | 大卡片/对话框 |
| Shadow card | `0 18px 55px rgba(22,42,59,.09)` | 主报告卡 |
| Shadow soft | `0 8px 26px rgba(22,42,59,.07)` | 浮层/二级卡 |
| Topbar shadow | `0 8px 30px rgba(16,36,62,.18)` | 深色顶栏 |

## 5. Layout Grid

### 5.1 桌面（1440+）

```text
┌ 顶栏（高 58）  工作台｜模板中心      ● 已连接  用户
├ 左栏（268）│  中央工作区             │ 右栏（300）
│ 新建分析    │  连续对话 + 需求卡     │ 阶段信息
│ 会话历史    │  生成进度 + 报告       │ 业务建议
│ 收藏报告    │  报告版本切换          │ Runtime（折叠）
└─────────────┴────────────────────────┴──────────┘
```

- 中央工作区最大宽 1080，居中
- 卡片左右留 22px 内边距，左右卡片/标题保持对齐

### 5.2 平板（1180）

- 隐藏右栏
- 左侧与中央保持
- 右侧信息（Runtime、阶段详情）通过浮层或中央内联展示

### 5.3 窄屏（880）

- 隐藏左栏与右栏
- 中央铺满
- 顶栏保留工作台/模板中心切换、连接、用户

## 6. Component Theming (Ant Design)

所有 Ant Design 控件必须按本节重新主题化。`ConfigProvider` 仅给 token 基线，portal、弹层、Message、Notification、Tooltip 等还需要 CSS Modules 局部覆盖。

### 6.1 Button

| 状态 | 视觉 |
| --- | --- |
| primary default | 背景 `--teal`，文字 `#fff`，无边框 |
| primary hover/active | 背景 `--teal-deep` |
| primary disabled | 背景 `--teal` 不透明度 0.42 |
| default default | 背景 `--paper`，边框 `--line-2`，文字 `--ink-2` |
| default hover | 边框 `--teal`，文字 `--teal-deep` |
| quiet (text) | 透明背景，文字 `--ink-2`，hover 文字 `--teal-deep` |
| 圆角 | 6 |
| 高度 | 32（中）/ 38（主操作） |

### 6.2 Input

- 背景 `--paper`
- 边框 `--line-2`
- hover 边框 `--teal`
- focus 边框 `--teal`，2px 外发光 `rgba(8,127,115,.18)`，无默认黑色 outline
- placeholder 颜色 `--faint`
- 圆角 6
- 高度 32（小）/ 38（默认）

### 6.3 Select / Dropdown

- trigger 视觉同 Input
- 弹层背景 `--paper`
- 弹层阴影 `--shadow-soft`，边框 `--line`
- option hover/selected 背景 `--teal-soft`，文字 `--teal-deep`
- 分组标题/分隔线用 `--line`
- 滚动条使用原型细滚动条样式

### 6.4 Modal / Drawer

- 头部 `background: var(--paper); border-bottom: var(--line)`
- 关闭按钮 hover 背景 `--teal-soft`
- 主体 24px 内边距
- 底部 12px 内边距，分隔线
- 遮罩 `rgba(16, 36, 62, 0.32)`，不使用黑色

### 6.5 Tooltip

- 背景 `--ink`
- 文字 `#fff`
- 圆角 4
- 不使用 Ant 默认白底
- 箭头 `--ink`

### 6.6 Tabs

- inkbar 颜色 `--teal`
- selected 文字 `--ink-2`，加粗
- unselected 文字 `--muted`
- hover 文字 `--ink-2`
- 底部线 `--line`

### 6.7 Table

- 表头背景 `#f0f2ef`
- 表头文字 `--muted`
- 行高 36
- 行 hover 背景 `#f8faf7`
- 分隔线 `--line`
- 数字列 `text-align: right; font-variant-numeric: tabular-nums`

### 6.8 Message / Notification

- 成功背景 `--teal-pale`，文字 `--teal-deep`，左侧 4px 条 `--teal`
- 警告背景 `--amber-soft`，文字 `--amber`
- 错误背景 `--red-soft`，文字 `--red`
- 圆角 8，阴影 `--shadow-soft`

### 6.9 Skeleton

- 块背景 `#eceeea`，动画 `1.2s ease-in-out infinite`
- 不使用 Ant 默认粉灰

### 6.10 Form

- label 文字 `--ink-2`，加粗
- 校验失败文字 `--red`
- 校验中 文字 `--muted`
- 必填标记 `--teal`

## 7. Component Patterns (from the prototype)

### 7.1 Workbench top bar

- 深海军蓝背景 `#10243e`
- Logo 区域 9px/15px 宋体大写品牌名 + 8px 副标题
- Tab 高度 100%，4 个，间距 24px，selected 底部 2px `--teal` 强调线
- 右侧 Connection 指示灯（live dot 4px `#41d1b4`）
- Userchip 27px 圆形 avatar + 文字

### 7.2 Left rail

- 宽度 268，背景 `--rail`
- 顶部 12px 内边距 + 38px 高 “新建分析” 按钮
- 群组标题 9–10px 大写灰字
- 会话行：29px 图标 + 标题 + 时间 + 状态 chip
- 仅 active 会话展开报告版本时间线
- 报告版本按钮：9px 圆点 + 标题 + 当前/时间
- 底部 11px Session 标签

### 7.3 Center canvas

- 背景 `--canvas` + 28px 细网格（rgba 2.4%）
- 头部 54px 高，背景 rgba(255,254,251,0.9)，backdrop-filter blur 12px
- 中间内容居中最大 1080
- 消息气泡：用户右对齐（深海军蓝背景），Agent 左对齐（纸张白+边线）
- 输入栏：12px 圆角 fixed bottom，纸张白+blur

### 7.4 Requirement card

- 左 4px 色条：complete `--teal` / missing `--amber` / locked `#cdd5d0`
- 头部 kicker 8px 大写彩色
- summary 行暖灰背景 `--teal-pale` → 渐变到 `--paper`
- 字段两列 12 栅格 24px gap
- 缺失字段用 chip 选项 + 浅色背景
- 假设行 `--amber-soft` 背景
- 底部按钮组：左 secondary `修改` / 右 primary `确认并生成报告`

### 7.5 Generation progress

- 4 阶段：锁定需求 / 准备数据 / 执行查询 / 生成报告
- 每阶段：8–10px padding，文本说明 + 时长
- 进行中：文字 `--ink-2`，加粗
- 完成：文字 `--teal-deep`，背景 `--teal-soft`
- 进度条：4px 高，背景 `--line`，填充 `--teal`

### 7.6 Report paper

- 居中卡片，最大 1100
- 左 4px `--teal` 边条
- 报告标题 26–32px serif
- 核心发现：暖青绿色背景带渐变
- KPI 4 列 1px 边框分隔
- 趋势图：220px 高 inline SVG
- 异常卡片：amber-soft 背景
- 表格：表头浅灰，行 hover 浅米色
- 报告脚：分隔线 + meta

### 7.7 Right rail

- 宽度 300，背景 `--paper`
- 阶段信息卡 + 完整度条
- 阶段列表 4 项
- 当前需求范围 chip 列表
- Runtime 折叠区，展开时按 4 阶段列出节点

## 8. Iconography

- 业务图标：24–28px，2px 描边
- 状态指示：纯色圆点 4–6px
- 状态色板：teal / amber / red / green，与文本同色
- 不用 emoji 作为状态指示；emoji 仅在示例/演示中允许
- icons 集中在 `components/ui/Icons.tsx`，SVG 内联

## 9. Motion

- 入场：fadeIn 250ms，translateY 6px → 0
- 报告阶段：进度条 350ms 过渡
- 按钮 hover/active：颜色 180ms 过渡
- 卡片 hover：translateY -2px + 阴影柔化 180ms
- 报告版本切换：fade 200ms
- 谨慎使用动画；避免与数据本身无关的长时间动画

## 10. Accessibility

- 所有交互元素键盘可达
- focus 2px outline `rgba(8,127,115,.32)`，偏移 2px
- 文字与背景对比度满足 WCAG AA（深海军蓝 #10243e on #fffefb 对比 > 11）
- 状态信息附文本（不仅靠颜色）
- Ant Design 控件保留原生无障碍属性

## 11. Don'ts

- ❌ 不使用 `Inter / Roboto / Arial` 等通用无衬线作为主显示
- ❌ 不使用 Ant Design 默认的 `#1677ff` 蓝色按钮
- ❌ 不使用 Ant Design 默认白底 Tooltip
- ❌ 不使用紫色渐变背景
- ❌ 不使用默认抽屉、默认 Card 阴影、默认分段控件
- ❌ 不在大段 inline style 中写魔法色值
- ❌ 不在按钮文字中使用纯 emoji
- ❌ 不让运行过程成为永久性主面板
- ❌ 不暴露后端工具 key 给业务用户
- ❌ 不在没有确认前自动查询业务数据

## 12. Cross-Reference

- 视觉基准：[`docs/intelligent-analysis-workbench.html`](../intelligent-analysis-workbench.html)
- 组件清单：plan 第 8.3 节
- 颜色对照：本文件第 2 节
- 字体对照：本文件第 3 节
- Ant Design 重塑清单：本文件第 6 节
- 全应用状态机：plan 第 6 节
