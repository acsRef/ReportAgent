import type { PanelConfig } from '../../types/panel';
import KpiCard from '../panels/KpiCard';
import ChartPanel from '../panels/ChartPanel';
import TablePanel from '../panels/TablePanel';
import ScrollTable from '../panels/ScrollTable';
import TextBlock from '../panels/TextBlock';
import ImagePanel from '../panels/ImagePanel';
import Gauge from '../panels/Gauge';
import BorderBox from '../panels/BorderBox';
import Divider from '../panels/Divider';
import TimeWidget from '../panels/TimeWidget';
import CountUp from '../panels/CountUp';
import ProgressBar from '../panels/ProgressBar';
import VideoPanel from '../panels/VideoPanel';
import IframePanel from '../panels/IframePanel';

interface PanelRendererProps {
  panel: PanelConfig;
}

export default function PanelRenderer({ panel }: PanelRendererProps) {
  switch (panel.type) {
    case 'kpi-card':
      return <KpiCard {...(panel.props as any)} />;
    case 'chart':
      return <ChartPanel {...(panel.props as any)} />;
    case 'table':
      return <TablePanel {...(panel.props as any)} />;
    case 'scroll-table':
      return <ScrollTable {...(panel.props as any)} />;
    case 'text':
      return <TextBlock {...(panel.props as any)} />;
    case 'image':
      return <ImagePanel {...(panel.props as any)} />;
    case 'gauge':
      return <Gauge {...(panel.props as any)} />;
    case 'border-box':
      return <BorderBox {...(panel.props as any)} />;
    case 'divider':
      return <Divider {...(panel.props as any)} />;
    case 'time':
      return <TimeWidget {...(panel.props as any)} />;
    case 'count-up':
      return <CountUp {...(panel.props as any)} />;
    case 'progress':
      return <ProgressBar {...(panel.props as any)} />;
    case 'video':
      return <VideoPanel {...(panel.props as any)} />;
    case 'iframe':
      return <IframePanel {...(panel.props as any)} />;
    default:
      return <div style={{ padding: 16, color: '#999' }}>未知面板类型</div>;
  }
}