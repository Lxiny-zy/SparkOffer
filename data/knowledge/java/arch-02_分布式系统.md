# 分布式系统

## 一、CAP 定理与 BASE 理论

### 1.1 CAP 定理

分布式系统最多同时满足以下三个特性中的两个：

- **C（Consistency，一致性）**：所有节点在同一时刻看到的数据一致
- **A（Availability，可用性）**：每个请求都能在合理时间内得到非错误响应（不保证数据最新）
- **P（Partition Tolerance，分区容错性）**：网络分区（节点间通信中断）时系统仍能运行

```
        C（一致性）
       / \
      /   \
     /     \
   CP      CA（理论上，实际网络分区不可避免）
   /         \
  P --------- A
      AP
```

**在分布式系统中，P（分区容错性）是必须保证的**，因为网络分区是客观存在的。因此实际选择在 CP 和 AP 之间：

| 类型 | 特点 | 典型系统 | 适用场景 |
|------|------|----------|----------|
| CP | 强一致性，可能牺牲可用性 | ZooKeeper, etcd, HBase, Redis Cluster | 金融交易、配置中心 |
| AP | 高可用性，允许最终一致 | Eureka, Cassandra, DynamoDB, CouchDB | 社交、电商商品展示 |

**注意：** CAP 是针对数据粒度的选择，同一系统不同模块可以选不同策略。例如电商系统：库存扣减选 CP（强一致），商品详情选 AP（最终一致）。

### 1.2 BASE 理论

BASE 是对 CAP 中 AP 方案的补充，是大规模互联网系统的实践总结：

- **BA（Basically Available，基本可用）**：故障时允许损失部分可用性
  - 响应时间增加（平时 200ms，故障时 2s）
  - 功能降级（双11高峰时关闭退款功能）
- **S（Soft State，软状态）**：允许系统中存在中间状态，不要求实时一致
  - 订单状态在各系统间可能暂时不一致
- **E（Eventually Consistent，最终一致性）**：经过一段时间后，数据最终达到一致
  - DNS 系统就是最终一致性的典型

**最终一致性的变种：**

| 变种 | 说明 | 示例 |
|------|------|------|
| 因果一致性 | 有因果关系的操作按序可见 | 先发帖再评论，看到评论一定能看到帖子 |
| 读己之写一致性 | 自己写的数据立刻能被自己读到 | 修改昵称后立即刷新能看到新昵称 |
| 会话一致性 | 同一会话内保证读己之写 | 同一次登录会话中数据一致 |
| 单调读一致性 | 不会读到比之前更旧的数据 | 不会先看到新数据后又看到旧数据 |
| 单调写一致性 | 同一来源的写操作按序执行 | 保证同一用户的操作顺序 |

---

## 二、分布式 ID 生成

### 2.1 方案对比

| 方案 | 有序性 | 性能 | 可用性 | 缺点 |
|------|--------|------|--------|------|
| UUID | 无序 | 高（本地生成） | 高 | 无序导致 B+ 树索引分裂，占 128 位 |
| 数据库自增 | 有序 | 低 | 低（单点） | 数据库压力大，单机瓶颈 |
| 数据库号段模式 | 有序 | 高 | 较高 | 需预分配号段 |
| Redis INCR | 有序 | 高 | 中 | 依赖 Redis，持久化可能丢失 |
| 雪花算法 | 有序 | 极高（本地） | 高 | 时钟回拨问题 |
| Leaf（美团） | 有序 | 高 | 高 | 系统复杂度高 |
| Tinyid（滴滴） | 有序 | 高 | 高 | 需要额外部署 |
| UidGenerator（百度） | 有序 | 极高 | 高 | 需要数据库初始化 |

### 2.2 雪花算法（Snowflake）

```
 0 | 0000000000 0000000000 0000000000 0000000000 0 | 00000 00000 | 000000000000
 1位  |               41位时间戳                    | 10位机器ID  |  12位序列号
符号位 |          （约69年）                        | (1024台机器) | (4096/ms)

总计：64 位 = 1 + 41 + 10 + 12
```

