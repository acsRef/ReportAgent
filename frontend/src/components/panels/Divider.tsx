import type { DividerProps } from '../../types/panel';

export default function Divider(props: DividerProps) {
  const { type = 'solid', color = 'var(--color-border)', thickness = 1, icon } = props;

  const borderStyle = type === 'gradient'
    ? `linear-gradient(90deg, transparent, ${color}, transparent)`
    : undefined;

  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '0 var(--spacing-md)',
      }}
    >
      <div
        style={{
          flex: 1,
          height: 0,
          borderTop: type === 'gradient' ? `${thickness}px solid transparent` : `${thickness}px ${type} ${color}`,
          background: borderStyle ? borderStyle : undefined,
          backgroundSize: '100% 1px',
          backgroundRepeat: 'no-repeat',
          backgroundPosition: 'center',
        }}
      />
      {icon && (
        <span style={{ padding: '0 var(--spacing-sm)', fontSize: 16, color }}>{icon}</span>
      )}
      {icon && (
        <div
          style={{
            flex: 1,
            height: 0,
            borderTop: type === 'gradient' ? `${thickness}px solid transparent` : `${thickness}px ${type} ${color}`,
            background: borderStyle ? borderStyle : undefined,
            backgroundSize: '100% 1px',
            backgroundRepeat: 'no-repeat',
            backgroundPosition: 'center',
          }}
        />
      )}
    </div>
  );
}