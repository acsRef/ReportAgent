import type { ReportEntry } from '../types/report'

export function exportReportHTML(report: ReportEntry): void {
  const blocksHTML = report.blocks.map((b) => {
    const data = b.data as Record<string, unknown>
    switch (b.type) {
      case 'insight':
        return `<div style="background:#f8faff;border:1px solid #adc6ff;border-radius:8px;padding:18px 20px;margin-bottom:16px">
          <div style="font-weight:600;color:#1d39c4;margin-bottom:10px">💡 AI 洞察</div>
          <div style="color:#262626;font-size:14px;line-height:1.8">${String(data.content || '')}</div>
        </div>`
      case 'table': {
        const cols = (data.columns as Array<{ key: string; title: string }>) || []
        const rows = (data.rows as Record<string, unknown>[]) || []
        return `<div style="margin-bottom:16px">
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead><tr>${cols.map((c) => `<th style="padding:8px 12px;background:#fafafa;border-bottom:2px solid #e8e8e8;text-align:left;font-weight:600;color:#555;font-size:12px">${c.title || c.key}</th>`).join('')}</tr></thead>
            <tbody>${rows.slice(0, 50).map((r) => `<tr>${cols.map((c) => `<td style="padding:8px 12px;border-bottom:1px solid #f0f0f0">${String(r[c.key] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody>
          </table>
          ${rows.length > 50 ? `<p style="text-align:center;font-size:11px;color:#999;margin-top:8px">仅显示前 50 条，共 ${rows.length} 条</p>` : ''}
        </div>`
      }
      case 'chart':
        return `<div style="margin-bottom:16px;padding:16px;border:1px solid #e8e8e8;border-radius:8px">
          <div style="font-weight:600;margin-bottom:12px">${b.title || '图表'}</div>
          <div style="text-align:center;padding:60px 0;background:#fafafa;border-radius:6px;color:#bbb;font-size:13px">图表 (需在应用中查看)</div>
        </div>`
      default:
        return `<div style="margin-bottom:16px;padding:16px;border:1px solid #e8e8e8;border-radius:8px">${String(data.content || '')}</div>`
    }
  }).join('\n')

  const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>${report.query} - ReportAgent</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:1000px;margin:0 auto;padding:40px 32px;color:#1f2329;line-height:1.6}
h1{font-size:22px;font-weight:600;margin:0 0 8px}
.meta{color:#8f959e;font-size:13px;margin-bottom:32px;display:flex;gap:16px}
hr{border:none;border-top:1px solid #e8e8e8;margin:24px 0}
.footer{text-align:center;color:#bbb;font-size:12px;margin-top:40px}
</style>
</head>
<body>
  <h1>数据分析报告</h1>
  <div class="meta">
    <span>${new Date(report.timestamp).toLocaleString('zh-CN')}</span>
    <span>${report.query}</span>
  </div>
  <hr>
  ${blocksHTML}
  <div class="footer">— ReportAgent AI 自动生成 —</div>
</body>
</html>`

  const blob = new Blob([html], { type: 'text/html;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `report-${report.id.slice(0, 8)}.html`
  a.click()
  URL.revokeObjectURL(url)
}
