import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { GaugeChart } from 'echarts/charts';
import { TooltipComponent, TitleComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { GaugeProps } from '../../types/panel';
import './Gauge.css';

echarts.use([GaugeChart, TooltipComponent, TitleComponent, CanvasRenderer]);

export default function Gauge(props: GaugeProps) {
  const { title, value, min = 0, max = 100, unit = '', threshold } = props;

  const option: echarts.EChartsCoreOption = {
    series: [{
      type: 'gauge',
      min,
      max,
      startAngle: 220,
      endAngle: -40,
      center: ['50%', '55%'],
      radius: '90%',
      axisLine: {
        lineStyle: {
          width: 12,
          color: threshold
            ? [
                [threshold.warning! / max, '#52c41a'],
                [threshold.danger! / max, '#faad14'],
                [1, '#ff4d4f'],
              ] as [number, string][]
            : [[1, '#1677ff']],
        },
      },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      pointer: { width: 4, length: '60%' },
      detail: {
        show: false,
      },
      data: [{ value }],
      title: {
        show: !!title,
        offsetCenter: [0, '70%'],
        fontSize: 13,
        color: '#666',
      },
    }],
  };

  return (
    <div className="gauge-panel">
      {title && <div className="gauge-panel-title">{title}</div>}
      <ReactEChartsCore
        echarts={echarts}
        option={option}
        style={{ height: '100%', width: '100%' }}
        opts={{ renderer: 'canvas' }}
      />
      <div className="gauge-value-wrapper">
        <div className="gauge-value">{value}</div>
        {unit && <div className="gauge-unit">{unit}</div>}
      </div>
    </div>
  );
}