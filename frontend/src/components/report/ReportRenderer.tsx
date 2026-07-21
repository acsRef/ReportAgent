import type { ReportBlock } from '../../types/report'
import { getBlockComponent } from './registry'
import { Typography } from 'antd'

const { Text } = Typography

interface Props {
  blocks: ReportBlock[]
}

export default function ReportRenderer({ blocks }: Props) {
  if (blocks.length === 0) return null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {blocks.map((block) => {
        const Component = getBlockComponent(block.type)
        if (!Component) {
          return (
            <Text key={block.id} type="secondary" style={{ fontSize: 12 }}>
              未支持的组件类型: {block.type}
            </Text>
          )
        }
        return <Component key={block.id} block={block} />
      })}
    </div>
  )
}
