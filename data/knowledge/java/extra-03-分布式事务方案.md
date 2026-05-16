# 分布式事务方案：2PC、TCC、Saga、本地消息表、Seata

单库事务靠数据库 ACID 解决，跨服务跨库需要分布式事务。这一题面试必考、生产必踩坑。

## 1. 为什么分布式事务难

CAP 定理：一致性（C）、可用性（A）、分区容错性（P）三选二。分布式系统 P 必选，只能 CP 或 AP。

**分布式事务的目标**：在跨服务调用中保证最终一致或强一致。代价是性能损失、复杂度上升。

## 2. 一致性级别

| 级别 | 含义 | 例子 |
|---|---|---|
| **强一致** | 写完立即可读，全节点同步 | 银行核心账务 |
| **顺序一致** | 操作顺序在所有节点一致 | etcd / ZooKeeper |
| **最终一致** | 异步收敛 | 电商订单 / 物流状态 |
| **会话一致** | 同一会话内可见 | 用户读自己写的 |

业务大多数能接受最终一致，少数强需求才用强一致方案。

## 3. 2PC（两阶段提交）

### 3.1 协议

```
[Prepare 阶段]
TM → 所有 RM：能不能提交？
RM → TM：可以 / 不行

[Commit 阶段]
所有 RM 都回复"可以" → TM → 所有 RM：commit
任一 RM 回复"不行" → TM → 所有 RM：rollback
```

TM = Transaction Manager，RM = Resource Manager（如各数据库）。

### 3.2 痛点

- **同步阻塞**：Prepare 后 RM 锁资源等 Commit
- **TM 单点**：TM 挂掉 RM 永久阻塞
- **网络问题**：Commit 阶段部分 RM 收到部分没收到，数据不一致
- **性能差**：每次跨服务多两次 round-trip

### 3.3 XA

XA 是 2PC 的工业实现标准。MySQL InnoDB / Oracle / DB2 都支持 XA。

```java
// JTA + Atomikos / Narayana
@Transactional("jtaTransactionManager")
public void transfer(...) {
    accountADao.deduct(...);  // 数据库 A
    accountBDao.add(...);     // 数据库 B
}
```

性能差 5-10x，仅适合金融账务等强一致场景。

## 4. TCC（Try-Confirm-Cancel）

### 4.1 思路

每个业务接口拆三个：
- **Try**：资源预留 / 业务校验（不真正提交）
- **Confirm**：实际提交（幂等）
- **Cancel**：回滚（幂等）

```java
// 转账例子
class AccountService {
    // Try：冻结余额
    boolean tryDeduct(String userId, BigDecimal amount, String txId) {
        return accountDao.freeze(userId, amount, txId);
    }
    
    // Confirm：扣款
    void confirmDeduct(String userId, BigDecimal amount, String txId) {
        accountDao.deductFrozen(userId, amount, txId);  // 幂等
    }
    
    // Cancel：解冻
    void cancelDeduct(String userId, BigDecimal amount, String txId) {
        accountDao.unfreeze(userId, amount, txId);  // 幂等
    }
}
```

### 4.2 流程

```
1. TM 调各服务的 Try：全部成功？
   - 全成功 → 调 Confirm
   - 任一失败 → 调 Cancel
2. Confirm/Cancel 必须幂等
3. 框架（Seata / Hmily / TCC-Transaction）负责协调
```

### 4.3 优缺

- ✓ 高性能（无锁，资源短暂预留）
- ✓ 灵活（业务定义 Try/Confirm/Cancel 粒度）
- ✗ 业务侵入大（一个接口拆三个）
- ✗ 处理空回滚 / 防悬挂等边界 case 复杂

### 4.4 边界问题

1. **空回滚**：Try 还没到，Cancel 先到（网络乱序）→ Cancel 要识别"没 try 过"直接返回成功
2. **悬挂**：Cancel 已执行，Try 才到 → Try 要识别"已 cancel"不做处理
3. **幂等**：Confirm/Cancel 重复调用结果一致

解决：用 try 表记录 txId 状态，每次操作前查状态。

## 5. Saga

### 5.1 思路

把长事务拆成 N 个本地事务，每个本地事务都有对应的补偿事务。任一步失败按反向顺序执行补偿。

```
[T1 → T2 → T3 → T4]   正常流
[T1 → T2 → T3 → ✗]    T3 失败
[C2 ← C1]             补偿 T2 → T1（反向）
```

### 5.2 实现方式

**编排式（Orchestration）**：中心协调器（Saga Orchestrator）决定下一步。
```java
class OrderSaga {
    void execute(OrderRequest req) {
        try {
            inventory.reserve(...);  // T1
            payment.charge(...);     // T2
            shipping.create(...);    // T3
        } catch (Exception e) {
            // 反向补偿
            payment.refund(...);     // C2
            inventory.release(...);  // C1
        }
    }
}
```

**协作式（Choreography）**：每个服务发事件，其他服务订阅响应。

```
订单服务 → 发"订单已创建"事件
库存服务 → 听到 → 扣库存 → 发"库存已扣"事件
支付服务 → 听到 → 扣款 → 发"支付完成"事件
任一失败发"失败"事件触发补偿
```

### 5.3 优缺

- ✓ 性能好（无锁，本地事务直接 commit）
- ✓ 适合长事务（小时 / 天级）
- ✗ 一致性弱（中间状态可被看到）
- ✗ 补偿逻辑复杂（业务需可逆）
- ✗ 编排式有单点，协作式难追踪

## 6. 本地消息表

### 6.1 思路

业务和消息发送在同一本地事务，保证业务成功必发消息。消息消费方失败重试。

