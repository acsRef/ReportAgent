import type { BorderBoxProps } from '../../types/panel';
import './BorderBox.css';

export default function BorderBox(props: BorderBoxProps) {
  const { title, content, borderType = 'default', borderColor = 'var(--color-primary)', backgroundColor } = props;

  return (
    <div
      className={`border-box ${borderType}`}
      style={{
        '--border-color': borderColor,
        background: backgroundColor || undefined,
      } as React.CSSProperties}
    >
      {title && <div className="border-box-title">{title}</div>}
      {content && <div className="border-box-content">{content}</div>}
      {!content && !title && (
        <div className="border-box-content" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--color-text-muted)' }}>
          装饰边框
        </div>
      )}
    </div>
  );
}