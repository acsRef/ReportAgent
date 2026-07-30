# Plan: Auth Secret Hardening — 默认密钥/默认账号安全加固

> 状态: 已完成（B-1 落地：startup_guard fail-closed + ensure_default_user 弱密码检查 + 10 测试）  
> **优先级**：P0（安全）  
> **关联来源**：[2026-07-30-bug-review.md](2026-07-30-bug-review.md) B-1  
> **索引**：[2026-07-30-index.md](2026-07-30-index.md)

## 背景（Context）

当前后端存在一个**部署期安全漏洞**：

- `JWT_SECRET` 有默认值：`reportagent-dev-secret-key-change-in-production`
- `DEFAULT_USERNAME` / `DEFAULT_PASSWORD` 有默认值：`admin` / `admin123`

这导致一个真实风险：

1. 生产环境如果直接复制 `.env.example` 或忘记显式配置 `JWT_SECRET`
2. 应用仍会正常启动
3. 攻击者可以使用公开已知的默认密钥签发 token，或直接使用默认管理员账号登录
4. 结果是**远程认证绕过 / 管理员权限接管**

当前代码的问题不是“功能缺失”，而是**启动期没有安全闸门**：  
即使配置明显不安全，服务仍然允许运行。

本计划的目标是：

- **在不破坏本地开发体验的前提下**
- **在启动阶段阻止不安全默认配置进入生产运行态**
- 让“忘记配置”变成**显式失败**，而不是隐性漏洞

---

## 设计（Design）

### 核心原则

1. **本地开发可继续默认启动**
2. **非本地环境必须显式配置**
3. **安全检查必须发生在应用启动期，而不是请求期**
4. **失败必须快速、明确、可诊断**
5. **不引入复杂密钥管理系统，不做 KMS/Vault 集成（本轮不做）**

---

## 判定“生产/非生产”的最小可靠方式

采用**fail-closed** 设计：  
**默认按最严格环境处理**。未显式声明开发环境时，一律按生产级规则校验。

### 条件 1：显式运行环境标记
读取环境变量：

- `APP_ENV`（推荐）

取值约定：

- `development`
- `staging`
- `production`

**若未设置，默认按 `production` 处理。**

这意味着：

- 照抄 `.env.example` 但未补 `APP_ENV=development` 的部署，不会自动获得宽松校验
- “忘记配置”会变成显式启动失败，而不是隐性放行

### 条件 2：显式允许不安全默认值（仅开发逃生门）
读取环境变量：

- `ALLOW_INSECURE_DEFAULT_AUTH`

取值约定：

- `true` / `1` 表示允许
- 其他一律不允许

**重要约束**：  
`ALLOW_INSECURE_DEFAULT_AUTH` **只在 `APP_ENV=development` 下生效**。  
在 `staging` / `production` 下，该变量**一律被忽略**。

---

## 启动期校验规则

### 规则 A：JWT_SECRET 校验

启动时必须满足以下之一：

1. `JWT_SECRET` 已显式设置
2. 当前为开发环境且显式允许不安全默认值

否则启动失败。

### 规则 B：JWT_SECRET 强度校验

当环境为 `staging` 或 `production` 时：

- `JWT_SECRET` 长度必须至少 32 字符
- 不能等于默认开发密钥字面值

否则启动失败。

### 规则 C：默认管理员密码校验

本规则不仅校验“本次是否创建默认用户”，还必须覆盖“库里已有默认弱密码账户”的历史后门。

#### C1：配置项校验
在非开发环境下：

- `DEFAULT_PASSWORD` 不能等于默认字面值 `admin123`

否则启动失败。

#### C2：存量账户校验
在非开发环境下，如果数据库中已存在默认用户名：

- 必须检查该账户当前密码哈希是否匹配默认弱密码 `admin123`
- 如果匹配，**同样拒绝启动**

原因：

- `ensure_default_user()` 对“已存在用户”会直接跳过创建
- 如果只校验“本次是否创建”，则开发期建过的 `admin/admin123` 账户会被直接带入生产，guard 被绕过

#### C3：开发环境豁免
只有当：

- `APP_ENV=development`
- 且 `ALLOW_INSECURE_DEFAULT_AUTH=1`

