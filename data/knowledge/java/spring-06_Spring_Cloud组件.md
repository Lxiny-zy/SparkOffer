# Spring Cloud 组件详解

## 1. 概览

### Spring Cloud 是什么
基于 Spring Boot 的**微服务治理工具集**，提供服务注册发现、配置中心、网关、负载均衡、熔断限流、链路追踪等一站式方案。

### 主流组件生态
| 功能 | Spring Cloud Netflix（旧） | Spring Cloud Alibaba | 推荐组合 |
|------|---------------------------|----------------------|----------|
| 注册中心 | Eureka（停止更新） | Nacos | Nacos |
| 配置中心 | Config + Bus | Nacos | Nacos |
| 网关 | Zuul 1 | Spring Cloud Gateway | Spring Cloud Gateway |
| 负载均衡 | Ribbon（停止） | LoadBalancer / Dubbo | Spring Cloud LoadBalancer |
| 熔断限流 | Hystrix（停止） | Sentinel | Sentinel / Resilience4j |
| 服务调用 | Feign | OpenFeign / Dubbo | OpenFeign |
| 链路追踪 | Sleuth + Zipkin | SkyWalking | Micrometer + Tempo |
| 分布式事务 | - | Seata | Seata |

**2024 推荐栈**：Nacos + Spring Cloud Gateway + OpenFeign + Sentinel + Seata + Micrometer Tracing。

---

## 2. Nacos（注册中心 + 配置中心）

### 特点
- 一个组件兼顾注册 + 配置
- 支持 AP/CP 双模式
- 可视化控制台
- 多数据中心
- 集群部署

### 启动 Nacos Server
```bash
sh startup.sh -m standalone
# 访问 http://localhost:8848/nacos
```

### 服务注册

```xml
<dependency>
    <groupId>com.alibaba.cloud</groupId>
    <artifactId>spring-cloud-starter-alibaba-nacos-discovery</artifactId>
</dependency>
```

```yaml
spring:
  application.name: order-service
  cloud.nacos.discovery:
    server-addr: localhost:8848
    namespace: dev
    group: DEFAULT_GROUP
```

启动后自动注册到 Nacos。

### 服务发现

```java
@Autowired DiscoveryClient discoveryClient;

List<ServiceInstance> instances = discoveryClient.getInstances("user-service");
// 获取 ip:port
```

通常不直接用，走 OpenFeign / LoadBalancer。

### 配置中心

```xml
<dependency>
    <groupId>com.alibaba.cloud</groupId>
    <artifactId>spring-cloud-starter-alibaba-nacos-config</artifactId>
</dependency>
```

`bootstrap.yml`（早于 application.yml 加载）：
```yaml
spring:
  application.name: order-service
  profiles.active: dev
  cloud.nacos.config:
    server-addr: localhost:8848
    file-extension: yaml
    namespace: dev
```

Nacos 上 Data ID：`order-service-dev.yaml`。

### 动态刷新

```java
@RestController
@RefreshScope  // 关键：启用动态刷新
public class FooController {
    @Value("${my.config}")
    private String config;
}
```

Nacos 修改配置 → 应用自动刷新（无需重启）。

### 配置共享
- 公共配置放 `common.yaml`
- 通过 `spring.cloud.nacos.config.shared-configs` 引入

---

## 3. Spring Cloud Gateway

### 特点
- 基于 Spring WebFlux（响应式，非阻塞）
- 性能优于 Zuul 1（阻塞式）
- 内置路由、过滤、限流
- Java 17 + Spring Boot 3

### 依赖
```xml
<dependency>
    <groupId>org.springframework.cloud</groupId>
    <artifactId>spring-cloud-starter-gateway</artifactId>
</dependency>
```

### 静态路由配置

```yaml
spring.cloud.gateway.routes:
  - id: user-service-route
    uri: lb://user-service  # lb 前缀 = 从注册中心负载均衡
    predicates:
      - Path=/api/user/**
    filters:
      - StripPrefix=2  # 去掉前两段 /api/user
      - AddRequestHeader=X-Gateway, true
```

