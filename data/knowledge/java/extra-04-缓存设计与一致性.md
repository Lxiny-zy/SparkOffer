# 缓存设计与一致性：多级缓存、缓存击穿/雪崩/穿透、一致性方案

缓存是后端性能的基石，也是 bug 的高发区。这一题面试必问，生产事故频出。

## 1. 缓存的层次

```
[CDN / 浏览器]      静态资源
   ↓
[Nginx / 网关]      响应级 / API 级
   ↓
[本地缓存]          Caffeine / Guava，进程内
   ↓
[分布式缓存]        Redis / Memcached
   ↓
[数据库]
```

每层都能扛流量。多级缓存是高并发系统标配。

## 2. 经典三大问题

### 2.1 缓存穿透

**现象**：查不存在的 key，每次都打到 DB。恶意攻击场景灾难。

**解决**：
1. **空值缓存**：DB 返空也缓存（短 TTL，如 60s）
```java
User user = redis.get(key);
if (user != null) return user;
user = db.find(id);
if (user == null) {
    redis.setex(key, 60, EMPTY_MARKER);  // 防穿透
    return null;
}
redis.setex(key, 3600, user);
return user;
```

2. **布隆过滤器（Bloom Filter）**：前置过滤不存在的 key
```java
BloomFilter<String> filter = BloomFilter.create(...);
// 启动时把所有合法 ID 加进 filter
if (!filter.mightContain(id)) {
    return null;  // 一定不存在
}
// 可能存在，继续查 cache + DB
```

3. **接口层校验**：参数合法性检查（ID 格式、长度）

### 2.2 缓存击穿

**现象**：热点 key 突然失效，瞬间大量请求同时打 DB。

**解决**：
1. **互斥锁（Mutex）**：只让一个线程去查 DB 回填缓存
```java
User user = redis.get(key);
if (user != null) return user;

String lockKey = "lock:" + key;
if (redis.setnx(lockKey, "1", 10)) {  // 抢到锁
    try {
        user = db.find(id);
        redis.setex(key, 3600, user);
        return user;
    } finally {
        redis.del(lockKey);
    }
} else {
    Thread.sleep(50);  // 等其他线程回填
    return redis.get(key);  // 再读
}
```

2. **热点 key 永不过期**：业务侧逻辑过期（值里带过期时间），到期后主动异步刷新
3. **预热**：热点 key 启动时加载，并定时主动刷新

### 2.3 缓存雪崩

**现象**：大量 key 同时失效（或 Redis 宕机），请求全打 DB。

**解决**：
1. **过期时间加随机偏移**：
```java
int ttl = baseTtl + ThreadLocalRandom.current().nextInt(300);
redis.setex(key, ttl, value);
```

2. **多级缓存**：本地 Caffeine 兜底，Redis 挂了不至于全部打 DB
3. **熔断降级**：Sentinel / Resilience4j，DB 慢时主动拒绝部分流量
4. **Redis 高可用**：主从 / 哨兵 / 集群部署
5. **缓存预热**：发布后立即预加载核心数据

## 3. 一致性方案

DB 是 source of truth，缓存是为了性能。两者同步是核心难题。

### 3.1 模式对比

#### Cache-Aside（旁路缓存，最常用）

```java
// 读
T data = cache.get(key);
if (data == null) {
    data = db.find(key);
    cache.set(key, data);
}

// 写
db.update(data);
cache.delete(key);   // 或 cache.set(key, data)
```

**经典问题**：先删缓存还是先写 DB？

| 顺序 | 风险 |
|---|---|
| 先删缓存 + 后写 DB | 删除瞬间另一线程读未命中 → 读 DB（旧值）→ 写回缓存（旧值），不一致 |
| 先写 DB + 后删缓存 | 删缓存失败 → 后续读到旧值。可加重试 / 异步 |

**推荐先写 DB 后删缓存**，配合重试机制（消息队列）。

#### Write-Through

写时同步更新 cache + DB（cache 作为门面）。一致但写慢。Memcached 不支持，Redis 也少见用。

