# Redis

---

## 一、基本概念

- 基于内存的键值存储数据库，读写速度极快（10 万+ QPS）
- **单线程模型**：Redis 6.0 之前网络 I/O 和命令执行都是单线程；6.0+ 网络 I/O 多线程，命令执行仍单线程
- 使用 **I/O 多路复用**（epoll/kqueue）处理大量并发连接
- 常用场景：缓存、会话存储、排行榜、分布式锁、消息队列、计数器、限流

### Redis 为什么快？

1. **纯内存操作**：数据存储在内存中，读写无磁盘 I/O
2. **单线程无锁竞争**：避免了多线程上下文切换和锁竞争
3. **I/O 多路复用**：单线程高效处理大量并发网络连接
4. **高效数据结构**：专门优化的底层数据结构（SDS、ziplist、skiplist 等）
5. **通信协议简单**：RESP 协议解析快
6. **C 语言实现**：接近系统级的执行效率

### Redis 6.0 为什么引入多线程？

- 瓶颈不在 CPU 而在**网络 I/O**（读写 socket、协议解析）
- 多线程仅用于处理网络 I/O 的读写和协议解析
- 命令执行仍然是单线程（保证原子性，无需加锁）
- 需要手动开启：`io-threads 4`、`io-threads-do-reads yes`

---

## 二、数据类型与内部编码

### 2.1 五大基础类型

#### String（字符串）

| 项目 | 详情 |
|------|------|
| 内部编码 | **int**（8字节长整型）/ **embstr**（<=44字节的短字符串，SDS 与 redisObject 连续内存）/ **raw**（>44字节的字符串） |
| 最大 | 512MB |
| 常用命令 | `GET`、`SET`、`MGET`、`MSET`、`INCR`、`INCRBY`、`SETNX`、`SETEX` |
| 使用场景 | 缓存（JSON 序列化对象）、计数器（INCR）、分布式锁（SETNX）、Session 共享 |

```redis
-- 分布式锁
SET lock:order unique_value NX EX 30

-- 计数器
INCR article:1001:views

-- 分布式 ID
INCRBY order_id 1000
```

#### Hash（哈希）

| 项目 | 详情 |
|------|------|
| 内部编码 | **listpack**（Redis 7.0+，替代 ziplist）/ **hashtable** |
| 转换阈值 | 元素数量 > `hash-max-listpack-entries`(128) 或单个值 > `hash-max-listpack-value`(64字节) 时转为 hashtable |
| 常用命令 | `HGET`、`HSET`、`HMGET`、`HMSET`、`HGETALL`、`HDEL`、`HINCRBY` |
| 使用场景 | 对象存储（用户信息、商品信息）、购物车 |

```redis
-- 存储用户信息
HSET user:1001 name "张三" age 25 city "北京"
HGET user:1001 name

-- 购物车
HINCRBY cart:user1001 product:2001 1   -- 商品数量+1
HGETALL cart:user1001                   -- 获取购物车所有商品
```

#### List（列表）

| 项目 | 详情 |
|------|------|
| 内部编码 | **listpack**（Redis 7.0+）/ **quicklist**（ziplist 组成的双向链表） |
| 常用命令 | `LPUSH`、`RPUSH`、`LPOP`、`RPOP`、`LRANGE`、`LLEN`、`BLPOP`（阻塞） |
| 使用场景 | 消息队列（LPUSH + BRPOP）、最新列表（朋友圈时间线）、栈（LPUSH + LPOP） |

```redis
-- 最新消息列表
LPUSH timeline:user1001 "发布了一条动态"
LRANGE timeline:user1001 0 9   -- 获取最新 10 条

-- 简单消息队列
LPUSH queue:task "task_data"
BRPOP queue:task 0   -- 阻塞等待消息
```

#### Set（集合）

| 项目 | 详情 |
|------|------|
| 内部编码 | **intset**（全是整数且数量少）/ **hashtable** |
| 转换阈值 | 元素数量 > `set-max-intset-entries`(512) 或包含非整数时转为 hashtable |
| 常用命令 | `SADD`、`SREM`、`SMEMBERS`、`SISMEMBER`、`SINTER`、`SUNION`、`SDIFF`、`SRANDMEMBER` |
| 使用场景 | 标签系统、共同好友（交集）、去重、随机抽奖（SRANDMEMBER） |