```java
public class SnowflakeIdGenerator {
    private final long epoch = 1609459200000L; // 自定义起始时间 2021-01-01
    private final long workerIdBits = 5L;       // 机器 ID 位数
    private final long datacenterIdBits = 5L;   // 数据中心 ID 位数
    private final long sequenceBits = 12L;      // 序列号位数

    private final long maxWorkerId = ~(-1L << workerIdBits);         // 31
    private final long maxDatacenterId = ~(-1L << datacenterIdBits); // 31
    private final long sequenceMask = ~(-1L << sequenceBits);        // 4095

    private final long workerIdShift = sequenceBits;                 // 12
    private final long datacenterIdShift = sequenceBits + workerIdBits; // 17
    private final long timestampShift = sequenceBits + workerIdBits + datacenterIdBits; // 22

    private long workerId;
    private long datacenterId;
    private long sequence = 0L;
    private long lastTimestamp = -1L;

    public SnowflakeIdGenerator(long workerId, long datacenterId) {
        if (workerId > maxWorkerId || workerId < 0) {
            throw new IllegalArgumentException("Worker ID 超出范围");
        }
        if (datacenterId > maxDatacenterId || datacenterId < 0) {
            throw new IllegalArgumentException("Datacenter ID 超出范围");
        }
        this.workerId = workerId;
        this.datacenterId = datacenterId;
    }

    public synchronized long nextId() {
        long timestamp = System.currentTimeMillis();

        // 时钟回拨检测
        if (timestamp < lastTimestamp) {
            long offset = lastTimestamp - timestamp;
            if (offset <= 5) {
                // 小回拨：等待追上
                try { Thread.sleep(offset << 1); } catch (InterruptedException e) { }
                timestamp = System.currentTimeMillis();
            }
            if (timestamp < lastTimestamp) {
                throw new RuntimeException("时钟回拨，拒绝生成 ID，回拨 " + offset + "ms");
            }
        }

        if (timestamp == lastTimestamp) {
            sequence = (sequence + 1) & sequenceMask;
            if (sequence == 0) {
                timestamp = waitNextMillis(lastTimestamp); // 序列号用尽，等待下一毫秒
            }
        } else {
            sequence = 0L;
        }

        lastTimestamp = timestamp;

        return ((timestamp - epoch) << timestampShift)
             | (datacenterId << datacenterIdShift)
             | (workerId << workerIdShift)
             | sequence;
    }

    private long waitNextMillis(long lastTimestamp) {
        long timestamp = System.currentTimeMillis();
        while (timestamp <= lastTimestamp) {
            timestamp = System.currentTimeMillis();
        }
        return timestamp;
    }
}
```

**时钟回拨解决方案：**
1. **等待追上**：小回拨（< 5ms）直接等待
2. **拒绝生成**：大回拨直接报错
3. **扩展位**：预留几位做回拨计数器
4. **Leaf-Snowflake 方案**：用 ZooKeeper 分配 workerID，且定期上报时间戳做校验

### 2.3 Leaf（美团分布式 ID 方案）

提供两种模式：

**Leaf-Segment（号段模式）：**

```
数据库表:
+----------+-----------+------+----------+-----+
| biz_tag  | max_id    | step | desc     | ... |
+----------+-----------+------+----------+-----+
| order    | 20000     | 1000 | 订单ID   |     |
| user     | 50000     | 2000 | 用户ID   |     |
+----------+-----------+------+----------+-----+

流程:
1. 服务启动时从数据库获取一个号段 [max_id+1, max_id+step]
2. 内存中发号，无需每次访问数据库
3. 号段用完前（如 10% 剩余时），异步加载下一个号段（双 Buffer）
4. 避免数据库成为瓶颈
```

**Leaf-Snowflake（雪花模式）：**
- 使用 ZooKeeper 持久顺序节点自动分配 workerID
- 每次启动校验本机时间是否合法（防时钟回拨）
- 定期上报自身时间戳到 ZooKeeper

---

## 三、分布式事务

### 3.1 2PC（两阶段提交）

```
          协调者 (Coordinator)
         /       |       \
        v        v        v
    参与者A   参与者B   参与者C

阶段一 - Prepare（投票）:
  协调者 -> 所有参与者: "能否提交?"
  参与者执行事务但不提交，记录 undo/redo 日志
  参与者 -> 协调者: "YES" 或 "NO"

阶段二 - Commit/Rollback（执行）:
  如果所有参与者都 YES:
    协调者 -> 所有参与者: "COMMIT"
  如果有任一参与者 NO 或超时:
    协调者 -> 所有参与者: "ROLLBACK"
```

**缺点：**
- **同步阻塞**：Prepare 阶段所有参与者锁定资源，阻塞等待
- **单点故障**：协调者宕机，参与者一直阻塞
- **数据不一致**：Commit 阶段部分参与者收到 Commit，部分没收到

### 3.2 3PC（三阶段提交）

在 2PC 基础上增加 CanCommit 阶段和超时机制：

```
阶段一 - CanCommit（询问）:
  协调者 -> 参与者: "你能参与事务吗?"（不锁资源）
  参与者: "YES/NO"

阶段二 - PreCommit（预提交）:
  协调者 -> 参与者: "执行事务，但先不提交"
  参与者执行事务，记录日志，锁定资源

阶段三 - DoCommit（正式提交）:
  协调者 -> 参与者: "COMMIT" 或 "ROLLBACK"
  参与者超时未收到指令 -> 默认提交（降低阻塞概率）
```

