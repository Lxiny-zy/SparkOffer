# MySQL 基础与索引

---

## 一、存储引擎

### 1.1 InnoDB（MySQL 5.5+ 默认）

- 支持事务（ACID）、外键约束、行级锁
- 聚簇索引组织表，数据和主键索引存储在一起
- 支持 MVCC（多版本并发控制），实现非锁定一致性读
- 支持崩溃恢复（基于 redo log 的 WAL 机制）
- 自适应哈希索引（AHI）：对频繁访问的索引页自动建立哈希索引
- Buffer Pool 缓冲池：缓存热点数据页和索引页，减少磁盘 I/O
- Change Buffer：缓存对二级索引的修改操作，合并后再写入磁盘
- 适合读写混合、事务要求高的 OLTP 场景

### 1.2 MyISAM

- 不支持事务和行级锁，只有表级锁
- 非聚簇索引，数据和索引分开存储（.MYD 数据文件 + .MYI 索引文件）
- 支持全文索引（InnoDB 5.6+ 也支持了）
- 支持压缩表，适合归档数据
- 读多写少、不需要事务的场景可以考虑

### 1.3 InnoDB vs MyISAM 对比

| 特性 | InnoDB | MyISAM |
|------|--------|--------|
| 事务 | 支持 | 不支持 |
| 锁粒度 | 行锁 | 表锁 |
| 外键 | 支持 | 不支持 |
| MVCC | 支持 | 不支持 |
| 崩溃恢复 | 支持（redo log） | 不支持 |
| 索引类型 | 聚簇索引 | 非聚簇索引 |
| COUNT(*) | 需遍历（无计数器） | 有行数计数器，速度快 |
| 全文索引 | 5.6+ 支持 | 支持 |
| 适用场景 | OLTP | 读密集型/归档 |

### 1.4 其他存储引擎

- **Memory**：数据存内存，重启丢失，适合临时表
- **Archive**：高压缩比，只支持 INSERT 和 SELECT，适合日志归档
- **CSV**：以 CSV 格式存储，可直接用文本编辑器查看
- **NDB（Cluster）**：MySQL Cluster 使用，支持分布式高可用

---

## 二、索引数据结构详解

### 2.1 B+ 树 vs B 树 vs Hash 索引

#### B 树（B-Tree）

```
               [30, 60]
              /    |    \
         [10,20] [40,50] [70,80,90]
          /|\     /|\       /| | \
        数据   数据   数据   数据
```

- 所有节点都存储数据（key + data）
- 搜索可能在非叶子节点结束
- 每个节点能存的 key 较少（因为还要存 data）

#### B+ 树（B+Tree）

```
               [30,    60]              <- 非叶子节点只存 key
              /    |      \
         [10,20] [30,40,50] [60,70,80,90]  <- 叶子节点存 key+data
           |-------->|-------->|            <- 叶子节点双向链表
```

- **非叶子节点只存 key**，不存数据 --> 一个节点可以存更多 key --> 树更矮 --> I/O 次数更少
- **所有数据都在叶子节点**，查询稳定（每次都走到叶子层，时间复杂度稳定 O(log n)）
- **叶子节点通过双向链表相连** --> 天然支持范围查询和排序
- 每个节点对应磁盘上的一个页（InnoDB 默认 16KB）
- 高度通常为 2~4 层，千万级数据 3 层 B+ 树即可覆盖

#### Hash 索引

```
hash(key1) --> bucket1 --> data1
hash(key2) --> bucket2 --> data2
hash(key3) --> bucket1 --> data3 (hash冲突，链表)
```

- 基于哈希表实现，等值查询 O(1) 极快
- **不支持范围查询**（WHERE age > 20）
- **不支持排序**
- **存在哈希冲突**
- 不支持最左前缀匹配
- Memory 引擎默认使用 Hash 索引

#### 三者对比总结

