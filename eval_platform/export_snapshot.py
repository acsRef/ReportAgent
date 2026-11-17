"""把平台数据导出成**静态展示页**（博客 `/eval/`，自包含 HTML，无外部依赖）。

用法（平台在跑时）：

    python -m eval_platform.export_snapshot --out-html ../acsRef.github.io/source/eval/index.html \
                                            --out-json eval_platform/assets/snapshot.json

为什么单独导出：博客是静态站，不能在浏览器里跑 FastAPI；但**快照 + 证据档位**才是最该被看到的东西，
所以这里只导出"当前事实"（资产/风险库/最近运行/判定层/证据分量），并标注导出时间与版本。
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

PLATFORM = "http://127.0.0.1:8200"


async def collect(base: str, runs_limit: int = 30) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        overview = (await client.get(f"{base}/api/overview")).json()
        assets = (await client.get(f"{base}/api/assets")).json()
        risk = (await client.get(f"{base}/api/risk")).json()
        scenarios = (await client.get(f"{base}/api/scenarios")).json()
        runs = (await client.get(f"{base}/api/runs?limit={runs_limit}")).json()
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "overview": overview,
        "assets": assets,
        "risk": risk,
        "scenarios": scenarios,
        "runs": runs,
    }


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _verdict_class(verdict: str) -> str:
    return {"pass": "pass", "fail": "fail"}.get(verdict, "deferred")


def _render_runs(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return '<p class="muted">暂无运行记录。</p>'
    rows = "".join(
        f"<tr><td class=mono>{_esc(r['created_at'])}</td>"
        f"<td>{'ReportAgent' if r['project'] == 'reportagent' else 'ragent-py' if r['project'] == 'ragent' else _esc(r['project'])}</td>"
        f"<td>{_esc(r['case_id'])}</td>"
        f"<td><span class='tag {_verdict_class(r['verdict'])}'>{_esc(r['verdict'])}</span></td>"
        f"<td class=mono>{int(r.get('latency_ms') or 0)} ms</td></tr>"
        for r in runs
    )
    return (
        "<table><tr><th>时间</th><th>项目</th><th>用例 / 场景</th><th>结论</th><th>延迟</th></tr>"
        f"{rows}</table>"
    )


def _render_assets(assets: list[dict[str, Any]]) -> str:
    rows = ""
    for a in assets:
        rows += (
            "<tr>"
            f"<td><b>{_esc(a['id'])}</b><div class='mono muted'>{_esc(a['path'])}</div></td>"
            f"<td>{'RA' if a['project'] == 'reportagent' else 'RAG'}</td>"
            f"<td class=mono>{_esc(a['cases'])}</td>"
            f"<td>{_esc(a['coverage'])}</td>"
            f"<td>{_esc(a['labeling'])}</td>"
            f"<td class=mono>{_esc(a['version'])}</td>"
            f"<td><span class='tag tier'>{_esc(a['tier'])}</span></td>"
            "</tr>"
        )
    return (
        "<table><tr><th>资产</th><th>项目</th><th>用例</th><th>覆盖</th><th>标注方式</th><th>版本</th><th>证据档位</th></tr>"
        f"{rows}</table>"
    )


def _render_risk(risks: list[dict[str, Any]]) -> str:
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in risks:
        groups.setdefault(str(r.get("risk") or "其他"), []).append(r)
    parts = []
    for risk, items in groups.items():
        rows = "".join(
            f"<tr><td class=mono>{_esc(x.get('id'))}</td><td>{_esc(x.get('title'))}</td>"
            f"<td>{_esc(x.get('trigger'))}</td><td class=muted>{_esc(x.get('guard'))}</td></tr>"
            for x in items
        )
        parts.append(
            f"<h3>{_esc(risk)} <span class='muted'>({len(items)})</span></h3>"
            "<table><tr><th>ID</th><th>场景</th><th>触发条件</th><th>现有护栏</th></tr>"
            f"{rows}</table>"
        )
    return "".join(parts)


def _render_scenarios(scenarios: list[dict[str, Any]]) -> str:
    rows = "".join(
        f"<tr><td><b>{_esc(s['name'])}</b><div class='mono muted'>{_esc(s['id'])}</div></td>"
        f"<td>{_esc(s['desc'])}</td><td class=muted>{_esc(' → '.join(s.get('steps') or []))}</td></tr>"
        for s in scenarios
    )
    return (
        "<table><tr><th>场景</th><th>验证什么</th><th>步骤</th></tr>"
        f"{rows}</table>"
    )


def render_html(snapshot: dict[str, Any]) -> str:
    o = snapshot["overview"]
    snap = o.get("rag_snapshot") or {}
    versions = o.get("versions") or {}
    stats = o.get("run_stats") or {}
    cards = [
        ("ReportAgent 用例", o["cases"]["reportagent"], "9 维字段期望 · 确定性判定"),
        ("ragent 用例", o["cases"]["ragent"], f"{o.get('gold_documents', 0)} 题带金标文档 · {o.get('human_verified', 0)} 题人工核验"),
        ("风险案例", o["risks"], "误答 / 幻觉 / 越权 / 错误执行 / 不可追溯"),
        ("已跑记录", stats.get("total", 0), f"pass {stats.get('by_verdict', {}).get('pass', 0)} · fail {stats.get('by_verdict', {}).get('fail', 0)}"),
    ]
    cards_html = "".join(
        f"<div class=card><div class=h>{_esc(h)}</div><div class=n>{_esc(n)}</div><div class=s>{_esc(s)}</div></div>"
        for h, n, s in cards
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 评测平台 · ReportAgent × ragent-py</title>
<style>
:root{{--ink:#1f2a33;--muted:#6b7a86;--line:#e3e8ec;--paper:#fff;--canvas:#f6f8fa;--teal:#12796f;--teal-soft:#e6f2f0;--amber:#b5761b;--amber-soft:#fdf3e2;--red:#b3392f;--red-soft:#fbeceb;--green:#2f7d4f;--green-soft:#e9f4ee}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--canvas);color:var(--ink);font:14px/1.6 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}}
.wrap{{max-width:1080px;margin:0 auto;padding:28px 20px 80px}}
header{{border-bottom:1px solid var(--line);padding-bottom:16px;margin-bottom:22px}}
h1{{font-size:22px;margin:0 0 6px}}h2{{font-size:16px;margin:30px 0 10px}}h3{{font-size:14px;margin:20px 0 8px}}
.muted{{color:var(--muted)}}.mono{{font-family:Consolas,monospace;font-size:12px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}}
.card{{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:14px}}
.card .h{{font-size:12px;color:var(--muted)}}.card .n{{font-size:24px;font-weight:700}}.card .s{{font-size:12px;color:var(--muted)}}
table{{width:100%;border-collapse:collapse;background:var(--paper);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin:8px 0 4px}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);font-size:12.5px;vertical-align:top}}
th{{background:#fafbfc;color:var(--muted);font-size:12px}}tr:last-child td{{border-bottom:0}}
.tag{{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11.5px;border:1px solid var(--line);background:#eef1f4;color:var(--muted)}}
.tag.pass{{background:var(--green-soft);color:var(--green);border-color:#cfe6da}}
.tag.fail{{background:var(--red-soft);color:var(--red);border-color:#f2d3d0}}
.tag.deferred{{background:var(--amber-soft);color:var(--amber);border-color:#f0dcb8}}
.tag.tier{{background:var(--teal-soft);color:var(--teal);border-color:#cfe4e1}}
.note{{background:var(--teal-soft);border-left:3px solid var(--teal);padding:10px 14px;border-radius:6px;margin:12px 0}}
pre{{background:#0f1720;color:#d8e2ea;padding:12px 14px;border-radius:8px;overflow:auto;font:12px/1.5 Consolas,monospace}}
</style></head><body><div class="wrap">
<header>
  <h1>AI 评测平台 · ReportAgent × ragent-py</h1>
  <p class="muted">实时测试 + 资产沉淀 + 证据分量 ｜ 快照导出 {_esc(snapshot["generated_at"])} ｜
  ReportAgent <span class="mono">{_esc(versions.get("reportagent", "?"))}</span> ·
  ragent-py <span class="mono">{_esc(versions.get("ragent", "?"))}</span></p>
  <div class="note">
    <b>为什么有这一页</b>：评测的价值不在“跑过”，而在<b>能说清这份结论是什么档位的证据</b>。
    下面每张表都带来源 / 覆盖 / 版本 / 档位；<code>deferred</code> 的层不算通过。
    本地平台 <code>:8200</code> 可实时跑，这里是可对外展示的快照。
  </div>
</header>

<div class="grid">{cards_html}</div>

<h2>被测对象与可判层级</h2>
<table>
<tr><th>项目</th><th>角色</th><th>本平台可判到哪一层</th></tr>
<tr><td>ReportAgent</td><td>Agent / Application Runtime（LangGraph 两图 + 三态落库）</td>
<td>需求理解层（需求卡字段，确定性）；执行 / 报告层 <span class="tag deferred">deferred</span>（需 confirm + 真 SQL）</td></tr>
<tr><td>ragent-py</td><td>Retrieval Runtime（经 MCP 对 ReportAgent 暴露）</td>
<td>检索层（金标 doc_id / 证据关键词，确定性）；答案语义 <span class="tag deferred">deferred</span>（需生成 + 裁判）</td></tr>
</table>

<h2>评测资产（来源 / 覆盖 / 版本 / 档位）</h2>
{_render_assets(snapshot["assets"])}

<h2>场景测试（小功能与细节）</h2>
<p class="muted">单条用例回答“命中了吗”；场景回答工程问题：增量更新会不会重复、删了会不会还召回、跨库会不会串。</p>
{_render_scenarios(snapshot["scenarios"])}

<h2>风险案例库</h2>
<p class="muted">ReportAgent 精选真实事故（来自 plan 与测试）+ ragent 从题目元数据自动派生（避开 LLM 裁判）。</p>
{_render_risk(snapshot["risk"])}

<h2>最近的运行记录</h2>
{_render_runs(snapshot["runs"])}

<h2>RAG 历史快照（LLM 裁判 0–3 分，仅作背景）</h2>
{"<p>" + f"65 题均分 {snap.get('mean_score')} / 3（归一 {snap.get('normalized')}%），标注 <b>仅参考</b>：裁判未逐题人工复核。" + "</p>" if snap.get("available") else "<p class='muted'>未找到快照。</p>"}
<table><tr><th>类别</th><th>题数</th><th>均分</th></tr>
{"".join(f"<tr><td>{_esc(k)}</td><td class=mono>{v['n']}</td><td class=mono>{v['mean']}</td></tr>" for k, v in (snap.get("by_category") or {}).items())}
</table>

<h2>判定层与证据分量</h2>
<table>
<tr><th>层</th><th>含义</th><th>进 pass 率</th></tr>
<tr><td><span class="tag tier">deterministic</span></td><td>字段 / 集合 / 命中，可离线复算</td><td>是</td></tr>
<tr><td><span class="tag tier">gold</span></td><td>金标（现跑 SQL / 现查 doc_id / 关键词）</td><td>是</td></tr>
<tr><td><span class="tag deferred">deferred</span></td><td>本轮没覆盖的层（需 confirm、需生成回答）</td><td><b>否</b>，页面标注</td></tr>
</table>
<p class="muted">证据档位：Demo ＜ PoC ＜ 测试环境 ＜ 受限试行 ＜ 正式运行。本项目多数结论落在
<b>测试环境</b>（固定 commit + 固定数据集 + 可复算）；RAG 裁判快照与压缩/崩溃实验落在 <b>PoC</b>（单次或 n 很小）。</p>

<h2>怎么复跑</h2>
<pre>cd D:/PyProject/ReportAgent
$env:PYTHONPATH='D:\\PyProject\\ReportAgent'
python -m uvicorn eval_platform.app:app --port 8200     # 平台
# 依赖：ReportAgent :8100（uvicorn app.main:app）、ragent-py :8000（python -m app.main）
python -m pytest eval_platform/tests -q                  # 16 例自测
python -m eval_platform.export_snapshot --out-html &lt;blog&gt;/source/eval/index.html</pre>

<p class="muted" style="margin-top:26px">方法论 / 资产登记 / 风险库 / 证据分量：<code>docs/evaluation/*.md</code>；设计：<code>docs/plans/2026-11-17-eval-platform.md</code>。</p>
</div></body></html>"""


async def main() -> None:
    parser = argparse.ArgumentParser(description="导出评测平台静态快照")
    parser.add_argument("--base", default=PLATFORM)
    parser.add_argument("--out-html", required=True)
    parser.add_argument("--out-json", default="")
    args = parser.parse_args()

    snapshot = await collect(args.base)
    out_html = Path(args.out_html)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(render_html(snapshot), encoding="utf-8")
    print(f"html -> {out_html} ({out_html.stat().st_size} bytes)")
    if args.out_json:
        out_json = Path(args.out_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"json -> {out_json}")


if __name__ == "__main__":
    asyncio.run(main())