**改进：** 减少阻塞范围，参与者有超时自动提交/回滚能力。
**缺点：** 仍不能完全解决数据不一致，复杂度更高。

### 3.3 TCC（Try-Confirm-Cancel）

```
TCC 三阶段:
  Try     - 检查并预留资源（冻结库存、冻结余额）
  Confirm - 确认执行（扣减冻结的库存和余额）
  Cancel  - 取消释放（解冻库存和余额）

示例 - 下单扣库存 + 扣余额:
  Try:
    库存服务: 冻结 1 件库存 (available: 10->9, frozen: 0->1)
    账户服务: 冻结 100 元 (available: 500->400, frozen: 0->100)

  Confirm (Try 全部成功):
    库存服务: frozen: 1->0 (已发货)
    账户服务: frozen: 100->0 (已扣款)

  Cancel (Try 任一失败):
    库存服务: available: 9->10, frozen: 1->0 (解冻)
    账户服务: available: 400->500, frozen: 100->0 (解冻)
```

```java
// TCC 接口定义
public interface InventoryTccService {

    @TwoPhaseBusinessAction(name = "inventoryTcc",
        commitMethod = "confirm", rollbackMethod = "cancel")
    boolean tryDeduct(BusinessActionContext context,
                      @BusinessActionContextParameter(paramName = "productId") Long productId,
                      @BusinessActionContextParameter(paramName = "count") int count);

    boolean confirm(BusinessActionContext context);

    boolean cancel(BusinessActionContext context);
}

// Try 实现
public boolean tryDeduct(BusinessActionContext context, Long productId, int count) {
    // 冻结库存
    int rows = inventoryMapper.freeze(productId, count);
    return rows > 0;
}

// Confirm 实现
public boolean confirm(BusinessActionContext context) {
    Long productId = (Long) context.getActionContext("productId");
    int count = (int) context.getActionContext("count");
    // 扣减冻结库存
    inventoryMapper.confirmDeduct(productId, count);
    return true;
}

// Cancel 实现
public boolean cancel(BusinessActionContext context) {
    Long productId = (Long) context.getActionContext("productId");
    int count = (int) context.getActionContext("count");
    // 解冻库存
    inventoryMapper.unfreeze(productId, count);
    return true;
}
```

**优点：** 灵活，性能好（锁粒度由业务控制）
**缺点：** 代码侵入大，需实现 3 个方法；需考虑幂等、空回滚、悬挂问题

**TCC 三大坑：**
- **幂等**：Confirm/Cancel 可能被重复调用，必须幂等
- **空回滚**：Try 未执行（超时），Cancel 被调用，需判断是否需要回滚
- **悬挂**：Cancel 先于 Try 执行（网络延迟），Try 后续执行会导致数据不一致

### 3.4 Saga 模式

```
Saga 将长事务拆分为一系列本地事务:
  T1 -> T2 -> T3 -> ... -> Tn

每个 Ti 有对应的补偿操作 Ci:
  成功: T1 -> T2 -> T3 -> Done
  失败: T1 -> T2 -> T3(fail) -> C2 -> C1 (反向补偿)

示例 - 订单流程:
  T1: 创建订单
  T2: 扣减库存
  T3: 扣减余额
  T4: 发送通知

  如果 T3 失败:
  C2: 恢复库存
  C1: 取消订单
```

**两种执行策略：**

| 策略 | 说明 | 优点 | 缺点 |
|------|------|------|------|
| 编排（Choreography） | 各服务监听事件自行处理 | 解耦，无中心 | 流程不直观，难调试 |
| 协调（Orchestration） | 中央协调器统一编排 | 流程清晰 | 协调器单点 |

### 3.5 本地消息表

```
              Service A                          MQ                    Service B
                 |                                |                        |
  1. BEGIN       |                                |                        |
  2. 写业务数据   |                                |                        |
  3. 写消息到    |                                |                        |
     本地消息表  |                                |                        |
  4. COMMIT     |                                |                        |
                |                                |                        |
  5. 定时扫描   |                                |                        |
     消息表     |                                |                        |
  6. 发送消息 --|-----> 消息队列 ------发送------->|                        |
                |                                |  7. 消费消息             |
                |                                |  8. 执行本地事务          |
                |                                |  9. ACK 确认             |
                |                                |                        |
  10. 更新消息  |<-------- 消费确认 --------------|                        |
      状态为    |                                |                        |
      已发送    |                                |                        |
```

```sql
-- 本地消息表
CREATE TABLE local_message (
    id          BIGINT PRIMARY KEY AUTO_INCREMENT,
    biz_id      VARCHAR(64) NOT NULL COMMENT '业务ID',
    topic       VARCHAR(128) NOT NULL COMMENT '消息主题',
    body        TEXT NOT NULL COMMENT '消息内容',
    status      TINYINT DEFAULT 0 COMMENT '0-待发送 1-已发送 2-失败',
    retry_count INT DEFAULT 0 COMMENT '重试次数',
    create_time DATETIME NOT NULL,
    update_time DATETIME NOT NULL,
    UNIQUE KEY uk_biz_id (biz_id)
);
```

