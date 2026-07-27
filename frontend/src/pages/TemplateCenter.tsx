import { useState } from 'react'
import { Form } from 'antd'
import { AppstoreOutlined, PlusOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'

import { Text, Title } from '../components/atelier/Typography'
import Card from '../components/atelier/Card'
import Empty from '../components/atelier/Empty'
import Modal from '../components/atelier/Modal'
import Button from '../components/atelier/Button'
import TextField from '../components/atelier/TextField'
import TextArea from '../components/atelier/TextArea'
import { useToast } from '../components/atelier/useToast'
import { useSessionStore } from '../stores/session'

export default function TemplateCenter() {
  const toast = useToast()
  const { templates, deleteTemplate, saveAsTemplate, setTemplateParams, setViewMode } = useSessionStore()
  const navigate = useNavigate()
  const [modalOpen, setModalOpen] = useState(false)
  const [form] = Form.useForm()

  const handleCreate = () => {
    form.validateFields().then((values) => {
      saveAsTemplate(values.name, values.description)
      toast.success('模板创建成功')
      setModalOpen(false)
      form.resetFields()
    })
  }

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
              <AppstoreOutlined style={{ marginRight: 8 }} />
              模板中心
            </Title>
            <Text type="secondary" style={{ fontSize: 13 }}>
              保存和管理报告模板，快速复用分析逻辑
            </Text>
          </div>
          <Button variant="primary" onClick={() => setModalOpen(true)}>
            <PlusOutlined /> 新建模板
          </Button>
        </div>

        {templates.length === 0 ? (
          <Empty
            icon={<AppstoreOutlined style={{ fontSize: 48, color: 'var(--muted)' }} />}
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
          <Form form={form} layout="vertical">
            <Form.Item name="name" label="模板名称" rules={[{ required: true, message: '请输入模板名称' }]}>
              <TextField placeholder="例如：月度销售分析" />
            </Form.Item>
            <Form.Item name="description" label="描述">
              <TextArea rows={2} placeholder="可选：模板用途说明" style={{ width: '100%' }} />
            </Form.Item>
          </Form>
        </Modal>
      </div>
    </div>
  )
}
