# DDD 领域驱动设计

## 1. 什么是 DDD

### 定义
**DDD（Domain-Driven Design，领域驱动设计）** 是 Eric Evans 在 2003 年提出的一套**以业务领域为核心**的软件设计方法论。核心思想：软件架构应当反映业务领域。

### 解决什么问题
- 技术架构与业务脱节
- 业务复杂度上升时系统失控
- 需求沟通困难（开发 ↔ 业务）
- 代码中看不到业务概念
- 微服务拆分无依据

### 适用场景
- **复杂业务**：金融、电商、供应链、保险、ERP
- **长期演进**：5+ 年生命周期的系统
- **团队规模**：多团队协作
- **微服务**：提供拆分依据

### 不适用
- 简单 CRUD 应用
- 一次性项目
- 小团队快速迭代

---

## 2. DDD 战略设计

### 2.1 领域（Domain）

业务所涉及的问题空间。例：电商的"订单、商品、支付、物流"都是领域。

### 2.2 子域（Subdomain）

对领域的细分：

**核心域（Core Domain）**：
- 业务差异化的核心
- 值得重金投入
- 例：电商的商品推荐算法、支付风控

**支撑域（Supporting Domain）**：
- 支持核心，但无差异化
- 可自研但不如核心重要
- 例：订单管理、库存

**通用域（Generic Domain）**：
- 通用能力
- 优先买现成（SaaS）
- 例：用户认证、消息通知

### 2.3 限界上下文（Bounded Context）

**同一个词在不同上下文含义不同**。如"商品"：
- 商品中心：SKU、名称、分类
- 订单中心：商品快照（购买时的信息）
- 库存中心：仓位、可售量
- 搜索中心：全文索引

每个上下文有**独立模型**，通过明确接口交互。

### 2.4 上下文映射（Context Mapping）

描述上下文间的关系：

- **Shared Kernel**（共享内核）：两个上下文共享小块模型
- **Customer-Supplier**（客户-供应商）：上游优先服务下游
- **Conformist**（遵循者）：下游完全适配上游
- **Anticorruption Layer**（防腐层，ACL）：适配第三方或遗留系统
- **Open Host Service**（开放主机服务）：上游提供标准 API
- **Published Language**（发布语言）：跨上下文的标准数据格式
- **Partnership**（合作）：两个上下文平等协作
- **Separate Ways**（各自独立）：互不相干

### 2.5 通用语言（Ubiquitous Language）

**业务与开发共用一套词汇**。文档、代码、讨论都用同一名字。

例：
- 错：`UserService.changeStatus(1, 2)`
- 对：`CustomerService.activate(customerId)`

通用语言落地到：
- 类名、方法名
- 数据库字段
- 接口定义
- 产品文档
- 团队沟通

---

## 3. DDD 战术设计

### 3.1 实体（Entity）

**有唯一标识，生命周期内可变**。

```java
public class Order {
    private OrderId id;  // 唯一标识
    private OrderStatus status;
    private List<OrderItem> items;
    private Money totalAmount;

    public void pay(PaymentMethod method) {
        if (status != OrderStatus.CREATED)
            throw new IllegalStateException("订单不可支付");
        this.status = OrderStatus.PAID;
        // 发布领域事件
        DomainEvents.publish(new OrderPaidEvent(this.id));
    }
}
```

**关键**：行为写在实体内，不要写成贫血模型（只有 getter/setter）。

### 3.2 值对象（Value Object）

**无标识，完全由属性决定**。不可变。

```java
public record Money(BigDecimal amount, Currency currency) {
    public Money {
        Objects.requireNonNull(amount);
        Objects.requireNonNull(currency);
        if (amount.signum() < 0) throw new IllegalArgumentException();
    }

    public Money add(Money other) {
        if (!currency.equals(other.currency))
            throw new IllegalArgumentException();
        return new Money(amount.add(other.amount), currency);
    }
}
```

常用值对象：
- `Money`、`Address`、`DateRange`、`Email`、`PhoneNumber`

**好处**：
- 避免基本类型污染（如 `String email` → `Email` 类型安全）
- 业务规则内聚（校验、运算在 VO 内）

