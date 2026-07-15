/** 面板类型枚举 */
export type PanelType =
  | 'kpi-card'
  | 'chart'
  | 'table'
  | 'scroll-table'
  | 'text'
  | 'image'
  | 'gauge'
  | 'border-box'
  | 'divider'
  | 'time'
  | 'count-up'
  | 'progress'
  | 'video'
  | 'iframe';

/** 面板布局信息（像素坐标，自由画布用） */
export interface PanelLayout {
  x: number;  // left px
  y: number;  // top px
  w: number;  // width px
  h: number;  // height px
}

/** KPI 卡片配置 */
export interface KpiCardProps {
  label: string;
  value: string | number;
  unit?: string;
  trend?: 'up' | 'down' | 'flat';
  trendValue?: string;
  color?: string;
  prefix?: string;
  suffix?: string;
}

/** 图表配置 */
export type ChartType = 'bar' | 'line' | 'pie' | 'scatter' | 'area' | 'radar' | 'funnel' | 'heatmap';

export interface ChartProps {
  type: ChartType;
  title?: string;
  data: Record<string, unknown>[];
  dimensions: {
    x?: string;
    y?: string | string[];
    category?: string;
    value?: string;
    indicator?: string;  // 雷达图用
  };
  echartsOption?: Record<string, unknown>;
}

/** 表格列定义 */
export interface TableColumn {
  key: string;
  title: string;
  width?: number;
  sortable?: boolean;
  align?: 'left' | 'right' | 'center';
}

/** 表格配置 */
export interface TableProps {
  title?: string;
  columns: TableColumn[];
  data: Record<string, unknown>[];
  pageSize?: number;
}

/** 滚动表格配置 */
export interface ScrollTableProps {
  title?: string;
  columns: TableColumn[];
  data: Record<string, unknown>[];
  speed?: number;         // 滚动速度 ms
  hoverPause?: boolean;
}

/** 仪表盘配置 */
export interface GaugeProps {
  title?: string;
  value: number;
  min?: number;
  max?: number;
  unit?: string;
  threshold?: {          // 阈值区间
    warning?: number;
    danger?: number;
  };
}

/** 文本配置 */
export interface TextProps {
  content: string;
  fontSize?: number;
  color?: string;
  align?: 'left' | 'center' | 'right';
  fontWeight?: 'normal' | 'bold' | 'lighter';
}

/** 图片配置 */
export interface ImageProps {
  url: string;
  alt?: string;
  borderRadius?: number;
  objectFit?: 'cover' | 'contain' | 'fill';
}

/** 边框盒子配置 */
export interface BorderBoxProps {
  title?: string;
  content?: string;
  borderType?: 'default' | 'glow' | 'dashed' | 'gradient';
  borderColor?: string;
  backgroundColor?: string;
}

/** 分割线配置 */
export interface DividerProps {
  type?: 'solid' | 'dashed' | 'dotted' | 'gradient';
  color?: string;
  thickness?: number;
  icon?: string;
}

/** 时间组件配置 */
export interface TimeProps {
  format?: string;         // 'YYYY-MM-DD HH:mm:ss' | 'HH:mm:ss' | 'YYYY年MM月DD日'
  fontSize?: number;
  color?: string;
  showIcon?: boolean;
}

/** 数字翻牌配置 */
export interface CountUpProps {
  title?: string;
  value: number;
  prefix?: string;
  suffix?: string;
  decimals?: number;
  duration?: number;
  color?: string;
  fontSize?: number;
}

/** 进度条配置 */
export interface ProgressProps {
  title?: string;
  value: number;
  max?: number;
  showLabel?: boolean;
  color?: string;
  size?: 'sm' | 'md' | 'lg';
  type?: 'line' | 'circle';
}

/** 视频配置 */
export interface VideoProps {
  url: string;
  poster?: string;
  autoplay?: boolean;
  loop?: boolean;
  muted?: boolean;
}

/** iframe 配置 */
export interface IframeProps {
  url: string;
  title?: string;
}

/** 数据源绑定 */
export interface PanelDataSource {
  type: 'sql' | 'api';
  query: string;
  refreshInterval?: number;
}

/** 面板统一配置 */
export interface PanelConfig {
  id: string;
  type: PanelType;
  title?: string;
  props:
    | KpiCardProps
    | ChartProps
    | TableProps
    | ScrollTableProps
    | GaugeProps
    | TextProps
    | ImageProps
    | BorderBoxProps
    | DividerProps
    | TimeProps
    | CountUpProps
    | ProgressProps
    | VideoProps
    | IframeProps;
  layout: PanelLayout;
  dataSource?: PanelDataSource;
}
