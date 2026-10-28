> 状态: 进行中

# LLM Provider 切换：MiniMax → SiliconFlow

## Context

本机跑端到端验证时，所有 LLM 环节（需求解析 / SQL 生成 / 报告洞察）全部降级，日志统一报：

```
parse_requirement LLM failed: Error code: 402 - insufficient_balance_error (1008)
```

MiniMax 账号余额耗尽，链路退化为 `_fallback_missing_card`（把 5 个维度全部回问用户），表面症状是「需求卡什么都没解析出来」，容易误判成代码 bug。

同时 SiliconFlow 的 key 有余额，且 embedding 本来就走它（`SILICONFLOW_API_KEY` + `Qwen/Qwen3-Embedding-8B`，1536 维已通过启动校验）。Chat 与 embedding 共用一个 provider 可以少维护一套密钥。

用户决策：**把 MiniMax 全部换成 SiliconFlow**。

## Design

三层一起切，避免「配了 `.env` 但代码兜底仍然回 MiniMax」：

1. **运行时配置（`.env`）**
   - 删除 `MINIMAX_API_KEY`；新增 `LLM_API_KEY` = 既有 `SILICONFLOW_API_KEY` 的值（同一把 key）。
   - `LLM_MODEL=deepseek-ai/DeepSeek-V3`（reasoning-capable，符合宪法 §8「一个 reasoning-capable chat model」）。
   - `LLM_BASE_URL=https://api.siliconflow.cn/v1`。
   - `LLM_CONTEXT_WINDOW=65536`（DeepSeek-V3 实际窗口；旧默认 131072 是 MiniMax 口径，会让 Context Runtime 的 `remaining_token_budget` 估算偏大）。

2. **代码兜底默认值（保留 `MINIMAX_*` 别名读取，只改 fallback 常量）**
   - `backend/app/llm/config.py`：`model` 兜底 → `deepseek-ai/DeepSeek-V3`；`base_url` 兜底 → SiliconFlow；`provider` 默认 → `siliconflow`。
   - `backend/app/llm_legacy.py`：同上（legacy 路径的 `_LLM_CONFIG` 是模块级常量）。
   - `backend/app/infra/memory/mem0_extractor.py`：mem0 的 LLM config 复用同一组 env，兜底同步。
   - **别名机制不动**：`LLM_API_KEY` 缺失时仍回退读 `MINIMAX_API_KEY`，由 `tests/contracts/test_llm_adapter.py::test_settings_alias_minimax` / `test_settings_llm_overrides_minimax` 钉住；改这里会直接破契约。

3. **模板与文档**
   - `backend/.env.example`：主 key 改为 `LLM_API_KEY`，`MINIMAX_API_KEY` 标注为历史别名，补 `LLM_CONTEXT_WINDOW`。
   - `README.md`（技术栈 / 环境要求 / 快速开始）、`AGENTS.md`（Setup + Known Quirks）、`CLAUDE.md`（Configuration 表 + P6 注记）同步。
   - `docs/plans/*` 历史 plan **不改**（设计文档是不可变记录）。

## Files to change

| 文件 | 变更 |
|---|---|
| `.env` | 删 `MINIMAX_API_KEY`；加 `LLM_API_KEY` / `LLM_CONTEXT_WINDOW`；改 `LLM_MODEL` / `LLM_BASE_URL`（已在 `.env.bak-20261028` 备份） |
| `backend/app/llm/config.py` | 三个兜底默认值 |
| `backend/app/llm_legacy.py` | `_LLM_CONFIG` 兜底 |
| `backend/app/infra/memory/mem0_extractor.py` | mem0 LLM 兜底 |
| `backend/.env.example` | LLM 段重写 |
| `README.md` / `AGENTS.md` / `CLAUDE.md` | 配置描述 |
| `docs/plans/README.md` | 登记本 plan |

## Reused existing utilities

- `app/llm/config.py` `LLMConfig` 已是单一配置入口（P6 落地），无需新增 adapter 或 provider 分支。
- `app/llm/adapter.py` 走 OpenAI 兼容协议，`base_url` 换掉即可，**零代码改动**。
- `tests/contracts/test_llm_adapter.py` 的别名回退测试直接复用为回归保护。
- `evaluation/runner.py` 复用为端到端验证器（自带 login → chat → PATCH → confirm → 读报告）。

## Verification

1. 后端启动自检：`Embedding dimension verified: 1536 == 1536`、`/health` 200。
2. 需求解析真实回归（此前 402 降级）：
   ```
   POST /api/v1/chat  {"user_query":"2024年各区域销售额排名","mode":"new"}
   → status=missing / time_range=2024年 / target_metrics=[销售额] / dimensions=[区域] / confidence=0.9
   ```
3. 端到端（SQL + 报告）：`python -m evaluation.runner --base-url http://127.0.0.1:8100 --only-category explicit_query`
   → **2/2 pass**，`agent.report_version.query_snapshot.sql` 真 SQL、`rows` 非空（实测 6 行区域汇总）。
4. 契约回归：`cd backend && pytest tests/contracts/test_llm_adapter.py -q` → **14 passed**。
5. 端口联通：`:3000`（Vite）→ `/api` proxy → `:8100`；`:8000`（ragent-py）供 MCP 字典检索。

## Explicitly NOT doing

- 不删除 `MINIMAX_*` 别名读取（契约测试钉住，且兼容旧部署）。
- 不改 `LLM_CONTEXT_WINDOW` 的**代码默认值**（`test_context_window_default` 断言 131072）；只在 `.env` 覆盖。
- 不改历史 plan 文档与 `docs/architecture/*` 的宪法条款。
- 不引入多 provider 路由 / 双模型（`2026-09-01-llm-dual-model-r1-v3.md` 仍为暂缓）。
- 不改 `app/utils/text.py`、`reliability/retry.py` 里提到 MiniMax 的注释性文案（非配置）。