### 3.3 聚合（Aggregate）

**一组相关对象的集合，视为一个整体**，有一个**聚合根（Aggregate Root）**作为外部访问入口。

```java
// Order 是聚合根
public class Order {
    private OrderId id;
    private List<OrderItem> items;  // OrderItem 属于聚合内部

    // 外部只能通过 Order 操作 OrderItem
    public void addItem(Product product, int quantity) {
        items.add(new OrderItem(product, quantity));
        recalculateTotal();
    }
}
```

**聚合设计原则**：
- 聚合内强一致性（一个事务保证）
- 聚合间最终一致性（事件/异步）
- 聚合尽量小（减小事务范围）
- 通过 ID 引用其他聚合，不直接持有对象

### 3.4 领域服务（Domain Service）

**跨多个聚合的业务逻辑**，放在领域服务。

```java
public class TransferService {
    // 跨账户转账，涉及多个 Account 聚合
    public void transfer(AccountId from, AccountId to, Money amount) {
        Account fromAcc = accountRepo.find(from);
        Account toAcc = accountRepo.find(to);
        fromAcc.withdraw(amount);
        toAcc.deposit(amount);
        accountRepo.save(fromAcc);
        accountRepo.save(toAcc);
    }
}
```

### 3.5 仓储（Repository）

**封装持久化**，给领域层提供集合式访问。

```java
public interface OrderRepository {
    Order findById(OrderId id);
    Order findByNumber(String number);
    void save(Order order);
    List<Order> findByCustomer(CustomerId id);
}

// 实现在基础设施层
@Repository
public class JpaOrderRepository implements OrderRepository { ... }
```

**Repository 只针对聚合根**，不给每个 Entity 建 Repository。

### 3.6 工厂（Factory）

创建复杂对象：

```java
public class OrderFactory {
    public Order createFromCart(Customer customer, Cart cart) {
        Order order = new Order(OrderId.generate(), customer.id());
        cart.items().forEach(i -> order.addItem(i.product(), i.quantity()));
        return order;
    }
}
```

### 3.7 领域事件（Domain Event）

**业务中"发生了什么"的建模**。

```java
public record OrderPaidEvent(OrderId orderId, Instant paidAt) implements DomainEvent {}

// 聚合内发布
order.pay(method);  // 内部：DomainEvents.publish(new OrderPaidEvent(...));

// 监听器响应
@EventListener
public void on(OrderPaidEvent event) {
    // 通知物流、积分、营销...
}
```

**作用**：
- 解耦副作用
- 跨聚合/上下文通信
- 审计日志
- CQRS 事件溯源基础

---

## 4. 分层架构

### 经典四层

```
┌──────────────────────────────────────┐
│ Interface 接口层                      │
│   Controller / REST / GraphQL        │
└──────────────┬───────────────────────┘
               │
┌──────────────▼───────────────────────┐
│ Application 应用层                    │
│   Use Case / Application Service     │
│   事务、编排、DTO 转换                 │
└──────────────┬───────────────────────┘
               │
┌──────────────▼───────────────────────┐
│ Domain 领域层（核心）                  │
│   Entity / Value Object              │
│   Aggregate / Domain Service         │
│   Repository Interface               │
│   Domain Event                       │
└──────────────┬───────────────────────┘
               │
┌──────────────▼───────────────────────┐
│ Infrastructure 基础设施层             │
│   Repository Impl / DB / Cache       │
│   MQ / 第三方 API                     │
└──────────────────────────────────────┘
```

### 依赖方向
**上层依赖下层**，但 Infrastructure 依赖 Domain 的接口（依赖倒置）。

### Clean Architecture（整洁架构）

同心圆模型：
```
外 ← Frameworks & Drivers（DB、Web）
     Interface Adapters（Controller、Presenter）
     Application Business Rules（Use Case）
内 ← Enterprise Business Rules（Domain）
```

内层不依赖外层，外层通过接口注入。

---

## 5. 实战示例：订单系统

### 领域模型

