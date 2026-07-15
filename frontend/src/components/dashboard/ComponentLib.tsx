import { useState } from 'react';
import { useDashboardStore } from '../../stores/dashboardStore';
import type { PanelConfig, PanelType } from '../../types/panel';
import './ComponentLib.css';

interface LibItem {
  type: PanelType;
  icon: string;
  name: string;
  category: 'chart' | 'data' | 'deco' | 'media';
  defaultProps: Record<string, unknown>;
  defaultSize: { w: number; h: number };
  badge?: string;
}

const categories = [
  { key: 'chart', label: '图表' },
  { key: 'data',  label: '数据' },
  { key: 'deco',  label: '装饰' },
  { key: 'media', label: '媒体' },
] as const;

const libItems: LibItem[] = [
  { type: 'chart', icon: '📊', name: '柱状图', category: 'chart', defaultProps: { type: 'bar', data: [], dimensions: { x: 'x', y: 'y' } }, defaultSize: { w: 500, h: 350 } },
  { type: 'chart', icon: '📈', name: '折线图', category: 'chart', defaultProps: { type: 'line', data: [], dimensions: { x: 'x', y: 'y' } }, defaultSize: { w: 500, h: 350 } },
  { type: 'chart', icon: '🥧', name: '饼图', category: 'chart', defaultProps: { type: 'pie', data: [], dimensions: { category: 'name', value: 'value' } }, defaultSize: { w: 350, h: 350 } },
  { type: 'chart', icon: '🔵', name: '散点图', category: 'chart', defaultProps: { type: 'scatter', data: [], dimensions: { x: 'x', y: 'y' } }, defaultSize: { w: 500, h: 350 } },
  { type: 'chart', icon: '📉', name: '面积图', category: 'chart', defaultProps: { type: 'area', data: [], dimensions: { x: 'x', y: 'y' } }, defaultSize: { w: 500, h: 350 } },
  { type: 'chart', icon: '🕸️', name: '雷达图', category: 'chart', defaultProps: { type: 'radar', data: [], dimensions: { indicator: 'name', y: 'value' } }, defaultSize: { w: 380, h: 350 } },
  { type: 'chart', icon: '🔺', name: '漏斗图', category: 'chart', defaultProps: { type: 'funnel', data: [], dimensions: { category: 'name', value: 'value' } }, defaultSize: { w: 320, h: 380 } },
  { type: 'gauge',  icon: '🎯', name: '仪表盘', category: 'chart', defaultProps: { value: 65, min: 0, max: 100, unit: '%' }, defaultSize: { w: 280, h: 320 } },
  { type: 'kpi-card',    icon: '◉', name: 'KPI 卡片', category: 'data', defaultProps: { label: '指标名称', value: 0 }, defaultSize: { w: 220, h: 140 } },
  { type: 'table',       icon: '▤', name: '数据表格', category: 'data', defaultProps: { columns: [], data: [] }, defaultSize: { w: 600, h: 380 } },
  { type: 'scroll-table', icon: '≡', name: '滚动表格', category: 'data', defaultProps: { columns: [], data: [] }, defaultSize: { w: 380, h: 340 } },
  { type: 'count-up',     icon: '⊡', name: '数字翻牌', category: 'data', defaultProps: { value: 9999, prefix: '¥', duration: 2000 }, defaultSize: { w: 220, h: 120 } },
  { type: 'progress',     icon: '▰', name: '进度条', category: 'data', defaultProps: { value: 75, max: 100, showLabel: true }, defaultSize: { w: 380, h: 70 } },
  { type: 'text',        icon: 'T', name: '文本', category: 'deco', defaultProps: { content: '文本内容', fontSize: 14 }, defaultSize: { w: 280, h: 80 } },
  { type: 'border-box',  icon: '▢', name: '边框盒子', category: 'deco', defaultProps: { borderType: 'glow', content: '' }, defaultSize: { w: 360, h: 200 }, badge: '装饰' },
  { type: 'divider',     icon: '—', name: '分割线', category: 'deco', defaultProps: { type: 'gradient', color: '#5E6AD2' }, defaultSize: { w: 500, h: 30 } },
  { type: 'time',        icon: '◷', name: '动态时间', category: 'deco', defaultProps: { format: 'YYYY-MM-DD HH:mm:ss', fontSize: 28 }, defaultSize: { w: 340, h: 60 }, badge: '动态' },
  { type: 'image',       icon: '🖼', name: '图片', category: 'media', defaultProps: { url: '', alt: '' }, defaultSize: { w: 280, h: 320 } },
  { type: 'video',       icon: '▶', name: '视频', category: 'media', defaultProps: { url: '', muted: true }, defaultSize: { w: 480, h: 320 } },
  { type: 'iframe',      icon: '⊞', name: '网页嵌入', category: 'media', defaultProps: { url: '', title: '' }, defaultSize: { w: 600, h: 400 } },
];

