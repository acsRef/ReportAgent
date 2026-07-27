import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Button from '../components/atelier/Button'
import TextField from '../components/atelier/TextField'
import { Text, Title, Paragraph } from '../components/atelier/Typography'
import { useToast } from '../components/atelier/useToast'
import { useAuthStore } from '../stores/authStore'
import { loginAPI } from '../api/api'
import '../styles/global.css'

/**
 * Login page — editorial two-column layout. antd-free: local form state
 * with the exact validation messages the tests pin (请输入用户名/请输入密码).
 */
export default function LoginPage() {
  const navigate = useNavigate()
  const setAuth = useAuthStore((s) => s.setAuth)
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('admin123')
  const [errors, setErrors] = useState<{ username?: string; password?: string }>({})
  const [submitting, setSubmitting] = useState(false)
  const toast = useToast()

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    const next: { username?: string; password?: string } = {}
    if (!username.trim()) next.username = '请输入用户名'
    if (!password.trim()) next.password = '请输入密码'
    setErrors(next)
    if (next.username || next.password) return

    setSubmitting(true)
    try {
      const res = await loginAPI(username.trim(), password)
      setAuth(res.access_token, res.user_id, res.username)
      toast.success('登录成功')
      navigate('/')
    } catch (err) {
      toast.error(`登录失败：${(err as Error).message ?? '未知错误'}`)
    } finally {
      setSubmitting(false)
    }
  }

  const labelStyle = { color: 'var(--ink-2)', fontWeight: 600, fontSize: 13 }
  const errorStyle = { color: 'var(--red)', fontSize: 12, marginTop: 4, display: 'block' }

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
        <Title
          level={1}
          style={{
            fontFamily: 'var(--font-display)',
            fontSize: 40,
            margin: 0,
            lineHeight: 1.15,
            fontWeight: 700,
          }}
        >
          数据，先问再查
          <br />
          报告，按需演化
        </Title>
        <Paragraph
          style={{
            fontFamily: 'var(--font-ui)',
            maxWidth: 460,
            margin: 0,
            lineHeight: 1.7,
          }}
        >
          ReportAgent 让你用中文提问，自动生成 SQL、报告与洞察。
          每一份分析都可追溯、可调整、可重做。
        </Paragraph>
        <Text
          style={{
            color: 'var(--muted)',
            fontSize: 11,
            letterSpacing: 1.4,
            textTransform: 'uppercase',
            fontWeight: 700,
          }}
        >
          默认账号 · admin / admin123
        </Text>
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
          <Title
            level={3}
            style={{
              fontFamily: 'var(--font-display)',
              fontSize: 22,
              marginBottom: 24,
            }}
          >
            登录
          </Title>
          <form onSubmit={handleSubmit} style={{ display: 'grid', gap: 16 }}>
            <div>
              <label htmlFor="login-username" style={labelStyle}>
                用户名
              </label>
              <TextField
                id="login-username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder="admin"
                autoComplete="username"
              />
              {errors.username && (
                <span role="alert" style={errorStyle}>
                  {errors.username}
                </span>
              )}
            </div>
            <div>
              <label htmlFor="login-password" style={labelStyle}>
                密码
              </label>
              <TextField
                id="login-password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="••••••••"
                autoComplete="current-password"
              />
              {errors.password && (
                <span role="alert" style={errorStyle}>
                  {errors.password}
                </span>
              )}
            </div>
            <Button variant="primary" block loading={submitting} type="submit" style={{ marginTop: 8 }}>
              进入工作台
            </Button>
          </form>
        </div>
      </section>
    </div>
  )
}
