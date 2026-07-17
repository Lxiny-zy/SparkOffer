# MySQL 事务与锁

---

## 一、事务 ACID 详解

### 1.1 四大特性

| 特性 | 含义 | 实现机制 |
|------|------|---------|
| **原子性（Atomicity）** | 事务中的操作要么全部成功，要么全部回滚 | undo log |
| **一致性（Consistency）** | 事务前后数据库从一个一致状态变为另一个一致状态 | 是最终目标，由 AID 共同保证 |
| **隔离性（Isolation）** | 并发事务之间互不影响 | 锁 + MVCC |
| **持久性（Durability）** | 事务提交后数据永久保存，即使系统崩溃也不丢失 | redo log |

### 1.2 一致性的本质

一致性是事务的最终目标，其他三个特性是手段：
- 原子性保证操作不会执行一半
- 隔离性保证并发不会互相干扰
- 持久性保证提交的数据不丢失
- 三者共同保证数据库从一个合法状态转变为另一个合法状态

---

## 二、隔离级别详解

### 2.1 四种隔离级别

| 隔离级别 | 脏读 | 不可重复读 | 幻读 | 实现方式 | 性能 |
|---------|------|-----------|------|---------|------|
| **READ UNCOMMITTED** | 有 | 有 | 有 | 无特殊处理 | 最高 |
| **READ COMMITTED（RC）** | 无 | 有 | 有 | MVCC（每次 SELECT 新建 ReadView） | 高 |
| **REPEATABLE READ（RR）** | 无 | 无 | 有* | MVCC + Next-Key Lock | 中 |
| **SERIALIZABLE** | 无 | 无 | 无 | 所有读加共享锁，读写串行 | 最低 |

> MySQL InnoDB 默认隔离级别是 **REPEATABLE READ**。
> RR 级别下，MVCC 解决了**快照读**的幻读，Next-Key Lock 解决了**当前读**的幻读。

### 2.2 并发问题详解

#### 脏读（Dirty Read）

```
事务A                         事务B
BEGIN;
UPDATE user SET age=25 WHERE id=1;
                              BEGIN;
                              SELECT age FROM user WHERE id=1;
                              -- 读到 age=25（事务A未提交的数据）
ROLLBACK;
                              -- 事务B读到的 age=25 是脏数据！
```

#### 不可重复读（Non-Repeatable Read）

```
事务A                         事务B
BEGIN;
SELECT age FROM user WHERE id=1;
-- 读到 age=20
                              BEGIN;
                              UPDATE user SET age=25 WHERE id=1;
                              COMMIT;
SELECT age FROM user WHERE id=1;
-- 读到 age=25（同一行数据被修改了）
-- 两次读取同一行结果不同！
```

#### 幻读（Phantom Read）

```
事务A                         事务B
BEGIN;
SELECT * FROM user WHERE age > 20;
-- 返回 3 行
                              BEGIN;
                              INSERT INTO user (name, age) VALUES ('新用户', 25);
                              COMMIT;
SELECT * FROM user WHERE age > 20;
-- 返回 4 行（多出一行新数据）
-- 结果集行数变了！
```

> **不可重复读** vs **幻读**：不可重复读针对**同一行数据被修改**；幻读针对**结果集行数变化**（INSERT/DELETE）。

### 2.3 快照读 vs 当前读

| 类型 | 定义 | 示例 |
|------|------|------|
| **快照读** | 读取 MVCC 快照版本，不加锁 | 普通 `SELECT` |
| **当前读** | 读取最新版本数据，加锁 | `SELECT ... FOR UPDATE`、`SELECT ... LOCK IN SHARE MODE`、`INSERT`、`UPDATE`、`DELETE` |

---

## 三、MVCC 原理深入

### 3.1 核心组件

MVCC（Multi-Version Concurrency Control）通过以下机制实现：

#### 隐藏字段

InnoDB 为每行数据自动添加三个隐藏列：

