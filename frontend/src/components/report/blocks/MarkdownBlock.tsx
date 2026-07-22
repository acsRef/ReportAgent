import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Typography } from 'antd'
import type { ReportBlock } from '../../../types/report'

const { Text } = Typography

interface Props {
  block: ReportBlock
}

export default function MarkdownBlock({ block }: Props) {
  const content = String((block.data as Record<string, unknown>).content || '')

  if (!content) return null

  return (
    <div style={{
      background: '#FFFFFF',
      padding: '20px 24px',
      borderRadius: 10,
      border: '1px solid #E2E8F0',
      boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)',
    }}>
      <div style={{ fontSize: 14, lineHeight: 1.8, color: '#1E293B' }}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h1: ({ children }) => (
              <Text strong style={{ fontSize: 20, display: 'block', margin: '20px 0 10px', color: '#1E293B' }}>
                {children}
              </Text>
            ),
            h2: ({ children }) => (
              <Text strong style={{ fontSize: 17, display: 'block', margin: '16px 0 8px', color: '#1E293B' }}>
                {children}
              </Text>
            ),
            h3: ({ children }) => (
              <Text strong style={{ fontSize: 15, display: 'block', margin: '14px 0 6px', color: '#1E293B' }}>
                {children}
              </Text>
            ),
            p: ({ children }) => (
              <Text style={{ display: 'block', marginBottom: 10, color: '#1E293B' }}>{children}</Text>
            ),
            ul: ({ children }) => <ul style={{ paddingLeft: 20, margin: '6px 0' }}>{children}</ul>,
            ol: ({ children }) => <ol style={{ paddingLeft: 20, margin: '6px 0' }}>{children}</ol>,
            li: ({ children }) => <li style={{ marginBottom: 4, color: '#1E293B' }}>{children}</li>,
            code: ({ children }) => (
              <code style={{
                background: '#F1F5F9',
                padding: '2px 8px',
                borderRadius: 4,
                fontSize: 13,
                color: '#1E40AF',
                border: '1px solid #E2E8F0',
              }}>
                {children}
              </code>
            ),
            blockquote: ({ children }) => (
              <div style={{
                borderLeft: '3px solid #3B82F6',
                padding: '8px 16px',
                margin: '12px 0',
                background: '#F8FAFC',
                borderRadius: '0 6px 6px 0',
              }}>
                {children}
              </div>
            ),
          }}
        >
          {content}
        </ReactMarkdown>
      </div>
    </div>
  )
}