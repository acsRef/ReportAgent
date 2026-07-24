/**
 * Template Library — PG-backed CRUD via /api/v1/templates.
 *
 * Layout (per docs/ui-style-guide.md):
 *  - Left: filters (search)
 *  - Center: template cards (click → preview)
 *  - Right: preview + delete button
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  App,
  Button,
  Empty,
  Form,
  Input,
  Layout,
  List,
  Modal,
  Popconfirm,
  Spin,
  Tag,
  Typography,
} from 'antd'
import { ArrowLeftOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { useTemplateStore } from '../stores/templateStore'
import { useAnalysisStore } from '../stores/analysisStore'
import type { TemplateRow } from '../api/templatesClient'
import type { RequirementCard as RC } from '../types/requirement'
import '../styles/global.css'

/** Build a minimal valid `RequirementCard` for cases where the user
 * opens the modal without first having analysed anything. The card is
 * in `status: "missing"` so the server will accept it (Pydantic
 * validator: missing ↔ missing_fields non-empty). */
function buildMinimalRequirement(): RC {
  return {
    id: `tpl-minimal-${Date.now()}`,
    version: 1,
    status: 'missing',
    summary: '新建模板',
    target_metrics: [],
    time_range: null,
    scope: [],
    dimensions: [],
    analysis_methods: [],
    expected_blocks: [],
    missing_fields: [
      { key: 'time_range', label: '时间范围', kind: 'single', options: [], selected_value: null },
      { key: 'scope', label: '范围', kind: 'multiple', options: [], selected_value: null },
      { key: 'metric', label: '指标', kind: 'multiple', options: [], selected_value: null },
      { key: 'granularity', label: '粒度', kind: 'single', options: [], selected_value: null },
      { key: 'comparison', label: '对比', kind: 'single', options: [], selected_value: null },
    ],
    assumptions: [],
    confidence: 0,
    confirmed_at: null,
  }
}

