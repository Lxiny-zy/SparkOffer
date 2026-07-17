# MongoDB

## 1. 概览

### 定位
最流行的文档型 NoSQL 数据库，JSON（BSON）格式存储，支持动态 Schema、水平扩展、丰富查询。

### 适用场景
- 内容管理（文章、评论、商品）
- 用户画像、游戏数据
- 日志、事件
- 实时分析
- IoT、时序数据（MongoDB 5+ 时序集合）
- 非强一致性交易场景

### 不适用
- 强事务要求（虽然 4.0 后支持多文档事务，但不是最优）
- 复杂 JOIN 查询
- 严格关系模型

### vs MySQL vs PostgreSQL
| 维度 | MongoDB | MySQL | PostgreSQL |
|------|---------|-------|------------|
| 模型 | 文档 | 关系 | 关系 + 文档（JSONB） |
| Schema | 灵活 | 严格 | 严格（JSONB 灵活） |
| 事务 | 4.0+ 多文档 | 强 | 强 |
| 水平扩展 | 原生 Sharding | 需分库分表 | 有限（Citus 等） |
| 聚合 | 强（Aggregation Pipeline） | SQL 聚合 | SQL 聚合（最强） |
| 查询语言 | 自有 | SQL | SQL |

---

## 2. 核心概念

### 层级

```
Cluster
 └── Database（库）≈ MySQL database
      └── Collection（集合）≈ table
           └── Document（文档）≈ row（JSON/BSON）
                └── Field（字段）≈ column
```

### Document（文档）

```json
{
  "_id": ObjectId("..."),
  "name": "Alice",
  "age": 25,
  "hobbies": ["reading", "coding"],
  "address": {
    "city": "Beijing",
    "zip": "100000"
  },
  "created_at": ISODate("2024-01-01T00:00:00Z")
}
```

- 最大 16MB
- `_id` 唯一，默认 ObjectId（12 字节：时间戳+机器+进程+序列）

### BSON
JSON 二进制扩展，支持更多类型：
- ObjectId、Decimal128
- Date、Timestamp
- Binary、Regex
- Int32/Int64/Double

---

## 3. 基本操作

### 安装（Docker）
```bash
docker run -d -p 27017:27017 --name mongo mongo:7
```

### CRUD

```javascript
// 插入
db.users.insertOne({ name: "Alice", age: 25 })
db.users.insertMany([{ name: "Bob" }, { name: "Carol" }])

// 查询
db.users.findOne({ _id: ObjectId("...") })
db.users.find({ age: { $gte: 18, $lt: 30 } })
db.users.find({ name: /^A/ })  // 正则
db.users.find({ "address.city": "Beijing" })  // 嵌套
db.users.find({ hobbies: "coding" })  // 数组元素匹配

// 投影（只返回指定字段）
db.users.find({}, { name: 1, age: 1, _id: 0 })

// 排序、分页
db.users.find().sort({ age: -1 }).skip(20).limit(10)

// 更新
db.users.updateOne(
    { _id: ObjectId("...") },
    { $set: { age: 26 }, $push: { hobbies: "gaming" } }
)
db.users.updateMany({ age: { $lt: 18 } }, { $set: { status: "minor" } })

// 删除
db.users.deleteOne({ _id: ObjectId("...") })
db.users.deleteMany({ status: "deleted" })

// 计数
db.users.countDocuments({ age: { $gte: 18 } })

// 去重
db.users.distinct("city")
```

---

## 4. 查询操作符

### 比较
```
$eq $ne $gt $gte $lt $lte $in $nin
```

### 逻辑
```
$and $or $not $nor
```

```javascript
db.users.find({
  $and: [
    { age: { $gte: 18 } },
    { $or: [{ city: "Beijing" }, { city: "Shanghai" }] }
  ]
})
```

### 元素
```
$exists    // 字段存在
$type      // 字段类型
```

### 数组
```
$all       // 包含所有
$elemMatch // 数组元素匹配条件
$size      // 数组长度
```

```javascript
// 数组中有年龄 > 18 的元素
db.posts.find({ comments: { $elemMatch: { age: { $gt: 18 } } } })
```

### 文本搜索
```javascript
// 需先建文本索引
db.articles.createIndex({ title: "text", content: "text" })
db.articles.find({ $text: { $search: "mongodb tutorial" } })
```

---

## 5. 更新操作符

### 字段
```
$set        设置值
$unset      删除字段
$inc        增加数值
$mul        乘
$rename     重命名字段
$min / $max 条件更新
$currentDate 设为当前时间
```

### 数组
```
$push       尾部添加
$pull       删除匹配元素
$addToSet   去重添加
$pop        删首/尾
$each       批量
$slice      限制长度
$sort       排序
```

