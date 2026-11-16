> 状态: 已完成（2026-11-16）

# CI 红转绿：全新库上的三处「只在本地成立」的假设

## Context

ReportAgent 的 CI（`.github/workflows/ci.yml`）在 `8de689d` 上是红的。用 GitHub check-runs 的
**匿名可读 annotations**（workflow 里 `tail -50 | ::error::` 的设计正是为此）定位到：

```
backend/tests/persistence/test_session_delete.py:46: in _user_id
    owner = await _user_id(get_pool())
```

而且不是这一笔引入的：`ci` 的通过史是 `689c445`（2026-11-11 12:44）绿，之后 `ae479ec / 907c74d /
7e1275e / 8de689d` **连续四次红**——从「会话硬删除」那次提交开始。

本地全量一直是绿的（1909 passed），所以问题不在代码逻辑，而在**本地库与 CI 库的形状不同**：

| 假设 | 本地为什么成立 | CI（全新库）为什么炸 |
|---|---|---|
| `app.users` 里至少有一条用户 | 跑过应用，`admin` 在 | `init_pg.sql` 不建用户，断言直接失败 |
| `public.checkpoints` / `checkpoint_writes` / `checkpoint_blobs` 存在 | 启动过后端 → `AsyncPostgresSaver.setup()` 建过 | 从未启动应用 → 三张表**不存在**，`delete_session` 抛 `UndefinedTableError` |
| `agent.analysis_task_revision` 存在 | 某次手工 DDL 建的 | `init_pg.sql` 里**没有这段 DDL**，全新库缺表 |

顺带发现第四个「只在本地成立」：17 个 persistence 测试把 DSN **硬编码**成 `localhost:5432/ragent`，
不认 `DATABASE_URL`——本地恰好同一个库所以绿；一旦要跑第二个库（本轮的验证手段）就全部读错库。

## 设计与改动

1. **`init_pg.sql` 补 `agent.analysis_task_revision`**（`CREATE TABLE IF NOT EXISTS` + 索引）。
   它是 Turn Understanding 的审计表，代码一直在写（`infra/db/task_revision_repository.py`），
   但 schema 引导脚本漏了——**新环境按文档装完就是缺表**，这是产品侧的缺陷，不只是测试问题。
2. **`session_deletion` 容忍可选表**：级联前查一次 `information_schema`，表不存在就跳过并 `counts[label]=0`。
   dev 用 `MemorySaver`（没有 checkpoint 表）时删会话本来就不该 500——这是**真缺陷**，CI 帮我们抓到了。
3. **两个 persistence 文件自己造用户**（`INSERT ... RETURNING id`），测试结束删掉；
   顺带删掉当年「借库里现成一条」的 `_user_id`。
4. **`test_session_delete` 自建 checkpoint 三件套**（`AsyncPostgresSaver.setup()`，幂等）；
   Windows 下 psycopg async 不能用默认 ProactorEventLoop，切 selector policy（Linux CI 无此问题）。
5. **17 个 persistence 测试改读 `DATABASE_URL`**（`os.getenv(..., 默认 localhost/ragent)`）。

## 验证

**复现 CI 的条件**（不再靠「本地绿」猜）：在同一个容器里新建一个空库，只跑初始化脚本：

```bash
docker exec ragent-postgres psql -U ragent -d postgres -c "CREATE DATABASE ragent_ci"
psql -d ragent_ci -f backend/scripts/init_pg.sql
psql -d ragent_ci -f backend/scripts/seed_business_p15prelude.sql
psql -d ragent_ci -f backend/scripts/setup_app_role.sql
DATABASE_URL=...ragent_ci ANALYSIS_DSN=...ragent_ci pytest -q
```

结果：

| 阶段 | 结果 |
|---|---|
| 修复前（空库） | `test_session_delete` + `test_failure_recovery_pg` 4 失败：`app.users 为空` → 修完用户又撞 `checkpoint_writes 不存在` → 再修又撞 `analysis_task_revision 不存在` |
| 修复后（空库） | 两个文件 **7 passed** |
| 修复后（空库全量） | **1909 passed / 19 skipped**（与本地同数） |
| 本地主库全量 | 1909 passed（无回退） |

## Explicitly NOT doing

- 不把 CI 改成「跑第二次库」：CI 本来就只有一个空库，问题在我们对它的假设；
- 不给 persistence 测试加「缺表就 skip」：那是把产品缺陷藏起来；
- 不引入迁移框架（alembic 等）：项目现状是「一份 init_pg.sql 引导」，本轮只把它补完整。
