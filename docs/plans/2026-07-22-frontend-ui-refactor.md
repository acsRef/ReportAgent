# 前端 UI 重构 + 功能补全 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将前端从手写 inline style 彻底改造为 Ant Design 驱动的完整产品，实现"对话 → 执行 → 报告"三态视图切换，支持 4 种报表视觉样式，补齐模板/历史/导出功能

**Architecture:** 中间区域三态视图机（Chat → Running → Report），左侧 Sidebar 导航，右侧 Agent Runtime 面板。报表渲染层支持 4 种视觉样式切换，基于同一份数据源。所有颜色/间距/阴影走 Ant Design Design Token。

**Tech Stack:** React 19 + TypeScript 6.0 + Ant Design 6.x + ECharts 6.x + Zustand 5.x

---

## Task 1: 建立 Design Token 体系

**当前问题：** 颜色、间距、阴影全部硬编码，没用 Ant Design 的 Design Token。

**方案：** 扩展 `antdTheme.ts`，定义完整的 Token 配置，所有组件通过 `useToken()` 获取主题变量。

**Files:**
- Modify: `frontend/src/theme/antdTheme.ts`

**Step 1: 重写主题配置**

```typescript
import type { ThemeConfig } from 'antd'

const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: '#1677ff',
    colorSuccess: '#52c41a',
    colorWarning: '#faad14',
    colorError: '#ff4d4f',
    colorInfo: '#1677ff',
    borderRadius: 6,
    fontFamily: `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif`,
    fontSize: 13,
    colorBgContainer: '#ffffff',
    colorBgLayout: '#f5f6f9',
    colorBorderSecondary: '#e8e8e8',
  },
  components: {
    Layout: {
      headerBg: '#001529',
      bodyBg: '#f5f6f9',
      siderBg: '#ffffff',
    },
    Card: {
      paddingLG: 20,
      paddingMD: 16,
      boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
    },
    Table: {
      headerBg: '#fafafa',
    },
    Menu: {
      itemBg: 'transparent',
      itemColor: 'rgba(255,255,255,0.55)',
      itemSelectedColor: '#ffffff',
      itemHoverColor: 'rgba(255,255,255,0.85)',
    },
    Button: {
      controlHeight: 36,
      controlHeightSM: 28,
    },
  },
}

export default antdTheme
```

**Step 2: 提交**

```bash
git add frontend/src/theme/antdTheme.ts
git commit -m "refactor(frontend): define complete Ant Design Design Token config"
```

---

## Task 2: 重构整体布局 — 三栏 + 三态视图

**当前问题：** 左右面板用 `div` + inline style，无视图切换概念。

**方案：** 使用 Ant Design `Layout` / `Sider` / `Content` 组件，实现三栏布局。中间区域实现 Chat / Running / Report 三态切换。

**Files:**
- Create: `frontend/src/components/layout/AppLayout.tsx` — 整体三栏布局
- Create: `frontend/src/components/layout/Sidebar.tsx` — 左侧面板
- Modify: `frontend/src/App.tsx` — 路由布局
- Modify: `frontend/src/pages/ChatPage.tsx` — 精简为对话视图

**Step 1: 创建 AppLayout**

```typescript
// AppLayout.tsx
import { Layout, theme } from 'antd'
import Navbar from './Navbar'
import Sidebar from './Sidebar'
import AgentRuntime from './AgentRuntime'

const { Content } = Layout

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { token } = theme.useToken()
  const [view, setView] = useState<'chat' | 'running' | 'report'>('chat')

  return (
    <Layout style={{ height: '100vh' }}>
      <Navbar />
      <Layout style={{ flex: 1, overflow: 'hidden' }}>
        <Sidebar onSelectReport={() => setView('report')} />
        <Content style={{ background: token.colorBgContainer, overflow: 'hidden' }}>
          {children}  {/* 或直接渲染 ChatView / RunningView / ReportView */}
        </Content>
        <AgentRuntime />
      </Layout>
    </Layout>
  )
}
```

