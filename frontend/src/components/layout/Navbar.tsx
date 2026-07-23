import { Layout, Typography, Dropdown, Tag } from 'antd'
import { UserOutlined, LogoutOutlined } from '@ant-design/icons'
import { useLocation, useNavigate } from 'react-router-dom'

import { useAuthStore } from '../../stores/authStore'
import {
  IconChat, IconHistory, IconLogo, IconTemplate,
} from '../ui/Icons'

const { Text } = Typography

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
    <Layout.Header
      style={{
        height: 52,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 24px',
        background: '#FFFFFF',
        borderBottom: '1px solid #E5E7EB',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 32, height: '100%' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <IconLogo width={20} height={20} style={{ color: '#0F172A' }} />
          <Text strong style={{ fontSize: 16, color: '#0F172A', lineHeight: 1.25 }}>
            ReportAgent
          </Text>
          <Tag
            style={{
              fontSize: 13,
              lineHeight: '20px',
              padding: '0 8px',
              borderRadius: 6,
              background: '#EFF6FF',
              border: '1px solid #E5E7EB',
              color: '#6B7280',
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
                  color: active ? '#0F172A' : '#6B7280',
                  background: 'transparent', border: 'none',
                  borderBottom: active ? '2px solid #1677FF' : '2px solid transparent',
                  cursor: 'pointer', fontSize: 14,
                  fontWeight: active ? 600 : 400,
                }}
                onMouseEnter={(e) => { e.currentTarget.style.color = '#0F172A' }}
                onMouseLeave={(e) => { e.currentTarget.style.color = active ? '#0F172A' : '#6B7280' }}
              >
                {item.icon}{item.label}
              </button>
            )
          })}
        </nav>
      </div>

      <Dropdown
        menu={{
          items: [
            {
              key: 'logout',
              icon: <LogoutOutlined />,
              label: '退出登录',
              onClick: handleLogout,
            },
          ],
        }}
        placement="bottomRight"
      >
        <div
          style={{
            display: 'flex', alignItems: 'center', gap: 8,
            cursor: 'pointer', padding: '4px 8px',
            borderRadius: 6, color: '#6B7280', fontSize: 14,
            border: '1px solid transparent',
            transition: 'color 0.15s, border-color 0.15s',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = '#0F172A'
            e.currentTarget.style.borderColor = '#E5E7EB'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = '#6B7280'
            e.currentTarget.style.borderColor = 'transparent'
          }}
        >
          <UserOutlined style={{ fontSize: 14 }} />
          <Text style={{ color: 'inherit', fontSize: 14 }}>{username || '用户'}</Text>
        </div>
      </Dropdown>
    </Layout.Header>
  )
}
