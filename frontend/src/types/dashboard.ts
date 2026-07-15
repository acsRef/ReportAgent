import type { PanelConfig } from './panel';

/** 画布分辨率 */
export interface CanvasResolution {
  width: number;
  height: number;
}

/** 看板模板描述 */
export interface DashboardTemplate {
  id: string;
  name: string;
  description: string;
  thumbnail?: string;
  panelCount: number;
}

/** 完整看板配置 */
export interface DashboardConfig {
  id: string;
  title: string;
  description?: string;
  panels: PanelConfig[];
  canvasResolution?: CanvasResolution;
  createdAt: string;
  updatedAt: string;
}