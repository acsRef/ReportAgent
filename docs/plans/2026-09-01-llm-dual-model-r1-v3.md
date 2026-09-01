# 双模型 LLM 切换——intent=DeepSeek-R1 / 其他=DeepSeek-V3 + SiliconFlow Provider

> 状态: 暂缓（2026-09-01；2026-09-01 P14 启动决策：搁置双模型不做，避免与 P14 evaluation 在「Model 变更重跑 Golden Set」维度上交叉。base 仍为 P12 master `e711bcb`，**未从 P13 master `079dd2f` rebase**；未实施任何 TDD task（仅 plan 文档落地 commit `87a3f83`）。重启条件：P14 baseline 跑通 + 模型维度指标有评估信号后再讨论；宪法 §8「统一原则」现状不修）
> 上游: P12 Playwright E2E + review-prep-r2 已合 master `e711bcb`；pytest.ini 移到 repo root 已修 71 failed（953 passed / 1 skipped）；CLAUDE.md §8 现状偏离 P6 收敛目标——本 plan 显式记录偏离原因与边界

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 ReportAgent 的 LLM 切到 SiliconFlow 上的 DeepSeek 双模型——意图分类走 R1（reasoning 链提升语义路由稳定性），其他场景（requirement parse / SQL plan / SQL generate / Report）走 V3（标准 chat、低成本、低延迟）；同步同步更新 CLAUDE.md §8 现状标注分层偏离。

**Architecture:** adapter 已有 `kwargs["model"]` 透传（adapter.py:90），无需改 adapter 形状；在 `LLMConfig` 新增 `intent_model` 字段（env `LLM_INTENT_MODEL`），`intent._llm_classify` 调用时显式传 `model=settings.intent_model`；SiliconFlow 同 base_url 不同 model 字段，复用现有 `LLM_BASE_URL`。intent prompt 强化（"禁思考 + 整段即 JSON"，防 R1 输出大段推理吃 token，参考 ragent-py 实战经验）。

**Tech Stack:** OpenAI-compatible HTTP / `langchain_openai.ChatOpenAI`（已支持 custom base_url）/ `os.getenv` 配置（已有 `LLM_*` settings）/ prompt 工程（已有 6 段结构）

---

## Context

P12 合 master 后 user 主导把模型从 MiniMax 切到 DeepSeek 平台：

| 维度 | 当前（MiniMax） | 目标（DeepSeek 双模型 + SiliconFlow） |
|---|---|---|
| Provider | minimax（拼写，按代码 default） | SiliconFlow（OpenAI-compatible） |
| base_url | `https://api.minimax.chat/v1` | `https://api.siliconflow.cn/v1` |
| 意图路由 | 默认 chat_model（MiniMax-M2.7-highspeed） | **DeepSeek-R1**（reasoning，提升语义路由稳定性） |
| SQL / Report / requirement parse / 对话 | 默认 chat_model | **DeepSeek-V3**（标准 chat，低成本低延迟） |
| 体验问题 | M2.7-highspeed 整体不理想 | R1 有思维链提升推理质量；V3 成本合适 |

### 架构偏离 CLAUDE.md §8 宪法

CLAUDE.md §8 明确："全项目统一使用一个 reasoning-capable chat model，provider 无关"——这是 P6 收敛宪法。

**本 plan 显式偏离 §8**：intent=R1 / 其他=V3 的双模型分层是 user-driven 决策（ragent-py 已实战验证 `intent_model = DeepSeek-R1` + `chat_model = DeepSeek-V3` 分层）。plan 落地同时同步更新 §8「现状」段标注分层偏离 + 偏离原因 + 边界。

### 借鉴 ragent-py 实战经验（`D:\PyProject\ragent-py\docs\plans\2026-08-20-intent-vision-model-switch.md`）

ragent-py 把意图路由切到 R1 后实测发现：
- R1 默认会把路由当问答、输出大段推理 → 吃掉 `max_tokens` + 丢 JSON
- **修复手段**：prompt 强化（禁思考 + 整个回复即 JSON + 输出前确认清单）→ 三一营收类问题 25-30s → 7-17s
- 用了 2 个锁定测试：`test_intent_tokens.py` + `test_intent_guard.py`

本 plan 复用 ragent-py 的 prompt 强化策略 + 锁定测试模式。

