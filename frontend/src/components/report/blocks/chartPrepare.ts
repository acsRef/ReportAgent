/**
 * Pure helpers that turn a backend chart payload into render-ready
 * field assignments.
 *
 * The backend chart_advisor emits `{type, config: {data: rows}}` with NO
 * field hints (no xField/yField/dimensions). Without inference the chart
 * falls back to `Object.values(row)[0]` → NaN bars. Rules:
 *   - numeric column  → last one wins as the metric (yField)
 *   - categorical col → last one wins as the category axis (xField)
 *   - a second categorical column becomes the series grouping field
 *     (bar/line only), e.g. rows of 区域×季度 are drawn as one series
 *     per 区域 over the 季度 axis
 * Explicit config.xField/yField always win over inference.
 */

export type ChartType = 'bar' | 'line' | 'pie'

export interface PreparedChart {
  chartType: ChartType
  rows: Record<string, unknown>[]
  xField: string
  yField: string
  seriesField: string | null
}

export function prepareChart(data: Record<string, unknown>): PreparedChart {
  const rawType = String(data.type || 'bar')
  const chartType: ChartType = rawType === 'line' || rawType === 'pie' ? rawType : 'bar'
  const config = (data.config as Record<string, unknown> | undefined) || {}
  const rows =
    ((data.data as Record<string, unknown>[]) ||
      (data.dataset as Record<string, unknown>[]) ||
      (config.data as Record<string, unknown>[])) ??
    []

  let xField = String(config.xField || config.x || '')
  let yField = String(config.yField || config.y || '')
  let seriesField: string | null = null

  if (rows.length > 0 && (!xField || !yField)) {
    const keys = Object.keys(rows[0])
    const isNumericColumn = (key: string) =>
      rows.every((row) => {
        const value = row[key]
        return value !== null && value !== '' && Number.isFinite(Number(value))
      })
    const numericKeys = keys.filter(isNumericColumn)
    const categoricalKeys = keys.filter((key) => !isNumericColumn(key))

    if (!yField) {
      yField = numericKeys[numericKeys.length - 1] ?? keys[keys.length - 1] ?? ''
    }
    if (!xField) {
      xField = categoricalKeys[categoricalKeys.length - 1] ?? keys[0] ?? ''
    }
    if (chartType !== 'pie' && categoricalKeys.length >= 2) {
      seriesField = categoricalKeys.find((key) => key !== xField) ?? null
    }
  }

  return { chartType, rows, xField, yField, seriesField }
}

/** Unique x-axis categories in order of first appearance. */
export function uniqueCategories(rows: Record<string, unknown>[], xField: string): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const row of rows) {
    const key = String(row[xField] ?? '')
    if (!seen.has(key)) {
      seen.add(key)
      out.push(key)
    }
  }
  return out
}
