# Java AI 项目架构

本章聚焦：**企业级 Java AI 应用如何分层、解耦、扩展、可维护**。以 Spring Boot + Spring AI / LangChain4j 为主线。

## 1. 分层架构参考

### 经典四层

```
┌─────────────────────────────────────────────────┐
│  Interface Layer（接口层）                       │
│  REST / GraphQL / WebSocket / SSE / gRPC        │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│  Application Layer（应用层）                     │
│  Use Cases / Services（编排业务用例）             │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│  Domain Layer（领域层）                          │
│  Agent / Chain / Memory / Tool / RAG Pipeline   │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│  Infrastructure Layer（基础设施层）              │
│  LLM Clients / VectorStore / Cache / DB / MQ    │
└─────────────────────────────────────────────────┘
```

### 关键原则
- **领域纯净**：领域层不依赖具体 LLM SDK，只依赖接口
- **可替换**：基础设施可插拔（OpenAI ↔ Claude ↔ 本地）
- **可测试**：Domain 可 Mock LLM 单测
- **可演进**：新增 Agent/Tool 不影响现有

---

## 2. 项目结构范例

```
myai-service/
├── pom.xml
├── src/main/java/com/company/ai/
│   ├── MyAiApplication.java
│   ├── interfaces/                  # 接口层
│   │   ├── rest/
│   │   │   ├── ChatController.java
│   │   │   └── dto/
│   │   └── ws/
│   ├── application/                 # 应用层
│   │   ├── chat/
│   │   │   ├── ChatService.java       # 编排
│   │   │   └── ChatUseCase.java
│   │   ├── rag/
│   │   │   └── DocumentIngestionService.java
│   │   └── agent/
│   │       └── AgentService.java
│   ├── domain/                      # 领域层
│   │   ├── agent/
│   │   │   ├── Agent.java             # 领域模型
│   │   │   ├── AgentStrategy.java     # 抽象策略
│   │   │   └── impl/
│   │   ├── memory/
│   │   │   └── ConversationMemory.java
│   │   ├── rag/
│   │   │   ├── DocumentProcessor.java
│   │   │   └── Retriever.java
│   │   └── tool/
│   │       ├── Tool.java              # 抽象
│   │       └── impl/
│   │           ├── WeatherTool.java
│   │           └── SearchTool.java
│   └── infrastructure/              # 基础设施层
│       ├── llm/
│       │   ├── LlmClient.java         # 抽象
│       │   ├── OpenAiLlmClient.java
│       │   └── ClaudeLlmClient.java
│       ├── vector/
│       │   ├── VectorStore.java
│       │   └── QdrantVectorStore.java
│       ├── cache/
│       │   └── RedisCache.java
│       ├── persistence/
│       │   ├── entity/
│       │   ├── repository/
│       │   └── ConversationRepositoryImpl.java
│       └── observability/
│           └── LlmMetricsCollector.java
└── src/main/resources/
    ├── application.yml
    └── prompts/                     # Prompt 模板文件
        ├── system-assistant.md
        └── rag-qa.md
```

---

## 3. 核心抽象设计

### LlmClient（LLM 统一抽象）

```java
public interface LlmClient {
    ChatResponse chat(ChatRequest request);
    Flux<String> streamChat(ChatRequest request);
    CompletableFuture<ChatResponse> chatAsync(ChatRequest request);

    String providerId();
    boolean supports(ChatOptions opts);  // 例如是否支持 tool_use
}
```

### 工厂 + 策略

```java
@Component
public class LlmClientFactory {
    private final Map<String, LlmClient> clients;

    public LlmClientFactory(List<LlmClient> list) {
        this.clients = list.stream().collect(
            Collectors.toMap(LlmClient::providerId, Function.identity()));
    }

    public LlmClient get(String providerId) {
        return Optional.ofNullable(clients.get(providerId))
            .orElseThrow(() -> new IllegalArgumentException("unknown provider: " + providerId));
    }
}
```

### Tool 抽象

```java
public interface Tool {
    String name();
    String description();
    JsonSchema inputSchema();
    Object execute(Map<String, Object> args);

    default boolean requiresConfirmation() { return false; }
    default Set<String> requiredRoles() { return Set.of(); }
}
```

### Agent 抽象

