# Memory Ranking System — 实现计划

> 为 ReportAgent 设计分层 Memory 机制，替代原有的 Mem0 直连 + 简单 LIKE 搜索方案。
> 核心思路：将 LFU/LRU 作为 Memory Ranking 的特征因子，而非固定淘汰算法。
> 最终排序以语义相似度为主，频率和新鲜度为辅。

---

## 一、架构概览

```
parent_graph.py
      │
      ▼
MemoryManager              ← 新建，统一入口
      │
      ├── UserMemory       ← 新建，用户偏好记忆
      │     ├── pgvector 语义搜索
      │     ├── memory_type 区分长期/临时偏好
      │     └── 语义相似度 × 0.6 + 重要性 × 0.2 + 频率 × 0.1 + 新鲜度 × 0.1
      │
      └── QueryMemory      ← 改造，SQL 模板记忆
            ├── pgvector 语义搜索
            ├── 成功率 = success_count / (success_count + failure_count)
            └── 语义相似度 × 0.5 + 成功率 × 0.3 + 频率 × 0.1 + 新鲜度 × 0.1
```

---

## 二、数据库变更 (`scripts/init_pg.sql`)

### 2.1 `memory.semantic_entry` — 加字段

```sql
ALTER TABLE memory.semantic_entry ADD COLUMN IF NOT EXISTS memory_type VARCHAR(32) DEFAULT 'insight';
ALTER TABLE memory.semantic_entry ADD COLUMN IF NOT EXISTS importance_score REAL DEFAULT 0.0;
ALTER TABLE memory.semantic_entry ADD COLUMN IF NOT EXISTS access_count INT DEFAULT 0;
ALTER TABLE memory.semantic_entry ADD COLUMN IF NOT EXISTS last_access_time TIMESTAMP DEFAULT NOW();
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `memory_type` | `VARCHAR(32)` | `stable_preference` / `temporary_preference` / `insight` |
| `importance_score` | `REAL` | 0-1，MemoryPolicy 提取时赋值 |
| `access_count` | `INT` | 访问次数，用于 LFU |
| `last_access_time` | `TIMESTAMP` | 最后访问时间，用于 LRU |

### 2.2 `memory.query_template` — 加字段

```sql
ALTER TABLE memory.query_template ADD COLUMN IF NOT EXISTS access_count INT DEFAULT 0;
ALTER TABLE memory.query_template ADD COLUMN IF NOT EXISTS failure_count INT DEFAULT 0;
ALTER TABLE memory.query_template ADD COLUMN IF NOT EXISTS verified BOOLEAN DEFAULT FALSE;
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `access_count` | `INT` | 检索命中次数 |
| `failure_count` | `INT` | 执行失败次数 |
| `verified` | `BOOLEAN` | 是否人工/自动验证通过 |

---

## 三、新建文件 `infra/memory/user_memory.py`

### 3.1 类定义

```
class UserMemory:
    def __init__(self, top_k: int = 5)
```

### 3.2 方法

#### `save(user_id, content, memory_type, importance_score, source) → int`

- 去重：相同 `user_id + content` → 更新 `access_count + 1`、`last_access_time`
- 否则 INSERT

#### `search(user_id, query, top_k) → list[RankedMemory]`

```
1. 调用 EmbeddingService.embed_or_none(query) 获取向量
2. 如果向量成功：
   a. SQL: pgvector cosine 搜索，取 top_k × 3
   b. 对每条结果计算语义相似度（cosine distance 转 similarity）
3. 如果向量失败：
   a. 降级关键词 LIKE ANY 搜索，取 top_k × 3
   b. 语义相似度=0
4. 对每条记录计算综合得分：
   score = semantic_similarity × 0.6
         + importance_score × 0.2
         + log(1+access_count) × 0.1
         + recency_score × 0.1
5. 按 score 降序，取 top_k
6. 对返回的每条记录调用 record_access()（异步后置，不阻塞返回）
```

#### `record_access(entry_id) → None`