| 隐藏字段 | 大小 | 含义 |
|---------|------|------|
| `DB_TRX_ID` | 6 字节 | 最近一次修改该行的事务 ID |
| `DB_ROLL_PTR` | 7 字节 | 回滚指针，指向 undo log 中该行的上一个版本 |
| `DB_ROW_ID` | 6 字节 | 隐藏自增 ID（无主键时作为聚簇索引） |

#### Undo Log 版本链

```
当前数据：
┌────────────────────────────────────────────┐
│ name=王五 │ DB_TRX_ID=103 │ DB_ROLL_PTR ──────┐
└────────────────────────────────────────────┘   │
                                                  ▼
                                     ┌─────────────────────┐
                                     │ name=李四 │ TRX_ID=102 │ ROLL_PTR ──────┐
                                     └─────────────────────┘                   │
                                                                                ▼
                                                                   ┌─────────────────────┐
                                                                   │ name=张三 │ TRX_ID=101 │ ROLL_PTR=NULL │
                                                                   └─────────────────────┘
```

- 每次 UPDATE 会将旧版本写入 undo log
- 通过 `DB_ROLL_PTR` 串成一条版本链
- 事务通过版本链找到自己可见的数据版本

### 3.2 ReadView（读视图）

ReadView 是事务执行快照读时生成的一致性视图，包含四个核心字段：

| 字段 | 含义 |
|------|------|
| `m_ids` | 生成 ReadView 时，当前系统中所有**活跃事务**的 ID 列表 |
| `min_trx_id` | `m_ids` 中的最小值 |
| `max_trx_id` | 系统应该分配给**下一个事务**的 ID（不是 m_ids 的最大值） |
| `creator_trx_id` | 创建此 ReadView 的事务 ID |

### 3.3 可见性判断规则

对于版本链中的某个版本，其 `DB_TRX_ID` 记为 `trx_id`：

```
if (trx_id == creator_trx_id)
    // 当前事务自己修改的 --> 可见

else if (trx_id < min_trx_id)
    // 在 ReadView 创建前已提交 --> 可见

else if (trx_id >= max_trx_id)
    // 在 ReadView 创建后才开始 --> 不可见

else if (trx_id in m_ids)
    // 在 ReadView 创建时还未提交（活跃事务） --> 不可见

else
    // 在 ReadView 创建前已提交 --> 可见
```

**流程**：从版本链最新版本开始判断，不可见则顺着 ROLL_PTR 往上找，直到找到可见版本。

### 3.4 RC vs RR 的 ReadView 差异

#### READ COMMITTED（RC）

```
事务A (trx_id=100)                   事务B (trx_id=101)
BEGIN;
                                      BEGIN;
                                      UPDATE user SET name='李四' WHERE id=1;
SELECT name FROM user WHERE id=1;
-- 生成 ReadView: m_ids=[100,101]
-- trx_id=101 在 m_ids 中 --> 不可见
-- 沿版本链找到上一版本 --> 读到 '张三'
                                      COMMIT;
SELECT name FROM user WHERE id=1;
-- 重新生成 ReadView: m_ids=[100]
-- trx_id=101 不在 m_ids 中 --> 可见
-- 读到 '李四'（不可重复读！）
```

**RC 每次 SELECT 都生成新的 ReadView**，所以能读到其他事务已提交的修改。

#### REPEATABLE READ（RR）

```
事务A (trx_id=100)                   事务B (trx_id=101)
BEGIN;
SELECT name FROM user WHERE id=1;
-- 首次 SELECT 生成 ReadView: m_ids=[100,101]
-- 整个事务只用这一个 ReadView
                                      BEGIN;
                                      UPDATE user SET name='李四' WHERE id=1;
                                      COMMIT;
SELECT name FROM user WHERE id=1;
-- 复用首次的 ReadView: m_ids=[100,101]
-- trx_id=101 在 m_ids 中 --> 不可见
-- 读到 '张三'（可重复读！）
```

