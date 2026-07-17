# Java 调用大模型最佳实践

## 1. 调用方式选择

### 裸调 SDK / HTTP
最底层，控制最细但样板代码多：
```java
HttpClient http = HttpClient.newHttpClient();
var body = """
{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}
""";
HttpRequest req = HttpRequest.newBuilder()
    .uri(URI.create("https://api.openai.com/v1/chat/completions"))
    .header("Authorization", "Bearer " + apiKey)
    .header("Content-Type", "application/json")
    .POST(BodyPublishers.ofString(body))
    .build();
HttpResponse<String> resp = http.send(req, BodyHandlers.ofString());
```

**适用**：极简场景、学习原理。**生产不推荐**——要自己写流式、重试、错误处理。

### 官方 SDK
| 提供商 | Java SDK |
|--------|----------|
| OpenAI | `openai-java`（官方 2024+）、`simple-openai` |
| Anthropic | `anthropic-java`（官方） |
| Azure OpenAI | `azure-ai-openai` |
| AWS Bedrock | AWS SDK v2 |
| Google Vertex AI | `google-cloud-vertexai` |

### 框架
Spring AI / LangChain4j：**生产首选**。处理 SDK 差异、工具调用循环、记忆、RAG、重试。

---

## 2. 客户端配置要点

### 超时
```java
ChatClient.builder(model)
    .defaultRequestOptions(o -> o.timeout(Duration.ofSeconds(60)))
    .build();
```

**关键**：
- Connect timeout: 10s
- Read timeout: 60s+ (长生成可能超 30s)
- 流式模式下**不要 idle timeout 过短**，否则长 pause 后断开

### 连接池
底层 HttpClient 复用：
```java
OkHttpClient client = new OkHttpClient.Builder()
    .connectionPool(new ConnectionPool(50, 5, TimeUnit.MINUTES))
    .connectTimeout(10, TimeUnit.SECONDS)
    .readTimeout(120, TimeUnit.SECONDS)
    .build();
```

### 代理
企业内网：
```java
OkHttpClient client = new OkHttpClient.Builder()
    .proxy(new Proxy(Proxy.Type.HTTP, new InetSocketAddress("proxy.company", 8080)))
    .build();
```

---

## 3. 流式输出

### SSE 到前端（Spring WebFlux）

```java
@RestController
class ChatController {
    @GetMapping(value = "/chat", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    Flux<ServerSentEvent<String>> chat(@RequestParam String q) {
        return chatClient.prompt()
            .user(q)
            .stream()
            .content()
            .map(text -> ServerSentEvent.<String>builder()
                .data(text)
                .build())
            .concatWith(Flux.just(ServerSentEvent.<String>builder()
                .event("done").data("").build()));
    }
}
```

### WebSocket（双向）

```java
@Component
class ChatWebSocket extends TextWebSocketHandler {
    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage msg) {
        chatClient.prompt()
            .user(msg.getPayload())
            .stream()
            .content()
            .subscribe(
                chunk -> session.sendMessage(new TextMessage(chunk)),
                err -> session.close(CloseStatus.SERVER_ERROR),
                () -> session.sendMessage(new TextMessage("[DONE]"))
            );
    }
}
```

### 线程模型陷阱
- **不要**在流式回调中做阻塞 IO（数据库、同步 HTTP）
- 流式返回的每个 chunk 在 I/O 线程，重逻辑要 `subscribeOn(Schedulers.boundedElastic())`
- **Servlet 阻塞模式**：用 `SseEmitter`，搭配独立线程池

```java
@GetMapping("/chat-sse")
public SseEmitter chat(@RequestParam String q) {
    SseEmitter emitter = new SseEmitter(0L);
    executor.submit(() -> {
        chatClient.prompt().user(q).stream().content()
            .subscribe(
                chunk -> emitter.send(chunk),
                emitter::completeWithError,
                emitter::complete
            );
    });
    return emitter;
}
```

---

## 4. 重试与幂等

### Resilience4j

```xml
<dependency>
    <groupId>io.github.resilience4j</groupId>
    <artifactId>resilience4j-spring-boot3</artifactId>
</dependency>
```

```yaml
resilience4j.retry:
  instances:
    llm:
      maxAttempts: 3
      waitDuration: 1s
      enableExponentialBackoff: true
      exponentialBackoffMultiplier: 2
      retryExceptions:
        - java.net.SocketTimeoutException
        - org.springframework.web.client.ResourceAccessException
```

```java
@Retry(name = "llm")
public String callLlm(String q) {
    return chatClient.prompt().user(q).call().content();
}
```

