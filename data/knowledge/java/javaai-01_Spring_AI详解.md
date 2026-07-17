# Spring AI 详解

## 1. 概览

### 定位
Spring AI 是 Spring 官方的 AI 应用框架（2024 正式发布 1.0），提供与 Spring Boot 深度集成的 LLM 开发体验。目标是让 Java 开发者用熟悉的 Spring 抽象（ChatClient、ChatMemory、VectorStore、Tool）构建生产级 AI 应用，无需切换到 Python。

### 为什么需要
- Java 企业存量系统多，重写 Python 成本高
- 生产级特性（事务、监控、安全、K8s）Spring 成熟
- 对标 LangChain/LlamaIndex，但是 **Spring 风格**（DI、AutoConfig）

### 核心能力
- 多模型支持（OpenAI、Anthropic、Azure、Bedrock、Vertex AI、Ollama、Mistral…）
- ChatClient（流畅 API）
- Structured Output
- Tool Calling
- RAG（VectorStore、DocumentReader、ETL）
- ChatMemory
- Advisors（类似 AOP 的 Chain 模式）
- Observability（Micrometer 集成）

---

## 2. 快速开始

### 依赖

```xml
<dependency>
    <groupId>org.springframework.ai</groupId>
    <artifactId>spring-ai-starter-model-openai</artifactId>
</dependency>
```

### 配置

```yaml
spring:
  ai:
    openai:
      api-key: ${OPENAI_API_KEY}
      chat:
        options:
          model: gpt-4o
          temperature: 0.7
```

### 最简示例

```java
@RestController
class ChatController {
    private final ChatClient chatClient;

    ChatController(ChatClient.Builder builder) {
        this.chatClient = builder.build();
    }

    @GetMapping("/chat")
    String chat(@RequestParam String q) {
        return chatClient.prompt()
            .user(q)
            .call()
            .content();
    }
}
```

---

## 3. ChatClient API

### 构建
```java
ChatClient chatClient = ChatClient.builder(chatModel)
    .defaultSystem("你是有帮助的助手")
    .defaultOptions(ChatOptions.builder().temperature(0.5).build())
    .build();
```

### 调用模式
```java
// 同步
String text = chatClient.prompt()
    .user("你好")
    .call()
    .content();

// 流式
Flux<String> stream = chatClient.prompt()
    .user("写一首诗")
    .stream()
    .content();

// 获取完整响应（含 metadata）
ChatResponse resp = chatClient.prompt()
    .user("...")
    .call()
    .chatResponse();
```

### System + User + Params
```java
String result = chatClient.prompt()
    .system("你是翻译助手")
    .user(u -> u.text("翻译：{text}").param("text", "Hello World"))
    .call()
    .content();
```

---

## 4. Structured Output

### 自动映射到 POJO

```java
record ActorFilms(String actor, List<String> movies) {}

ActorFilms films = chatClient.prompt()
    .user("列出汤姆·汉克斯的 5 部代表作")
    .call()
    .entity(ActorFilms.class);
```

### List / Map

```java
List<ActorFilms> list = chatClient.prompt()
    .user("列出 3 位演员及代表作")
    .call()
    .entity(new ParameterizedTypeReference<List<ActorFilms>>() {});
```

### 原理
- 生成 JSON Schema 附加到 Prompt
- 约束 LLM 输出 JSON
- Jackson 反序列化

---

## 5. Tool Calling（函数调用）

### 定义工具

```java
class WeatherTools {
    @Tool(description = "查询指定城市当前天气")
    public String getWeather(
        @ToolParam(description = "城市名称") String city
    ) {
        // 调用真实 API
        return city + ": 晴 25℃";
    }
}
```

### 使用

```java
String result = chatClient.prompt()
    .user("北京天气如何？")
    .tools(new WeatherTools())  // 注入工具实例
    .call()
    .content();
```

### 多工具 & Bean 注入

```java
@Configuration
class ToolConfig {
    @Bean
    public Function<WeatherRequest, WeatherResponse> weatherFunction() {
        return req -> new WeatherResponse(...);
    }
}

// 调用
chatClient.prompt()
    .user("...")
    .toolNames("weatherFunction")  // 引用 Bean 名
    .call();
```

