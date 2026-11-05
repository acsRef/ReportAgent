/**
 * 思考块的文案与内容抽取（2026-11-05，plan `2026-11-05-reasoning-chain-display.md`）。
 *
 * 用户要求把「思考了 N 步 · 16.5s」换成参考产品那种「已思考（用时 4 秒）」
 * ——说的是**想了多久**，不是走了几步；展开后先读**模型原文思考链路**，
 * 再看执行步骤的结论。纯函数，便于穷举边界。
 */
import type { TimelineEntry } from '../../types/report'

/** 毫秒 → 「不到 1 秒」/「4 秒」/「16.5 秒」/「1 分 20 秒」。 */
export function formatThoughtDuration(ms: number): string {
  const value = Math.max(0, ms)
  if (value < 1000) return '不到 1 秒'
  const seconds = value / 1000
  if (seconds < 60) {
    // 4.0 → "4 秒"，16.5 → "16.5 秒"（参考产品也是这个口径）
    return `${Math.round(seconds * 10) / 10} 秒`
  }
  const total = Math.round(seconds)
  return `${Math.floor(total / 60)} 分 ${total % 60} 秒`
}

/** 这一轮从第一步到最后一步的跨度。 */
export function thoughtDurationMs(entries: TimelineEntry[]): number {
  if (entries.length === 0) return 0
  return Math.max(0, entries[entries.length - 1].timestamp - entries[0].timestamp)
}

/**
 * 折叠头文案。
 *
 * 执行中讲「在干什么」，结束后讲「想了多久」，中途失败/被停止则点名断在哪一步
 * ——三种都是用户当下最想知道的那一句。
 */
export function thoughtSummary(entries: TimelineEntry[], running: boolean): string {
  if (running) {
    const active = [...entries].reverse().find((entry) => entry.status === 'running')
    return `正在思考（${active?.nodeName ?? '准备中'}）`
  }

  const duration = formatThoughtDuration(thoughtDurationMs(entries))
  const failed = entries.find((entry) => entry.status === 'error')
  if (failed) return `思考中断于「${failed.nodeName}」（用时 ${duration}）`
  const stopped = entries.find((entry) => entry.status === 'pending')
  if (stopped) return `已停止于「${stopped.nodeName}」（后台继续）`
  return `已思考（用时 ${duration}）`
}

/** 有原文的节点 → 一条思考链路（按到达顺序）。 */
export interface ReasoningBlock {
  node: string
  text: string
}

export function reasoningBlocks(entries: TimelineEntry[]): ReasoningBlock[] {
  return entries
    .filter((entry): entry is TimelineEntry & { reasoning: string } =>
      typeof entry.reasoning === 'string' && entry.reasoning.trim().length > 0,
    )
    .map((entry) => ({ node: entry.nodeName, text: entry.reasoning.trim() }))
}

/** markdown 痕迹：标题 / 列表 / 引用 / 加粗 / 行内代码。 */
const MARKDOWN_HINT = /(^|\n)\s{0,3}(#{1,6}\s|[-*+]\s|\d+\.\s|>\s)|\*\*[^*\n]+\*\*|`[^`\n]+`/

/**
 * 原文是否按 markdown 渲染。
 *
 * 推理模型给的 CoT 有两种：一种是 markdown（参考截图那种带小标题/编号），
 * 另一种是纯段落文本（本项目 `deepseek-v4-flash` 实测就是 1200 字一坨）。
 * 后者交给 markdown 渲染会把换行吃掉 → 退回 `<p>` + `white-space: pre-wrap`。
 */
export function looksLikeMarkdown(text: string): boolean {
  return MARKDOWN_HINT.test(text)
}