### 幂等性
**LLM 调用本身不幂等**（非零 temperature 每次不同）。如果重试：
- 保证**用户请求幂等 key**，服务端去重（同 key 多次只调一次 LLM）
- 业务层缓存结果，重试时命中缓存

### 哪些可重试？
| 错误 | 可重试？ |
|------|----------|
| 429 Too Many Requests | ✅ 指数退避 |
| 500 / 502 / 503 / 504 | ✅ |
| Connection timeout | ✅ |
| 401 / 403 | ❌（认证/权限） |
| 400 | ❌（参数错） |
| 内容策略拒答 | ❌（改 prompt） |

---

## 5. 限流

### Spring Cloud Gateway + Redis

```java
@Bean
public RedisRateLimiter redisRateLimiter() {
    return new RedisRateLimiter(100, 200, 1);  // 100/s, burst 200
}

@Bean
public RouteLocator routes(RouteLocatorBuilder b) {
    return b.routes()
        .route("llm", r -> r.path("/api/chat/**")
            .filters(f -> f.requestRateLimiter(c -> c.setRateLimiter(redisRateLimiter())))
            .uri("lb://chat-service"))
        .build();
}
```

### Resilience4j RateLimiter

```java
@RateLimiter(name = "llm", fallbackMethod = "fallback")
public String chat(...) { ... }

public String fallback(String q, RequestNotPermitted ex) {
    return "服务繁忙，请稍后再试";
}
```

### 按用户/模型维度
```java
// Lua + Redis 按 userId 限流
String key = "rl:" + userId;
Long allowed = redisTemplate.execute(rateLimitScript, List.of(key),
    "10",  // max requests
    "60"   // window seconds
);
if (allowed == 0) throw new RateLimitException();
```

---

## 6. 熔断降级

```java
@CircuitBreaker(name = "llm", fallbackMethod = "localFallback")
public String chat(String q) {
    return primaryModel.generate(q);
}

public String localFallback(String q, Throwable t) {
    log.warn("LLM failed, using local model", t);
    return localModel.generate(q);
}
```

**降级链**：GPT-4o → Claude → 本地 Qwen → 预设模板。

---

## 7. 异步化

### CompletableFuture

```java
public CompletableFuture<String> chatAsync(String q) {
    return CompletableFuture.supplyAsync(
        () -> chatClient.prompt().user(q).call().content(),
        llmExecutor
    );
}
```

### Reactor

```java
public Mono<String> chat(String q) {
    return Mono.fromCallable(() -> chatClient.prompt().user(q).call().content())
        .subscribeOn(Schedulers.boundedElastic());
}
```

### 专用线程池

```java
@Bean("llmExecutor")
public ThreadPoolTaskExecutor llmExecutor() {
    ThreadPoolTaskExecutor exec = new ThreadPoolTaskExecutor();
    exec.setCorePoolSize(10);
    exec.setMaxPoolSize(50);
    exec.setQueueCapacity(200);
    exec.setThreadNamePrefix("llm-");
    exec.setRejectedExecutionHandler(new ThreadPoolExecutor.CallerRunsPolicy());
    return exec;
}
```

**不要用 `commonPool`**：LLM 调用长时间阻塞，会耗尽 ForkJoinPool。

---

## 8. Token 计数与预算

### JTokkit（OpenAI Tokenizer）

```xml
<dependency>
    <groupId>com.knuddels</groupId>
    <artifactId>jtokkit</artifactId>
</dependency>
```

```java
EncodingRegistry registry = Encodings.newDefaultEncodingRegistry();
Encoding enc = registry.getEncodingForModel(ModelType.GPT_4O).get();
int tokens = enc.countTokens("你好世界");
```

### 预算检查

```java
int budget = 8000;
int promptTokens = countTokens(messages);
if (promptTokens > budget) {
    messages = truncateMessages(messages, budget);
}
```

### 消息截断策略

```java
List<Message> truncate(List<Message> messages, int maxTokens) {
    // 保留 System Message
    Message system = messages.get(0);
    List<Message> rest = messages.subList(1, messages.size());

    // 从最新开始累计
    List<Message> kept = new ArrayList<>();
    kept.add(system);
    int tokens = countTokens(system);

    List<Message> reversed = new ArrayList<>(rest);
    Collections.reverse(reversed);
    for (Message m : reversed) {
        int t = countTokens(m);
        if (tokens + t > maxTokens) break;
        kept.add(1, m);
        tokens += t;
    }
    return kept;
}
```

---

## 9. 可观测性

### Micrometer + Prometheus