| 对比项 | B 树 | B+ 树 | Hash 索引 |
|-------|------|-------|----------|
| 数据存储位置 | 所有节点 | 仅叶子节点 | 哈希桶 |
| 范围查询 | 不高效 | 高效（链表） | 不支持 |
| 等值查询 | O(log n) | O(log n) | O(1) |
| 排序 | 不高效 | 高效 | 不支持 |
| 磁盘 I/O | 较多 | 较少 | 取决于冲突 |
| 稳定性 | 不稳定 | 稳定 | 不稳定 |
| MySQL 使用 | 不用 | InnoDB 默认 | Memory 引擎 |

#### 为什么 MySQL 选择 B+ 树？

1. **磁盘 I/O 优化**：非叶子节点不存数据，单个节点可以容纳更多 key，树更矮，减少磁盘寻址
2. **范围查询高效**：叶子节点链表相连，范围扫描只需遍历链表
3. **查询性能稳定**：任何查询都需走到叶子节点，时间复杂度稳定
4. **排序友好**：叶子节点有序链表，ORDER BY 和 GROUP BY 友好
5. **全表扫描高效**：只需遍历叶子节点链表，不需要遍历整棵树

### 2.2 B+ 树高度计算

假设主键为 bigint（8 字节），指针 6 字节，数据行 1KB：

- 非叶子节点：16KB / (8+6) = 1170 个 key
- 叶子节点：16KB / 1KB = 16 条数据
- 2 层：1170 * 16 = 18,720 条
- 3 层：1170 * 1170 * 16 = **2190 万条**
- 4 层：约 **256 亿条**

> 这就是为什么千万级别的表，B+ 树通常只需要 3 层，即 3 次磁盘 I/O。

---

## 三、索引分类深入

### 3.1 按数据存储方式分类

#### 聚簇索引（Clustered Index）

```
主键索引（聚簇索引）
       [30]
      /    \
   [10,20] [30,40,50]
     |         |
  完整行数据  完整行数据    <- 叶子节点存储完整行数据
```

- InnoDB 表数据按主键顺序存储，**数据即索引，索引即数据**
- 一张表只能有一个聚簇索引（因为数据只能按一种方式排序）
- 主键选择规则：
  1. 显式定义的主键
  2. 第一个不含 NULL 的唯一索引
  3. InnoDB 自动生成隐藏的 ROW_ID（6 字节递增）
- 优点：主键查询极快，相邻数据存储在一起（局部性好）
- 缺点：插入顺序影响性能（乱序插入导致页分裂），二级索引需要两次查找

#### 二级索引（Secondary Index / 非聚簇索引）

```
二级索引（如 name 索引）
       [李四]
      /      \
   [王五,张三] [李四,赵六]
      |           |
   主键值 10,20  主键值 30,40    <- 叶子节点存储主键值
```

- 叶子节点存储的是**主键值**，而不是完整行数据
- 查询过程：先通过二级索引找到主键值 --> 再通过聚簇索引找到完整行（**回表**）
- 一张表可以有多个二级索引

#### 回表（Back to Table）

```
SELECT * FROM user WHERE name = '张三';

步骤1: name索引 --> 找到主键 id=5
步骤2: 主键索引 --> 找到 id=5 的完整行数据（回表）
```

- 回表会增加一次 B+ 树查找，影响性能
- 优化方向：使用覆盖索引避免回表

### 3.2 覆盖索引（Covering Index）

```sql
-- 联合索引 (name, age)

-- 需要回表（查询了索引中没有的列 email）
SELECT name, age, email FROM user WHERE name = '张三';

-- 覆盖索引（查询的列都在索引中，不需要回表）
SELECT name, age FROM user WHERE name = '张三';
-- EXPLAIN 的 Extra 列会显示 "Using index"
```

- **定义**：查询的所有列都包含在索引中，无需回表
- EXPLAIN 中 Extra 列显示 `Using index` 即为覆盖索引
- 是一种查询优化策略，不是独立的索引类型
- 实践建议：根据高频查询设计联合索引以实现覆盖索引

### 3.3 联合索引（Composite Index）

```sql
-- 创建联合索引
ALTER TABLE user ADD INDEX idx_name_age_city(name, age, city);

-- 索引结构：先按 name 排序，name 相同按 age 排序，age 相同按 city 排序
```