```javascript
// 批量添加，限制最长 10，按时间倒序
db.users.updateOne(
    { _id: ... },
    {
        $push: {
            notifications: {
                $each: [...],
                $sort: { time: -1 },
                $slice: 10
            }
        }
    }
)
```

### Upsert
```javascript
db.users.updateOne(
    { email: "a@b.com" },
    { $set: { name: "Alice" } },
    { upsert: true }  // 不存在则插入
)
```

---

## 6. 索引

### 创建

```javascript
// 单字段
db.users.createIndex({ age: 1 })  // 1 升序，-1 降序

// 复合索引
db.users.createIndex({ city: 1, age: -1 })

// 唯一索引
db.users.createIndex({ email: 1 }, { unique: true })

// 稀疏（跳过不存在该字段的文档）
db.users.createIndex({ phone: 1 }, { sparse: true })

// 部分索引
db.users.createIndex(
    { age: 1 },
    { partialFilterExpression: { status: "active" } }
)

// TTL（自动过期）
db.sessions.createIndex({ createdAt: 1 }, { expireAfterSeconds: 3600 })

// 文本
db.articles.createIndex({ title: "text", content: "text" })

// 地理
db.places.createIndex({ location: "2dsphere" })

// Hash（分片用）
db.users.createIndex({ _id: "hashed" })
```

### 复合索引最左前缀
`{a:1, b:1, c:1}` 支持查询：
- `a`
- `a, b`
- `a, b, c`
- 不支持：`b`、`b, c`、`c`（只用 b/c 无法走索引）

### 索引策略
- **高选择性字段放前面**
- **等值字段放前面，范围字段放后面**（range 之后的索引字段可能无用）
- **排序字段可放复合索引尾**
- **覆盖索引**：查询字段都在索引中，无需回表

### 查看执行计划
```javascript
db.users.find({ age: 25 }).explain("executionStats")
// winningPlan / stage / indexName / docsExamined
```

理想：`IXSCAN`（索引扫描），避免 `COLLSCAN`（全表）。

---

## 7. Aggregation Pipeline（聚合管线）

### 概念
一组 stage 串联，文档流经各 stage 逐步转换。

### 常用 Stage

```
$match       过滤
$project     投影
$group       分组
$sort        排序
$skip $limit 分页
$unwind      展开数组
$lookup      关联其他集合（类 JOIN）
$addFields   新增字段
$facet       多路聚合
$bucket      分桶
$count       计数
$sample      随机抽样
$out $merge  结果输出
```

### 示例 1：统计各城市用户数

```javascript
db.users.aggregate([
    { $match: { status: "active" } },
    { $group: { _id: "$city", count: { $sum: 1 } } },
    { $sort: { count: -1 } },
    { $limit: 10 }
])
```

### 示例 2：Join（$lookup）

```javascript
db.orders.aggregate([
    {
        $lookup: {
            from: "products",
            localField: "product_id",
            foreignField: "_id",
            as: "product_info"
        }
    },
    { $unwind: "$product_info" },
    { $project: { order_id: "$_id", product_name: "$product_info.name" } }
])
```

### 示例 3：电商月销售

```javascript
db.orders.aggregate([
    { $match: { created_at: { $gte: ISODate("2024-01-01") } } },
    {
        $group: {
            _id: {
                year: { $year: "$created_at" },
                month: { $month: "$created_at" }
            },
            total: { $sum: "$amount" },
            count: { $sum: 1 }
        }
    },
    { $sort: { "_id.year": 1, "_id.month": 1 } }
])
```

### Facet（多路聚合）

```javascript
db.products.aggregate([
    {
        $facet: {
            "byCategory": [
                { $group: { _id: "$category", count: { $sum: 1 } } }
            ],
            "priceStats": [
                { $group: { _id: null, avg: { $avg: "$price" }, max: { $max: "$price" } } }
            ]
        }
    }
])
```

---

## 8. 事务

### 单文档事务
MongoDB 保证**单文档操作原子**。

### 多文档事务（4.0+）

```javascript
const session = client.startSession()
session.startTransaction()
try {
    db.accounts.updateOne({ _id: "A" }, { $inc: { balance: -100 } }, { session })
    db.accounts.updateOne({ _id: "B" }, { $inc: { balance: 100 } }, { session })
    session.commitTransaction()
} catch (e) {
    session.abortTransaction()
} finally {
    session.endSession()
}
```

**限制**：
- 事务默认 60s
- 涉及多分片事务 4.2+ 才支持
- 性能不如传统 RDBMS 事务

---

## 9. 复制集（Replica Set）

