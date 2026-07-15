import { useState } from 'react';
import type { ImageProps } from '../../types/panel';

export default function ImagePanel(props: ImageProps) {
  const { url, alt = '', borderRadius = 8, objectFit = 'contain' } = props;
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);

  if (!url) {
    return (
      <div
        style={{
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--color-text-muted)',
          fontSize: 'var(--font-sm)',
        }}
      >
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 32, marginBottom: 8 }}>🖼️</div>
          <div>输入图片地址</div>
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 'var(--spacing-sm)',
        overflow: 'hidden',
      }}
    >
      {!loaded && !error && (
        <div
          className="skeleton"
          style={{
            width: '80%',
            height: '80%',
            borderRadius: 'var(--radius-md)',
          }}
        />
      )}
      {error ? (
        <div
          style={{
            color: 'var(--color-text-muted)',
            fontSize: 'var(--font-sm)',
            textAlign: 'center',
          }}
        >
          <div style={{ fontSize: 32, marginBottom: 8 }}>🖼️</div>
          <div>图片加载失败</div>
        </div>
      ) : (
        <img
          src={url}
          alt={alt}
          onLoad={() => setLoaded(true)}
          onError={() => setError(true)}
          style={{
            maxWidth: '100%',
            maxHeight: '100%',
            objectFit,
            borderRadius,
            display: loaded ? 'block' : 'none',
            boxShadow: 'var(--shadow-sm)',
          }}
        />
      )}
    </div>
  );
}