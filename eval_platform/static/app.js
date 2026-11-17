/* AI 评测平台前端（无构建、无依赖）。
 * 三个原则：
 *  1) 服务不可用 = 显示「未启动」，不显示 0 分；
 *  2) deferred / advisory 视觉上与 pass/fail 区分（不给假 pass）；
 *  3) 每条结果都带 环境 / 版本 / 证据档位（可追溯）。
 */
const state = {
  overview: null,
  cases: [],
  selected: null,
  detail: null,
  running: false,
  docs: [],
  docName: null,
};

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function api(path, opts) {
  const resp = await fetch(path, opts);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`${resp.status} ${text.slice(0, 200)}`);
  }
  const ct = resp.headers.get('content-type') || '';
  return ct.includes('json') ? resp.json() : resp.text();
}

/* ---------------- tabs ---------------- */
function initTabs() {
  document.querySelectorAll('#tabs button').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#tabs button').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.tab').forEach((s) => s.classList.remove('active'));
      btn.classList.add('active');
      $(`#tab-${btn.dataset.tab}`).classList.add('active');
      if (btn.dataset.tab === 'history') loadRuns();
      if (btn.dataset.tab === 'method') loadDocs();
    });
  });
}

/* ---------------- health ---------------- */
function pill(name, h) {
  const up = h && h.up;
  return `<span class="pill ${up ? 'up' : 'down'}" title="${esc(h?.detail || '')}">
    ${up ? '●' : '○'} ${esc(name)} ${up ? '在线' : '未启动'}</span>`;
}

async function loadHealth() {
  try {
    const h = await api('/api/health');
    $('#health').innerHTML = pill('ReportAgent :8100', h.reportagent) + pill('ragent :8000', h.ragent);
  } catch (err) {
    $('#health').innerHTML = `<span class="pill down">平台自检失败：${esc(err.message)}</span>`;
  }
}

