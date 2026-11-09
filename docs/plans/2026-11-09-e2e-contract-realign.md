> 状态: 进行中

# e2e-contract 对齐当前对话形态 + 钉死实验开关（夜间 Playwright 红转绿）

## Context

用户贴夜间 `e2e-contract` 运行截图：`contract-e2e` 失败 6m17s，注解指向

```
27 | await expect(page.locator('.wb-req-summary')).toContainText('华东')
   - waiting for locator('.wb-req-summary')   → element(s) not found
```

本地复现（Windows + 本机 PG）得到**同一处失败**，但错误上下文（`error-context.md`）暴露了两层问题，都不是「选错了选择器」这种一层皮：

1. **UI 契约已漂移**：2026-11-04/05 的三次改造（`chat-like-conversation-flow` /
   `requirement-details-inline` / `conversation-transcript-messages` 修订 A4）之后，
   需求确认只剩「一句话 + 三个按钮」，参数卡改成**按需展开**的 `RequirementCardView variant="inline"`；
   缺项澄清改成**一次一问**的 `QuestionBar`。于是

   | 选择器 | 现状 |
   |---|---|
   | `.wb-req-status` / `.wb-req-summary` | 只在点开「需求详情」后才存在 → spec 01 / 09 必红 |
   | `.wb-option-group` / `label.atelier-checkbox` / 「补充完成，查看确认」 | 只剩死代码里的 `variant="full"`（生产已无 caller）→ spec 02 必红 |

   Contract 层是**手动/夜间** workflow（`e2e-contract.yml`，不进 PR 门禁），所以自
   2026-11-05 起一直红而没人被拦。`P13` plan 里记的「spec 05 在 master 一样失败」是同一类漂移的早期征兆。

2. **Contract 层并不 Contract**：本机跑 spec 09 时，第二轮「只看华东」走了**真实 LLM**
   （`已思考（用时 26 秒）`、思考预算用尽、3552 字思维链），并且把需求判成
   `NEEDS_CLARIFICATION / 待确认 metric`。原因：`startContractBackend` 的 env 是
   `{...process.env, ...loadDotEnv()}`——本机 `.env` 里 `TURN_UNDERSTANDING=1` **覆盖**了
   seam，而 Turn Understanding 走的是 `app/llm/reasoning_client`，**不看** `LLM_PROVIDER=mock`。
   于是「mock LLM + real PG」的确定性承诺在开了实验开关的机器上直接失效（CI 无 `.env` 所以没开，
   但这类「本机能跑绿、CI 是另一条路径」的分裂本身就是缺陷）。

结论：这不是单点修选择器，而是「Contract 层必须钉死它到底跑哪条产品路径 + 断言必须打在
A4 之后真实存在的用户可见契约上」。

## Design

### D1 · seam 决定路径（env 赋值顺序即契约）

`frontend/e2e/helpers/llm-mock.ts` 的 `startContractBackend` 里，**在 `.env` 合并之后**显式赋值
Contract 层要固定的键：

```ts
const env = {
  ...process.env,
  ...loadDotEnv(),   // 只为 DATABASE_URL / ANALYSIS_DSN 等运行必需项
  TURN_UNDERSTANDING: '0',   // 实验开关不得经 .env 漏进 Contract 层
  LLM_PROVIDER: 'mock',
  LLM_MOCK_CASE: caseId,
  LLM_MOCK_DIR: CONTRACT_FIXTURES_DIR,
  RAGENT_MCP_PYTHON,
  LLM_MOCK_DELAY_MS: String(delayMs),
}
```

理由：Contract 层的定义是「**出厂默认路径** + mock LLM + 真 PG」。`TURN_UNDERSTANDING` 默认关
（`requirement_analysis_graph.turn_understanding_enabled()` 要求显式 `=1`），所以钉 `0` 不是绕过，
而是**把它钉回默认**；实验路径要测，应该另开 spec 并给它自己的 fixture 体系（见「不做事项」）。

### D2 · page-object 换成「对话形态」的门面

`page-objects.ts` 追加（保留既有 `expectRequirementCard` 语义但改取 `.last()`——历史轮次也在 DOM 里，
`.first()` 会对着上一轮立刻通过，是**假绿**）：

| 门面 | 选择器 | 用途 |
|---|---|---|
| `currentAsk()` | `[aria-label="需求确认"], [aria-label="需求澄清"]` 的 `.last()` | 当前轮那条助手消息（`AssistantTurn` 的 bare 外壳） |
| `expectRequirementAsked(text)` | 同上 + `toContainText` | 断言「听懂了什么」——就是用户能看到的那句话（`ConfirmBar` 的 `wb-ask-text` / `QuestionBar` 的 `wb-ask-text`） |
| `openRequirementDetails()` / `closeRequirementDetails()` | 组内按钮「需求详情」/「收起详情」 | 按需展开后才断言 `.wb-req-status` / `.wb-req-summary`（参数格的契约就在这里） |
| `pickAskOption(label)` | `currentAsk()` 内 `.wb-ask-option-label` 含 label 的按钮 | 一次一问的澄清作答（替代已死的 checkbox 组） |
| `resolveRequirementIfAsked()` | 循环「有澄清就选第一个选项」，最多 6 轮 | Full spec / screenshots 的「把卡补到可确认」 |

