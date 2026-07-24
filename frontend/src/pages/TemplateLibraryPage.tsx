import { Layout, Typography } from 'antd'
import '../styles/global.css'

/**
 * Template Library — three-pane: left filters, center cards, right preview.
 * This is the Phase 6 minimum-viable shell. CRUD wiring (Phase 7) lives
 * in `stores/templateStore.ts` + `/api/v1/templates` client.
 */
export default function TemplateLibraryPage() {
  return (
    <Layout style={{ minHeight: '100vh', background: 'var(--canvas)' }}>
      <Layout.Header
        style={{
          background: 'var(--ink)',
          color: '#FFFFFF',
          display: 'flex',
          alignItems: 'center',
          padding: '0 22px',
        }}
      >
        <Typography.Title
          level={4}
          style={{ color: '#FFFFFF', margin: 0, fontFamily: 'var(--font-display)' }}
        >
          模板中心
        </Typography.Title>
      </Layout.Header>
      <Layout.Content
        style={{
          padding: 'var(--sp-xl)',
          display: 'grid',
          gridTemplateColumns: '220px 1fr 320px',
          gap: 'var(--sp-l)',
        }}
      >
        <aside
          style={{
            background: 'var(--paper)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-m)',
            padding: 'var(--sp-l)',
          }}
        >
          <Typography.Text style={{ color: 'var(--muted)', fontSize: 11, letterSpacing: 1.2, textTransform: 'uppercase' }}>
            分类
          </Typography.Text>
        </aside>
        <section
          style={{
            background: 'var(--paper)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-m)',
            padding: 'var(--sp-l)',
            minHeight: 480,
          }}
        >
          <Typography.Text style={{ color: 'var(--muted)' }}>
            模板卡片占位（Phase 7 接入 PG）
          </Typography.Text>
        </section>
        <aside
          style={{
            background: 'var(--paper)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-m)',
            padding: 'var(--sp-l)',
          }}
        >
          <Typography.Text style={{ color: 'var(--muted)' }}>预览</Typography.Text>
        </aside>
      </Layout.Content>
    </Layout>
  )
}
