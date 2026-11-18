"""评测资产登记：把两个项目的用例集读成统一的 `Case` 视图。

设计要点（plan `2026-11-17-eval-platform.md`）：

- **只读**消费两个项目，不改它们的评测代码；
- 每个 case 都带**来源 / 标注方式 / 版本 / 证据档位**——JD 第 3 条要的"复用说明"就落在这里；
- `gold_source=human_verified_*` 的题才有资格当"受限可用证据"，其余只作参考（证据分量在
  `docs/evaluation/evidence-weights.md`）。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# 本仓默认路径从 `__file__` 推出来（CI checkout 到任意目录都能用）；本地可用 RA_REPO 覆盖。
REPO_ROOT = Path(__file__).resolve().parents[1]
RA_REPO = Path(os.getenv("RA_REPO") or REPO_ROOT)
# ragent-py 是**另一个仓库**：本地默认放在同级目录；CI 里由 workflow clone 后通过 RAG_REPO 指过去。
RAG_REPO = Path(os.getenv("RAG_REPO") or (REPO_ROOT.parent / "ragent-py"))

RA_BASELINE = RA_REPO / "evaluation" / "baseline_cases.json"
DOC_MAP_PATH = Path(__file__).with_name("assets") / "rag_doc_map.json"
RA_MEMORY_GOLD = RA_REPO / "evaluation" / "memory_gold_cases.json"
RAG_TESTSET = RAG_REPO / "eval" / "sany_annual_reports" / "rag_testset.json"
RAG_RESULTS = RAG_REPO / "eval" / "sany_annual_reports" / "eval_results.json"


def rag_repo_available() -> bool:
    """ragent-py 的测试集是否可用（决定 RAG 相关用例能跑还是该跳过）。

    没有它的时候不该「报错让人以为平台坏了」，也不要静默返回空用例假装通过——
    调用方（测试）显式 skip 并说明缺什么。
    """
    return RAG_TESTSET.exists()


def missing_inputs() -> list[str]:
    """返回缺失的输入文件清单（诊断用；CI 里打印这个就知道该 clone/挂载什么）。"""
    return [
        f"{label}={path}"
        for label, path in (
            ("RA_BASELINE", RA_BASELINE),
            ("RA_MEMORY_GOLD", RA_MEMORY_GOLD),
            ("RAG_TESTSET", RAG_TESTSET),
        )
        if not path.exists()
    ]

PROJECT_RA = "reportagent"
PROJECT_RAG = "ragent"
PROJECT_LABEL = {PROJECT_RA: "ReportAgent（Agent Runtime）", PROJECT_RAG: "ragent-py（RAG Runtime）"}

# 证据档位：见 docs/evaluation/evidence-weights.md
TIER_HUMAN = "受限可用（人工核验）"
TIER_REFERENCE = "仅参考（未核验）"
TIER_RUNTIME = "受限可用（运行时实测）"


@dataclass(frozen=True)
class Asset:
    """一份评测资产（用例集）的登记信息。"""

    id: str
    project: str
    path: str
    cases: int
    coverage: str
    source: str
    labeling: str
    version: str
    reuse: str
    tier: str
    maintain: str


@dataclass(frozen=True)
class Case:
    """归一后的用例视图（两个项目共用）。"""

    id: str
    project: str
    suite: str
    title: str
    category: str
    query: str
    mode: str = "new"
    difficulty: str = ""
    expectations: dict[str, Any] = field(default_factory=dict)
    gold_documents: tuple[str, ...] = ()
    gold_evidence: tuple[dict[str, Any], ...] = ()
    gold_judgment: str = ""
    gold_source: str = ""
    tags: tuple[str, ...] = ()

    @property
    def tier(self) -> str:
        if self.gold_source.startswith("human_verified"):
            return TIER_HUMAN
        return TIER_REFERENCE

    def brief(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "suite": self.suite,
            "title": self.title,
            "category": self.category,
            "query": self.query,
            "difficulty": self.difficulty,
            "tier": self.tier,
            "gold_source": self.gold_source,
            "gold_judgment": self.gold_judgment,
            "has_gold_docs": bool(self.gold_documents),
            "has_gold_evidence": bool(self.gold_evidence),
            "tags": list(self.tags),
        }


@dataclass
class Dataset:
    assets: list[Asset]
    cases: list[Case]
    risks: list[dict[str, Any]]

    def by_project(self, project: str) -> list[Case]:
        return [c for c in self.cases if c.project == project]

    def find(self, case_id: str) -> Case | None:
        return next((c for c in self.cases if c.id == case_id), None)


# --- 读取 -------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _repo_version(repo: Path) -> str:
    """尽量给出资产所在仓库的 commit 短 sha（git 不在也能跑）。"""
    head = repo / ".git" / "HEAD"
    if not head.exists():
        return "unknown"
    try:
        raw = head.read_text(encoding="utf-8").strip()
        if raw.startswith("ref: "):
            ref = repo / ".git" / raw.split(" ", 1)[1]
            return ref.read_text(encoding="utf-8").strip()[:7] if ref.exists() else "unknown"
        return raw[:7]
    except OSError:
        return "unknown"


def load_reportagent_cases() -> list[Case]:
    """ReportAgent 行为期望用例（`evaluation/baseline_cases.json`，21 例）。"""
    data = _read_json(RA_BASELINE)
    if not isinstance(data, list):
        return []
    cases: list[Case] = []
    for raw in data:
        turns = raw.get("turns") or [{}]
        first = turns[0] if isinstance(turns[0], dict) else {}
        expectations = (raw.get("expectations") or [{}])[0]
        cases.append(Case(
            id=str(raw.get("id", "")),
            project=PROJECT_RA,
            suite="baseline",
            title=str(raw.get("description") or first.get("query") or raw.get("id")),
            category=str(raw.get("category") or ""),
            query=str(first.get("query") or ""),
            mode=str(first.get("mode") or "new"),
            expectations=expectations if isinstance(expectations, dict) else {},
            tags=("9 维字段期望", "确定性判定"),
        ))
    return cases


def load_ragent_cases() -> list[Case]:
    """ragent-py 三一重工年报测试集（65 题 / 10 类别，带 gold_documents）。"""
    data = _read_json(RAG_TESTSET)
    if not isinstance(data, dict):
        return []
    cases: list[Case] = []
    for raw in data.get("题目") or []:
        evidence = raw.get("gold_evidence") or []
        cases.append(Case(
            id=str(raw.get("id", "")),
            project=PROJECT_RAG,
            suite="sany_annual_reports",
            title=str(raw.get("问题", "")),
            category=str(raw.get("类别", "")),
            query=str(raw.get("问题", "")),
            difficulty=str(raw.get("难度", "")),
            gold_documents=tuple(str(x) for x in (raw.get("gold_documents") or [])),
            gold_evidence=tuple(e for e in evidence if isinstance(e, dict)),
            gold_judgment=str(raw.get("gold_judgment") or ""),
            gold_source=str(raw.get("gold_source") or ""),
            tags=tuple(
                t for t in (
                    str(raw.get("题型") or ""),
                    "人工核验" if str(raw.get("gold_source", "")).startswith("human_verified") else "未核验",
                ) if t
            ),
        ))
    return cases


def load_rag_doc_map() -> dict[str, Any]:
    """资产重挂表：gold_documents（旧 document_id）→ 当前库 document_id。

    为什么必须有：重新入库后 document_id 会变，金标不重挂就会**全部假失败**。
    这是资产维护动作（见 docs/evaluation/asset-registry.md），不是代码里的常量。
    """
    data = _read_json(DOC_MAP_PATH)
    if not isinstance(data, dict):
        return {"map": {}, "note": "未找到 rag_doc_map.json，gold_documents 按原值比对"}
    return data


def map_gold_documents(gold: Iterable[str], mapping: dict[str, Any] | None = None) -> list[str]:
    table = (mapping or load_rag_doc_map()).get("map") or {}
    return [str(table.get(doc, doc)) for doc in gold]


def load_memory_gold_summary() -> dict[str, Any]:
    """ReportAgent 记忆金标集只做**数量与来源**展示（判定器在它自己的测试里）。"""
    data = _read_json(RA_MEMORY_GOLD)
    if not isinstance(data, dict):
        return {"cases": 0, "source": "missing"}
    memories = data.get("memories") or data.get("记忆") or []
    queries = data.get("queries") or data.get("queries_gold") or data.get("查询") or []
    return {
        "memories": len(memories) if isinstance(memories, list) else 0,
        "queries": len(queries) if isinstance(queries, list) else 0,
        "keys": sorted(data.keys())[:8],
    }


def load_ragent_snapshot() -> dict[str, Any]:
    """ragent 历史评测快照（LLM 裁判 0–3 分制）：只作背景，不参与 live 判定。"""
    data = _read_json(RAG_RESULTS)
    # 形状是 {question_id: {question, category, judge_score, judge_reason, ...}}；也兼容 list
    if isinstance(data, dict):
        data = list(data.values())
    if not isinstance(data, list) or not data:
        return {"available": False}
    scores = [x.get("judge_score") for x in data if isinstance(x.get("judge_score"), int)]
    scores = [s for s in scores if s >= 0]
    by_cat: dict[str, list[int]] = {}
    for item in data:
        score = item.get("judge_score")
        if not isinstance(score, int) or score < 0:
            continue
        by_cat.setdefault(str(item.get("category") or item.get("类别") or "?"), []).append(score)
    return {
        "available": True,
        "questions": len(data),
        "scored": len(scores),
        "mean_score": round(sum(scores) / len(scores), 2) if scores else None,
        "normalized": round(sum(scores) / (len(scores) * 3) * 100, 1) if scores else None,
        "by_category": {
            k: {"n": len(v), "mean": round(sum(v) / len(v), 2)} for k, v in sorted(by_cat.items())
        },
    }


# --- 资产登记 ---------------------------------------------------------------


def load_assets(ra_cases: list[Case], rag_cases: list[Case]) -> list[Asset]:
    ra_sha = _repo_version(RA_REPO)
    rag_sha = _repo_version(RAG_REPO)
    human = sum(1 for c in rag_cases if c.gold_source.startswith("human_verified"))
    with_docs = sum(1 for c in rag_cases if c.gold_documents)
    mem = load_memory_gold_summary()
    return [
        Asset(
            id="ra-baseline",
            project=PROJECT_RA,
            path="evaluation/baseline_cases.json",
            cases=len(ra_cases),
            coverage="需求理解 / 执行 / 报告 / 行为 4 段期望，9 维字段（requirement·execution·report·behavior·memory·retrieval·sql·tool_selection·repair）",
            source="人工设计（P0 冻结基线，逐例带 golden SQL）",
            labeling="expectations 手写；execution_accuracy 用金标 SQL 现跑比对",
            version=f"{ra_sha} · 读取于 {datetime.now(timezone.utc):%Y-%m-%d}",
            reuse="`python -m evaluation.runner`（需后端在跑）；本平台复用其「字段期望」做需求卡比对",
            tier=TIER_REFERENCE,
            maintain="加 case 只改 JSON，`schema.py` 冻结不动",
        ),
        Asset(
            id="ra-memory-gold",
            project=PROJECT_RA,
            path="evaluation/memory_gold_cases.json",
            cases=int(mem.get("queries") or 0) or int(mem.get("memories") or 0),
            coverage="记忆召回：必中 / 禁召（forbidden）/ 保守空召回（expect_none）",
            source="人工设计（词交确定性）",
            labeling="hand-written gold + forbidden 列表",
            version=f"{ra_sha}",
            reuse="`backend/tests/persistence/test_memory_gold_recall.py`；真 embedding runner",
            tier=TIER_REFERENCE,
            maintain="25 记忆 / 30 金标 query",
        ),
        Asset(
            id="rag-sany",
            project=PROJECT_RAG,
            path="eval/sany_annual_reports/rag_testset.json",
            cases=len(rag_cases),
            coverage=f"10 类别（A–J）· {with_docs} 题带 gold_documents · {human} 题人工核验（含 gold_evidence 关键词）",
            source="基于三一重工 2023–2025 三份年报，人工出题 + 人工核验子集",
            labeling="gold_source 字段区分 `human_verified_2026-08-23` / `original_unverified`",
            version=f"{rag_sha}",
            reuse="本平台：检索层按 gold_documents 算命中，不跑 LLM 裁判",
            tier=TIER_HUMAN,
            maintain="加题改 JSON；核实一题就把 gold_source 改成 human_verified",
        ),
        Asset(
            id="rag-eval-snapshot",
            project=PROJECT_RAG,
            path="eval/sany_annual_reports/eval_results.json",
            cases=int(load_ragent_snapshot().get("questions") or 0),
            coverage="全量 65 题历史跑分（LLM 裁判 0–3 分）",
            source="历史运行产物",
            labeling="LLM 裁判评分，未逐题人工复核",
            version="快照（不可重算，见证据分量表：仅参考）",
            reuse="只做背景对照；不作 live 判定依据",
            tier=TIER_REFERENCE,
            maintain="重跑 `eval/eval_single.py` 才更新",
        ),
    ]


# --- 风险案例库（从资产自动派生 + 少量精选） --------------------------------

_RA_CURATED_RISKS: tuple[dict[str, Any], ...] = (
    {
        "id": "RA-R1",
        "risk": "误答",
        "title": "口径被换：问「销售额」答成「净销售额」",
        "trigger": "候选用 `sorted(known_metrics)[:3]`，中文码点把「净」排在「销」前",
        "evidence": "plan 2026-11-10-time-range-and-metric-slot-backfill；execution_accuracy 首跑 fail:values",
        "guard": "`match_metric_terms` 原话命中优先 + 夹具逐字取口径",
    },
    {
        "id": "RA-R2",
        "risk": "错误执行",
        "title": "过滤写进 LEFT JOIN 的 ON 子句 = 静默不过滤",
        "trigger": "`region='华南'`/`year=2026` 写在 ON，结果算出全库 9.38 亿（真值 3936 万）",
        "evidence": "plan 2026-11-11-sql-filter-in-join-on（C1/C2 两道确定性契约闸）",
        "guard": "必需过滤必须在 WHERE；缺失/落 ON 直接进 repair",
    },
    {
        "id": "RA-R3",
        "risk": "不可追溯",
        "title": "失败没有现场：崩了只留 phase=generating",
        "trigger": "内存缓冲导致 span 丢失、`last_failed_action` 为空、report_version 0 行",
        "evidence": "plan 2026-11-11-failure-recovery-durability（三次强杀实验）",
        "guard": "启动孤儿收敛 + span 增量落盘 + `interrupted` 可重试",
    },
    {
        "id": "RA-R4",
        "risk": "幻觉",
        "title": "编造表名/列名（dim_supplier、sales_amount）",
        "trigger": "模型自由生成 SQL 时引入 schema 不存在的对象",
        "evidence": "SQL 安全闸 6 层（含表 allowlist + EXPLAIN）；plan 2026-10-28-mainline-correctness-fixes",
        "guard": "SELECT-only + 关键字黑名单 + AST + 表 allowlist + EXPLAIN",
    },
    {
        "id": "RA-R5",
        "risk": "越权",
        "title": "跨用户读别人的会话",
        "trigger": "session 端点只按 session_id 查，不校验 owner",
        "evidence": "A-2 安全加固；`test_cross_user_isolation.py`；404 不区分「不存在/不是你的」",
        "guard": "所有 session 端点强制 (user_id, session_id) 双条件",
    },
)

_EVAL_TOOL_RISKS: tuple[dict[str, Any], ...] = (
    {
        "id": "EV-R1",
        "risk": "错误执行",
        "title": "清理临时库把全库删了（14 份文档 / 1391 chunks）",
        "trigger": "`GET /documents` 不支持 `kb_id` 过滤，admin 拿到全库；清理按列出的文档逐个删",
        "evidence": "场景测试首跑「文档数=16（期望 1）」被当成未就绪噪音；plan 2026-11-17-eval-platform",
        "guard": "`ragent_docs` 客户端过滤 + 回归测试；清理只作用于本库 document_id",
        "project": PROJECT_RAG,
    },
    {
        "id": "EV-R2",
        "risk": "不可追溯",
        "title": "工具超时被当成产品失败（90s 等不到 indexed 就判 fail）",
        "trigger": "PDF 走 OCR（250 页）+ 元数据 LLM 批处理，真实耗时分钟级；批量灌库时串行排队更久",
        "evidence": "三个场景首跑全 fail，实际都在 141s+ 才 indexed",
        "guard": "等待上限 300s；超时记 `timeout` / verdict `error`，与 `fail` 分开",
        "project": PROJECT_RAG,
    },
    {
        "id": "EV-R3",
        "risk": "误答",
        "title": "判定器自己的口径错：2/3 召回永远判 fail",
        "trigger": "阈值写 `ratio >= 0.67`，而 2/3 = 0.6666…，文案却写「至少 2/3」",
        "evidence": "恢复后重跑 65 题，8 条 fail 里 4 条（Q18/Q19/Q51/Q54）由此造成",
        "guard": "阈值改真分数 `CROSS_DOC_RECALL_MIN = 2/3` + 回归测试 `test_recall_two_of_three_passes`",
        "project": "eval_platform",
    },
    {
        "id": "EV-R4",
        "risk": "越权",
        "title": "金标证据不成立：问「金额单位」的金标关键词是员工人数",
        "trigger": "标注时把「命中同一份文档」当成「证据成立」，关键词与该问无因果关系",
        "evidence": "Q11 金标 keyword=25,930（员工情况表），与「主要会计数据单位」无关",
        "guard": "判定记录保留 expected/observed，人工复核能看见关键词与问题不对应；不改数据让它变绿",
        "project": "eval_platform",
    },
    {
        "id": "EV-R5",
        "risk": "错误执行",
        "title": "提速开关的隐性代价：关掉元数据 LLM → `chunks.year` 全 NULL",
        "trigger": "为了把入库从分钟级压到秒级设 `INGEST_METADATA_ENABLED=0`",
        "evidence": "ragent 的 period / coverage_by_year / 跨年冲突检测退化为「不可比」；Q25（2025 年题）金标文档在 k≤12 完全不出现",
        "guard": "平台量化该项代价；重灌必须显式选择档位，并写明「检索可用、元数据加成缺失」",
        "project": PROJECT_RAG,
    },
)

_RISK_BY_CATEGORY = {
    "H-错误前提纠偏": "错误前提（迎合性）",
    "I-拒答与知识边界": "幻觉 / 知识边界",
    "E-时序与追溯调整": "口径矛盾",
    "F-口径与概念辨析": "口径混淆",
    "G-实体消歧": "实体混淆",
    "D-计算与多跳推理": "多跳算错",
    "B-表格理解与单位换算": "单位换算",
}


def derive_risks(rag_cases: Iterable[Case]) -> list[dict[str, Any]]:
    """风险案例库：两个项目的精选事故 + 评测工具自身事故 + ragent 从题目元数据派生。"""
    risks: list[dict[str, Any]] = [dict(r) for r in _RA_CURATED_RISKS]
    risks += [dict(r) for r in _EVAL_TOOL_RISKS]
    for case in rag_cases:
        judgment = case.gold_judgment
        category = case.category
        if judgment == "not_answerable":
            risk = "幻觉（应拒答）"
        elif "错误前提" in category:
            risk = "错误前提（迎合性）"
        else:
            risk = _RISK_BY_CATEGORY.get(category, "")
        if not risk:
            continue
        risks.append({
            "id": f"RAG-{case.id}",
            "risk": risk,
            "title": case.title,
            "trigger": next(
                (str(e.get("keyword")) for e in case.gold_evidence if e.get("keyword")),
                "（该题未给 gold_evidence）",
            ),
            "evidence": f"{case.category} · {case.gold_source or 'unverified'} · {case.tier}",
            "guard": "检索命中 gold_documents / 回答含金标关键词" if case.gold_documents else "无金标，仅人工评审",
            "case_id": case.id,
            "project": PROJECT_RAG,
        })
    return risks


def load_dataset() -> Dataset:
    """一次性加载（无缓存：文件很小，避免热更新失效）。"""
    ra = load_reportagent_cases()
    rag = load_ragent_cases()
    return Dataset(
        assets=load_assets(ra, rag),
        cases=ra + rag,
        risks=derive_risks(rag),
    )


def summary() -> dict[str, Any]:
    """总览数字：用例/资产/风险/快照。"""
    ds = load_dataset()
    rag_cases = [c for c in ds.cases if c.project == PROJECT_RAG]
    return {
        "assets": len(ds.assets),
        "cases": {p: len(ds.by_project(p)) for p in (PROJECT_RA, PROJECT_RAG)},
        "risks": len(ds.risks),
        "human_verified": sum(1 for c in rag_cases if c.gold_source.startswith("human_verified")),
        "gold_documents": sum(1 for c in rag_cases if c.gold_documents),
        "rag_snapshot": load_ragent_snapshot(),
        "ra_memory_gold": load_memory_gold_summary(),
        "versions": {PROJECT_RA: _repo_version(RA_REPO), PROJECT_RAG: _repo_version(RAG_REPO)},
    }
