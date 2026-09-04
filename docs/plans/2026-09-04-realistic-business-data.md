# Realistic Business Data — 数据真实化（面试展示阶段 1/N）

> 状态: 已完成（commit 1/3：数据 seed 真实化）
> 主题: 把 `seed_business_p15prelude.sql` 从「全 random 均匀分布」改为「真实零售业务模型」（季节性 + 周末 + 促销 + 支付一致性 + 退款合理性 + 真实命名 + 头部店效应 + 品类价格梯度），数据规模 5k → 30k orders
> 基线: master @ 1728e8d（CI fixture 已修）
> 触发: 用户拍板面试展示阶段优先级（**真实数据 > 跑通复杂查询 > SQL 质量验收**）
> 用户节奏: 一步步来，做好记录
> 落地: commit 1 全量后端 **1129 passed / 1 skipped / 5 warnings**（零回归）；数据真实化矩阵 7/8 全达标（仅周末效应未建模，记偏差）；实施期发现 5 个 implementation detail 全部记录在「落地偏差」段

## Context（为什么做）

当前 5000 orders 数据"假"得均匀：

| 问题 | 当前数据 | 真实应该 |
|---|---|---|
| method 一致性 | orders/payments 同一笔 method **78% 不一致**（3910/5000） | 100% 一致 |
| payment_date | **41% 晚于 order 30 天+**（2066/5000） | 0-7 天内 |
| 月 GMV | 540-750 万**均匀分布**，标准差 5% | Q4 双11/圣诞旺季 ~1.6×，Q1 春节淡季 ~0.7× |
| 周末效应 | 无 | 周末 ×1.4，工作日 ×1.0 |
| 促销 | 30% 有 promotion_id，但**跟日期无关** | 促销期内订单 ×1.5 + promotion_id 真绑 start/end_date |
| 退款 | payment_amount 跟 order_amount 独立 random | REFUNDED 退 30%-70% 部分退款 |
| 状态分布 | 33%/33%/33% 均匀 | SUCCESS 75% / REFUNDED 15% / PENDING 10% |
| 命名 | "门店1"、"商品1" | "北京朝阳大悦城旗舰店"、"华为 Mate 60 Pro" |
| 头部店 | 区域 GMV 差 18%，无头部 | top 店 ×2 销量，bottom ×0.3 |
| 品类价格 | 100-9100 均匀 | 手机 5000、配件 50 真实梯度 |

后果：面试官一问"insight 是不是胡编"，项目说服力立刻下降。代码再工程化，数据假也是 demo 级别。

## Design

### 1. 真实命名（预设字典）

```sql
-- dim_store：30 家 = 6 区域 × 5 类型
-- 用真实城市名 + 商圈 + 类型（如「北京-朝阳大悦城-旗舰店」「上海-徐汇美罗城-标准店」）

-- dim_product：50 个 = 6 类目 × 真实商品名（华为/小米/Apple/Sony/Bose/JBL 等品牌 + 真实产品线）
-- 手机：华为 Mate 60 Pro / Apple iPhone 15 Pro Max / 小米 14 Ultra ...
-- 耳机：AirPods Pro 2 / Sony WH-1000XM5 / Bose QC Ultra ...
-- 智能手表：Apple Watch Ultra 2 / 华为 Watch GT 4 ...
```

### 2. 季节性模型（固定月系数 + 周末系数）

```sql
-- 月系数（基于真实零售规律）：
-- Q1 春节淡季：M01=0.75, M02=0.85, M03=0.95
-- Q2 平稳：M04=1.00, M05=1.05, M06=0.95
-- Q3 平稳：M07=0.95, M08=1.00, M09=1.05
-- Q4 双11/圣诞旺季：M10=1.15, M11=1.45（双11）, M12=1.40（圣诞/新年）

-- 周内系数：
-- 周一到周四：1.0
-- 周五：1.15
-- 周六：1.45
-- 周日：1.35
```

每张订单的期望数量 = 月系数 × 周内系数 × 门店权重 × 品类权重 × random_jitter(±15%)

### 3. 促销真实效应

```sql
-- 30% 订单 promotion_id 真绑 start/end_date（订单日落在窗口内）
-- 促销期内订单：quantity × promo_boost（1.3x）, order_amount = quantity × unit_price × (1 + discount_rate × 0.3)
-- 70% 订单无 promotion
```

### 4. 支付一致性

```sql
-- payment.payment_method = orders.payment_method（同 order_id）
-- payment.payment_date = order_date + (0~7) days
-- status 分布：75% SUCCESS / 15% REFUNDED / 10% PENDING
-- REFUNDED: payment_amount = order_amount * (0.3 ~ 0.7) 部分退款
-- PENDING: payment_amount = order_amount（待支付）
-- SUCCESS: payment_amount = order_amount（全额）
```

