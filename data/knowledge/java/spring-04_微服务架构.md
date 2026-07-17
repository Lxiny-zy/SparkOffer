# 微服务架构

## 1. 架构演进：单体 vs SOA vs 微服务

### 1.1 三种架构对比

| 特性 | 单体架构 | SOA（面向服务架构） | 微服务架构 |
|------|---------|-------------------|-----------|
| 部署方式 | 单个 WAR/JAR 包 | 多个服务，共享 ESB | 独立部署的小服务 |
| 通信方式 | 本地方法调用 | ESB（企业服务总线） | 轻量级 HTTP/gRPC/MQ |
| 数据管理 | 共享数据库 | 共享或独立 | 每个服务独立数据库 |
| 技术栈 | 统一技术栈 | 可不同（ESB 适配） | 完全自由 |
| 团队组织 | 大团队统一管理 | 按服务分组 | 小团队自治（2-Pizza 团队） |
| 扩展方式 | 整体水平扩展 | 服务级别扩展 | 服务级别精准扩展 |
| 复杂度 | 低（初期）→ 高（后期） | 中（ESB 是瓶颈） | 高（分布式复杂性） |
| 适用场景 | 小型项目、初创期 | 大型企业内部系统集成 | 互联网大规模应用 |

### 1.2 单体架构

```
┌───────────────────────────────────┐
│           单体应用                  │
│  ┌──────┐ ┌──────┐ ┌──────┐     │
│  │用户模块│ │订单模块│ │支付模块│     │
│  └──┬───┘ └──┬───┘ └──┬───┘     │
│     └────────┴────────┘          │
│           共享数据库               │
└───────────────────────────────────┘
```

**优点**：开发简单、测试方便、部署简单、性能好（本地调用）
**缺点**：代码高度耦合、技术栈单一、扩展困难、发布周期长、单点故障影响全局

### 1.3 微服务架构

```
     ┌─────────┐
     │ API 网关  │
     └────┬────┘
    ┌─────┼─────┐
    ▼     ▼     ▼
┌──────┐┌──────┐┌──────┐
│用户服务││订单服务││支付服务│
│ DB-1 ││ DB-2 ││ DB-3 │
└──────┘└──────┘└──────┘
    │              │
    ▼              ▼
┌──────┐     ┌──────┐
│消息队列│     │配置中心│
└──────┘     └──────┘
```

**微服务优缺点**：

| 优点 | 缺点 |
|------|------|
| 独立部署、独立扩展 | 分布式复杂性（网络延迟、数据一致性） |
| 技术栈自由 | 运维成本高（需要 CI/CD、容器化） |
| 团队自治，并行开发 | 服务间调用有性能开销 |
| 故障隔离，局部故障不影响全局 | 分布式事务难处理 |
| 按需扩展（热点服务独立扩容） | 集成测试复杂 |
| 适合大团队协作 | 服务拆分边界难以确定 |

---

## 2. 服务注册与发现

### 2.1 核心原理

```
                ┌──────────┐
    ① 注册 ──→  │ 注册中心   │ ←── ① 注册
                │(Registry) │
    ③ 健康检查  │           │  ③ 健康检查
                └─────┬────┘
                      │ ② 订阅/拉取服务列表
              ┌───────┴───────┐
              ▼               ▼
        ┌──────────┐   ┌──────────┐
        │ 服务消费者  │   │ 服务提供者  │
        │(Consumer) │──→│(Provider) │
        └──────────┘   └──────────┘
              ④ 本地缓存服务列表
              ⑤ 负载均衡调用
```

**工作流程**：
1. 服务启动时向注册中心注册（上报地址、端口、健康状态等元数据）
2. 消费者从注册中心订阅或定期拉取服务列表
3. 注册中心定期对服务实例进行健康检查，剔除不健康实例
4. 消费者本地缓存服务列表，注册中心宕机不影响已缓存的调用
5. 消费者通过负载均衡策略选择一个实例发起调用

### 2.2 Nacos（推荐）

```yaml
spring:
  cloud:
    nacos:
      discovery:
        server-addr: 127.0.0.1:8848
        namespace: dev               # 命名空间隔离
        group: DEFAULT_GROUP
        cluster-name: BJ             # 集群名称
        weight: 1                    # 权重
        ephemeral: true              # 临时实例（心跳模式）
```