```sql
UPDATE memory.semantic_entry
SET access_count = access_count + 1, last_access_time = NOW()
WHERE id = $1
```

#### `get_user_preferences(user_id, top_k) → list[RankedMemory]`

- 查询 `memory_type='stable_preference' OR memory_type='temporary_preference'`
- 按同公式排序

### 3.3 辅助函数

```
def _semantic_similarity(embedding, row_embedding) → float
    # cosine similarity from pgvector <=> distance
    return 1.0 - distance

def _recency_score(last_access_time) → float
    if last_access_time is None: return 0.0
    hours_since = (now - last_access_time).total_seconds() / 3600
    return 2 ^ (-hours_since / 72)  # 半衰期 72 小时

def _rank(memories, query_embedding) → list[RankedMemory]
    for each memory:
        semantic_sim = _semantic_similarity(query_embedding, memory.embedding)
        freq = log(1 + memory.access_count)
        recency = _recency_score(memory.last_access_time)
        score = semantic_sim × 0.6 + memory.importance × 0.2 + freq × 0.1 + recency × 0.1
    sort by score desc
```

---

## 四、改造 `infra/memory/query_memory.py`

### 4.1 现有方法改动

#### `save_query(question, sql, schema, target_metric) → int`

- 写入时 `access_count = 1`
- 新增 `failure_count = 0`

#### `search_similar(question, top_k) → list[RankedQuery]`

```
1. 调用 EmbeddingService.embed_or_none(question) 获取向量
2. 如果向量成功：
   a. pgvector 搜索，取 top_k × 3
3. 如果向量失败：
   a. 关键词 LIKE ANY 搜索，取 top_k × 3
   b. 语义相似度 = 0
4. 对每条记录计算综合得分：
   success_rate = success_count / max(success_count + failure_count, 1)
   score = semantic_similarity × 0.5
         + success_rate × 0.3
         + log(1+access_count) × 0.1
         + recency_score × 0.1
5. 按 score 降序，取 top_k
6. 后置 record_access()
```

#### 新增 `record_access(entry_id) → None`

```sql
UPDATE memory.query_template
SET access_count = access_count + 1, last_used_at = NOW()
WHERE id = $1
```

#### 新增 `record_failure(entry_id) → None`

```sql
UPDATE memory.query_template
SET failure_count = failure_count + 1
WHERE id = $1
```

### 4.2 保留方法

- `search_semantic()` — 保留，内部委托给 `UserMemory`
- `save_semantic()` — 保留，内部委托给 `UserMemory`

---

## 五、新建 `infra/memory/memory_manager.py`

### 5.1 类定义

```
class MemoryManager:
    def __init__(self):
        self._query_memory = QueryMemory()
        self._user_memory = UserMemory()
```

### 5.2 方法

#### `recall(query, user_id, top_k_queries=2, top_k_preferences=3) → str`

```
1. 调用 self._query_memory.search_similar(query, top_k_queries)
   → 格式化为 "[历史查询] {question} → {sql[:60]} (可靠度{score})"
2. 调用 self._user_memory.search(user_id, query, top_k_preferences)
   → 格式化为 "[{memory_type}] {content} (相关度{score})"
3. 合并为字符串，\n 分隔
4. 空时返回 ""
```

#### `remember_query(question, sql, schema, target_metric) → int`

- 委托 `QueryMemory.save_query()`

#### `remember_preference(user_id, content, memory_type, importance, source) → int`

- 委托 `UserMemory.save()`

#### `record_query_failure(query_id) → None`

- 委托 `QueryMemory.record_failure()`

---

## 六、改造 `infra/memory/policy.py`

### 6.1 改动 `extract_preference()`

返回新增字段：

```python
class MemoryEntry(BaseModel):
    type: MemoryType
    key: str
    value: str
    user_id: str = ""
    metadata: dict = {}
    created_at: str = ""
    importance_score: float = 0.5       # 新增，默认中等
    memory_type: str = "insight"        # 新增
```

### 6.2 提取时赋值规则