export default function ComponentLib() {
  const addPanel = useDashboardStore((s) => s.addPanel);
  const selectPanel = useDashboardStore((s) => s.selectPanel);
  const panels = useDashboardStore((s) => s.panels);

  const [search, setSearch] = useState('');
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const filtered = search.trim()
    ? libItems.filter((item) =>
        item.name.includes(search) || item.type.includes(search)
      )
    : libItems;

  const handleAdd = (item: LibItem) => {
    const offset = panels.length * 28;
    const newPanel: PanelConfig = {
      id: `p_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      type: item.type,
      title: item.name,
      props: { ...item.defaultProps },
      layout: {
        x: 60 + (panels.length % 4) * 48,
        y: 40 + offset,
        w: item.defaultSize.w,
        h: item.defaultSize.h,
      },
    };
    addPanel(newPanel);
    selectPanel(newPanel.id);
  };

  const toggle = (key: string) =>
    setCollapsed((p) => ({ ...p, [key]: !p[key] }));

  return (
    <div className="component-lib">
      {/* Search */}
      <div className="component-lib-search-wrap" style={{ position: 'relative' }}>
        <span className="component-lib-search-icon">⌕</span>
        <input
          className="component-lib-search"
          placeholder="搜索组件..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {/* Categories */}
      <div className="component-lib-categories">
        {search.trim() ? (
          <div className="component-items">
            {filtered.map((item) => (
              <div
                key={`${item.type}-${item.name}`}
                data-category={item.category}
                className="component-item"
                onClick={() => handleAdd(item)}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData('application/json', JSON.stringify(item));
                  e.dataTransfer.effectAllowed = 'copy';
                }}
                title={`${item.name} — 拖拽或点击添加`}
              >
                <div className="component-item-icon">{item.icon}</div>
                <span className="component-item-name">{item.name}</span>
                {item.badge && <span className="component-item-badge">{item.badge}</span>}
              </div>
            ))}
            {filtered.length === 0 && (
              <div style={{ gridColumn: '1/-1', textAlign: 'center', padding: '32px 0', color: 'var(--text-muted)', fontSize: 'var(--text-sm)' }}>
                未找到匹配组件
              </div>
            )}
          </div>
        ) : (
          categories.map((cat) => {
            const items = filtered.filter((item) => item.category === cat.key);
            if (items.length === 0) return null;
            const isOpen = !collapsed[cat.key];

            return (
              <div key={cat.key} className="component-category" data-key={cat.key}>
                <div className="component-category-header" onClick={() => toggle(cat.key)}>
                  <span className="component-category-dot" />
                  <span className="component-category-label">{cat.label}</span>
                  <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>{items.length}</span>
                  <span className={`component-category-chevron ${isOpen ? 'open' : ''}`}>▾</span>
                </div>
                {isOpen && (
                  <div className="component-items">
                    {items.map((item) => (
                      <div
                        key={`${item.type}-${item.name}`}
                        data-category={item.category}
                        className="component-item"
                        onClick={() => handleAdd(item)}
                        draggable
                        onDragStart={(e) => {
                          e.dataTransfer.setData('application/json', JSON.stringify(item));
                          e.dataTransfer.effectAllowed = 'copy';
                        }}
                        title={item.name}
                      >
                        <div className="component-item-icon">{item.icon}</div>
                        <span className="component-item-name">{item.name}</span>
                        {item.badge && <span className="component-item-badge">{item.badge}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