### 动态路由（基于 Nacos）
- 配置写在 Nacos
- 网关监听变更自动更新

### 核心概念

**Predicate（断言）**：判断请求是否匹配路由
- `Path=/api/user/**`
- `Method=GET,POST`
- `Header=X-Request-Id`
- `Cookie=token,.+`
- `Query=foo,bar`
- `After / Before / Between` 时间断言

**Filter（过滤器）**：修改请求/响应
- `AddRequestHeader`
- `AddResponseHeader`
- `RewritePath`
- `StripPrefix`
- `RequestRateLimiter`（限流）
- `CircuitBreaker`（熔断）
- `Retry`

### 自定义全局 Filter

```java
@Component
public class AuthFilter implements GlobalFilter, Ordered {
    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        String token = exchange.getRequest().getHeaders().getFirst("Authorization");
        if (token == null) {
            exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
            return exchange.getResponse().setComplete();
        }
        // 校验 token，注入用户信息
        return chain.filter(exchange);
    }

    @Override
    public int getOrder() { return -1; }
}
```

### 限流（Redis）

```yaml
filters:
  - name: RequestRateLimiter
    args:
      redis-rate-limiter.replenishRate: 10  # 每秒令牌数
      redis-rate-limiter.burstCapacity: 20   # 桶容量
      key-resolver: "#{@userKeyResolver}"
```

```java
@Bean
public KeyResolver userKeyResolver() {
    return exchange -> Mono.just(
        Objects.requireNonNull(exchange.getRequest()
            .getHeaders().getFirst("X-User-Id")));
}
```

### vs Zuul
- Gateway 基于 WebFlux（Netty），非阻塞
- Zuul 1 基于 Servlet（Tomcat），阻塞
- Zuul 2 基于 Netty 但生态弱
- Gateway 更适合高并发网关场景

---

## 4. OpenFeign（声明式 HTTP 客户端）

### 依赖
```xml
<dependency>
    <groupId>org.springframework.cloud</groupId>
    <artifactId>spring-cloud-starter-openfeign</artifactId>
</dependency>
```

### 启用

```java
@SpringBootApplication
@EnableFeignClients
public class App {}
```

### 定义客户端

```java
@FeignClient(name = "user-service", path = "/users")
public interface UserClient {
    @GetMapping("/{id}")
    UserDto getUser(@PathVariable Long id);

    @PostMapping
    UserDto createUser(@RequestBody CreateUserRequest req);

    @GetMapping("/search")
    List<UserDto> search(@RequestParam String keyword, @RequestParam int page);
}
```

自动向 `user-service` 发请求，通过 LoadBalancer 选择实例。

### 超时与重试

```yaml
spring.cloud.openfeign.client.config.default:
  connect-timeout: 5000
  read-timeout: 5000

feign.retryer.period: 100
feign.retryer.max-period: 1000
feign.retryer.max-attempts: 3
```

### 拦截器（传递 Token）

```java
@Component
public class FeignAuthInterceptor implements RequestInterceptor {
    @Override
    public void apply(RequestTemplate template) {
        // 从当前请求传递 token 到下游
        HttpServletRequest req = ((ServletRequestAttributes)
            RequestContextHolder.currentRequestAttributes()).getRequest();
        String token = req.getHeader("Authorization");
        if (token != null) template.header("Authorization", token);
        template.header("X-Trace-Id", MDC.get("traceId"));
    }
}
```

### 降级（Fallback）

```java
@FeignClient(name = "user-service", fallback = UserClientFallback.class)
public interface UserClient { ... }

@Component
public class UserClientFallback implements UserClient {
    @Override
    public UserDto getUser(Long id) {
        return UserDto.builder().id(id).name("default").build();  // 降级
    }
}
```

需配合熔断器（Resilience4j/Sentinel）。

### 压缩与日志
```yaml
spring.cloud.openfeign:
  compression:
    request.enabled: true
    response.enabled: true
  client.config.default.logger-level: full  # NONE/BASIC/HEADERS/FULL
```

---