| 触发条件 | `memory_type` | `importance_score` |
|----------|--------------|-------------------|
| 含"以后"/"默认"/"每次" | `stable_preference` | `0.8` |
| 含"这个月"/"这个季度"/"最近" | `temporary_preference` | `0.5` |
| 普通洞察/分析结果 | `insight` | `0.3` |

---

## 七、改造 `agent/parent_graph.py`

### 7.1 替换 import

```python
# 原
from app.infra.memory.query_memory import QueryMemory

# 改为
from app.infra.memory.memory_manager import MemoryManager
```

### 7.2 替换 `_classify_intent` 中的记忆召回

```python
# 原
qm = QueryMemory()
similar = await qm.search_similar(q, top_k=2)
semantic = await qm.search_semantic(session_id, q, top_k=3)
# ... 手动拼接 memory_lines

# 改为
mm = MemoryManager()
memory_context = await mm.recall(
    query=q,
    user_id=session_id,
    top_k_queries=2,
    top_k_preferences=3,
)
```

### 7.3 替换 `_run_sql_agent` 中的记忆保存

```python
# 原
qm = QueryMemory()
await qm.save_query(...)

# 改为
mm = MemoryManager()
await mm.remember_query(...)
```

### 7.4 替换 `_run_report_agent` 中的语义记忆保存

```python
# 原
qm = QueryMemory()
await qm.save_semantic(...)

# 改为
mm = MemoryManager()
await mm.remember_preference(
    user_id=...,
    content=insight,
    source="report_agent",
    memory_type="insight",
    importance=0.3,
)
```

---

## 八、文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| MODIFY | `scripts/init_pg.sql` | 加 5 个字段 |
| CREATE | `infra/memory/user_memory.py` | UserMemory 类 |
| MODIFY | `infra/memory/query_memory.py` | 加 LFU/LRU + 语义排序 |
| CREATE | `infra/memory/memory_manager.py` | MemoryManager 类 |
| MODIFY | `infra/memory/policy.py` | 加 `importance_score` + `memory_type` |
| MODIFY | `agent/parent_graph.py` | 替换为 MemoryManager |

---

## 九、关键设计决策

### 9.1 语义相似度为什么权重大？

因为无相关性的高频记忆是噪声。用户问"库存分析"，"销售喜欢柱状图"这个偏好就算访问了 1000 次，语义相似度接近 0，综合得分自然低。这是和纯 LFU/LRU 淘汰算法的本质区别。

### 9.2 为什么 `log(1+access_count)` 而不是 `^0.3`？

- `log(1+1) = 0.69`，`log(1+100) = 4.61` → 差距约 6.7 倍
- 物理意义清晰：每多一个数量级，分数线性增长
- 面试解释：log-scaling 防止高频记忆永久霸占

### 9.3 `record_access()` 为什么后置异步？

不阻塞主流程。语义搜索先返回结果，再后台更新访问计数。即使失败也不影响用户体验。

### 9.4 为什么 UserMemory 和 QueryMemory 用不同公式？

| | UserMemory | QueryMemory |
|--|-----------|-------------|
| 核心目标 | 找到用户稳定偏好 | 找到可复用的 SQL |
| 第一因子 | 语义相似度 0.6 | 语义相似度 0.5 |
| 第二因子 | 重要性 0.2 | 成功率 0.3 |
| 频率权重 | 0.1 | 0.1 |
| 新鲜度权重 | 0.1 | 0.1 |

QueryMemory 成功率权重更高，因为 SQL 模板的可靠性（能否正确执行）比用户偏好更重要。

---

## 十、执行顺序

```
Step 1: init_pg.sql — 加字段
Step 2: infra/memory/user_memory.py — 新建
Step 3: infra/memory/query_memory.py — 改造
Step 4: infra/memory/policy.py — 改造
Step 5: infra/memory/memory_manager.py — 新建
Step 6: agent/parent_graph.py — 替换
```

后三步依赖前两步，但前三步可以并行。