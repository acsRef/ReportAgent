import type { CompactionResult } from '../api/sessionsClient'

/** 手动压缩的结果 → toast 文案（纯函数，便于逐条钉住「不许假装成功」）。 */
export interface CompactToast {
  level: 'success' | 'info'
  text: string
}

const REASON_TEXT: Record<string, string> = {
  ALREADY_COMPACTED: '这个会话的上下文已经整理过了',
  NOTHING_TO_COMPACT: '这段对话还很短，不需要整理',
  NOT_WORTH_IT: '整理不划算（摘要不会比原文更短），已保持原样',
  EMPTY_SESSION: '这个会话还没有对话内容',
}

/**
 * 为什么单独抽出来：后端会**诚实地不压**（收益不划算 / 已压过 / 对话太短），
 * 界面如果一律弹「已整理」就是在骗用户。这里把 `compacted` 与 `reason` 翻成人话。
 */
export function compactResultToast(result: CompactionResult): CompactToast {
  if (result.compacted) {
    return {
      level: 'success',
      text: `已整理上下文：${result.raw_chars} → ${result.context_chars} 字`,
    }
  }
  const reason = result.reason ?? ''
  return {
    level: 'info',
    text: REASON_TEXT[reason] ?? `没有整理（${reason || '未知原因'}）`,
  }
}