时，C1/C2 可豁免。

否则一律启动失败。

---

## 建议实现位置

### 1）新增配置校验模块

建议新增：

- `backend/app/infra/auth/startup_guard.py`

职责单一：只做启动期 auth 安全校验。

### 2）在应用 lifespan 中接入

在 `backend/app/main.py` 的 `lifespan()` 中，初始化顺序应调整为：

1. 读取配置
2. **执行 auth 安全校验**
3. 初始化 PG pool
4. 创建默认用户
5. 检查 embedding dimension
6. 编译 graph

这样不安全配置会在最早阶段失败。

---

## 建议接口设计

### `validate_auth_security_config()`

输入：从环境变量读取的配置值  
输出：无；不通过时直接 `raise`

建议异常语义：

- `RuntimeError("JWT_SECRET is not configured for production")`
- `RuntimeError("JWT_SECRET is too weak for production")`
- `RuntimeError("DEFAULT_PASSWORD is insecure and cannot be used outside development")`

---

## 配置语义（最终版）

### 本地开发（必须显式声明）
```
APP_ENV=development
ALLOW_INSECURE_DEFAULT_AUTH=1
JWT_SECRET=reportagent-dev-secret-key-change-in-production
DEFAULT_USERNAME=admin
DEFAULT_PASSWORD=admin123
```
可启动。

> 注意：本地开发不再是“未配置即宽松”，而是**必须显式写出** `APP_ENV=development`。

---

### 生产 / 预发（必须）
```
APP_ENV=production
JWT_SECRET=<random-32+-chars>
DEFAULT_USERNAME=admin
DEFAULT_PASSWORD=<strong-password>
```
可启动。

> `ALLOW_INSECURE_DEFAULT_AUTH` 在非开发环境下无效，不应依赖它放行。

---

### 生产 / 预发（错误示例 1：未设 APP_ENV）
```
JWT_SECRET=reportagent-dev-secret-key-change-in-production
DEFAULT_PASSWORD=admin123
```
必须拒绝启动。

原因：`APP_ENV` 缺失时默认按 `production` 处理。

---

### 生产 / 预发（错误示例 2：显式 production）
```
APP_ENV=production
JWT_SECRET=reportagent-dev-secret-key-change-in-production
DEFAULT_PASSWORD=admin123
```
必须拒绝启动。

---

## 文件改动（Files to change）

- `backend/app/infra/auth/startup_guard.py`（新增）
  - 实现启动期 auth 安全校验逻辑
- `backend/app/main.py`
  - 在 `lifespan()` 中调用 `validate_auth_security_config()`
- `backend/.env.example`
  - 显式加入 `APP_ENV=production`
  - 将 `JWT_SECRET` 改为占位符 `<CHANGE_ME>`
  - 将 `DEFAULT_PASSWORD` 改为占位符 `<CHANGE_ME>`
  - 保留注释：仅本地开发可改为 `APP_ENV=development` 且 `ALLOW_INSECURE_DEFAULT_AUTH=1`

> 这样做的目的：让“照抄 example”默认进入 fail-closed 路径，而不是默认宽松。
- `CLAUDE.md` / `README.md`
  - 增补“生产启动前置要求”说明
- 测试文件（新增）
  - `backend/tests/infra/test_auth_startup_guard.py`
  - 该文件为纯配置校验测试，**不依赖 PostgreSQL**
  - 统一标记为 `pytest.mark.smoke`

---

## 复用工具（Reused existing utilities）

- 现有 `backend/app/infra/auth/jwt.py`
  - 作为 `JWT_SECRET` 来源点参考
- 现有 `backend/app/infra/auth/repository.py`
  - 作为默认用户创建路径参考
- 现有 `backend/app/main.py` lifespan 初始化链路
  - 直接接入启动校验

---

## 验证（Verification）

### 单元测试

新增测试至少覆盖：

1. **开发环境允许默认值**
   - `APP_ENV=development`
   - `ALLOW_INSECURE_DEFAULT_AUTH=1`
   - 默认 secret / 默认密码可启动