```java
// 值对象
public record OrderId(String value) {
    public static OrderId generate() { return new OrderId(UUID.randomUUID().toString()); }
}
public record Money(BigDecimal amount, String currency) { ... }
public record OrderItem(String productId, String productName, int quantity, Money unitPrice) {
    public Money subtotal() { return unitPrice.multiply(quantity); }
}

// 聚合根
public class Order {
    private OrderId id;
    private CustomerId customerId;
    private OrderStatus status;
    private List<OrderItem> items = new ArrayList<>();
    private Money totalAmount;
    private Instant createdAt;

    // 工厂方法
    public static Order create(CustomerId customerId, List<OrderItem> items) {
        Order o = new Order();
        o.id = OrderId.generate();
        o.customerId = customerId;
        o.items.addAll(items);
        o.status = OrderStatus.CREATED;
        o.createdAt = Instant.now();
        o.recalculate();
        DomainEvents.publish(new OrderCreatedEvent(o.id));
        return o;
    }

    // 领域行为
    public void pay() {
        if (status != OrderStatus.CREATED)
            throw new OrderCannotBePaidException(id);
        status = OrderStatus.PAID;
        DomainEvents.publish(new OrderPaidEvent(id, Instant.now()));
    }

    public void cancel(String reason) {
        if (status == OrderStatus.SHIPPED || status == OrderStatus.DELIVERED)
            throw new OrderCannotBeCancelledException(id);
        status = OrderStatus.CANCELLED;
        DomainEvents.publish(new OrderCancelledEvent(id, reason));
    }

    private void recalculate() {
        totalAmount = items.stream()
            .map(OrderItem::subtotal)
            .reduce(Money.zero("CNY"), Money::add);
    }

    // getter（无 setter）
}

// Repository
public interface OrderRepository {
    Order findById(OrderId id);
    void save(Order order);
    List<Order> findByCustomer(CustomerId id);
}
```

### 应用服务

```java
@Service
public class PlaceOrderUseCase {
    @Autowired OrderRepository orderRepo;
    @Autowired InventoryService inventoryService;
    @Autowired CustomerRepository customerRepo;

    @Transactional
    public OrderId placeOrder(PlaceOrderCommand cmd) {
        // 1. 校验
        Customer customer = customerRepo.findById(cmd.customerId());
        if (!customer.canPlaceOrder())
            throw new CustomerCannotOrderException();

        // 2. 锁库存（外部服务）
        inventoryService.reserve(cmd.items());

        // 3. 创建订单聚合
        Order order = Order.create(customer.id(), toOrderItems(cmd.items()));

        // 4. 持久化
        orderRepo.save(order);

        return order.id();
    }
}
```

### 接口层

```java
@RestController @RequestMapping("/orders")
public class OrderController {
    @Autowired PlaceOrderUseCase placeOrderUseCase;

    @PostMapping
    public ResponseEntity<OrderDto> create(@RequestBody CreateOrderRequest req) {
        OrderId id = placeOrderUseCase.placeOrder(toCommand(req));
        return ResponseEntity.created(URI.create("/orders/" + id.value())).build();
    }
}
```

### 基础设施

```java
@Repository
public class JpaOrderRepository implements OrderRepository {
    @Autowired OrderEntityRepository entityRepo;

    @Override
    public Order findById(OrderId id) {
        return entityRepo.findById(id.value())
            .map(this::toDomain)
            .orElseThrow();
    }

    @Override
    public void save(Order order) {
        entityRepo.save(toEntity(order));
    }
}
```

---

## 6. CQRS

### 概念
**CQRS**（Command Query Responsibility Segregation）：**读写分离**。
- **Command**（写）：改变状态，走领域模型
- **Query**（读）：不变状态，可直接查库（绕过领域）

### 为什么
- 读写模型天然不同（读要聚合多表，写要业务规则）
- 读可高性能优化（缓存、索引）
- 写可严格校验

### 实现

