import type { TextProps } from '../../types/panel';

export default function TextBlock(props: TextProps) {
  const { content, fontSize = 14, color = '#333', align = 'left', fontWeight = 'normal' } = props;

  if (!content) return null;

  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        padding: 'var(--spacing-md) var(--spacing-lg)',
        fontSize,
        color,
        textAlign: align,
        fontWeight,
        lineHeight: 1.7,
        overflow: 'auto',
        wordBreak: 'break-word',
      }}
    >
      {content}
    </div>
  );
}