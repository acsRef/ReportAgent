import { useState } from 'react';
import type { IframeProps } from '../../types/panel';

export default function IframePanel(props: IframeProps) {
  const { url, title } = props;
  const [error, setError] = useState(false);

  if (!url) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--color-text-muted)', fontSize: 'var(--font-sm)' }}>
        请输入网页地址
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--color-text-muted)', fontSize: 'var(--font-sm)' }}>
        <span>无法加载: {url}</span>
      </div>
    );
  }

  return (
    <iframe
      title={title || '嵌入页面'}
      src={url}
      style={{ width: '100%', height: '100%', border: 'none' }}
      onError={() => setError(true)}
      sandbox="allow-scripts allow-same-origin"
    />
  );
}