#### 最左前缀原则

```sql
-- 联合索引 (a, b, c)

-- 能命中索引的查询：
WHERE a = 1                     -- 命中 a
WHERE a = 1 AND b = 2           -- 命中 a, b
WHERE a = 1 AND b = 2 AND c = 3 -- 命中 a, b, c
WHERE a = 1 AND c = 3           -- 命中 a（c 无法使用，因为 b 断了）
WHERE a = 1 AND b > 2 AND c = 3 -- 命中 a, b（c 无法使用，因为 b 是范围查询）

-- 不能命中索引的查询：
WHERE b = 2                     -- 不命中（缺少最左列 a）
WHERE b = 2 AND c = 3           -- 不命中
WHERE c = 3                     -- 不命中
```

> MySQL 8.0 引入了**索引跳跃扫描（Index Skip Scan）**，在某些条件下即使不满足最左前缀也能使用索引，但有限制条件。

#### 索引下推（Index Condition Pushdown, ICP）

```sql
-- MySQL 5.6+ 引入
-- 联合索引 (name, age)
SELECT * FROM user WHERE name LIKE '张%' AND age = 25;

-- 无 ICP：通过 name LIKE '张%' 找到所有匹配行，回表后再过滤 age=25
-- 有 ICP：在索引层直接过滤 age=25，减少回表次数
-- EXPLAIN Extra 显示 "Using index condition"
```

### 3.4 按功能分类

| 索引类型 | 说明 | 示例 |
|---------|------|------|
| 主键索引 | 唯一且不为空 | `PRIMARY KEY (id)` |
| 唯一索引 | 值唯一，允许 NULL | `UNIQUE KEY (email)` |
| 普通索引 | 最基本的索引 | `INDEX (name)` |
| 全文索引 | 用于全文搜索 | `FULLTEXT (content)` |
| 前缀索引 | 对字符串前 N 位建索引 | `INDEX (name(10))` |
| 空间索引 | 用于地理数据 | `SPATIAL INDEX (geo)` |

#### 前缀索引

```sql
-- 对长字符串只索引前 N 个字符，减少索引空间
ALTER TABLE user ADD INDEX idx_email(email(6));

-- 如何选择前缀长度？计算区分度
SELECT
  COUNT(DISTINCT LEFT(email, 4)) / COUNT(*) AS sel4,
  COUNT(DISTINCT LEFT(email, 5)) / COUNT(*) AS sel5,
  COUNT(DISTINCT LEFT(email, 6)) / COUNT(*) AS sel6,
  COUNT(DISTINCT LEFT(email, 7)) / COUNT(*) AS sel7
FROM user;
-- 选择区分度接近完整列区分度的最小长度
```

- 注意：前缀索引**不能用作覆盖索引**，因为无法判断前缀是否完整匹配

---

## 四、索引失效的 15 种常见场景

### 场景 1：对索引列使用函数

```sql
-- 失效
SELECT * FROM user WHERE YEAR(create_time) = 2024;
SELECT * FROM user WHERE LEFT(name, 3) = '张三丰';

-- 优化
SELECT * FROM user WHERE create_time >= '2024-01-01' AND create_time < '2025-01-01';
```

### 场景 2：对索引列进行运算

```sql
-- 失效
SELECT * FROM user WHERE id + 1 = 10;
SELECT * FROM user WHERE id * 2 = 20;

-- 优化
SELECT * FROM user WHERE id = 9;
SELECT * FROM user WHERE id = 10;
```

### 场景 3：隐式类型转换

```sql
-- phone 是 VARCHAR 类型
-- 失效（字符串列用数字查询，MySQL 将 phone 转为数字比较）
SELECT * FROM user WHERE phone = 13800138000;

-- 优化（使用正确的类型）
SELECT * FROM user WHERE phone = '13800138000';
```

> 规则：MySQL 在比较时，会将字符串转成数字。如果索引列是字符串，传入数字会导致对索引列做隐式函数转换，索引失效。

### 场景 4：隐式字符编码转换

