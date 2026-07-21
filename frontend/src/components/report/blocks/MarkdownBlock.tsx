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
    <div style={{ fontSize: 14, lineHeight: 1.8 }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <Text strong style={{ fontSize: 20, display: 'block', margin: '16px 0 8px' }}>
              {children}
            </Text>
          ),
          h2: ({ children }) => (
            <Text strong style={{ fontSize: 17, display: 'block', margin: '14px 0 6px' }}>
              {children}
            </Text>
          ),
          h3: ({ children }) => (
            <Text strong style={{ fontSize: 15, display: 'block', margin: '12px 0 4px' }}>
              {children}
            </Text>
          ),
          p: ({ children }) => (
            <Text style={{ display: 'block', marginBottom: 8 }}>{children}</Text>
          ),
          ul: ({ children }) => <ul style={{ paddingLeft: 20, margin: '4px 0' }}>{children}</ul>,
          ol: ({ children }) => <ol style={{ paddingLeft: 20, margin: '4px 0' }}>{children}</ol>,
          li: ({ children }) => <li style={{ marginBottom: 2 }}>{children}</li>,
          code: ({ children }) => (
            <code style={{ background: '#f5f5f5', padding: '2px 6px', borderRadius: 4, fontSize: 13 }}>
              {children}
            </code>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
