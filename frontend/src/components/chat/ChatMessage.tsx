import type { ChatMessage as ChatMessageType } from '../../types/chat';
import type { ChartProps, TableProps } from '../../types/panel';
import StreamText from './StreamText';
import ChartPanel from '../panels/ChartPanel';
import TablePanel from '../panels/TablePanel';
import './ChatMessage.css';

interface ChatMessageProps {
  message: ChatMessageType;
}

export default function ChatMessage({ message }: ChatMessageProps) {
  const { role, content, blocks, timestamp, isStreaming } = message;

  const userIcons: Record<string, string> = {
    user: '👤',
    assistant: '🤖',
  };

  const timeStr = new Date(timestamp).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  });

  return (
    <div className={`chat-message ${role}`}>
      <div className="chat-avatar">{userIcons[role]}</div>

      <div className="chat-bubble-wrap">
        {content || isStreaming ? (
          <div className="chat-bubble">
            <StreamText content={content} isStreaming={!!isStreaming} />
          </div>
        ) : null}

        {blocks && blocks.length > 0 && (
          <div className="chat-blocks">
            {blocks.map((block, i) => (
              <div key={i} className="chat-block animate-fade-in">
                {block.type === 'chart' && (
                  <ChartPanel {...(block.data as unknown as ChartProps)} />
                )}
                {block.type === 'table' && (
                  <TablePanel {...(block.data as unknown as TableProps)} />
                )}
              </div>
            ))}
          </div>
        )}

        <div className="chat-time">{timeStr}</div>
      </div>
    </div>
  );
}