**RR 只在第一次 SELECT 时生成 ReadView**，后续复用同一个，所以实现了可重复读。

### 3.5 MVCC 解决幻读的局限

MVCC 只能解决**快照读**的幻读问题。对于**当前读**（SELECT FOR UPDATE、INSERT、UPDATE、DELETE），需要 Next-Key Lock 来防止幻读。

```
事务A                                事务B
BEGIN;
SELECT * FROM user WHERE age > 20 FOR UPDATE;
-- 当前读，加 Next-Key Lock 锁住 age>20 的区间
                                     BEGIN;
                                     INSERT INTO user (age) VALUES (25);
                                     -- 阻塞！被 Gap Lock 拦住
COMMIT;
                                     -- 事务A提交后，事务B才能插入
```

---

## 四、锁机制详解

### 4.1 全局锁

```sql
-- 加全局读锁（整个数据库只读）
FLUSH TABLES WITH READ LOCK;

-- 释放
UNLOCK TABLES;
```

- 用途：全库逻辑备份（如 mysqldump）
- 问题：阻塞所有写操作和 DDL
- 替代方案：`mysqldump --single-transaction`（利用 MVCC 一致性快照，不加全局锁）

### 4.2 表级锁

#### 表锁

```sql
-- 加读锁
LOCK TABLES user READ;
-- 加写锁
LOCK TABLES user WRITE;
-- 释放
UNLOCK TABLES;
```

#### 元数据锁（MDL, Metadata Lock）

- MySQL 5.5 引入，自动加锁，不需要显式使用
- **增删改查（DML）操作**加 MDL 读锁
- **表结构变更（DDL）操作**加 MDL 写锁
- 读锁之间不互斥，读写锁互斥
- **经典问题**：长事务持有 MDL 读锁 --> DDL 申请 MDL 写锁被阻塞 --> 后续所有 DML 也被阻塞（排队）

#### 意向锁（Intention Lock）

- **意向共享锁（IS）**：事务准备给某行加共享锁前，先给表加 IS 锁
- **意向排他锁（IX）**：事务准备给某行加排他锁前，先给表加 IX 锁
- 意向锁之间不冲突
- 作用：快速判断表中是否有行锁，避免逐行检查

| | IS | IX | S | X |
|---|---|---|---|---|
| **IS** | 兼容 | 兼容 | 兼容 | 不兼容 |
| **IX** | 兼容 | 兼容 | 不兼容 | 不兼容 |
| **S** | 兼容 | 不兼容 | 兼容 | 不兼容 |
| **X** | 不兼容 | 不兼容 | 不兼容 | 不兼容 |

#### AUTO-INC 锁

- 用于自增列（AUTO_INCREMENT）
- MySQL 5.1.22+ 引入 `innodb_autoinc_lock_mode` 参数
  - `0`（传统模式）：AUTO-INC 表级锁，语句结束释放
  - `1`（连续模式，默认）：简单 INSERT 用轻量级互斥锁，批量 INSERT 用 AUTO-INC 锁
  - `2`（交叉模式）：所有 INSERT 都用轻量级互斥锁（并发最好，但自增值可能不连续）

### 4.3 行级锁（InnoDB）

> InnoDB 的行锁是**加在索引上**的，不是加在数据行上。如果查询没有命中索引，会退化为表锁。

#### Record Lock（记录锁）

```sql
-- 锁定 id=1 这一行
SELECT * FROM user WHERE id = 1 FOR UPDATE;
-- 对 id=1 的索引记录加 X 型 Record Lock
```

- 锁住索引记录本身
- 只锁定精确匹配的行

#### Gap Lock（间隙锁）

```sql
-- 假设表中有 id: 1, 5, 10, 15
-- 间隙：(-∞,1), (1,5), (5,10), (10,15), (15,+∞)

SELECT * FROM user WHERE id = 7 FOR UPDATE;
-- id=7 不存在，对间隙 (5,10) 加 Gap Lock
-- 阻止其他事务在 (5,10) 之间插入数据
```