```java
public interface Agent {
    AgentResult run(AgentContext ctx);
    Flux<AgentEvent> runStream(AgentContext ctx);
}

public sealed interface AgentEvent {
    record Thought(String content) implements AgentEvent {}
    record ToolCall(String name, Map<String, Object> args) implements AgentEvent {}
    record ToolResult(String name, Object result) implements AgentEvent {}
    record Answer(String content) implements AgentEvent {}
    record Error(String message) implements AgentEvent {}
}
```

---

## 4. Prompt 管理

### 外部化模板

`src/main/resources/prompts/system-assistant.md`：
```markdown
你是公司专属助手，请遵守以下规则：
1. 回答限公司业务
2. 涉及个人信息必须脱敏
3. 若不确定请明确说"不知道"

当前时间：{{currentTime}}
用户角色：{{userRole}}
```

加载：

```java
@Component
public class PromptTemplateLoader {
    private final ResourceLoader loader;
    private final Map<String, String> cache = new ConcurrentHashMap<>();

    public String load(String name) {
        return cache.computeIfAbsent(name, n -> {
            try (var in = loader.getResource("classpath:prompts/" + n + ".md").getInputStream()) {
                return new String(in.readAllBytes(), StandardCharsets.UTF_8);
            } catch (IOException e) {
                throw new RuntimeException(e);
            }
        });
    }

    public String render(String name, Map<String, Object> vars) {
        String tpl = load(name);
        for (var e : vars.entrySet()) {
            tpl = tpl.replace("{{" + e.getKey() + "}}", String.valueOf(e.getValue()));
        }
        return tpl;
    }
}
```

### 生产：DB + 版本 + 热更新

```
prompt_templates
  id | name | version | content | status | created_at

prompt_versions_audit
  prompt_id | old_version | new_version | editor | time
```

服务启动加载全量，通过 Redis pub/sub 监听变更触发热刷。生产建议集成 Langfuse / PromptLayer 平台。

---

## 5. RAG Pipeline 组件化

```java
public interface RagPipeline {
    RagResult query(String question, RagContext ctx);
}

@Component
public class DefaultRagPipeline implements RagPipeline {
    private final QueryTransformer transformer;
    private final Retriever retriever;
    private final Reranker reranker;
    private final ContextBuilder builder;
    private final LlmClient llm;

    @Override
    public RagResult query(String q, RagContext ctx) {
        // 1. 查询改写
        List<String> queries = transformer.transform(q, ctx);
        // 2. 并行检索
        List<Document> retrieved = queries.parallelStream()
            .flatMap(query -> retriever.retrieve(query, ctx.topK()).stream())
            .collect(Collectors.toList());
        // 3. 重排序
        List<Document> ranked = reranker.rank(q, retrieved, ctx.topN());
        // 4. 构建上下文
        String contextText = builder.build(ranked);
        // 5. 生成
        ChatResponse resp = llm.chat(ChatRequest.builder()
            .messages(List.of(
                Message.system("基于以下上下文回答：" + contextText),
                Message.user(q)))
            .build());
        return new RagResult(resp.text(), ranked);
    }
}
```

每个组件都是接口 + 实现，可独立替换。

---

## 6. Tool Registry（工具注册）

```java
@Component
public class ToolRegistry {
    private final Map<String, Tool> tools = new ConcurrentHashMap<>();

    public ToolRegistry(List<Tool> toolList) {
        toolList.forEach(t -> tools.put(t.name(), t));
    }

    public List<Tool> forContext(AgentContext ctx) {
        return tools.values().stream()
            .filter(t -> hasPermission(ctx.user(), t))
            .toList();
    }

    public Object invoke(String name, Map<String, Object> args, AgentContext ctx) {
        Tool t = Objects.requireNonNull(tools.get(name), "unknown tool: " + name);
        if (!hasPermission(ctx.user(), t)) {
            throw new AccessDeniedException("no permission for tool " + name);
        }
        if (t.requiresConfirmation() && !ctx.hasConfirmed(name)) {
            throw new ConfirmationRequiredException(name, args);
        }
        return t.execute(args);
    }
}
```

---

## 7. 会话持久化

### 领域模型

```java
public record Conversation(
    String id,
    String userId,
    List<Message> messages,
    Instant createdAt,
    Instant updatedAt,
    Map<String, Object> metadata
) {}
```

### Repository

```java
public interface ConversationRepository {
    Conversation findById(String id);
    Conversation save(Conversation c);
    List<Conversation> findByUser(String userId, Pageable p);
    void delete(String id);
}
```