```java
@Component
class LlmMetrics {
    private final MeterRegistry registry;

    public void record(String model, TokenUsage usage, Duration latency) {
        registry.counter("llm.tokens.input",
            "model", model).increment(usage.inputTokenCount());
        registry.counter("llm.tokens.output",
            "model", model).increment(usage.outputTokenCount());
        registry.timer("llm.latency",
            "model", model).record(latency);
    }
}
```

### Trace（Zipkin/Tempo）

```java
import io.opentelemetry.api.trace.Span;

Span span = tracer.spanBuilder("llm.chat")
    .setAttribute("model", "gpt-4o")
    .setAttribute("user_id", userId)
    .startSpan();
try (Scope s = span.makeCurrent()) {
    return chatClient.prompt().user(q).call().content();
} finally {
    span.end();
}
```

### LangFuse/Arize 集成
作为 Listener/Advisor 上报每次调用详情。

---

## 10. 敏感信息与合规

### 输入脱敏

```java
String sanitized = input
    .replaceAll("\\d{11}", "<PHONE>")
    .replaceAll("\\d{15,18}[xX]?", "<ID_CARD>")
    .replaceAll("[\\w.-]+@[\\w.-]+", "<EMAIL>");
```

### 输出过滤

```java
class SafeGuardAdvisor implements CallAdvisor {
    @Override
    public ChatClientResponse adviseCall(ChatClientRequest req, CallAdvisorChain chain) {
        ChatClientResponse resp = chain.nextCall(req);
        String text = resp.chatResponse().getResult().getOutput().getText();
        if (containsSensitive(text)) {
            throw new ContentPolicyException();
        }
        return resp;
    }
}
```

### 审计日志

```java
@Aspect @Component
class LlmAuditAspect {
    @Around("@annotation(auditLlm)")
    public Object audit(ProceedingJoinPoint pjp, AuditLlm auditLlm) throws Throwable {
        String userId = SecurityContext.currentUser();
        long start = System.currentTimeMillis();
        Object result = pjp.proceed();
        auditRepo.save(new AuditRecord(
            userId, pjp.getSignature().toShortString(),
            Arrays.toString(pjp.getArgs()),
            result.toString(),
            System.currentTimeMillis() - start
        ));
        return result;
    }
}
```

---

## 11. 测试

### Mock 模型

```java
ChatLanguageModel mock = new ChatLanguageModel() {
    @Override
    public ChatResponse chat(ChatRequest req) {
        return ChatResponse.builder()
            .aiMessage(AiMessage.from("mocked"))
            .tokenUsage(new TokenUsage(10, 20))
            .build();
    }
};
```

或用 `@MockBean` + Mockito。

### 录制/回放

- 首次真实调用，录制到 fixtures
- 后续测试回放，无需真实 API Key
- 工具：WireMock 录制 HTTP 层

### 集成测试

```java
@SpringBootTest
@Testcontainers
class RagIntegrationTest {
    @Container
    static GenericContainer<?> qdrant = new GenericContainer<>("qdrant/qdrant")
        .withExposedPorts(6333);

    @Test
    void ragWorks() {
        // 真实 Qdrant + Mock LLM
    }
}
```

---

## 12. 部署架构

### 单体
最简，适合小应用：
```
Nginx → Spring Boot 应用 → OpenAI
```

### 网关模式（推荐生产）
```
       ┌─────────────────┐
       │ LLM Gateway     │ ← 统一鉴权/限流/缓存
       │ (Spring Cloud)  │
       └────────┬────────┘
                │
   ┌────────────┼────────────┐
   │            │            │
OpenAI      Claude       本地模型
```

### 独立 AI 服务
```
业务服务 A ──┐
业务服务 B ──┼→ AI 服务（RAG + Agent）→ LLM
业务服务 C ──┘
```
AI 逻辑集中，业务调 RPC。

---

## 13. K8s 部署要点

### HPA 配置
LLM 调用是 IO 密集，不要按 CPU 扩容：
```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: ai-service
spec:
  metrics:
    - type: Pods
      pods:
        metric:
          name: in_flight_llm_requests
        target:
          type: AverageValue
          averageValue: "20"
```

### 优雅关闭
流式请求中的 Pod 不能直接杀：
```yaml
terminationGracePeriodSeconds: 120
```

```java
@PreDestroy
void shutdown() {
    // 等待 in-flight 请求完成
    shutdownExecutor.awaitTermination(60, TimeUnit.SECONDS);
}
```

### Secret 管理
```yaml
env:
  - name: OPENAI_API_KEY
    valueFrom:
      secretKeyRef:
        name: llm-secrets
        key: openai
```

生产用 Vault / AWS Secrets Manager。

---

## 14. 常见坑

