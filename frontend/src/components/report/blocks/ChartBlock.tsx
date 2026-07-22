import ReactECharts from 'echarts-for-react'
import { Card, Tag } from 'antd'
import type { ReportBlock } from '../../../types/report'

interface Props {
  block: ReportBlock
}

const CHART_COLORS = ['#1E40AF', '#3B82F6', '#D97706', '#059669', '#DC2626', '#7C3AED']

export default function ChartBlock({ block }: Props) {
  const data = block.data as Record<string, unknown>
  const chartType = String(data.type || 'bar')
  const config = data.config as Record<string, unknown> | undefined
  const chartData = (data.data as Record<string, unknown>[]) || (data.dataset as Record<string, unknown>[]) || []

  let option: Record<string, unknown>

  if (chartType === 'line') {
    option = buildLineOption(config, chartData)
  } else if (chartType === 'pie') {
    option = buildPieOption(config, chartData)
  } else {
    option = buildBarOption(config, chartData)
  }

  const typeLabel = chartType === 'bar' ? '柱状图' : chartType === 'line' ? '折线图' : '饼图'

  return (
    <Card
      title={block.title || '图表'}
      extra={<Tag style={{ borderRadius: 4, fontSize: 11, padding: '0 8px', lineHeight: '22px' }}>{typeLabel}</Tag>}
      size="small"
      styles={{ body: { padding: '8px 16px 16px' } }}
      style={{
        borderRadius: 10,
        boxShadow: '0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06)',
      }}
    >
      <ReactECharts
        option={option}
        style={{ height: 280 }}
        opts={{ renderer: 'svg' }}
      />
    </Card>
  )
}

function buildBarOption(config: Record<string, unknown> | undefined, data: Record<string, unknown>[]) {
  const xField = String(config?.xField || config?.x || '')
  const yField = String(config?.yField || config?.y || '')
  const hasFields = xField && yField

  return {
    color: CHART_COLORS,
    tooltip: { trigger: 'axis' as const, backgroundColor: '#FFFFFF', borderColor: '#E2E8F0', borderWidth: 1, textStyle: { color: '#1E293B', fontSize: 12 } },
    grid: { left: 60, right: 20, bottom: 40, top: 20 },
    xAxis: {
      type: 'category' as const,
      data: hasFields ? data.map((d) => d[xField]) : data.map((_, i) => `项${i + 1}`),
      axisLabel: { rotate: 30, fontSize: 11, color: '#64748B' },
      axisLine: { lineStyle: { color: '#E2E8F0' } },
      axisTick: { alignWithLabel: true },
    },
    yAxis: {
      type: 'value' as const,
      splitLine: { lineStyle: { color: '#F1F5F9', type: 'dashed' as const } },
      axisLabel: { fontSize: 11, color: '#64748B' },
    },
    series: [
      {
        type: 'bar' as const,
        data: hasFields ? data.map((d) => Number(d[yField]) || 0) : data.map((d) => Number(Object.values(d)[0]) || 0),
        itemStyle: {
          color: '#1E40AF',
          borderRadius: [4, 4, 0, 0],
        },
        barMaxWidth: 40,
        emphasis: { itemStyle: { shadowBlur: 6, shadowOffsetX: 0, shadowColor: 'rgba(30,64,175,0.2)' } },
      },
    ],
  }
}

function buildLineOption(config: Record<string, unknown> | undefined, data: Record<string, unknown>[]) {
  const xField = String(config?.xField || config?.x || '')
  const yField = String(config?.yField || config?.y || '')
  const hasFields = xField && yField

  return {
    color: CHART_COLORS,
    tooltip: { trigger: 'axis' as const, backgroundColor: '#FFFFFF', borderColor: '#E2E8F0', borderWidth: 1, textStyle: { color: '#1E293B', fontSize: 12 } },
    grid: { left: 60, right: 20, bottom: 40, top: 20 },
    xAxis: {
      type: 'category' as const,
      data: hasFields ? data.map((d) => d[xField]) : data.map((_, i) => `项${i + 1}`),
      axisLabel: { fontSize: 11, color: '#64748B' },
      axisLine: { lineStyle: { color: '#E2E8F0' } },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value' as const,
      splitLine: { lineStyle: { color: '#F1F5F9', type: 'dashed' as const } },
      axisLabel: { fontSize: 11, color: '#64748B' },
    },
    series: [
      {
        type: 'line' as const,
        data: hasFields ? data.map((d) => Number(d[yField]) || 0) : data.map((d) => Number(Object.values(d)[0]) || 0),
        smooth: true,
        lineStyle: { width: 2.5 },
        itemStyle: { color: '#3B82F6' },
        areaStyle: {
          color: {
            type: 'linear' as const,
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(59,130,246,0.15)' },
              { offset: 1, color: 'rgba(59,130,246,0.01)' },
            ],
          },
        },
        symbol: 'circle',
        symbolSize: 6,
        emphasis: { itemStyle: { shadowBlur: 6, shadowOffsetX: 0, shadowColor: 'rgba(59,130,246,0.3)' } },
      },
    ],
  }
}

function buildPieOption(config: Record<string, unknown> | undefined, data: Record<string, unknown>[]) {
  const nameField = String(config?.nameField || config?.name || '')
  const valueField = String(config?.valueField || config?.value || '')
  const hasFields = nameField && valueField

  const pieData = hasFields
    ? data.map((d) => ({ name: String(d[nameField] || ''), value: Number(d[valueField]) || 0 }))
    : data.map((d) => {
        const vals = Object.values(d)
        return { name: String(vals[0] || ''), value: Number(vals[1]) || 0 }
      })

  return {
    tooltip: { trigger: 'item' as const, formatter: '{b}: {c} ({d}%)', backgroundColor: '#FFFFFF', borderColor: '#E2E8F0', borderWidth: 1, textStyle: { color: '#1E293B', fontSize: 12 } },
    color: CHART_COLORS,
    series: [
      {
        type: 'pie' as const,
        radius: ['35%', '60%'],
        center: ['50%', '50%'],
        data: pieData,
        label: { show: true, formatter: '{b}\n{d}%', fontSize: 11, color: '#475569' },
        labelLine: { lineStyle: { color: '#E2E8F0' } },
        emphasis: {
          itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.1)' },
          label: { fontWeight: 'bold' as const },
        },
      },
    ],
  }
}