### 3.6 最大努力通知

- 适用于对一致性要求不高的场景（如支付结果通知）
- 发送方最大努力通知接收方，有重试机制（间隔递增：1s, 5s, 30s, 5min ...）
- 接收方需支持幂等，主动提供查询接口做兜底
- 不保证一定通知到，但尽最大努力

### 3.7 分布式事务方案对比

| 方案 | 一致性 | 性能 | 复杂度 | 适用场景 |
|------|--------|------|--------|----------|
| 2PC | 强一致 | 低（阻塞） | 中 | 数据库层面 |
| 3PC | 强一致 | 低 | 高 | 理论方案，少用 |
| TCC | 最终一致 | 高 | 高（3 个方法） | 资金、库存等高一致场景 |
| Saga | 最终一致 | 高 | 中 | 长事务、跨服务编排 |
| 本地消息表 | 最终一致 | 高 | 低 | 异步场景、消息驱动 |
| 最大努力通知 | 弱一致 | 高 | 低 | 通知类（支付回调） |
| Seata AT | 最终一致 | 中 | 低（自动补偿） | 通用微服务事务 |

---

## 四、分布式锁

### 4.1 Redis 分布式锁

```java
// 基本实现：SET key value NX PX timeout
public boolean tryLock(String key, String value, long timeoutMs) {
    Boolean result = redisTemplate.opsForValue()
        .setIfAbsent(key, value, timeoutMs, TimeUnit.MILLISECONDS);
    return Boolean.TRUE.equals(result);
}

// 释放锁：必须用 Lua 脚本保证原子性（先验证再删除）
public boolean unlock(String key, String value) {
    String lua = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        else
            return 0
        end
    """;
    Long result = redisTemplate.execute(
        new DefaultRedisScript<>(lua, Long.class),
        List.of(key), value
    );
    return Long.valueOf(1L).equals(result);
}
```

**Redis 锁的问题与解决：**

| 问题 | 解决方案 |
|------|----------|
| 锁超时但业务未执行完 | Redisson 的看门狗（WatchDog）机制自动续期 |
| 主从切换导致锁丢失 | RedLock 算法（多数节点加锁成功才算成功） |
| 非锁持有者释放锁 | value 使用唯一标识（UUID），释放前验证 |
| 不可重入 | Redisson 用 Hash 结构记录重入次数 |

```java
// Redisson 分布式锁（推荐生产使用）
RLock lock = redissonClient.getLock("order:lock:" + orderId);
try {
    // 尝试加锁，等待 10 秒，锁超时 30 秒（自动续期）
    if (lock.tryLock(10, 30, TimeUnit.SECONDS)) {
        try {
            // 业务逻辑
            doBusinessLogic();
        } finally {
            lock.unlock();
        }
    }
} catch (InterruptedException e) {
    Thread.currentThread().interrupt();
}
```

### 4.2 ZooKeeper 分布式锁

```
基于临时顺序节点:

/locks/order-lock
  ├── 000000001  (Client A)  <-- 最小节点，获得锁
  ├── 000000002  (Client B)  <-- 监听 000000001
  └── 000000003  (Client C)  <-- 监听 000000002

流程:
1. 在 /locks/order-lock 下创建临时顺序节点
2. 获取所有子节点，判断自己是否是最小的
3. 如果是最小节点 -> 获得锁
4. 如果不是 -> 监听比自己小的前一个节点（避免惊群）
5. 前一个节点被删除 -> 再次判断自己是否最小
6. 释放锁：删除自己的节点（或客户端断开，临时节点自动删除）
```

```java
// Curator 框架实现（推荐）
InterProcessMutex lock = new InterProcessMutex(curatorClient, "/locks/order-lock");
try {
    if (lock.acquire(10, TimeUnit.SECONDS)) {
        try {
            doBusinessLogic();
        } finally {
            lock.release();
        }
    }
} catch (Exception e) {
    // 处理异常
}
```

### 4.3 MySQL 分布式锁

```sql
-- 方案一：悲观锁（SELECT ... FOR UPDATE）
BEGIN;
SELECT * FROM resource_lock WHERE resource_id = 'order_123' FOR UPDATE;
-- 执行业务逻辑
COMMIT;

-- 方案二：唯一索引插入
INSERT INTO distributed_lock (lock_key, lock_value, expire_at)
VALUES ('order_123', 'uuid-xxx', NOW() + INTERVAL 30 SECOND);
-- 成功则获取锁，失败（唯一索引冲突）则未获取
-- 释放：DELETE FROM distributed_lock WHERE lock_key = 'order_123' AND lock_value = 'uuid-xxx';
```