export default function TemplateLibraryPage() {
  const navigate = useNavigate()
  const { message } = App.useApp()
  const templates = useTemplateStore((s) => s.templates)
  const loading = useTemplateStore((s) => s.loading)
  const error = useTemplateStore((s) => s.error)
  const refresh = useTemplateStore((s) => s.refresh)
  const create = useTemplateStore((s) => s.create)
  const remove = useTemplateStore((s) => s.remove)
  const detectLegacy = useTemplateStore((s) => s.detectLegacy)
  const pendingMigration = useTemplateStore((s) => s.pendingMigration)
  const migrate = useTemplateStore((s) => s.migrateFromLocalStorage)
  const dismiss = useTemplateStore((s) => s.dismissMigration)

  const analysisRequirement = useAnalysisStore((s) => s.requirement)
  const dispatch = useAnalysisStore((s) => s.dispatch)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<TemplateRow | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm] = Form.useForm<{ name: string; description?: string }>()
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    void refresh()
    detectLegacy()
  }, [refresh, detectLegacy])

  async function handleCreateTemplate() {
    try {
      const values = await createForm.validateFields()
      // Build the requirement_payload: prefer the current workbench
      // requirement; otherwise fall back to a minimal valid card so the
      // server-side Pydantic validator accepts it.
      const payload: RC = analysisRequirement ?? buildMinimalRequirement()
      setCreating(true)
      await create(values.name, values.description ?? '', payload)
      message.success(`已创建模板「${values.name}」`)
      setCreateOpen(false)
      createForm.resetFields()
    } catch (err) {
      // AntD Form validation throws a special object; ignore those.
      if (err && (err as any).errorFields) return
      message.error(`创建失败：${String(err).slice(0, 200)}`)
    } finally {
      setCreating(false)
    }
  }

  const filtered = templates.filter((t) =>
    query.trim() === ''
      ? true
      : (t.name + ' ' + (t.description ?? '')).toLowerCase().includes(query.toLowerCase()),
  )

  async function handleDelete(t: TemplateRow) {
    try {
      await remove(t.id)
      message.success('已删除')
      if (selected?.id === t.id) setSelected(null)
    } catch (err) {
      message.error(`删除失败：${String(err).slice(0, 100)}`)
    }
  }

  async function handleMigrate() {
    const res = await migrate()
    message.success(`已导入 ${res.imported} 个，${res.skipped} 个跳过`)
  }

  function handleUseTemplate(t: TemplateRow) {
    // Inject the template as the current requirement and route back to
    // the workbench, where the user can hit confirm.
    const payload = t.requirement_payload as any
    if (payload?.id) {
      dispatch({ type: 'requirement/received', requirement: payload })
      message.success(`已载入模板「${t.name}」，回工作台确认执行`)
    }
    navigate('/')
  }

  return (
    <Layout style={{ minHeight: '100vh', background: 'var(--canvas)' }}>
      <Layout.Header
        style={{
          background: 'var(--ink)',
          color: '#FFFFFF',
          padding: '0 22px',
          display: 'flex',
          alignItems: 'center',
          gap: 18,
        }}
      >
        <Button
          type="text"
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate('/')}
          style={{ color: '#FFFFFF' }}
        >
          返回工作台
        </Button>
        <Typography.Title
          level={4}
          style={{ color: '#FFFFFF', margin: 0, fontFamily: 'var(--font-display)' }}
        >
          模板中心
        </Typography.Title>
        <div style={{ flex: 1 }} />
        <Typography.Text style={{ color: 'rgba(255,255,255,.65)', fontSize: 11 }}>
          {templates.length} 个模板
        </Typography.Text>
      </Layout.Header>
      <Layout.Content
        style={{
          padding: 'var(--sp-xl)',
          display: 'grid',
          gridTemplateColumns: '240px 1fr 320px',
          gap: 'var(--sp-l)',
        }}
      >
        {/* Left: search + migration banner */}
        <aside
          style={{
            background: 'var(--paper)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-m)',
            padding: 'var(--sp-l)',
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}
        >
          <Typography.Text
            style={{
              fontSize: 10,
              letterSpacing: 1.4,
              color: 'var(--muted)',
              textTransform: 'uppercase',
              fontWeight: 700,
            }}
          >
            搜索
          </Typography.Text>
          <Input.Search
            placeholder="按名称搜索"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            allowClear
          />

          {pendingMigration && pendingMigration.length > 0 && (
            <div
              style={{
                background: 'var(--amber-soft)',
                border: '1px solid var(--amber)',
                borderRadius: 6,
                padding: 10,
                fontSize: 12,
                color: 'var(--ink-2)',
              }}
            >
              <div style={{ fontWeight: 600, marginBottom: 4 }}>
                检测到 {pendingMigration.length} 个旧模板
              </div>
              <div style={{ marginBottom: 8 }}>
                旧的 localStorage 模板可以导入 PG。继续？
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <Button type="primary" size="small" onClick={handleMigrate}>
                  导入
                </Button>
                <Button size="small" onClick={dismiss}>
                  忽略
                </Button>
              </div>
            </div>
          )}
        </aside>

        {/* Center: list */}
        <section
          style={{
            background: 'var(--paper)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-m)',
            padding: 'var(--sp-l)',
            minHeight: 480,
          }}
        >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: 12,
            }}
          >
            <Typography.Text
              style={{
                fontSize: 10,
                letterSpacing: 1.4,
                color: 'var(--muted)',
                textTransform: 'uppercase',
                fontWeight: 700,
              }}
            >
              模板列表
            </Typography.Text>
            <Button
              type="primary"
              size="small"
              icon={<PlusOutlined />}
              onClick={() => {
                createForm.resetFields()
                setCreateOpen(true)
              }}
            >
              新建模板
            </Button>
          </div>
          {loading ? (
            <div style={{ textAlign: 'center', padding: 24 }}>
              <Spin />
            </div>
          ) : error ? (
            <Empty description={error} />
          ) : filtered.length === 0 ? (
            <Empty
              description={
                <Typography.Text style={{ color: 'var(--muted)' }}>
                  {templates.length === 0
                    ? '还没有模板。在工作台确认一张需求卡后会出现在这里。'
                    : '没有匹配项'}
                </Typography.Text>
              }
            />
          ) : (
            <List
              dataSource={filtered}
              renderItem={(t) => {
                const isActive = selected?.id === t.id
                return (
                  <List.Item
                    onClick={() => setSelected(t)}
                    style={{
                      padding: '12px 14px',
                      borderRadius: 6,
                      cursor: 'pointer',
                      background: isActive ? 'var(--teal-pale)' : 'transparent',
                      border: isActive ? '1px solid var(--teal)' : '1px solid var(--line)',
                      marginBottom: 8,
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 600, color: 'var(--ink)' }}>{t.name}</div>
                        <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                          {t.description || '（无描述）'}
                        </div>
                      </div>
                      <Tag color="teal">v{t.requirement_payload?.version ?? '?'}</Tag>
                    </div>
                  </List.Item>
                )
              }}
            />
          )}
        </section>

        {/* Right: preview */}
        <aside
          style={{
            background: 'var(--paper)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-m)',
            padding: 'var(--sp-l)',
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}
        >
          {selected ? (
            <>
              <Typography.Text
                style={{
                  fontSize: 10,
                  letterSpacing: 1.4,
                  color: 'var(--muted)',
                  textTransform: 'uppercase',
                  fontWeight: 700,
                }}
              >
                预览
              </Typography.Text>
              <Typography.Title
                level={4}
                style={{
                  fontFamily: 'var(--font-display)',
                  color: 'var(--ink)',
                  margin: 0,
                  fontSize: 18,
                }}
              >
                {selected.name}
              </Typography.Title>
              <Typography.Paragraph
                style={{ color: 'var(--ink-2)', fontSize: 12, margin: 0 }}
              >
                {selected.description || '（无描述）'}
              </Typography.Paragraph>
              <pre
                style={{
                  background: 'var(--canvas)',
                  border: '1px solid var(--line)',
                  borderRadius: 6,
                  padding: 10,
                  fontSize: 11,
                  color: 'var(--ink-2)',
                  maxHeight: 240,
                  overflow: 'auto',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  fontFamily: 'var(--font-mono)',
                }}
              >
                {JSON.stringify(selected.requirement_payload, null, 2)}
              </pre>
              <div style={{ display: 'flex', gap: 8 }}>
                <Button
                  type="primary"
                  icon={<PlusOutlined />}
                  onClick={() => handleUseTemplate(selected)}
                  block
                >
                  使用此模板
                </Button>
                <Popconfirm
                  title="确定删除？"
                  onConfirm={() => handleDelete(selected)}
                  okText="删除"
                  cancelText="取消"
                >
                  <Button danger icon={<DeleteOutlined />} />
                </Popconfirm>
              </div>
            </>
          ) : (
            <Empty
              description={
                <Typography.Text style={{ color: 'var(--muted)' }}>
                  选择左侧模板以预览
                </Typography.Text>
              }
            />
          )}
        </aside>
      </Layout.Content>

      <Modal
        title="新建模板"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={handleCreateTemplate}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
        destroyOnClose
      >
        <Form
          form={createForm}
          layout="vertical"
          initialValues={{ description: '' }}
        >
          <Form.Item
            label="模板名称"
            name="name"
            rules={[
              { required: true, message: '请输入模板名称' },
              { max: 128, message: '名称不能超过 128 字符' },
            ]}
          >
            <Input placeholder="例如：华东月销售分析" autoFocus />
          </Form.Item>
          <Form.Item label="描述" name="description">
            <Input.TextArea
              placeholder="一句话说明这个模板适用什么场景"
              autoSize={{ minRows: 2, maxRows: 4 }}
            />
          </Form.Item>
          <div
            style={{
              background: 'var(--canvas)',
              border: '1px solid var(--line)',
              borderRadius: 6,
              padding: 10,
              fontSize: 12,
              color: 'var(--ink-2)',
            }}
          >
            <Typography.Text style={{ color: 'var(--muted)', fontSize: 11 }}>
              requirement_payload 来源：
            </Typography.Text>
            <div style={{ marginTop: 4 }}>
              {analysisRequirement ? (
                <Tag color="teal">
                  当前工作台需求卡 v{analysisRequirement.version} · {analysisRequirement.status}
                </Tag>
              ) : (
                <Typography.Text style={{ color: 'var(--faint)' }}>
                  工作台暂无需求卡，将使用最小可用的占位卡
                </Typography.Text>
              )}
            </div>
          </div>
        </Form>
      </Modal>
    </Layout>
  )
}
