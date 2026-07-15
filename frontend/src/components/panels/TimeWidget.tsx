import { useState, useEffect } from 'react';
import type { TimeProps } from '../../types/panel';

function formatTime(format: string): string {
  const now = new Date();
  const pad = (n: number) => n.toString().padStart(2, '0');
  const year = now.getFullYear();
  const month = pad(now.getMonth() + 1);
  const day = pad(now.getDate());
  const hours = pad(now.getHours());
  const minutes = pad(now.getMinutes());
  const seconds = pad(now.getSeconds());

  return format
    .replace('YYYY', String(year))
    .replace('MM', month)
    .replace('DD', day)
    .replace('HH', hours)
    .replace('mm', minutes)
    .replace('ss', seconds);
}

export default function TimeWidget(props: TimeProps) {
  const { format = 'YYYY-MM-DD HH:mm:ss', fontSize = 24, color = '#333', showIcon = true } = props;
  const [time, setTime] = useState(() => formatTime(format));

  useEffect(() => {
    const timer = setInterval(() => setTime(formatTime(format)), 1000);
    return () => clearInterval(timer);
  }, [format]);

  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 'var(--spacing-sm)',
        fontSize,
        color,
        fontWeight: 700,
        fontFamily: 'var(--font-mono)',
        letterSpacing: 2,
      }}
    >
      {showIcon && <span style={{ fontSize: fontSize * 0.8, opacity: 0.6 }}>🕐</span>}
      {time}
    </div>
  );
}