### 架构
```
Primary  ←→  Secondary  ←→  Secondary
  │             │              │
  └─ 所有写 ──┴──────────────┘
     异步同步 oplog
```

### 角色
- **Primary**：唯一，接受写
- **Secondary**：从 Primary 同步，可读（配置）
- **Arbiter**（仲裁）：只投票，不存数据

### 选举
- Primary 宕机 → Secondary 投票选新 Primary
- 多数派原则（N/2 + 1）

### 搭建（Docker Compose）
```yaml
services:
  mongo1:
    image: mongo:7
    command: --replSet rs0
  mongo2:
    image: mongo:7
    command: --replSet rs0
  mongo3:
    image: mongo:7
    command: --replSet rs0
```

```javascript
rs.initiate({
  _id: "rs0",
  members: [
    { _id: 0, host: "mongo1:27017" },
    { _id: 1, host: "mongo2:27017" },
    { _id: 2, host: "mongo3:27017" }
  ]
})
```

### Read / Write Concern

```javascript
// Write Concern
db.users.insertOne(doc, { writeConcern: { w: "majority", j: true, wtimeout: 5000 } })

// Read Concern
db.users.find().readConcern("majority")

// Read Preference
db.users.find().readPref("secondaryPreferred")
```

---

## 10. 分片（Sharding）

### 架构
```
    mongos（路由）
        │
   ┌────┼────┐
   ▼    ▼    ▼
 Shard1 Shard2 Shard3  ← 每个 shard 是一个 replica set
        │
    Config Servers（元数据）
```

### 分片键选择
- **高基数**：值分布广
- **低频率**：单值重复少
- **非单调增**：避免热点

**常用**：
- Hashed：均匀但无范围查询
- Range：范围查询好但可能热点
- Zoned：按地域分片

```javascript
sh.enableSharding("mydb")
sh.shardCollection("mydb.orders", { user_id: "hashed" })
```

### Chunk
- 分片键值范围
- 默认 64MB，满了自动分裂
- Balancer 自动在 shard 间迁移

---

## 11. Spring Data MongoDB

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-mongodb</artifactId>
</dependency>
```

### Entity

```java
@Document(collection = "users")
public class User {
    @Id private String id;
    private String name;
    @Indexed private Integer age;
    @Indexed(unique = true) private String email;
    @DBRef private Organization org;  // 关联
    private Address address;  // 嵌套
    @CreatedDate private Instant createdAt;
}
```

### Repository

```java
public interface UserRepository extends MongoRepository<User, String> {
    List<User> findByAgeGreaterThan(int age);
    Optional<User> findByEmail(String email);

    @Query("{ 'address.city': ?0, 'age': { $gte: ?1 } }")
    List<User> findByCityAndMinAge(String city, int minAge);

    @Query(value = "{}", fields = "{ name: 1, age: 1 }")
    List<User> findAllProjected();
}
```

### MongoTemplate

```java
@Autowired MongoTemplate mongo;

// 查询
Query q = new Query(Criteria.where("age").gte(18).and("city").is("Beijing"));
q.with(Sort.by(Sort.Direction.DESC, "created_at")).limit(10);
List<User> users = mongo.find(q, User.class);

// 聚合
Aggregation agg = Aggregation.newAggregation(
    Aggregation.match(Criteria.where("status").is("active")),
    Aggregation.group("city").count().as("total"),
    Aggregation.sort(Sort.Direction.DESC, "total"),
    Aggregation.limit(10)
);
AggregationResults<CityStat> results = mongo.aggregate(agg, "users", CityStat.class);

// 更新
Update u = new Update().set("status", "active").inc("loginCount", 1);
mongo.updateFirst(Query.query(Criteria.where("_id").is(id)), u, User.class);
```

### Reactive 版本

```java
public interface UserReactiveRepository extends ReactiveMongoRepository<User, String> {
    Flux<User> findByAgeGreaterThan(int age);
    Mono<User> findByEmail(String email);
}
```

---

## 12. 性能调优

### 索引
- 用 `explain()` 验证是否走索引
- 避免全表扫描（COLLSCAN）
- 索引不宜过多（每次写更新所有索引）

### 查询
- 只投影需要的字段（省网络）
- 分页用 `$gte last_id` + `limit` 替代 `skip`
- 热数据加大缓存（WiredTiger cache）

### 写入
- 批量写（bulkWrite）
- 合理 writeConcern
- journal：生产必开

### 架构
- 复制集至少 3 节点
- 分片用于 TB 级以上或高 QPS
- 读写分离：读走 secondary（接受一致性延迟）

---

## 13. 数据建模

### 嵌入（Embed）vs 引用（Reference）

**嵌入**：
- 关系紧密（博客 + 评论少量）
- 读为主
- 关联数据一起用

**引用**：
- 多对多
- 关联数据独立使用
- 数据量大

**示例**：
```javascript
// 嵌入（少量评论）
{
  post: "标题",
  comments: [{ user: "A", text: "..." }, ...]
}