### JPA 实现

```java
@Entity @Table(name = "conversations")
public class ConversationEntity {
    @Id String id;
    String userId;
    @Convert(converter = MessagesJsonConverter.class)
    @Column(columnDefinition = "jsonb")
    List<Message> messages;
    Instant createdAt;
    Instant updatedAt;
}
```

### 分表策略
- 按 `userId` hash 分表（10 亿级会话）
- 冷热分离：3 个月前归档到 OSS

---

## 8. 权限与多租户

### 租户隔离

```java
@Component
public class TenantContext {
    private static final ThreadLocal<String> CURRENT = new ThreadLocal<>();

    public static String current() { return CURRENT.get(); }
    public static void set(String tenantId) { CURRENT.set(tenantId); }
    public static void clear() { CURRENT.remove(); }
}
```

### VectorStore 租户隔离

```java
vectorStore.similaritySearch(SearchRequest.builder()
    .query(q)
    .filterExpression("tenant_id == '" + TenantContext.current() + "'")
    .build());
```

### 工具权限

```java
@Component
public class AdminTool implements Tool {
    @Override
    public Set<String> requiredRoles() { return Set.of("ADMIN"); }
    @Override
    public Object execute(Map<String, Object> args) { ... }
}
```

---

## 9. 异步处理与消息队列

### 异步场景
- 长任务（报告生成、批量分析）→ 后台 Agent
- 文档入库（大文件 chunk + embedding）→ 异步 pipeline
- 多 Agent 协作 → 消息驱动

### 架构

```
用户请求 → 创建 Task（PENDING）→ 返回 task_id
            ↓ MQ (Kafka/RabbitMQ)
         Worker 消费 → 运行 Agent → 更新 Task 状态
            ↓
       Webhook / WebSocket / 轮询通知用户
```

### 代码

```java
@Service
public class AsyncAgentService {
    @Autowired KafkaTemplate<String, TaskEvent> kafka;
    @Autowired TaskRepository taskRepo;

    public String submit(String userId, String input) {
        Task task = taskRepo.save(new Task(UUID.randomUUID().toString(), userId, input, TaskStatus.PENDING));
        kafka.send("agent.tasks", new TaskEvent(task.id(), userId, input));
        return task.id();
    }
}

@Component
public class AgentWorker {
    @KafkaListener(topics = "agent.tasks")
    public void handle(TaskEvent event) {
        try {
            String result = agent.run(event.input());
            taskRepo.complete(event.taskId(), result);
            notifyUser(event.userId(), event.taskId());
        } catch (Exception e) {
            taskRepo.fail(event.taskId(), e.getMessage());
        }
    }
}
```

---

## 10. 配置与 Feature Flag

### 多模型配置

```yaml
llm:
  providers:
    openai:
      enabled: true
      api-key: ${OPENAI_API_KEY}
      default-model: gpt-4o
    claude:
      enabled: true
      api-key: ${ANTHROPIC_API_KEY}
      default-model: claude-opus-4-7
    local:
      enabled: false
      base-url: http://localhost:11434

  routing:
    default: openai
    rules:
      - condition: "#feature == 'code-review'"
        provider: claude
      - condition: "#user.tier == 'free'"
        provider: local
```

### Feature Flag（LaunchDarkly / Unleash）

```java
@Component
public class LlmRouter {
    private final FeatureFlags flags;
    private final LlmClientFactory factory;

    public LlmClient route(AgentContext ctx) {
        String provider = flags.stringValue("llm.provider",
            Map.of("user", ctx.userId()),
            "openai");
        return factory.get(provider);
    }
}
```

---

## 11. 缓存层

### 多级缓存

```java
@Service
public class CachedLlmClient implements LlmClient {
    private final LlmClient delegate;
    private final Cache<String, String> localCache;  // Caffeine
    private final RedisTemplate<String, String> redis;
    private final SemanticCache semanticCache;

    @Override
    public ChatResponse chat(ChatRequest req) {
        String key = cacheKey(req);
        // L1 本地
        String cached = localCache.getIfPresent(key);
        if (cached != null) return ChatResponse.cached(cached);
        // L2 Redis
        cached = redis.opsForValue().get(key);
        if (cached != null) {
            localCache.put(key, cached);
            return ChatResponse.cached(cached);
        }
        // L3 语义缓存
        Optional<String> semantic = semanticCache.search(req.lastUserMessage());
        if (semantic.isPresent()) return ChatResponse.cached(semantic.get());
        // 未命中
        ChatResponse resp = delegate.chat(req);
        String text = resp.text();
        localCache.put(key, text);
        redis.opsForValue().set(key, text, Duration.ofHours(1));
        semanticCache.add(req.lastUserMessage(), text);
        return resp;
    }
}
```

