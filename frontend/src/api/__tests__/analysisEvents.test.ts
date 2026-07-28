import { describe, expect, it } from 'vitest'
import { parseAnalysisSSEEvent, __test__ } from '../analysisEvents'

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