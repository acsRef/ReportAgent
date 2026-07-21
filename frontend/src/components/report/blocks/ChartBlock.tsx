import ReactECharts from 'echarts-for-react'
import { Typography } from 'antd'
import type { ReportBlock } from '../../../types/report'

const { Text } = Typography

interface Props {
  block: ReportBlock
}

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

  return (
    <div style={{
      background: '#fff',
      padding: '20px 24px',
      borderRadius: 8,
      border: '1px solid #e8e8e8',
      boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
    }}>
      <div style={{
        fontSize: 15,
        fontWeight: 600,
        marginBottom: 16,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <span>{block.title || '图表'}</span>
        {block.title && (
          <Text style={{ fontSize: 12, color: '#8f959e', fontWeight: 400 }}>
            {chartType === 'bar' ? '柱状图' : chartType === 'line' ? '折线图' : '饼图'}
          </Text>
        )}
      </div>
      <ReactECharts
        option={option}
        style={{ height: 280 }}
        opts={{ renderer: 'svg' }}
      />
    </div>
  )
}

function buildBarOption(config: Record<string, unknown> | undefined, data: Record<string, unknown>[]) {
  const xField = String(config?.xField || config?.x || '')
  const yField = String(config?.yField || config?.y || '')
  const hasFields = xField && yField

  return {
    tooltip: { trigger: 'axis' as const },
    grid: { left: 60, right: 20, bottom: 40, top: 20 },
    xAxis: {
      type: 'category' as const,
      data: hasFields ? data.map((d) => d[xField]) : data.map((_, i) => `项${i + 1}`),
      axisLabel: { rotate: 30, fontSize: 11, color: '#8f959e' },
      axisLine: { lineStyle: { color: '#e8e8e8' } },
    },
    yAxis: {
      type: 'value' as const,
      splitLine: { lineStyle: { color: '#f0f0f0' } },
      axisLabel: { fontSize: 11, color: '#8f959e' },
    },
    series: [
      {
        type: 'bar' as const,
        data: hasFields ? data.map((d) => Number(d[yField]) || 0) : data.map((d) => Number(Object.values(d)[0]) || 0),
        itemStyle: { color: '#1677ff', borderRadius: [4, 4, 0, 0] },
        barMaxWidth: 40,
      },
    ],
  }
}

function buildLineOption(config: Record<string, unknown> | undefined, data: Record<string, unknown>[]) {
  const xField = String(config?.xField || config?.x || '')
  const yField = String(config?.yField || config?.y || '')
  const hasFields = xField && yField

  return {
    tooltip: { trigger: 'axis' as const },
    grid: { left: 60, right: 20, bottom: 40, top: 20 },
    xAxis: {
      type: 'category' as const,
      data: hasFields ? data.map((d) => d[xField]) : data.map((_, i) => `项${i + 1}`),
      axisLabel: { fontSize: 11, color: '#8f959e' },
      axisLine: { lineStyle: { color: '#e8e8e8' } },
    },
    yAxis: {
      type: 'value' as const,
      splitLine: { lineStyle: { color: '#f0f0f0' } },
      axisLabel: { fontSize: 11, color: '#8f959e' },
    },
    series: [
      {
        type: 'line' as const,
        data: hasFields ? data.map((d) => Number(d[yField]) || 0) : data.map((d) => Number(Object.values(d)[0]) || 0),
        smooth: true,
        lineStyle: { color: '#1677ff', width: 2 },
        itemStyle: { color: '#1677ff' },
        areaStyle: { color: 'rgba(22,119,255,0.08)' },
        symbol: 'circle',
        symbolSize: 6,
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
    tooltip: { trigger: 'item' as const, formatter: '{b}: {c} ({d}%)' },
    color: ['#1677ff', '#69b1ff', '#91caff', '#bae0ff', '#d6e4ff', '#f0f5ff'],
    series: [
      {
        type: 'pie' as const,
        radius: ['35%', '60%'],
        center: ['50%', '50%'],
        data: pieData,
        label: { show: true, formatter: '{b}\n{d}%', fontSize: 11, color: '#646a73' },
        labelLine: { lineStyle: { color: '#e8e8e8' } },
        emphasis: {
          itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.15)' },
        },
      },
    ],
  }
}
