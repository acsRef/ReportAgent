import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTemplateStore } from '../stores/templateStore'
import { useAnalysisStore } from '../stores/analysisStore'
import type { TemplateRow } from '../api/templatesClient'
import type { RequirementCard as RC } from '../types/requirement'
import { useToast } from '../components/atelier/useToast'
import Button from '../components/atelier/Button'
import Empty from '../components/atelier/Empty'
import Modal from '../components/atelier/Modal'
import Popconfirm from '../components/atelier/Popconfirm'
import Spinner from '../components/atelier/Spinner'
import Tag from '../components/atelier/Tag'
import TextField from '../components/atelier/TextField'
import TextArea from '../components/atelier/TextArea'
import { Text, Title, Paragraph } from '../components/atelier/Typography'
import { IconArrowLeft, IconTrash, IconPlus } from '../components/ui/Icons'
import '../styles/global.css'
import '../styles/observability.css'

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

const sectionLabelStyle: React.CSSProperties = {
  fontSize: 10,
  letterSpacing: 1.4,
  color: 'var(--muted)',
  textTransform: 'uppercase',
  fontWeight: 700,
}

export default function TemplateLibraryPage() {
  const navigate = useNavigate()
  const toast = useToast()
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
  const [newName, setNewName] = useState('')
  const [newDescription, setNewDescription] = useState('')
  const [nameError, setNameError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    void refresh()
    detectLegacy()
  }, [refresh, detectLegacy])

  function openCreate() {
    setNewName('')
    setNewDescription('')
    setNameError(null)
    setCreateOpen(true)
  }

  async function handleCreateTemplate() {
    const name = newName.trim()
    if (!name) {
      setNameError('请输入模板名称')
      return
    }
    if (name.length > 128) {
      setNameError('名称不能超过 128 字符')
      return
    }
    setNameError(null)
    const payload: RC = analysisRequirement ?? buildMinimalRequirement()
    setCreating(true)
    try {
      await create(name, newDescription, payload)
      toast.success(`已创建模板「${name}」`)
      setCreateOpen(false)
      setNewName('')
      setNewDescription('')
    } catch (err) {
      toast.error(`创建失败：${String(err).slice(0, 200)}`)
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
      toast.success('已删除')
      if (selected?.id === t.id) setSelected(null)
    } catch (err) {
      toast.error(`删除失败：${String(err).slice(0, 100)}`)
    }
  }

  async function handleMigrate() {
    const res = await migrate()
    toast.success(`已导入 ${res.imported} 个，${res.skipped} 个跳过`)
  }

  function handleUseTemplate(t: TemplateRow) {
    const payload = t.requirement_payload as RC | undefined
    if (payload?.id) {
      dispatch({ type: 'requirement/received', requirement: payload })
      toast.success(`已载入模板「${t.name}」，回工作台确认执行`)
    }
    navigate('/')
  }

  return (
    <div style={{ minHeight: '100vh', background: 'var(--canvas)', display: 'flex', flexDirection: 'column' }}>
      <header
        style={{
          background: 'var(--ink)',
          color: 'var(--on-ink)',
          padding: '0 22px',
          display: 'flex',
          alignItems: 'center',
          gap: 18,
          height: 48,
        }}
      >
        <Button
          variant="quiet"
          onClick={() => navigate('/')}
          style={{ color: 'var(--on-ink)' }}
        >
          <IconArrowLeft /> 返回工作台
        </Button>
        <Title
          level={4}
          style={{ color: 'var(--on-ink)', margin: 0, fontFamily: 'var(--font-display)', fontSize: 16 }}
        >
          模板中心
        </Title>
        <div style={{ flex: 1 }} />
        <Text style={{ color: 'var(--on-ink-3)', fontSize: 11 }}>
          {templates.length} 个模板
        </Text>
      </header>
      <main
        style={{
          padding: 'var(--sp-xl)',
          display: 'grid',
          gridTemplateColumns: '240px 1fr 320px',
          gap: 'var(--sp-l)',
          flex: 1,
        }}
      >
        <aside
          className="obs-fade"
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
          <Text style={sectionLabelStyle}>搜索</Text>
          <TextField
            placeholder="按名称搜索"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
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
                <Button variant="primary" size="sm" onClick={handleMigrate}>
                  导入
                </Button>
                <Button variant="default" size="sm" onClick={dismiss}>
                  忽略
                </Button>
              </div>
            </div>
          )}
        </aside>

        <section
          className="obs-fade"
          style={{
            background: 'var(--paper)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-m)',
            padding: 'var(--sp-l)',
            minHeight: 480,
            animationDelay: '60ms',
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
            <Text style={sectionLabelStyle}>模板列表</Text>
            <Button variant="primary" size="sm" onClick={openCreate}>
              <IconPlus /> 新建模板
            </Button>
          </div>
          {loading ? (
            <div style={{ textAlign: 'center', padding: 24 }}>
              <Spinner />
            </div>
          ) : error ? (
            <Empty description={error} />
          ) : filtered.length === 0 ? (
            <Empty
              description={
                <Text style={{ color: 'var(--muted)' }}>
                  {templates.length === 0
                    ? '还没有模板。在工作台确认一张需求卡后会出现在这里。'
                    : '没有匹配项'}
                </Text>
              }
            />
          ) : (
            <div>
              {filtered.map((t) => {
                const isActive = selected?.id === t.id
                return (
                  <div
                    key={t.id}
                    className="obs-lift"
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
                      <Tag tone="teal">v{t.requirement_payload?.version ?? '?'}</Tag>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </section>

        <aside
          className="obs-fade"
          style={{
            background: 'var(--paper)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-m)',
            padding: 'var(--sp-l)',
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
            animationDelay: '120ms',
          }}
        >
          {selected ? (
            <>
              <Text style={sectionLabelStyle}>预览</Text>
              <Title
                level={4}
                style={{
                  fontFamily: 'var(--font-display)',
                  color: 'var(--ink)',
                  margin: 0,
                  fontSize: 18,
                }}
              >
                {selected.name}
              </Title>
              <Paragraph style={{ color: 'var(--ink-2)', fontSize: 12, margin: 0 }}>
                {selected.description || '（无描述）'}
              </Paragraph>
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
                  variant="primary"
                  onClick={() => handleUseTemplate(selected)}
                  block
                >
                  <IconPlus /> 使用此模板
                </Button>
                <Popconfirm
                  title="确定删除？"
                  onConfirm={() => handleDelete(selected)}
                  okText="删除"
                  cancelText="取消"
                >
                  <Button variant="danger">
                    <IconTrash />
                  </Button>
                </Popconfirm>
              </div>
            </>
          ) : (
            <Empty
              description={
                <Text style={{ color: 'var(--muted)' }}>选择左侧模板以预览</Text>
              }
            />
          )}
        </aside>
      </main>

      <Modal
        title="新建模板"
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onOk={handleCreateTemplate}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
      >
        <div style={{ display: 'grid', gap: 14 }}>
          <div>
            <label htmlFor="tpl-name" style={{ color: 'var(--ink-2)', fontWeight: 600, fontSize: 13 }}>
              模板名称
            </label>
            <TextField
              id="tpl-name"
              placeholder="例如：华东月销售分析"
              autoFocus
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
            {nameError && (
              <span role="alert" style={{ color: 'var(--red)', fontSize: 12, marginTop: 4, display: 'block' }}>
                {nameError}
              </span>
            )}
          </div>
          <div>
            <label htmlFor="tpl-description" style={{ color: 'var(--ink-2)', fontWeight: 600, fontSize: 13 }}>
              描述
            </label>
            <TextArea
              id="tpl-description"
              placeholder="一句话说明这个模板适用什么场景"
              value={newDescription}
              onChange={(e) => setNewDescription(e.target.value)}
            />
          </div>
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
            <Text style={{ color: 'var(--muted)', fontSize: 11 }}>
              requirement_payload 来源：
            </Text>
            <div style={{ marginTop: 4 }}>
              {analysisRequirement ? (
                <Tag tone="teal">
                  当前工作台需求卡 v{analysisRequirement.version} · {analysisRequirement.status}
                </Tag>
              ) : (
                <Text style={{ color: 'var(--faint)' }}>
                  工作台暂无需求卡，将使用最小可用的占位卡
                </Text>
              )}
            </div>
          </div>
        </div>
      </Modal>
    </div>
  )
}