**Nacos 特性**：
- 同时支持 **AP 模式**（临时实例，心跳检测）和 **CP 模式**（持久实例，Raft 协议）
- 内置 **配置中心**，不需要额外部署配置服务
- 支持 **命名空间** 隔离（dev/test/prod）
- 支持 **集群** 和 **权重** 配置
- 提供完善的 **管理界面**

### 2.3 Nacos vs Eureka vs Consul

| 特性 | Nacos | Eureka | Consul |
|------|-------|--------|--------|
| 一致性协议 | AP/CP 可切换 | AP（最终一致性） | CP（Raft） |
| 健康检查 | TCP/HTTP/MySQL/自定义 | 客户端心跳 | TCP/HTTP/gRPC/脚本 |
| 配置中心 | 内置支持 | 不支持 | KV 存储支持 |
| 管理界面 | 完善 | 简单 | 完善 |
| 多数据中心 | 支持 | 不支持 | 支持 |
| 维护状态 | 活跃维护 | 停止维护（2.x） | 活跃维护 |
| 适用场景 | Java 微服务首选 | 旧项目维护 | 多语言微服务 |

### 2.4 临时实例 vs 持久实例

```
临时实例（Ephemeral）：
- 客户端主动发送心跳（默认 5s 一次）
- 15s 未收到心跳标记为不健康
- 30s 未收到心跳剔除实例
- AP 模式，采用 Distro 协议
- 适合：需要自动上下线的服务实例

持久实例（Persistent）：
- 服务端主动健康检查
- 不健康不会被剔除，只标记状态
- CP 模式，采用 Raft 协议
- 适合：数据库、缓存等基础设施服务
```

---

## 3. API 网关

### 3.1 Spring Cloud Gateway（推荐）

```
                        ┌─────────────────────┐
   客户端请求 ──────────→ │  Spring Cloud Gateway │
                        │                     │
                        │  Route Predicate     │ → 路由匹配
                        │        ↓             │
                        │  Pre-Filter Chain    │ → 前置过滤器
                        │        ↓             │
                        │  Proxy to Service    │ → 转发到下游服务
                        │        ↓             │
                        │  Post-Filter Chain   │ → 后置过滤器
                        │        ↓             │
                        │  Response to Client  │ → 返回响应
                        └─────────────────────┘
```

### 3.2 路由配置

```yaml
spring:
  cloud:
    gateway:
      routes:
        # 用户服务路由
        - id: user-service
          uri: lb://user-service          # lb:// 表示负载均衡
          predicates:
            - Path=/api/users/**          # 路径匹配
            - Method=GET,POST             # 请求方法
            - Header=Authorization, Bearer.* # 请求头匹配
          filters:
            - StripPrefix=1               # 去掉路径前缀
            - AddRequestHeader=X-Request-Source, gateway
            - name: RequestRateLimiter    # 限流
              args:
                redis-rate-limiter.replenishRate: 10    # 令牌填充速率
                redis-rate-limiter.burstCapacity: 20    # 令牌桶容量
                key-resolver: "#{@ipKeyResolver}"       # 限流 key

        # 订单服务路由
        - id: order-service
          uri: lb://order-service
          predicates:
            - Path=/api/orders/**
          filters:
            - StripPrefix=1
            - name: CircuitBreaker        # 熔断
              args:
                name: orderCircuitBreaker
                fallbackUri: forward:/fallback/order

      # 全局过滤器
      default-filters:
        - AddResponseHeader=X-Response-Time, ${T(java.time.Instant).now()}
```

### 3.3 自定义全局过滤器

```java
@Component
public class AuthGlobalFilter implements GlobalFilter, Ordered {

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        ServerHttpRequest request = exchange.getRequest();
        String path = request.getURI().getPath();

        // 白名单路径直接放行
        if (isWhiteListed(path)) {
            return chain.filter(exchange);
        }

        // 验证 Token
        String token = request.getHeaders().getFirst("Authorization");
        if (token == null || !token.startsWith("Bearer ")) {
            exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
            return exchange.getResponse().setComplete();
        }

        try {
            UserInfo user = jwtUtil.parseToken(token.substring(7));
            // 将用户信息传递给下游服务
            ServerHttpRequest mutatedRequest = request.mutate()
                .header("X-User-Id", String.valueOf(user.getId()))
                .header("X-User-Name", user.getUsername())
                .build();
            return chain.filter(exchange.mutate().request(mutatedRequest).build());
        } catch (Exception e) {
            exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
            return exchange.getResponse().setComplete();
        }
    }

    @Override
    public int getOrder() {
        return -100; // 优先级（数值越小越先执行）
    }
}
```

