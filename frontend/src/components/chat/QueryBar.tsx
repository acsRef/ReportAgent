import { useState, useCallback } from 'react'
import { Input, Button } from 'antd'
import { SendOutlined } from '@ant-design/icons'

const { TextArea } = Input

interface Props {
  onSend: (text: string) => void
  disabled: boolean
}

export default function QueryBar({ onSend, disabled }: Props) {
  const [text, setText] = useState('')

  const handleSend = useCallback(() => {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setText('')
  }, [text, disabled, onSend])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div
      style={{
        display: 'flex',
        gap: 12,
        padding: '16px 0',
        borderTop: '1px solid #f0f0f0',
        background: '#fff',
      }}
    >
      <TextArea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="输入分析问题，例如：2024年各区域销售总额"
        autoSize={{ minRows: 1, maxRows: 3 }}
        disabled={disabled}
        variant="outlined"
        style={{ fontSize: 14 }}
      />
      <Button
        type="primary"
        icon={<SendOutlined />}
        onClick={handleSend}
        disabled={disabled || !text.trim()}
        style={{
          alignSelf: 'flex-end',
          height: 40,
          paddingInline: 24,
          fontSize: 14,
        }}
      >
        生成报告
      </Button>
    </div>
  )
}