### 工具执行生命周期
ChatClient 内部自动循环：
1. 发送 Prompt + Tools
2. 如果模型返回 tool_call，解析并反射调用
3. 结果回传模型
4. 重复直至没有 tool_call

可通过 `ToolCallingManager` 自定义该流程。

---

## 6. ChatMemory

### 作用
对话历史管理，支持多用户、多 session。

### 内置实现
- `InMemoryChatMemory`：内存
- `JdbcChatMemoryRepository`：JDBC
- `CassandraChatMemoryRepository`：Cassandra
- `Neo4jChatMemoryRepository`：Neo4j

### 使用

```java
ChatMemory memory = MessageWindowChatMemory.builder()
    .chatMemoryRepository(jdbcRepo)
    .maxMessages(20)
    .build();

chatClient.prompt()
    .user("...")
    .advisors(new MessageChatMemoryAdvisor(memory))
    .advisors(a -> a.param(ChatMemory.CONVERSATION_ID, "user-123"))
    .call();
```

### 策略
- `MessageWindowChatMemory`：滑动窗口（最近 N 条）
- 自己实现 `ChatMemory` 做 summary

---

## 7. Advisors（建议器）

### 概念
类似 Servlet Filter / AOP Interceptor 链，对 Prompt 请求/响应做增强。

### 内置 Advisor
- `MessageChatMemoryAdvisor`：注入历史消息
- `QuestionAnswerAdvisor`：RAG 自动注入上下文
- `SafeGuardAdvisor`：敏感词拦截
- `SimpleLoggerAdvisor`：日志

### 示例

```java
chatClient.prompt()
    .user("...")
    .advisors(
        new MessageChatMemoryAdvisor(memory),
        new QuestionAnswerAdvisor(vectorStore),
        new SimpleLoggerAdvisor()
    )
    .call();
```

### 自定义

```java
class TokenLimitAdvisor implements CallAdvisor {
    @Override
    public ChatClientResponse adviseCall(ChatClientRequest req, CallAdvisorChain chain) {
        if (tokenCount(req) > 10000) {
            throw new TokenLimitException();
        }
        return chain.nextCall(req);
    }
}
```

---

## 8. RAG 实现

### ETL Pipeline

```java
// 1. 读取文档
DocumentReader reader = new TikaDocumentReader("classpath:docs/a.pdf");
List<Document> docs = reader.get();

// 2. 切分
TextSplitter splitter = new TokenTextSplitter();
List<Document> chunks = splitter.apply(docs);

// 3. 存入 VectorStore
vectorStore.add(chunks);
```

### VectorStore 实现
- `SimpleVectorStore`（内存/文件）
- Chroma / Milvus / Pinecone / Qdrant / Weaviate
- PGVector / Redis / Elasticsearch / OpenSearch
- Oracle / MongoDB Atlas / Neo4j

### 检索

```java
List<Document> results = vectorStore.similaritySearch(
    SearchRequest.builder()
        .query("Spring AI 是什么")
        .topK(5)
        .similarityThreshold(0.7)
        .filterExpression("source == 'official'")
        .build()
);
```

### RAG + ChatClient

```java
@Bean
QuestionAnswerAdvisor qaAdvisor(VectorStore vs) {
    return QuestionAnswerAdvisor.builder(vs)
        .searchRequest(SearchRequest.builder().topK(5).build())
        .build();
}

// 自动注入检索结果
String answer = chatClient.prompt()
    .user(question)
    .advisors(qaAdvisor)
    .call()
    .content();
```

### Advanced RAG

`RetrievalAugmentationAdvisor` 支持完整高级 RAG 流程：
- Query Transformation（改写、扩展）
- Query Routing
- Document Retrieval
- Document Post-Processing（重排序、压缩）
- Generation

```java
RetrievalAugmentationAdvisor advisor = RetrievalAugmentationAdvisor.builder()
    .queryTransformers(CompressionQueryTransformer.builder().chatClientBuilder(cb).build())
    .documentRetriever(VectorStoreDocumentRetriever.builder().vectorStore(vs).topK(5).build())
    .documentPostProcessors(reranker)
    .build();
```

---

## 9. 多模型切换

### 同时用多个