/* ---------------- overview ---------------- */
async function loadOverview() {
  const o = await api('/api/overview');
  state.overview = o;
  const snap = o.rag_snapshot || {};
  const stats = o.run_stats || {};
  const raVer = o.versions?.reportagent || '?';
  const ragVer = o.versions?.ragent || '?';
  const cards = [
    { h: 'ReportAgent 用例', n: o.cases.reportagent, sub: '字段级期望 · 9 维 · 确定性判定' },
    { h: 'ragent-py 用例', n: o.cases.ragent, sub: `${o.gold_documents} 题带金标文档 · ${o.human_verified} 题人工核验` },
    { h: '风险案例', n: o.risks, sub: '误答 / 幻觉 / 越权 / 错误执行 / 不可追溯' },
    { h: '已跑记录', n: stats.total ?? 0, sub: `pass ${stats.by_verdict?.pass ?? 0} · fail ${stats.by_verdict?.fail ?? 0} · deferred ${stats.by_verdict?.deferred ?? 0}` },
  ];
  $('#tab-overview').innerHTML = `
    <div class="grid">
      ${cards.map((c) => `<div class="card"><h3>${esc(c.h)}</h3><div class="num">${c.n}</div><div class="sub">${esc(c.sub)}</div></div>`).join('')}
    </div>

    <h2 class="section">被测对象与版本</h2>
    <table>
      <tr><th>项目</th><th>角色</th><th>代码版本</th><th>本平台可判到哪一层</th></tr>
      <tr>
        <td>ReportAgent</td><td>Agent / Application Runtime（LangGraph 两图 + 三态落库）</td>
        <td class="mono">${esc(raVer)}</td>
        <td>需求理解层（需求卡字段，确定性）；执行/报告层 <span class="tag deferred">deferred</span></td>
      </tr>
      <tr>
        <td>ragent-py</td><td>Retrieval Runtime（经 MCP 对 ReportAgent 暴露）</td>
        <td class="mono">${esc(ragVer)}</td>
        <td>检索层（金标 doc_id / 证据关键词，确定性）；答案语义 <span class="tag deferred">deferred</span></td>
      </tr>
    </table>

    <h2 class="section">RAG 历史快照（LLM 裁判 0–3 分，仅作背景）</h2>
    ${snap.available ? `
      <div class="grid">
        <div class="card"><h3>题目</h3><div class="num">${snap.questions}</div><div class="sub">已评分 ${snap.scored}</div></div>
        <div class="card"><h3>均分</h3><div class="num">${snap.mean_score ?? '—'}<span class="small muted"> / 3</span></div><div class="sub">归一 ${snap.normalized ?? '—'}%</div></div>
        <div class="card"><h3>证据档位</h3><div class="num small">仅参考</div><div class="sub">LLM 裁判未逐题人工复核</div></div>
      </div>
      <table style="margin-top:14px">
        <tr><th>类别</th><th>题数</th><th>均分</th></tr>
        ${Object.entries(snap.by_category || {}).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.n}</td><td>${v.mean}</td></tr>`).join('')}
      </table>` : '<p class="muted">未找到 <code>eval_results.json</code>。</p>'}

    <h2 class="section">判定层说明（为什么有的格子是 deferred）</h2>
    <table>
      <tr><th>层</th><th>含义</th><th>是否进 pass 率</th></tr>
      <tr><td><span class="tag layer">deterministic</span></td><td>字段/集合/命中，可离线复算</td><td>是</td></tr>
      <tr><td><span class="tag layer">gold</span></td><td>金标（现跑 SQL / 现查 doc_id / 关键词）</td><td>是</td></tr>
      <tr><td><span class="tag deferred">deferred</span></td><td>本轮没覆盖的层（需 confirm、需生成回答）</td><td><b>否</b>，页面显式标注</td></tr>
    </table>`;
  $('#foot-version').textContent = `RA ${raVer} · RAG ${ragVer}`;
}

/* ---------------- live testing ---------------- */
async function loadCases() {
  const project = $('#case-project')?.value || '';
  const q = $('#case-search')?.value || '';
  const params = new URLSearchParams();
  if (project) params.set('project', project);
  if (q) params.set('q', q);
  state.cases = await api(`/api/cases?${params}`);
  renderCaseList();
}

function renderCaseList() {
  const ul = $('#case-list');
  if (!ul) return;
  ul.innerHTML = state.cases.map((c) => `
    <li data-id="${esc(c.id)}" class="${state.selected === c.id ? 'sel' : ''}">
      <span class="cid">${esc(c.project === 'reportagent' ? 'RA' : 'RAG')} · ${esc(c.id)}</span>
      <span class="ct">${esc(c.title.slice(0, 70))}</span>
    </li>`).join('') || '<li class="muted small">没有匹配的用例</li>';
  ul.querySelectorAll('li[data-id]').forEach((li) => {
    li.addEventListener('click', () => selectCase(li.dataset.id));
  });
}

async function selectCase(caseId) {
  state.selected = caseId;
  renderCaseList();
  const c = await api(`/api/cases/${encodeURIComponent(caseId)}`);
  state.detail = c;
  const exp = c.expectations?.requirement ? c.expectations.requirement : null;
  const goldDocs = (c.gold_documents || []).join(', ') || '—';
  const evidence = (c.gold_evidence || []).map((e) => e.keyword).filter(Boolean).join(', ') || '—';
  const projectNote = c.project === 'reportagent'
    ? '实时动作：<code>POST /api/v1/chat</code> → 取 <code>requirement</code> 事件（不 confirm）'
    : '实时动作：<code>POST /api/v1/retrieve</code>（pin 知识库，不生成回答）';
  $('#case-detail').innerHTML = `
    <div class="row">
      <h2 style="margin:0;font-size:16px">${esc(c.title)}</h2>
      <span class="tag tier">${esc(c.tier)}</span>
    </div>
    <p class="small muted mono">${esc(c.project)} / ${esc(c.suite)} / ${esc(c.id)} ${c.category ? '· ' + esc(c.category) : ''}</p>
    <dl class="kv">
      <dt>查询</dt><dd>${esc(c.query)}</dd>
      <dt>${c.project === 'reportagent' ? '需求期望' : '金标文档'}</dt>
      <dd>${c.project === 'reportagent'
        ? `<span class="mono">${esc(JSON.stringify(exp || {}))}</span>`
        : `<span class="mono">${esc(goldDocs)}</span> · 证据关键词：${esc(evidence)}`}</dd>
      <dt>标注来源</dt><dd>${esc(c.gold_source || '人工设计（ReportAgent baseline）')}</dd>
      <dt>实时动作</dt><dd class="small">${projectNote}</dd>
    </dl>
    <div class="row" style="margin-top:14px">
      <button class="btn" id="run-btn">运行这条用例</button>
      <span class="small muted">判定层 deferred 的部分不会计分</span>
    </div>
    <div id="run-out"></div>`;
  $('#run-btn').addEventListener('click', () => runSelected());
}

async function runSelected() {
  if (state.running || !state.selected) return;
  state.running = true;
  const btn = $('#run-btn');
  btn.disabled = true;
  btn.textContent = '运行中…';
  $('#run-out').innerHTML = '<p class="muted small">正在调用被评测服务…</p>';
  try {
    const out = await api(`/api/run?case_id=${encodeURIComponent(state.selected)}`, { method: 'POST' });
    $('#run-out').innerHTML = renderRunOut(out);
  } catch (err) {
    $('#run-out').innerHTML = `<p class="tag fail">运行失败：${esc(err.message)}</p>`;
  } finally {
    state.running = false;
    btn.disabled = false;
    btn.textContent = '运行这条用例';
  }
}

function renderRunOut(out) {
  if (out.status === 'unavailable') {
    return `<div class="card" style="margin-top:14px;border-color:#f2d3d0;background:var(--red-soft)">
      <b>被评测服务不可用</b>
      <p class="small">${esc(out.message)}</p>
      <p class="small muted">${esc(out.hint || '')}</p>
    </div>`;
  }
  const r = out.result;
  const checks = (r.checks || []).map((c) => `
    <tr>
      <td><span class="tag layer">${esc(c.layer)}</span></td>
      <td class="mono">${esc(c.name)}</td>
      <td><span class="tag ${c.status === 'pass' ? 'pass' : c.status === 'fail' ? 'fail' : 'deferred'}">${esc(c.status)}</span></td>
      <td class="small">${esc(JSON.stringify(c.expected ?? ''))}</td>
      <td class="small">${esc(JSON.stringify(c.observed ?? ''))}</td>
      <td class="small muted">${esc(c.detail || '')}</td>
    </tr>`).join('');
  const verdictTag = r.verdict === 'pass' ? 'pass' : r.verdict === 'fail' ? 'fail' : 'deferred';
  return `
    <div class="card" style="margin-top:14px">
      <div class="row">
        <b>结论</b><span class="tag ${verdictTag}">${esc(r.verdict)}</span>
        <span class="small muted">${r.latency_ms ?? '—'} ms · ${esc(r.tier || '')}</span>
        <span class="small mono muted">run ${esc(r.run_id)}</span>
      </div>
      <p class="small muted">进 pass 率：${esc((r.coverage?.graded || []).join(', ') || '—')}　|　未覆盖：${esc((r.coverage?.deferred || []).join(', ') || '—')}</p>
    </div>
    <table style="margin-top:12px">
      <tr><th>层</th><th>check</th><th>结果</th><th>期望</th><th>实测</th><th>说明</th></tr>
      ${checks}
    </table>
    <pre class="block">${esc(JSON.stringify(r.observation, null, 2))}</pre>`;
}

/* ---------------- assets ---------------- */
async function loadAssets() {
  const items = await api('/api/assets');
  $('#tab-assets').innerHTML = `
    <p class="small muted">资产登记是交接的关键：来源 / 覆盖 / 版本 / 复用方式 / 证据档位，一道念完就能接手。</p>
    <table>
      <tr><th>资产</th><th>项目</th><th>用例</th><th>覆盖</th><th>来源与标注</th><th>版本</th><th>复用方式</th><th>证据档位</th></tr>
      ${items.map((a) => `
        <tr>
          <td><b>${esc(a.id)}</b><div class="mono small muted">${esc(a.path)}</div></td>
          <td>${esc(a.project === 'reportagent' ? 'ReportAgent' : 'ragent-py')}</td>
          <td>${a.cases}</td>
          <td class="small">${esc(a.coverage)}</td>
          <td class="small">${esc(a.source)}<div class="muted">${esc(a.labeling)}</div></td>
          <td class="mono small">${esc(a.version)}</td>
          <td class="small">${esc(a.reuse)}</td>
          <td class="small"><span class="tag tier">${esc(a.tier)}</span><div class="muted">${esc(a.maintain)}</div></td>
        </tr>`).join('')}
    </table>`;
}

/* ---------------- risk ---------------- */
async function loadRisk() {
  const items = await api('/api/risk');
  const groups = {};
  items.forEach((r) => { (groups[r.risk] ||= []).push(r); });
  $('#tab-risk').innerHTML = `
    <p class="small muted">边界案例库：ReportAgent 精选（来自 plan 与测试）+ ragent 从题目元数据派生（避开 LLM 裁判）。</p>
    ${Object.entries(groups).map(([risk, rows]) => `
      <h2 class="section">${esc(risk)} <span class="small muted">(${rows.length})</span></h2>
      <table>
        <tr><th>ID</th><th>场景</th><th>触发条件</th><th>证据 / 出处</th><th>现有护栏</th></tr>
        ${rows.map((r) => `<tr>
          <td class="mono small">${esc(r.id)}</td>
          <td>${esc(r.title)}</td>
          <td class="small">${esc(r.trigger)}</td>
          <td class="small">${esc(r.evidence)}</td>
          <td class="small">${esc(r.guard)}</td>
        </tr>`).join('')}
      </table>`).join('')}`;
}

/* ---------------- history ---------------- */
async function loadRuns() {
  const runs = await api('/api/runs?limit=60');
  $('#tab-history').innerHTML = runs.length ? `
    <p class="small muted">每条记录都含：运行环境（base_url / 版本 / 资产清单）、判定层、证据档位——结论能不能复用看这里。</p>
    <table>
      <tr><th>时间(UTC)</th><th>项目</th><th>用例</th><th>结论</th><th>延迟</th><th>run</th></tr>
      ${runs.map((r) => `<tr data-run="${esc(r.run_id)}" class="runrow" style="cursor:pointer">
        <td class="mono small">${esc(r.created_at)}</td>
        <td>${esc(r.project === 'reportagent' ? 'RA' : 'RAG')}</td>
        <td class="small">${esc(r.title?.slice(0, 46) || r.case_id)}</td>
        <td><span class="tag ${r.verdict === 'pass' ? 'pass' : r.verdict === 'fail' ? 'fail' : 'deferred'}">${esc(r.verdict)}</span></td>
        <td class="small">${Math.round(r.latency_ms || 0)} ms</td>
        <td class="mono small">${esc(r.run_id)}</td>
      </tr>`).join('')}
    </table>
    <div id="run-detail"></div>` : '<p class="muted">还没有运行记录。去「实时测试」跑一条。</p>';
  document.querySelectorAll('.runrow').forEach((tr) => {
    tr.addEventListener('click', async () => {
      const row = await api(`/api/runs/${encodeURIComponent(tr.dataset.run)}`);
      $('#run-detail').innerHTML = `
        <h2 class="section">运行详情 ${esc(row.run_id)}</h2>
        <dl class="kv">
          <dt>环境</dt><dd class="mono small">${esc(JSON.stringify(row.env))}</dd>
          <dt>结论 / 档位</dt><dd><span class="tag ${row.payload?.verdict === 'pass' ? 'pass' : 'fail'}">${esc(row.payload?.verdict)}</span> ${esc(row.tier)}</dd>
          <dt>覆盖</dt><dd class="small">进 pass 率：${esc((row.payload?.coverage?.graded || []).join(', '))}　未覆盖：${esc((row.payload?.coverage?.deferred || []).join(', '))}</dd>
        </dl>
        <pre class="block">${esc(JSON.stringify(row.payload?.observation ?? {}, null, 2))}</pre>`;
    });
  });
}

/* ---------------- docs ---------------- */
async function loadDocs() {
  state.docs = await api('/api/docs');
  $('#doc-list').innerHTML = state.docs.map((d) => `
    <li data-doc="${esc(d.name)}" class="${state.docName === d.name ? 'sel' : ''}">
      <span class="ct">${esc(d.title)}</span>
      <span class="cid">${esc(d.name)}.md</span>
    </li>`).join('') || '<li class="muted small">docs/evaluation/ 下还没有文档</li>';
  $('#doc-list').querySelectorAll('li[data-doc]').forEach((li) => {
    li.addEventListener('click', () => openDoc(li.dataset.doc));
  });
  if (!state.docName && state.docs.length) openDoc(state.docs[0].name);
}

async function openDoc(name) {
  state.docName = name;
  const md = await api(`/api/docs/${encodeURIComponent(name)}`);
  $('#doc-body').innerHTML = renderMarkdown(md);
  loadDocsHighlight();
}

function loadDocsHighlight() {
  document.querySelectorAll('#doc-list li[data-doc]').forEach((li) => {
    li.classList.toggle('sel', li.dataset.doc === state.docName);
  });
}

/* 极简 Markdown 渲染（表格 / 列表 / 代码块 / 标题 / 粗体 / 行内代码 / 链接） */
function renderMarkdown(md) {
  const lines = String(md).split(/\r?\n/);
  const out = [];
  let inCode = false;
  let listType = null;
  let tableBuf = [];
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const flushTable = () => {
    if (tableBuf.length < 2) { tableBuf = []; return; }
    const rows = tableBuf.filter((r) => !/^\|[\s:|-]+\|$/.test(r));
    const cells = (r) => r.replace(/^\||\|$/g, '').split('|').map((x) => x.trim());
    const head = cells(rows[0]);
    const body = rows.slice(1).map(cells);
    out.push(`<table><tr>${head.map((h) => `<th>${inline(h)}</th>`).join('')}</tr>${body.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</table>`);
    tableBuf = [];
  };
  const closeList = () => { if (listType) { out.push(`</${listType}>`); listType = null; } };
  for (const line of lines) {
    if (line.startsWith('```')) {
      closeList(); flushTable();
      if (!inCode) { out.push('<pre>'); inCode = true; } else { out.push('</pre>'); inCode = false; }
      continue;
    }
    if (inCode) { out.push(esc(line).replace(/`/g, '&#96;')); continue; }
    if (/^\|/.test(line)) { closeList(); tableBuf.push(line); continue; }
    flushTable();
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { closeList(); const lv = h[1].length; out.push(`<h${lv}>${inline(h[2])}</h${lv}>`); continue; }
    if (/^>\s?/.test(line)) { closeList(); out.push(`<blockquote>${inline(line.replace(/^>\s?/, ''))}</blockquote>`); continue; }
    const ul = line.match(/^[-*]\s+(.*)$/);
    if (ul) {
      flushTable();
      if (listType !== 'ul') { closeList(); out.push('<ul>'); listType = 'ul'; }
      out.push(`<li>${inline(ul[1])}</li>`);
      continue;
    }
    const ol = line.match(/^\d+\.\s+(.*)$/);
    if (ol) {
      flushTable();
      if (listType !== 'ol') { closeList(); out.push('<ol>'); listType = 'ol'; }
      out.push(`<li>${inline(ol[1])}</li>`);
      continue;
    }
    closeList();
    if (!line.trim()) { continue; }
    if (/^---+$/.test(line.trim())) { out.push('<hr>'); continue; }
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList(); flushTable();
  if (inCode) out.push('</pre>');
  return out.join('\n');
}

/* ---------------- boot ---------------- */
(async function boot() {
  initTabs();
  await loadHealth();
  try { await loadOverview(); } catch (err) { $('#tab-overview').innerHTML = `<p class="tag fail">${esc(err.message)}</p>`; }
  try { await loadCases(); } catch (err) { /* 后端没起时静默 */ }
  try { await loadAssets(); } catch (err) { /* ignore */ }
  try { await loadRisk(); } catch (err) { /* ignore */ }
  $('#case-project').addEventListener('change', loadCases);
  $('#case-search').addEventListener('input', () => { clearTimeout(window.__t); window.__t = setTimeout(loadCases, 200); });
  setInterval(loadHealth, 15000);
})();
