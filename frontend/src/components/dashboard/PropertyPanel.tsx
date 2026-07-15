import { useState, useCallback } from 'react';
import { useDashboardStore } from '../../stores/dashboardStore';
import type { PanelConfig } from '../../types/panel';
import './PropertyPanel.css';

/* ── Color Presets ── */
const COLOR_PRESETS = [
  '#1677ff', '#4096ff', '#69b1ff', '#91caff', '#bae0ff',
  '#52c41a', '#73d13d', '#95de64', '#b7eb8f', '#d9f7be',
  '#faad14', '#ffc53d', '#ffd666', '#ffe58f', '#fff1b8',
  '#ff4d4f', '#ff7875', '#ffa39e', '#ffccc7', '#fff1f0',
  '#722ed1', '#9254de', '#b37feb', '#d3adf7', '#efdbff',
  '#13c2c2', '#36cfc9', '#5cdbd3', '#87e8de', '#b5f5ec',
];

interface CollapsibleGroupProps {
  title: string;
  icon?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

function CollapsibleGroup({ title, icon, defaultOpen = true, children }: CollapsibleGroupProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="property-group">
      <div className="property-group-header" onClick={() => setOpen(!open)}>
        <div className="property-group-title">{icon && <span>{icon}</span>}{title}</div>
        <span className={`property-group-toggle ${open ? 'open' : 'closed'}`}>▼</span>
      </div>
      {open && <div className="property-group-body">{children}</div>}
    </div>
  );
}

interface ColorPickerProps {
  label: string;
  value?: string;
  onChange: (color: string | undefined) => void;
  allowNone?: boolean;
}

function ColorPicker({ label, value, onChange, allowNone = true }: ColorPickerProps) {
  const [hex, setHex] = useState(value || '');

  return (
    <div className="property-field">
      <label>{label}</label>
      <div className="color-picker-row">
        <input
          type="color"
          className="color-picker-input"
          value={value || '#1677ff'}
          onChange={(e) => { onChange(e.target.value); setHex(e.target.value); }}
        />
        <input
          className="color-hex-input"
          value={hex}
          onChange={(e) => setHex(e.target.value)}
          onBlur={() => { if (hex) onChange(hex); }}
          placeholder="#000000"
        />
        {allowNone && value && (
          <button
            style={{ fontSize: 12, color: 'var(--color-text-muted)', padding: '4px 8px', whiteSpace: 'nowrap' }}
            onClick={() => { onChange(undefined); setHex(''); }}
          >
            清除
          </button>
        )}
      </div>
      <div className="color-swatches">
        {COLOR_PRESETS.map((c) => (
          <div
            key={c}
            className={`color-swatch ${value === c ? 'active' : ''}`}
            style={{ background: c }}
            onClick={() => { onChange(c); setHex(c); }}
            title={c}
          />
        ))}
      </div>
    </div>
  );
}

/* ── Props Editor by Component Type ── */
function KpiCardEditor(props: Record<string, unknown>, onChange: (key: string, v: unknown) => void) {
  return (
    <>
      <div className="property-field">
        <label>指标名</label>
        <input value={(props.label as string) || ''} onChange={(e) => onChange('label', e.target.value)} placeholder="销售额" />
      </div>
      <div className="property-field">
        <label>数值</label>
        <input value={String(props.value ?? '')} onChange={(e) => onChange('value', e.target.value)} />
      </div>
      <div className="property-field-row">
        <div className="property-field" style={{ flex: 1 }}>
          <label>单位</label>
          <input value={(props.unit as string) || ''} onChange={(e) => onChange('unit', e.target.value)} placeholder="元" />
        </div>
        <div className="property-field" style={{ flex: 1 }}>
          <label>前缀</label>
          <input value={(props.prefix as string) || ''} onChange={(e) => onChange('prefix', e.target.value)} placeholder="¥" />
        </div>
      </div>
      <div className="property-field">
        <label>趋势</label>
        <div className="trend-buttons">
          {(['up', 'down', 'flat', undefined] as const).map((t) => (
            <button
              key={t || 'none'}
              className={`trend-btn ${props.trend === t ? t ? `active-${t}` : 'active-flat' : ''}`}
              onClick={() => onChange('trend', t)}
            >
              {t === 'up' ? '▲ 上升' : t === 'down' ? '▼ 下降' : t === 'flat' ? '― 持平' : '无'}
            </button>
          ))}
        </div>
      </div>
      {(props.trend && props.trend !== 'flat') && (
        <div className="property-field">
          <label>趋势值</label>
          <input value={(props.trendValue as string) || ''} onChange={(e) => onChange('trendValue', e.target.value)} placeholder="+12.5%" />
        </div>
      )}
    </>
  );
}

