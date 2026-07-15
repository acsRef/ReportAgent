import type { ProgressProps } from '../../types/panel';

export default function ProgressBar(props: ProgressProps) {
  const { title, value, max = 100, showLabel = true, color = 'var(--color-primary)', size = 'md', type = 'line' } = props;

  const pct = Math.min(Math.max(value / max, 0), 1);
  const pctDisplay = Math.round(pct * 100);

  const sizeMap = { sm: 6, md: 10, lg: 16 };

  if (type === 'circle') {
    const radius = 40;
    const circumference = 2 * Math.PI * radius;
    const offset = circumference * (1 - pct);

    return (
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 4, padding: 'var(--spacing-md)' }}>
        {title && <div style={{ fontSize: 'var(--font-sm)', color: 'var(--color-text-secondary)', fontWeight: 500 }}>{title}</div>}
        <svg width="100" height="100" viewBox="0 0 100 100">
          <circle cx="50" cy="50" r={radius} fill="none" stroke="var(--color-border-light)" strokeWidth="8" />
          <circle
            cx="50" cy="50" r={radius}
            fill="none"
            stroke={color}
            strokeWidth="8"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            transform="rotate(-90 50 50)"
            style={{ transition: 'stroke-dashoffset 1s ease' }}
          />
        </svg>
        {showLabel && (
          <div style={{ fontSize: 'var(--font-lg)', fontWeight: 700, color, fontFamily: 'var(--font-mono)' }}>
            {pctDisplay}%
          </div>
        )}
      </div>
    );
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: size === 'sm' ? 2 : 6, padding: '0 var(--spacing-lg)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        {title && <span style={{ fontSize: 'var(--font-sm)', color: 'var(--color-text-secondary)', fontWeight: 500 }}>{title}</span>}
        {showLabel && <span style={{ fontSize: 'var(--font-sm)', color, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>{pctDisplay}%</span>}
      </div>
      <div style={{ height: sizeMap[size], background: 'var(--color-border-light)', borderRadius: sizeMap[size], overflow: 'hidden' }}>
        <div
          style={{
            height: '100%',
            width: `${pct * 100}%`,
            background: `linear-gradient(90deg, ${color}, color-mix(in srgb, ${color} 70%, white))`,
            borderRadius: sizeMap[size],
            transition: 'width 1s ease',
          }}
        />
      </div>
    </div>
  );
}