### 缓存粒度
- 无状态 Q&A：可缓存
- 对话（带历史）：一般不缓存
- Embedding：必须缓存（重复文本直接返回，省成本）

---

## 12. 测试策略

### 单元测试
领域层用 Mock `LlmClient`：
```java
@Test
void agentHandlesToolCall() {
    LlmClient mock = mock(LlmClient.class);
    when(mock.chat(any())).thenReturn(
        ChatResponse.withToolCall("weather", Map.of("city", "北京"))
    );
    Agent agent = new DefaultAgent(mock, toolRegistry);
    AgentResult r = agent.run(ctx);
    assertThat(r.answer()).contains("25℃");
}
```

### 集成测试
- Testcontainers：Qdrant / Postgres / Redis
- WireMock：录制 OpenAI API 回放
- 小模型本地：Ollama 跑 llama3.2 做真实集成

### 评估测试（CI 跑）
```java
@Test
void regressionTestGoldenSet() {
    GoldenSet set = GoldenSet.load("rag-v1.jsonl");
    List<Score> scores = set.cases().parallelStream()
        .map(c -> evaluator.eval(ragPipeline.query(c.input(), ...), c.expected()))
        .toList();
    double avg = scores.stream().mapToDouble(Score::value).average().orElse(0);
    assertThat(avg).isGreaterThan(0.8);
}
```

---

## 13. 可观测性工程化

### 统一 Trace ID 贯穿

```java
@Component @Order(1)
public class TraceIdFilter implements Filter {
    @Override
    public void doFilter(ServletRequest req, ServletResponse resp, FilterChain chain)
        throws IOException, ServletException {
        String traceId = Optional.ofNullable(((HttpServletRequest) req).getHeader("X-Trace-Id"))
            .orElse(UUID.randomUUID().toString());
        MDC.put("traceId", traceId);
        try {
            chain.doFilter(req, resp);
        } finally {
            MDC.clear();
        }
    }
}
```

### 结构化日志（JSON）

```json
{
  "timestamp": "2025-03-15T10:00:00Z",
  "level": "INFO",
  "traceId": "abc-123",
  "userId": "u1",
  "service": "ai-chat",
  "operation": "llm.chat",
  "model": "gpt-4o",
  "tokens_in": 1234,
  "tokens_out": 567,
  "latency_ms": 2345,
  "cost_usd": 0.012
}
```

### 告警规则
- 错误率 > 5% / 5min
- P99 > 30s
- 单会话 > 10 万 token
- 月度成本 > 预算 80%

---

## 14. 发布与回滚

### 多环境
dev → test → staging → prod，各环境独立 API Key、独立 VectorStore。

### 蓝绿 / 金丝雀
- K8s 两套 Deployment，Service 切流
- Istio / SkyWalking 做流量镜像（Shadow）

### Prompt / Model 快速切换
- Feature Flag 控制，无需重新部署
- 出问题 1 分钟内切回

### 数据迁移
- VectorStore 加维度：新旧集合并行 + 双写
- 模型升级：Embedding 不兼容的必须重建索引

---

## 15. 常见反模式

1. **Controller 直接调 LLM SDK**：业务和基础设施耦合
2. **Prompt 硬编码在代码**：改 Prompt 要重新部署
3. **没有 LlmClient 抽象**：切换模型改几十处
4. **工具在 Agent 里 if-else 分发**：新增工具改主逻辑
5. **同步阻塞处理长任务**：用户等 30 秒白屏
6. **无多租户隔离**：A 看到 B 的数据
7. **无限重试**：一个 bug 刷爆预算
8. **没有评估闭环**：改 Prompt 不知道效果
9. **日志不脱敏**：合规事故
10. **测试只 Mock LLM**：忽略真实模型行为差异

---

## 面试高频问题

**Q1：Java AI 应用如何分层？**

