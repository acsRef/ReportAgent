import { useState, useEffect, useRef } from 'react';
import type { CountUpProps } from '../../types/panel';

export default function CountUp(props: CountUpProps) {
  const { title, value, prefix = '', suffix = '', decimals = 0, duration = 2000, color = 'var(--color-primary)', fontSize = 36 } = props;
  const [display, setDisplay] = useState(0);
  const startRef = useRef<number | null>(null);
  const fromRef = useRef(0);

  useEffect(() => {
    fromRef.current = 0;
    startRef.current = null;

    const animate = (timestamp: number) => {
      if (!startRef.current) startRef.current = timestamp;
      const elapsed = timestamp - startRef.current;
      const progress = Math.min(elapsed / duration, 1);
      // easeOutExpo
      const eased = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress);
      const current = fromRef.current + (value - fromRef.current) * eased;
      setDisplay(current);
      if (progress < 1) requestAnimationFrame(animate);
    };

    requestAnimationFrame(animate);
  }, [value, duration]);

  const formatted = display.toFixed(decimals);

  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 4,
        padding: 'var(--spacing-md)',
      }}
    >
      {title && (
        <div style={{ fontSize: 'var(--font-sm)', color: 'var(--color-text-secondary)', fontWeight: 500 }}>
          {title}
        </div>
      )}
      <div style={{ fontSize, fontWeight: 700, color, letterSpacing: -1, fontFamily: 'var(--font-mono)' }}>
        {prefix}{formatted}{suffix}
      </div>
    </div>
  );
}