## 5. Sentinel（熔断限流）

### 特点
阿里开源，特性比 Hystrix 更多：
- **流量控制**：QPS / 并发数限流
- **熔断降级**：响应时间 / 异常比例 / 异常数
- **系统保护**：系统 Load / CPU / 线程
- **热点参数限流**：按参数维度限流（热点 key）
- **可视化控制台**

### 依赖
```xml
<dependency>
    <groupId>com.alibaba.cloud</groupId>
    <artifactId>spring-cloud-starter-alibaba-sentinel</artifactId>
</dependency>
```

### 注解使用

```java
@SentinelResource(
    value = "getUser",
    blockHandler = "blockHandler",
    fallback = "fallback"
)
public User getUser(Long id) {
    return userService.findById(id);
}

public User blockHandler(Long id, BlockException ex) {
    return User.builder().id(id).name("blocked").build();
}

public User fallback(Long id, Throwable t) {
    return User.builder().id(id).name("error").build();
}
```

- **blockHandler**：被限流/降级触发
- **fallback**：业务异常触发

### 控制台配置规则
在 Sentinel Dashboard 可视化配置，或通过 API/Nacos 持久化。

### 限流算法
- **快速失败**：直接返回
- **Warm Up**（预热）：冷启动时逐步放量
- **排队等待**：匀速通过（漏桶）
- **关联流控**：A 过载时限 B 的流量

---

## 6. Resilience4j（替代 Hystrix 推荐）

### 依赖
```xml
<dependency>
    <groupId>io.github.resilience4j</groupId>
    <artifactId>resilience4j-spring-boot3</artifactId>
</dependency>
```

### 配置

```yaml
resilience4j:
  circuitbreaker.instances.userService:
    slidingWindowSize: 10
    failureRateThreshold: 50
    waitDurationInOpenState: 10s
    permittedNumberOfCallsInHalfOpenState: 5
  retry.instances.userService:
    maxAttempts: 3
    waitDuration: 1s
  bulkhead.instances.userService:
    maxConcurrentCalls: 20
  ratelimiter.instances.userService:
    limitForPeriod: 100
    limitRefreshPeriod: 1s
  timelimiter.instances.userService:
    timeoutDuration: 3s
```

### 注解

```java
@CircuitBreaker(name = "userService", fallbackMethod = "getUserFallback")
@Retry(name = "userService")
@TimeLimiter(name = "userService")
@Bulkhead(name = "userService")
@RateLimiter(name = "userService")
public CompletableFuture<User> getUser(Long id) {
    return CompletableFuture.supplyAsync(() -> userClient.getUser(id));
}

public CompletableFuture<User> getUserFallback(Long id, Throwable t) {
    return CompletableFuture.completedFuture(User.defaultUser(id));
}
```

### 熔断器状态
- **CLOSED**（正常）
- **OPEN**（熔断，直接失败）
- **HALF_OPEN**（半开，试探）

---

## 7. Seata（分布式事务）

### 模式

**AT 模式（默认，推荐）**：
- 基于 XA 演进，业务无侵入
- 一阶段：本地事务 + undo log
- 二阶段：提交/回滚由 TC 协调

**TCC 模式**：
- 业务自定义 Try/Confirm/Cancel
- 性能好但开发成本高

**Saga 模式**：
- 长事务，补偿式

**XA 模式**：
- 强一致，性能低

### 架构

```
TC (Transaction Coordinator) ← Seata Server
    ↑
TM (Transaction Manager)     ← 发起全局事务的服务
    ↓
RM (Resource Manager)        ← 参与事务的服务
```

### 使用（AT 模式）

```xml
<dependency>
    <groupId>io.seata</groupId>
    <artifactId>seata-spring-boot-starter</artifactId>
</dependency>
```

```java
@Service
public class OrderService {
    @GlobalTransactional
    public void createOrder(Order order) {
        orderRepo.save(order);
        accountClient.deduct(order.userId(), order.amount());  // Feign 调用其他服务
        stockClient.lock(order.productId(), order.count());
    }
}
```