```redis
-- 共同关注
SADD follow:user1001 "user2001" "user2002" "user2003"
SADD follow:user1002 "user2002" "user2003" "user2004"
SINTER follow:user1001 follow:user1002   -- 共同关注: user2002, user2003

-- 抽奖
SADD lottery:2024 "user1" "user2" "user3" "user4"
SRANDMEMBER lottery:2024 3   -- 随机抽取 3 名
```

#### Sorted Set（有序集合）

| 项目 | 详情 |
|------|------|
| 内部编码 | **listpack**（Redis 7.0+）/ **skiplist + hashtable** |
| 转换阈值 | 元素数量 > `zset-max-listpack-entries`(128) 或单个值 > `zset-max-listpack-value`(64字节) 时转为 skiplist |
| 常用命令 | `ZADD`、`ZREM`、`ZSCORE`、`ZRANK`、`ZRANGE`、`ZRANGEBYSCORE`、`ZINCRBY` |
| 使用场景 | 排行榜、延迟队列（score 存时间戳）、滑动窗口限流 |

```redis
-- 排行榜
ZADD leaderboard 95 "player1" 87 "player2" 99 "player3"
ZREVRANGE leaderboard 0 9 WITHSCORES   -- Top 10

-- 延迟队列
ZADD delay_queue 1680000000 "task1"     -- score 为执行时间戳
ZRANGEBYSCORE delay_queue 0 <当前时间戳>  -- 取出到期任务
```

### 2.2 三种特殊类型

#### HyperLogLog

| 项目 | 详情 |
|------|------|
| 用途 | 基数统计（统计不重复元素个数） |
| 精度 | 标准误差 0.81% |
| 内存 | 固定 12KB（无论统计多少元素） |
| 命令 | `PFADD`、`PFCOUNT`、`PFMERGE` |
| 场景 | UV（独立访客）统计、日活统计 |

```redis
-- 统计页面 UV
PFADD page:index:uv "user1" "user2" "user3" "user1"
PFCOUNT page:index:uv   -- 返回 3（去重）
```

#### Bitmap（位图）

| 项目 | 详情 |
|------|------|
| 本质 | String 类型的位操作扩展 |
| 命令 | `SETBIT`、`GETBIT`、`BITCOUNT`、`BITOP`、`BITPOS` |
| 场景 | 用户签到、在线状态、布隆过滤器（RedisBloom 模块） |

```redis
-- 用户签到（key: sign:用户ID:年月）
SETBIT sign:1001:202401 0 1    -- 1月1日签到
SETBIT sign:1001:202401 1 1    -- 1月2日签到
BITCOUNT sign:1001:202401       -- 本月签到天数

-- 日活跃用户
SETBIT active:20240101 1001 1   -- 用户1001活跃
SETBIT active:20240101 1002 1
BITCOUNT active:20240101        -- 今日活跃用户数

-- 连续 3 天活跃
BITOP AND active:3days active:20240101 active:20240102 active:20240103
BITCOUNT active:3days
```

#### GEO（地理位置）

| 项目 | 详情 |
|------|------|
| 底层 | Sorted Set（使用 GeoHash 编码作为 score） |
| 命令 | `GEOADD`、`GEODIST`、`GEOSEARCH`(6.2+)、`GEOPOS`、`GEOHASH` |
| 场景 | 附近的人/店铺、距离计算 |

```redis
-- 添加位置
GEOADD locations 116.40 39.90 "天安门"
GEOADD locations 116.48 39.92 "望京"

-- 计算距离
GEODIST locations "天安门" "望京" km

-- 搜索附近（Redis 6.2+）
GEOSEARCH locations FROMMEMBER "天安门" BYRADIUS 10 km ASC COUNT 10
```

### 2.3 Redis 5.0+ 新增类型

#### Stream

- 专门为消息队列设计的数据结构
- 支持消费者组（Consumer Group）、消息确认（ACK）、持久化
- 比 List 实现的消息队列更完善（支持消息 ID、消费确认、历史消息）

```redis
-- 发布消息
XADD stream:orders * user_id 1001 product_id 2001

-- 创建消费者组
XGROUP CREATE stream:orders group1 0

-- 消费消息
XREADGROUP GROUP group1 consumer1 COUNT 1 BLOCK 2000 STREAMS stream:orders >

-- 确认消息
XACK stream:orders group1 "1680000000000-0"
```