**关键变更：**
- 所有 `#fff` → `token.colorBgContainer`
- 所有 `#f5f6f9` → `token.colorBgLayout`
- 所有 `#e8e8e8` → `token.colorBorderSecondary`
- 所有 `#1677ff` → `token.colorPrimary`
- 所有 `borderRadius` → `token.borderRadius`
- 所有 `fontSize` → `token.fontSize`

**Step 2: 提交**

```bash
git add frontend/src/components/layout/AppLayout.tsx frontend/src/components/layout/Sidebar.tsx frontend/src/App.tsx
git commit -m "refactor(frontend): three-column layout with Ant Design Layout + view state"
```

---

## Task 3: 重构导航栏 — 使用 Ant Design Menu

**当前问题：** Navbar 手写导航，激活态 / hover 态全部手写。

**方案：** 使用 `Menu` 组件替换手写导航。

**Files:**
- Modify: `frontend/src/components/layout/Navbar.tsx`

**Step 1: 替换为 Menu 组件**

```typescript
import { Layout, Menu, Button, Space, Typography, theme } from 'antd'
import { MessageOutlined, AppstoreOutlined, HistoryOutlined } from '@ant-design/icons'

const NAV_ITEMS = [
  { key: '/', label: '对话与生成', icon: <MessageOutlined /> },
  { key: '/templates', label: '模板中心', icon: <AppstoreOutlined /> },
  { key: '/history', label: '历史报告', icon: <HistoryOutlined /> },
]

export default function Navbar() {
  const navigate = useNavigate()
  const location = useLocation()

  return (
    <Layout.Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 20px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 32 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 18 }}>📊</span>
          <Typography.Text strong style={{ fontSize: 15, color: '#fff' }}>AI Report Workspace</Typography.Text>
        </div>
        <Menu
          mode="horizontal"
          selectedKeys={[location.pathname]}
          items={NAV_ITEMS.map(item => ({
            key: item.key, icon: item.icon, label: item.label,
            onClick: () => navigate(item.key),
          }))}
          style={{ background: 'transparent', borderBottom: 'none', flex: 1 }}
          theme="dark"
        />
      </div>
      <Space>
        <Button size="small" icon={<SaveOutlined />}>保存为模板</Button>
        <Button size="small" icon={<DownloadOutlined />}>导出</Button>
      </Space>
    </Layout.Header>
  )
}
```

**Step 2: 提交**

```bash
git add frontend/src/components/layout/Navbar.tsx
git commit -m "refactor(frontend): replace handwritten nav with Ant Design Menu"
```

---

## Task 4: 实现三态视图切换 (Chat / Running / Report)

**当前问题：** 中间区域就是 ChatPage，没有视图切换。

**方案：** 创建三个独立视图组件，通过 ViewContext 或 Zustand 状态控制切换。

**Files:**
- Create: `frontend/src/pages/ChatView.tsx` — 对话视图（气泡 + 需求确认 + 预览卡片）
- Create: `frontend/src/pages/RunningView.tsx` — 执行视图（Agent 步骤进度）
- Create: `frontend/src/pages/ReportView.tsx` — 报告视图（完整报表渲染）
- Create: `frontend/src/stores/viewStore.ts` — 视图状态管理
- Modify: `frontend/src/pages/ChatPage.tsx` — 精简为视图容器

**视图切换逻辑：**

```typescript
// viewStore.ts
import { create } from 'zustand'

type ViewMode = 'chat' | 'running' | 'report'

interface ViewStore {
  mode: ViewMode
  reportType: 'flat' | 'tree' | 'indent' | 'cross'
  setMode: (mode: ViewMode) => void
  setReportType: (type: 'flat' | 'tree' | 'indent' | 'cross') => void
}

export const useViewStore = create<ViewStore>((set) => ({
  mode: 'chat',
  reportType: 'flat',
  setMode: (mode) => set({ mode }),
  setReportType: (reportType) => set({ reportType }),
}))
```

