# Kafka vs RocketMQ：选型、原理、实战

消息队列是后端必备组件。Java 生态主流两个选择：Kafka 和 RocketMQ。理解它们的差异、适用场景、关键机制是面试和架构决策的核心。

## 1. 为什么用 MQ

| 用途 | 例子 |
|---|---|
| **异步解耦** | 订单创建后发 MQ，下游订阅（库存、积分、推送各自消费） |
| **削峰填谷** | 秒杀场景请求入队，消费端按能力消费 |
| **流量缓冲** | 突发流量进 MQ 等下游恢复 |
| **数据流** | 日志收集 / 用户行为 / CDC binlog 入仓 |
| **事件驱动** | 微服务间通信、最终一致 |

代价：复杂度上升、延迟增加、顺序 / 幂等需处理。**业务能同步搞定别用 MQ**。

## 2. Kafka 核心架构

### 2.1 组件

- **Producer**：生产者
- **Broker**：服务节点（一个集群多个 broker）
- **Topic**：逻辑消息分类
- **Partition**：topic 的分片，是并行单元
- **Consumer / Consumer Group**：消费者组，组内分担 partition
- **ZooKeeper / KRaft**：元数据管理（Kafka 3.x+ 用 KRaft 替代 ZK）

### 2.2 消息模型

```
Topic "order"
├── Partition 0 → Broker A (Leader), Broker B (Follower), Broker C (Follower)
├── Partition 1 → Broker B (Leader), ...
└── Partition 2 → Broker C (Leader), ...
```

每个 partition 是有序的 commit log。Leader 处理读写，Follower 复制。

### 2.3 关键机制

**ISR（In-Sync Replicas）**：保持同步的副本集合。Leader 挂了从 ISR 选新 Leader。
**HW（High Watermark）**：消费者最多读到 HW 位置，保证已 commit。
**LEO（Log End Offset）**：partition 最大 offset。

```
Partition Log:
[msg 0][msg 1][msg 2]...[msg N]
              ↑           ↑
              HW          LEO
```

## 3. 生产者机制

### 3.1 ACK 语义

```java
props.put("acks", "all");   // 0/1/all
```

- `0`：发完就完，最快最不可靠
- `1`：等 leader 写完
- `all`（或 `-1`）：等 ISR 全部写完，最可靠最慢

### 3.2 重试与幂等

```java
props.put("enable.idempotence", true);   // 幂等生产者
props.put("retries", Integer.MAX_VALUE);
props.put("max.in.flight.requests.per.connection", 5);  // ≤5 才能保证有序
```

幂等生产者通过 ProducerId + Sequence Number 实现：broker 端检测重复。

### 3.3 事务

跨多个 partition 原子写：

```java
producer.initTransactions();
producer.beginTransaction();
try {
    producer.send(record1);
    producer.send(record2);
    producer.commitTransaction();
} catch (Exception e) {
    producer.abortTransaction();
}
```

支持 read-process-write 模式（消费 + 处理 + 生产原子）。

### 3.4 批量 + 压缩

```java
props.put("batch.size", 16384);      // 批量字节数
props.put("linger.ms", 10);          // 等待批量 ms
props.put("compression.type", "lz4"); // 压缩
```

## 4. 消费者机制

### 4.1 消费者组

每个 partition 在同一 group 内只被一个 consumer 消费。**partition 数决定最大并发**。

```
Topic 4 partition + 2 consumer = 每 consumer 拿 2 partition
Topic 4 partition + 4 consumer = 每 consumer 拿 1 partition
Topic 4 partition + 8 consumer = 4 consumer 工作，4 闲置
```

### 4.2 Offset 管理

```java
props.put("enable.auto.commit", false);  // 关自动提交
consumer.commitSync();                    // 手动提交
```

自动提交简单但可能丢消息（已提交但处理失败）或重复消费（处理完但未提交）。生产推荐手动提交 + 业务幂等。

### 4.3 Rebalance

consumer 加入 / 离开 / partition 变化时触发。Rebalance 期间消费暂停。**避免频繁 rebalance**：
- 心跳超时调大（`session.timeout.ms`、`heartbeat.interval.ms`）
- `max.poll.interval.ms` 大于业务处理时间
- 用 cooperative rebalancing（Kafka 2.4+）

### 4.4 消费模式

- **At-most-once**：先提交 offset 后处理（可能丢）
- **At-least-once**（默认）：处理完提交 offset（可能重）
- **Exactly-once**：事务 + 幂等 + 一次性处理

业务多数选 at-least-once + 幂等。

## 5. Kafka 性能为什么这么快

1. **顺序写盘**：append-only log，磁盘顺序写接近内存速度
2. **零拷贝**：sendfile 从 page cache 直接发 socket
3. **批量 + 压缩**：减少网络 IO
4. **Page cache**：OS 自动缓存 hot data
5. **分区并行**：水平扩展无上限
6. **Pull 模型**：消费者按自己速度拉，避免 push 流控复杂

## 6. RocketMQ 核心架构

阿里出品，Java 写的，金融场景优化。

### 6.1 组件

- **NameServer**：轻量级注册中心（无状态）
- **Broker**：消息存储与转发
- **Producer / Consumer**

### 6.2 跟 Kafka 差异

| 维度 | Kafka | RocketMQ |
|---|---|---|
| 实现语言 | Scala / Java | Java |
| 元数据 | ZK / KRaft | NameServer（轻） |
| 单 broker topic 数 | 数百（过多性能降） | 数万（CommitLog 设计） |
| 延迟消息 | 不支持（需外置） | 内置（18 级延迟） |
| 顺序消息 | partition 内有序 | 全局 / 分区有序，更强 |
| 事务消息 | 0.11+ 有（但少用） | 原生支持，文档完善 |
| 死信队列 | 需手动 | 自动转 DLQ |
| 消息回溯 | offset 回溯 | 按时间回溯 |
| 优先级 | 不支持 | 弱支持 |
| 生态 | 全球最大（Kafka Streams / Connect） | 国内为主 |