### 4.4 分布式锁方案对比

| 维度 | Redis | ZooKeeper | MySQL |
|------|-------|-----------|-------|
| 性能 | 高 | 中 | 低 |
| 可靠性 | 中（主从切换可能丢锁） | 高（CP 模型） | 中 |
| 实现复杂度 | 低（Redisson） | 中（Curator） | 低 |
| 公平性 | 不公平（抢占） | 公平（顺序节点） | 不公平 |
| 可重入 | 支持（Redisson） | 支持（Curator） | 需自行实现 |
| 锁释放 | 需主动释放 + 超时 | 临时节点自动释放 | 需主动释放 |
| 适用场景 | 高并发、性能优先 | 高可靠性要求 | 并发不高、已有 MySQL |

---

## 五、分布式一致性算法

### 5.1 Raft 算法

Raft 将一致性问题分解为三个子问题：

**1. 领导者选举（Leader Election）**

```
节点三种状态:
  Follower  -> (选举超时) -> Candidate -> (获得多数票) -> Leader
  Leader    -> (发现更高任期) -> Follower
  Candidate -> (发现 Leader / 更高任期) -> Follower

选举流程:
  1. Follower 在选举超时时间内未收到 Leader 心跳
  2. 转变为 Candidate，任期 (term) +1，投票给自己
  3. 向其他节点发送 RequestVote RPC
  4. 获得多数票 -> 成为 Leader
  5. 未获得多数票 -> 随机等待后重新发起选举
```

**2. 日志复制（Log Replication）**

```
  Client -> Leader: "SET x = 5"
  Leader:
    1. 将命令追加到本地日志
    2. 并行发送 AppendEntries RPC 给所有 Follower
    3. 收到多数节点确认 -> 提交日志 -> 应用到状态机 -> 响应客户端
    4. 下一次心跳通知 Follower 提交
```

**3. 安全性（Safety）**
- 选举限制：只有日志至少和大多数节点一样新的 Candidate 才能当选
- 提交限制：Leader 只提交当前任期的日志（通过提交当前任期日志间接提交之前任期的日志）

### 5.2 Paxos 算法

**Basic Paxos 角色：**
- **Proposer（提议者）**：提出提案
- **Acceptor（接受者）**：对提案投票
- **Learner（学习者）**：学习已达成共识的值

```
两阶段流程:

Phase 1 - Prepare:
  Proposer                    Acceptor
     |--- Prepare(n) --------->|  (n 是提案编号，全局递增)
     |<-- Promise(n, v_prev) --|  (承诺不再接受编号 < n 的提案)
                                  (如果之前接受过提案，返回最高编号的已接受值)

Phase 2 - Accept:
  Proposer                    Acceptor
     |--- Accept(n, v) ------->|  (v 为 Phase 1 中获知的最高编号已接受值，
     |                         |   或自己的值（如果没有已接受值）)
     |<-- Accepted(n, v) ------|  (接受提案)

共识达成: 多数 Acceptor 接受了同一提案
```

**Multi-Paxos 优化：**
- 选出一个稳定的 Leader（Distinguished Proposer）
- Leader 直接跳过 Prepare 阶段，只执行 Accept
- 减少消息轮次，提高性能
- Raft 可以视为 Multi-Paxos 的一种简化实现

### 5.3 Raft vs Paxos

| 维度 | Raft | Paxos |
|------|------|-------|
| 可理解性 | 高（设计目标就是易懂） | 低（论文晦涩难懂） |
| Leader | 强 Leader 模型 | 可无 Leader（Basic Paxos） |
| 日志连续性 | 要求日志连续 | 允许日志空洞 |
| 实现难度 | 中 | 高 |
| 工程实现 | etcd, Consul, TiKV | Chubby (Google), OceanBase |

---

## 六、限流算法

### 6.1 固定窗口计数器

```
|<------- 窗口1 ------->|<------- 窗口2 ------->|
|  请求: |||||||         |  请求: |||             |
|  计数: 7 (限制10)      |  计数: 3 (限制10)      |
|  状态: 允许            |  状态: 允许            |

临界问题:
|         ...|||||||||||  |  ||||||||||...         |
|         窗口1 末尾 10   |  窗口2 开头 10         |
|         一瞬间通过 20 个请求！超出限制            |
```

### 6.2 滑动窗口计数器

```
将窗口细分为多个小窗口，滑动统计:

|--格1--|--格2--|--格3--|--格4--|--格5--|--格6--|
      |<---------- 当前统计窗口 ----------->|
               随时间推移向右滑动

格子越多，精度越高，越接近准确限流
Redis 实现: 用 ZSet，score 为时间戳，统计窗口内的元素数量
```

