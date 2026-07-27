import ReactECharts from 'echarts-for-react'
import Tag from '../../../components/atelier/Tag'
import Card from '../../../components/atelier/Card'
import type { ReportBlock } from '../../../types/report'
import { prepareChart, uniqueCategories, type PreparedChart } from './chartPrepare'

interface Props {
  block: ReportBlock
  /** Render bare (inside a report-shell panel) instead of the Card wrap. */
  bare?: boolean
  height?: number
}

const CHART_COLORS = [
  'var(--chart-1)',
  'var(--chart-2)',
  'var(--chart-3)',
  'var(--chart-4)',
  'var(--chart-5)',
  'var(--chart-6)',
]

const TYPE_LABELS: Record<PreparedChart['chartType'], string> = {
  bar: '柱状图',
  line: '折线图',
  pie: '饼图',
}

export default function ChartBlock({ block, bare, height = 280 }: Props) {
  const prepared = prepareChart(block.data as Record<string, unknown>)
  const option = buildOption(prepared)

  const chart = (
    /* width:100% is load-bearing: the Card body is a flex container,
       and without it the chart div shrinks to 0px wide → ECharts
       "Can't get DOM width or height". */
    <ReactECharts
      option={option}
      style={{ height, width: '100%' }}
      opts={{ renderer: 'svg' }}
      notMerge
    />
  )

  if (bare) return chart

  return (
    <Card
      title={block.title || '图表'}
      extra={
        <Tag tone="default" style={{ borderRadius: 4, fontSize: 11, padding: '0 8px', lineHeight: '22px' }}>
          {TYPE_LABELS[prepared.chartType]}
        </Tag>
      }
      size="small"
      bodyStyle={{ padding: '8px 0 0', display: 'block' }}
      style={{ borderRadius: 10 }}
    >
      {chart}
    </Card>
  )
}

function buildOption(p: PreparedChart): Record<string, unknown> {
  if (p.rows.length === 0 || !p.xField || !p.yField) {
    return {
      title: { text: '无图表数据', left: 'center', top: 'middle', textStyle: { color: 'var(--muted)', fontSize: 13, fontWeight: 400 } },
    }
  }
  if (p.chartType === 'pie') return buildPieOption(p)
  return buildCartesianOption(p)
}

const TOOLTIP_STYLE = {
  backgroundColor: 'var(--paper)',
  borderColor: 'var(--line)',
  borderWidth: 1,
  textStyle: { color: 'var(--ink)', fontSize: 12 },
}

function buildSeries(p: PreparedChart, type: 'bar' | 'line') {
  const categories = uniqueCategories(p.rows, p.xField)
  const value = (row: Record<string, unknown>) => Number(row[p.yField]) || 0

  if (!p.seriesField) {
    return [
      {
        name: p.yField,
        type,
        data: p.rows.map(value),
        ...(type === 'bar'
          ? { barMaxWidth: 40, itemStyle: { borderRadius: [4, 4, 0, 0] } }
          : { smooth: true, lineStyle: { width: 2.5 }, symbol: 'circle', symbolSize: 6 }),
      },
    ]
  }

  const seriesNames = uniqueCategories(p.rows, p.seriesField)
  return seriesNames.map((name) => ({
    name,
    type,
    data: categories.map((cat) => {
      const row = p.rows.find(
        (r) => String(r[p.xField] ?? '') === cat && String(r[p.seriesField!] ?? '') === name,
      )
      return row ? value(row) : null
    }),
    ...(type === 'bar'
      ? { barMaxWidth: 28, itemStyle: { borderRadius: [3, 3, 0, 0] } }
      : { smooth: true, lineStyle: { width: 2.5 }, symbol: 'circle', symbolSize: 6 }),
  }))
}

function buildCartesianOption(p: PreparedChart): Record<string, unknown> {
  const type = p.chartType === 'line' ? 'line' : 'bar'
  return {
    color: CHART_COLORS,
    tooltip: { trigger: 'axis', ...TOOLTIP_STYLE },
    legend: p.seriesField ? { bottom: 0, textStyle: { color: 'var(--muted)', fontSize: 11 } } : undefined,
    grid: { left: 60, right: 20, bottom: p.seriesField ? 42 : 40, top: 20 },
    xAxis: {
      type: 'category',
      data: uniqueCategories(p.rows, p.xField),
      axisLabel: { rotate: 30, fontSize: 11, color: 'var(--muted)' },
      axisLine: { lineStyle: { color: 'var(--line)' } },
      axisTick: { alignWithLabel: true },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: 'var(--line)', type: 'dashed' } },
      axisLabel: { fontSize: 11, color: 'var(--muted)' },
    },
    series: buildSeries(p, type),
  }
}

function buildPieOption(p: PreparedChart): Record<string, unknown> {
  // Aggregate duplicate names (e.g. one slice per 区域 summed over 季度).
  const totals = new Map<string, number>()
  for (const row of p.rows) {
    const name = String(row[p.xField] ?? '')
    totals.set(name, (totals.get(name) ?? 0) + (Number(row[p.yField]) || 0))
  }
  return {
    color: CHART_COLORS,
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)', ...TOOLTIP_STYLE },
    legend: { bottom: 0, textStyle: { color: 'var(--muted)', fontSize: 11 } },
    series: [
      {
        type: 'pie',
        radius: ['35%', '60%'],
        center: ['50%', '46%'],
        data: [...totals.entries()].map(([name, total]) => ({ name, value: total })),
        label: { show: true, formatter: '{b}\n{d}%', fontSize: 11, color: 'var(--ink-2)' },
        labelLine: { lineStyle: { color: 'var(--line)' } },
        emphasis: { itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.1)' } },
      },
    ],
  }
}