### adapter 兼容性

- `LLMAdapter.generate(prompt, **kwargs)` 已支持 `kwargs["model"]`（adapter.py:90）→ 透传给 ChatOpenAI → 无需改 adapter
- `strip_think_tags`（adapter.py:20）已处理 `<think>` 标签 → R1 推理链剥离已在位
- `LLMConfig` 单实例共享 → 用 `kwargs["model"]` 而非新建 adapter 实例

## Design

### 1. LLMConfig 加 `intent_model` 字段（不破现有 LLM_* 接口）

```python
class LLMConfig:
    def __init__(self) -> None:
        # 主模型：所有非 intent 场景（requirement parse / SQL plan / SQL generate / Report）
        self.model: str = os.getenv("LLM_MODEL") or os.getenv("MINIMAX_MODEL") or "MiniMax-M2.7-highspeed"
        # intent 专用：DeepSeek-R1 推理模型
        self.intent_model: str = os.getenv("LLM_INTENT_MODEL") or self.model
        # 共享同一 base_url（同 provider 不同 model）
        self.base_url: str = os.getenv("LLM_BASE_URL") or os.getenv("MINIMAX_BASE_URL") or "https://api.minimax.chat/v1"
        self.api_key: str | None = os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY")
```

关键：**`self.intent_model` 默认 fallback 到 `self.model`**——未设 `LLM_INTENT_MODEL` 时不动现有行为（向后兼容）。

### 2. intent 调用显式传 model

```python
# backend/app/agent/intent.py:66-85 _llm_classify
def _llm_classify(q: str) -> IntentResult:
    from app.llm.config import LLMConfig
    settings = LLMConfig()
    prompt = build_intent_classify_prompt(q)
    raw = call_llm(prompt, max_tokens=200, model=settings.intent_model)
    ...
```

Adapter kwargs["model"] 已支持，无需改 adapter / __init__.py。

### 3. intent prompt 强化（ragent-py 教训）

`backend/app/agent/prompts/intent_prompts.py` 在现有 6 段基础上**前置** R1-specific 强化段：

```python
INTENT_CLASSIFY_V1: dict[str, str] = {
    "system_contract": (
        "你是 ReportAgent 的意图分类器。职责：判断用户查询属于哪一类，"
        "不做 SQL 生成、不调工具、不重写查询。"
    ),
    # R1 强化：禁思考 + 整段即 JSON + 输出前自检清单（ragent-py 实战验证）
    "no_thinking_directive": (
        "【最高优先级 - 输出形态】"
        "不要在内部思考，不要写任何思考过程、推理、解释、开场白或结束语。"
        "你的【整个回复】只能是一个 JSON 对象，直接以 { 开头并以 } 结束，前后没有任何其他字符。"
        "任何解释、markdown 代码块、或"好的，让我..."之类的话都是错误。"
    ),
    "role": "你是意图分类器。",
    "task_contract": (
        "判断用户查询属于哪类，只输出 JSON，禁止解释。"
        "\n\n用户查询: {user_query}"
        ...
    ),
    ...
}
```

预期效果：R1 调一次稳定吐 JSON（不再是 25-30s 长段推理），延迟 ~7-17s。

### 4. CLAUDE.md §8 同步偏离说明

把 §8「现状」从：

> 现状：app/llm.py call_llm + llm_resilience.py 分散承担；P6 迁移并跑 Golden Set Before/After 对比

改为：

> 现状（2026-09-01 双模型分层后）：`LLMConfig.intent_model` 字段（env `LLM_INTENT_MODEL`）默认 fallback 到 `LLM_MODEL`；`_llm_classify`（intent.py:69）显式传 `model=settings.intent_model`；adapter kwargs["model"] 透传（adapter.py:90）无改动。**显式偏离 §8 统一原则**：intent=R1（reasoning 提升语义路由稳定性）+ 其他=V3（成本/延迟）；ragent-py 已实战验证该分层（`D:\PyProject\ragent-py\docs\plans\2026-08-20-intent-vision-model-switch.md` 25-30s → 7-17s）。SiiconFlow provider（OpenAI-compatible），共享 base_url。P6 后续若评估 R1 全场景可用，可再合并回 §8 统一原则。

CLAUDE.md §6（P6 LLM Adapter 现状）也加一行：现状追加"r1/v3 双模型分层，详见 §8"。