---

## 三、持久化机制

### 3.1 RDB（Redis Database）

#### 工作原理

```
               fork()
Redis主进程 ──────────> 子进程
    |                    |
    | 继续处理命令         | 遍历内存数据
    |                    | 写入 RDB 文件
    |                    | (dump.rdb)
    |                    |
    | <──── 替换旧 RDB 文件
```

- **触发方式**：
  - 手动：`SAVE`（阻塞主进程）、`BGSAVE`（fork 子进程后台生成）
  - 自动：配置 `save 900 1`（900秒内至少1次修改）
  - 主从复制时自动触发

- **优点**：
  - 紧凑的二进制文件，适合备份和灾难恢复
  - 恢复速度快（直接加载到内存）
  - fork 子进程不影响主进程性能（COW 写时复制）

- **缺点**：
  - 非实时持久化，可能丢失最后一次快照后的数据
  - fork 操作在数据量大时可能阻塞主进程（复制页表）
  - 不适合对数据安全性要求极高的场景

#### RDB 文件结构

```
┌──────────┬─────────┬───────────┬───────────┬─────────┐
│ REDIS    │ RDB版本  │ 数据库数据  │ EOF标记    │ 校验和   │
│ (5字节)  │ (4字节)  │ (变长)     │ (1字节)   │ (8字节)  │
└──────────┴─────────┴───────────┴───────────┴─────────┘
```

### 3.2 AOF（Append Only File）

#### 工作原理

```
命令执行 -> 追加到 AOF 缓冲区 -> 根据策略 fsync 到 AOF 文件

AOF 文件内容示例：
*3\r\n$3\r\nSET\r\n$4\r\nname\r\n$5\r\nzhang\r\n
```

- **写回策略**（`appendfsync`）：

| 策略 | 写入时机 | 安全性 | 性能 |
|------|---------|--------|------|
| **always** | 每条命令都 fsync | 最高（最多丢 1 条） | 最差 |
| **everysec** | 每秒 fsync（默认） | 较高（最多丢 1 秒） | 较好 |
| **no** | 由 OS 决定 fsync | 最低 | 最好 |

- **优点**：
  - 数据安全性高，最多丢 1 秒数据（everysec）
  - 文件是追加操作，写入性能好
  - AOF 文件可读（RESP 协议格式），可手动修复

- **缺点**：
  - 文件体积比 RDB 大
  - 恢复速度比 RDB 慢（需要重放所有命令）
  - AOF 重写期间可能有额外的 I/O 开销

#### AOF 重写（Rewrite）

```
原 AOF 文件：                    重写后：
SET name "张三"                  SET name "王五"
SET name "李四"                  （只保留最终状态）
SET name "王五"
```

- 触发条件：`auto-aof-rewrite-percentage 100`（AOF 文件大小是上次重写后的 2 倍）
- 重写过程：fork 子进程 -> 遍历内存生成新 AOF -> 合并重写期间的增量数据 -> 替换旧 AOF
- Redis 7.0 AOF 重写采用 Multi Part AOF 机制（基础 AOF + 增量 AOF）

### 3.3 RDB + AOF 混合持久化（Redis 4.0+）

```
开启：aof-use-rdb-preamble yes

AOF 重写时的文件结构：
┌──────────────────────────────────┐
│ RDB 格式数据（全量快照）           │  <- 前半部分
├──────────────────────────────────┤
│ AOF 格式数据（重写期间的增量命令）  │  <- 后半部分
└──────────────────────────────────┘

恢复时：先加载 RDB 部分（快），再重放 AOF 部分（少量命令）
```

- 兼顾 RDB 的快速恢复和 AOF 的数据安全
- **推荐生产环境使用**

### 3.4 持久化方案对比总结

| 对比项 | RDB | AOF | 混合模式 |
|-------|-----|-----|---------|
| 数据安全 | 可能丢失数分钟数据 | 最多丢 1 秒 | 最多丢 1 秒 |
| 恢复速度 | 快 | 慢 | 快 |
| 文件大小 | 小（压缩） | 大 | 中等 |
| 性能影响 | fork 时可能阻塞 | everysec 影响小 | 综合 |
| 推荐场景 | 备份/灾备 | 数据安全优先 | 生产环境推荐 |

---

## 四、内存淘汰策略

### 4.1 过期策略

Redis 采用**惰性删除 + 定期删除**两种策略配合：

