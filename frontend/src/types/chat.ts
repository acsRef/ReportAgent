/** 消息来源 */
export type MessageRole = 'user' | 'assistant';

/** 内容块类型 */
export type ContentBlockType = 'chart' | 'table';

/** 消息中的内容块（图表、表格等富内容） */
export interface ContentBlock {
  type: ContentBlockType;
  data: Record<string, unknown>;
}

/** 单条消息 */
export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  blocks?: ContentBlock[];
  timestamp: number;
  isStreaming?: boolean;
}

/** SSE 事件类型 */
export type SSEEventType = 'token' | 'block' | 'done' | 'error';

/** SSE 事件数据 */
export interface SSEEvent {
  type: SSEEventType;
  data: unknown;
}