### 5. .env.example 更新（不是 .env）

`.env` 由用户自己填。`.env.example` 加：
```bash
# LLM provider (P12+ 收敛；MINIMAX_* 兼容)
LLM_BASE_URL=https://api.siliconflow.cn/v1
LLM_API_KEY=<your_siliconflow_key>
# 主模型：requirement parse / SQL plan / SQL generate / Report / 对话
LLM_MODEL=deepseek-chat
# intent 专用：reasoning 模型（默认 fallback LLM_MODEL）
LLM_INTENT_MODEL=deepseek-reasoner
```

## Files to change

| 模块 | 模式 | 路径 |
|---|---|---|
| 配置 | 修改 | `backend/app/llm/config.py`（加 `intent_model` 字段 + fallback 到 model） |
| 配置 | 修改 | `backend/.env.example`（加 LLM_BASE_URL / LLM_INTENT_MODEL / LLM_MODEL 默认） |
| intent 调用 | 修改 | `backend/app/agent/intent.py:66-85`（`_llm_classify` 显式 `model=settings.intent_model`） |
| intent prompt | 修改 | `backend/app/agent/prompts/intent_prompts.py`（前置 R1 no_thinking_directive 段） |
| 测试 | 新增 | `backend/tests/smoke/test_intent.py`（2 例：test_intent_uses_intent_model / test_intent_prompt_has_no_thinking_directive） |
| 测试 | 新增 | `backend/tests/contracts/test_llm_config_intent_model.py`（1 例：test_llm_config_intent_model_fallback） |
| 宪法 | 修改 | `CLAUDE.md` §8「现状」段（标注分层偏离 + ragent-py 参考） |
| 宪法 | 修改 | `CLAUDE.md` §6 P6 现状（加"双模型分层，详见 §8"） |
| plan | 修改 | `docs/plans/README.md`（登记本 plan 入 进行中 → 已完成） |
| plan | 收尾 | `docs/plans/2026-09-01-llm-dual-model-r1-v3.md`（状态改 已完成 + 落地记录） |

## Reused existing utilities

- `backend/app/llm/adapter.py:90` — `kwargs.get("model", self.config.model)` 透传模型名 → 无需改 adapter
- `backend/app/llm/adapter.py:20` — `strip_think_tags` 已处理 `<think>` 标签 → R1 推理链剥离已在位
- `backend/app/llm/__init__.py:80` — `call_llm(prompt, **kwargs)` 透传 kwargs → intent 调用点直接传 `model=...`
- `backend/app/agent/prompts/__init__.py` — prompt registry（已有 6 段构建模式）→ 复用 build_intent_classify_prompt
- `backend/app/utils/text.py:safe_json_parse` — 已用于 intent LLM 输出容错解析
- `backend/tests/smoke/test_intent.py` — 现有 intent 单测 → 直接加 2 例
- `backend/tests/contracts/` 模式 — 现有 contract test 模式（pytestmark=contracts）→ 复用
- ragent-py 实战：`D:\PyProject\ragent-py\app\core\intent.py:32-88` prompt 模板 + `tests/unit/test_intent_tokens.py` 锁定测试模式

## Verification

### 单测 / 集成

```bash
# TDD：先红后绿
cd backend && D:/miniConda/envs/agent/python.exe -m pytest tests/contracts/test_llm_config_intent_model.py -v
# 预期：1/1 PASSED（intent_model fallback）

cd backend && D:/miniConda/envs/agent/python.exe -m pytest tests/smoke/test_intent.py -v
# 预期：现有 + 2 新例（test_intent_uses_intent_model / test_intent_prompt_has_no_thinking_directive）

cd backend && D:/miniConda/envs/agent/python.exe -m pytest -q
# 预期：≥ 953 passed / 1 skipped（修后无回归；新增 3 例）
```

### Playwright Contract（验证 mock 路径不破）

```bash
cd frontend && npx playwright test --config e2e/playwright.config.ts e2e/specs/0[1-9]-*.spec.ts e2e/specs/10-*.spec.ts
# 预期：10/10 全绿（Contract 用 MockLLMAdapter，不走真实 LLM）
```

### 真实 LLM smoke（Full E2E 之一，user 自验）

