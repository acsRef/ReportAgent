import { UserOutlined, LogoutOutlined } from '@ant-design/icons'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../../stores/authStore'
import {
  IconChat, IconHistory, IconLogo, IconTemplate,
} from '../ui/Icons'
import { Text } from '../atelier/Typography'
import Dropdown from '../atelier/Dropdown'
import Tag from '../atelier/Tag'

const NAV_ITEMS = [
  { key: '/', label: '对话与生成', icon: <IconChat /> },
  { key: '/templates', label: '模板中心', icon: <IconTemplate /> },
  { key: '/history', label: '历史报告', icon: <IconHistory /> },
]

export default function Navbar() {
  const location = useLocation()
  const navigate = useNavigate()
  const { username, logout } = useAuthStore()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  return (
    <header
      style={{
        height: 52,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 24px',
        background: 'var(--paper)',
        borderBottom: '1px solid var(--line)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 32, height: '100%' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <IconLogo width={20} height={20} style={{ color: 'var(--ink)' }} />
          <Text strong style={{ fontSize: 16, color: 'var(--ink)', lineHeight: 1.25 }}>
            ReportAgent
          </Text>
          <Tag
            style={{
              fontSize: 13,
              lineHeight: '20px',
              padding: '0 8px',
              borderRadius: 6,
              background: 'var(--teal-soft)',
              border: '1px solid var(--line)',
              color: 'var(--muted)',
              margin: 0,
            }}
          >
            v2.0
          </Tag>
        </div>

        <nav style={{ display: 'flex', alignItems: 'stretch', gap: 24, height: '100%' }}>
          {NAV_ITEMS.map((item) => {
            const active = location.pathname === item.key
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => navigate(item.key)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  height: '100%', padding: 0,
                  color: active ? 'var(--ink)' : 'var(--muted)',
                  background: 'transparent', border: 'none',
                  borderBottom: active ? '2px solid var(--teal)' : '2px solid transparent',
                  cursor: 'pointer', fontSize: 14,
                  fontWeight: active ? 600 : 400,
                }}
                onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--ink)' }}
                onMouseLeave={(e) => { e.currentTarget.style.color = active ? 'var(--ink)' : 'var(--muted)' }}
              >
                {item.icon}{item.label}
              </button>
            )
          })}
        </nav>
      </div>

      <Dropdown
        items={[
          {
            key: 'logout',
            icon: <LogoutOutlined />,
            label: '退出登录',
            onClick: handleLogout,
          },
        ]}
        placement="bottom-end"
      >
        <div
          style={{
            display: 'flex', alignItems: 'center', gap: 8,
            cursor: 'pointer', padding: '4px 8px',
            borderRadius: 6, color: 'var(--muted)', fontSize: 14,
            border: '1px solid transparent',
            transition: 'color 0.15s, border-color 0.15s',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = 'var(--ink)'
            e.currentTarget.style.borderColor = 'var(--line)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = 'var(--muted)'
            e.currentTarget.style.borderColor = 'transparent'
          }}
        >
          <UserOutlined style={{ fontSize: 14 }} />
          <Text style={{ color: 'inherit', fontSize: 14 }}>{username || '用户'}</Text>
        </div>
      </Dropdown>
    </header>
  )
}