#### Write-Back（Write-Behind）

写 cache，异步刷 DB。性能极高但有丢数据风险。Linux page cache、Redis AOF everysec 类似机制。

#### Read-Through / Refresh-Ahead

cache miss 时自动从 DB 加载（cache 库内置）；快过期时主动预刷新避免 miss。Caffeine 支持。

### 3.2 双删策略

```java
cache.delete(key);
db.update(data);
sleep(500);          // 等可能的并发读完成
cache.delete(key);   // 第二次删，清掉可能写回的旧值
```

简单但 sleep 是 hack。更靠谱用 binlog 监听（Canal）。

### 3.3 基于 Binlog（最终一致首选）

```
DB 写 → MySQL Binlog → Canal/Debezium 监听 → 发 MQ → 消费者删 cache
```

优点：业务无感知、强一致最佳逼近、可重放。
缺点：需 infra 投入，有 ms 级延迟。

## 4. 本地缓存：Caffeine

Java 性能最强的本地缓存。Google Guava 升级版。

```java
LoadingCache<String, User> cache = Caffeine.newBuilder()
    .maximumSize(10_000)
    .expireAfterWrite(10, TimeUnit.MINUTES)
    .expireAfterAccess(30, TimeUnit.MINUTES)
    .refreshAfterWrite(5, TimeUnit.MINUTES)  // 自动刷新
    .recordStats()
    .build(key -> db.find(key));

User user = cache.get(key);   // miss 时自动 load
```

特性：
- W-TinyLFU 淘汰算法（命中率高于 LRU）
- 异步刷新（不阻塞读）
- 命中率统计
- 弱引用 / 软引用支持

**使用场景**：少量热点 + 不要求强一致（如配置、字典）。

## 5. Redis 高可用

### 5.1 持久化

- **RDB**：周期 snapshot，快速恢复但可能丢部分
- **AOF**：每次写记日志（appendfsync everysec 兼顾性能与持久）
- **混合**：RDB + AOF（推荐）

### 5.2 主从复制

主写从读，从节点异步复制。命令 `replicaof <master> <port>`。

### 5.3 Sentinel（哨兵）

监控主从，主挂自动 failover。Client 通过 sentinel 拿主地址。

### 5.4 Cluster

数据分片到多节点（16384 slots，分给各 master）。每个 master 配从。横向扩展无上限。Client 直连任一节点，被告知 MOVED 后切换。

### 5.5 选型

- 单实例：< 10GB 数据，开发 / 小项目
- 主从 + Sentinel：高可用 + 读写分离，中规模
- Cluster：大数据 / 高 QPS（10w+），生产首选

## 6. Redis 性能优化

### 6.1 连接池

```java
JedisPoolConfig config = new JedisPoolConfig();
config.setMaxTotal(200);
config.setMaxIdle(50);
config.setMinIdle(10);
config.setMaxWaitMillis(2000);
JedisPool pool = new JedisPool(config, "redis-host", 6379);
```

或用 Lettuce（基于 Netty，异步，单连接 multiplex）。

### 6.2 批处理

```java
// pipeline：一次发多个命令
Pipeline p = jedis.pipelined();
for (String k : keys) p.get(k);
List<Object> results = p.syncAndReturnAll();

// 或 mget / mset
List<String> values = jedis.mget(keys);
```

### 6.3 大 key / 热 key

- **大 key**（>10KB）：序列化、网络、删除都慢，会阻塞 Redis 单线程
  - 拆分：1 个 1MB hash 拆成 10 个 100KB hash
  - 异步删除：`unlink` 代替 `del`
- **热 key**（QPS > 1w）：单分片打满
  - 本地缓存挡前面
  - 拆 key：`hot_key` 拆成 `hot_key_0..9`，访问时随机选
  - Redis Cluster 多副本

### 6.4 慢查询

```bash
config set slowlog-log-slower-than 10000   # 10ms
config set slowlog-max-len 1024
slowlog get 10
```