```sql
-- 业务库
BEGIN;
UPDATE order SET status = 'paid' WHERE id = ?;
INSERT INTO outbox_msg (id, topic, payload, status) VALUES (?, 'order.paid', ?, 'pending');
COMMIT;
```

后台任务扫描 outbox，把 pending 消息发到 MQ，确认后 mark 为 sent。

### 6.2 优势

- 业务和消息一致（同库事务）
- MQ 失败不影响业务
- 简单可靠

### 6.3 缺点

- 业务库要加 outbox 表，污染 schema
- 后台任务多一层 infra

### 6.4 改进：Transactional Outbox

很多 MQ（Debezium + Kafka）通过 CDC 自动监听 outbox 表变化，免去后台任务。

## 7. 最大努力通知

适合不重要的下游：业务事务完成后异步通知，失败重试 N 次最终放弃。

```java
@Transactional
public void payOrder(...) {
    paymentDao.save(...);
    notifyService.notify(...);   // 失败 retry，最终给 admin 报警
}
```

适合"积分增加"、"日志归档"这种允许丢失的场景。

## 8. Seata 框架

阿里开源的分布式事务中间件，主推 4 种模式：

| 模式 | 一致性 | 性能 | 业务侵入 | 适合 |
|---|---|---|---|---|
| **AT** | 弱（最终） | 高 | 几乎无 | 多数业务（默认） |
| **TCC** | 弱（最终） | 高 | 大（写三个接口） | 性能 + 灵活性都要 |
| **Saga** | 弱（最终） | 高 | 中（写补偿） | 长事务 / 流程引擎 |
| **XA** | 强 | 低 | 几乎无 | 强一致需求 |

### 8.1 Seata AT 模式

AT = Auto Transaction。原理：拦截 SQL，自动生成 undo log，回滚时按 undo log 反向执行。

```java
@GlobalTransactional
public void order(OrderRequest req) {
    inventoryFeign.deduct(...);   // 自动加入全局事务
    paymentFeign.charge(...);
    orderDao.create(...);
}
```

底层流程：
1. 全局事务开始（TC 分配 xid）
2. 各分支事务执行 SQL，Seata 拦截解析生成 undo log 入库
3. 各分支事务提交（释放锁）
4. 全局事务提交 → 异步删 undo log
5. 全局事务回滚 → 各分支按 undo log 反向回滚

依赖：所有数据库表加 undo_log 表，所有连接走 Seata 代理。

### 8.2 优势 vs 痛点

- ✓ 业务零侵入
- ✗ 全局锁影响并发（同一行被多个全局事务同时改时排队）
- ✗ 对复杂 SQL（多表 join、子查询）支持有限

## 9. 选型决策

```
是不是强一致？
├── 是 → 能接受性能损失 5-10x？
│       ├── 是 → XA / Seata XA
│       └── 否 → TCC（业务能拆三段）
│
└── 否（最终一致够）
    ├── 业务可逆 → Saga 或 Seata AT（无侵入）
    ├── 业务不可逆 → 本地消息表 + MQ
    └── 下游不重要 → 最大努力通知
```

实际生产 80% 场景用最终一致方案。强一致只在金融、库存等高敏场景。

## 10. 高频面试题

**Q1：2PC 和 3PC 的区别？**
3PC 在 2PC 基础上多一个 CanCommit 阶段：
- CanCommit：试探性询问，不锁资源
- PreCommit：真正预备
- DoCommit：提交

加 timeout 机制：协调者挂了参与者也能自己决定（默认 commit）。但仍解决不了"网络分区导致部分 commit 部分 abort"的根本问题。实践中很少用。

**Q2：TCC 跟 2PC 区别？**
- 2PC 是数据库层面（锁记录）
- TCC 是业务层面（应用代码冻结资源）
- TCC 性能好（短期锁），但业务侵入大
- 2PC 业务无感知，但性能差 + 数据库需支持 XA

**Q3：本地消息表 vs MQ 事务消息？**
本质相同：保证业务和消息原子。
- 本地消息表：每个业务库加 outbox，后台任务推 MQ。简单但每个服务自己实现
- RocketMQ 事务消息：MQ 提供"半消息"，业务 commit 后 MQ 才转可消费。MQ 主动回查业务状态决定。原生支持，但耦合 RocketMQ

**Q4：Seata AT 怎么实现的？**
Seata Proxy 拦截 JDBC 操作，每次 update / insert / delete 前先 select 现有数据生成 before image，执行后生成 after image，组合成 undo log 入库。回滚时按 undo log 反向执行 SQL。提交时异步删除 undo log。

**Q5：Saga 跟 TCC 怎么选？**
- 业务步骤多 + 长事务（小时 / 天） → Saga
- 步骤少 + 短事务（秒级）+ 要资源预留 → TCC
- 业务可逆性好（补偿简单）→ Saga
- 业务难补偿（如发送邮件无法撤销）→ 加最大努力通知 + 人工

**Q6：怎么保证幂等？**
- 唯一 ID：每个请求带 idempotency_key，DB 唯一索引
- 状态机：操作前查当前状态，已是目标状态则跳过
- token：服务端发 token，客户端带，用一次销毁
- 版本号：CAS 比较 version 字段

**Q7：分布式事务永远成功的兜底？**
没有"永远成功"。极端场景下（网络分区、人为故障）数据可能不一致。生产兜底：
- 对账系统：定时跑批对比上下游数据，发现不一致告警 / 自动修复
- 人工干预接口：admin 后台手动修数据
- 监控 + Runbook：异常告警 + 处置流程