| 策略 | 原理 | 优缺点 |
|------|------|--------|
| **惰性删除** | 访问 key 时检查是否过期，过期则删除 | 节省 CPU，但过期 key 不被访问时一直占内存 |
| **定期删除** | 每秒 10 次随机检查一批设了过期时间的 key | 折中方案，不保证及时清理所有过期 key |

### 4.2 八种内存淘汰策略

当 Redis 内存使用超过 `maxmemory` 时触发淘汰：

| 策略 | 范围 | 算法 | 说明 |
|------|------|------|------|
| **noeviction** | - | 不淘汰 | 写入报错 OOM（默认） |
| **allkeys-lru** | 所有 key | LRU | 淘汰最近最少使用的（**最常用**） |
| **allkeys-lfu** | 所有 key | LFU | 淘汰最不常用的（4.0+） |
| **allkeys-random** | 所有 key | 随机 | 随机淘汰 |
| **volatile-lru** | 有过期时间的 key | LRU | 淘汰最近最少使用的 |
| **volatile-lfu** | 有过期时间的 key | LFU | 淘汰最不常用的（4.0+） |
| **volatile-random** | 有过期时间的 key | 随机 | 随机淘汰 |
| **volatile-ttl** | 有过期时间的 key | TTL | 淘汰即将过期的 |

#### LRU vs LFU

| 算法 | 全称 | 原理 | 适用场景 |
|------|------|------|---------|
| **LRU** | Least Recently Used | 淘汰最近最少**访问**的 | 热点数据持续被访问 |
| **LFU** | Least Frequently Used | 淘汰访问**频率**最低的 | 访问模式不均匀，偶尔的热点不应被保留 |

> Redis 的 LRU 是**近似 LRU**，不是严格的 LRU。使用采样方式（默认采样 5 个 key，`maxmemory-samples` 配置），从中淘汰最久未访问的。

#### 选择建议

```
缓存场景（允许丢失）：
  - 通用推荐：allkeys-lru
  - 访问频率差异大：allkeys-lfu
  - 数据都设了过期时间：volatile-lru / volatile-lfu

持久化场景（不允许丢失）：
  - noeviction + 做好容量规划
```

---

## 五、缓存问题解决方案

### 5.1 缓存穿透

**定义**：查询一个**不存在的数据**，缓存和数据库都没有，每次请求都打到数据库。

```
请求 --> 缓存（未命中） --> 数据库（无数据） --> 返回空
恶意攻击大量不存在的 key --> 数据库被打崩
```

**解决方案**：

| 方案 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| **缓存空值** | 数据库查不到时，缓存空值（设短 TTL） | 简单 | 浪费内存、数据不一致 |
| **布隆过滤器** | 请求先经过布隆过滤器判断 key 是否存在 | 内存占用小 | 有误判率、不能删除 |
| **接口限流/参数校验** | 拦截非法请求 | 从根源拦截 | 不能防止所有场景 |

```
布隆过滤器方案：
请求 --> 布隆过滤器
          |
          ├── 不存在（一定不存在） --> 直接返回
          |
          └── 可能存在 --> 缓存 --> 数据库
```

### 5.2 缓存击穿

**定义**：某个**热点 key 过期**的瞬间，大量并发请求同时打到数据库。

```
热点 key 过期
  --> 10000 个并发请求同时发现缓存没有
  --> 10000 个请求同时查数据库
  --> 数据库被打崩
```

**解决方案**：

| 方案 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| **互斥锁** | 缓存未命中时加分布式锁，只让一个线程查数据库 | 一致性好 | 等待时间长 |
| **逻辑过期** | 不设实际 TTL，数据中存逻辑过期时间，过期后异步更新 | 不阻塞 | 短暂不一致 |
| **永不过期** | 热点 key 不设过期时间 | 简单 | 数据可能过时 |
| **预热** | 提前加载热点数据到缓存 | 主动 | 需要预判热点 |

```java
// 互斥锁方案
public String getData(String key) {
    String value = redis.get(key);
    if (value == null) {
        // 获取分布式锁
        if (redis.setnx("lock:" + key, "1", 10)) {
            try {
                value = db.query(key);
                redis.setex(key, 300, value);
            } finally {
                redis.del("lock:" + key);
            }
        } else {
            // 未获取到锁，短暂等待后重试
            Thread.sleep(50);
            return getData(key);
        }
    }
    return value;
}
```

