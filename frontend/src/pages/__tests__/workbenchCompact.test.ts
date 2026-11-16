import { describe, expect, it } from 'vitest'
import { compactResultToast } from '../workbenchCompact'

describe('compactResultToast', () => {
  it('压成功时报压缩前后字数', () => {
    expect(
      compactResultToast({
        compacted: true, reason: null, digest_msg_count: 33, digest_version: 1,
        context_chars: 1500, raw_chars: 4808,
      }),
    ).toEqual({ level: 'success', text: '已整理上下文：4808 → 1500 字' })
  })

  it.each([
    ['ALREADY_COMPACTED', '这个会话的上下文已经整理过了'],
    ['NOTHING_TO_COMPACT', '这段对话还很短，不需要整理'],
    ['NOT_WORTH_IT', '整理不划算（摘要不会比原文更短），已保持原样'],
    ['EMPTY_SESSION', '这个会话还没有对话内容'],
  ])('没压时按 reason 说实话：%s', (reason, text) => {
    expect(
      compactResultToast({
        compacted: false, reason, digest_msg_count: 0, digest_version: 0,
        context_chars: 120, raw_chars: 120,
      }),
    ).toEqual({ level: 'info', text })
  })

  it('未知 reason 不吞', () => {
    expect(
      compactResultToast({
        compacted: false, reason: 'WEIRD', digest_msg_count: 0, digest_version: 0,
        context_chars: 0, raw_chars: 0,
      }).text,
    ).toBe('没有整理（WEIRD）')
  })
})