### 3.4 网关限流

```java
// 基于 IP 的限流 Key
@Bean
public KeyResolver ipKeyResolver() {
    return exchange -> Mono.just(
        exchange.getRequest().getRemoteAddress().getAddress().getHostAddress()
    );
}

// 基于用户的限流 Key
@Bean
public KeyResolver userKeyResolver() {
    return exchange -> Mono.just(
        exchange.getRequest().getHeaders().getFirst("X-User-Id")
    );
}

// 基于 API 路径的限流 Key
@Bean
public KeyResolver apiKeyResolver() {
    return exchange -> Mono.just(
        exchange.getRequest().getPath().value()
    );
}
```

### 3.5 Gateway vs Zuul

| 特性 | Spring Cloud Gateway | Zuul 1.x | Zuul 2.x |
|------|---------------------|----------|----------|
| 底层框架 | WebFlux（Netty） | Servlet（阻塞） | Netty（异步） |
| 编程模型 | 响应式（Reactor） | 同步阻塞 | 异步非阻塞 |
| 性能 | 高 | 一般 | 高 |
| 长连接 | 支持 WebSocket | 不支持 | 支持 |
| 限流 | 内置 Redis 限流 | 需自定义 | 需自定义 |
| 维护状态 | Spring 官方维护 | Netflix 停止维护 | Netflix 内部使用 |

---

## 4. 负载均衡

### 4.1 客户端负载均衡 vs 服务端负载均衡

```
服务端负载均衡（Nginx）：
   Client → Nginx(LB) → Service Instance
   所有请求经过 LB 节点，LB 是单独的组件

客户端负载均衡（Ribbon/LoadBalancer）：
   Client(内置LB) → Service Instance
   LB 逻辑在客户端内，从注册中心获取服务列表后本地决策
```

### 4.2 Spring Cloud LoadBalancer（推荐）

```java
// 使用 @LoadBalanced 启用负载均衡
@Bean
@LoadBalanced
public RestTemplate restTemplate() {
    return new RestTemplate();
}

// 调用时使用服务名替代 IP:Port
restTemplate.getForObject("http://user-service/api/users/1", User.class);
```

### 4.3 负载均衡策略对比

| 策略 | 说明 | 适用场景 |
|------|------|---------|
| 轮询（Round Robin） | 按顺序依次分配（默认） | 实例性能相近 |
| 随机（Random） | 随机选择一个实例 | 简单场景 |
| 加权轮询（Weighted RR） | 按权重分配，权重高的分配多 | 实例性能不同 |
| 加权随机（Weighted Random） | 按权重概率随机选择 | 实例性能不同 |
| 最少活跃连接（Least Connections） | 选择当前活跃连接最少的实例 | 长连接场景 |
| 一致性哈希（Consistent Hash） | 相同参数请求路由到同一实例 | 需要会话保持 |
| 响应时间加权 | 响应时间短的实例分配更多请求 | 对延迟敏感的场景 |

### 4.4 自定义负载均衡策略

```java
public class CustomLoadBalancer implements ReactorServiceInstanceLoadBalancer {

    private final ObjectProvider<ServiceInstanceListSupplier> supplier;
    private final AtomicInteger position = new AtomicInteger(0);

    @Override
    public Mono<Response<ServiceInstance>> choose(Request request) {
        return supplier.getIfAvailable().get(request)
            .next()
            .map(instances -> {
                if (instances.isEmpty()) {
                    return new EmptyResponse();
                }
                // 自定义选择逻辑
                int index = position.getAndIncrement() % instances.size();
                ServiceInstance instance = instances.get(Math.abs(index));
                return new DefaultResponse(instance);
            });
    }
}
```

---

## 5. 服务熔断与降级

### 5.1 Circuit Breaker 模式

