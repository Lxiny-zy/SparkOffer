# Spring WebFlux 响应式编程

## 1. 概览

### 定位
Spring 5 引入的响应式 Web 框架，基于 **Reactive Streams** 规范和 **Project Reactor**。与传统 Spring MVC 并列，提供**非阻塞、异步、函数式**的 Web 编程模型。

### 为什么需要
- **高并发低延迟**：少量线程处理大量连接
- **资源利用高**：IO 等待不占线程
- **响应式全链路**：数据库（R2DBC）、消息、HTTP 客户端全非阻塞
- **流式数据**：天然适合 SSE、WebSocket、LLM 流式

### 适用场景
- 高并发 API 网关
- 实时消息推送
- LLM 流式输出
- 微服务间 IO 密集调用
- 长连接应用

### 不适用
- CPU 密集计算
- 简单 CRUD（MVC 更直观）
- 团队不熟悉响应式思维

---

## 2. Reactor 核心

### Mono 和 Flux
- **Mono<T>**：0 或 1 个元素的异步序列
- **Flux<T>**：0 到 N 个元素的异步序列

```java
Mono<String> mono = Mono.just("hello");
Flux<Integer> flux = Flux.just(1, 2, 3, 4, 5);
Flux<String> range = Flux.range(1, 10).map(i -> "item " + i);
```

### 创建方式

```java
Mono.just("x")                         // 已有值
Mono.empty()                           // 空
Mono.error(new RuntimeException())     // 错误
Mono.fromCallable(() -> fetch())       // 同步调用包装
Mono.fromFuture(future)                // CompletableFuture
Mono.defer(() -> Mono.just(new Date()))// 每次订阅重新求值

Flux.just(1, 2, 3)
Flux.fromIterable(list)
Flux.fromStream(stream)
Flux.interval(Duration.ofSeconds(1))   // 定时器
Flux.generate(sink -> sink.next(...))  // 同步生成
Flux.create(sink -> ...)               // 异步生成
```

### 操作符

**转换**
```java
.map(x -> x * 2)
.flatMap(x -> Mono.just(x + 1))   // 异步转换 + 展开
.concatMap(x -> ...)               // 保持顺序的 flatMap
```

**过滤**
```java
.filter(x -> x > 0)
.distinct()
.take(10)
.skip(5)
```

**聚合**
```java
.reduce((a, b) -> a + b)
.count()
.collectList()
.collectMap(Person::id)
```

**组合**
```java
Flux.merge(flux1, flux2)       // 合并（交错）
Flux.concat(flux1, flux2)       // 顺序拼接
Flux.zip(flux1, flux2)          // 配对
flux1.combineLatest(flux2)      // 各自最新值组合
```

**错误处理**
```java
.onErrorReturn(fallback)
.onErrorResume(e -> Mono.just(fallback))
.onErrorMap(e -> new BusinessException(e))
.retry(3)
.retryWhen(Retry.backoff(3, Duration.ofSeconds(1)))
```

**副作用**
```java
.doOnNext(x -> log.info("{}", x))
.doOnError(e -> log.error("error", e))
.doOnSuccess(x -> ...)
.doOnComplete(() -> ...)
.doFinally(sig -> cleanup())
```

**调度器**
```java
.subscribeOn(Schedulers.boundedElastic())  // 上游执行在
.publishOn(Schedulers.parallel())           // 下游执行在
```

### 常用 Scheduler
- `Schedulers.immediate()`：当前线程
- `Schedulers.single()`：单线程
- `Schedulers.parallel()`：CPU 密集（N 个线程 = CPU 核数）
- `Schedulers.boundedElastic()`：IO 密集（最多 10×CPU 线程）

### 冷流 vs 热流
- **冷流**：每个订阅者独立执行（默认）
- **热流**：所有订阅者共享数据流（`.share()`、`Sinks`）

---

## 3. WebFlux 基础

### 依赖

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-webflux</artifactId>
</dependency>
```

**注意**：不能同时引入 `starter-web`（MVC），只能二选一。

### 注解式 Controller

```java
@RestController
@RequestMapping("/users")
public class UserController {
    @Autowired UserService userService;

    @GetMapping("/{id}")
    public Mono<User> getUser(@PathVariable Long id) {
        return userService.findById(id);
    }

    @GetMapping
    public Flux<User> listUsers() {
        return userService.findAll();
    }

