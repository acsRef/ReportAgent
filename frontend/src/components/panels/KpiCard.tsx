import type { KpiCardProps } from '../../types/panel';
import './KpiCard.css';

const trendIcons: Record<string, string> = {
  up: '▲',
  down: '▼',
  flat: '―',
};

export default function KpiCard(props: KpiCardProps) {
  const { label, value, unit, trend, trendValue, color = 'var(--accent)', prefix, suffix } = props;

  const displayValue = value ?? '--';

  return (
    <div className="kpi-card">
      <div className="kpi-accent" style={{ background: color }} />
      <div className="kpi-body">
        <div className="kpi-label">{label}</div>
        <div className="kpi-value-row">
          <span className="kpi-value">
            {prefix && <span style={{ fontSize: 'var(--text-lg)', color: 'var(--text-muted)', fontWeight: 600 }}>{prefix}</span>}
            {displayValue}
            {suffix && <span className="kpi-unit">{suffix}</span>}
          </span>
          {unit && <span className="kpi-unit">{unit}</span>}
        </div>
        {trend && (
          <div className={`kpi-trend ${trend}`}>
            {trendIcons[trend]} {trendValue}
          </div>
        )}
      </div>
    </div>
  );
}