### 5.3 缓存雪崩

**定义**：**大量 key 同时过期**或 **Redis 宕机**，请求全部打到数据库。

```
大量 key 同时过期
  --> 缓存命中率骤降
  --> 海量请求涌入数据库
  --> 数据库被打崩
  --> 整个系统雪崩
```

**解决方案**：

| 方案 | 针对 | 原理 |
|------|------|------|
| **过期时间加随机值** | 大量 key 同时过期 | `TTL = baseTime + random(0, 300)` |
| **Redis 集群** | Redis 宕机 | 哨兵/Cluster 保障高可用 |
| **多级缓存** | 提高整体可用性 | 本地缓存 (Caffeine) + Redis + 数据库 |
| **降级限流** | 保护数据库 | 熔断降级返回默认值/限流排队 |
| **缓存预热** | 重启后缓存为空 | 启动时提前加载热点数据 |

### 5.4 缓存与数据库双写一致性

| 方案 | 流程 | 一致性 | 备注 |
|------|------|--------|------|
| **先更新数据库，再删除缓存** | DB.update -> Cache.del | 较好 | 推荐（Cache Aside 模式） |
| **延迟双删** | Cache.del -> DB.update -> sleep -> Cache.del | 较好 | 延迟时间难估算 |
| **Canal 监听 binlog** | 订阅 binlog 异步删缓存 | 最终一致性 | 解耦，推荐 |
| **先更新缓存，再更新数据库** | Cache.set -> DB.update | 差 | 不推荐 |
| **先删除缓存，再更新数据库** | Cache.del -> DB.update | 差 | 并发问题严重 |

> 推荐方案：**先更新数据库，再删除缓存**（Cache Aside Pattern），配合**消息队列重试**保证删除成功。

---

## 六、分布式锁

### 6.1 基础实现

```redis
-- 加锁（原子操作：SET + NX + EX）
SET lock:order:1001 unique_value NX EX 30
-- NX: 只在 key 不存在时设置（互斥）
-- EX 30: 30秒自动过期（防止死锁）
-- unique_value: 唯一标识（UUID），防止误删其他线程的锁

-- 解锁（Lua 脚本保证原子性）
-- 判断是否是自己加的锁，是则删除
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
```

### 6.2 存在的问题及解决

| 问题 | 说明 | 解决方案 |
|------|------|---------|
| **锁超时** | 业务未执行完锁就过期了 | 看门狗自动续期（Redisson） |
| **不可重入** | 同一线程不能重复获取锁 | 可重入锁（Redisson 基于 Hash） |
| **非公平** | 无法保证获取锁的顺序 | 公平锁（Redisson 基于 Sorted Set） |
| **Redis 故障** | 主从切换导致锁丢失 | RedLock 算法 |
| **误删** | 删除了其他线程的锁 | Lua 脚本判断 value |

### 6.3 Redisson 分布式锁

Redisson 是 Redis 的 Java 客户端，提供了丰富的分布式锁实现：

```java
// 基本使用
RLock lock = redissonClient.getLock("lock:order:1001");
try {
    // 尝试获取锁，等待 10 秒，锁持有 30 秒
    boolean acquired = lock.tryLock(10, 30, TimeUnit.SECONDS);
    if (acquired) {
        // 执行业务逻辑
    }
} finally {
    if (lock.isHeldByCurrentThread()) {
        lock.unlock();
    }
}
```

#### 看门狗（Watchdog）机制

```
加锁成功
  |
  ├── 默认锁过期时间 30 秒
  |
  └── 启动 Watchdog 后台线程
       |
       └── 每 10 秒（锁过期时间 / 3）检查一次
            |
            ├── 线程还在 --> 续期到 30 秒
            └── 线程结束 --> 停止续期，锁自然过期
```

- 只有**不指定 leaseTime** 时才启动看门狗
- 指定了 leaseTime 时不会自动续期

#### Redisson 可重入锁原理