```
          ┌───────────────────────────┐
          │        状态转换图           │
          │                           │
          │  ┌──────┐                 │
          │  │ CLOSED │ ←──── 成功率恢复 │
          │  │ (正常)  │               │
          │  └──┬───┘                 │
          │     │ 失败率超过阈值        │
          │     ▼                     │
          │  ┌──────┐  超时后放行探测   │
          │  │ OPEN  │ ────────────→  │
          │  │(熔断)  │              │ │
          │  └──────┘              │ │
          │                        ▼ │
          │              ┌──────────┐ │
          │              │ HALF_OPEN │ │
          │              │ (半开探测) │ │
          │              └──┬───────┘ │
          │      探测成功 ──┘  探测失败 │
          │      → CLOSED     → OPEN │
          └───────────────────────────┘
```

### 5.2 Sentinel（推荐）

```xml
<dependency>
    <groupId>com.alibaba.cloud</groupId>
    <artifactId>spring-cloud-starter-alibaba-sentinel</artifactId>
</dependency>
```

```yaml
spring:
  cloud:
    sentinel:
      transport:
        dashboard: localhost:8080    # Sentinel 控制台地址
        port: 8719                    # 与控制台通信端口
      eager: true                     # 立即初始化
```

```java
// 1. 资源定义 + 降级
@SentinelResource(value = "getUser",
                  blockHandler = "getUserBlockHandler",
                  fallback = "getUserFallback")
public User getUser(Long id) {
    return userService.getById(id);
}

// 限流/熔断后的处理（BlockException）
public User getUserBlockHandler(Long id, BlockException ex) {
    return new User(-1L, "系统繁忙，请稍后重试");
}

// 业务异常的降级处理
public User getUserFallback(Long id, Throwable throwable) {
    return new User(-1L, "服务暂时不可用");
}

// 2. OpenFeign 整合 Sentinel
@FeignClient(name = "user-service", fallbackFactory = UserClientFallback.class)
public interface UserClient {
    @GetMapping("/api/users/{id}")
    User getUser(@PathVariable Long id);
}

@Component
public class UserClientFallback implements FallbackFactory<UserClient> {
    @Override
    public UserClient create(Throwable cause) {
        return new UserClient() {
            @Override
            public User getUser(Long id) {
                log.error("调用用户服务失败: {}", cause.getMessage());
                return new User(-1L, "用户服务不可用");
            }
        };
    }
}
```

### 5.3 Sentinel 规则类型

| 规则类型 | 说明 | 配置项 |
|---------|------|--------|
| 流控规则 | 限制 QPS 或并发线程数 | 阈值、流控模式（直接/关联/链路）、流控效果（快速失败/Warm Up/排队等待） |
| 熔断规则 | 错误率/慢调用率超阈值时熔断 | 慢调用比例、异常比例、异常数、熔断时长、最小请求数 |
| 热点规则 | 对热点参数限流 | 参数索引、单机阈值、统计窗口 |
| 系统规则 | 系统级别的保护 | LOAD、CPU 使用率、总 QPS、入口 QPS、线程数 |
| 授权规则 | 黑白名单控制 | 来源应用、控制类型（白名单/黑名单） |

### 5.4 熔断 vs 降级 vs 限流

| 机制 | 触发条件 | 目的 | 示例 |
|------|---------|------|------|
| 熔断 | 下游服务故障率超阈值 | 快速失败，防止级联故障 | 订单服务调用支付服务超时率 > 50%，熔断 30s |
| 降级 | 服务不可用时 | 返回兜底数据，保证核心流程 | 推荐服务不可用时返回默认推荐列表 |
| 限流 | 请求量超过系统承载能力 | 保护系统不被打垮 | 秒杀接口限制 QPS = 1000 |

### 5.5 Sentinel vs Hystrix vs Resilience4j

| 特性 | Sentinel | Hystrix | Resilience4j |
|------|----------|---------|-------------|
| 隔离策略 | 信号量隔离 | 线程池/信号量 | 信号量 |
| 熔断策略 | 慢调用比例/异常比例/异常数 | 异常比例 | 异常比例/慢调用 |
| 实时统计 | 滑动窗口（LeapArray） | 滑动窗口（RxJava） | Ring Bit Buffer |
| 动态规则 | 支持（Nacos/ZK/Apollo） | 支持（Archaius） | 有限支持 |
| 控制台 | 完善的 Dashboard | 简单的 Dashboard | 无 |
| 注解支持 | @SentinelResource | @HystrixCommand | @CircuitBreaker |
| 维护状态 | 活跃维护 | 停止维护 | 活跃维护 |

---

