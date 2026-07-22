import { Layout, Menu, Typography, Dropdown, Tag } from 'antd'
import {
  MessageOutlined,
  AppstoreOutlined,
  HistoryOutlined,
  UserOutlined,
  LogoutOutlined,
} from '@ant-design/icons'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../../stores/authStore'

const { Text } = Typography

const NAV_ITEMS = [
  { key: '/', label: '对话与生成', icon: <MessageOutlined /> },
  { key: '/templates', label: '模板中心', icon: <AppstoreOutlined /> },
  { key: '/history', label: '历史报告', icon: <HistoryOutlined /> },
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
        padding: '0 20px',
        background: '#0F172A',
        borderBottom: '1px solid #1E293B',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 28 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
            <rect x="2" y="2" width="9" height="9" rx="2" fill="#3B82F6" />
            <rect x="13" y="2" width="9" height="9" rx="2" fill="#60A5FA" opacity="0.8" />
            <rect x="2" y="13" width="9" height="9" rx="2" fill="#60A5FA" opacity="0.8" />
            <rect x="13" y="13" width="9" height="9" rx="2" fill="#3B82F6" />
          </svg>
          <Text strong style={{ fontSize: 15, color: '#F1F5F9', letterSpacing: '0.3px' }}>
            ReportAgent
          </Text>
          <Tag
            color="default"
            style={{
              fontSize: 10,
              lineHeight: '18px',
              padding: '0 6px',
              borderRadius: 4,
              background: 'rgba(59,130,246,0.15)',
              border: '1px solid rgba(59,130,246,0.3)',
              color: '#93C5FD',
            }}
          >
            v2.0
          </Tag>
        </div>
        <Menu
          mode="horizontal"
          theme="dark"
          selectedKeys={[location.pathname]}
          items={NAV_ITEMS.map((item) => ({
            key: item.key,
            icon: item.icon,
            label: item.label,
            onClick: () => navigate(item.key),
          }))}
          style={{
            background: 'transparent',
            borderBottom: 'none',
            minWidth: 340,
          }}
        />
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
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            cursor: 'pointer',
            padding: '4px 10px',
            borderRadius: 6,
            color: '#94A3B8',
            fontSize: 13,
            transition: 'background 0.15s',
          }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.06)' }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
        >
          <UserOutlined style={{ fontSize: 14, color: '#94A3B8' }} />
          <Text style={{ color: '#E2E8F0', fontSize: 13 }}>{username || '用户'}</Text>
        </div>
      </Dropdown>
    </Layout.Header>
  )
}