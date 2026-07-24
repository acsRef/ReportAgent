import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Form, Input, Typography, message } from 'antd'
import { useAuthStore } from '../stores/authStore'
import { loginAPI } from '../api/api'
import '../styles/global.css'

/**
 * Login page — editorial two-column layout. Left: brand / value prop on a
 * warm paper canvas. Right: a single centered form. Narrow viewport
 * collapses to a single column (handled by the workbench-shell grid).
 *
 * Uses the existing `loginAPI` from `api/api.ts` and the `authStore` so
 * that subsequent pages can read the JWT.
 */
export default function LoginPage() {
  const navigate = useNavigate()
  const setAuth = useAuthStore((s) => s.setAuth)
  const [submitting, setSubmitting] = useState(false)
  const [form] = Form.useForm()

  async function onFinish(values: { username: string; password: string }) {
    setSubmitting(true)
    try {
      const res = await loginAPI(values.username, values.password)
      setAuth(res.access_token, res.user_id, res.username)
      message.success('登录成功')
      navigate('/')
    } catch (err) {
      message.error(`登录失败：${(err as Error).message ?? '未知错误'}`)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        background: 'var(--canvas)',
        display: 'grid',
        gridTemplateColumns: '1.1fr 1fr',
      }}
    >
      {/* Left — editorial brand */}
      <section
        style={{
          padding: '64px 56px',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          gap: 24,
        }}
      >
        <Typography.Title
          level={1}
          style={{
            fontFamily: 'var(--font-display)',
            fontSize: 40,
            color: 'var(--ink)',
            margin: 0,
            lineHeight: 1.15,
            fontWeight: 700,
          }}
        >
          数据，先问再查
          <br />
          报告，按需演化
        </Typography.Title>
        <Typography.Paragraph
          style={{
            fontFamily: 'var(--font-ui)',
            color: 'var(--ink-2)',
            fontSize: 14,
            maxWidth: 460,
            margin: 0,
            lineHeight: 1.7,
          }}
        >
          ReportAgent 让你用中文提问，自动生成 SQL、报告与洞察。
          每一份分析都可追溯、可调整、可重做。
        </Typography.Paragraph>
        <Typography.Text
          style={{
            color: 'var(--muted)',
            fontSize: 11,
            letterSpacing: 1.4,
            textTransform: 'uppercase',
            fontWeight: 700,
          }}
        >
          默认账号 · admin / admin123
        </Typography.Text>
      </section>

      {/* Right — form card on paper */}
      <section
        style={{
          padding: '64px 48px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <div
          style={{
            width: '100%',
            maxWidth: 380,
            background: 'var(--paper)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-l)',
            boxShadow: 'var(--shadow-card)',
            padding: '40px 32px',
          }}
        >
          <Typography.Title
            level={3}
            style={{
              fontFamily: 'var(--font-display)',
              fontSize: 22,
              color: 'var(--ink)',
              marginBottom: 24,
            }}
          >
            登录
          </Typography.Title>
          <Form form={form} layout="vertical" onFinish={onFinish} initialValues={{ username: 'admin', password: 'admin123' }}>
            <Form.Item
              label={<span style={{ color: 'var(--ink-2)', fontWeight: 600 }}>用户名</span>}
              name="username"
              rules={[{ required: true, message: '请输入用户名' }]}
            >
              <Input placeholder="admin" autoComplete="username" />
            </Form.Item>
            <Form.Item
              label={<span style={{ color: 'var(--ink-2)', fontWeight: 600 }}>密码</span>}
              name="password"
              rules={[{ required: true, message: '请输入密码' }]}
            >
              <Input.Password placeholder="••••••••" autoComplete="current-password" />
            </Form.Item>
            <Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                block
                loading={submitting}
                style={{ height: 38, fontWeight: 500, marginTop: 8 }}
              >
                进入工作台
              </Button>
            </Form.Item>
          </Form>
        </div>
      </section>
    </div>
  )
}