```redis
-- 使用 Hash 结构存储锁
-- key: lock:order
-- field: 线程唯一标识（UUID:threadId）
-- value: 重入次数

-- 加锁 Lua 脚本（简化版）
if redis.call('exists', KEYS[1]) == 0 then
    redis.call('hset', KEYS[1], ARGV[2], 1)   -- 首次加锁
    redis.call('pexpire', KEYS[1], ARGV[1])
    return nil
end
if redis.call('hexists', KEYS[1], ARGV[2]) == 1 then
    redis.call('hincrby', KEYS[1], ARGV[2], 1) -- 重入，计数+1
    redis.call('pexpire', KEYS[1], ARGV[1])
    return nil
end
return redis.call('pttl', KEYS[1])  -- 锁被其他线程持有
```

### 6.4 RedLock 算法

解决**单点 Redis 故障**导致锁丢失的问题。

```
         ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐
         │ Redis1 │  │ Redis2 │  │ Redis3 │  │ Redis4 │  │ Redis5 │
         │ 独立    │  │ 独立    │  │ 独立    │  │ 独立    │  │ 独立    │
         └────────┘  └────────┘  └────────┘  └────────┘  └────────┘
              |          |          |          |          |
              └────┬─────┴──────┬───┴──────┬───┘          |
                   |            |          |              |
              加锁成功      加锁成功    加锁成功       加锁失败

              3/5 = 多数节点加锁成功 --> 获取锁成功
```

**流程**：
1. 获取当前时间戳
2. 依次向 N 个独立 Redis 实例请求加锁（设短超时，如 5~50ms）
3. 如果**超过半数**（>= N/2 + 1）节点加锁成功，且总耗时 < 锁过期时间 --> 加锁成功
4. 加锁失败则向所有节点发送释放锁请求

**争议**：Martin Kleppmann 指出 RedLock 依赖时钟，在时钟偏移场景下不安全。Antirez 进行了回应和辩论。实际生产中，如果对正确性要求极高，应使用 ZooKeeper/etcd 实现分布式锁。

---

## 七、Redis 集群方案

### 7.1 主从复制（Replication）

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│  Master  │ --> │  Slave1  │     │  Slave2  │
│  读 + 写  │     │  只读     │     │  只读     │
└──────────┘     └──────────┘     └──────────┘
      |               ▲                ▲
      └───────────────┴────────────────┘
           异步/半同步复制
```

#### 复制过程

1. **全量复制**（首次连接 / 主从断线较久）：
   - Slave 发送 PSYNC 命令
   - Master 执行 BGSAVE 生成 RDB 文件
   - Master 将 RDB 发送给 Slave
   - Slave 加载 RDB 恢复数据
   - Master 将复制期间的增量命令发送给 Slave

2. **增量复制**（短暂断线恢复）：
   - 基于 **replication offset**（复制偏移量）和 **repl_backlog_buffer**（复制积压缓冲区）
   - Slave 发送自己的 offset，Master 只发送增量数据

#### 主从延迟

- 主从复制是**异步**的，存在数据延迟
- `min-slaves-to-write` / `min-slaves-max-lag`：控制最少同步从节点数和最大延迟
- 读从库可能读到旧数据 --> 强一致性场景读主库

### 7.2 哨兵模式（Sentinel）

```
         ┌────────────┐
         │ Sentinel 1 │
         └─────┬──────┘
               |
         ┌────────────┐     ┌────────────┐
         │ Sentinel 2 │ --- │ Sentinel 3 │
         └─────┬──────┘     └─────┬──────┘
               |                  |
     ┌─────────┴──────────────────┴──────────┐
     |                                       |
┌──────────┐     ┌──────────┐     ┌──────────┐
│  Master  │ --> │  Slave1  │     │  Slave2  │
└──────────┘     └──────────┘     └──────────┘
```

#### 核心功能

1. **监控**：定期检查 Master 和 Slave 是否正常
2. **自动故障转移**：Master 宕机后自动选举新 Master
3. **通知**：将新 Master 地址通知客户端和其他 Slave
4. **配置中心**：客户端连接 Sentinel 获取 Master 地址

#### 故障转移流程

```
1. 主观下线（SDOWN）：单个 Sentinel 认为 Master 不可达（ping 超时）
2. 客观下线（ODOWN）：quorum 个 Sentinel 都认为 Master 不可达
3. Leader 选举：Sentinel 之间通过 Raft 协议选出 Leader
4. Leader Sentinel 执行故障转移：
   a. 选择最优 Slave 作为新 Master（优先级 > 偏移量 > runid）
   b. 让新 Master 执行 slaveof no one
   c. 让其他 Slave 指向新 Master
   d. 等旧 Master 恢复后变为 Slave