```sql
-- table_a 是 utf8, table_b 是 utf8mb4
-- 失效（MySQL 会对 table_a.name 做 CONVERT 转换）
SELECT * FROM table_a a JOIN table_b b ON a.name = b.name;

-- 优化：统一字符编码为 utf8mb4
```

### 场景 5：LIKE 以通配符开头

```sql
-- 失效
SELECT * FROM user WHERE name LIKE '%三';
SELECT * FROM user WHERE name LIKE '%三%';

-- 能使用索引
SELECT * FROM user WHERE name LIKE '张%';
```

### 场景 6：OR 条件中有非索引列

```sql
-- name 有索引，age 没有索引
-- 失效（需要全表扫描 age，所以 name 的索引也没用了）
SELECT * FROM user WHERE name = '张三' OR age = 25;

-- 优化方案1：给 age 也加索引
-- 优化方案2：改写为 UNION
SELECT * FROM user WHERE name = '张三'
UNION ALL
SELECT * FROM user WHERE age = 25;
```

### 场景 7：不满足最左前缀原则

```sql
-- 联合索引 (a, b, c)
-- 失效
SELECT * FROM user WHERE b = 2;
SELECT * FROM user WHERE c = 3;
SELECT * FROM user WHERE b = 2 AND c = 3;
```

### 场景 8：范围查询后的列无法使用索引

```sql
-- 联合索引 (a, b, c)
-- b 使用范围查询后，c 列无法使用索引
SELECT * FROM user WHERE a = 1 AND b > 10 AND c = 3;
-- 只有 a 和 b 使用了索引

-- 优化：调整联合索引顺序为 (a, c, b)，等值条件在前，范围条件在后
```

### 场景 9：使用 != 或 NOT IN

```sql
-- 可能导致索引失效（优化器判断扫描行数过多时选择全表扫描）
SELECT * FROM user WHERE status != 1;
SELECT * FROM user WHERE id NOT IN (1, 2, 3);

-- NOT EXISTS 同理
```

> 注意：不是一定失效，取决于优化器对成本的判断。如果过滤后剩余数据量很小，可能仍使用索引。

### 场景 10：IS NOT NULL

```sql
-- 可能导致索引失效
SELECT * FROM user WHERE name IS NOT NULL;

-- IS NULL 通常可以使用索引
SELECT * FROM user WHERE name IS NULL;
```

### 场景 11：ORDER BY 无法使用索引排序

```sql
-- 联合索引 (a, b, c)
-- 使用 filesort（索引失效于排序）
SELECT * FROM user ORDER BY b, c;       -- 缺少 a
SELECT * FROM user ORDER BY a ASC, b DESC; -- 排序方向不一致（MySQL 8.0 前）
SELECT * FROM user WHERE a = 1 ORDER BY c; -- 跳过了 b
```

### 场景 12：SELECT *

```sql
-- 导致无法使用覆盖索引，必须回表
SELECT * FROM user WHERE name = '张三';

-- 优化：只查需要的列
SELECT id, name, age FROM user WHERE name = '张三';
```

### 场景 13：优化器认为全表扫描更快

```sql
-- 当查询结果占总行数比例较大时（通常超过 30%），优化器可能选择全表扫描
SELECT * FROM user WHERE status = 1; -- 如果 90% 的数据 status=1
```

### 场景 14：使用 IN 包含大量值

```sql
-- IN 列表过长时可能走全表扫描
SELECT * FROM user WHERE id IN (1, 2, 3, ..., 10000);

-- 优化：改为 JOIN 临时表或分批查询
```

### 场景 15：两列做比较

```sql
-- 失效
SELECT * FROM user WHERE col_a > col_b;
-- 即使 col_a 和 col_b 都有索引也无法使用
```

---

## 五、EXPLAIN 执行计划详解

```sql
EXPLAIN SELECT * FROM user WHERE name = 'test';
```

### 5.1 各字段详解