1. **同步调用阻塞 Tomcat 线程**：用 WebFlux 或异步化
2. **重试无退避**：瞬间打爆 API
3. **流式用 Servlet 默认 timeout**：30s 生成直接断
4. **Token 计数错**：拿 GPT-4 的 tokenizer 算 Claude 不对
5. **共享 ThreadPool**：LLM 长任务拖垮其他异步
6. **API Key 硬编码**：必须从配置/Secret 读
7. **日志记录完整 Prompt**：可能泄露 PII
8. **错误不分类**：所有错都重试，包括 400
9. **没有 Feature Flag**：新模型/新 Prompt 上线无法秒回滚
10. **忽视成本告警**：一个 bug 可能烧光月度预算

---

## 面试高频问题

**Q1：Java 调用 LLM 主要有哪些方式？**

- **裸 HTTP**：简单但要自己处理流式/重试
- **官方 SDK**：OpenAI/Anthropic/Azure 官方 Java SDK
- **框架**：Spring AI / LangChain4j（推荐）

框架屏蔽多提供商差异、自动处理工具调用循环、记忆管理、RAG，还提供 Spring Boot 集成、Observability。

**Q2：流式输出在 Spring 里如何实现？**

三种：
- **WebFlux + Flux + ServerSentEvent**：响应式，最推荐
- **Servlet + SseEmitter**：传统 Servlet 可用
- **WebSocket**：双向通信，支持客户端中断

要点：独立线程池、长 timeout、心跳、前端 EventSource 接收。

**Q3：LLM 调用的重试策略？**

- **可重试**：429、5xx、超时、连接错误
- **不可重试**：400、401、403、内容策略拒答
- **策略**：指数退避 + 抖动，最多 3-5 次
- **工具**：Resilience4j `@Retry` 注解
- **配合熔断**：连续失败后短路，避免打爆下游

**Q4：LLM 场景如何限流？**

多维度：
- **全局 QPS**：保护下游 API
- **Token/分钟**：OpenAI 按 TPM 计
- **用户级**：每用户每分钟上限
- **功能级**：某功能总额度

实现：Redis + Lua 滑动窗口 / Resilience4j RateLimiter / Spring Cloud Gateway。

**Q5：LLM 调用为什么不能用 ForkJoinPool？**

ForkJoinPool 为 CPU 密集短任务设计，LLM 调用是 **IO 长任务**（秒级到十几秒）：
- 阻塞线程池，后续 CompletableFuture 排队
- common pool 被占满，整个 JVM 异步任务卡住
- 违反 FJP 的 work-stealing 假设

解决：专用 boundedElastic Scheduler 或自建线程池。

**Q6：生产 LLM 调用的监控指标？**

- **请求级**：QPS、成功率、P50/P95/P99 延迟
- **Token**：输入/输出 Token 数、模型成本
- **错误**：按类型分布（429/5xx/超时/内容策略）
- **降级率**：fallback 触发次数
- **缓存**：命中率
- **业务**：按用户/功能的调用分布

Micrometer + Prometheus + Grafana 是 Java 栈标配。

**Q7：如何防止 LLM 超预算？**

多层保护：
- **Token 上限**：每次请求限制 max_tokens
- **用户配额**：每日/每月限额
- **异常告警**：单次消耗超阈值通知
- **熔断**：月度预算 80% 触发降级
- **成本归因**：按用户/功能分析，优化头部消耗者
- **审批流**：大请求（长上下文）需审批

**Q8：Java 里怎么准确数 Token？**

- **OpenAI**：JTokkit（Java port of tiktoken）
- **Claude**：Anthropic SDK 有 `beta.messages.count_tokens` API
- **通用估算**：1 token ≈ 0.75 英文单词 ≈ 1.5 中文字（仅估算）

准确计数要和对应模型匹配，跨模型别混用。

**Q9：如何设计可替换模型的架构？**

抽象：`LlmClient` 接口 + 多实现
```java
interface LlmClient {
    String chat(List<Message> msgs, ChatOptions opts);
}
class OpenAiClient implements LlmClient { ... }
class ClaudeClient implements LlmClient { ... }
```

配置驱动：`llm.provider=openai|claude|...`

配合 Feature Flag（如 `Spring Cloud Config`）：可灰度切换、秒级回滚。Spring AI / LangChain4j 已做了这层抽象。

**Q10：LLM 应用如何上线发布？**

- **CI**：单元测试 + 集成测试（Mock LLM）
- **评估测试**：跑黄金测试集，回归通过才合并
- **灰度**：10% → 50% → 100%
- **Shadow**：新版本并跑不返回，对比效果
- **Feature Flag**：快速回滚新 Prompt / 新模型
- **监控阈值**：成功率下降、成本异常自动告警
- **预热**：灰度期观察 P99、错误率