```

### 7.3 Redis Cluster 集群

```
┌────────────────────────────────────────────────────────┐
│                   16384 个 Slot                         │
│  0────5460        5461────10922       10923────16383    │
│                                                        │
│  ┌──────────┐     ┌──────────┐       ┌──────────┐     │
│  │  Node A  │     │  Node B  │       │  Node C  │     │
│  │  Master  │     │  Master  │       │  Master  │     │
│  │ slot 0-  │     │ slot 5461│       │ slot 10923│    │
│  │    5460  │     │   -10922 │       │   -16383 │     │
│  └────┬─────┘     └────┬─────┘       └────┬─────┘     │
│       |                |                   |           │
│  ┌──────────┐     ┌──────────┐       ┌──────────┐     │
│  │  Node A' │     │  Node B' │       │  Node C' │     │
│  │  Slave   │     │  Slave   │       │  Slave   │     │
│  └──────────┘     └──────────┘       └──────────┘     │
└────────────────────────────────────────────────────────┘
```

#### 核心特性

- **数据分片**：16384 个 slot 分配到各节点
- **key 的 slot 计算**：`CRC16(key) % 16384`
- **HashTag**：`{user}:1001` 和 `{user}:1002` 会分配到同一 slot（花括号内容做哈希）
- 去中心化架构，节点间通过 **Gossip 协议**通信
- 每个 Master 至少一个 Slave，支持自动故障转移

#### 客户端路由

```
客户端发送命令 --> 任意节点
  |
  ├── key 在本节点 --> 直接执行
  |
  └── key 不在本节点 --> 返回 MOVED 重定向
       MOVED 5461 192.168.1.2:6379
       客户端重新向正确节点发送命令
```

- **MOVED**：永久重定向，客户端应更新 slot 映射
- **ASK**：临时重定向（slot 迁移中），不需要更新映射

#### 集群限制

- **不支持多 key 跨 slot 操作**：`MGET key1 key2`（除非同一 slot）
- 不支持跨 slot 的事务
- 不支持 SELECT 多数据库（只有 db0）
- 建议使用 HashTag 让相关 key 在同一 slot

### 7.4 三种方案对比

| 对比项 | 主从复制 | 哨兵模式 | Cluster |
|-------|---------|---------|---------|
| 数据分片 | 不支持 | 不支持 | 支持（16384 slot） |
| 自动故障转移 | 不支持 | 支持 | 支持 |
| 写能力 | 单 Master | 单 Master | 多 Master |
| 容量上限 | 单机内存 | 单机内存 | 所有节点内存之和 |
| 适用场景 | 读扩展 | 高可用 | 大容量 + 高可用 |

---

## 八、Redis 7.0 新特性

### 8.1 主要更新

| 特性 | 说明 |
|------|------|
| **Multi Part AOF** | AOF 拆分为基础文件 + 增量文件，重写更高效 |
| **Function** | 替代 EVAL/EVALSHA，服务端管理 Lua 函数 |
| **Sharded Pub/Sub** | 分片发布订阅，消息只在 slot 所在分片传播 |
| **listpack 替代 ziplist** | Hash、ZSet、List 小编码统一使用 listpack |
| **Client eviction** | 客户端连接内存超限时淘汰客户端 |
| **ACL v2** | 更细粒度的权限控制（key 和命令级别） |
| **命令新增** | `ZMPOP`、`LMPOP`、`SINTERCARD`、`CLIENT NO-EVICT` 等 |
| **性能优化** | 内存分配优化、更高效的 key 过期处理 |

### 8.2 listpack vs ziplist

| 对比 | ziplist | listpack |
|------|---------|----------|
| 连锁更新问题 | 有（一个节点变化可能导致后续节点 prevlen 字段级联更新） | 无 |
| 内存效率 | 高 | 高 |
| 使用范围 | Redis < 7.0 | Redis >= 7.0 |

### 8.3 Multi Part AOF

```
Redis < 7.0:
  appendonly.aof （单文件）

Redis >= 7.0:
  appendonlydir/
    ├── base.aof         <- RDB/AOF 基础文件
    ├── incr_1.aof       <- 增量 AOF
    ├── incr_2.aof       <- 增量 AOF
    └── manifest         <- 清单文件