```java
// Command 侧（写）
@Service
public class OrderCommandService {
    public OrderId placeOrder(PlaceOrderCommand cmd) {
        Order order = Order.create(...);
        orderRepo.save(order);
        return order.id();
    }
}

// Query 侧（读）
@Service
public class OrderQueryService {
    @Autowired JdbcTemplate jdbc;

    public OrderDetailDto getOrderDetail(String id) {
        // 直接多表 JOIN，跳过领域模型
        return jdbc.queryForObject("""
            SELECT o.*, c.name as customer_name, ...
            FROM orders o JOIN customers c ON o.customer_id = c.id
            WHERE o.id = ?
            """, ..., id);
    }
}
```

---

## 7. Event Sourcing（事件溯源）

### 概念
**不存当前状态，只存所有事件**。通过重放事件得到当前状态。

```
传统：
  orders 表：id=1, status=PAID, total=100

事件溯源：
  events 表：
    1. OrderCreatedEvent(id=1, items=...)
    2. OrderPaidEvent(id=1, time=...)
    3. OrderShippedEvent(id=1, tracking=...)
```

### 优势
- 完整审计
- 时间穿越（任意时刻状态）
- 自然支持 CQRS + 事件驱动

### 劣势
- 学习曲线陡
- 查询复杂
- 事件不可变（改 schema 难）

### 工具
- **Axon Framework**（Java）
- **EventStore DB**

---

## 8. 六边形架构（端口与适配器）

```
        ┌─────────────────────────┐
        │   入站适配器             │
        │   REST / gRPC / MQ       │
        └──────┬──────────────────┘
               │ 入站端口（Use Case Interface）
               ▼
┌──────────────────────────────────┐
│       核心业务（领域 + 应用）      │
└──────────────────────────────────┘
               │ 出站端口（Repository Interface）
               ▼
        ┌─────────────────────────┐
        │   出站适配器             │
        │   JPA / Redis / 外部 API │
        └─────────────────────────┘
```

**核心无外部依赖**，外部通过端口（接口）与核心交互。可轻松替换 DB、协议。

---

## 9. DDD 与微服务

### 对应关系
- **一个限界上下文 ≈ 一个微服务**
- 上下文映射 = 服务间接口
- 聚合边界 = 事务边界 = 微服务边界

### 实战原则
- 先 DDD 建模，再拆服务
- 核心域独立服务
- 通用域考虑 SaaS
- 跨服务事务用 Saga / 最终一致

---

## 10. 落地挑战

### 挑战 1：业务人员不配合
**解法**：**Event Storming 工作坊**，用便签把业务事件贴满墙，业务和开发一起梳理。

### 挑战 2：代码变复杂
**解法**：
- 简单 CRUD 不强推 DDD
- 核心域严格 DDD，外围域用传统分层

### 挑战 3：团队不熟
**解法**：
- 培训 + 结对
- 从小模块试点
- 代码评审把关

### 挑战 4：ORM 映射麻烦
**解法**：
- 把 Entity 分为 DomainEntity 和 DataEntity
- 用 Repository 做 Mapping
- 或 JPA 直接映射（但会污染领域）

### 挑战 5：过度设计
**解法**：
- 从贫血模型重构到充血模型
- 先实现后重构
- 不是所有代码都要"战术 DDD"

---

## 11. Event Storming 事件风暴

### 流程
1. **领域专家 + 开发**共同参与
2. **橙色便签**：领域事件（用过去时，"订单已支付"）
3. **蓝色便签**：命令（触发事件的动作）
4. **黄色便签**：聚合（封装业务逻辑）
5. **粉色便签**：外部系统
6. **紫色便签**：策略/规则
7. 按**时间顺序**排列事件
8. 识别限界上下文

### 价值
- 业务全景可视
- 开发与业务对齐
- 识别领域模型
- 发现流程问题

---

## 12. 贫血模型 vs 充血模型

### 贫血（反模式）

```java
// Entity 只有 getter/setter
class Order {
    String status;
    List<OrderItem> items;
    // ... 全是 getter/setter
}

// 业务逻辑全在 Service
class OrderService {
    public void payOrder(Long id) {
        Order o = repo.find(id);
        if (!"CREATED".equals(o.getStatus())) throw ...;
        o.setStatus("PAID");
        repo.save(o);
    }
}
```

