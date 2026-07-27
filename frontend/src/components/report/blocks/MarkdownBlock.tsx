import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Text } from '../../atelier/Typography'
import type { ReportBlock } from '../../../types/report'

interface Props {
  block: ReportBlock
}

export default function MarkdownBlock({ block }: Props) {
  const content = String((block.data as Record<string, unknown>).content || '')

  if (!content) return null

  return (
    <div style={{
      background: 'var(--paper)',
      padding: '20px 24px',
      borderRadius: 10,
      border: '1px solid var(--line)',
      boxShadow: 'var(--shadow-card)',
    }}>
      <div style={{ fontSize: 14, lineHeight: 1.8, color: 'var(--ink)' }}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h1: ({ children }) => (
              <Text strong style={{ fontSize: 20, display: 'block', margin: '20px 0 10px', color: 'var(--ink)' }}>
                {children}
              </Text>
            ),
            h2: ({ children }) => (
              <Text strong style={{ fontSize: 17, display: 'block', margin: '16px 0 8px', color: 'var(--ink)' }}>
                {children}
              </Text>
            ),
            h3: ({ children }) => (
              <Text strong style={{ fontSize: 15, display: 'block', margin: '14px 0 6px', color: 'var(--ink)' }}>
                {children}
              </Text>
            ),
            p: ({ children }) => (
              <Text style={{ display: 'block', marginBottom: 10, color: 'var(--ink)' }}>{children}</Text>
            ),
            ul: ({ children }) => <ul style={{ paddingLeft: 20, margin: '6px 0' }}>{children}</ul>,
            ol: ({ children }) => <ol style={{ paddingLeft: 20, margin: '6px 0' }}>{children}</ol>,
            li: ({ children }) => <li style={{ marginBottom: 4, color: 'var(--ink)' }}>{children}</li>,
            code: ({ children }) => (
              <code style={{
                background: 'var(--canvas)',
                padding: '2px 8px',
                borderRadius: 4,
                fontSize: 13,
                color: 'var(--teal-deep)',
                border: '1px solid var(--line)',
              }}>
                {children}
              </code>
            ),
            blockquote: ({ children }) => (
              <div style={{
                borderLeft: '3px solid var(--teal)',
                padding: '8px 16px',
                margin: '12px 0',
                background: 'var(--canvas)',
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