## 6. 分布式配置中心

### 6.1 Nacos Config

```yaml
# bootstrap.yml（必须是 bootstrap，优先于 application 加载）
spring:
  application:
    name: user-service
  cloud:
    nacos:
      config:
        server-addr: 127.0.0.1:8848
        namespace: dev
        group: DEFAULT_GROUP
        file-extension: yaml        # 配置格式
        shared-configs:             # 共享配置
          - data-id: common.yaml
            group: DEFAULT_GROUP
            refresh: true
        extension-configs:          # 扩展配置
          - data-id: redis.yaml
            group: DEFAULT_GROUP
            refresh: true
```

```java
// 动态刷新配置
@RestController
@RefreshScope    // 配置变更时自动刷新
public class ConfigController {

    @Value("${app.feature.enabled:false}")
    private boolean featureEnabled;

    @GetMapping("/config")
    public String getConfig() {
        return "featureEnabled: " + featureEnabled;
    }
}

// 监听配置变更
@Component
public class ConfigChangeListener {

    @NacosConfigListener(dataId = "user-service.yaml", groupId = "DEFAULT_GROUP")
    public void onConfigChange(String config) {
        System.out.println("配置变更: " + config);
        // 执行重新初始化逻辑
    }
}
```

### 6.2 Nacos Config vs Apollo vs Spring Cloud Config

| 特性 | Nacos Config | Apollo | Spring Cloud Config |
|------|-------------|--------|-------------------|
| 配置存储 | MySQL | MySQL | Git 仓库 |
| 实时推送 | 支持（长轮询） | 支持（长轮询 + 推送） | 需要 Bus + MQ |
| 版本管理 | 支持 | 支持（完善） | Git 天然支持 |
| 灰度发布 | 支持 | 支持（完善） | 不支持 |
| 权限管理 | 基础 | 完善（细粒度） | 依赖 Git 权限 |
| 多环境管理 | 命名空间 | 环境 + 集群 | profile + 分支 |
| 管理界面 | 内置 | 完善 | 无（需看 Git） |
| 额外依赖 | 无（注册中心自带） | 需独立部署 | 需 Git + MQ |

---

## 7. 服务通信

### 7.1 OpenFeign 声明式 HTTP 客户端

```java
@FeignClient(
    name = "user-service",
    path = "/api/users",
    configuration = FeignConfig.class,
    fallbackFactory = UserClientFallback.class
)
public interface UserClient {

    @GetMapping("/{id}")
    User getUser(@PathVariable("id") Long id);

    @PostMapping
    User createUser(@RequestBody UserDTO dto);

    @GetMapping
    List<User> listUsers(@RequestParam("status") Integer status);

    @DeleteMapping("/{id}")
    void deleteUser(@PathVariable("id") Long id);
}

// Feign 配置
@Configuration
public class FeignConfig {
    // 日志级别
    @Bean
    public Logger.Level feignLoggerLevel() {
        return Logger.Level.FULL;
    }

    // 超时配置
    @Bean
    public Request.Options options() {
        return new Request.Options(
            5, TimeUnit.SECONDS,    // connectTimeout
            10, TimeUnit.SECONDS,   // readTimeout
            true                    // followRedirects
        );
    }

    // 请求拦截器（传递 Token）
    @Bean
    public RequestInterceptor requestInterceptor() {
        return template -> {
            ServletRequestAttributes attributes =
                (ServletRequestAttributes) RequestContextHolder.getRequestAttributes();
            if (attributes != null) {
                String token = attributes.getRequest().getHeader("Authorization");
                template.header("Authorization", token);
            }
        };
    }
}
```

### 7.2 gRPC vs REST

| 特性 | gRPC | REST（HTTP/JSON） |
|------|------|-------------------|
| 协议 | HTTP/2 | HTTP/1.1 或 HTTP/2 |
| 序列化 | Protocol Buffers（二进制） | JSON（文本） |
| 性能 | 高（二进制编码 + 多路复用） | 一般（JSON 解析开销大） |
| 流式传输 | 支持（双向流） | 不支持（或 WebSocket） |
| 代码生成 | 自动生成客户端/服务端代码 | 手动编写或 OpenAPI 生成 |
| 浏览器支持 | 需要 gRPC-Web 代理 | 原生支持 |
| 适用场景 | 内部服务间高性能通信 | 对外 API、前后端交互 |

