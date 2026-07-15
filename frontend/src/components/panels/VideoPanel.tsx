import type { VideoProps } from '../../types/panel';

export default function VideoPanel(props: VideoProps) {
  const { url, poster, autoplay = false, loop = false, muted = true } = props;

  if (!url) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--color-text-muted)', fontSize: 'var(--font-sm)' }}>
        请输入视频地址
      </div>
    );
  }

  return (
    <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#000', borderRadius: 'var(--radius-md)', overflow: 'hidden' }}>
      <video
        src={url}
        poster={poster}
        autoPlay={autoplay}
        loop={loop}
        muted={muted}
        controls
        playsInline
        style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
      />
    </div>
  );
}