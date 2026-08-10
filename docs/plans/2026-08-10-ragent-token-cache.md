# 2026-08-10 ragent-py token 跨进程共享缓存：根治登录 429

> 状态: 已完成（ReportAgent commit `7a3fc1d`；含真实跨进程 E2E）

## Context

ragent-py `/api/v1/auth/login` 限流 **每 IP 5 分钟 10 次**（`_LOGIN_MAX_ATTEMPTS=10`/`_LOGIN_WINDOW=300s`）。多个消费方各自登录、进程一重启就撞限流：

- ragent-py `mcp_server/client.py::RagentClient`——**每次实例都重新登录**，无缓存。
- ReportAgent `interface_dict_tools.py` / `rag_schema.py`——进程内缓存（`_token_cache`），跨进程不共享。
- ReportAgent `mcp_schema_server/registry.py`——进程内缓存，跨进程不共享。

用户反馈：频繁 429「不方便正式使用」。进程内缓存解决不了跨进程/重启，需**跨进程共享 token 缓存**。

token 有效期 24h（`access_token_expire_minutes=1440`）。

## Design

文件持久化的跨进程 token 缓存，三个登录点共享同一缓存文件 → 全系统**约 1 次登录 / token 生命周期**，401 自动失效重登。缓存文件格式：

```json
{ "<RAGENT_URL>": { "token": "eyJ...", "expires_at": <unix> } }
```

### 1. 缓存 helper（三处各一，同格式）

`get_token(base_url) -> str | None`（未过期才返回）、`set_token(base_url, token)`、`invalidate(base_url)`：

- 路径 `RAGENT_TOKEN_CACHE`（默认 `~/.ragent_token_cache.json`）。
- TTL `RAGENT_TOKEN_TTL`（默认 `20*3600`，24h token 留 4h 安全余量）。
- `threading.Lock` 防进程内并发；写文件 try/except（写失败不影响主流程，下次登录重试）。
- 文件读写是纯 IO，同步即可（登录路径罕见调用）。

放这四处，避免 import 耦合：

- `ragent-py/mcp_server/token_cache.py`（`client.py` 用）。
- `ReportAgent/backend/app/tools/ragent_token_cache.py`（`interface_dict_tools.py` 用；`rag_schema.py` 走 interface_dict 的登录 helper，自动共享）。
- `ReportAgent/mcp_schema_server/token_cache.py`（`registry.py` 用，独立包不 import backend）。

### 2. 接入各登录点

| 位置 | 改动 |
|---|---|
| ragent-py `client.py::_login` | 先查文件缓存，命中则 `self._token = cached` 直接返回；否则真实登录后 `set_token`。`_request` 的 401 分支先 `invalidate` 再重登（防陈旧 token 死循环重登）。 |
| ReportAgent `interface_dict_tools.py::_login_token` | 先查文件缓存，命中直接返回；否则登录后 `set_token`。401 重登前 `invalidate`。`_token_cache` 内存缓存保留（进程内更快的首层）。 |
| ReportAgent `mcp_schema_server/registry.py::_login_token` | 同上。 |

### 3. 安全

- token 是 JWT（24h），缓存 TTL 20h 留余量；服务端吊销/过期 → 请求 401 → `invalidate` + 重登（复用既有 401 重登链路）。
- 不缓存密码，只缓存 token；缓存文件属服务账号 token，权限靠文件系统（默认用户目录）。

## Files to change

- `ragent-py/mcp_server/token_cache.py`（新建）+ `ragent-py/mcp_server/client.py`（接入）。
- `ReportAgent/backend/app/tools/ragent_token_cache.py`（新建）+ `backend/app/tools/interface_dict_tools.py`（接入）。
- `ReportAgent/mcp_schema_server/token_cache.py`（新建）+ `mcp_schema_server/registry.py`（接入）。
- `docs/plans/2026-08-10-ragent-token-cache.md`（本文件）；两仓库各登记 plan。

## Reused existing utilities

- 既有 401 重登链路（`_request` / `_login_token` 的 401 分支）——`invalidate` 挂上去即可。
- `interface_dict_tools._token_cache` 内存缓存——保留为首层，文件缓存为跨进程层。

## Verification

```bash
# ragent-py
D:/miniConda/envs/rag/python.exe -m pytest tests/unit/test_dict_client.py tests/unit/test_faq_server.py -q
D:/miniConda/envs/rag/python.exe -m pytest tests/unit -q

# ReportAgent
cd backend && pytest tests/smoke/test_ragent_token_cache.py tests/smoke/test_rag_schema.py -q
cd backend && pytest -q
```

新增测试：

1. `token_cache` 单元：`set_token` 后 `get_token` 命中；TTL 过期 `get_token` 返回 None；`invalidate` 清空。
2. `interface_dict_tools._login_token`：预置文件缓存 → 不调 ragent-py 登录；无缓存 → 登录并写文件。
3. ragent-py `RagentClient._login`：预置文件缓存 → 不调 HTTP 登录；无缓存 → 登录写文件。

真实冒烟（起 ragent-py）：

1. 两个**独立进程**连续调 `search_faq`/`search_tables` → 第二个复用第一个写的 token，**登录次数不增**（数 ragent-py 登录日志）。
2. 手动改缓存文件 token 为假值 → 下一次请求 401 → 自动失效重登 → 恢复。

## 落地记录（2026-08-10，真实跨进程 E2E）

- 起 ragent-py 实测：进程 A（`rag_schema`→`interface_dict`→`ragent_token_cache`）登录 1 次写缓存；进程 B（`MCPFaqClient`→新起 ragent mcp_server 子进程→`RagentClient`）**复用 A 的 token，0 次新登录**，检索正常。跨消费方、跨进程、含子进程全共享。
- 三处缓存 helper（ragent-py `mcp_server/token_cache.py`、ReportAgent `app/tools/ragent_token_cache.py`、`mcp_schema_server/token_cache.py`）同格式共享同一文件；401 自动 `invalidate` 重登。
- ragent-py 测试经 conftest autouse fixture 隔离缓存路径，避免测试登录污染真实缓存文件。
- 全量：ragent-py 216 passed、ReportAgent 356 passed。

## Explicitly NOT doing

- **不做** PG 存 token——共享文件足够，PG 会增加耦合与迁移。
- **不做** 放宽/绕过 ragent-py 登录限流——保留现有安全边界，用减少登录次数根治。
- **不做** 把 4 处缓存 helper 合并成跨仓库共享库——延续双份部署，避免 sys.path/部署耦合。
- **不做** token 刷新（refresh token）——ragent-py 只有 access_token，过期就重登（缓存随 401 失效）。