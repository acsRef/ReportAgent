import { Typography, Space, Button } from 'antd'
import {
  MessageOutlined,
  AppstoreOutlined,
  HistoryOutlined,
  SaveOutlined,
  DownloadOutlined,
} from '@ant-design/icons'
import { useLocation, useNavigate } from 'react-router-dom'

const { Text } = Typography

const NAV_ITEMS = [
  { key: '/', label: '对话与生成 (Chat)', icon: <MessageOutlined /> },
  { key: '/templates', label: '模板中心 (Templates)', icon: <AppstoreOutlined /> },
  { key: '/history', label: '历史报告 (History)', icon: <HistoryOutlined /> },
]

export default function Navbar() {
  const location = useLocation()
  const navigate = useNavigate()

  return (
    <header
      style={{
        height: 52,
        background: '#001529',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 20px',
        borderBottom: '1px solid rgba(255,255,255,0.08)',
        flexShrink: 0,
        zIndex: 100,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 18 }}>📊</span>
          <Text strong style={{ fontSize: 15, color: '#fff', letterSpacing: '0.3px' }}>
            ReportAgent
          </Text>
          <span
            style={{
              background: 'rgba(255,255,255,0.12)',
              fontSize: 10,
              padding: '1px 7px',
              borderRadius: 10,
              color: '#91caff',
              fontWeight: 500,
            }}
          >
            Enterprise v1.2
          </span>
        </div>

        <nav style={{ display: 'flex', gap: 2 }}>
          {NAV_ITEMS.map((item) => {
            const isActive = location.pathname === item.key
            return (
              <div
                key={item.key}
                onClick={() => navigate(item.key)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '14px 14px',
                  cursor: 'pointer',
                  color: isActive ? '#fff' : 'rgba(255,255,255,0.55)',
                  borderBottom: '2px solid',
                  borderBottomColor: isActive ? '#1677ff' : 'transparent',
                  transition: 'all 0.2s',
                  fontSize: 13,
                  fontWeight: isActive ? 500 : 400,
                  userSelect: 'none',
                }}
              >
                {item.icon}
                <span>{item.label}</span>
              </div>
            )
          })}
        </nav>
      </div>

      <Space size="small">
        <Button
          size="small"
          icon={<SaveOutlined />}
          style={{
            background: 'rgba(255,255,255,0.08)',
            border: '1px solid rgba(255,255,255,0.15)',
            color: 'rgba(255,255,255,0.85)',
            fontSize: 12,
          }}
        >
          保存为模板
        </Button>
        <Button
          size="small"
          icon={<DownloadOutlined />}
          style={{
            background: 'rgba(255,255,255,0.08)',
            border: '1px solid rgba(255,255,255,0.15)',
            color: 'rgba(255,255,255,0.85)',
            fontSize: 12,
          }}
        >
          导出
        </Button>
      </Space>
    </header>
  )
}