```yaml
spring.ai:
  openai.api-key: xxx
  anthropic.api-key: yyy
```

```java
@Bean
ChatClient openAiClient(OpenAiChatModel m) { return ChatClient.create(m); }

@Bean
ChatClient claudeClient(AnthropicChatModel m) { return ChatClient.create(m); }
```

### 本地模型（Ollama）

```xml
<dependency>
    <groupId>org.springframework.ai</groupId>
    <artifactId>spring-ai-starter-model-ollama</artifactId>
</dependency>
```

```yaml
spring.ai.ollama:
  base-url: http://localhost:11434
  chat.options.model: qwen2.5:72b
```

---

## 10. Embedding & VectorStore

### Embedding 调用

```java
@Autowired
EmbeddingModel embeddingModel;

float[] vector = embeddingModel.embed("Spring AI");
List<float[]> vectors = embeddingModel.embed(List.of("a", "b"));
```

### 自定义 Embedding 提供商

```yaml
spring.ai.openai:
  embedding.options.model: text-embedding-3-large
```

或用 HuggingFace、Ollama、Azure 等。

---

## 11. Observability

### Micrometer 集成
Spring AI 自动为所有 ChatModel/EmbeddingModel/VectorStore 调用发送 metrics 和 traces。

```java
@Bean
ObservationRegistry observationRegistry() {
    return ObservationRegistry.create();
}
```

自动采集：
- `spring.ai.chat.client.operation`
- `spring.ai.tool.call`
- `spring.ai.vector.store`

### 集成 Prometheus / Grafana / Zipkin

```yaml
management:
  tracing.sampling.probability: 1.0
  metrics.export.prometheus.enabled: true
```