**ChatView 核心结构：**

```
┌─ ChatView ─────────────────────────┐
│  messages[]                         │
│  ├─ 用户气泡                        │
│  ├─ AI 气泡（含需求确认卡片）        │
│  ├─ 用户气泡                        │
│  └─ AI 气泡（含预览卡片 + "查看报告")│
│                                     │
│  ┌─ InputArea ──────────────────┐  │
│  │  TextArea + Send Button      │  │
│  └──────────────────────────────┘  │
└─────────────────────────────────────┘
```

**预览卡片触发：** 生成完成后，对话气泡内显示摘要 KPI + 迷你图表 + 一句话洞察，底部有 `[查看完整报告 →]` 按钮 → 调用 `setMode('report')`

**ReportView 核心结构：**

```
┌─ ReportView ────────────────────────┐
│  Report Type Tabs                    │
│  ①平铺重复 | ②合并树形 | ③缩进层级 | ④交叉透视│
│                                     │
│  ┌─ ReportHeader ───────────────┐  │
│  │  标题 + 元信息 + 操作栏       │  │
│  │  [返回对话] [保存模板] [导出]  │  │
│  └──────────────────────────────┘  │
│                                     │
│  ┌─ KPI Grid ──────────────────┐  │
│  │  Card × 4 (useToken)        │  │
│  └──────────────────────────────┘  │
│                                     │
│  ┌─ Table (根据 reportType 切换) ─┐│
│  │  flat: 平铺表格                 ││
│  │  tree: rowspan 合并树           ││
│  │  indent: 缩进层级树             ││
│  │  cross: 交叉透视矩阵            ││
│  └──────────────────────────────┘  │
│                                     │
│  ┌─ ECharts Chart ─────────────┐  │
│  │  柱状图 / 折线图 / 饼图       │  │
│  └──────────────────────────────┘  │
│                                     │
│  ┌─ Insight Card ──────────────┐  │
│  │  AI 洞察文本                  │  │
│  └──────────────────────────────┘  │
└─────────────────────────────────────┘
```

**Step N: 提交**

```bash
git add frontend/src/pages/ChatView.tsx frontend/src/pages/RunningView.tsx frontend/src/pages/ReportView.tsx frontend/src/stores/viewStore.ts
git commit -m "feat(frontend): three-view mode (Chat / Running / Report) with view store"
```

---

## Task 5: 实现 4 种报表视觉样式组件

**当前问题：** Table 渲染只有一种样式，不支持切换。

**方案：** 创建 4 个独立的 Table 样式组件，通过 `reportType` 状态切换渲染。

**Files:**
- Create: `frontend/src/components/report/styles/FlatTable.tsx` — ① 平铺重复式
- Create: `frontend/src/components/report/styles/TreeTable.tsx` — ② 合并树形式（rowspan）
- Create: `frontend/src/components/report/styles/IndentTable.tsx` — ③ 缩进层级式
- Create: `frontend/src/components/report/styles/CrossTable.tsx` — ④ 交叉透视
- Create: `frontend/src/components/report/ReportTable.tsx` — 根据 reportType 分发

**Step 1: 创建 ReportTable 分发组件**

```typescript
// ReportTable.tsx
import { useViewStore } from '../../../stores/viewStore'
import FlatTable from './styles/FlatTable'
import TreeTable from './styles/TreeTable'
import IndentTable from './styles/IndentTable'
import CrossTable from './styles/CrossTable'

const TABLES = { flat: FlatTable, tree: TreeTable, indent: IndentTable, cross: CrossTable }

export default function ReportTable({ data }: { data: ReportTableData }) {
  const reportType = useViewStore((s) => s.reportType)
  const Component = TABLES[reportType]
  return <Component data={data} />
}
```

**Step 2: 实现 TreeTable（合并树形式 — 最复杂）**