---

## 8. 链路追踪

### 8.1 核心概念

```
Trace（整个请求链路）
├── Span A（网关）
│   ├── Span B（用户服务）
│   │   └── Span D（数据库查询）
│   └── Span C（订单服务）
│       ├── Span E（支付服务）
│       └── Span F（库存服务）
```

- **Trace**：一次完整请求的唯一标识（TraceID）
- **Span**：一次服务调用的标识（SpanID），包含开始/结束时间、标签、日志
- **Parent Span**：上游调用方的 Span，形成调用树

### 8.2 SkyWalking（推荐）

**优势**：Java Agent 无侵入接入，无需修改业务代码

```
# 启动方式：添加 JVM 参数
java -javaagent:/path/to/skywalking-agent.jar \
     -Dskywalking.agent.service_name=user-service \
     -Dskywalking.collector.backend_service=127.0.0.1:11800 \
     -jar user-service.jar
```

**SkyWalking 架构**：
```
┌──────────┐     ┌──────────┐     ┌──────────┐
│ Service A │     │ Service B │     │ Service C │
│ (Agent)   │     │ (Agent)   │     │ (Agent)   │
└────┬─────┘     └────┬─────┘     └────┬─────┘
     │                │                │
     └────────────────┼────────────────┘
                      │ gRPC 上报
                      ▼
               ┌────────────┐
               │   OAP Server  │  数据分析和聚合
               │ (Collector)    │
               └──────┬─────┘
                      │
                      ▼
               ┌────────────┐
               │   Storage    │  ElasticSearch / MySQL / H2
               └──────┬─────┘
                      │
                      ▼
               ┌────────────┐
               │   UI Console │  可视化面板
               └────────────┘
```

### 8.3 SkyWalking vs Zipkin vs Jaeger

| 特性 | SkyWalking | Zipkin | Jaeger |
|------|-----------|--------|--------|
| 接入方式 | Java Agent（无侵入） | SDK（需要改代码） | SDK/Agent |
| 语言支持 | Java 为主，多语言支持 | 多语言 | 多语言 |
| 存储后端 | ES/MySQL/H2/TiDB | ES/MySQL/Cassandra | ES/Cassandra/Kafka |
| 告警功能 | 内置 | 需要第三方 | 有限 |
| 服务拓扑 | 自动生成 | 需额外配置 | 自动生成 |
| 性能影响 | 低（Agent 高效） | 中 | 低 |
| 社区活跃 | Apache 顶级项目 | 活跃 | CNCF 毕业项目 |

---

## 9. 服务拆分原则与 DDD

### 9.1 服务拆分原则

1. **单一职责原则**：每个服务只负责一个业务功能
2. **按业务领域拆分**：用户服务、订单服务、支付服务、商品服务
3. **高内聚低耦合**：服务内部高度相关，服务之间尽量独立
4. **数据库独立**：每个服务有自己的数据库，避免直接访问其他服务的数据库
5. **接口稳定原则**：服务间的 API 接口应保持向后兼容
6. **适度拆分**：不要过度拆分，2-Pizza 团队能维护 2-3 个服务为宜

### 9.2 DDD（领域驱动设计）核心概念

```
┌─────────────────────────────────────────────────┐
│                  战略设计                          │
│                                                   │
│  ┌──────────────┐    ┌──────────────┐           │
│  │ 限界上下文 A    │←→│ 限界上下文 B    │           │
│  │ (用户域)       │    │ (订单域)       │           │
│  │               │    │               │           │
│  │ 上下文映射      │    │               │           │
│  │ (Context Map)  │    │               │           │
│  └──────────────┘    └──────────────┘           │
├─────────────────────────────────────────────────┤
│                  战术设计                          │
│                                                   │
│  ┌──────────────────────────────────┐           │
│  │ 聚合（Aggregate）                   │           │
│  │  ┌─────────────┐                 │           │
│  │  │ 聚合根        │ ←── 外部只能通过   │           │
│  │  │(Aggregate Root)│     聚合根访问    │           │
│  │  └──────┬──────┘                 │           │
│  │         │                        │           │
│  │  ┌──────┴──────┐ ┌────────┐    │           │
│  │  │  实体(Entity) │ │值对象(VO)│    │           │
│  │  └─────────────┘ └────────┘    │           │
│  └──────────────────────────────────┘           │
│                                                   │
│  领域事件(Domain Event)：跨聚合/跨服务通信           │
│  领域服务(Domain Service)：不属于任何实体的业务逻辑    │
│  仓储(Repository)：聚合的持久化接口                  │
│  应用服务(Application Service)：编排领域对象完成用例   │
└─────────────────────────────────────────────────┘
```