```bash
# 配 .env 后启动 backend
LLM_BASE_URL=https://api.siliconflow.cn/v1 \
LLM_API_KEY=<key> \
LLM_MODEL=deepseek-chat \
LLM_INTENT_MODEL=deepseek-reasoner \
APP_ENV=development \
ALLOW_INSECURE_DEFAULT_AUTH=1 \
JWT_SECRET=$(openssl rand -hex 32) \
DEFAULT_PASSWORD=admin123 \
D:/miniConda/envs/agent/python.exe -m uvicorn app.main:app --port 8100

# 走一个 chat → 看 intent trace：tracer 应输出 model=deepseek-reasoner
curl -X POST http://localhost:8100/api/v1/chat \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"user_query":"你好","session_id":"t1","mode":"new"}'
# 预期：phase=idle（chitchat 快路径，stage 1 关键词命中）；无 LLM 调用

curl -X POST http://localhost:8100/api/v1/chat \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"user_query":"2024年各区域销售额","session_id":"t2","mode":"new"}'
# 预期：phase=awaiting_confirm（intent LLM 调用 model=deepseek-reasoner）；trace 看得到
```

冒烟矩阵：
- [ ] `LLM_INTENT_MODEL` 未设 → intent 仍走 `LLM_MODEL`（fallback）
- [ ] `LLM_INTENT_MODEL=deepseek-reasoner` → intent 走 R1，其他场景仍 V3
- [ ] `call_llm(prompt, model=...)` kwargs 透传到 ChatOpenAI（adapter test 验证）
- [ ] intent prompt 含 `no_thinking_directive` 段（"不要在内部思考"）
- [ ] R1 调一次稳定吐 JSON（不在 max_tokens=200 内吃大段推理）
- [ ] Contract E2E 全绿（mock LLM 路径不受真实模型影响）
- [ ] 全量 backend 测试 953+ passed / 1 skipped（新增 3 例）

## Explicitly NOT doing

- **不动 adapter** — `kwargs["model"]` 已支持；改 adapter 是无谓破坏
- **不新增第二个 LLMAdapter 实例** — `get_llm_adapter()` 单例 + kwargs 透传已足够；多实例会破坏 fixture miss 检测
- **不改其他 Agent caller** — Requirement / SQL / Report / Conversation 都走默认 `LLM_MODEL`（V3），不显式传 model（CLAUDE.md §8 偏离仅限 intent）
- **不改 `LLM_PROVIDER=mock` 路径** — MockLLMAdapter 与真实 LLM 解耦，本 plan 不动 mock
- **不修 P12 review-prep 任何内容** — 不动 master 上 pytest.ini / mock cursor scope / spec 命名
- **不动 .env** — 由 user 自己填 key；plan 只更新 .env.example 模板
- **不写 Golden Case** — R1/V3 真实延迟与质量评估留 P14 Evaluation；本 plan 只到 unit + Contract E2E + smoke curl
- **不接 Langfuse** — P13 范围内
- **不删 MINIMAX_* env 兼容** — P6 兼容性是宪法 §8 设计；保留 env fallback
- **不动 SiliconFlow embedding** — `LLM_*` settings 仅 chat 用；embedding 走 `app/services/embedding.py` 自己的 env，独立

---

## Task 1: LLMConfig 加 intent_model 字段

**Files:**
- Modify: `backend/app/llm/config.py`
- Create: `backend/tests/contracts/test_llm_config_intent_model.py`

- [ ] **Step 1.1: 写 fallback 测试（先红）**

新建 `backend/tests/contracts/test_llm_config_intent_model.py`：
```python
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contracts

from app.llm.config import LLMConfig


def test_intent_model_falls_back_to_model(monkeypatch):
    """LLM_INTENT_MODEL 未设 → intent_model == model（向后兼容）。"""
    monkeypatch.delenv("LLM_INTENT_MODEL", raising=False)
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.siliconflow.cn/v1")
    cfg = LLMConfig()
    assert cfg.intent_model == "deepseek-chat"


def test_intent_model_explicit_env(monkeypatch):
    """LLM_INTENT_MODEL 显式设 → intent_model == LLM_INTENT_MODEL。"""
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("LLM_INTENT_MODEL", "deepseek-reasoner")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.siliconflow.cn/v1")
    cfg = LLMConfig()
    assert cfg.intent_model == "deepseek-reasoner"
    assert cfg.model == "deepseek-chat"  # 不污染主模型


def test_intent_model_minimax_fallback_legacy(monkeypatch):
    """MINIMAX_* legacy env → intent_model 仍 fallback（兼容旧 .env）。"""
    monkeypatch.delenv("LLM_INTENT_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("MINIMAX_MODEL", "MiniMax-M2.7-highspeed")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    cfg = LLMConfig()
    assert cfg.intent_model == cfg.model == "MiniMax-M2.7-highspeed"
```