```java
// Redis 滑动窗口限流
public boolean isAllowed(String key, int maxCount, long windowMs) {
    long now = System.currentTimeMillis();
    long windowStart = now - windowMs;

    String lua = """
        -- 移除窗口外的元素
        redis.call('zremrangebyscore', KEYS[1], 0, ARGV[1])
        -- 统计窗口内的元素数量
        local count = redis.call('zcard', KEYS[1])
        if count < tonumber(ARGV[2]) then
            -- 未超限，添加当前请求
            redis.call('zadd', KEYS[1], ARGV[3], ARGV[4])
            redis.call('pexpire', KEYS[1], ARGV[5])
            return 1
        else
            return 0
        end
    """;
    Long result = redisTemplate.execute(
        new DefaultRedisScript<>(lua, Long.class),
        List.of(key),
        String.valueOf(windowStart),
        String.valueOf(maxCount),
        String.valueOf(now),
        UUID.randomUUID().toString(),
        String.valueOf(windowMs)
    );
    return Long.valueOf(1L).equals(result);
}
```

### 6.3 漏桶算法（Leaky Bucket）

```
       请求流入 (速率不定)
          |  |  |||  |
          v  v  vvv  v
       +--------------+
       |              |  <- 桶（有固定容量）
       |   ////////   |
       |   ////////   |     溢出 -> 拒绝
       |   ////////   |
       +----|-------- +
            |
            v              恒定速率流出
         处理请求
```

特点：**恒定速率**处理请求，超出桶容量的请求被丢弃。无论请求多快涌入，处理速度始终恒定。不允许突发流量。

### 6.4 令牌桶算法（Token Bucket）

```
       令牌以恒定速率生成
            |
            v
       +--------------+
       |  o o o o o o  |  <- 令牌桶（有最大容量）
       |  o o o o      |
       +----|-------- +
            |
            v
       请求到来 -> 取令牌
                   有令牌 -> 处理
                   无令牌 -> 拒绝/等待
```

特点：允许一定的**突发流量**（桶中积累的令牌可以一次性使用），平均速率恒定。

```java
// Guava RateLimiter（令牌桶实现）
RateLimiter rateLimiter = RateLimiter.create(100); // 每秒 100 个令牌

// 阻塞等待
rateLimiter.acquire(); // 获取一个令牌，无令牌则等待

// 非阻塞
if (rateLimiter.tryAcquire()) {
    // 获取到令牌
} else {
    // 限流
}

// 带超时
if (rateLimiter.tryAcquire(500, TimeUnit.MILLISECONDS)) {
    // 在 500ms 内获取到令牌
}
```

### 6.5 限流算法对比

| 算法 | 突发流量 | 精度 | 实现复杂度 | 适用场景 |
|------|----------|------|------------|----------|
| 固定窗口 | 有临界突发 | 低 | 低 | 简单计数场景 |
| 滑动窗口 | 较好控制 | 高 | 中 | API 限流 |
| 漏桶 | 不允许 | 高 | 中 | 需要严格恒速的场景 |
| 令牌桶 | 允许一定突发 | 高 | 中 | 通用场景（最常用） |

---

## 七、负载均衡策略

### 7.1 常见算法

| 算法 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| 轮询（Round Robin） | 依次分配 | 简单公平 | 忽略服务器性能差异 |
| 加权轮询 | 按权重分配 | 考虑服务器性能差异 | 权重静态配置 |
| 随机（Random） | 随机选择 | 实现简单 | 不均匀 |
| 加权随机 | 按权重随机 | 简单且考虑权重 | 短期不均匀 |
| 最少连接（Least Connection） | 分配给连接数最少的 | 动态均衡 | 需维护连接计数 |
| 源地址哈希（IP Hash） | 按客户端 IP 哈希 | 会话保持 | 服务器增减影响大 |
| 一致性哈希 | 哈希环 | 节点变更影响小 | 实现稍复杂 |

### 7.2 一致性哈希

```
                   Hash 环 (0 ~ 2^32-1)
                        0
                       /|\
                      / | \
                   /    |    \
                 A      |      B     <- 服务器节点
                /       |       \
               /        |        \
              /         |         \
           --+----------+----------+--
                        |
                        C            <- 服务器节点

请求 key 经过哈希后落在环上，顺时针找到第一个服务器节点处理

虚拟节点: 为解决数据倾斜，每个真实节点映射多个虚拟节点到环上
  Node A -> A#1, A#2, A#3, ...
  Node B -> B#1, B#2, B#3, ...
```