### 9.3 DDD 核心概念详解

| 概念 | 说明 | 示例 |
|------|------|------|
| 限界上下文（Bounded Context） | 业务边界，一个微服务对应一个限界上下文 | 用户上下文、订单上下文 |
| 聚合（Aggregate） | 一组相关对象的集合，保证事务一致性 | 订单聚合（订单 + 订单项） |
| 聚合根（Aggregate Root） | 聚合的入口点，外部只能通过聚合根操作 | Order 是订单聚合的聚合根 |
| 实体（Entity） | 有唯一标识的领域对象 | User、Order |
| 值对象（Value Object） | 无唯一标识，通过属性值判断相等 | Money、Address |
| 领域事件（Domain Event） | 领域中发生的重要事情 | OrderCreatedEvent |
| 领域服务（Domain Service） | 不属于任何实体的业务逻辑 | TransferService（转账） |
| 仓储（Repository） | 聚合持久化的抽象接口 | OrderRepository |
| 应用服务（Application Service） | 编排领域对象完成用例 | OrderApplicationService |

### 9.4 DDD 分层架构

```
┌────────────────────┐
│  用户接口层(Interface) │  Controller、DTO
├────────────────────┤
│  应用服务层(Application)│  用例编排、事务管理
├────────────────────┤
│  领域层(Domain)       │  实体、值对象、聚合、领域服务、领域事件
├────────────────────┤
│  基础设施层(Infrastructure)│  数据库、MQ、外部服务调用
└────────────────────┘
依赖方向：上层依赖下层，领域层不依赖任何外部层（依赖倒置）
```

---

## 10. 数据一致性方案

### 10.1 分布式事务方案对比

| 方案 | 一致性 | 性能 | 复杂度 | 适用场景 |
|------|--------|------|--------|---------|
| 2PC（两阶段提交） | 强一致 | 低 | 中 | 短事务、数据库层面 |
| TCC | 强一致 | 中 | 高 | 资金交易等高一致性场景 |
| Saga | 最终一致 | 高 | 中 | 长事务、跨服务流程 |
| 本地消息表 | 最终一致 | 高 | 低 | 异步通知、最终一致场景 |
| 事务消息（RocketMQ） | 最终一致 | 高 | 低 | 消息驱动的最终一致 |
| Seata AT 模式 | 准强一致 | 中 | 低 | 中小规模微服务 |

### 10.2 Saga 模式

```
正向操作：
  创建订单 → 扣减库存 → 扣减余额 → 订单确认

补偿操作（某步失败时反向执行）：
  取消订单 ← 恢复库存 ← 恢复余额

编排式（Orchestration）：中央协调器编排各步骤
事件式（Choreography）：各服务监听事件自行响应
```

### 10.3 本地消息表

```
1. 业务操作 + 写消息表（同一个本地事务）
2. 定时任务扫描消息表，发送消息到 MQ
3. 消费者消费消息，执行业务逻辑
4. 消费成功后回调更新消息状态
5. 发送失败时定时重试（需要消费者幂等）
```

---

## 11. 容器化部署

### 11.1 Docker + Kubernetes

```dockerfile
# Dockerfile
FROM eclipse-temurin:17-jre-alpine
WORKDIR /app
COPY target/user-service.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar", \
    "--spring.profiles.active=prod"]
```

```yaml
# Kubernetes Deployment
apiVersion: apps/v1
kind: Deployment
metadata:
  name: user-service
spec:
  replicas: 3
  selector:
    matchLabels:
      app: user-service
  template:
    metadata:
      labels:
        app: user-service
    spec:
      containers:
        - name: user-service
          image: registry.example.com/user-service:v1.0
          ports:
            - containerPort: 8080
          env:
            - name: SPRING_PROFILES_ACTIVE
              value: "prod"
          resources:
            requests:
              memory: "256Mi"
              cpu: "200m"
            limits:
              memory: "512Mi"
              cpu: "500m"
          livenessProbe:
            httpGet:
              path: /actuator/health/liveness
              port: 8080
            initialDelaySeconds: 30
          readinessProbe:
            httpGet:
              path: /actuator/health/readiness
              port: 8080
            initialDelaySeconds: 20
```