function ChartEditor(props: Record<string, unknown>, onChange: (key: string, v: unknown) => void) {
  return (
    <>
      <div className="property-field">
        <label>图表类型</label>
        <select value={(props.type as string) || 'bar'} onChange={(e) => onChange('type', e.target.value)}>
          <option value="bar">柱状图</option>
          <option value="line">折线图</option>
          <option value="pie">饼图</option>
          <option value="area">面积图</option>
          <option value="scatter">散点图</option>
          <option value="radar">雷达图</option>
          <option value="funnel">漏斗图</option>
        </select>
      </div>
      <div className="property-field">
        <label>标题</label>
        <input value={(props.title as string) || ''} onChange={(e) => onChange('title', e.target.value)} placeholder="图表标题" />
      </div>
      <div className="property-field-row">
        <div className="property-field" style={{ flex: 1 }}>
          <label>X 轴字段</label>
          <input value={((props.dimensions as any)?.x as string) || ''} onChange={(e) => onChange('dimensions', { ...(props.dimensions as any), x: e.target.value })} placeholder="x" />
        </div>
        <div className="property-field" style={{ flex: 1 }}>
          <label>Y 轴字段</label>
          <input value={((props.dimensions as any)?.y as string) || ''} onChange={(e) => onChange('dimensions', { ...(props.dimensions as any), y: e.target.value })} placeholder="y" />
        </div>
      </div>
    </>
  );
}

function TableEditor(props: Record<string, unknown>, onChange: (key: string, v: unknown) => void) {
  const cols = (props.columns as Array<{key: string; title: string}>) || [];
  return (
    <>
      <div className="property-field">
        <label>标题</label>
        <input value={(props.title as string) || ''} onChange={(e) => onChange('title', e.target.value)} placeholder="表格标题" />
      </div>
      <div className="property-field">
        <label>列数</label>
        <input value={cols.length} disabled />
      </div>
      <div className="property-field">
        <label>每页行数</label>
        <input type="number" value={(props.pageSize as number) || 10} onChange={(e) => onChange('pageSize', Number(e.target.value))} />
      </div>
    </>
  );
}

function TextEditor(props: Record<string, unknown>, onChange: (key: string, v: unknown) => void) {
  return (
    <>
      <div className="property-field">
        <label>内容</label>
        <textarea value={(props.content as string) || ''} onChange={(e) => onChange('content', e.target.value)} placeholder="文本内容" />
      </div>
      <div className="property-field-row">
        <div className="property-field" style={{ flex: 1 }}>
          <label>字号</label>
          <input type="number" value={(props.fontSize as number) || 14} onChange={(e) => onChange('fontSize', Number(e.target.value))} />
        </div>
        <div className="property-field" style={{ flex: 1 }}>
          <label>字重</label>
          <select value={(props.fontWeight as string) || 'normal'} onChange={(e) => onChange('fontWeight', e.target.value)}>
            <option value="normal">常规</option>
            <option value="bold">加粗</option>
            <option value="lighter">细体</option>
          </select>
        </div>
      </div>
      <div className="property-field">
        <label>对齐</label>
        <div className="align-buttons">
          {(['left', 'center', 'right'] as const).map((a) => (
            <button key={a} className={`align-btn ${props.align === a ? 'active' : ''}`} onClick={() => onChange('align', a)}>
              {a === 'left' ? '⫷' : a === 'center' ? '⫿' : '⫸'}
            </button>
          ))}
        </div>
      </div>
    </>
  );
}

function GaugeEditor(props: Record<string, unknown>, onChange: (key: string, v: unknown) => void) {
  return (
    <>
      <div className="property-field">
        <label>标题</label>
        <input value={(props.title as string) || ''} onChange={(e) => onChange('title', e.target.value)} placeholder="仪表盘标题" />
      </div>
      <div className="property-field">
        <label>数值</label>
        <input type="number" value={Number(props.value) || 0} onChange={(e) => onChange('value', Number(e.target.value))} />
      </div>
      <div className="property-field-row">
        <div className="property-field" style={{ flex: 1 }}>
          <label>最小值</label>
          <input type="number" value={Number(props.min) || 0} onChange={(e) => onChange('min', Number(e.target.value))} />
        </div>
        <div className="property-field" style={{ flex: 1 }}>
          <label>最大值</label>
          <input type="number" value={Number(props.max) || 100} onChange={(e) => onChange('max', Number(e.target.value))} />
        </div>
      </div>
      <div className="property-field">
        <label>单位</label>
        <input value={(props.unit as string) || ''} onChange={(e) => onChange('unit', e.target.value)} placeholder="%" />
      </div>
    </>
  );
}

function ThemeEditor(panel: PanelConfig, updatePanel: (id: string, upd: Partial<PanelConfig>) => void) {
  return (
    <CollapsibleGroup title="主题" icon="🎨">
      <ColorPicker
        label="组件主色"
        value={(panel.props as any).color}
        onChange={(c) => updatePanel(panel.id, { props: { ...panel.props, color: c } })}
      />
    </CollapsibleGroup>
  );
}