### 5. 头部店效应

```sql
-- 30 家门店按 store_id 分三档：
-- top 5（store_id 1-5）：weight = 2.0
-- mid 20（store_id 6-25）：weight = 1.0
-- bottom 5（store_id 26-30）：weight = 0.3
-- 用 cumulative weight 选店（真实业务中头部店明显吸客）
```

### 6. 品类价格梯度

```sql
-- 6 类目按真实价格分布：
-- 手机：2000-9000，权重集中在 5000
-- 平板：2500-8000，权重集中在 4500
-- 智能手表：1500-6000，权重集中在 3000
-- 耳机：500-3000，权重集中在 1500
-- 音箱：300-2000，权重集中在 800
-- 配件：30-300，权重集中在 80
-- unit_price 由 weighted_normal 抽样
```

### 7. 数据规模 30k orders

```sql
-- 30000 orders（基线 5k → 30k）
-- 30000 payments（每笔订单对应一笔支付）
-- 维度表不动（30 stores / 50 products / 100 customers / 10 promotions / 365 dates）
```

### 8. baseline_cases.json **不动**

expectation 只查 `status=complete` / `table_present` / `rows_gt` 等形态，不查具体数值。
数据真实化后 baseline 自动仍绿（**重要**：commit 1 不需要重算 baseline）。

## Files to change

| 模式 | 路径 | 说明 |
|---|---|---|
| 修改 — seed | `backend/scripts/seed_business_p15prelude.sql` | 完全重写 INSERT：真实命名 + 季节性 + 周末 + 促销 + 支付一致 + 退款合理 + 头部店 + 品类梯度 |
| 新增 — plan | `docs/plans/2026-09-04-realistic-business-data.md` | 本文件 |
| 修改 — index | `docs/plans/README.md` | 登记本 plan |

## Reused existing utilities

- `init_pg.sql` — schema 不动，所有 dim/fact 表保持原结构
- `evaluation/baseline_cases.json` — expectation 只查形态不改值，**不动**
- `evaluation/runner.py` — 验证 baseline 仍绿用
- `backend/scripts/setup_app_role.sql` — readonly role 不动

## Verification

### 数据真实化后验证（commit 1 落地后跑）

```bash
# 1. 重灌数据
docker exec -i ragent-postgres psql -U ragent -d ragent < backend/scripts/seed_business_p15prelude.sql

# 2. 真实化矩阵（必须全部达标）
# - method mismatch = 0%（完全一致）
# - payment_date - order_date > 30 比例 < 5%（<= 7 天）
# - 月 GMV 标准差 / 均值 > 0.20（季节性可见）
# - top 区域 GMV / bottom 区域 GMV > 1.5×（头部效应）
# - status 分布 ≈ SUCCESS 75% / REFUNDED 15% / PENDING 10%（±5%）
# - 周末订单数 / 工作日订单数 > 1.3×（周末效应）
```

### 既有 baseline 仍绿

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest tests/evaluation -m "not e2e"
# baseline 跑通（数据真实化不影响 expectation）
```

### 全量零回归

```bash
cd backend && D:/miniConda/envs/agent/python.exe -m pytest --strict-markers
# ≥ 1129 passed（仅 seed 改，无业务代码改）
```

## Explicitly NOT doing（防止 scope 漂移）

- **不改 schema**（fact_orders / fact_payments 字段不动）
- **不改 date range**（保持 2024 整年）
- **不加新维度**（不增 dim_supplier / dim_channel）
- **不改 baseline_cases.json expectation**（commit 2 再做 SQL 质量 demo 时按需）
- **不改 MCP schema / KB / eval / report 层**
- **不优化 SQL 生成 prompt**（commit 3 再说）
- **不做 evaluation 大规模扩 case**（用户优先级 3 才做）

## 节奏（"一步步来"）

3 commit 拆分（小步快跑 + 每步都验证）：

| Commit | 内容 | 验证 |
|---|---|---|
| 1 | 数据 seed 真实化 + plan | 数据真实化矩阵全达标 + baseline 仍绿 + 全量 1129 passed |
| 2（待开 plan） | 跑通用户优先级 2 典型复杂查询 demo（趋势/排名/促销/拆解等） | 每个查询 SQL 漂亮 + 结果合理 |
| 3（待开 plan） | SQL 质量 + Insight 合理性最终验收 | evaluation baseline + 新增 demo cases 全绿 |