关键：后端返回的数据需要包含层级信息（`level`、`rowspan`、`isGroupHeader`、`isSubtotal`），前端据此渲染 rowspan 合并。

```typescript
// TreeTable.tsx
// 数据格式假设：
// rows = [
//   { type: 'group', label: '生产部', rowspan: 4, level: 1 },
//   { type: 'data', cols: {员工:'张三', 项目:'A-001', 工时:168, 日期:'2025-07'}, level: 2 },
//   ...
//   { type: 'subtotal', label: '生产部小计', cols: {工时: 528}, level: 1 },
// ]

export default function TreeTable({ data }: { data: ReportTableData }) {
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
      <thead>
        <tr>{data.columns.map(col => <th key={col.key}>{col.label}</th>)}</tr>
      </thead>
      <tbody>
        {data.rows.map((row, i) => {
          if (row.type === 'group') {
            return <tr key={i}><td className="dept-name" rowSpan={row.rowspan}>{row.label}</td>...</tr>
          }
          // ...
        })}
      </tbody>
    </table>
  )
}
```

**Step 3: 提交**

```bash
git add frontend/src/components/report/styles/ frontend/src/components/report/ReportTable.tsx
git commit -m "feat(frontend): 4 report table visual styles (flat, tree, indent, cross)"
```

---

## Task 6: 重构 Agent Timeline — 使用 Ant Design Timeline

**当前问题：** AgentTimeline 手写时间线布局。

**方案：** 使用 Ant Design 的 `Timeline` 组件。

**Files:**
- Modify: `frontend/src/components/chat/AgentTimeline.tsx`

**Step 1: 替换为 Timeline 组件**

```typescript
import { Timeline, Typography } from 'antd'
import { CheckCircleFilled, LoadingOutlined, MinusCircleOutlined } from '@ant-design/icons'

const STATUS_ITEMS = {
  success: { color: 'green', dot: <CheckCircleFilled style={{ color: '#1677ff' }} /> },
  running: { color: 'blue', dot: <LoadingOutlined style={{ color: '#faad14' }} /> },
  pending: { color: 'gray', dot: <MinusCircleOutlined style={{ color: '#bbb' }} /> },
}

export default function AgentTimeline({ events, isStreaming }: Props) {
  const items = events.map((event) => ({
    color: STATUS_ITEMS[event.status]?.color || 'gray',
    dot: STATUS_ITEMS[event.status]?.dot,
    children: (
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Typography.Text>{event.nodeName}</Typography.Text>
        {event.duration && <Typography.Text type="secondary">{event.duration}</Typography.Text>}
      </div>
    ),
  }))

  return <Timeline items={items} />
}
```

**Step 2: 提交**

```bash
git add frontend/src/components/chat/AgentTimeline.tsx
git commit -m "refactor(frontend): replace handwritten timeline with Ant Design Timeline"
```

---

## Task 7: 实现报告渲染区块 — 全部使用 Ant Design Card

**当前问题：** ChartBlock、InsightBlock、KpiBlock 全部用 `div` + inline style 模拟卡片。

**方案：** 统一使用 `Card` 组件替代手写卡片样式。

**Files:**
- Modify: `frontend/src/components/report/blocks/ChartBlock.tsx`
- Modify: `frontend/src/components/report/blocks/InsightBlock.tsx`
- Modify: `frontend/src/components/report/blocks/KpiBlock.tsx`
- Modify: `frontend/src/components/report/blocks/MarkdownBlock.tsx`

**Step 1: 统一替换为 Card 组件**

以 ChartBlock 为例：

```typescript
import { Card, Typography, theme } from 'antd'
import ReactECharts from 'echarts-for-react'

export default function ChartBlock({ block }: Props) {
  const { token } = theme.useToken()

  return (
    <Card
      title={block.title || '图表'}
      size="small"
      styles={{ body: { padding: 16 } }}
    >
      <ReactECharts option={block.data} style={{ height: 280 }} opts={{ renderer: 'svg' }} />
    </Card>
  )
}
```

