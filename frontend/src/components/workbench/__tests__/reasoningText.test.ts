/** 思考块文案/内容抽取（plan 2026-11-05-reasoning-chain-display）。 */
import { describe, expect, it } from 'vitest'
import {
  formatThoughtDuration,
  looksLikeMarkdown,
  reasoningBlocks,
  thoughtDurationMs,
  thoughtElapsedSeconds,
  thoughtSummary,
} from '../reasoningText'
import type { TimelineEntry } from '../../../types/report'

function entry(
  nodeName: string,
  status: TimelineEntry['status'],
  timestamp: number,
  extra: Partial<TimelineEntry> = {},
): TimelineEntry {
  return { id: `${nodeName}-${timestamp}`, nodeName, status, timestamp, ...extra }
}

describe('formatThoughtDuration — 讲时间不讲步骤数', () => {
  it('不到 1 秒', () => {
    expect(formatThoughtDuration(0)).toBe('不到 1 秒')
    expect(formatThoughtDuration(999)).toBe('不到 1 秒')
  })

  it('秒级：整数不带小数，半秒保留一位', () => {
    expect(formatThoughtDuration(4000)).toBe('4 秒')
    expect(formatThoughtDuration(16_500)).toBe('16.5 秒')
    expect(formatThoughtDuration(1000)).toBe('1 秒')
  })

  it('超过一分钟换成「M 分 S 秒」', () => {
    expect(formatThoughtDuration(80_000)).toBe('1 分 20 秒')
    expect(formatThoughtDuration(60_000)).toBe('1 分 0 秒')
  })

  it('负数按 0 处理（时钟回拨不炸）', () => {
    expect(formatThoughtDuration(-500)).toBe('不到 1 秒')
  })
})

describe('thoughtSummary — 三种状态三句话', () => {
  it('执行中讲「在干什么」，带等待秒数（2026-11-05 D2：让「还在跑」和「卡死」分开）', () => {
    const entries = [
      entry('安全检查', 'success', 1000),
      entry('解析需求', 'running', 2000),
    ]
    expect(thoughtSummary(entries, true)).toBe('正在思考（解析需求）')
    expect(thoughtSummary(entries, true, 12)).toBe('正在思考（解析需求 · 12s）')
    expect(thoughtSummary(entries, true, 0)).toBe('正在思考（解析需求）')
  })

  it('执行中但没有 running 步骤 → 准备中', () => {
    expect(thoughtSummary([entry('安全检查', 'success', 1000)], true)).toBe('正在思考（准备中）')
  })

  it('正常结束讲「想了多久」', () => {
    const entries = [
      entry('解析需求', 'success', 1000),
      entry('生成 SQL', 'success', 17_500),
    ]
    expect(thoughtSummary(entries, false)).toBe('已思考（用时 16.5 秒）')
  })

  it('失败点名断在哪一步并带用时', () => {
    const entries = [
      entry('解析需求', 'success', 1000),
      entry('生成 SQL', 'error', 3000),
    ]
    expect(thoughtSummary(entries, false)).toBe('思考中断于「生成 SQL」（用时 2 秒）')
  })

  it('用户停止不说用时（后台还在跑）', () => {
    const entries = [entry('撰写报告', 'pending', 1000)]
    expect(thoughtSummary(entries, false)).toBe('已停止于「撰写报告」（后台继续）')
  })
})

describe('thoughtDurationMs', () => {
  it('首尾跨度；空列表为 0', () => {
    expect(thoughtDurationMs([entry('a', 'success', 1000), entry('b', 'success', 4000)])).toBe(3000)
    expect(thoughtDurationMs([])).toBe(0)
  })
})

describe('thoughtElapsedSeconds — 执行中「已经等了多久」', () => {
  it('正常秒数', () => {
    expect(thoughtElapsedSeconds(entry('解析需求', 'running', 10_000), 25_400)).toBe(15)
  })

  it('没有 running 步骤 → null', () => {
    expect(thoughtElapsedSeconds(null, 25_400)).toBeNull()
  })

  it('时间戳在未来/过旧 → null（历史数据或时钟错位，不显示荒谬秒数）', () => {
    expect(thoughtElapsedSeconds(entry('解析需求', 'running', 30_000), 25_000)).toBeNull()
    expect(thoughtElapsedSeconds(entry('解析需求', 'running', 1), 1_000_000)).toBeNull()
  })
})

describe('reasoningBlocks — 只取有原文的节点', () => {
  it('按到达顺序返回，空串/纯空白跳过', () => {
    const entries = [
      entry('理解意图 · 思考', 'success', 1000, { reasoning: '  先判断是不是新任务。  ' }),
      entry('解析需求', 'success', 2000),
      entry('规划查询', 'success', 3000, { reasoning: '再决定按区域分组。' }),
      entry('生成 SQL', 'success', 4000, { reasoning: '   ' }),
    ]
    expect(reasoningBlocks(entries)).toEqual([
      { node: '理解意图 · 思考', text: '先判断是不是新任务。' },
      { node: '规划查询', text: '再决定按区域分组。' },
    ])
  })
})

describe('looksLikeMarkdown — 两种 CoT 分开渲染', () => {
  it('纯段落文本（本项目的 deepseek CoT）→ 不是 markdown', () => {
    expect(looksLikeMarkdown('我们只需要输出JSON。用户原话是2024年各区域销售额，这是新任务。')).toBe(false)
  })

  it('加粗 / 标题 / 列表 / 引用 / 行内代码 → 是 markdown', () => {
    expect(looksLikeMarkdown('**关键区分**：配方是设计。')).toBe(true)
    expect(looksLikeMarkdown('## 结论\n先看时间范围。')).toBe(true)
    expect(looksLikeMarkdown('1. 配方\n2. 批次')).toBe(true)
    expect(looksLikeMarkdown('- 配方\n- 批次')).toBe(true)
    expect(looksLikeMarkdown('> 引用一句')).toBe(true)
    expect(looksLikeMarkdown('用 `turn_plan` 字段')).toBe(true)
  })

  it('句中出现星号但不是加粗 → 不算 markdown', () => {
    expect(looksLikeMarkdown('销售额 * 1.13 是含税价')).toBe(false)
  })
})
