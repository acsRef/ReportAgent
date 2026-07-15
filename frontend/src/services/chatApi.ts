import type { ChatRequest } from '../types/api';
import type { ContentBlock } from '../types/chat';

const API_BASE = '/api/v1';

export function sendChatStream(
  request: ChatRequest,
  callbacks: {
    onToken: (token: string) => void;
    onBlock: (block: ContentBlock) => void;
    onDone: () => void;
    onError: (error: string) => void;
  }
): { abort: () => void } {
  const controller = new AbortController();

  fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        callbacks.onError(`请求失败: ${response.status}`);
        return;
      }

      const reader = response.body?.getReader();
      if (!reader) {
        callbacks.onError('无法读取响应流');
        return;
      }

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data: ')) continue;

          try {
            const json = JSON.parse(trimmed.slice(6));
            switch (json.type) {
              case 'token':
                callbacks.onToken(json.data);
                break;
              case 'block':
                callbacks.onBlock(json.data as ContentBlock);
                break;
              case 'done':
                callbacks.onDone();
                break;
              case 'error':
                callbacks.onError(json.data);
                break;
            }
          } catch {
            // skip malformed SSE lines
          }
        }
      }

      callbacks.onDone();
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        callbacks.onError(err.message || '连接异常');
      }
    });

  return { abort: () => controller.abort() };
}