`confirmRequirement()` 改为点 `[aria-label="需求确认"] .wb-ask-confirm`——不再用 `.wb-primary`
盲点第一个（报告区里也可能有 primary）。

### D3 · spec 改写（只改断言，不改被测流程）

| spec | 改动 |
|---|---|
| 01-happy-path | `.wb-req-status` 直接断言 → `expectRequirementAsked('销售额')` + `openRequirementDetails()` 后再断言状态 pill |
| 02-clarification | 缺失组 + checkbox + 「补充完成，查看确认」→ 断言澄清消息问的是指标，`pickAskOption('净销售额')`，再等确认条 |
| 09-memory-multiturn | `.wb-req-summary` 无条件断言 → 先 `expectRequirementAsked('华东')`，再展开详情断言 `.wb-req-summary` 含「华东」（保留原意图：收窄真的写进了卡） |
| 11/12（env-gated Full） | 跟随：`.wb-requirement-card` 计数 → 无 `需求确认/需求澄清` 组；`fillMissingIfAny` 40 行手写补全 → `resolveRequirementIfAsked()` |
| screenshots.spec.ts | 同上替换 `fillAllMissingGroups` / `resolveRequirementCard` |

### D4 · 顺带发现：06 / 07 断的是**产品契约**，不是选择器

全量跑（8 passed / 2 failed）之后追下去，这两条不是「选择器烂了」，而是 spec 的前提被后续设计改掉了：

| spec | 原前提 | 现状（证据） |
|---|---|---|
| 06-report-version | 「报告态下 composer 走 adjust，自动出 v2」 | `chatModeForPhase` 自 **2026-11-02** 起 `report_ready` 也返回 **supplement**（溯源注释：追问必须回需求层重新解析，否则「详细讲一下七月份」只会得到同一张卡的第二份报告）。后端日志实锤：第二条 `/chat` 重新进了 requirement 图（`MockLLMMiss intent_classify:2`）→ 任务失败 → 会话 `生成失败`。**并且 200ms 的时序竞争**：`.wb-report-shell` 一出现就发第二条消息，此时 phase 仍是 `generating`，连 supplement 都不是。 |
| 07-background-execution | 「`2024年华南区销售额` → 直接出确认条」 | 2026-11-04 槽位三态之后，模型给的 `analysis_methods: group_compare` **不算数**（日志：`analysis_methods dropped (user never expressed intent)`）→ 缺 `analysis_intent` 槽位 → 先问一次「你想看哪一种视角？」，此时没有 `.wb-ask-confirm`，旧代码的 `.wb-primary` 同样点不到（所以这条在改断言之前也是红的）。 |

处置：
- **06**：改成当前契约——`waitIdle()` 等上一轮真结束（`phase` 已到 `report_ready`）→ 追问 → 重新出需求卡 → 确认 → v2 渲染「商品1」；`report-version.json` 补 `intent_classify:2` / `requirement_parse:2`（补的是**缺的 mock 覆盖**，不改被测语义）。
- **07**：`resolveRequirementIfAsked()` 答掉视角提问再确认。
- 两处都不改产品代码——spec 错在假设，不在实现。

## Files to change

| 文件 | 改动 |
|---|---|
| `frontend/e2e/helpers/llm-mock.ts` | env 赋值顺序 + `TURN_UNDERSTANDING: '0'`（D1） |
| `frontend/e2e/helpers/page-objects.ts` | 新增 D2 五个门面；`expectRequirementCard` 取 `.last()`；`confirmRequirement` 精确到确认按钮 |
| `frontend/e2e/specs/01-happy-path.spec.ts` | 断言搬到「那句话」+ 展开详情 |
| `frontend/e2e/specs/02-clarification.spec.ts` | 改走一次一问 |
| `frontend/e2e/specs/09-memory-multiturn.spec.ts` | 同上（用户报错的这一条） |
| `frontend/e2e/specs/11-chitchat-bubble.spec.ts`、`12-empty-result.spec.ts` | env-gated，跟随新形态 |
| `frontend/e2e/specs/06-report-version.spec.ts` | 追问 = supplement（重新出卡 → 确认 → v2），并 `waitIdle()` 消掉时序竞争（D4） |
| `frontend/e2e/specs/07-background-execution.spec.ts` | 先答掉「视角」澄清再确认（D4） |
| `backend/tests/fixtures/llm_responses/report-version.json` | 补 `intent_classify:2` / `requirement_parse:2`（追问轮的 mock 覆盖） |
| `frontend/e2e/specs/screenshots.spec.ts` | 复用新门面，删两段死选择器补全逻辑 |