**Step 2: 提交**

```bash
git add frontend/src/components/report/blocks/
git commit -m "refactor(frontend): replace handwritten card styles with Ant Design Card component"
```

---

## Task 8: 实现保存模板功能

**当前问题：** "保存为模板"按钮 disabled。

**方案：** 实现保存模板弹窗，localStorage 持久化。

**Files:**
- Modify: `frontend/src/components/layout/Navbar.tsx`
- Modify: `frontend/src/stores/session.ts`
- Create: `frontend/src/types/template.ts`

**Step 1: 扩展 Store 支持模板**

```typescript
// session.ts 新增
export interface ReportTemplate {
  id: string
  name: string
  description: string
  query: string
  params: TemplateParams
  timestamp: number
}

// 新增方法
saveAsTemplate: (report: ReportEntry, name: string, desc: string) => void
deleteTemplate: (id: string) => void
```

**Step 2: 保存模板弹窗**

```typescript
// 使用 Ant Design Modal + Form
<Modal title="保存报告模板" open={open} onOk={handleSave} onCancel={handleClose}>
  <Form>
    <Form.Item label="模板名称"><Input /></Form.Item>
    <Form.Item label="描述"><Input /></Form.Item>
    <Form.Item label="参数配置">
      <Checkbox.Group options={['年份', '区域', '产品']} />
    </Form.Item>
  </Form>
</Modal>
```

**Step 3: 提交**

```bash
git add frontend/src/stores/session.ts frontend/src/components/layout/Navbar.tsx
git commit -m "feat(frontend): save-as-template dialog with Ant Design Modal + Form"
```

---

## Task 9: 实现历史报告持久化

**当前问题：** HistoryPage 只显示当前会话报告，刷新丢失。

**方案：** localStorage 持久化历史报告。

**Files:**
- Modify: `frontend/src/stores/session.ts`
- Modify: `frontend/src/pages/HistoryPage.tsx`

**Step 1: 扩展 Store 持久化历史**

```typescript
const HISTORY_KEY = 'ragent_history'

function loadHistory(): ReportEntry[] {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]') } catch { return [] }
}

function saveHistory(reports: ReportEntry[]) {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(reports.slice(-50)))
}
```

**Step 2: HistoryPage 使用 Ant Design List**

```typescript
import { List, Card, Tag, Empty, Typography } from 'antd'

export default function HistoryPage() {
  const reports = useSessionStore(s => s.reports)

  return (
    <div style={{ maxWidth: 800, margin: '0 auto', padding: 24 }}>
      <Typography.Title level={4}>📄 历史报告</Typography.Title>
      {reports.length === 0 ? (
        <Empty description="暂无历史报告" />
      ) : (
        <List
          dataSource={reports}
          renderItem={(item) => (
            <List.Item>
              <Card hoverable style={{ width: '100%' }}>
                <Card.Meta
                  title={item.query}
                  description={new Date(item.timestamp).toLocaleString('zh-CN')}
                />
              </Card>
            </List.Item>
          )}
        />
      )}
    </div>
  )
}
```

**Step 3: 提交**

```bash
git add frontend/src/stores/session.ts frontend/src/pages/HistoryPage.tsx
git commit -m "feat(frontend): persist report history to localStorage, Ant Design List"
```

---

## Task 10: 实现模板中心功能

**当前问题：** /templates 页面完全是空占位符。

**方案：** 实现完整的模板中心，基于 localStorage 持久化。

**Files:**
- Modify: `frontend/src/pages/TemplateCenter.tsx`

**Step 1: 模板中心页面**