| 字段 | 含义 | 重点说明 |
|------|------|---------|
| **id** | 查询序号 | 相同 id 从上往下执行；不同 id 大的先执行 |
| **select_type** | 查询类型 | SIMPLE/PRIMARY/SUBQUERY/DERIVED/UNION |
| **table** | 访问的表名 | 可能是实际表名、别名或 derived 临时表 |
| **partitions** | 匹配的分区 | 非分区表显示 NULL |
| **type** | 访问类型（重点） | 性能从好到差排序见下方 |
| **possible_keys** | 可能使用的索引 | 优化器评估后可能不用 |
| **key** | 实际使用的索引 | NULL 表示没有使用索引 |
| **key_len** | 使用索引的字节长度 | 可判断联合索引用了几个列 |
| **ref** | 索引的参照列 | const/字段名/func |
| **rows** | 预估扫描行数 | 越小越好 |
| **filtered** | 按条件过滤后的行百分比 | 越大越好，100% 最佳 |
| **Extra** | 额外信息（重点） | 见下方详解 |

### 5.2 type 访问类型（从好到差）

```
system > const > eq_ref > ref > fulltext > ref_or_null > index_merge >
unique_subquery > index_subquery > range > index > ALL
```

| type | 含义 | 示例 |
|------|------|------|
| **system** | 表只有一行记录 | 系统表 |
| **const** | 通过主键或唯一索引等值查询，最多一行 | `WHERE id = 1` |
| **eq_ref** | JOIN 时使用主键或唯一索引，每次关联一行 | `JOIN ON a.id = b.user_id`（b.user_id 是主键） |
| **ref** | 使用普通索引等值查询 | `WHERE name = '张三'` |
| **ref_or_null** | 类似 ref 但还查 NULL | `WHERE name = '张三' OR name IS NULL` |
| **index_merge** | 使用多个索引合并 | `WHERE name = 'x' OR age = 25` |
| **range** | 索引范围扫描 | `WHERE id > 10`、`WHERE id IN (1,2,3)` |
| **index** | 全索引扫描（遍历索引树） | `SELECT count(*) FROM user` |
| **ALL** | 全表扫描（最差） | 无索引查询 |

> 一般来说，至少要达到 **range** 级别，最好达到 **ref** 级别。

### 5.3 Extra 字段详解

| Extra | 含义 | 是否需要优化 |
|-------|------|------------|
| **Using index** | 覆盖索引，无需回表 | 好，无需优化 |
| **Using where** | 在存储引擎层过滤后，Server 层再过滤 | 视情况而定 |
| **Using index condition** | 索引下推（ICP） | 好，已优化 |
| **Using temporary** | 使用了临时表 | 差，需优化 |
| **Using filesort** | 额外排序操作 | 差，需优化 |
| **Using join buffer** | JOIN 时使用缓冲区（无索引） | 差，需给 JOIN 列加索引 |
| **Select tables optimized away** | 聚合函数直接从索引获取结果 | 最优 |
| **Impossible WHERE** | WHERE 条件永远为 false | 检查 SQL 逻辑 |

### 5.4 key_len 计算规则

```
字符串类型：
  char(n)   : n * 字符集字节数（utf8=3, utf8mb4=4）
  varchar(n): n * 字符集字节数 + 2字节（记录长度）

数值类型：
  tinyint: 1字节   smallint: 2字节
  int: 4字节       bigint: 8字节

时间类型：
  date: 3字节      timestamp: 4字节
  datetime: 8字节（MySQL 5.6.4+ 为 5字节）

如果列允许 NULL，额外 +1 字节
```

> key_len 可以用来判断联合索引中实际使用了几个列。

---

## 六、SQL 优化实战

### 6.1 慢查询分析流程

```sql
-- 1. 开启慢查询日志
SET GLOBAL slow_query_log = ON;
SET GLOBAL long_query_time = 1;  -- 超过1秒为慢查询
SET GLOBAL slow_query_log_file = '/var/log/mysql/slow.log';

-- 2. 使用 mysqldumpslow 分析慢查询日志
-- 按查询时间排序，取 Top 10
-- mysqldumpslow -s t -t 10 /var/log/mysql/slow.log

-- 3. 使用 EXPLAIN 分析执行计划
EXPLAIN SELECT ...;

-- 4. 使用 SHOW PROFILE 分析查询各阶段耗时
SET profiling = 1;
SELECT ...;
SHOW PROFILES;
SHOW PROFILE FOR QUERY 1;

-- 5. MySQL 8.0+ 使用 EXPLAIN ANALYZE 获取实际执行统计
EXPLAIN ANALYZE SELECT ...;
```