经典四层：
- **Interface**：REST/WebSocket，只做协议转换
- **Application**：Use Case 编排，调用 Domain
- **Domain**：Agent/Chain/Memory/Tool 等核心业务抽象
- **Infrastructure**：LLM Client/VectorStore/Cache 具体实现

核心：**领域层不依赖具体 SDK**，只依赖抽象接口。这样能换模型、Mock 测试、单元测试不依赖外部。

**Q2：如何支持多 LLM 提供商？**

抽象 `LlmClient` 接口 + 多个实现（OpenAI/Claude/本地）。通过 Spring 注入 `List<LlmClient>`，工厂按 providerId 路由。Feature Flag 控制默认提供商，运行时可切换。Spring AI / LangChain4j 已经做了这层。

**Q3：Prompt 如何管理？**

- **开发初期**：外部 `.md` 文件 + ClassPath 加载
- **生产**：DB 存储，带版本、审计、热更新
- **高级**：集成 Langfuse / PromptLayer，A/B 测试、灰度、评分

关键：**代码版本和 Prompt 版本解耦**，Prompt 改动可秒级回滚。

**Q4：Tool 如何设计扩展？**

`Tool` 接口 + Spring 自动收集（`List<Tool>` 注入 Registry）。新工具 = 新 Bean，无需改主流程。Tool 自己声明权限、是否需确认。Registry 按上下文过滤。

**Q5：多租户如何实现？**

- **ThreadLocal TenantContext**：请求入口设置，贯穿整个请求
- **VectorStore 过滤**：tenant_id 作为 metadata，检索时强制 filter
- **DB 隔离**：按需选择共享表（tenant_id 列）/ 独立 Schema / 独立库
- **API Key 隔离**：不同租户可使用不同 LLM Key 和额度
- **Prompt 隔离**：租户级 System Prompt 定制

**Q6：长任务如何处理？**

- **MQ 异步化**：请求入队返回 task_id，Worker 后台处理
- **状态跟踪**：DB 记录任务状态（PENDING/RUNNING/DONE/FAILED）
- **结果通知**：Webhook / WebSocket / SSE / 轮询
- **超时处理**：Worker 端设硬超时，防卡死
- **断点续跑**：复杂 Agent 用 LangGraph Checkpointer 思路持久化中间状态

**Q7：如何做 LLM 应用的 CI/CD？**

- **单元测试**：Mock LLM
- **集成测试**：Testcontainers + WireMock / Ollama
- **评估测试**：黄金测试集，低于阈值 PR 不能合
- **环境隔离**：dev/test/staging/prod 独立 Key 和 VectorStore
- **灰度发布**：10% → 50% → 100%
- **监控告警**：成本/成功率/延迟上线后自动盯

**Q8：怎么选 Spring AI vs LangChain4j 作为主框架？**

**Spring AI**：
- Spring Boot 深度集成
- Advisor 链设计优雅
- 官方背书，更新稳定

**LangChain4j**：
- AI Services 声明式极简
- 接近 LangChain Python，迁移友好
- 独立性强，非 Spring 项目可用

企业级 Spring 项目首选 Spring AI；轻量服务或需要 LangChain 风格选 LangChain4j。两者不互斥，可以混用（各组件各取所长）。

**Q9：缓存策略怎么设计？**

- **Embedding**：必缓存（重复率高，省钱）
- **无状态 Q&A**：可缓存（语义缓存）
- **带会话历史**：一般不缓存
- **工具结果**：幂等工具可缓存
- **多级**：Caffeine（进程内）→ Redis（跨实例）→ 语义缓存（向量）

注意缓存 TTL、失效策略、按用户/租户隔离。

**Q10：如何规划 Java AI 团队技术栈？**

推荐栈：
- **框架**：Spring Boot + Spring AI（主）/ LangChain4j（轻）
- **模型**：混合策略（API + 本地 Ollama/vLLM 兜底）
- **向量库**：Qdrant / Milvus / pgvector（看规模）
- **缓存**：Redis + Caffeine
- **消息**：Kafka（异步任务）
- **DB**：PostgreSQL（元数据）
- **可观测**：Micrometer + Prometheus + Grafana + Langfuse
- **部署**：K8s + Helm + GitOps（ArgoCD）
- **测试**：JUnit 5 + Testcontainers + WireMock

不要追新，稳定的 Spring 栈 + 精选 AI 组件最可维护。