### 6.3 RocketMQ 适合

- 国内业务（中文文档好、阿里背书）
- 强事务需求（原生事务消息）
- 大量 topic（如多租户 SaaS）
- 复杂消息特性（延迟、顺序、定时）

### 6.4 Kafka 适合

- 数据 pipeline / 日志流（Kafka Connect + Streams）
- 极致吞吐（百万 QPS）
- 流式计算（配合 Flink / Spark Streaming）
- 国际化、生态多语言

## 7. 顺序消息

### 7.1 Kafka

只能保证单 partition 有序。**业务 key 哈希到同一 partition** 实现局部有序：

```java
producer.send(new ProducerRecord<>("order", orderId, payload));
// 同 orderId 总到同一 partition，自然有序
```

### 7.2 RocketMQ

```java
producer.send(msg, new MessageQueueSelector() {
    public MessageQueue select(List<MessageQueue> mqs, Message msg, Object arg) {
        int index = Math.abs(arg.hashCode()) % mqs.size();
        return mqs.get(index);
    }
}, orderId);
```

消费端用 `MessageListenerOrderly` 单线程消费同一队列。

### 7.3 顺序的代价

- 单 partition 串行，吞吐受限
- consumer 不能并行处理同 key 消息
- 失败 retry 会阻塞后续

**业务设计原则**：能不要全局有序就不要，分区级有序通常够用。

## 8. 消息丢失与重复

### 8.1 三个环节

1. **生产端丢**：网络断、broker 挂、ack 配 0 → 配 ack=all + 重试
2. **broker 丢**：未刷盘机器挂 → 配同步刷盘 + 多副本
3. **消费端丢**：处理失败但 offset 已提交 → 业务处理完才提交

### 8.2 重复消息怎么办

任何 MQ 都无法 100% 避免重复（exactly-once 是 marketing）。**业务必须幂等**：
- 唯一 ID + DB 唯一索引
- 状态机（已处理跳过）
- 分布式锁

## 9. 高吞吐调优

### 9.1 Producer

- batch.size 大、linger.ms 大
- 压缩开 lz4 / snappy
- buffer.memory 加大避免阻塞
- 多 producer 实例

### 9.2 Broker

- 多磁盘 / RAID（log.dirs 配多盘）
- num.io.threads / num.network.threads 调大
- log.segment.bytes 大（少 segment 切换）
- 单 broker 监控 partition 数（太多影响性能）

### 9.3 Consumer

- 并发数 = partition 数
- fetch.min.bytes / fetch.max.wait.ms 平衡延迟与吞吐
- max.poll.records 大批量
- 业务处理多线程（注意 offset 提交时序）

## 10. 高频面试题

**Q1：Kafka vs RocketMQ 怎么选？**
- 数据 pipeline / 国际化 / 极致吞吐 / 生态多样 → Kafka
- 业务消息 / 国内 / 事务 + 延迟 + 顺序复杂需求 → RocketMQ
- 同一团队两个都用也常见（log 用 Kafka，业务用 RocketMQ）

**Q2：Kafka 为什么快？**
顺序写盘 + 零拷贝 + page cache + 批量 + 分区并行 + Pull 模型。本质上利用 OS 而非自己实现复杂内存管理。

**Q3：怎么保证消息不丢？**
三段防护：① 生产端 ack=all + 重试 + 幂等 producer；② broker 多副本 + 同步刷盘（或 ISR 同步策略）；③ 消费端处理完再提交 offset。任一段都不能省。

**Q4：怎么保证消息只消费一次？**
exactly-once 是端到端话题，单靠 MQ 不行。需要：
- Kafka 0.11+ 事务 + 幂等 producer + 事务消费
- 业务幂等（唯一 ID + 状态检查）
- 跨系统时配本地消息表 / Outbox 模式

**Q5：消息积压怎么办？**
- 临时扩 consumer：扩到等于 partition 数（再多无效）
- 临时扩 partition：但要权衡（增 partition 可能破坏 key 顺序）
- 消费端优化：批量处理、并发处理、跳过非关键消息
- 极端：单独写消费脚本快速 drain（如 dump 到 DB 后慢慢处理）
- 业务降级：暂停部分 topic 让出资源

**Q6：partition 数量怎么定？**
公式：max_partitions = max(producer_throughput, consumer_throughput) / single_partition_throughput。
经验：小 topic 8-16，大 topic 32-64，超大 100+。不是越多越好（broker 元数据 + leader 选举开销 + 文件句柄）。

**Q7：Kafka 跟 RabbitMQ 区别？**
- Kafka：日志型，pull 模型，顺序消费，高吞吐，扩展强
- RabbitMQ：传统 MQ，push 模型，路由复杂（exchange + binding），延迟低
- 选型：大数据 / 流处理 → Kafka；复杂路由 / 任务队列 / RPC → RabbitMQ

**Q8：RocketMQ 事务消息原理？**
"半消息"机制：
1. Producer 发"半消息"到 broker（消费者不可见）
2. Producer 执行本地事务
3. 本地成功 → 发 commit 让消息可见；失败 → 发 rollback 删除半消息
4. Producer 挂了 → broker 定时回查 producer 本地事务状态
5. 长时间 unknown 的半消息进死信

保证业务 commit 才发消息。
