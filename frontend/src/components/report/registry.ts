import type { ComponentType } from 'react'
import type { ReportBlock, ReportBlockType } from '../../types/report'
import KpiBlock from './blocks/KpiBlock'
import TableBlock from './blocks/TableBlock'
import ChartBlock from './blocks/ChartBlock'
import InsightBlock from './blocks/InsightBlock'
import MarkdownBlock from './blocks/MarkdownBlock'

type BlockComponent = ComponentType<{ block: ReportBlock }>

const registry: Partial<Record<ReportBlockType, BlockComponent>> = {
  kpi: KpiBlock,
  table: TableBlock,
  chart: ChartBlock,
  insight: InsightBlock,
  markdown: MarkdownBlock,
  summary: MarkdownBlock,
}

export function getBlockComponent(type: ReportBlockType): BlockComponent | null {
  return registry[type] ?? null
}