- [ ] **Step 1.2: 跑测试确认红**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest tests/contracts/test_llm_config_intent_model.py -v
```
预期：3 例全部 AttributeError 或 assertion（intent_model 字段未定义）

- [ ] **Step 1.3: LLMConfig 加 intent_model 字段**

修改 `backend/app/llm/config.py:7-17`：
```python
class LLMConfig:
    def __init__(self) -> None:
        # 主模型：requirement parse / SQL plan / SQL generate / Report / 对话
        self.model: str = os.getenv("LLM_MODEL") or os.getenv("MINIMAX_MODEL") or "MiniMax-M2.7-highspeed"
        # intent 专用：ragent-py 验证 reasoning 模型提升语义路由稳定性（默认 fallback 到 model）
        self.intent_model: str = os.getenv("LLM_INTENT_MODEL") or self.model
        self.api_key: str | None = os.getenv("LLM_API_KEY") or os.getenv("MINIMAX_API_KEY")
        self.base_url: str = os.getenv("LLM_BASE_URL") or os.getenv("MINIMAX_BASE_URL") or "https://api.minimax.chat/v1"
        self.provider: str = os.getenv("LLM_PROVIDER", "minimax")
        self.timeout: float = float(os.getenv("LLM_TIMEOUT", "60"))
        # 宪法 §11 契约值：LLM transient retry 预算 2（P9 自 P6 遗留默认 5 收敛）。
        self.max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))
        self.max_total_time: float = float(os.getenv("LLM_MAX_TOTAL_TIME", "90"))
        self.context_window: int = int(os.getenv("LLM_CONTEXT_WINDOW", "131072"))
        self.temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.1"))
```

- [ ] **Step 1.4: 跑测试确认绿**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest tests/contracts/test_llm_config_intent_model.py -v
```
预期：3/3 passed

- [ ] **Step 1.5: Commit**

```bash
git add backend/app/llm/config.py backend/tests/contracts/test_llm_config_intent_model.py
git commit -m "feat(llm): LLMConfig.intent_model 字段（env LLM_INTENT_MODEL，fallback 到 LLM_MODEL）"
```

---

## Task 2: intent 调用显式传 model

**Files:**
- Modify: `backend/app/agent/intent.py:66-85`（`_llm_classify`）

- [ ] **Step 2.1: 写测试（先红）**

在 `backend/tests/smoke/test_intent.py` 新增：
```python
def test_intent_uses_intent_model(monkeypatch):
    """_llm_classify 调用 call_llm 时传 model=intent_model（k=v 锁定）。"""
    from app.agent.intent import _llm_classify
    from app.llm import config as llm_config_module
    from app.llm.config import LLMConfig

    monkeypatch.setattr(llm_config_module, "LLMConfig", lambda: LLMConfig.__new__(LLMConfig))
    # 用 __new__ 绕开 env（保持 monkeypatch 不污染），但需要单独 patch model/intent_model
    captured_kwargs: dict = {}

    def fake_call_llm(prompt, **kwargs):
        captured_kwargs.update(kwargs)
        return '{"kind": "report", "confidence": 0.9, "reason": "t"}'

    import app.agent.intent as intent_module
    monkeypatch.setattr(intent_module, "call_llm", fake_call_llm)
    # 直接 monkeypatch LLMConfig 实例字段
    cfg = LLMConfig()
    monkeypatch.setattr(cfg, "intent_model", "deepseek-reasoner")
    monkeypatch.setattr(llm_config_module, "LLMConfig", lambda: cfg)

    res = _llm_classify("2024 年各区域销售额")
    assert captured_kwargs.get("model") == "deepseek-reasoner", (
        f"intent should route to deepseek-reasoner, got {captured_kwargs.get('model')}"
    )
```

注：测试用 `monkeypatch.setattr` 替换 `_llm_classify` 内的 `LLMConfig` 调用，确保拿到的是测试构造的 cfg 实例。