    @PostMapping
    public Mono<User> create(@RequestBody Mono<CreateReq> req) {
        return req.flatMap(userService::create);
    }
}
```

返回 `Mono<T>` / `Flux<T>`，框架自动订阅并响应。

### 函数式 Router（推荐大型应用）

```java
@Configuration
public class UserRouter {
    @Bean
    public RouterFunction<ServerResponse> userRoutes(UserHandler handler) {
        return RouterFunctions.route()
            .GET("/users/{id}", handler::getUser)
            .GET("/users", handler::listUsers)
            .POST("/users", handler::createUser)
            .build();
    }
}

@Component
public class UserHandler {
    @Autowired UserService userService;

    public Mono<ServerResponse> getUser(ServerRequest req) {
        Long id = Long.parseLong(req.pathVariable("id"));
        return userService.findById(id)
            .flatMap(u -> ServerResponse.ok().bodyValue(u))
            .switchIfEmpty(ServerResponse.notFound().build());
    }

    public Mono<ServerResponse> listUsers(ServerRequest req) {
        return ServerResponse.ok().body(userService.findAll(), User.class);
    }

    public Mono<ServerResponse> createUser(ServerRequest req) {
        return req.bodyToMono(CreateReq.class)
            .flatMap(userService::create)
            .flatMap(u -> ServerResponse.created(URI.create("/users/" + u.id())).bodyValue(u));
    }
}
```

---

## 4. WebClient（响应式 HTTP 客户端）

### 替代 RestTemplate

```java
WebClient client = WebClient.builder()
    .baseUrl("https://api.example.com")
    .defaultHeader("Accept", "application/json")
    .build();

// GET
Mono<User> user = client.get()
    .uri("/users/{id}", 1)
    .retrieve()
    .bodyToMono(User.class);

// POST
Mono<User> created = client.post()
    .uri("/users")
    .bodyValue(createReq)
    .retrieve()
    .bodyToMono(User.class);

// 错误处理
.retrieve()
.onStatus(status -> status.is4xxClientError(),
    resp -> Mono.error(new BadRequestException()))
.bodyToMono(User.class);
```

### 配置连接池

```java
HttpClient httpClient = HttpClient.create(
    ConnectionProvider.builder("my-pool")
        .maxConnections(500)
        .maxIdleTime(Duration.ofSeconds(60))
        .build())
    .responseTimeout(Duration.ofSeconds(10));

WebClient client = WebClient.builder()
    .clientConnector(new ReactorClientHttpConnector(httpClient))
    .build();
```

---

## 5. 响应式数据访问

### R2DBC（响应式 SQL）

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-r2dbc</artifactId>
</dependency>
<dependency>
    <groupId>io.asyncer</groupId>
    <artifactId>r2dbc-mysql</artifactId>
</dependency>
```

```yaml
spring.r2dbc:
  url: r2dbc:mysql://localhost:3306/mydb
  username: root
  password: ...
```

```java
public interface UserRepository extends ReactiveCrudRepository<User, Long> {
    Flux<User> findByCityOrderByAge(String city);

    @Query("SELECT * FROM users WHERE age > :age")
    Flux<User> findOlderThan(int age);
}

@Service
public class UserService {
    @Autowired UserRepository repo;

    public Mono<User> findById(Long id) {
        return repo.findById(id);
    }
}
```

### MongoDB Reactive

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-mongodb-reactive</artifactId>
</dependency>
```

```java
public interface OrderRepository extends ReactiveMongoRepository<Order, String> {
    Flux<Order> findByUserId(Long userId);
}
```

### Redis Reactive

```java
@Autowired ReactiveStringRedisTemplate redis;