- 锁住索引记录之间的间隙
- 只在 RR 隔离级别下存在
- 目的：防止幻读（阻止其他事务在间隙中插入新记录）
- Gap Lock 之间不冲突（两个事务可以同时对同一间隙加 Gap Lock）
- Gap Lock 只阻止 INSERT 操作

#### Next-Key Lock（临键锁）

```
-- 假设表中有 id: 1, 5, 10, 15
-- Next-Key Lock 锁定的范围是左开右闭区间

SELECT * FROM user WHERE id BETWEEN 5 AND 10 FOR UPDATE;
-- 加锁范围：(1,5], (5,10], (10,15]
-- 即 Record Lock(5) + Gap Lock(1,5)
--  + Record Lock(10) + Gap Lock(5,10)
--  + Gap Lock(10,15)
```

- Next-Key Lock = Record Lock + Gap Lock
- InnoDB 在 RR 级别下的**默认行锁类型**
- 锁定范围：左开右闭区间 `(a, b]`
- 作用：锁住记录本身 + 前面的间隙，防止幻读

#### 插入意向锁（Insert Intention Lock）

- 一种特殊的 Gap Lock
- INSERT 操作在等待 Gap Lock 释放时使用
- 不同事务在同一间隙中插入不同位置的记录不会互相阻塞

### 4.4 加锁规则总结（RR 级别）

以下是 InnoDB 在 RR 级别下的加锁规则（基于 MySQL 5.7/8.0）：

1. **加锁的基本单位是 Next-Key Lock**
2. 查找过程中访问到的对象才加锁
3. **等值查询**：
   - 唯一索引：Next-Key Lock 退化为 **Record Lock**（只锁记录）
   - 普通索引：向右遍历到不满足条件的记录时，Next-Key Lock 退化为 **Gap Lock**
4. **范围查询**：
   - 唯一索引：到不满足条件的第一个值为止
   - 普通索引：到不满足条件的第一个值为止，包含 Gap Lock

#### 加锁案例分析

```sql
-- 表 user: id(主键) = 1, 5, 10, 15, 20
-- name 有普通索引

-- 案例1: 主键等值查询（记录存在）
SELECT * FROM user WHERE id = 10 FOR UPDATE;
-- 加锁：Record Lock(id=10)  （Next-Key Lock 退化）

-- 案例2: 主键等值查询（记录不存在）
SELECT * FROM user WHERE id = 8 FOR UPDATE;
-- 加锁：Gap Lock(5, 10)  （Next-Key Lock 退化）

-- 案例3: 主键范围查询
SELECT * FROM user WHERE id >= 10 AND id < 15 FOR UPDATE;
-- 加锁：Next-Key Lock(5,10] + Next-Key Lock(10,15] + Gap Lock(15,20)

-- 案例4: 普通索引等值查询
SELECT * FROM user WHERE name = '张三' FOR UPDATE;
-- 先在 name 索引加 Next-Key Lock + Gap Lock
-- 再在主键索引加 Record Lock（回表）
```

### 4.5 锁的兼容矩阵（行锁）

| 请求\持有 | Gap | Insert Intention | Record | Next-Key |
|----------|-----|-----------------|--------|----------|
| **Gap** | 兼容 | 兼容 | 兼容 | 兼容 |
| **Insert Intention** | 冲突 | 兼容 | 兼容 | 冲突 |
| **Record S** | 兼容 | 兼容 | S兼容/X冲突 | S兼容/X冲突 |
| **Record X** | 兼容 | 兼容 | 冲突 | 冲突 |
| **Next-Key S** | 兼容 | 兼容 | S兼容/X冲突 | S兼容/X冲突 |
| **Next-Key X** | 兼容 | 兼容 | 冲突 | 冲突 |

---

## 五、死锁

### 5.1 死锁产生条件

