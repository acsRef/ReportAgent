import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Text, Title } from '../../components/atelier/Typography'
import Card from '../../components/atelier/Card'
import Empty from '../../components/atelier/Empty'
import Modal from '../../components/atelier/Modal'
import Button from '../../components/atelier/Button'
import TextField from '../../components/atelier/TextField'
import TextArea from '../../components/atelier/TextArea'
import { useToast } from '../../components/atelier/useToast'
import { IconTemplate, IconPlus } from '../../components/ui/Icons'
import { useSessionStore } from '../stores/session'

export default function TemplateCenter() {
  const toast = useToast()
  const { templates, deleteTemplate, saveAsTemplate, setTemplateParams, setViewMode } = useSessionStore()
  const navigate = useNavigate()
  const [modalOpen, setModalOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [errors, setErrors] = useState<{ name?: string }>({})

  const handleCreate = () => {
    const next: { name?: string } = {}
    if (!name.trim()) next.name = '请输入模板名称'
    setErrors(next)
    if (next.name) return
    saveAsTemplate(name, description)
    toast.success('模板创建成功')
    setModalOpen(false)
    setName('')
    setDescription('')
    setErrors({})
  }

  const labelStyle = { color: 'var(--ink-2)', fontWeight: 600, fontSize: 13 }
  const errorStyle = { color: 'var(--red)', fontSize: 12, marginTop: 4, display: 'block' }

  const handleUse = (tmpl: typeof templates[0]) => {
    setTemplateParams(tmpl.params)
    setViewMode('chat')
    navigate('/')
  }

  return (
    <div style={{ height: '100%', overflow: 'auto', background: 'var(--canvas)', padding: 32 }}>
      <div style={{ maxWidth: 1000, margin: '0 auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
          <div>
            <Title level={4} style={{ margin: 0 }}>
              <IconTemplate style={{ marginRight: 8 }} />
              模板中心
            </Title>
            <Text type="secondary" style={{ fontSize: 13 }}>
              保存和管理报告模板，快速复用分析逻辑
            </Text>
          </div>
          <Button variant="primary" onClick={() => setModalOpen(true)}>
            <IconPlus /> 新建模板
          </Button>
        </div>

        {templates.length === 0 ? (
          <Empty
            icon={<IconTemplate style={{ width: 48, height: 48, color: 'var(--muted)' }} />}
            description="暂无模板，在对话页面可保存当前报告为模板"
            style={{ marginTop: 60 }}
          />
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 16 }}>
            {templates.map((t) => (
              <Card
                key={t.id}
                hoverable
                bodyStyle={{ flexDirection: 'column', alignItems: 'stretch', gap: 4 }}
              >
                <Text strong>{t.name}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>{t.description || '无描述'}</Text>
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                  创建于 {new Date(t.createdAt).toLocaleDateString('zh-CN')}
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                  <Button variant="default" size="sm" onClick={() => handleUse(t)}>
                    使用模板
                  </Button>
                  <Button variant="quiet" size="sm" onClick={() => { deleteTemplate(t.id); toast.success('已删除') }}>
                    删除
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        )}

        <Modal title="新建模板" open={modalOpen} onOk={handleCreate} onClose={() => setModalOpen(false)} okText="创建" cancelText="取消">
          <form onSubmit={(event) => { event.preventDefault(); handleCreate() }} style={{ display: 'grid', gap: 16 }}>
            <div>
              <label htmlFor="template-create-name" style={labelStyle}>
                模板名称
              </label>
              <TextField
                id="template-create-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="例如：月度销售分析"
                style={{ width: '100%' }}
              />
              {errors.name && (
                <span role="alert" style={errorStyle}>
                  {errors.name}
                </span>
              )}
            </div>
            <div>
              <label htmlFor="template-create-description" style={labelStyle}>
                描述
              </label>
              <TextArea
                id="template-create-description"
                rows={2}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="可选：模板用途说明"
                style={{ width: '100%' }}
              />
            </div>
          </form>
        </Modal>
      </div>
    </div>
  )
}