function SizeEditor(panel: PanelConfig, updatePanel: (id: string, upd: Partial<PanelConfig>) => void) {
  return (
    <CollapsibleGroup title="尺寸与位置" icon="📐">
      <div className="size-inputs">
        <div className="size-input-group">
          <label>宽度 (w)</label>
          <input
            type="number"
            className="small-input"
            value={panel.layout.w}
            min={1}
            max={12}
            onChange={(e) => updatePanel(panel.id, { layout: { ...panel.layout, w: Number(e.target.value) } })}
          />
        </div>
        <div className="size-input-group">
          <label>高度 (h)</label>
          <input
            type="number"
            className="small-input"
            value={panel.layout.h}
            min={1}
            onChange={(e) => updatePanel(panel.id, { layout: { ...panel.layout, h: Number(e.target.value) } })}
          />
        </div>
      </div>
      <div className="size-inputs" style={{ marginTop: 4 }}>
        <div className="size-input-group">
          <label>X 位置</label>
          <input type="number" className="small-input" value={panel.layout.x} disabled />
        </div>
        <div className="size-input-group">
          <label>Y 位置</label>
          <input type="number" className="small-input" value={panel.layout.y} disabled />
        </div>
      </div>
      <div className="property-field horizontal" style={{ marginTop: 4 }}>
        <label>宽 (px)</label>
        <input type="number" className="small-input" value={panel.layout.w} disabled />
      </div>
    </CollapsibleGroup>
  );
}

