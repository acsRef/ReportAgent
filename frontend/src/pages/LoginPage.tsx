import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Button from '../components/atelier/Button'
import TextField from '../components/atelier/TextField'
import { Title, Paragraph } from '../components/atelier/Typography'
import { useToast } from '../components/atelier/useToast'
import { useAuthStore } from '../stores/authStore'
import { loginAPI } from '../api/api'
import '../styles/global.css'
import '../styles/observability.css'

/**
 * 登录页——编辑感双栏：左侧深色品牌区（层叠氛围光 + 图表母题 + 渐入），
 * 右侧纸面表单卡。表单结构与校验文案被测试钉住（请输入用户名/请输入密码），
 * 美化只动左侧视觉与动效，不动表单逻辑。
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
      {/* 左 — 深色品牌区（层叠氛围光 + 图表母题） */}
      <section
        style={{
          position: 'relative',
          padding: '64px 56px',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          gap: 26,
          background: 'var(--ink)',
          color: 'var(--on-ink)',
          overflow: 'hidden',
        }}
      >
        {/* 层叠氛围光（仅用 tokens） */}
        <div
          aria-hidden
          style={{
            position: 'absolute', inset: 0, opacity: 0.4, pointerEvents: 'none',
            background:
              'radial-gradient(620px 420px at 12% 18%, var(--teal-deep), transparent 62%), radial-gradient(520px 520px at 92% 96%, var(--teal), transparent 60%)',
          }}
        />
        {/* 图表母题（低不透明装饰） */}
        <svg
          aria-hidden
          viewBox="0 0 360 360"
          style={{ position: 'absolute', right: -30, bottom: -30, width: 340, height: 340, opacity: 0.14, pointerEvents: 'none' }}
        >
          <g stroke="var(--on-ink)" strokeWidth="2" fill="none" strokeLinecap="round">
            <polyline points="20,300 80,250 140,270 200,180 260,210 330,110" />
          </g>
          <g fill="var(--on-ink)">
            <rect x="40" y="320" width="26" height="20" rx="3" />
            <rect x="96" y="300" width="26" height="40" rx="3" />
            <rect x="152" y="280" width="26" height="60" rx="3" />
            <rect x="208" y="240" width="26" height="100" rx="3" />
            <rect x="264" y="200" width="26" height="140" rx="3" />
          </g>
        </svg>

        <div className="obs-fade" style={{ position: 'relative', display: 'flex', flexDirection: 'column', gap: 26 }}>
          <span
            style={{
              fontSize: 11, letterSpacing: 2.4, textTransform: 'uppercase', fontWeight: 700,
              color: 'var(--nav-active)',
            }}
          >
            ReportAgent · 智能分析工作台
          </span>
          <Title
            level={1}
            style={{
              fontFamily: 'var(--font-display)',
              fontSize: 42,
              margin: 0,
              lineHeight: 1.18,
              fontWeight: 700,
              color: 'var(--on-ink-strong)',
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
              lineHeight: 1.75,
              color: 'var(--on-ink-2)',
            }}
          >
            ReportAgent 让你用中文提问，自动生成 SQL、报告与洞察。
            每一份分析都可追溯、可调整、可重做。
          </Paragraph>
          <div
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 8, alignSelf: 'flex-start',
              padding: '6px 12px', borderRadius: 999,
              background: 'var(--on-ink-chip)', color: 'var(--ink)',
              fontSize: 11, fontWeight: 600, letterSpacing: 0.4,
            }}
          >
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--teal-deep)' }} />
            默认账号 · admin / admin123
          </div>
        </div>
      </section>

      {/* 右 — 纸面表单卡 */}
      <section
        style={{
          padding: '64px 48px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <div
          className="obs-fade"
          style={{
            width: '100%',
            maxWidth: 380,
            background: 'var(--paper)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-l)',
            boxShadow: 'var(--shadow-card)',
            padding: '40px 32px',
            animationDelay: '120ms',
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
