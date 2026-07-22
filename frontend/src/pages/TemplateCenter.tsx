import { Typography, Card, Row, Col, Empty, Modal, Form, Input, Button, message } from 'antd'
import { AppstoreOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useSessionStore } from '../stores/session'

const { Text, Title } = Typography

export default function TemplateCenter() {
  const { templates, deleteTemplate, saveAsTemplate, setTemplateParams, setViewMode } = useSessionStore()
  const navigate = useNavigate()
  const [modalOpen, setModalOpen] = useState(false)
  const [form] = Form.useForm()

  const handleCreate = () => {
    form.validateFields().then((values) => {
      saveAsTemplate(values.name, values.description)
      message.success('模板创建成功')
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
    <div style={{ height: '100%', overflow: 'auto', background: '#f5f6f9', padding: 32 }}>
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
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
            新建模板
          </Button>
        </div>

        {templates.length === 0 ? (
          <Empty
            image={<AppstoreOutlined style={{ fontSize: 48, color: '#bbb' }} />}
            description="暂无模板，在对话页面可保存当前报告为模板"
            style={{ marginTop: 60 }}
          />
        ) : (
          <Row gutter={[16, 16]}>
            {templates.map((t) => (
              <Col span={8} key={t.id}>
                <Card
                  hoverable
                  actions={[
                    <Button type="link" size="small" key="use" onClick={() => handleUse(t)}>
                      使用模板
                    </Button>,
                    <DeleteOutlined key="delete" onClick={() => { deleteTemplate(t.id); message.success('已删除') }} />,
                  ]}
                >
                  <Card.Meta
                    title={<Text strong>{t.name}</Text>}
                    description={
                      <div>
                        <Text type="secondary" style={{ fontSize: 12 }}>{t.description || '无描述'}</Text>
                        <div style={{ marginTop: 8, fontSize: 11, color: '#999' }}>
                          创建于 {new Date(t.createdAt).toLocaleDateString('zh-CN')}
                        </div>
                      </div>
                    }
                  />
                </Card>
              </Col>
            ))}
          </Row>
        )}

        <Modal title="新建模板" open={modalOpen} onOk={handleCreate} onCancel={() => setModalOpen(false)} okText="创建" cancelText="取消">
          <Form form={form} layout="vertical">
            <Form.Item name="name" label="模板名称" rules={[{ required: true, message: '请输入模板名称' }]}>
              <Input placeholder="例如：月度销售分析" />
            </Form.Item>
            <Form.Item name="description" label="描述">
              <Input.TextArea rows={2} placeholder="可选：模板用途说明" />
            </Form.Item>
          </Form>
        </Modal>
      </div>
    </div>
  )
}