---

## 12. 高频面试题

### Q1：微服务的优缺点？什么场景适合用微服务？
优点：独立部署扩展、技术栈自由、团队自治、故障隔离。缺点：分布式复杂性、运维成本高、数据一致性难。适合场景：业务复杂度高、团队规模大、需要独立扩展的互联网应用。不适合：初创小项目、团队人数少。

### Q2：Spring Cloud 有哪些核心组件？各自的作用？
注册中心（Nacos/Eureka）：服务注册与发现。API 网关（Spring Cloud Gateway）：路由、限流、鉴权。负载均衡（LoadBalancer）：客户端负载均衡。服务调用（OpenFeign）：声明式 HTTP 客户端。配置中心（Nacos Config）：动态配置管理。熔断降级（Sentinel）：服务保护。链路追踪（SkyWalking）：分布式追踪。

### Q3：服务注册与发现的原理？Nacos 的 AP 和 CP 模式？
服务启动时向注册中心注册，消费者从注册中心获取服务列表并本地缓存。Nacos AP 模式用于临时实例（心跳检测，Distro 协议），适合常规微服务。CP 模式用于持久实例（服务端主动检查，Raft 协议），适合数据库等基础设施。

### Q4：熔断器的工作原理和三种状态？
三种状态：Closed（正常通过）、Open（熔断，直接走降级逻辑）、Half-Open（放行部分探测请求）。工作原理：统计窗口内的失败率，超过阈值则从 Closed 转为 Open；超时后转为 Half-Open 进行探测；探测成功恢复 Closed，失败则继续 Open。

### Q5：微服务间如何保证数据一致性？
强一致：2PC、TCC、Seata AT 模式。最终一致：Saga 模式（正向操作 + 补偿操作）、本地消息表（本地事务 + 定时发送）、事务消息（RocketMQ）。大多数场景使用最终一致性即可，关键资金场景使用 TCC。

### Q6：如何进行服务拆分？有什么原则？
按业务领域拆分（DDD 限界上下文），遵循单一职责、高内聚低耦合、数据库独立、接口稳定原则。建议从单体出发逐步拆分，不要一开始就过度拆分。

### Q7：API 网关的作用？处理了哪些横切关注点？
统一入口、路由转发、负载均衡、认证鉴权、限流熔断、日志监控、跨域处理、协议转换、灰度发布。Spring Cloud Gateway 基于 WebFlux 异步非阻塞，通过 Predicate 匹配路由、Filter 链处理请求。

### Q8：Sentinel 和 Hystrix 的区别？
Sentinel 信号量隔离，支持流控/熔断/热点/系统规则，有完善的 Dashboard，支持动态规则配置。Hystrix 支持线程池和信号量隔离，但已停止维护。推荐使用 Sentinel。

### Q9：什么是 DDD？在微服务中如何应用？
DDD 是领域驱动设计，核心是限界上下文（对应微服务边界）、聚合（保证事务一致性）、聚合根（外部访问入口）、领域事件（跨服务通信）。通过限界上下文划分微服务边界，通过领域事件实现服务间的松耦合通信。

### Q10：OpenFeign 的工作原理？
基于 JDK 动态代理。@FeignClient 标注的接口在启动时被扫描，为每个接口创建代理对象。调用方法时，代理根据注解信息（URL、请求方法、参数）构造 HTTP 请求，通过负载均衡选择实例后发起调用。支持拦截器、超时配置、熔断降级。

### Q11：链路追踪的原理？TraceID 和 SpanID 的作用？
在请求入口生成全局唯一 TraceID，每经过一个服务生成一个 SpanID。TraceID 标识整个请求链路，SpanID 标识单个服务调用，通过 Parent SpanID 建立调用父子关系。SkyWalking 通过 Java Agent 无侵入地拦截方法调用，自动传播上下文和上报数据。

### Q12：微服务的灰度发布方案？
方案一：通过网关路由规则，按用户标签/IP/百分比分流到新版本。方案二：Nacos 权重配置，新版本实例设置较低权重逐步增加。方案三：Kubernetes 的 Canary Deployment，配合 Istio Service Mesh 实现精细流量控制。