### 6.2 索引设计原则

1. **选择区分度高的列**：`COUNT(DISTINCT col) / COUNT(*)` 越接近 1 越好
2. **等值条件在前，范围条件在后**：`INDEX(status, create_time)` 优于 `INDEX(create_time, status)`
3. **覆盖高频查询**：根据高频 SQL 设计联合索引实现覆盖索引
4. **控制索引数量**：单表索引不超过 5~6 个，过多影响写入性能
5. **利用前缀索引节省空间**：长字符串用前缀索引
6. **避免冗余索引**：`INDEX(a)` 和 `INDEX(a, b)` 中前者冗余
7. **主键用自增 ID**：避免随机插入导致页分裂
8. **不要在低区分度列上建索引**：如 gender（男/女）单独建索引意义不大
9. **长字符串考虑哈希索引方案**：对长字符串做 CRC32/MD5 后建索引

### 6.3 SQL 优化技巧

#### 分页优化

```sql
-- 深分页问题：LIMIT 1000000, 10 需要扫描 1000010 行
SELECT * FROM user LIMIT 1000000, 10;

-- 优化方案1：游标分页（推荐）
SELECT * FROM user WHERE id > 1000000 LIMIT 10;

-- 优化方案2：延迟关联
SELECT u.* FROM user u
INNER JOIN (SELECT id FROM user LIMIT 1000000, 10) t
ON u.id = t.id;
```

#### JOIN 优化

```sql
-- 1. 小表驱动大表（MySQL 优化器通常会自动选择）
-- 2. JOIN 列一定要建索引
-- 3. 避免超过 3 张表 JOIN
-- 4. 使用 STRAIGHT_JOIN 强制驱动表顺序（谨慎使用）
SELECT STRAIGHT_JOIN * FROM small_table s JOIN big_table b ON s.id = b.sid;
```

#### COUNT 优化

```sql
-- COUNT(*) vs COUNT(1) vs COUNT(col)
-- COUNT(*) 和 COUNT(1) 性能一样，InnoDB 会选择最小的索引树遍历
-- COUNT(col) 会排除该列为 NULL 的行
-- 大表 COUNT 可用 Redis 缓存计数、或使用近似值 SHOW TABLE STATUS
```

#### 批量操作优化

```sql
-- 差：循环单条插入
INSERT INTO user (name) VALUES ('a');
INSERT INTO user (name) VALUES ('b');

-- 好：批量插入
INSERT INTO user (name) VALUES ('a'), ('b'), ('c'), ...;
-- 一次不要超过 500~1000 条，避免事务过大
```

#### 子查询改 JOIN

```sql
-- 差：子查询（可能产生临时表）
SELECT * FROM user WHERE id IN (SELECT user_id FROM orders WHERE amount > 100);

-- 好：改为 JOIN
SELECT DISTINCT u.* FROM user u
INNER JOIN orders o ON u.id = o.user_id
WHERE o.amount > 100;
```

---

## 七、分库分表

### 7.1 为什么要分库分表

- 单表数据量过大（超过 2000 万~5000 万行），查询变慢
- 单库连接数达到上限
- 单机磁盘空间不足
- 数据库 QPS/TPS 达到瓶颈

### 7.2 拆分方式

#### 垂直拆分

```
垂直分库：按业务拆分
┌──────────┐     ┌──────────┐     ┌──────────┐
│ 用户库    │     │ 订单库    │     │ 商品库    │
│ user      │     │ order     │     │ product   │
│ user_info │     │ order_item│     │ category  │
└──────────┘     └──────────┘     └──────────┘

垂直分表：拆分字段
┌──────────┐           ┌──────────┐   ┌──────────────┐
│ user 表   │   -->    │ user 表   │   │ user_detail   │
│ id        │          │ id        │   │ user_id       │
│ name      │          │ name      │   │ bio           │
│ age       │          │ age       │   │ avatar        │
│ bio       │          │ phone     │   │ address       │
│ avatar    │          └──────────┘   └──────────────┘
│ address   │           (高频字段)      (低频大字段)
│ phone     │
└──────────┘
```