死锁需要同时满足四个条件（操作系统理论同理）：
1. **互斥条件**：资源（锁）只能被一个事务持有
2. **持有并等待**：事务持有锁的同时等待获取其他锁
3. **不可抢占**：锁不能被强制释放
4. **循环等待**：事务之间形成环形等待链

### 5.2 死锁示例

```
事务A                              事务B
BEGIN;                             BEGIN;
UPDATE user SET age=25 WHERE id=1; UPDATE user SET age=30 WHERE id=2;
-- 持有 id=1 的 X Lock             -- 持有 id=2 的 X Lock

UPDATE user SET age=26 WHERE id=2; UPDATE user SET age=31 WHERE id=1;
-- 等待 id=2 的锁（事务B持有）      -- 等待 id=1 的锁（事务A持有）
-- 死锁！
```

### 5.3 InnoDB 死锁检测

```sql
-- 查看死锁信息
SHOW ENGINE INNODB STATUS;
-- 关注 LATEST DETECTED DEADLOCK 部分

-- 死锁检测参数
innodb_deadlock_detect = ON    -- 开启死锁检测（默认开启）
innodb_lock_wait_timeout = 50  -- 锁等待超时时间（秒）
```

InnoDB 死锁检测机制：
- **wait-for graph（等待图）**算法：构建事务等待关系图，检测是否有环
- 检测到死锁后，回滚**代价最小**（undo log 最少）的事务
- 死锁检测本身有性能开销，高并发时可能成为瓶颈

### 5.4 死锁预防策略

1. **按固定顺序访问表和行**：所有事务按 id 从小到大的顺序更新
2. **缩短事务时间**：减少事务中的操作，避免大事务
3. **降低隔离级别**：RC 级别没有 Gap Lock，减少死锁概率
4. **一次性锁定所需资源**：`SELECT ... FOR UPDATE` 在事务开始时锁定所有需要的行
5. **使用合理的索引**：避免全表扫描导致锁范围过大
6. **控制并发度**：在应用层限制并发更新同一行数据
7. **超时机制**：合理设置 `innodb_lock_wait_timeout`

### 5.5 Gap Lock 导致死锁的经典场景

```sql
-- 表中有 id: 1, 5, 10

-- 事务A
BEGIN;
SELECT * FROM user WHERE id = 3 FOR UPDATE;
-- id=3 不存在，加 Gap Lock(1, 5)

-- 事务B
BEGIN;
SELECT * FROM user WHERE id = 4 FOR UPDATE;
-- id=4 不存在，加 Gap Lock(1, 5) -- Gap Lock 互相兼容

-- 事务A
INSERT INTO user (id) VALUES (3);
-- 等待事务B的 Gap Lock 释放（Insert Intention Lock 与 Gap Lock 冲突）

-- 事务B
INSERT INTO user (id) VALUES (4);
-- 等待事务A的 Gap Lock 释放
-- 死锁！
```

---

## 六、binlog / redo log / undo log 三大日志

### 6.1 总览对比

| 特性 | redo log | undo log | binlog |
|------|----------|----------|--------|
| **层级** | InnoDB 引擎层 | InnoDB 引擎层 | MySQL Server 层 |
| **类型** | 物理日志 | 逻辑日志 | 逻辑日志 |
| **内容** | 数据页的修改 | 修改前的数据 | SQL 语句/行变更 |
| **作用** | 崩溃恢复（持久性） | 事务回滚（原子性）+ MVCC | 主从复制 + 数据恢复 |
| **写入时机** | 事务执行中不断写入 | 事务执行前写入 | 事务提交时写入 |
| **文件大小** | 固定大小，循环写 | 按需分配 | 不限大小，追加写 |

### 6.2 redo log 详解

#### WAL（Write-Ahead Logging）机制