避免：keys *、hgetall 超大 hash、smembers 超大 set、sort/zrange 超大 zset。

## 7. 限流与防爆

Redis 常做分布式限流：

```lua
-- Lua 脚本：固定窗口
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local current = redis.call("incr", key)
if current == 1 then
    redis.call("expire", key, ARGV[2])
end
if current > limit then
    return 0
end
return 1
```

或滑动窗口 / 令牌桶（Redisson 内置）。

## 8. 分布式锁

```java
String token = UUID.randomUUID().toString();
boolean locked = jedis.set(lockKey, token, "NX", "EX", 30);
if (!locked) throw new BusinessException("获取锁失败");
try {
    doWork();
} finally {
    // 用 Lua 保证 check-and-delete 原子
    String script = "if redis.call('get', KEYS[1]) == ARGV[1] " +
                    "then return redis.call('del', KEYS[1]) else return 0 end";
    jedis.eval(script, 1, lockKey, token);
}
```

生产首选 Redisson（自动续期 + 重入 + 公平锁）。

## 9. 监控

关键指标：
- 命中率（应 > 90%）
- QPS / 平均延迟 / p99 延迟
- 内存使用 / 碎片率
- 慢查询数
- 主从延迟
- 连接数

工具：RedisInsight、Prometheus + redis_exporter。

## 10. 高频面试题

**Q1：缓存穿透 / 击穿 / 雪崩区别？**
- 穿透：查不存在的 key（恶意 / 异常）→ 空值缓存 + 布隆过滤器
- 击穿：热点 key 过期瞬间 → 互斥锁 / 永不过期
- 雪崩：大量 key 同时失效 / Redis 宕机 → 过期时间随机 + 多级缓存 + 高可用

**Q2：缓存与数据库一致性怎么保证？**
- 强一致：基本无解（除非用分布式事务，性能崩盘）
- 最终一致首选：写 DB → 删缓存 + 删失败重试 + Binlog 兜底
- 接受短暂不一致：Cache-Aside 简单可靠
- 双删策略只能缓解，不能根治

**Q3：本地缓存 vs Redis 怎么选？**
- 数据量小、命中率高、可短期不一致 → Caffeine（本地，零网络）
- 多实例需共享、数据大、强一致 → Redis
- 极致性能 → 两级（Caffeine + Redis）

**Q4：Redis 单线程为什么还快？**
- 内存操作（μs 级）
- IO 多路复用（epoll）
- 数据结构高度优化（hash 表 / 跳表 / 压缩列表）
- 单线程避免锁、上下文切换
- 6.0 后多线程做 IO（read/write 网络），命令执行仍单线程

**Q5：Redis 持久化怎么选？**
- 只缓存（丢数据可接受）→ RDB 即可
- 不能丢数据（队列 / 配置）→ AOF everysec
- 既快恢复又少丢 → 混合持久化（RDB 全量 + 增量 AOF）

**Q6：分布式锁的坑？**
1. 不要忘 finally 解锁
2. 锁要带 token，避免误解别人的锁
3. 业务执行时间可能超过锁 TTL → Redisson 看门狗自动续期
4. 主从切换可能丢锁 → Redlock（多 master 同时获锁，但争议大）
5. 死锁兜底：锁必须有 TTL

**Q7：怎么处理热 key？**
- 检测：监控访问频率，超阈值告警
- 本地缓存挡前面：进程内 Caffeine 短 TTL
- 拆 key：`hot:item:0..9` 随机访问分散到多分片
- 多副本：Redis Cluster master 多副本（risk: 一致性）
- 限流：超限直接降级 / 返默认值

**Q8：缓存淘汰策略？**
Redis 8 种：
- noeviction：满了报错（不淘汰）
- allkeys-lru / allkeys-lfu / allkeys-random：所有 key 中淘汰
- volatile-lru / lfu / random / ttl：只在有 TTL 的 key 中淘汰

生产推荐 `allkeys-lfu`（最近最不常用），命中率最高。