任一失败 → TC 协调所有 RM 回滚。

---

## 8. 链路追踪

### Micrometer Tracing + Zipkin（推荐）

Spring Boot 3 替代 Sleuth：

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-actuator</artifactId>
</dependency>
<dependency>
    <groupId>io.micrometer</groupId>
    <artifactId>micrometer-tracing-bridge-brave</artifactId>
</dependency>
<dependency>
    <groupId>io.zipkin.reporter2</groupId>
    <artifactId>zipkin-reporter-brave</artifactId>
</dependency>
```

```yaml
management.tracing.sampling.probability: 1.0
management.zipkin.tracing.endpoint: http://localhost:9411/api/v2/spans
```

日志自动加 traceId/spanId。

### 手动 Span

```java
@Autowired Tracer tracer;

Span span = tracer.nextSpan().name("my-op").start();
try (var ws = tracer.withSpan(span)) {
    // 业务
} finally {
    span.end();
}
```

### SkyWalking（替代方案）
国产 APM，字节码注入，无侵入。支持 Dubbo、Spring Cloud 等。

---

## 9. Spring Cloud Stream（消息驱动）

统一消息中间件抽象（Kafka/RabbitMQ/RocketMQ）：

```xml
<dependency>
    <groupId>org.springframework.cloud</groupId>
    <artifactId>spring-cloud-starter-stream-kafka</artifactId>
</dependency>
```

```yaml
spring.cloud.stream:
  bindings:
    output-out-0.destination: orders
    input-in-0.destination: orders
```

```java
@Bean
public Supplier<Order> output() {
    return () -> new Order(...);
}

@Bean
public Consumer<Order> input() {
    return order -> System.out.println("received: " + order);
}
```

---

## 10. 实战：一个典型微服务启动

```yaml
# bootstrap.yml
spring:
  application.name: order-service
  profiles.active: dev
  cloud:
    nacos:
      config:
        server-addr: nacos:8848
        file-extension: yaml
      discovery:
        server-addr: nacos:8848

# application.yml
server.port: 8080

resilience4j:
  circuitbreaker.instances.userClient:
    slidingWindowSize: 10
    failureRateThreshold: 50

management:
  endpoints.web.exposure.include: '*'
  tracing.sampling.probability: 1.0
```

```java
@SpringBootApplication
@EnableFeignClients
@EnableDiscoveryClient
public class OrderServiceApplication {
    public static void main(String[] args) {
        SpringApplication.run(OrderServiceApplication.class, args);
    }
}

@FeignClient("user-service")
public interface UserClient {
    @GetMapping("/users/{id}") UserDto getUser(@PathVariable Long id);
}

@RestController @RequestMapping("/orders")
public class OrderController {
    @Autowired UserClient userClient;
    @Autowired OrderService orderService;