// 引用（大量评论）
// posts 集合
{ _id: 1, post: "标题" }
// comments 集合
{ _id: 100, post_id: 1, user: "A", text: "..." }
```

### 1:N 选择
- N 很少（<几十）→ 嵌入数组
- N 多 → 引用
- N 很大（>几千）→ 引用 + 分页

### N:M
通常用引用 + 中间集合，类似关系模型。

---

## 14. 典型应用模式

### 活动摘要（Feeds）
```javascript
// 用户 Feed
{
  user_id: "u1",
  feed: [  // 最新 20 条（$slice）
    { post_id: "p1", type: "post", time: ... },
    { post_id: "p2", type: "like", time: ... }
  ]
}
```

### 版本化
```javascript
{
  _id: ...,
  version: 3,
  history: [  // 保留历史
    { version: 1, data: ..., time: ... },
    { version: 2, data: ..., time: ... }
  ]
}
```

### 分桶（Time Series 时序）
```javascript
// 每小时一个 bucket
{
  sensor_id: "s1",
  hour: ISODate("2024-01-01T10:00"),
  measurements: [
    { time: "10:00:01", value: 23.5 },
    ...
  ]
}
```

MongoDB 5+ 原生 Time Series Collection。

---

## 面试高频问题

**Q1：MongoDB 和 MySQL 的区别？**

| 维度 | MongoDB | MySQL |
|------|---------|-------|
| 模型 | 文档（JSON） | 关系（表） |
| Schema | 动态 | 固定 |
| JOIN | $lookup（较弱） | 强 |
| 事务 | 4.0+ 支持 | 强 |
| 扩展 | 原生分片 | 分库分表 |
| 查询 | 自有语法 | SQL |

**选择**：强事务/复杂 JOIN → MySQL；灵活 Schema/水平扩展 → MongoDB。

**Q2：MongoDB 索引原理？**

基于 B-tree（和 MySQL 类似）。`_id` 默认索引。支持：
- 单字段、复合
- 唯一、稀疏、部分
- TTL、文本、地理、hashed

复合索引遵循**最左前缀**原则。

**Q3：嵌入 vs 引用怎么选？**

- **嵌入**：一对少（blog + comments < 100）、读为主、数据一起用
- **引用**：多对多、数据量大、独立使用

权衡：嵌入读快但文档可能过大（16MB 上限）、更新会重写整个文档。

**Q4：MongoDB 事务用法？**

4.0+ 支持多文档事务：
```javascript
session.startTransaction()
try {
    // 操作
    session.commitTransaction()
} catch {
    session.abortTransaction()
}
```

性能不如 MySQL，且 4.2+ 才支持跨分片。大多数场景通过**文档设计（嵌入）**避免多文档事务。

**Q5：写入一致性 writeConcern？**

- `{w: 0}`：不等确认（fire-forget）
- `{w: 1}`：Primary 确认（默认）
- `{w: "majority"}`：多数节点确认（安全但慢）
- `{j: true}`：journal 落盘后才确认

生产建议 `w: "majority", j: true`。

**Q6：复制集和分片区别？**

- **复制集**：多副本、高可用、读扩展，数据量不增
- **分片**：水平分区、数据量扩展，本质是多个复制集

大规模部署：**分片集群**（每分片是一个复制集）。

**Q7：分片键如何选？**

- 高基数（值多）
- 低频（单值不热）
- 非单调递增（避免热点）

常见错误：用 `_id` 默认 ObjectId（时间戳递增 → 热点），应 `_id: "hashed"`。

**Q8：Aggregation 和 MapReduce 区别？**

- **Aggregation**：管线式，优化好、性能高，推荐
- **MapReduce**：灵活但慢，已不推荐

聚合能做 90% 以上的分析任务。复杂场景用 `$function` 嵌 JS。

**Q9：MongoDB 性能瓶颈？**

- **大文档**：> 1MB 性能下降
- **索引不足**：COLLSCAN 慢
- **索引过多**：写放大
- **深分页 skip**：性能随 offset 线性下降
- **写集中单分片**：分片键不当
- **内存不足**：WiredTiger cache 命中率低

**Q10：MongoDB 和 Redis 如何搭配？**

典型架构：
- **Redis**：热数据缓存、Session、计数器（毫秒级）
- **MongoDB**：持久化主库、复杂查询

流程：请求 → Redis 命中返回；未命中 → Mongo 查询 → 回填 Redis → 返回。

更新：先写 Mongo，再失效 Redis。
