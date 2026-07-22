import { useState } from 'react'
import { Typography, Input, Button, Form, message } from 'antd'
import { UserOutlined, LockOutlined } from '@ant-design/icons'
import { useNavigate, Navigate } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'
import { loginAPI } from '../api/api'

const { Text } = Typography

export default function LoginPage() {
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()
  const { setAuth, isAuthenticated } = useAuthStore()

  if (isAuthenticated()) {
    return <Navigate to="/" replace />
  }

  const handleSubmit = async (values: { username: string; password: string }) => {
    setLoading(true)
    try {
      const res = await loginAPI(values.username, values.password)
      setAuth(res.access_token, res.user_id, res.username)
      message.success(`欢迎回来，${res.username}`)
      navigate('/', { replace: true })
    } catch (err) {
      message.error(err instanceof Error ? err.message : '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: '#0f1419',
      position: 'relative', overflow: 'hidden',
    }}>
      {/* Subtle grid pattern */}
      <div style={{
        position: 'absolute', inset: 0,
        backgroundImage: 'linear-gradient(rgba(22,119,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(22,119,255,0.03) 1px, transparent 1px)',
        backgroundSize: '40px 40px',
      }} />
      <div style={{
        background: '#161b22',
        border: '1px solid #21262d',
        borderRadius: 12,
        padding: '40px 36px 32px',
        width: 380,
        position: 'relative',
        boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
      }}>
        <div style={{ textAlign: 'center', marginBottom: 36 }}>
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" style={{ marginBottom: 12 }}>
            <rect x="2" y="2" width="9" height="9" rx="2" fill="#1677ff" />
            <rect x="13" y="2" width="9" height="9" rx="2" fill="#4690ff" opacity="0.7" />
            <rect x="2" y="13" width="9" height="9" rx="2" fill="#4690ff" opacity="0.7" />
            <rect x="13" y="13" width="9" height="9" rx="2" fill="#1677ff" />
          </svg>
          <Text strong style={{ fontSize: 20, color: '#e6edf3', display: 'block', letterSpacing: '0.3px' }}>
            ReportAgent
          </Text>
          <Text style={{ fontSize: 13, color: '#8b949e', marginTop: 4, display: 'block' }}>
            企业智能报表平台
          </Text>
        </div>
        <Form layout="vertical" onFinish={handleSubmit} autoComplete="off">
          <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input
              prefix={<UserOutlined style={{ color: '#484f58' }} />}
              placeholder="用户名"
              size="large"
              style={{ background: '#0f1419', borderColor: '#21262d', color: '#e6edf3' }}
            />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password
              prefix={<LockOutlined style={{ color: '#484f58' }} />}
              placeholder="密码"
              size="large"
              style={{ background: '#0f1419', borderColor: '#21262d', color: '#e6edf3' }}
            />
          </Form.Item>
          <Form.Item style={{ marginBottom: 16 }}>
            <Button type="primary" htmlType="submit" loading={loading} block size="large" style={{ height: 44, fontSize: 15, borderRadius: 8 }}>
              登录
            </Button>
          </Form.Item>
        </Form>
        <div style={{ textAlign: 'center' }}>
          <Text style={{ fontSize: 12, color: '#484f58' }}>
            默认账号: admin / admin123
          </Text>
        </div>
      </div>
    </div>
  )
}