配合 **Langfuse**/**Arize** 等专业 LLM Observability 平台。

---

## 12. MCP 集成

Spring AI 1.0+ 原生支持 MCP：

```xml
<dependency>
    <groupId>org.springframework.ai</groupId>
    <artifactId>spring-ai-starter-mcp-client</artifactId>
</dependency>
```

```yaml
spring.ai.mcp.client:
  stdio:
    servers:
      filesystem:
        command: npx
        args: ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
```

```java
@Autowired
McpSyncClient mcpClient;

// 把 MCP 工具暴露给 ChatClient
List<ToolCallback> tools = McpToolUtils.toSyncToolCallbacks(mcpClient);

chatClient.prompt()
    .user("...")
    .toolCallbacks(tools)
    .call();
```

---

## 13. 生产最佳实践

### 1. 重试与超时
```yaml
spring.ai.retry:
  max-attempts: 5
  backoff:
    initial-interval: 1s
    max-interval: 30s
    multiplier: 2
```

### 2. 鉴权
```java
@RestController
@PreAuthorize("hasRole('AI_USER')")
class AiController { ... }
```

### 3. 限流
集成 Resilience4j / Sentinel：
```java
@RateLimiter(name = "llm", fallbackMethod = "fallback")
public String chat(...) { ... }
```

### 4. 成本追踪
```java
ChatResponse resp = ... ;
Usage u = resp.getMetadata().getUsage();
metrics.counter("llm.tokens",
    "model", model, "type", "input").increment(u.getPromptTokens());
```

### 5. 敏感信息
```java
.advisors(new SafeGuardAdvisor(List.of("违禁词")))
```

### 6. 异步化
```java
CompletableFuture<String> future = CompletableFuture.supplyAsync(
    () -> chatClient.prompt().user(q).call().content(),
    executor
);
```

---

## 14. Spring AI vs LangChain / LangChain4j

| 维度 | Spring AI | LangChain4j | LangChain (Python) |
|------|-----------|-------------|---------------------|
| 生态 | Spring Boot | 独立 | Python 大生态 |
| 抽象风格 | ChatClient + Advisor | Service + AiServices | Chain / Runnable |
| 入门 | 极低（Spring 用户） | 中 | 中 |
| 集成广度 | 持续扩展 | 扩展中 | 最广 |
| 生产特性 | 完善（Spring 体系） | 一般 | 需自行加 |
| Multi-Agent | 弱 | 中（Agent2Agent） | 强（LangGraph） |
| 适合 | Java 企业级 | Java 创业/独立 | 快速原型、研究 |

---

## 面试高频问题

**Q1：Spring AI 的核心抽象？**

- **ChatModel / EmbeddingModel**：底层模型接口
- **ChatClient**：流畅 API，开发主入口
- **Advisor**：Chain 模式的 Prompt/Response 增强器
- **VectorStore**：向量存储抽象
- **DocumentReader / TextSplitter**：ETL 组件
- **ChatMemory**：对话历史
- **Tool / Function**：工具调用

一句话：**用 Spring 方式封装 LLM 应用的所有组件**。

**Q2：Advisor 是什么，有什么价值？**

类似 Servlet Filter / AOP Interceptor，形成处理链：
```
user prompt → [MemoryAdvisor] → [RagAdvisor] → [LogAdvisor] → LLM → [ResponseFilter] → user
```

价值：
- **正交关注点解耦**：记忆/RAG/日志独立
- **组合灵活**：按需叠加
- **可复用**：自定义 Advisor 跨 ChatClient 共享
- **生产特性**：限流、审计、脱敏都可以做成 Advisor

**Q3：如何实现 RAG？**

三步：
1. **ETL**：DocumentReader → TextSplitter → VectorStore
2. **检索**：`vectorStore.similaritySearch(query)` 或用 `QuestionAnswerAdvisor` 自动注入
3. **回答**：ChatClient 带上检索结果调 LLM

```java
chatClient.prompt()
    .user(q)
    .advisors(new QuestionAnswerAdvisor(vectorStore))
    .call();
```

**Q4：Tool Calling 怎么做？**

两种方式：
- **注解**：`@Tool` + `@ToolParam`，方法即工具
- **Bean**：`Function<Req, Resp>` Bean，通过 `toolNames()` 引用

ChatClient 内部自动处理调用循环（检测 tool_call → 反射执行 → 结果回传）。

**Q5：Structured Output 怎么实现？**

```java
record Person(String name, int age) {}
Person p = chatClient.prompt().user("...").call().entity(Person.class);
```

框架内部：
1. 从 `Person` 生成 JSON Schema
2. 注入到 Prompt（告诉 LLM 输出格式）
3. LLM 返回 JSON
4. Jackson 反序列化

对非严格模式 LLM 仍可能失败，生产环境需要重试。

**Q6：ChatMemory 如何在多用户场景使用？**

通过 `conversation_id` 隔离：
```java
.advisors(a -> a.param(ChatMemory.CONVERSATION_ID, userId))
```

底层 `ChatMemoryRepository` 按 id 存取消息。生产用 JDBC/Cassandra，不要用内存版。

**Q7：Spring AI 如何切换模型？**

三种方式：
1. 改 `application.yml` 的 model 名称
2. 引入不同 starter（openai / anthropic / ollama）可并存
3. 运行时通过 `ChatOptions` 覆盖：
```java
.options(OpenAiChatOptions.builder().model("gpt-4o-mini").build())
```

**Q8：Spring AI 如何与 Spring Boot 生态整合？**

- **配置**：`@ConfigurationProperties` 统一
- **DI**：所有组件都是 Bean，`@Autowired` 注入
- **Actuator**：Metrics/Traces 自动暴露
- **Security**：`@PreAuthorize` 控权限
- **事务**：持久化记忆 / 工具调用可在事务内
- **Cloud**：Spring Cloud 服务发现、配置中心
- **AOT / Native**：支持编译成 GraalVM Native Image

**Q9：Spring AI 的生产级监控？**

Micrometer 自动采集：
- Chat 调用次数、延迟、Token 数
- Tool 调用
- VectorStore 操作

可导出到 Prometheus、Zipkin、Tempo。配合 Langfuse 做完整 LLM 可观测性。

**Q10：Spring AI 和 LangChain4j 怎么选？**

- **Spring AI**：你用 Spring Boot，希望 AI 组件像其他 Spring Bean 一样自然。官方背书、集成深、生产特性完备。
- **LangChain4j**：独立项目，设计更接近 Python LangChain，适合非 Spring 项目或偏好 LangChain 抽象的团队。

企业选 Spring AI 更稳妥；轻量/独立服务可用 LangChain4j；两者都比直接调用 SDK 合算。
