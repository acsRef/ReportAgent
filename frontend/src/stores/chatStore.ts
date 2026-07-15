import { create } from 'zustand';
import type { ChatMessage, ContentBlock } from '../types/chat';

interface ChatState {
  messages: ChatMessage[];
  currentSessionId: string;
  isStreaming: boolean;

  addMessage: (msg: ChatMessage) => void;
  updateLastMessage: (updater: (msg: ChatMessage) => ChatMessage) => void;
  appendToLastMessage: (text: string) => void;
  addBlockToLastMessage: (block: ContentBlock) => void;
  setStreaming: (v: boolean) => void;
  clearMessages: () => void;
  generateSessionId: () => string;
}

function genId(): string {
  return `sess_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  currentSessionId: genId(),
  isStreaming: false,

  addMessage: (msg) =>
    set((s) => ({ messages: [...s.messages, msg] })),

  updateLastMessage: (updater) =>
    set((s) => {
      if (s.messages.length === 0) return s;
      const msgs = [...s.messages];
      msgs[msgs.length - 1] = updater(msgs[msgs.length - 1]);
      return { messages: msgs };
    }),

  appendToLastMessage: (text) =>
    set((s) => {
      if (s.messages.length === 0) return s;
      const msgs = [...s.messages];
      const last = { ...msgs[msgs.length - 1] };
      last.content += text;
      msgs[msgs.length - 1] = last;
      return { messages: msgs };
    }),

  addBlockToLastMessage: (block) =>
    set((s) => {
      if (s.messages.length === 0) return s;
      const msgs = [...s.messages];
      const last = { ...msgs[msgs.length - 1] };
      last.blocks = [...(last.blocks || []), block];
      msgs[msgs.length - 1] = last;
      return { messages: msgs };
    }),

  setStreaming: (v) => set({ isStreaming: v }),

  clearMessages: () => set({ messages: [], currentSessionId: genId() }),

  generateSessionId: () => {
    const id = genId();
    set({ currentSessionId: id });
    return id;
  },
}));