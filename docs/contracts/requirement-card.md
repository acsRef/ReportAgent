# RequirementCard Shared Contract

> Pydantic ↔ TypeScript 手工镜像。任何字段/枚举/状态新增必须两侧同一提交更新；后端字段名使用 snake_case，前端使用同样的 snake_case key（这是我们刻意保持的镜像选择，而不是 TS 习惯的 camelCase，方便 SSE payload 直接消费）。

## Field table

| Field | Type (Pydantic) | Type (TS) | Required | Notes |
| --- | --- | --- | --- | --- |
| `id` | `str` | `string` | 是 | 稳定 ID：`{session_id}-v{version}` |
| `version` | `int >= 1` | `number` | 是 | 每次后端补丁递增 |
| `status` | `Literal['missing','complete','locked']` | union | 是 | 与下面 invariant 联动 |
| `summary` | `str` | `string` | 是 | 一句话业务目标 |
| `target_metrics` | `list[str]` | `string[]` | 否 | 缺省 `[]` |
| `time_range` | `str \| None` | `string \| null` | 否 | |
| `scope` | `list[str]` | `string[]` | 否 | 业务范围标签 |
| `dimensions` | `list[str]` | `string[]` | 否 | 维度标签 |
| `analysis_methods` | `list[str]` | `string[]` | 否 | 业务名称，不暴露 tool key |
| `expected_blocks` | `list[str]` | `string[]` | 否 | 报告将包含的区块 |
| `missing_fields` | `list[RequirementMissingField]` | `RequirementMissingField[]` | 否 | 缺省 `[]` |
| `assumptions` | `list[RequirementAssumption]` | `RequirementAssumption[]` | 否 | 缺省 `[]` |
| `confidence` | `float [0,1]` | `number` | 否 | 缺省 `0.0` |
| `confirmed_at` | `datetime \| None` | `string \| null` | 否 | ISO8601 字符串 |

## Nested types

### RequirementMissingField

| Field | Type | Required |
| --- | --- | --- |
| `key` | `Literal['time_range','scope','metric','comparison','granularity']` | 是 |
| `label` | `str` | 是 |
| `kind` | `Literal['single','multiple']` | 否，缺省 `'single'` |
| `options` | `RequirementOption[]` | 否，缺省 `[]` |

### RequirementOption

| Field | Type | Required |
| --- | --- | --- |
| `label` | `str` | 是 |
| `value` | `str` | 是 |

### RequirementAssumption

| Field | Type | Required |
| --- | --- | --- |
| `key` | `str` | 是 |
| `text` | `str` | 是 |
| `accepted` | `bool \| None` | 否 |
| `alternatives` | `RequirementOption[]` | 否，缺省 `[]` |

## Status invariants

通过 Pydantic `model_validator` 与前端 `isRequirementReadyForConfirmation` 同时强制：

1. `status == 'missing'` ⇒ `missing_fields.length >= 1`
2. `status in {'complete','locked'}` ⇒ `missing_fields.length == 0`
3. `status in {'complete','locked'}` ⇒ 所有 `assumptions.accepted` 必须非 null（已接受或已拒绝）
4. `status == 'locked'` ⇒ `confirmed_at != null`
5. `status != 'locked'` ⇒ `confirmed_at == null`

## PATCH flow

- 客户端发送 PATCH 携带新 `RequirementCard` 草稿（缺省忽略服务端的 id/version 字段）
- 服务端在事务内：
  1. 校验 session 与 user_id 归属；
  2. 加载 latest draft；
  3. 重算 `missing_fields / status / version`；
  4. 写新 draft 行；
  5. 返回新的 RequirementCard（version 自增）。

## Confirm flow

- 服务端在事务内校验：
  - session.user_id == jwt.user_id
  - latest draft 存在
  - `status == 'complete'`
  - 所有 assumptions accepted
- 校验通过后进入 confirmed-execution graph，写 report v1 + 更新 session phase + 写 conversation pointer，全部同一事务。

## SQL gate

- `requirement-analysis` 流程中不注册 `validate_sql` / `execute_sql` / 任何 Report Agent tool。
- 测试通过 spy 校验 `validate_sql` / `execute_sql` 在该流程调用次数为 0。