```
                    ┌─────────────┐
                    │  Buffer Pool │  (内存)
                    │  (脏页)      │
                    └──────┬──────┘
                           │ ① 修改内存中的数据页
         ┌─────────────────┤
         │                 │
         ▼                 ▼
┌─────────────┐    ┌─────────────┐
│  redo log   │    │   磁盘数据    │
│  (磁盘)     │    │   (后台刷新)  │
└─────────────┘    └─────────────┘
   ② 先写日志         ③ 后写数据
```

- **先写日志，再写数据**：修改数据时不直接写磁盘，而是写 redo log
- redo log 是顺序 I/O（追加写），比随机 I/O 快得多
- 后台线程异步将 Buffer Pool 中的脏页刷到磁盘

#### redo log 结构

```
redo log 由多个固定大小的文件组成，循环写入：

┌──────────┬──────────┬──────────┬──────────┐
│ ib_logfile0│ ib_logfile1│ ib_logfile2│ ib_logfile3│
└──────────┴──────────┴──────────┴──────────┘
     ↑                                   ↑
  write pos                          checkpoint
  (当前写入位置)                     (已刷盘位置)

write pos 追赶 checkpoint：空间不足时，需要等待 checkpoint 前进
checkpoint 追赶 write pos：后台线程将脏页刷盘后推进
```

#### redo log 刷盘策略

```sql
-- innodb_flush_log_at_trx_commit 参数
-- 0: 每秒将 log buffer 写入 OS cache 并 fsync（可能丢失 1 秒数据）
-- 1: 每次事务提交都 fsync（最安全，默认值）
-- 2: 每次事务提交写入 OS cache，每秒 fsync（MySQL 崩溃不丢数据，OS 崩溃可能丢 1 秒）
```

| 值 | 写 OS Cache | fsync 到磁盘 | 安全性 | 性能 |
|---|---|---|---|---|
| 0 | 每秒 | 每秒 | 最低 | 最高 |
| 1 | 每次提交 | 每次提交 | 最高 | 最低 |
| 2 | 每次提交 | 每秒 | 中等 | 中等 |

### 6.3 undo log 详解

#### 作用

1. **事务回滚**：保存数据修改前的值，ROLLBACK 时用旧值恢复
2. **MVCC**：提供数据的历史版本，实现一致性快照读

#### undo log 类型

| 类型 | 操作 | 内容 |
|------|------|------|
| **insert undo log** | INSERT | 记录插入行的主键值，回滚时 DELETE |
| **update undo log** | UPDATE/DELETE | 记录修改前的旧值，回滚时恢复 |

#### undo log 生命周期

- insert undo log：事务提交后即可删除（其他事务不可能读到未提交的 INSERT）
- update undo log：不能立即删除，MVCC 可能需要旧版本。由 **purge 线程**在没有事务需要旧版本时清理

#### 大事务的 undo log 膨胀问题

```sql
-- 避免大事务！大事务会导致：
-- 1. undo log 占用大量空间
-- 2. 长事务持有旧的 ReadView，导致 purge 无法清理旧版本
-- 3. undo log 回滚段空间不足

-- 查看长事务
SELECT * FROM information_schema.innodb_trx
WHERE TIME_TO_SEC(TIMEDIFF(NOW(), trx_started)) > 60;
```

### 6.4 binlog 详解

#### 三种格式

| 格式 | 内容 | 优点 | 缺点 |
|------|------|------|------|
| **STATEMENT** | SQL 语句 | 日志量小 | 某些函数（NOW()、UUID()）主从不一致 |
| **ROW** | 行数据变更（前后镜像） | 精确，不会不一致 | 日志量大（推荐格式） |
| **MIXED** | 混合模式 | 兼顾 | 仍可能不一致 |

> MySQL 5.7.7+ 默认 ROW 格式。

#### binlog 写入机制

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  binlog cache │ -> │  OS Page Cache│ -> │  binlog 文件  │
│  (线程私有)    │    │  (操作系统)    │    │  (磁盘)       │
└──────────────┘    └──────────────┘    └──────────────┘
      write                fsync

