# 测试脚手架模板（Agent 项目）

从 `docs/testing-playbook.md` 抽出的一套**可整包复制**的文件。新 agent 项目按下面顺序落地，
每件都能在半小时内接上。

```
agent-testing/
├── README.md                        # 本文件：落地顺序
├── CHECKLIST.md                     # 一页检查表（复制进新项目 README）
├── pytest.ini.snippet               # marker 约定 + 只跑某层的命令
├── conftest_gate.snippet.py         # env-gate：缺 key 时 skip 而不是 fail
├── live-eval.yml.template           # L2 真链路流水线（nightly + dispatch + 失败开 Issue）
└── prompt_behavior_cases.template.json  # prompt 行为用例 schema（含 2 个示例）
```

## 落地顺序（与 playbook §四 对应）

1. **D0**：把 `pytest.ini.snippet` 的内容并进仓库根的 `pytest.ini`，把 `conftest_gate.snippet.py`
   放进 `tests/conftest.py`（按注释改 env 名）→ 离线/真链路两条路就分开了。
2. **D1**：先写契约层（工具 description、状态 schema、事件面）。本模板不含这层，因为契约是业务相关的。
3. **D3**：按 `live-eval.yml.template` 建 `.github/workflows/live-eval.yml`，改三处：
   `secrets` 名、`pip install` 路径、跑哪几个 live 用例。
4. **D4**：按 `prompt_behavior_cases.template.json` 写 10–15 条 prompt 行为用例。
5. **D5–D6**：把 `CHECKLIST.md` 抄进 README，跑一次夜间确认报警链路。

## 三条硬约束（抄模板时最容易改坏的地方）

1. **缺 secret 时只能 skip**：`job` 保持绿色 + 打印 `::warning::`。红了会训练人无视 CI；
   假装通过则更糟（绿着没证据）。
2. **手动触发失败不刷 Issue**：`notify-failure` 的 `if` 里必须有 `github.event_name == 'schedule'`。
3. **`needs` + `if: always()`**：普通回归排在真链路之后，但真链路红了也要跑完普通回归——
   否则前一个信号会掩盖后面的。
