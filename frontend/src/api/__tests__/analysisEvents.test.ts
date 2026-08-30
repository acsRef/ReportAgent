import { describe, expect, it } from 'vitest'
import { isAnalysisPhase, parseAnalysisSSEEvent, __test__ } from '../analysisEvents'

const { readAnalysisError } = __test__

describe('analysisEvents — error envelope', () => {
  it('reads code / message / recoverable / failed_action for legacy error frames', () => {
    const err = readAnalysisError({
      code: 'QUERY_FAILED',
      message: '查询未返回数据',
      recoverable: true,
      failed_action: 'confirm',
    })
    expect(err).toEqual({
      code: 'QUERY_FAILED',
      message: '查询未返回数据',
      recoverable: true,
      failed_action: 'confirm',
      kind: null,
      sql: null,
    })
  })

  it('forwards structured kind + sql from the SSE helper', () => {
    const err = readAnalysisError({
      code: 'QUERY_TIMEOUT',
      message: '查询超时,请缩小时间范围或维度后重试\n尝试的 SQL: SELECT 1',
      recoverable: true,
      failed_action: 'sql',
      kind: 'timeout',
      sql: 'SELECT 1',
    })
    expect(err?.kind).toBe('timeout')
    expect(err?.sql).toBe('SELECT 1')
  })

  it('ignores unknown kind values (falls back to null, not the raw string)', () => {
    const err = readAnalysisError({
      code: 'X', message: 'x', recoverable: true, failed_action: 'sql',
      kind: 'totally-bogus', sql: 'SELECT 1',
    })
    expect(err?.kind).toBeNull()
    // sql is still preserved (only kind is validated against the enum).
    expect(err?.sql).toBe('SELECT 1')
  })

  it('drops the frame when required fields are missing', () => {
    expect(readAnalysisError({ code: 'X', message: 'x', recoverable: true })).toBeNull()
    expect(readAnalysisError({
      code: 'X', message: 'x', recoverable: true, failed_action: 'bogus',
    })).toBeNull()
  })

  it('parseAnalysisSSEEvent roundtrips a structured error event', () => {
    const evt = parseAnalysisSSEEvent({
      event: 'error',
      data: JSON.stringify({
        code: 'QUERY_OBJECT',
        message: '查询引用的表/列不存在',
        recoverable: true,
        failed_action: 'sql',
        kind: 'object',
        sql: 'SELECT * FROM nope',
      }),
    })
    expect(evt).toEqual({
      type: 'error',
      error: {
        code: 'QUERY_OBJECT',
        message: '查询引用的表/列不存在',
        recoverable: true,
        failed_action: 'sql',
        kind: 'object',
        sql: 'SELECT * FROM nope',
      },
    })
  })
})
describe('analysisEvents — trace progress (P11 kind×status)', () => {
  it('parses a progress trace frame into a TimelineEntry with kind', () => {
    const evt = parseAnalysisSSEEvent({
      event: 'trace',
      data: JSON.stringify({ step: '生成 SQL', status: 'running', detail: '', kind: 'sql' }),
    })
    expect(evt?.type).toBe('trace')
    const entry = (evt as { entry: { nodeName: string; status: string; kind: string } }).entry
    expect(entry.nodeName).toBe('生成 SQL')
    expect(entry.status).toBe('running')
    expect(entry.kind).toBe('sql')
  })

  it('rejects trace frames with unknown status or missing step', () => {
    expect(parseAnalysisSSEEvent({
      event: 'trace', data: JSON.stringify({ step: 'x', status: 'fizzled' }),
    })).toBeNull()
    expect(parseAnalysisSSEEvent({
      event: 'trace', data: JSON.stringify({ status: 'running' }),
    })).toBeNull()
  })

  it('unknown kind falls back to no kind field (still parses)', () => {
    const evt = parseAnalysisSSEEvent({
      event: 'trace',
      data: JSON.stringify({ step: 'x', status: 'success', kind: 'nope' }),
    })
    expect(evt?.type).toBe('trace')
    const entry = (evt as unknown as { entry: Record<string, unknown> }).entry
    expect('kind' in entry).toBe(false)
  })

  it('parses a thinking event with optional text/phase', () => {
    const evt = parseAnalysisSSEEvent({
      event: 'thinking',
      data: JSON.stringify({ phase: 'planning', text: '正在规划查询...' }),
    })
    expect(evt).toEqual({ type: 'thinking', phase: 'planning', text: '正在规划查询...' })
  })
})

describe('analysisEvents — report wire shape (sse-v2, P11 F3)', () => {
  it('parses a full report frame (version/title/answer)', () => {
    const evt = parseAnalysisSSEEvent({
      event: 'report',
      data: JSON.stringify({
        version: 2, parent_version: 1, title: '华东对比', answer: { text: 'x' },
      }),
    })
    expect(evt).toEqual({
      type: 'report',
      report: { version: 2, parent_version: 1, title: '华东对比', answer: { text: 'x' } },
    })
  })

  it('parses a chitchat reply (answer.text only, no version)', () => {
    const evt = parseAnalysisSSEEvent({
      event: 'report',
      data: JSON.stringify({ answer: { text: '你好！' } }),
    })
    expect(evt?.type).toBe('report')
    expect((evt as { report: { version?: number } }).report.version).toBeUndefined()
  })

  it('rejects report frames without an answer object', () => {
    expect(parseAnalysisSSEEvent({
      event: 'report', data: JSON.stringify({ version: 1 }),
    })).toBeNull()
    expect(parseAnalysisSSEEvent({
      event: 'report', data: JSON.stringify({ answer: null }),
    })).toBeNull()
  })
})

describe('isAnalysisPhase', () => {
  it('accepts whitelist members and rejects others', () => {
    expect(isAnalysisPhase('generating')).toBe(true)
    expect(isAnalysisPhase('report_ready')).toBe(true)
    expect(isAnalysisPhase('bogus')).toBe(false)
    expect(isAnalysisPhase(undefined)).toBe(false)
  })
})