#### 水平拆分

```
水平分表：按行拆分（相同表结构，不同数据）
┌──────────┐           ┌──────────┐  ┌──────────┐  ┌──────────┐
│ order 表  │   -->    │ order_0   │  │ order_1   │  │ order_2   │
│ 3000万行  │          │ 1000万行   │  │ 1000万行   │  │ 1000万行   │
└──────────┘          └──────────┘  └──────────┘  └──────────┘

水平分库：每个分片在不同数据库实例中
┌─────────┐  ┌─────────┐  ┌─────────┐
│ DB实例1   │  │ DB实例2   │  │ DB实例3   │
│ order_0  │  │ order_1  │  │ order_2  │
└─────────┘  └─────────┘  └─────────┘
```

### 7.3 分片策略

| 策略 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| **Hash 取模** | `shard = hash(key) % N` | 数据均匀分布 | 扩容需要数据迁移 |
| **范围分片** | 按 ID 或时间范围划分 | 扩容方便 | 热点问题（新数据集中） |
| **一致性哈希** | 环形哈希空间 | 扩缩容迁移数据少 | 实现复杂 |
| **查表路由** | 维护路由表 | 灵活 | 路由表是瓶颈 |

### 7.4 分库分表带来的问题

| 问题 | 解决方案 |
|------|---------|
| **跨分片查询** | 中间件聚合、冗余数据、ES 搜索 |
| **跨分片 JOIN** | 全局表（字典表冗余到每个分片）、应用层 JOIN |
| **分布式事务** | Seata、TCC、最终一致性 |
| **分布式 ID** | Snowflake 雪花算法、号段模式（Leaf、Uid-generator） |
| **排序分页** | 各分片排序后归并排序 |
| **扩容迁移** | 一致性哈希、成倍扩容减少迁移 |

### 7.5 ShardingSphere

Apache ShardingSphere 是主流的分库分表中间件，包含：

- **ShardingSphere-JDBC**：Java 应用层分片，以 JAR 包形式接入
  - 轻量级，无需额外部署
  - 支持任何实现 JDBC 规范的数据库
  - 性能高（应用内直连数据库）
- **ShardingSphere-Proxy**：数据库中间件代理层
  - 对应用透明，不需要改代码
  - 支持跨语言
  - 多了一层网络代理，性能略低

```yaml
# ShardingSphere-JDBC 配置示例（YAML）
rules:
  - !SHARDING
    tables:
      order:
        actualDataNodes: ds_${0..1}.order_${0..2}
        tableStrategy:
          standard:
            shardingColumn: order_id
            shardingAlgorithmName: order_mod
        databaseStrategy:
          standard:
            shardingColumn: user_id
            shardingAlgorithmName: user_mod
    shardingAlgorithms:
      order_mod:
        type: MOD
        props:
          sharding-count: 3
      user_mod:
        type: MOD
        props:
          sharding-count: 2
```

### 7.6 其他分库分表方案

| 方案 | 类型 | 特点 |
|------|------|------|
| **MyCat** | Proxy | 老牌中间件，社区活跃度下降 |
| **DBLE** | Proxy | MyCat 优化版 |
| **Vitess** | Proxy | YouTube 开源，K8s 原生 |
| **TiDB** | NewSQL | 兼容 MySQL，自动分片，无需中间件 |
| **CockroachDB** | NewSQL | 分布式 SQL，强一致性 |

---

## 八、面试高频题

### Q1：B+ 树和 B 树的区别？为什么 MySQL 用 B+ 树？

**B 树**：所有节点都存数据；**B+ 树**：只有叶子节点存数据，非叶子节点只存索引 key。

MySQL 选择 B+ 树的原因：
1. 非叶子节点不存数据 -> 单节点可容纳更多 key -> 树更矮 -> 磁盘 I/O 更少
2. 叶子节点链表相连 -> 范围查询只需遍历链表 -> 高效
3. 所有查询都走到叶子节点 -> 查询性能稳定