/* ── Main Component ── */
export default function PropertyPanel() {
  const panels = useDashboardStore((s) => s.panels);
  const selectedPanelId = useDashboardStore((s) => s.selectedPanelId);
  const updatePanel = useDashboardStore((s) => s.updatePanel);

  const selected = panels.find((p) => p.id === selectedPanelId) || null;

  const handleChange = useCallback((key: string, value: unknown) => {
    if (!selected) return;
    updatePanel(selected.id, { props: { ...selected.props, [key]: value } } as Partial<PanelConfig>);
  }, [selected, updatePanel]);

  if (!selected) {
    return (
      <div className="property-panel">
        <div className="property-panel-title">属性</div>
        <div className="property-panel-scroll">
          <div className="property-panel-empty">
            <div className="property-panel-empty-icon">👆</div>
            <div>点击画布中的组件编辑属性</div>
            <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-text-muted)' }}>
              也可以右键组件查看更多操作
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="property-panel">
      <div className="property-panel-title">
        <span>属性</span>
        <span className="property-panel-title-type">{selected.type}</span>
      </div>

      <div className="property-panel-scroll">
        {/* Basic info */}
        <CollapsibleGroup title="基本信息" icon="ℹ️">
          <div className="property-field">
            <label>标题</label>
            <input value={selected.title || ''} onChange={(e) => updatePanel(selected.id, { title: e.target.value })} placeholder="组件标题" />
          </div>
        </CollapsibleGroup>

        {/* Size & Position */}
        {SizeEditor(selected, updatePanel)}

        {/* Component-specific props */}
        <CollapsibleGroup title="内容配置" icon="⚙️">
          {selected.type === 'kpi-card' && KpiCardEditor(selected.props as Record<string, unknown>, handleChange)}
          {selected.type === 'chart' && ChartEditor(selected.props as Record<string, unknown>, handleChange)}
          {selected.type === 'table' && TableEditor(selected.props as Record<string, unknown>, handleChange)}
          {selected.type === 'text' && TextEditor(selected.props as Record<string, unknown>, handleChange)}
          {selected.type === 'gauge' && GaugeEditor(selected.props as Record<string, unknown>, handleChange)}
          {selected.type === 'scroll-table' && (
            <div className="property-field">
              <label>滚动速度 (ms)</label>
              <div className="slider-row">
                <input type="range" min={2000} max={20000} step={500} value={(selected.props as any).speed || 8000}
                  onChange={(e) => handleChange('speed', Number(e.target.value))} />
                <span className="slider-value">{(selected.props as any).speed || 8000}</span>
              </div>
            </div>
          )}
          {selected.type === 'border-box' && (
            <div className="property-field">
              <label>边框样式</label>
              <select value={(selected.props as any).borderType || 'default'} onChange={(e) => handleChange('borderType', e.target.value)}>
                <option value="default">实线边框</option>
                <option value="glow">发光边框</option>
                <option value="dashed">虚线边框</option>
                <option value="gradient">渐变色边框</option>
              </select>
            </div>
          )}
          {selected.type === 'divider' && (
            <div className="property-field">
              <label>分割线样式</label>
              <select value={(selected.props as any).type || 'solid'} onChange={(e) => handleChange('type', e.target.value)}>
                <option value="solid">实线</option>
                <option value="dashed">虚线</option>
                <option value="dotted">点线</option>
                <option value="gradient">渐变</option>
              </select>
            </div>
          )}
          {selected.type === 'time' && (
            <div className="property-field">
              <label>时间格式</label>
              <select value={(selected.props as any).format || 'YYYY-MM-DD HH:mm:ss'} onChange={(e) => handleChange('format', e.target.value)}>
                <option value="YYYY-MM-DD HH:mm:ss">完整日期时间</option>
                <option value="HH:mm:ss">仅时间</option>
                <option value="YYYY-MM-DD">仅日期</option>
                <option value="YYYY年MM月DD日">中文日期</option>
              </select>
            </div>
          )}
          {selected.type === 'count-up' && (
            <>
              <div className="property-field">
                <label>数值</label>
                <input type="number" value={(selected.props as any).value || 0} onChange={(e) => handleChange('value', Number(e.target.value))} />
              </div>
              <div className="property-field-row">
                <div className="property-field" style={{ flex: 1 }}>
                  <label>前缀</label>
                  <input value={(selected.props as any).prefix || ''} onChange={(e) => handleChange('prefix', e.target.value)} />
                </div>
                <div className="property-field" style={{ flex: 1 }}>
                  <label>后缀</label>
                  <input value={(selected.props as any).suffix || ''} onChange={(e) => handleChange('suffix', e.target.value)} />
                </div>
              </div>
              <div className="property-field">
                <label>动画时长 (ms)</label>
                <div className="slider-row">
                  <input type="range" min={500} max={5000} step={100} value={(selected.props as any).duration || 2000}
                    onChange={(e) => handleChange('duration', Number(e.target.value))} />
                  <span className="slider-value">{(selected.props as any).duration || 2000}</span>
                </div>
              </div>
            </>
          )}
          {selected.type === 'progress' && (
            <>
              <div className="property-field-row">
                <div className="property-field" style={{ flex: 1 }}>
                  <label>数值</label>
                  <input type="number" value={(selected.props as any).value || 0} onChange={(e) => handleChange('value', Number(e.target.value))} />
                </div>
                <div className="property-field" style={{ flex: 1 }}>
                  <label>最大值</label>
                  <input type="number" value={(selected.props as any).max || 100} onChange={(e) => handleChange('max', Number(e.target.value))} />
                </div>
              </div>
              <div className="property-field">
                <label>样式</label>
                <select value={(selected.props as any).type || 'line'} onChange={(e) => handleChange('type', e.target.value)}>
                  <option value="line">条形</option>
                  <option value="circle">圆形</option>
                </select>
              </div>
            </>
          )}
          {selected.type === 'iframe' && (
            <div className="property-field">
              <label>网页地址</label>
              <input value={(selected.props as any).url || ''} onChange={(e) => handleChange('url', e.target.value)} placeholder="https://..." />
            </div>
          )}
          {selected.type === 'video' && (
            <div className="property-field">
              <label>视频地址</label>
              <input value={(selected.props as any).url || ''} onChange={(e) => handleChange('url', e.target.value)} placeholder="https://..." />
            </div>
          )}
        </CollapsibleGroup>

        {/* Theme - color */}
        {ThemeEditor(selected, updatePanel)}

        {/* Color props for components that support it */}
        {selected.type === 'text' && (
          <CollapsibleGroup title="文字颜色" icon="🎨">
            <ColorPicker
              label="颜色"
              value={(selected.props as any).color}
              onChange={(c) => handleChange('color', c)}
            />
          </CollapsibleGroup>
        )}
        {selected.type === 'divider' && (
          <CollapsibleGroup title="线条颜色" icon="🎨">
            <ColorPicker
              label="颜色"
              value={(selected.props as any).color}
              onChange={(c) => handleChange('color', c)}
            />
          </CollapsibleGroup>
        )}
        {selected.type === 'count-up' && (
          <CollapsibleGroup title="数字颜色" icon="🎨">
            <ColorPicker
              label="颜色"
              value={(selected.props as any).color}
              onChange={(c) => handleChange('color', c)}
            />
          </CollapsibleGroup>
        )}
        {selected.type === 'border-box' && (
          <CollapsibleGroup title="边框颜色" icon="🎨">
            <ColorPicker
              label="颜色"
              value={(selected.props as any).borderColor}
              onChange={(c) => handleChange('borderColor', c)}
            />
          </CollapsibleGroup>
        )}
        {selected.type === 'progress' && (
          <CollapsibleGroup title="进度条颜色" icon="🎨">
            <ColorPicker
              label="颜色"
              value={(selected.props as any).color}
              onChange={(c) => handleChange('color', c)}
            />
          </CollapsibleGroup>
        )}
      </div>
    </div>
  );
}