redis.opsForValue().set("key", "value").subscribe();
redis.opsForValue().get("key").subscribe(System.out::println);
```

**注意**：传统 JPA 和 JDBC 是**阻塞**的，在 WebFlux 里用会破坏响应式特性。必须用 R2DBC / Mongo Reactive / Redis Reactive。

---

## 6. 流式传输

### SSE（Server-Sent Events）

```java
@GetMapping(value = "/events", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
public Flux<ServerSentEvent<String>> events() {
    return Flux.interval(Duration.ofSeconds(1))
        .map(i -> ServerSentEvent.<String>builder()
            .id(String.valueOf(i))
            .event("update")
            .data("tick " + i)
            .build());
}
```

### 流式响应 JSON

```java
@GetMapping(value = "/stream", produces = MediaType.APPLICATION_NDJSON_VALUE)
public Flux<User> stream() {
    return userService.findAll();  // 每条 User 以 NDJSON 推送
}
```

### WebSocket

```java
@Component
public class ChatHandler implements WebSocketHandler {
    @Override
    public Mono<Void> handle(WebSocketSession session) {
        return session.send(
            session.receive()
                .map(WebSocketMessage::getPayloadAsText)
                .map(msg -> session.textMessage("Echo: " + msg))
        );
    }
}

@Configuration
public class WsConfig {
    @Bean
    public HandlerMapping wsMapping(ChatHandler handler) {
        Map<String, WebSocketHandler> map = Map.of("/ws/chat", handler);
        SimpleUrlHandlerMapping mapping = new SimpleUrlHandlerMapping(map, -1);
        return mapping;
    }
}
```

---

## 7. 响应式安全

Spring Security 5+ 支持响应式：

```java
@EnableWebFluxSecurity
public class SecurityConfig {
    @Bean
    public SecurityWebFilterChain filter(ServerHttpSecurity http) {
        return http
            .authorizeExchange(a -> a
                .pathMatchers("/public/**").permitAll()
                .anyExchange().authenticated())
            .oauth2ResourceServer(o -> o.jwt(Customizer.withDefaults()))
            .build();
    }

    @Bean
    public ReactiveUserDetailsService userDetailsService() {
        // 返回 Mono<UserDetails>
    }
}
```

---

## 8. 背压（Backpressure）

### 概念
生产者产生数据过快，消费者处理不过来 → 背压。Reactor 通过 **Reactive Streams** 规范支持。

### 策略

```java
Flux.create(sink -> {
    for (int i = 0; i < 1_000_000; i++) sink.next(i);
    sink.complete();
}, FluxSink.OverflowStrategy.BUFFER)  // 缓冲（默认，OOM 风险）

FluxSink.OverflowStrategy.DROP         // 丢弃新元素
FluxSink.OverflowStrategy.LATEST       // 保留最新
FluxSink.OverflowStrategy.ERROR        // 抛异常
FluxSink.OverflowStrategy.IGNORE       // 忽略
```

### onBackpressureXxx

```java
Flux.range(1, 1_000_000)
    .onBackpressureBuffer(1000, dropped -> log.warn("dropped"))
    .subscribe(...);
```

---

## 9. 常见陷阱

### 陷阱 1：阻塞调用
在响应式链中调用阻塞代码（JDBC、`Thread.sleep`）会阻塞 event-loop。
**解法**：`.subscribeOn(Schedulers.boundedElastic())` 切到弹性线程池。

### 陷阱 2：忘记订阅
```java
Mono<User> mono = userService.save(user);  // 没 subscribe 永远不执行！
```
响应式是**声明式**，必须订阅才触发。Controller 返回 Mono/Flux 时框架会自动订阅。

### 陷阱 3：ThreadLocal 失效
响应式切换线程，ThreadLocal 存的值（Session、MDC）丢失。
**解法**：用 `Reactor Context`：
```java
Mono.deferContextual(ctx -> {
    String user = ctx.get("user");
    return doSomething(user);
}).contextWrite(Context.of("user", "alice"));
```

### 陷阱 4：顺序错误
`map/flatMap/concatMap` 顺序差别大，异步 flatMap 可能乱序。

### 陷阱 5：调试难
链式调用堆栈不清晰。
**解法**：
- `.log()` 打印所有事件
- `Hooks.onOperatorDebug()` 增强栈
- `BlockHound` 检测阻塞调用

### 陷阱 6：内存泄漏
冷流订阅后未取消，或热流 backpressure 不当。

---

## 10. 性能与调优

### vs MVC

| 场景 | WebFlux | MVC |
|------|---------|-----|
| 少量请求，短响应 | 性能相当 | 编程简单 |
| 大量并发 IO | 显著优势 | 线程耗尽 |
| CPU 密集 | 无优势 | 相当 |
| 学习曲线 | 陡 | 平缓 |

典型优势场景：
- 1 万并发长连接（WebSocket 聊天）
- 网关（大量出站 HTTP 调用）
- SSE/流式（LLM 输出）

### 调优
- **Netty 线程数**：`-Dreactor.netty.ioWorkerCount=N`（默认 = CPU 核数）
- **背压策略**：根据场景选
- **超时**：WebClient 要显式配
- **连接池**：下游 HTTP 连接池大小

### BlockHound（开发环境）
```xml
<dependency>
    <groupId>io.projectreactor.tools</groupId>
    <artifactId>blockhound</artifactId>
</dependency>
```
```java
BlockHound.install();  // 检测事件循环上的阻塞调用
```

---

## 11. 实战：LLM 流式输出

```java
@RestController
public class ChatController {
    @Autowired ChatClient chatClient;

    @GetMapping(value = "/chat", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<ServerSentEvent<String>> chat(@RequestParam String q) {
        return chatClient.prompt()
            .user(q)
            .stream()
            .content()
            .map(token -> ServerSentEvent.<String>builder()
                .data(token)
                .build())
            .concatWith(Mono.just(
                ServerSentEvent.<String>builder().event("done").data("").build()
            ))
            .onErrorResume(e -> Flux.just(
                ServerSentEvent.<String>builder().event("error").data(e.getMessage()).build()
            ));
    }
}
```

前端：
```javascript
const es = new EventSource(`/chat?q=${encodeURIComponent(query)}`);
es.onmessage = (e) => appendToken(e.data);
es.addEventListener("done", () => es.close());
```

---

## 12. 何时不要用 WebFlux

**直接不用**：
- 团队完全不熟响应式
- CRUD 为主的简单应用
- 需要用大量阻塞库（老 JDBC、阻塞 SDK）

**混合使用**：
- 部分服务用 WebFlux（网关、流式接口）
- 其他仍用 MVC

---

## 面试高频问题

**Q1：WebFlux 和 Spring MVC 区别？**

| 维度 | MVC | WebFlux |
|------|-----|---------|
| 模型 | Servlet 阻塞 | Reactive 非阻塞 |
| 线程 | 每请求一线程 | 少量线程 + event loop |
| 容器 | Tomcat（默认） | Netty（默认） |
| 返回 | T | Mono<T>/Flux<T> |
| 适合 | 通用 | 高并发 IO、流式 |

同一应用只能二选一（不能混）。

**Q2：Mono 和 Flux 区别？**

- **Mono<T>**：0 或 1 个元素
- **Flux<T>**：0..N 个元素

都遵循 Reactive Streams，都是异步、惰性（订阅才执行）。

**Q3：响应式为什么更高并发？**

传统模型：一请求一线程，IO 等待时线程被占用；线程数上限限制并发。

响应式：少量 event-loop 线程处理所有连接，IO 等待时线程去处理其他请求。1000 线程可支持 10 万+并发连接。

关键：**不阻塞线程**。

**Q4：响应式要求全链路非阻塞？**

是的。任何阻塞调用（JDBC、Thread.sleep、BlockingQueue）在事件循环上会卡死整个线程。

解法：
- 用响应式驱动（R2DBC、Reactive Mongo）
- 必须阻塞时切到 `boundedElastic` 线程池
- 开发时用 BlockHound 检测

**Q5：背压是什么？**

生产者速度 > 消费者速度时的流量控制机制。Reactor 通过 Reactive Streams 规范，订阅者可按需 `request(n)` 拉取数据。

策略：buffer（缓冲）、drop（丢弃）、latest（保留最新）、error。

**Q6：flatMap vs map vs concatMap？**

- **map**：同步转换 `T → R`
- **flatMap**：异步转换 `T → Mono<R>/Flux<R>`，**可能乱序**
- **concatMap**：同 flatMap 但**保持顺序**
- **switchMap**：切换到最新的 Flux

```java
// map：1 → 2 → 3
flux.map(x -> x + 1)

// flatMap：异步调用，乱序
flux.flatMap(id -> userClient.getUser(id))

// concatMap：异步但有序
flux.concatMap(id -> userClient.getUser(id))
```

**Q7：如何在响应式里传递 ThreadLocal？**

ThreadLocal 绑定线程，响应式切线程会丢。用 **Reactor Context**：

```java
Mono.deferContextual(ctx -> {
    String user = ctx.get("user");
    return process(user);
}).contextWrite(Context.of("user", "alice"));
```

Spring Security Reactive 就是这么传 SecurityContext 的。

**Q8：WebClient 和 RestTemplate 区别？**

- **RestTemplate**：同步、阻塞，已进入维护模式
- **WebClient**：异步、响应式，推荐新项目使用

WebClient 也能在 MVC 里用（`.block()` 转同步）。

**Q9：什么时候不该用 WebFlux？**

- 团队不熟（学习曲线陡，调试难）
- 简单 CRUD 应用（收益小）
- 依赖大量阻塞库（无法全链路非阻塞）
- CPU 密集任务（响应式无优势）

**Q10：流式 LLM 输出如何实现？**

```java
@GetMapping(value = "/chat", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
public Flux<ServerSentEvent<String>> chat(@RequestParam String q) {
    return chatClient.stream(q)
        .map(token -> ServerSentEvent.builder(token).build());
}
```

前端用 EventSource 接收。核心：
- 返回 Flux 触发 SSE
- LLM SDK 返回 Flux/Stream
- 错误处理用 `onErrorResume`
- 完成发 `done` 事件