**问题**：Entity 只是数据袋子，业务规则散落各 Service，无法复用和演进。

### 充血（DDD 推崇）

```java
class Order {
    public void pay() {  // 行为写在 Entity 内
        if (status != CREATED) throw new IllegalStateException();
        status = PAID;
        DomainEvents.publish(new OrderPaidEvent(id));
    }
}

class OrderApplicationService {
    public void pay(OrderId id) {
        Order o = repo.find(id);
        o.pay();  // 业务规则在 Entity 内
        repo.save(o);
    }
}
```

**好处**：业务规则封装在对象内，不易遗漏，易单测。

---

## 面试高频问题

**Q1：DDD 解决什么问题？**

解决**业务复杂度上升后的软件失控**。核心：
- 代码反映业务（通用语言）
- 复杂业务拆成清晰模块（限界上下文）
- 业务规则聚合（充血模型）
- 提供微服务拆分依据

适合复杂、长期演进的系统；不适合简单 CRUD。

**Q2：聚合设计原则？**

- 聚合内**强一致**（一次事务）
- 聚合间**最终一致**（事件/异步）
- **尽量小**（事务范围小）
- 通过**聚合根**访问内部对象
- 跨聚合引用用 **ID**，不直接持有对象

太大影响性能，太小失去聚合意义，需权衡。

**Q3：限界上下文如何划分？**

- 领域专家参与 Event Storming
- 识别**通用语言边界**（同一概念不同含义 → 不同上下文）
- 子域对应上下文（核心/支撑/通用）
- 组织结构反映（康威定律）
- 持续演进调整

典型：电商 → 商品、订单、库存、支付、物流、用户等上下文。

**Q4：实体 vs 值对象区别？**

- **实体**：有 ID，生命周期内可变。例：Order、Customer
- **值对象**：无 ID，由属性决定，不可变。例：Money、Address

**判断**：两个实例是否有意义区分？
- 两个 Money(100) 等价 → 值对象
- 两个 Order(id=1) 是同一个 → 实体

**Q5：充血模型 vs 贫血模型？**

- **贫血**：Entity 只有 getter/setter，业务逻辑在 Service
- **充血**：Entity 内有行为，业务规则封装

DDD 推充血：业务规则内聚、不易遗漏、易测试。

但全充血不现实，应用服务还是要做编排。关键：**领域层不要是贫血的**。

**Q6：Repository 和 DAO 区别？**

- **DAO**：数据访问对象，一对一映射到表
- **Repository**：面向**聚合根**，提供集合式访问，屏蔽持久化

DAO 更底层（写 SQL），Repository 更面向业务（`findByCustomer`）。Repository 可由 DAO 实现。

**Q7：为什么需要防腐层（ACL）？**

两种场景：
- 对接遗留系统（模型老旧）
- 对接第三方（模型不可控）

ACL 做**模型翻译**，防止外部模型污染核心领域。

```java
// 核心
class MyOrder { ... }

// ACL
class LegacyOrderAdapter {
    MyOrder toDomain(LegacyOrder legacy) { ... }
}
```

**Q8：CQRS 什么时候用？**

- 读写模型差异大（复杂聚合写 + 多表联查读）
- 读写性能要求不同
- 审计需求强
- 系统读远大于写

简单系统不需要 CQRS，增加复杂度。

**Q9：DDD 和微服务关系？**

DDD 是设计方法论，微服务是部署架构。

- **限界上下文 ≈ 微服务**（DDD 提供拆分依据）
- **聚合边界 = 事务边界**
- **上下文映射 = 服务接口**

先 DDD 建模，再决定服务边界，避免拆分失败（微服务反模式）。

**Q10：如何落地 DDD？**

渐进式：
1. **通用语言**：团队统一术语
2. **限界上下文**：梳理业务子域
3. **识别聚合**：从核心业务对象入手
4. **充血模型**：逐步把 Service 逻辑下沉到 Entity
5. **领域事件**：解耦副作用
6. **分层架构**：重构代码目录结构

不要一步到位，先核心域试点，总结经验再推广。