sync_binlog 参数：
  0: 只 write，不 fsync（OS 决定何时刷盘）
  1: 每次提交都 fsync（最安全，推荐）
  N: 每 N 次提交 fsync（折中）
```

#### binlog 用途

1. **主从复制**：Slave 读取 Master 的 binlog 重放
2. **数据恢复**：`mysqlbinlog` 工具恢复到指定时间点
3. **数据同步**：Canal 监听 binlog 同步到 ES、Redis 等

### 6.5 两阶段提交（2PC）

redo log 和 binlog 需要保持一致（否则主从数据不一致），通过**两阶段提交**实现：

```
┌─────────────────────────────────────────────────────┐
│ 1. 执行 SQL，修改 Buffer Pool 中的数据页             │
│ 2. 写入 undo log                                     │
│ 3. 写入 redo log（状态: prepare）         ← 第一阶段  │
│ 4. 写入 binlog                                       │
│ 5. 提交事务，redo log 状态改为 commit     ← 第二阶段  │
└─────────────────────────────────────────────────────┘
```

**崩溃恢复判断**：
- redo log 是 prepare，binlog **完整**：提交事务
- redo log 是 prepare，binlog **不完整**：回滚事务
- redo log 是 commit：提交事务

#### 组提交（Group Commit）

MySQL 5.6+ 引入，多个事务的 fsync 合并为一次，提高性能：

```
多个事务的 binlog 和 redo log 攒在一起：
事务1 ─┐
事务2 ──├─ 一次 fsync 刷盘
事务3 ─┘
```

### 6.6 三大日志协作流程

```
UPDATE user SET name='李四' WHERE id=1;

① 从 Buffer Pool 读取 id=1 的数据页（如不在则从磁盘加载）
② 写 undo log（记录旧值 name='张三'）
③ 在 Buffer Pool 中修改数据页（name='李四'，生成脏页）
④ 写 redo log（prepare 状态）
⑤ 写 binlog
⑥ redo log 改为 commit 状态
⑦ 事务提交成功

