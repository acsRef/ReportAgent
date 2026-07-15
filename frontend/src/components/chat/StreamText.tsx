interface StreamTextProps {
  content: string;
  isStreaming: boolean;
}

export default function StreamText({ content, isStreaming }: StreamTextProps) {
  if (!content && !isStreaming) return null;
  return (
    <span className={isStreaming ? 'typing-cursor' : ''}>
      {content || (isStreaming ? <span style={{ opacity: 0.4 }}>思考中...</span> : null)}
    </span>
  );
}