---

### Q2：什么是回表？如何避免？

**回表**：通过二级索引查到主键值后，再去聚簇索引查完整行数据的过程。

**避免方式**：使用覆盖索引，让查询需要的列都包含在索引中，EXPLAIN 的 Extra 显示 `Using index`。

---

### Q3：联合索引 (a, b, c) 哪些查询能命中索引？

能命中：`a=1`、`a=1 AND b=2`、`a=1 AND b=2 AND c=3`（最左前缀）。
部分命中：`a=1 AND c=3`（只用到 a）、`a=1 AND b>2 AND c=3`（只用到 a, b）。
不能命中：`b=2`、`c=3`、`b=2 AND c=3`（缺少最左列 a）。

---

### Q4：索引失效的常见场景？

1. 对索引列使用函数或运算
2. 隐式类型转换（字符串列传数字）
3. LIKE 以通配符开头（`%xxx`）
4. OR 条件中有非索引列
5. 不满足最左前缀原则
6. 范围查询后的列无法用索引
7. 使用 `!=`、`NOT IN`、`IS NOT NULL`（视情况）
8. 优化器认为全表扫描更快

---

### Q5：EXPLAIN 中哪些字段最重要？

**type**：访问类型，至少要 range 级别；**key**：实际使用的索引；**rows**：预估扫描行数；**Extra**：`Using index`（好）、`Using filesort`/`Using temporary`（需优化）。

---

### Q6：如何优化深分页 LIMIT 1000000, 10？

1. **游标分页**：`WHERE id > last_id LIMIT 10`（需要 id 连续递增）
2. **延迟关联**：先在索引中快速定位 id，再用 id 回表查完整数据
3. 业务层限制：禁止跳页，只允许上一页/下一页

---

### Q7：什么时候应该分库分表？有哪些方案？

**时机**：单表超过 2000 万行、单库 QPS 超过瓶颈、磁盘空间不足。

**方案**：
- 垂直拆分：按业务拆库、按字段拆表
- 水平拆分：按 Hash/范围/一致性哈希拆分数据

**中间件**：ShardingSphere（推荐）、MyCat；**NewSQL**：TiDB 可免分库分表。

---

### Q8：聚簇索引和非聚簇索引的区别？

| 对比项 | 聚簇索引 | 非聚簇索引 |
|-------|---------|-----------|
| 叶子节点 | 存完整行数据 | 存主键值 |
| 数量 | 一张表只有一个 | 可以有多个 |
| 查询 | 直接获取数据 | 需要回表 |
| 插入性能 | 自增主键最优 | 影响较小 |

---

### Q9：索引下推（ICP）是什么？

MySQL 5.6 引入。使用联合索引时，在索引遍历阶段就对索引中包含的列进行条件过滤，减少回表次数。EXPLAIN Extra 显示 `Using index condition`。

---

### Q10：为什么推荐使用自增 ID 作为主键？

1. **顺序写入**：自增 ID 保证数据按顺序插入，避免页分裂
2. **减少磁盘碎片**：顺序写入使数据页利用率高
3. **二级索引更小**：主键越小，二级索引占用空间越小（二级索引叶子节点存主键值）
4. **空间占用**：int(4字节)/bigint(8字节) 比 UUID(36字节) 小很多

---

### Q11：MySQL 单表多大需要优化？如何判断？

经验值：**2000 万 ~ 5000 万行**，但实际取决于行大小和查询复杂度。

判断指标：
- 慢查询增多
- 表空间超过 10GB
- 索引深度增加，查询走索引但仍慢
- Buffer Pool 命中率下降

---

### Q12：覆盖索引、联合索引、前缀索引分别什么时候用？

- **覆盖索引**：高频查询只需要索引中的列时使用，避免回表
- **联合索引**：多个列经常一起出现在 WHERE/ORDER BY 中时使用
- **前缀索引**：对长字符串列（如 URL、email）建索引时使用，节省空间但无法做覆盖索引