```java
public class ConsistentHash<T> {
    private final TreeMap<Long, T> ring = new TreeMap<>();
    private final int virtualNodes; // 每个真实节点的虚拟节点数

    public ConsistentHash(int virtualNodes, Collection<T> nodes) {
        this.virtualNodes = virtualNodes;
        for (T node : nodes) {
            addNode(node);
        }
    }

    public void addNode(T node) {
        for (int i = 0; i < virtualNodes; i++) {
            long hash = hash(node.toString() + "#" + i);
            ring.put(hash, node);
        }
    }

    public void removeNode(T node) {
        for (int i = 0; i < virtualNodes; i++) {
            long hash = hash(node.toString() + "#" + i);
            ring.remove(hash);
        }
    }

    public T getNode(String key) {
        long hash = hash(key);
        // 顺时针查找第一个节点
        Map.Entry<Long, T> entry = ring.ceilingEntry(hash);
        if (entry == null) {
            entry = ring.firstEntry(); // 绕回环的起点
        }
        return entry.getValue();
    }

    private long hash(String key) {
        // 使用 MurmurHash 或 FNV 等哈希算法
        return Hashing.murmur3_128().hashString(key, StandardCharsets.UTF_8).asLong();
    }
}
```

---

## 八、服务降级与熔断

### 8.1 服务降级

当系统压力过大或非核心服务不可用时，暂时关闭一些非核心功能，保证核心功能正常：

```
降级策略:
  - 延迟降级：非核心请求排队等待，超时返回默认值
  - 限流降级：超出流量直接拒绝或返回缓存数据
  - 功能降级：关闭非核心功能（推荐、评论）
  - 读降级：直接读缓存，不查数据库
  - 写降级：先写消息队列，异步落库

示例:
  正常: 商品详情页展示 [基本信息 + 评论 + 推荐 + 物流]
  降级: 商品详情页展示 [基本信息 + 缓存评论]，关闭推荐和物流查询
```

### 8.2 熔断模式（Circuit Breaker）

```
状态机:

         失败率超阈值
  CLOSED ---------> OPEN
    ^                 |
    |     冷却时间到   |
    |                 v
    +--- HALF_OPEN <--+
         (放行少量请求测试)
         成功 -> CLOSED
         失败 -> OPEN

CLOSED (关闭): 正常处理请求，统计失败率
OPEN   (打开): 直接拒绝请求，返回降级响应（快速失败）
HALF_OPEN (半开): 放行少量请求探测，成功则恢复，失败则继续熔断
```

```java
// Resilience4j 熔断示例
CircuitBreakerConfig config = CircuitBreakerConfig.custom()
    .failureRateThreshold(50)        // 失败率 50% 触发熔断
    .waitDurationInOpenState(Duration.ofSeconds(30)) // 熔断 30 秒
    .slidingWindowSize(10)           // 统计窗口 10 个请求
    .permittedNumberOfCallsInHalfOpenState(3) // 半开状态放行 3 个请求
    .build();

CircuitBreaker circuitBreaker = CircuitBreaker.of("inventoryService", config);

Supplier<Inventory> decorated = CircuitBreaker.decorateSupplier(
    circuitBreaker,
    () -> inventoryService.getStock(productId)
);

Try<Inventory> result = Try.ofSupplier(decorated)
    .recover(CallNotPermittedException.class,
             e -> new Inventory(productId, 0, "熔断降级，库存暂不可用"));
```

### 8.3 Sentinel（阿里巴巴限流熔断框架）

```java
// Sentinel 注解方式
@SentinelResource(
    value = "getUser",
    blockHandler = "getUserBlockHandler",
    fallback = "getUserFallback"
)
public User getUser(Long id) {
    return userMapper.selectById(id);
}

// 限流/降级兜底
public User getUserBlockHandler(Long id, BlockException e) {
    return new User(id, "系统繁忙，请稍后重试");
}

// 异常兜底
public User getUserFallback(Long id, Throwable e) {
    return new User(id, "默认用户");
}
```

---

## 九、面试高频题

### Q1: CAP 定理是什么？为什么不能同时满足三个？举例说明。

**答：** CAP 指一致性（C）、可用性（A）、分区容错性（P）。分布式系统网络分区不可避免（P 必选），当分区发生时：要保证一致性（C）就需要等节点同步，可能导致部分请求超时（牺牲 A）；要保证可用性（A）就需要立即响应，可能返回旧数据（牺牲 C）。例如 ZooKeeper 是 CP（选举期间不可用），Eureka 是 AP（节点间数据可能暂时不一致）。

### Q2: 分布式事务有哪些方案？怎么选？

**答：** (1) 2PC -- 强一致但性能差，适合数据库层面；(2) TCC -- 最终一致，高性能但代码侵入大，适合资金/库存；(3) Saga -- 最终一致，适合长事务/跨服务编排；(4) 本地消息表 -- 最终一致，适合消息驱动场景；(5) 最大努力通知 -- 弱一致，适合通知类。选型原则：强一致场景用 TCC，异步场景用本地消息表，长流程用 Saga，简单场景用 Seata AT。

