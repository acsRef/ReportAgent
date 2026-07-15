import { useDashboardStore } from '../../stores/dashboardStore';
import './DesignCanvas.css';

export default function CanvasToolbar() {
  const zoom = useDashboardStore((s) => s.canvasZoom);
  const setZoom = useDashboardStore((s) => s.setCanvasZoom);
  const res = useDashboardStore((s) => s.canvasResolution);
  const panels = useDashboardStore((s) => s.panels);

  const zoomIn = () => setZoom(Math.min(zoom + 0.1, 3));
  const zoomOut = () => setZoom(Math.max(zoom - 0.1, 0.1));
  const zoomReset = () => setZoom(1);

  return (
    <div className="canvas-toolbar">
      <div className="canvas-toolbar-left">
        <div className="canvas-toolbar-res">
          <span>{res.width}</span>
          <span style={{ color: 'var(--text-muted)' }}>×</span>
          <span>{res.height}</span>
        </div>
      </div>

      <div className="canvas-toolbar-center">
        <div className="canvas-zoom-controls">
          <button className="btn-icon" onClick={zoomOut} title="缩小"
            style={{ background: 'var(--glass-bg)', border: '1px solid var(--glass-border)', borderRadius: 'var(--radius-sm)', width: 28, height: 28 }}>
            −
          </button>
          <span className="canvas-zoom-value" onClick={zoomReset}>{Math.round(zoom * 100)}%</span>
          <button className="btn-icon" onClick={zoomIn} title="放大"
            style={{ background: 'var(--glass-bg)', border: '1px solid var(--glass-border)', borderRadius: 'var(--radius-sm)', width: 28, height: 28 }}>
            +
          </button>
        </div>
      </div>

      <div className="canvas-toolbar-right">
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
          {panels.length} 个组件
        </span>
      </div>
    </div>
  );
}