```

优势：重写期间不再需要 aof_rewrite_buf，减少内存和 I/O 开销。

---

## 九、面试高频题

### Q1：Redis 为什么快？

1. 纯内存操作，无磁盘 I/O
2. 单线程，无锁竞争和上下文切换
3. I/O 多路复用（epoll），单线程高效处理大量连接
4. 高效数据结构（SDS、skiplist、ziplist/listpack、intset 等）
5. RESP 协议简单，解析快

---

### Q2：缓存穿透、击穿、雪崩的区别和解决方案？

| 问题 | 定义 | 解决方案 |
|------|------|---------|
| **穿透** | 查不存在的数据，缓存和 DB 都没有 | 布隆过滤器、缓存空值 |
| **击穿** | 热点 key 过期瞬间大量请求 | 互斥锁、逻辑过期、永不过期 |
| **雪崩** | 大量 key 同时过期或 Redis 宕机 | 随机 TTL、集群部署、多级缓存、降级限流 |

---

### Q3：Redis 的持久化方式？生产环境怎么选？

- **RDB**：定时快照，恢复快但可能丢数据
- **AOF**：命令追加，数据安全但恢复慢
- **混合模式（推荐）**：RDB + AOF 结合，兼顾恢复速度和数据安全

---

### Q4：Redis 的内存淘汰策略？

8 种策略，按范围分 allkeys 和 volatile 两类，按算法分 LRU/LFU/random/ttl。最常用 **allkeys-lru**。Redis 的 LRU 是近似 LRU（采样算法）。

---

### Q5：如何用 Redis 实现分布式锁？有什么问题？

基础：`SET key value NX EX 30` 加锁 + Lua 脚本原子性释放锁。

问题：锁超时、不可重入、Redis 故障锁丢失。

推荐方案：Redisson（看门狗续期 + 可重入锁 + RedLock）。

---

### Q6：Redis 和 MySQL 数据一致性怎么保证？

推荐：**先更新数据库，再删除缓存**（Cache Aside），配合消息队列重试删除。

进阶：Canal 监听 MySQL binlog，异步同步到 Redis。

任何方案都只能保证**最终一致性**，无法保证强一致性。

---

### Q7：Redis Cluster 的数据分片原理？

16384 个 slot 分配到各 Master 节点。key 的 slot 通过 `CRC16(key) % 16384` 计算。客户端发送命令到任意节点，如果 key 不在该节点，返回 MOVED 重定向。

---

### Q8：Redis 主从复制的原理？

首次连接进行**全量复制**（RDB + 增量命令），之后进行**增量复制**（基于 offset 和 repl_backlog_buffer）。复制是**异步**的，存在主从延迟。

---

### Q9：Redisson 看门狗机制是什么？

当不指定 leaseTime 时，Redisson 启动后台看门狗线程，每隔 10 秒（锁过期时间/3）检查锁持有者是否还在，是则自动续期到 30 秒。线程结束后看门狗停止，锁自然过期。

---

### Q10：Redis 的 String 类型最大能存多少？底层实现是什么？

最大 512MB。底层实现 SDS（Simple Dynamic String），根据长度选择编码：int（纯整数）、embstr（<=44字节，SDS 和 redisObject 一起分配）、raw（>44字节，分两次分配）。

SDS 相对 C 字符串的优势：O(1) 获取长度、二进制安全、自动扩容、预分配减少内存分配。

---

### Q11：如何实现 Redis 的延迟队列？

使用 Sorted Set，score 存延迟执行的时间戳，定时轮询取出到期任务。

```redis
-- 生产者
ZADD delay_queue <执行时间戳> <任务数据>

-- 消费者（定时轮询）
ZRANGEBYSCORE delay_queue 0 <当前时间戳> LIMIT 0 10
-- 取出后删除（Lua 脚本保证原子性）
```

进阶：可使用 Redis Stream 或 Redisson 的 RDelayedQueue。

---

### Q12：Redis 大 key 有什么危害？如何排查和处理？

**危害**：内存不均、阻塞主线程（大 key 删除耗时）、网络带宽占满、主从复制延迟。

**排查**：
```bash
# 扫描大 key
redis-cli --bigkeys

# Memory 命令（Redis 4.0+）
MEMORY USAGE key
```

**处理**：
- 拆分：大 Hash 拆成多个小 Hash
- 异步删除：`UNLINK` 替代 `DEL`（Redis 4.0+，后台线程删除）
- 压缩：序列化前压缩数据