## Reused existing utilities

- `AssistantTurn` 已经给两条助手消息挂了 `role="group"` + `aria-label="需求确认" / "需求澄清"`——这就是 A4 之后**稳定且语义化**的锚点，不再依赖卡片内部 class。
- `QuestionBar` / `ConfirmBar` 的既有 class：`.wb-ask-text`、`.wb-ask-option`、`.wb-ask-option-label`、`.wb-ask-confirm`、`.wb-req-summary`（inline 详情）。
- `global-setup.ts` 的 PG/vite 探活、`startContractBackend` 的 mock fixture 目录与 case 机制，零改动。

## Verification

```bash
# 1) 单条（用户报错的这条）：先红后绿
cd frontend && RAGENT_PYTHON=D:/miniConda/envs/agent/python.exe \
  npx playwright test --config e2e/playwright.config.ts e2e/specs/09-memory-multiturn.spec.ts

# 2) 全 Contract 层（10 spec；Windows 下 glob 不展开，需逐个列文件或传 --grep）
cd frontend && npx playwright test --config e2e/playwright.config.ts \
  e2e/specs/01-happy-path.spec.ts e2e/specs/02-clarification.spec.ts \
  e2e/specs/03-retry.spec.ts e2e/specs/04-empty-result.spec.ts \
  e2e/specs/05-failed-result.spec.ts e2e/specs/06-report-version.spec.ts \
  e2e/specs/07-background-execution.spec.ts e2e/specs/08-session-recovery.spec.ts \
  e2e/specs/09-memory-multiturn.spec.ts e2e/specs/10-trace-progress.spec.ts
```

验收标准：
1. 09 由红转绿，且**日志里不再出现真实 LLM 调用**（无 `已思考（用时 N 秒）`、无 `思考预算用尽`——mock 模式下该节点不会走 reasoning client）。
2. 01–10 全绿（env-gated 的 11/12 保持 skip）。
3. `cd frontend && npm run test:run` 不回归（本次不动组件，预期 491 全绿）。
4. 后端零改动（`git diff --stat` 只含 `frontend/e2e/**` + 本 plan + 索引）。

## 落地记录（2026-11-09）

按上面 D1–D4 实施完后，全量 10 条仍有 2 条红，逐条追下去又是「断言写的是旧世界」：

| spec | 残留断言的真相 | 处置 |
|---|---|---|
| 06 | `expectReportContains('商品1')` 的 `商品1` 从来不是真数据——v2 表里是 seed 的真品名（华为 WATCH Ultimate / Apple iPad Pro …）。这条断言在 seed 换成真实商品名那天就失效了，只是被前面更早的红挡住了 | 改钉**契约**而非数据：v2 标题必须来自追问那一轮的需求卡（`2024年各产品销售额`）+ `报告 v2` |
| 07 | toast 文案自 2026-11-03（解析轮也后台化）从「报告已在后台生成，可查看」改成「后台任务已完成，内容已更新」——停止既可能停执行轮也可能停解析轮 | 断言改成新文案；因果链断言（`phase=report_ready` + `report v1` 真落库 `SUCCESS`）保留不动 |

另外两条观察（本轮**不修**，留作已知项）：
- mock 的 `KIND_MARKERS` 不覆盖报告叙述（`report narration failed: 无法从 prompt 识别语义 kind`）——只降级为无叙述文案，不影响断言，但说明 mock 覆盖面仍是「够用」而不是「完整」。
- `e2e:contract` 脚本用 shell glob（`0[1-9]-*.spec.ts`），Windows 的 npm 不走 bash，**本地跑必须逐个列文件**（CI 是 bash 所以无感）。本轮未改脚本形态，因为改它要引入跨平台 glob 依赖。

## Explicitly NOT doing
- **不给 Turn Understanding 补 mock fixture**：它走 `app/llm/reasoning_client`（不是 `LLM_ADAPTER`），
  要 mock 就得让 reasoning client 认识 `LLM_PROVIDER=mock`，属于 Adapter 层设计决策（P6 边界），
  不在「修红」的范围里。本轮只保证实验开关不进 Contract 层；等它默认翻正时再单独开 plan 补
  `turn_plan:{case}:{seq}` fixture 体系。
- **不恢复整卡 UI**：`RequirementCardView variant="full"` 已是死代码（生产零 caller），本轮不清除它
  （清理属于 UI 收尾），但也绝不为了让 e2e 好写把它请回主流程。
- **不动 `e2e-contract.yml`**：workflow 结构（服务、seed、artifacts）本身没问题，红的根因在 spec 与 seam。
- **不把 Playwright 塞回 PR 门禁**：仍按 2026-10-28 plan 的结论（手动/夜间）。
