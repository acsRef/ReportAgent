# SSE Protocol v2

> 后端流式事件协议，与 [`2026-07-24-conversational-workbench.md`](../plans/2026-07-24-conversational-workbench.md) 第 5 节配套。

## 1. 事件总览

| Event | 触发 | Payload 关键字段 | 权威度 |
| --- | --- | --- | --- |
| `phase` | 服务端主动推送阶段变化 | `phase`, `reason?` | 权威，UI 必须以 phase 为准 |
| `requirement` | requirement-analysis 流程产出 | `requirement: RequirementCard` | 权威 |
| `trace` | 各节点/工具 start/end | `step, status, detail?` | 进度展示 |
| `thinking` | 关键 LLM 调用前 | `phase, text?` | 提示 |
| `report` | confirmed-execution 完成 | `version, parent_version, title, answer, trace?` | 权威 |
| `error` | 任意错误 | `code, message, recoverable, failed_action?` | 终态 |
| `done` | 流结束 | `final_phase` | 终态 |

## 2. 事件 schema

### phase

```json
{ "phase": "awaiting_confirm", "reason": "requirement_complete" }
```

合法 phase 值：

- `idle`
- `parsing`
- `awaiting_missing`
- `awaiting_confirm`
- `generating`
- `adjusting`
- `report_ready`
- `error`

非法值会被前端 reducer 拒绝并保留上一阶段。

### requirement

```json
{ "requirement": RequirementCard }
```

完整 RequirementCard payload；前端用 `isRequirementReadyForConfirmation` 重新计算 status。

### report

```json
{
  "version": 2,
  "parent_version": 1,
  "title": "华东与华南经营对比",
  "answer": {
    "text": "...",
    "table": null,
    "chart": { "type": "bar", "config": {...}, "data": [...] },
    "insight": "..."
  },
  "trace": [...]
}
```

### error

```json
{
  "code": "QUERY_FAILED",
  "message": "查询执行失败",
  "recoverable": true,
  "failed_action": "confirm"
}
```

`failed_action ∈ { new, supplement, confirm, adjust, retry, null }`。

### done

```json
{ "final_phase": "report_ready" }
```

## 3. 向后兼容

- 旧事件 `card / clarify / token` 保留 1 个 epoch
- 旧客户端仍可消费；新客户端以 `phase / requirement` 为准
- `card` envelope 类型 `intent_card / options_group / confirm_card / preview_card` 继续存在但仅作 legacy 渲染

## 4. 解析器

前端 `parseAnalysisSSEEvent` 必须：

- 在解析失败时返回 `null`，不能抛出
- 在事件类型不在合法集合时返回 `null`
- 解析 `phase` 时使用 `ANALYSIS_PHASES` 白名单
- 解析 `error` 时校验 `failed_action` 在合法集合
- 解析 `report` 时确认 `report: object` 非空
- 解析 `done` 时确认 `final_phase` 在合法集合

## 5. 事件时序

requirement-analysis 流程：

```text
phase(parsing) → thinking → trace(nodes...) → requirement → phase(awaiting_missing | awaiting_confirm) → done
```

confirmed-execution 流程：

```text
phase(generating) → thinking → trace(nodes...) → report → phase(report_ready) → done
```

adjust 流程：

```text
phase(adjusting) → thinking → trace(nodes...) → report → phase(report_ready) → done
```

错误流程：

```text
... → error → phase(error) → done
```