后台线程异步：
- Buffer Pool 脏页刷到磁盘（checkpoint）
- purge 线程清理不需要的 undo log
```

---

## 七、面试高频题

### Q1：MySQL 的四种隔离级别？默认哪种？

四种隔离级别（从低到高）：
1. **READ UNCOMMITTED**：可以读取未提交数据（脏读）
2. **READ COMMITTED**：只能读取已提交数据，但不可重复读
3. **REPEATABLE READ**：同一事务内多次读取结果一致（MySQL 默认）
4. **SERIALIZABLE**：完全串行化，最安全但性能最差

MySQL InnoDB 默认使用 **REPEATABLE READ**，并通过 MVCC + Next-Key Lock 基本解决了幻读问题。

---

### Q2：MVCC 的原理？ReadView 是什么？

MVCC 通过**隐藏字段**（DB_TRX_ID、DB_ROLL_PTR）和 **undo log 版本链**维护数据的多个版本。

ReadView 是快照读时生成的一致性视图，包含当前活跃事务 ID 列表（m_ids）、最小活跃事务 ID（min_trx_id）、下一个事务 ID（max_trx_id）。通过这些字段判断版本链中哪个版本对当前事务可见。

RC 级别每次 SELECT 生成新 ReadView（所以不可重复读）；RR 级别只在第一次 SELECT 生成（所以可重复读）。

---

### Q3：redo log 和 binlog 的区别？

| 对比 | redo log | binlog |
|------|----------|--------|
| 层级 | InnoDB 引擎层 | Server 层 |
| 内容 | 物理日志（数据页修改） | 逻辑日志（SQL/行变更） |
| 写入 | 循环写，固定大小 | 追加写，文件切换 |
| 用途 | 崩溃恢复 | 主从复制、数据恢复 |

两者通过**两阶段提交**保持一致性。

---

### Q4：什么是死锁？如何避免？

死锁是两个或多个事务互相等待对方持有的锁，形成循环等待。

InnoDB 通过 wait-for graph 检测死锁，回滚代价最小的事务。

避免死锁：按固定顺序访问数据、缩短事务、使用合理索引避免全表锁、一次性锁定所需资源。

---

### Q5：InnoDB 的行锁有哪些类型？

1. **Record Lock**：锁定索引记录本身
2. **Gap Lock**：锁定索引记录之间的间隙，防止插入
3. **Next-Key Lock**：Record Lock + Gap Lock，RR 级别默认锁类型
4. **Insert Intention Lock**：INSERT 等待 Gap Lock 时的特殊锁

---

### Q6：什么是两阶段提交？为什么需要？

两阶段提交保证 redo log 和 binlog 的一致性。第一阶段 redo log 写入 prepare 状态，第二阶段 binlog 写入后 redo log 改为 commit。

如果不做两阶段提交，redo log 和 binlog 可能不一致，导致崩溃恢复后主库数据和从库数据不一致。

---

### Q7：MVCC 能否完全解决幻读？

不能。MVCC 只能解决**快照读**（普通 SELECT）的幻读。对于**当前读**（SELECT FOR UPDATE、INSERT、UPDATE、DELETE），需要 Next-Key Lock 来防止幻读。

即使在 RR 级别下，某些特殊场景仍可能出现幻读：先快照读再当前读。

---

### Q8：Gap Lock 和 Next-Key Lock 分别在什么场景下使用？

- **等值查询 + 唯一索引 + 记录存在**：退化为 Record Lock
- **等值查询 + 唯一索引 + 记录不存在**：退化为 Gap Lock
- **等值查询 + 普通索引**：Next-Key Lock + 向右遍历的 Gap Lock
- **范围查询**：Next-Key Lock（可能包含多个区间）

---

### Q9：innodb_flush_log_at_trx_commit 和 sync_binlog 参数怎么设置？

- 数据安全最高（推荐）：`innodb_flush_log_at_trx_commit=1` + `sync_binlog=1`（双 1 配置）
- 性能优先：`innodb_flush_log_at_trx_commit=2` + `sync_binlog=100`
- 双 1 配置保证每次事务提交都持久化，但性能有损耗

---

### Q10：RC 和 RR 隔离级别在实际生产中怎么选？

- **RR（默认）**：更安全，防止不可重复读和部分幻读。适合对数据一致性要求高的场景
- **RC**：互联网公司常用。优势：没有 Gap Lock 减少死锁、ReadView 创建更频繁但更贴近实时、binlog 必须用 ROW 格式
- 阿里巴巴等公司生产环境大量使用 **RC** 级别

---

### Q11：如何排查和解决死锁问题？

```sql
-- 1. 查看最近的死锁信息
SHOW ENGINE INNODB STATUS;

-- 2. 开启死锁日志
SET GLOBAL innodb_print_all_deadlocks = ON;

-- 3. 查看当前锁等待
SELECT * FROM performance_schema.data_lock_waits;        -- MySQL 8.0
SELECT * FROM information_schema.innodb_lock_waits;       -- MySQL 5.7

-- 4. 查看当前持有的锁
SELECT * FROM performance_schema.data_locks;              -- MySQL 8.0
```

分析步骤：找到死锁日志 --> 分析两个事务持有和等待的锁 --> 确认加锁顺序 --> 调整业务逻辑或索引。

---

### Q12：长事务有什么危害？如何避免？

危害：
1. undo log 膨胀（MVCC 旧版本不能被 purge 清理）
2. 锁持有时间长，阻塞其他事务
3. 占用数据库连接资源
4. 导致主从复制延迟

避免：
- 避免在事务中做耗时操作（RPC 调用、文件处理等）
- 设置 `innodb_lock_wait_timeout` 控制锁等待时间
- 监控 `information_schema.innodb_trx` 杀掉长事务
- 使用 `SET autocommit = 1`，避免意外开启长事务