    @PostMapping
    @GlobalTransactional  // Seata
    public Order create(@RequestBody CreateOrderReq req) {
        UserDto user = userClient.getUser(req.userId());
        return orderService.create(user, req);
    }
}
```

---

## 11. 版本兼容

| Spring Boot | Spring Cloud | Spring Cloud Alibaba |
|-------------|--------------|---------------------|
| 3.2.x | 2023.0.x (Leyton) | 2023.0.x |
| 3.0.x - 3.1.x | 2022.0.x (Kilburn) | 2022.0.x |
| 2.7.x | 2021.0.x (Jubilee) | 2021.0.x |

新项目用 **Spring Boot 3.x + Spring Cloud 2023.x + Spring Cloud Alibaba 2023.x**。

---

## 12. 常见问题

### Feign 循环依赖
A 服务 Feign 调 B，B 又 Feign 调 A → 死锁风险。
**解法**：避免循环，提取公共下游服务。

### Nacos 配置不生效
- 确认 `bootstrap.yml` 而非 `application.yml`
- 加 `@RefreshScope`
- Data ID 格式：`${name}-${profile}.${ext}`

### Gateway 内存泄漏
- 原因：WebFlux 下 ThreadLocal 陷阱
- 解法：不要在 Filter 里用 ThreadLocal，用 Reactor Context

### 熔断不触发
- 确认请求量达到最小触发阈值
- 确认异常类型在统计范围内

### Feign 超时被覆盖
- Ribbon / LoadBalancer / Feign / Hystrix 多层超时，取最小
- 建议统一配置在 `feign.client.config.default`

---

## 面试高频问题

**Q1：Spring Cloud 核心组件有哪些？**

微服务五大件：
- **注册中心**：Nacos / Eureka
- **配置中心**：Nacos / Config
- **网关**：Spring Cloud Gateway
- **负载均衡 + 服务调用**：OpenFeign + LoadBalancer
- **熔断限流**：Sentinel / Resilience4j

可选：链路追踪（Micrometer Tracing）、分布式事务（Seata）、消息（Spring Cloud Stream）。

**Q2：注册中心 CAP 如何选择？**

- **Eureka**：AP，服务可用优先
- **ZooKeeper**：CP，数据一致优先
- **Nacos**：可选 AP/CP（默认 AP）
- **Consul**：CP

微服务场景优先 AP：短暂看到旧服务列表比整体不可用好得多。

**Q3：Gateway vs Nginx？**

- **Nginx**：C 语言、高性能反向代理，偏运维
- **Gateway**：Java、动态路由、深度集成 Spring Cloud
- 实践中常组合：Nginx（外层 L4/L7 + CDN）+ Gateway（内层业务路由）

**Q4：Feign 是怎么实现的？**

- `@EnableFeignClients` 启用
- `@FeignClient` 接口通过**JDK 动态代理**生成实现
- 代理类发起 HTTP 请求（底层 OkHttp/HttpClient）
- LoadBalancer 选实例
- 编解码、拦截器、重试等流程
- 本质：声明式封装 HTTP 调用

**Q5：熔断器的三种状态？**

- **CLOSED**（闭合）：正常放行
- **OPEN**（熔断）：直接失败，不调下游
- **HALF_OPEN**（半开）：放行少量请求试探，成功进 CLOSED，失败继续 OPEN

防止雪崩：下游故障时快速失败，给下游恢复时间。

**Q6：限流算法有哪些？**

- **计数器**：简单但有临界问题
- **滑动窗口**：精确
- **漏桶**：平滑输出
- **令牌桶**：允许突发（推荐）
- **响应式**：基于系统负载动态调整

Sentinel/RateLimiter 多用令牌桶。

**Q7：分布式事务几种方案？**

- **2PC/XA**：强一致、性能低
- **TCC**：业务侵入、性能好
- **Saga**：长事务、补偿
- **本地消息表**：基于 MQ 最终一致
- **Seata AT**：基于 undo log 自动补偿，无侵入

选型：
- 强一致（金融）→ TCC / XA
- 最终一致（电商）→ Saga / 消息
- 降低开发成本 → Seata AT

**Q8：Nacos 和 Eureka 区别？**

- **Eureka**：AP、无配置中心、已停止更新
- **Nacos**：AP/CP 可选、注册+配置一体、中文社区活跃、阿里开源

Nacos 是更现代的选择，新项目推荐。

**Q9：OpenFeign 超时怎么配？**

```yaml
spring.cloud.openfeign.client.config:
  default:
    connect-timeout: 5000
    read-timeout: 10000
  user-service:  # 针对某服务覆盖
    read-timeout: 30000
```

注意：
- Feign 超时 和 Hystrix/Resilience4j 超时要协调
- Feign < 熔断超时

**Q10：Spring Cloud 和 Dubbo 区别？**

| 维度 | Spring Cloud | Dubbo |
|------|--------------|-------|
| 通信 | HTTP/REST | RPC（私有协议/Triple） |
| 性能 | 中 | 高 |
| 跨语言 | 好（HTTP） | Dubbo3 支持 gRPC |
| 生态 | 广 | 阿里系 |
| 门槛 | 低 | 中 |

现在 Dubbo3 也支持 Nacos、Seata、OpenTelemetry，生态重叠。技术选型取决于团队熟悉度和性能需求。
