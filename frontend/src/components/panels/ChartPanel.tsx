import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart, ScatterChart, RadarChart, FunnelChart, HeatmapChart } from 'echarts/charts';
import {
  GridComponent, TooltipComponent, TitleComponent, LegendComponent,
  RadarComponent, VisualMapComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { ChartProps } from '../../types/panel';
import './ChartPanel.css';

echarts.use([
  BarChart, LineChart, PieChart, ScatterChart, RadarChart, FunnelChart, HeatmapChart,
  GridComponent, TooltipComponent, TitleComponent, LegendComponent,
  RadarComponent, VisualMapComponent,
  CanvasRenderer,
]);

function buildOption(props: ChartProps): echarts.EChartsCoreOption {
  const { type, title, data, dimensions, echartsOption } = props;

  const colors = ['#1677ff', '#52c41a', '#faad14', '#ff4d4f', '#722ed1', '#13c2c2', '#eb2f96'];

  const base: echarts.EChartsCoreOption = {
    color: colors,
    tooltip: { trigger: (type === 'pie' || type === 'funnel') ? 'item' : 'axis' },
    animationDuration: 400,
  };

  if (title) {
    base.title = {
      text: title,
      textStyle: { fontSize: 14, fontWeight: 600, color: '#1f1f1f' },
      left: 0,
      top: 0,
    };
  }

  // ── Radar ──
  if (type === 'radar') {
    const indicators = dimensions.indicator
      ? data.map((d) => ({ name: String(d[dimensions.indicator!]), max: 100 }))
      : data.map((d) => ({ name: String(d.name || d.label || d.category), max: 100 }));
    const valueField = Array.isArray(dimensions.y) ? dimensions.y[0] : (dimensions.y || 'value');

    return {
      ...base,
      radar: {
        indicator: indicators,
        center: ['50%', '55%'],
        radius: '65%',
        name: { textStyle: { fontSize: 11 } },
        splitArea: { areaStyle: { color: ['rgba(22,119,255,0.02)', 'rgba(22,119,255,0.04)'] } },
      },
      series: [{
        type: 'radar',
        data: [{ value: data.map((d) => Number(d[valueField])), name: title || '' }],
        areaStyle: { opacity: 0.15 },
        lineStyle: { width: 2 },
        symbol: 'circle',
        symbolSize: 6,
      }],
      ...echartsOption,
    };
  }

  // ── Funnel ──
  if (type === 'funnel') {
    const nameField = dimensions.category || 'name';
    const valueField = dimensions.value || 'value';

    return {
      ...base,
      tooltip: { trigger: 'item', formatter: '{b}: {c}' },
      series: [{
        type: 'funnel',
        left: '10%',
        right: '10%',
        top: title ? 40 : 16,
        bottom: 20,
        minSize: '10%',
        maxSize: '100%',
        sort: 'descending',
        gap: 2,
        label: { show: true, formatter: '{b}\n{d}%', fontSize: 12 },
        labelLine: { show: false },
        itemStyle: { borderColor: '#fff', borderWidth: 1 },
        emphasis: { label: { fontSize: 14 }, itemStyle: { shadowBlur: 10 } },
        data: data.map((d) => ({
          name: String(d[nameField]),
          value: Number(d[valueField]),
        })),
      }],
      ...echartsOption,
    };
  }

  // ── Pie ──
  if (type === 'pie' && dimensions.category && dimensions.value) {
    return {
      ...base,
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      series: [{
        type: 'pie',
        radius: ['40%', '65%'],
        center: ['50%', '55%'],
        data: data.map((d) => ({
          name: String(d[dimensions.category!]),
          value: Number(d[dimensions.value!]),
        })),
        label: { show: true, formatter: '{b}: {d}%', fontSize: 12 },
        emphasis: { itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.2)' } },
      }],
      ...echartsOption,
    };
  }

  const xField = dimensions.x || 'x';
  const yFields = Array.isArray(dimensions.y) ? dimensions.y : (dimensions.y ? [dimensions.y] : ['y']);

  // ── Scatter ──
  if (type === 'scatter') {
    return {
      ...base,
      xAxis: { type: 'value', name: xField },
      yAxis: { type: 'value', name: yFields[0] },
      series: [{
        type: 'scatter',
        data: data.map((d) => [Number(d[xField]), Number(d[yFields[0]])]),
        symbolSize: 8,
      }],
      ...echartsOption,
    };
  }

  // ── Bar / Line / Area ──
  const categories = data.map((d) => String(d[xField]));
  const series = yFields.map((field) => ({
    name: field,
    type: (type === 'area' ? 'line' : type) as 'bar' | 'line',
    data: data.map((d) => Number(d[field])),
    smooth: type === 'line' || type === 'area',
    areaStyle: type === 'area' ? { opacity: 0.15 } : undefined,
    itemStyle: { borderRadius: type === 'bar' ? [2, 2, 0, 0] as unknown as number : undefined },
  }));

  return {
    ...base,
    grid: { left: 48, right: 16, top: title ? 40 : 16, bottom: 32 },
    xAxis: {
      type: 'category',
      data: categories,
      axisLabel: { rotate: categories.length > 6 ? 35 : 0, fontSize: 11 },
    },
    yAxis: { type: 'value' },
    series,
    legend: yFields.length > 1 ? { bottom: 0, icon: 'circle', itemWidth: 8 } : undefined,
    ...echartsOption,
  };
}

export default function ChartPanel(props: ChartProps) {
  const { data } = props;

  if (!data || data.length === 0) {
    return (
      <div className="chart-panel-empty">
        <span className="chart-panel-empty-icon">📈</span>
        <span>暂无图表数据</span>
      </div>
    );
  }

  const option = buildOption(props);

  return (
    <div className="chart-panel">
      <ReactEChartsCore
        echarts={echarts}
        option={option}
        notMerge
        lazyUpdate
        style={{ height: '100%', width: '100%' }}
        opts={{ renderer: 'canvas' }}
      />
    </div>
  );
}