### Q3: 雪花算法的原理？时钟回拨怎么解决？

**答：** 雪花算法生成 64 位 ID：1 位符号位 + 41 位时间戳（约 69 年） + 10 位机器 ID（1024 台） + 12 位序列号（每毫秒 4096 个）。时钟回拨解决：(1) 小回拨（< 5ms）等待追上；(2) 大回拨拒绝生成并报警；(3) 扩展位方案，用几个 bit 做回拨计数器；(4) 美团 Leaf-Snowflake 用 ZooKeeper 上报时间做校验。

### Q4: Redis 分布式锁如何实现？有什么问题？

**答：** 用 `SET key value NX PX timeout` 加锁，用 Lua 脚本（验证 value 后 DEL）释放。问题：(1) 锁超时但业务未完成 -- Redisson 看门狗自动续期；(2) Redis 主从切换丢锁 -- RedLock 向多数节点加锁；(3) 非持有者释放 -- value 用 UUID，释放前验证；(4) 不可重入 -- Redisson 用 Hash 记录重入次数。

### Q5: Raft 算法的选举流程？

**答：** (1) Follower 超时未收到 Leader 心跳，转为 Candidate，任期 +1，投自己一票；(2) 向其他节点发 RequestVote；(3) 获得多数票成为 Leader；(4) 未获得多数票（票数分散或发现更高任期），随机等待后重试。安全性保证：一个任期内每个节点只投一票，保证最多选出一个 Leader；日志完整性约束保证数据安全。

### Q6: 令牌桶和漏桶的区别？

**答：** 漏桶以恒定速率处理请求，不允许突发流量，适合需要严格恒速的场景。令牌桶以恒定速率产生令牌，请求取令牌才能处理，桶中令牌可累积，因此允许一定的突发流量（一次性消耗积累的令牌）。实际中令牌桶更常用（如 Guava RateLimiter），因为它既能控制平均速率，又能容忍短时突发。

### Q7: 一致性哈希的原理？如何解决数据倾斜？

**答：** 将哈希空间组织成环（0 ~ 2^32-1），服务器节点映射到环上，请求 key 哈希后顺时针找到第一个节点处理。优点：节点增减只影响相邻节点的数据。数据倾斜问题：节点分布不均匀。解决方案：引入虚拟节点，每个真实节点映射 100-200 个虚拟节点到环上，使分布更均匀。

### Q8: 服务熔断的状态机？和降级的区别？

**答：** 熔断三个状态：CLOSED（正常，统计失败率）-> OPEN（失败率超阈值，直接拒绝）-> HALF_OPEN（冷却后，放少量请求探测，成功恢复 CLOSED，失败回到 OPEN）。区别：熔断是自动触发的保护机制（下游异常时自动切断），降级是主动决策（系统压力大时手动/策略性关闭非核心功能）。二者常配合使用：熔断触发后走降级逻辑返回默认值。

### Q9: 分布式锁用 Redis 还是 ZooKeeper？怎么选？

**答：** Redis 锁性能高，适合高并发场景，但主从切换可能丢锁（AP 模型）。ZooKeeper 锁可靠性高，临时节点保证客户端断开自动释放（CP 模型），且天然公平（顺序节点），但性能较低。选型：对性能要求高、可接受极小概率丢锁用 Redis（Redisson）；对可靠性要求极高（如金融场景）用 ZooKeeper（Curator）。大多数互联网场景用 Redis 锁足够。

### Q10: 什么是 Saga 模式？和 TCC 的区别？

**答：** Saga 将长事务拆为一系列本地事务（T1, T2, ... Tn），每个有对应补偿操作（C1, C2, ...），失败时反向补偿。与 TCC 区别：(1) TCC 有 Try 阶段预留资源，Saga 直接执行；(2) TCC 隔离性更好（Try 阶段冻结资源），Saga 隔离性差（T1 执行后其他事务可见中间状态）；(3) TCC 需要写 3 个方法（Try/Confirm/Cancel），Saga 只需写正向 + 补偿方法；(4) Saga 适合长流程（跨多个服务），TCC 适合短事务高一致场景。

### Q11: 如何设计一个高可用的分布式系统？

**答：** 核心策略：(1) 冗余 -- 多副本、多机房部署，消除单点；(2) 负载均衡 -- 流量分散到多台机器；(3) 限流熔断 -- 防止雪崩，快速失败；(4) 降级 -- 牺牲非核心保核心；(5) 异步解耦 -- 消息队列削峰、解耦；(6) 数据分片 -- 分库分表、分区；(7) 监控告警 -- 全链路监控、自动扩缩容；(8) 灰度发布 -- 小流量验证再全量；(9) 超时重试 -- 合理的超时时间和重试策略。
