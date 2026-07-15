import { useState, useRef, useCallback, useEffect } from 'react';
import { useDashboardStore } from '../../stores/dashboardStore';
import PanelRenderer from './PanelRenderer';
import type { PanelConfig } from '../../types/panel';
import './DesignCanvas.css';

type ResizeDir = 'nw' | 'n' | 'ne' | 'w' | 'e' | 'sw' | 's' | 'se';

export default function DesignCanvas() {
  const panels = useDashboardStore((s) => s.panels);
  const selectedPanelId = useDashboardStore((s) => s.selectedPanelId);
  const zoom = useDashboardStore((s) => s.canvasZoom);
  const res = useDashboardStore((s) => s.canvasResolution);
  const selectPanel = useDashboardStore((s) => s.selectPanel);
  const removePanel = useDashboardStore((s) => s.removePanel);
  const addPanel = useDashboardStore((s) => s.addPanel);
  const movePanel = useDashboardStore((s) => s.movePanel);
  const resizePanel = useDashboardStore((s) => s.resizePanel);

  const [isDragging, setIsDragging] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  const dragRef = useRef<{ id: string; startX: number; startY: number; origX: number; origY: number } | null>(null);
  const resizeRef = useRef<{ id: string; dir: ResizeDir; startX: number; startY: number; origL: number; origT: number; origW: number; origH: number } | null>(null);
  const canvasAreaRef = useRef<HTMLDivElement>(null);

  /* ── Mouse move/up handlers for dragging ── */
  useEffect(() => {
    if (!isDragging && !isResizing) return;

    const handleMouseMove = (e: MouseEvent) => {
      if (dragRef.current && isDragging) {
        const { id, startX, startY, origX, origY } = dragRef.current;
        const dx = (e.clientX - startX) / zoom;
        const dy = (e.clientY - startY) / zoom;
        movePanel(id, Math.max(0, origX + dx), Math.max(0, origY + dy));
      }
      if (resizeRef.current && isResizing) {
        const { id, dir, startX, startY, origL, origT, origW, origH } = resizeRef.current;
        const minSize = 30;
        const dx = (e.clientX - startX) / zoom;
        const dy = (e.clientY - startY) / zoom;

        let newX = origL, newY = origT, newW = origW, newH = origH;

        if (dir.includes('e')) newW = Math.max(minSize, origW + dx);
        if (dir.includes('w')) { newW = Math.max(minSize, origW - dx); newX = origL + (origW - newW); }
        if (dir.includes('s')) newH = Math.max(minSize, origH + dy);
        if (dir.includes('n')) { newH = Math.max(minSize, origH - dy); newY = origT + (origH - newH); }

        resizePanel(id, newW, newH, newX, newY);
      }
    };

    const handleMouseUp = () => {
      setIsDragging(false);
      setIsResizing(false);
      dragRef.current = null;
      resizeRef.current = null;
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, isResizing, zoom, movePanel, resizePanel]);

  /* ── Start drag ── */
  const handleComponentMouseDown = useCallback((e: React.MouseEvent, panelId: string) => {
    if ((e.target as HTMLElement).closest('.canvas-component-delete, .resize-handle')) return;
    e.preventDefault();
    selectPanel(panelId);
    const panel = panels.find((p) => p.id === panelId);
    if (!panel) return;
    setIsDragging(true);
    dragRef.current = { id: panelId, startX: e.clientX, startY: e.clientY, origX: panel.layout.x, origY: panel.layout.y };
  }, [panels, selectPanel]);

  /* ── Start resize ── */
  const handleResizeStart = useCallback((e: React.MouseEvent, panelId: string, dir: ResizeDir) => {
    e.preventDefault();
    e.stopPropagation();
    const panel = panels.find((p) => p.id === panelId);
    if (!panel) return;
    setIsResizing(true);
    resizeRef.current = {
      id: panelId, dir, startX: e.clientX, startY: e.clientY,
      origL: panel.layout.x, origT: panel.layout.y,
      origW: panel.layout.w, origH: panel.layout.h,
    };
  }, [panels]);

  /* ── Drop from ComponentLib ── */
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    try {
      const data = JSON.parse(e.dataTransfer.getData('application/json'));
      if (!data.type) return;
      const rect = canvasAreaRef.current?.getBoundingClientRect();
      if (!rect) return;

      const x = Math.max(0, Math.round((e.clientX - rect.left) / zoom - (data.defaultSize?.w || 300) / 2));
      const y = Math.max(0, Math.round((e.clientY - rect.top) / zoom - 20));

      const newPanel: PanelConfig = {
        id: `panel_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
        type: data.type,
        title: data.name,
        props: { ...data.defaultProps },
        layout: {
          x, y,
          w: data.defaultSize?.w || 300,
          h: data.defaultSize?.h || 200,
        },
      };
      addPanel(newPanel);
      selectPanel(newPanel.id);
    } catch { /* ignore invalid drop */ }
  }, [zoom, addPanel, selectPanel]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    // Only set false if leaving the canvas area, not entering a child
    if (e.currentTarget === e.target || !e.currentTarget.contains(e.relatedTarget as Node)) {
      setDragOver(false);
    }
  }, []);

  const handleCanvasClick = useCallback((e: React.MouseEvent) => {
    if (e.target === e.currentTarget || (e.target as HTMLElement).closest('.design-canvas-area')) {
      // Don't deselect on empty area click in the container
    }
  }, []);

  return (
    <div
      className="design-canvas-container"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onClick={handleCanvasClick}
    >
      <div
        ref={canvasAreaRef}
        className={`design-canvas-area ${dragOver ? 'drag-over' : ''}`}
        style={{
          width: res.width,
          height: panels.length === 0 ? 400 : res.height,
          transform: `scale(${zoom})`,
        }}
        onClick={(e) => { e.stopPropagation(); if (panels.length === 0) selectPanel(null); }}
      >
        {/* Empty state overlay */}
        {panels.length === 0 && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 16,
              color: '#bbb',
              zIndex: 1,
              pointerEvents: 'none',
            }}
          >
            <div style={{ fontSize: 48, opacity: 0.5 }}>📋</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: '#999' }}>开始搭建你的看板</div>
            <div style={{ fontSize: 13, textAlign: 'center', maxWidth: 320, lineHeight: 1.6, color: '#aaa' }}>
              从左侧组件库拖拽组件到画布上<br />
              或点击组件添加到看板
            </div>
          </div>
        )}

        {/* Components */}
        {panels.map((panel) => (
          <div
            key={panel.id}
            className={`canvas-component ${selectedPanelId === panel.id ? 'selected' : ''}`}
            style={{
              left: panel.layout.x,
              top: panel.layout.y,
              width: panel.layout.w,
              height: panel.layout.h,
            }}
            onMouseDown={(e) => handleComponentMouseDown(e, panel.id)}
            onClick={(e) => { e.stopPropagation(); selectPanel(panel.id); }}
          >
            <div className="canvas-component-body">
              <PanelRenderer panel={panel} />
            </div>

            {/* Delete button */}
            <button
              className="canvas-component-delete"
              onClick={(e) => { e.stopPropagation(); removePanel(panel.id); }}
            >
              ✕
            </button>

            {/* Resize handles */}
            {selectedPanelId === panel.id && (
              <>
                {(['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se'] as ResizeDir[]).map((dir) => (
                  <div
                    key={dir}
                    className={`resize-handle resize-handle-${dir}`}
                    onMouseDown={(e) => handleResizeStart(e, panel.id, dir)}
                  />
                ))}
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}