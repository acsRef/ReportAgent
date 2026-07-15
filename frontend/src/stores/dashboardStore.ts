import { create } from 'zustand';
import type { PanelConfig, PanelLayout } from '../types/panel';
import type { DashboardConfig, DashboardTemplate, CanvasResolution } from '../types/dashboard';

interface DashboardState {
  dashboardConfig: DashboardConfig | null;
  panels: PanelConfig[];
  selectedPanelId: string | null;
  isEditing: boolean;
  isGenerating: boolean;
  templates: DashboardTemplate[];

  /** 画布缩放比例 */
  canvasZoom: number;
  /** 画布分辨率 */
  canvasResolution: CanvasResolution;

  setDashboardConfig: (config: DashboardConfig) => void;
  addPanel: (panel: PanelConfig) => void;
  removePanel: (panelId: string) => void;
  updatePanel: (panelId: string, updates: Partial<PanelConfig>) => void;
  updatePanelLayout: (panelId: string, layout: PanelLayout) => void;
  movePanel: (panelId: string, x: number, y: number) => void;
  resizePanel: (panelId: string, w: number, h: number, x?: number, y?: number) => void;
  selectPanel: (panelId: string | null) => void;
  setEditing: (v: boolean) => void;
  setGenerating: (v: boolean) => void;
  setCanvasZoom: (zoom: number) => void;
  setCanvasResolution: (res: CanvasResolution) => void;
  setTemplates: (templates: DashboardTemplate[]) => void;
  resetDashboard: () => void;
}

export const useDashboardStore = create<DashboardState>((set) => ({
  dashboardConfig: null,
  panels: [],
  selectedPanelId: null,
  isEditing: true,
  isGenerating: false,
  templates: [],
  canvasZoom: 1,
  canvasResolution: { width: 1920, height: 1080 },

  setDashboardConfig: (config) =>
    set({
      dashboardConfig: config,
      panels: config.panels,
      canvasResolution: config.canvasResolution || { width: 1920, height: 1080 },
    }),

  addPanel: (panel) =>
    set((s) => ({ panels: [...s.panels, panel] })),

  removePanel: (panelId) =>
    set((s) => ({
      panels: s.panels.filter((p) => p.id !== panelId),
      selectedPanelId: s.selectedPanelId === panelId ? null : s.selectedPanelId,
    })),

  updatePanel: (panelId, updates) =>
    set((s) => ({
      panels: s.panels.map((p) =>
        p.id === panelId ? { ...p, ...updates } : p
      ),
    })),

  updatePanelLayout: (panelId, layout) =>
    set((s) => ({
      panels: s.panels.map((p) =>
        p.id === panelId ? { ...p, layout } : p
      ),
    })),

  movePanel: (panelId, x, y) =>
    set((s) => ({
      panels: s.panels.map((p) =>
        p.id === panelId ? { ...p, layout: { ...p.layout, x, y } } : p
      ),
    })),

  resizePanel: (panelId, w, h, x, y) =>
    set((s) => ({
      panels: s.panels.map((p) =>
        p.id === panelId
          ? { ...p, layout: { ...p.layout, w, h, ...(x !== undefined ? { x } : {}), ...(y !== undefined ? { y } : {}) } }
          : p
      ),
    })),

  selectPanel: (panelId) => set({ selectedPanelId: panelId }),

  setEditing: (v) => set({ isEditing: v }),

  setGenerating: (v) => set({ isGenerating: v }),

  setCanvasZoom: (zoom) => set({ canvasZoom: zoom }),

  setCanvasResolution: (res) => set({ canvasResolution: res }),

  setTemplates: (templates) => set({ templates }),

  resetDashboard: () =>
    set({
      dashboardConfig: null,
      panels: [],
      selectedPanelId: null,
      canvasZoom: 1,
      canvasResolution: { width: 1920, height: 1080 },
    }),
}));