```typescript
import { Card, Row, Col, Empty, Modal, Form, Input, Select, Button, Typography } from 'antd'
import { PlusOutlined, DeleteOutlined, PlayCircleOutlined } from '@ant-design/icons'

export default function TemplateCenter() {
  const templates = useSessionStore(s => s.templates)

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Typography.Title level={4}>📋 模板中心</Typography.Title>
        <Button type="primary" icon={<PlusOutlined />}>新建模板</Button>
      </div>
      {templates.length === 0 ? (
        <Empty description="暂无模板，保存报告时可创建模板" />
      ) : (
        <Row gutter={[16, 16]}>
          {templates.map(t => (
            <Col span={8} key={t.id}>
              <Card
                hoverable
                actions={[
                  <PlayCircleOutlined key="run" />,
                  <DeleteOutlined key="delete" />,
                ]}
              >
                <Card.Meta title={t.name} description={t.description} />
              </Card>
            </Col>
          ))}
        </Row>
      )}
    </div>
  )
}
```

**Step 2: 提交**

```bash
git add frontend/src/pages/TemplateCenter.tsx
git commit -m "feat(frontend): implement template center with Ant Design Card + Grid"
```

---

## Task 11: 实现导出功能

**当前问题：** "导出"按钮 disabled。

**方案：** 实现 HTML 导出 + 打印样式。

**Files:**
- Modify: `frontend/src/components/layout/Navbar.tsx`（或 ReportView 顶部操作栏）

**Step 1: 导出 HTML 功能**

```typescript
function exportReport(report: ReportEntry) {
  const html = generateReportHTML(report)  // 生成完整的 HTML 文档
  const blob = new Blob([html], { type: 'text/html;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `report-${report.id.slice(0, 8)}.html`
  a.click()
  URL.revokeObjectURL(url)
}
```

**Step 2: 提交**

```bash
git add frontend/src/components/layout/Navbar.tsx
git commit -m "feat(frontend): HTML report export with print-friendly styles"
```

---

## Task 12: 清理 — 删除未使用组件，验证 Design Token 覆盖

**当前问题：** 可能存在未使用的组件（如 QueryBar.tsx），大量硬编码颜色值。

**方案：** 全局搜索硬编码颜色值，确保全部被 Design Token 替代。

**Files:**
- Check: `frontend/src/components/chat/QueryBar.tsx` — 如果未被引用则删除

**Step 1: 查找未使用的组件**

```bash
grep -r "QueryBar" frontend/src/ --include="*.tsx" --include="*.ts"
```

**Step 2: 全文搜索硬编码颜色**

```bash
grep -rn "'#[0-9a-fA-F]\{6\}'" frontend/src/ --include="*.tsx" --include="*.ts"
```

确保所有颜色值都已被 `token.xxx` 替代。

**Step 3: 提交**

```bash
git add frontend/src/
git commit -m "chore(frontend): remove unused components, verify Design Token coverage"
```

---

## 执行顺序

```
Task 1:  Design Token 体系（基础）
Task 2:  三栏布局（AppLayout + Sidebar）
Task 3:  Navbar 重构（Menu 组件）
Task 4:  三态视图机（Chat/Running/Report）
Task 5:  4 种报表视觉样式
Task 6:  Agent Timeline 重构
Task 7:  Report 区块卡片化
Task 8:  保存模板功能
Task 9:  历史报告持久化
Task 10: 模板中心
Task 11: 导出功能
Task 12: 清理
```

**预估工作量：** Task 4 + Task 5 最大（视图切换 + 4 种表格样式），约 4-6 小时。其他每个 Task 0.5-1 小时。

---

## 验收标准

1. 所有页面使用 Ant Design Layout 组件，无手写 `div` 布局
2. 所有颜色/间距/字号通过 `useToken()` 获取，无硬编码值
3. 中间区域三态视图切换流畅（Chat → Running → Report）
4. 4 种报表视觉样式基于同一份数据可切换
5. 对话内预览卡片带 KPI + 迷你图表 + 查看完整报告按钮
6. 模板中心可创建/删除模板
7. 历史报告页面刷新后保留数据
8. "保存为模板"和"导出"按钮功能正常
9. Agent Runtime 面板使用 Ant Design Timeline 组件