2. **未设置 APP_ENV 时默认按 production 处理**
   - 不设 `APP_ENV`
   - 使用默认弱 secret / 弱密码
   - 必须抛错

3. **生产环境禁止默认 secret**
   - `APP_ENV=production`
   - `JWT_SECRET` 为默认字面值
   - 必须抛错

4. **生产环境禁止弱 secret**
   - `APP_ENV=production`
   - `JWT_SECRET` 长度不足 32
   - 必须抛错

5. **生产环境禁止默认密码（配置项）**
   - `APP_ENV=production`
   - `DEFAULT_PASSWORD=admin123`
   - 必须抛错

6. **生产环境禁止存量弱密码账户**
   - `APP_ENV=production`
   - 数据库中已存在默认用户名
   - 其密码哈希匹配默认弱密码
   - 必须抛错

7. **ALLOW_INSECURE_DEFAULT_AUTH 在 production 下无效**
   - `APP_ENV=production`
   - `ALLOW_INSECURE_DEFAULT_AUTH=1`
   - 默认弱 secret / 弱密码
   - 仍必须抛错

8. **生产环境正确配置可启动**
   - `APP_ENV=production`
   - 强 `JWT_SECRET`
   - 强 `DEFAULT_PASSWORD`
   - 无存量弱密码账户
   - 不抛错

9. **预发环境同生产**
   - `APP_ENV=staging`
   - 使用生产级规则

---

### 手工验证矩阵

| 场景 | 环境变量 | 期望 |
|---|---|---|
| 本地开发 | `APP_ENV=development`, `ALLOW_INSECURE_DEFAULT_AUTH=1` | 可启动 |
| 未设 APP_ENV | 默认弱 secret / 弱密码 | 启动失败 |
| 生产但默认密钥 | `APP_ENV=production`, 默认 `JWT_SECRET` | 启动失败 |
| 生产但短密钥 | `APP_ENV=production`, 短 `JWT_SECRET` | 启动失败 |
| 生产但默认密码 | `APP_ENV=production`, `DEFAULT_PASSWORD=admin123` | 启动失败 |
| 生产且库存量弱密码账户 | 库里已有 `admin/admin123` | 启动失败 |
| 生产设 ALLOW=1 但配置弱 | `APP_ENV=production`, `ALLOW_INSECURE_DEFAULT_AUTH=1` | 启动失败 |
| 生产正确配置 | 强 secret + 强密码 + 无弱密码存量账户 | 可启动 |

---

## 明确不做（Explicitly NOT doing）

- 不做密钥轮换（key rotation）
- 不做多密钥签名/验证
- 不集成 Vault / KMS / Secrets Manager
- 不做 OAuth / SSO
- 不做密码复杂度策略系统（仅做启动期最低安全闸）
- 不改动登录接口协议
- 不引入新的认证框架

---

## 本轮风险边界与已知遗留

### 本轮明确纳入 scope
- 启动期校验 `JWT_SECRET`
- 启动期校验 `DEFAULT_PASSWORD`
- 启动期校验**数据库中已存在的默认弱密码账户**

### 本轮不做但显式记录
- 不做“所有历史用户弱密码扫描”
- 不做“强制首次登录改密”
- 不做“密码过期策略”
- 不做“登录失败锁定/限速”

> 这些属于后续安全加固，不在本 plan 范围。但它们不应掩盖一个事实：**默认弱密码存量账户**是 B-1 的一条真实后门，因此本轮必须至少堵住默认用户名这一条。

---

## 可逆性评估

- **可逆性：高**
- 本改动主要是启动期配置校验，不改核心业务逻辑
- 若误判导致本地开发受阻，可通过 `ALLOW_INSECURE_DEFAULT_AUTH=1` 临时恢复
- 生产侧则应长期保持严格校验

---

## 执行顺序建议

1. 新增 `startup_guard.py`
2. 写测试
3. 接入 `main.py` lifespan
4. 更新 `.env.example` 与文档
5. 手工验证生产/开发两套矩阵

---

## 验收标准（Definition of Done）

- 默认密钥在生产/预发不能启动
- 默认管理员密码在生产/预发不能启动
- 本地开发不被破坏
- 所有新增测试通过
- 文档明确生产前置要求