- [ ] **Step 2.2: 跑测试确认红**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest tests/smoke/test_intent.py::test_intent_uses_intent_model -v
```
预期：FAIL（`model` kwarg 未传）

- [ ] **Step 2.3: 修改 `_llm_classify`**

修改 `backend/app/agent/intent.py:66-85`：
```python
def _llm_classify(q: str) -> IntentResult:
    """Stage 3：LLM 语义分类。失败降级为 REPORT（保主流程）。

    2026-09-01 双模型分层：intent 专用 DeepSeek-R1（reasoning 提升语义路由稳定性），
    主模型 DeepSeek-V3。Adapter kwargs["model"] 透传（adapter.py:90）。
    """
    from app.llm.config import LLMConfig
    settings = LLMConfig()
    prompt = build_intent_classify_prompt(q)
    raw = call_llm(prompt, max_tokens=200, model=settings.intent_model)
    parsed = safe_json_parse(raw) if isinstance(raw, str) else raw
    if not isinstance(parsed, dict):
        parsed = {}
    kind = str((parsed or {}).get("kind", "")).lower()
    try:
        conf = float((parsed or {}).get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    reason = str((parsed or {}).get("reason", ""))[:100]
    mapping = {
        "report": IntentKind.REPORT,
        "interface": IntentKind.INTERFACE,
        "chitchat": IntentKind.CHITCHAT,
        "other": IntentKind.UNKNOWN,
    }
    return IntentResult(mapping.get(kind, IntentKind.REPORT), reason or "LLM 判定", conf)
```

- [ ] **Step 2.4: 跑测试确认绿**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest tests/smoke/test_intent.py::test_intent_uses_intent_model -v
```
预期：PASSED

- [ ] **Step 2.5: 全量 smoke intent 测试确认无回归**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest tests/smoke/test_intent.py -v
```
预期：现有用例 + 新用例全绿

- [ ] **Step 2.6: Commit**

```bash
git add backend/app/agent/intent.py backend/tests/smoke/test_intent.py
git commit -m "feat(intent): _llm_classify 显式传 model=settings.intent_model（双模型分层）"
```

---

## Task 3: intent prompt 强化（ragent-py R1 教训）

**Files:**
- Modify: `backend/app/agent/prompts/intent_prompts.py`

- [ ] **Step 3.1: 写 prompt 段测试（先红）**

在 `backend/tests/smoke/test_intent.py` 新增：
```python
def test_intent_prompt_has_no_thinking_directive():
    """intent prompt 必须含 R1 强化段（防 R1 输出大段推理吃 max_tokens）。"""
    from app.agent.prompts.intent_prompts import build_intent_classify_prompt

    prompt = build_intent_classify_prompt("2024 年各区域销售额")
    # ragent-py 实战：禁思考 + 整个回复即 JSON
    assert "不要在内部思考" in prompt or "不要写任何思考过程" in prompt, (
        "intent prompt 必须含 R1 no_thinking_directive 段"
    )
    assert "整个回复" in prompt, "intent prompt 必须明确输出形态约束"
```

- [ ] **Step 3.2: 跑测试确认红**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest tests/smoke/test_intent.py::test_intent_prompt_has_no_thinking_directive -v
```
预期：FAIL（prompt 当前不含 R1 强化段）

- [ ] **Step 3.3: 加 no_thinking_directive 段**

修改 `backend/app/agent/prompts/intent_prompts.py:12-43`：
```python
INTENT_CLASSIFY_V1: dict[str, str] = {
    "system_contract": (
        "你是 ReportAgent 的意图分类器。职责：判断用户查询属于哪一类，"
        "不做 SQL 生成、不调工具、不重写查询。"
    ),
    # 2026-09-01 双模型分层：intent 走 R1（reasoning 模型）。R1 默认会输出大段
    # 推理吃掉 max_tokens，必须显式禁思考 + 整段即 JSON。参考 ragent-py 实战：
    # 25-30s 长段推理 → 7-17s 稳定 JSON（prompt 强化后）。
    "no_thinking_directive": (
        "【最高优先级 - 输出形态】"
        "不要在内部思考，不要写任何思考过程、推理、解释、开场白或结束语。"
        "你的【整个回复】只能是一个 JSON 对象，直接以 { 开头并以 } 结束，"
        "前后没有任何其他字符。"
        "任何解释、markdown 代码块、或"好的，让我..."之类的话都是错误。"
    ),
    "role": "你是意图分类器。",
    "task_contract": (
        "判断用户查询属于哪类，只输出 JSON，禁止解释。"
        "\n\n用户查询: {user_query}"
        "\n\n类别:"
        "\n- report: 针对数据库星型模型做报表/数据分析（销售额、趋势、排名、退货、库存、考勤等业务指标）"
        "\n- interface: 关于外部接口/实时推送/数据源接入的查询（不是数据库报表），"
        "如「订单接口字段」「实时库存推送」"
        "\n- chitchat: 闲聊或与数据无关的请求"
        "\n- other: 其他"
    ),
    "tool_policy": (
        "本 Agent 不调用任何外部工具；判定仅依据查询语义。"
        "如对类别无把握，confidence 应 ≤ 0.5，由上层决定 fallback。"
    ),
    "output_schema": (
        '输出: {{"kind": "report|interface|chitchat|other", '
        '"confidence": 0.0-1.0, "reason": "简短理由"}}'
    ),
    "safety_policy": (
        "Do NOT invent tables/columns。"
        "Do NOT fabricate query results。"
        "Do NOT assume unavailable schema。"
        "Do NOT call search_schema（意图分类不需要表结构）。"
        "Do NOT generate SQL。"
    ),
}
```

`build_intent_classify_prompt` 在 `no_thinking_directive` 段也加入 sections 列表（在 `system_contract` 之后、`role` 之前）。

修改 `build_intent_classify_prompt` 函数：
```python
def build_intent_classify_prompt(user_query: str) -> str:
    """组装 7 段 + 注入 user_query。返回完整 prompt string。"""
    sections = [
        INTENT_CLASSIFY_V1["system_contract"],
        INTENT_CLASSIFY_V1["no_thinking_directive"],  # R1 强化段
        INTENT_CLASSIFY_V1["role"],
        INTENT_CLASSIFY_V1["task_contract"].format(user_query=user_query),
        INTENT_CLASSIFY_V1["tool_policy"],
        INTENT_CLASSIFY_V1["output_schema"],
        INTENT_CLASSIFY_V1["safety_policy"],
    ]
    return "\n\n".join(s for s in sections if s)
```

- [ ] **Step 3.4: 跑测试确认绿**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest tests/smoke/test_intent.py::test_intent_prompt_has_no_thinking_directive -v
```
预期：PASSED

- [ ] **Step 3.5: 跑全量 intent 测试**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest tests/smoke/test_intent.py -v
```
预期：全绿（含现有用例 + 2 新例）

- [ ] **Step 3.6: Commit**

```bash
git add backend/app/agent/prompts/intent_prompts.py backend/tests/smoke/test_intent.py
git commit -m "feat(intent): prompt 加 R1 no_thinking_directive 段（禁思考 + 整段即 JSON）"
```

---

## Task 4: CLAUDE.md §8 现状标注分层偏离

**Files:**
- Modify: `CLAUDE.md` §8 LLM Policy「现状」段

- [ ] **Step 4.1: 读 CLAUDE.md §8 当前现状**

读 `CLAUDE.md` 找 §8「现状」段，确认位置。

- [ ] **Step 4.2: 替换 §8 现状段**

把：
```
> 现状：app/llm.py call_llm + llm_resilience.py 分散承担；P6 迁移并跑 Golden Set Before/After 对比
```

改为：
```
> 现状（2026-09-01 双模型分层后）：`LLMConfig.intent_model` 字段（env `LLM_INTENT_MODEL`，默认 fallback 到 `LLM_MODEL`）；`_llm_classify`（intent.py:69）显式传 `model=settings.intent_model`；adapter kwargs["model"] 透传（adapter.py:90）无改动。**显式偏离 §8 统一原则**：intent=R1（reasoning 提升语义路由稳定性）+ 其他=V3（成本/延迟）。SiliconFlow provider（OpenAI-compatible，与 embedding 共用），共享 base_url。ragent-py 已实战验证该分层（`D:\PyProject\ragent-py\docs\plans\2026-08-20-intent-vision-model-switch.md` 25-30s → 7-17s，prompt 强化后稳定 JSON）。P6 后续若评估 R1 全场景可用，可再合并回 §8 统一原则。详见 plan `2026-09-01-llm-dual-model-r1-v3.md`。
```

- [ ] **Step 4.3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(llm): §8 现状标注双模型分层偏离（intent=R1 / 其他=V3，SiliconFlow）"
```

---

## Task 5: .env.example 更新 + 收尾

**Files:**
- Modify: `backend/.env.example`
- Modify: `docs/plans/README.md`（登记 plan）
- Modify: `docs/plans/2026-09-01-llm-dual-model-r1-v3.md`（状态 → 已完成 + 落地记录）

- [ ] **Step 5.1: 读 .env.example**

读 `backend/.env.example`，找 LLM 相关段。

- [ ] **Step 5.2: 改 .env.example LLM 段**

更新 LLM_* 配置：
```bash
# ── LLM Provider (P6+ 收敛，MINIMAX_* 兼容旧 .env) ──
# SiliconFlow（OpenAI-compatible）= deepseek-chat / deepseek-reasoner
LLM_BASE_URL=https://api.siliconflow.cn/v1
LLM_API_KEY=<your_siliconflow_key>
# 主模型：requirement parse / SQL plan / SQL generate / Report / 对话
LLM_MODEL=deepseek-chat
# intent 专用：reasoning 模型（默认 fallback LLM_MODEL；不设则与主模型一致）
LLM_INTENT_MODEL=deepseek-reasoner
LLM_TEMPERATURE=0.1
LLM_MAX_RETRIES=2
LLM_MAX_TOTAL_TIME=90
LLM_CONTEXT_WINDOW=131072
LLM_TIMEOUT=60
```

- [ ] **Step 5.3: 全量最终验证**

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest -q
# 预期：≥ 953+3=956 passed / 1 skipped / 5 warnings

cd frontend && npx playwright test --config e2e/playwright.config.ts e2e/specs/0[1-9]-*.spec.ts e2e/specs/10-*.spec.ts
# 预期：10/10 全绿（mock LLM 路径不破）
```

- [ ] **Step 5.4: 登记 plan README.md**

读 `docs/plans/README.md`「进行中」表，加：
```
| [2026-09-01-llm-dual-model-r1-v3.md](2026-09-01-llm-dual-model-r1-v3.md) | 双模型 LLM（intent=DeepSeek-R1 / 其他=DeepSeek-V3 + SiliconFlow）：LLMConfig.intent_model 字段（env LLM_INTENT_MODEL fallback LLM_MODEL）+ intent._llm_classify 显式传 model + intent prompt 加 R1 no_thinking_directive 段 + CLAUDE.md §8 显式偏离标注 | 接 P12 master `e711bcb`；user 主导从 MiniMax 切到 DeepSeek 平台（成本 + reasoning 质量权衡）；独立 4 个 fix commit + 1 个 docs commit |
```

实施完成后把 plan 行从「进行中」移到「已完成」并加 commit 信息。

- [ ] **Step 5.5: 改 plan 顶部状态 + 加落地记录**

修改 `docs/plans/2026-09-01-llm-dual-model-r1-v3.md` 顶部 + 加落地记录段（参考 review-prep-r2 模式）。

- [ ] **Step 5.6: Commit**

```bash
git add backend/.env.example docs/plans/README.md docs/plans/2026-09-01-llm-dual-model-r1-v3.md
git commit -m "docs(llm): .env.example 更新 SiliconFlow + 双模型；plan README 登记 + 收尾"
```

---

## Self-Review

1. **Spec coverage**:
   - LLMConfig intent_model → Task 1 ✓
   - intent 调用 model → Task 2 ✓
   - intent prompt 强化 → Task 3 ✓
   - CLAUDE.md §8 偏离 → Task 4 ✓
   - .env.example + 收尾 → Task 5 ✓

2. **Placeholder scan**: 无 TBD/TODO/fill in details；测试代码完整可跑

3. **Type consistency**:
   - `LLMConfig.intent_model: str` 在 config.py 一处定义
   - `_llm_classify` 调用 `call_llm(prompt, max_tokens=200, model=settings.intent_model)` 签名一致
   - `INTENT_CLASSIFY_V1["no_thinking_directive"]` dict key 与 `build_intent_classify_prompt` 中 sections 引用一致
