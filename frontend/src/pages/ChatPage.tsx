import { useCallback, useRef, useEffect } from 'react';
import { useChatStore } from '../stores/chatStore';
import { sendChatStream } from '../services/chatApi';
import ChatMessage from '../components/chat/ChatMessage';
import ChatInput from '../components/chat/ChatInput';
import type { ChatMessage as ChatMessageType, ContentBlock } from '../types/chat';
import './ChatPage.css';

const suggestions = [
  '第一季度各区域销售额排名',
  '分析退货率趋势',
  '哪个品类利润最高？',
  '生成销售看板',
];

function genMsgId(): string {
  return `msg_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
}

export default function ChatPage() {
  const {
    messages,
    isStreaming,
    currentSessionId,
    addMessage,
    appendToLastMessage,
    addBlockToLastMessage,
    setStreaming,
    generateSessionId,
  } = useChatStore();

  const abortRef = useRef<(() => void) | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages]);

  const handleSend = useCallback((text: string) => {
    if (isStreaming) return;

    const sessionId = currentSessionId || generateSessionId();

    const userMsg: ChatMessageType = {
      id: genMsgId(),
      role: 'user',
      content: text,
      timestamp: Date.now(),
    };
    addMessage(userMsg);

    const assistantMsg: ChatMessageType = {
      id: genMsgId(),
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      isStreaming: true,
    };
    addMessage(assistantMsg);
    setStreaming(true);

    const { abort } = sendChatStream(
      { user_query: text, session_id: sessionId },
      {
        onToken: (token) => appendToLastMessage(token),
        onBlock: (block: ContentBlock) => addBlockToLastMessage(block),
        onDone: () => setStreaming(false),
        onError: (error) => {
          appendToLastMessage(`\n\n[错误] ${error}`);
          setStreaming(false);
        },
      }
    );

    abortRef.current = abort;
  }, [isStreaming, currentSessionId, generateSessionId, addMessage, appendToLastMessage, addBlockToLastMessage, setStreaming]);

  return (
    <div className="chat-page">
      {messages.length === 0 ? (
        <div className="chat-empty">
          <div className="chat-empty-icon">◈</div>
          <div className="chat-empty-title">ReportAgent</div>
          <div className="chat-empty-desc">
            用自然语言查询数据、生成报表和分析洞察。输入你的问题，AI 将自动完成查询和可视化。
          </div>
          <div className="chat-suggestions">
            {suggestions.map((s) => (
              <button
                key={s}
                className="chat-suggestion"
                onClick={() => handleSend(s)}
                disabled={isStreaming}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="chat-messages" ref={listRef}>
          {messages.map((msg) => (
            <ChatMessage key={msg.id} message={msg} />
          ))}
        </div>
      )}

      <ChatInput onSend={handleSend} disabled={isStreaming} />
    </div>
  );
}
