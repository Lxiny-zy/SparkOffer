# LangChain4j

## 1. 概览

### 定位
LangChain4j 是受 LangChain（Python）启发的 Java/Kotlin LLM 应用框架。2024 年发布 1.0，已成为 Java 生态**最流行的 LLM 框架之一**（与 Spring AI 并列）。

### 特点
- **纯 Java**：无 Python 依赖
- **Spring Boot 集成**：提供 starter，也可独立使用
- **AI Services**：极简声明式 API（类似 Spring Data Repository）
- **LangChain 对齐**：熟悉 Python LangChain 的人易上手
- **广泛集成**：15+ LLM 提供商，20+ 向量库，常用工具

### 适用
- 非 Spring 项目
- 需要细粒度控制（Chain、Memory、Tool 组合）
- 从 Python LangChain 迁移
- Java 独立 AI 微服务

### vs Spring AI

| 维度 | LangChain4j | Spring AI |
|------|-------------|-----------|
| 设计风格 | LangChain Python 对齐 | Spring 官方风格 |
| AI Services | ✅（招牌功能） | ❌（用 Advisor） |
| Advisor | ❌ | ✅ |
| Spring Boot 集成 | starter 可选 | 深度集成 |
| 学习曲线 | LangChain 用户低 | Spring 用户低 |
| 生态集成 | 广 | 广 |
| Multi-Agent | `agentic-java` 子项目 | 弱 |

---

## 2. 快速开始

### 依赖

```xml
<dependency>
    <groupId>dev.langchain4j</groupId>
    <artifactId>langchain4j-open-ai</artifactId>
    <version>1.0.0</version>
</dependency>
```

### 最简示例

```java
ChatLanguageModel model = OpenAiChatModel.builder()
    .apiKey(System.getenv("OPENAI_API_KEY"))
    .modelName("gpt-4o")
    .temperature(0.7)
    .build();

String answer = model.generate("Java 里 volatile 的作用？");
System.out.println(answer);
```

### Spring Boot Starter

```xml
<dependency>
    <groupId>dev.langchain4j</groupId>
    <artifactId>langchain4j-open-ai-spring-boot-starter</artifactId>
</dependency>
```

```yaml
langchain4j.open-ai.chat-model:
  api-key: ${OPENAI_API_KEY}
  model-name: gpt-4o
```

```java
@Autowired ChatLanguageModel model;
```

---

## 3. AI Services（核心招牌）

### 声明式接口

```java
interface Assistant {
    String chat(String userMessage);
}

Assistant assistant = AiServices.create(Assistant.class, model);
String reply = assistant.chat("你好");
```

### 带系统提示、记忆、工具

```java
interface Coder {
    @SystemMessage("你是资深 Java 工程师，代码风格遵循 Google Java Style。")
    @UserMessage("帮我写一个 {{feature}} 的实现。")
    String write(@V("feature") String feature);
}

Coder coder = AiServices.builder(Coder.class)
    .chatLanguageModel(model)
    .chatMemory(MessageWindowChatMemory.withMaxMessages(10))
    .tools(new MyTools())
    .build();

String code = coder.write("LRU 缓存");
```

### 返回类型自动映射

```java
interface Extractor {
    @UserMessage("从文本提取人物：{{text}}")
    Person extract(@V("text") String text);
}

record Person(String name, int age, String job) {}

Person p = extractor.extract("张三，30 岁，程序员");
```

支持返回：
- 基础类型（String、int、boolean）
- POJO（自动 JSON Schema + 反序列化）
- List / Map
- `Result<T>`（含 metadata：tokens、finishReason）

### 流式

```java
interface Streamer {
    TokenStream chat(String q);
}

Streamer s = AiServices.create(Streamer.class, streamingModel);
s.chat("写首诗")
    .onNext(System.out::print)
    .onComplete(r -> System.out.println("done"))
    .onError(Throwable::printStackTrace)
    .start();
```

---

## 4. ChatMemory

### 类型
- `MessageWindowChatMemory`：滑动窗口，按消息数
- `TokenWindowChatMemory`：按 Token 数（需传入 Tokenizer）

### 持久化

```java
class RedisChatMemoryStore implements ChatMemoryStore {
    @Override
    public List<ChatMessage> getMessages(Object memoryId) { ... }
    @Override
    public void updateMessages(Object memoryId, List<ChatMessage> messages) { ... }
    @Override
    public void deleteMessages(Object memoryId) { ... }
}

ChatMemoryProvider provider = memoryId ->
    MessageWindowChatMemory.builder()
        .id(memoryId)
        .maxMessages(20)
        .chatMemoryStore(new RedisChatMemoryStore())
        .build();

interface MultiUserAssistant {
    String chat(@MemoryId String userId, @UserMessage String message);
}

MultiUserAssistant assistant = AiServices.builder(MultiUserAssistant.class)
    .chatLanguageModel(model)
    .chatMemoryProvider(provider)
    .build();

assistant.chat("alice", "记住我叫 Alice");
assistant.chat("alice", "我叫什么？");  // 记得
assistant.chat("bob", "记住我叫 Bob");   // 隔离
```

---

## 5. Tools（函数调用）

### 注解方式

```java
class CalculatorTools {
    @Tool("计算两数之和")
    double add(double a, double b) {
        return a + b;
    }

    @Tool("查询当前时间")
    String now() {
        return Instant.now().toString();
    }
}

interface Assistant { String chat(String q); }

Assistant assistant = AiServices.builder(Assistant.class)
    .chatLanguageModel(model)
    .tools(new CalculatorTools())
    .build();

assistant.chat("3.14 + 2.86 等于多少？当前时间？");
```

### @P 参数描述

```java
@Tool("查询城市天气")
String getWeather(
    @P("城市名") String city,
    @P(value = "单位", required = false) String unit
) {
    ...
}
```

### 动态工具

```java
List<ToolSpecification> specs = List.of(
    ToolSpecification.builder()
        .name("search")
        .description("搜索网页")
        .parameters(JsonObjectSchema.builder().addStringProperty("query").build())
        .build()
);

ChatResponse resp = model.chat(
    ChatRequest.builder()
        .messages(...)
        .parameters(ChatRequestParameters.builder().toolSpecifications(specs).build())
        .build()
);
```

---

## 6. RAG

### 流程

```
Document → DocumentSplitter → TextSegment → EmbeddingModel → EmbeddingStore
              ↓ (检索时)
         Query → EmbeddingModel → EmbeddingStore → Retrieved Segments → LLM
```

### 基础 RAG

```java
// 1. 加载文档
List<Document> docs = FileSystemDocumentLoader.loadDocuments("./data");

// 2. 切分
DocumentSplitter splitter = DocumentSplitters.recursive(500, 50);
List<TextSegment> segments = splitter.splitAll(docs);

// 3. 嵌入
EmbeddingModel embed = OpenAiEmbeddingModel.builder().apiKey(...).build();
List<Embedding> embeddings = embed.embedAll(segments).content();

// 4. 存入
EmbeddingStore<TextSegment> store = new InMemoryEmbeddingStore<>();
store.addAll(embeddings, segments);

// 5. 检索
Embedding q = embed.embed("查询").content();
List<EmbeddingMatch<TextSegment>> matches = store.findRelevant(q, 5);
```

### EasyRAG（开箱即用）

```java
<dependency>
    <groupId>dev.langchain4j</groupId>
    <artifactId>langchain4j-easy-rag</artifactId>
</dependency>
```

```java
List<Document> docs = FileSystemDocumentLoader.loadDocuments("./data");
InMemoryEmbeddingStore<TextSegment> store = new InMemoryEmbeddingStore<>();
EmbeddingStoreIngestor.ingest(docs, store);

ContentRetriever retriever = EmbeddingStoreContentRetriever.builder()
    .embeddingStore(store)
    .embeddingModel(embed)
    .maxResults(5)
    .build();

interface Assistant { String chat(String q); }

Assistant assistant = AiServices.builder(Assistant.class)
    .chatLanguageModel(model)
    .contentRetriever(retriever)
    .build();

String answer = assistant.chat("基于文档回答...");
```

### Advanced RAG

```java
RetrievalAugmentor augmentor = DefaultRetrievalAugmentor.builder()
    .queryTransformer(new CompressingQueryTransformer(model))  // 查询压缩
    .queryRouter(new DefaultQueryRouter(retriever1, retriever2))  // 多源路由
    .contentAggregator(new ReRankingContentAggregator(reranker))  // 重排序
    .contentInjector(new DefaultContentInjector())  // 注入 Prompt
    .build();

assistant = AiServices.builder(Assistant.class)
    .retrievalAugmentor(augmentor)
    .build();
```

---

## 7. EmbeddingStore 集成

**内置**：
- InMemoryEmbeddingStore（测试用）
- Chroma、Milvus、Qdrant、Pinecone、Weaviate
- Elasticsearch、OpenSearch
- PGVector、Redis
- Azure AI Search、AWS Neptune
- Cassandra、Couchbase、MongoDB Atlas
- Oracle、Neo4j

```xml
<dependency>
    <groupId>dev.langchain4j</groupId>
    <artifactId>langchain4j-qdrant</artifactId>
</dependency>
```

```java
EmbeddingStore<TextSegment> store = QdrantEmbeddingStore.builder()
    .host("localhost").port(6334)
    .collectionName("my-collection")
    .build();
```

---

## 8. 多模态

### 图片输入

```java
UserMessage msg = UserMessage.from(
    TextContent.from("描述这张图"),
    ImageContent.from("https://.../cat.jpg")
);
ChatResponse resp = model.chat(List.of(msg));
```

### 音频、视频、PDF

```java
AudioContent.from("./audio.mp3"),
VideoContent.from("./video.mp4"),
PdfFileContent.from("./doc.pdf")
```

---

## 9. Output Guardrails（输出校验）

```java
class PIIGuard implements OutputGuardrail {
    @Override
    public OutputGuardrailResult validate(AiMessage msg) {
        if (containsPII(msg.text())) {
            return retry("输出包含敏感信息，请重新生成");
        }
        return success();
    }
}

Assistant assistant = AiServices.builder(Assistant.class)
    .chatLanguageModel(model)
    .outputGuardrails(new PIIGuard())
    .build();
```

失败时可自动重试或终止。

---

## 10. Observability

### 日志

```java
OpenAiChatModel model = OpenAiChatModel.builder()
    .apiKey(...).modelName("gpt-4o")
    .logRequests(true)
    .logResponses(true)
    .build();
```

### Listeners

```java
ChatModelListener listener = new ChatModelListener() {
    @Override
    public void onRequest(ChatModelRequestContext ctx) {
        log.info("Request: {}", ctx.chatRequest());
    }
    @Override
    public void onResponse(ChatModelResponseContext ctx) {
        log.info("Tokens: {}", ctx.chatResponse().tokenUsage());
    }
};
```

### Micrometer
Spring Boot 集成自动暴露 metrics。

---

## 11. Agentic Java（多 Agent）

LangChain4j 2024 年推出的 Multi-Agent 子项目：

```xml
<dependency>
    <groupId>dev.langchain4j</groupId>
    <artifactId>langchain4j-agentic</artifactId>
</dependency>
```

### Agent 定义

```java
interface Researcher {
    @SystemMessage("你是资深研究员")
    @UserMessage("调研主题：{{topic}}")
    @Agent
    String research(@V("topic") String topic);
}

interface Writer {
    @SystemMessage("你是技术作家")
    @UserMessage("基于研究写文章：{{research}}")
    @Agent
    String write(@V("research") String research);
}
```

### Sequential 流水线

```java
var flow = AgenticServices.sequenceBuilder()
    .subAgents(researcher, writer)
    .build();

String article = flow.invoke("AI Agent 2025 趋势");
```

### Supervisor 模式

```java
var supervisor = AgenticServices.supervisorBuilder()
    .subAgents(researcher, coder, reviewer)
    .build();
```

---

## 12. 生产最佳实践

### 1. 重试

```java
OpenAiChatModel.builder()
    .maxRetries(3)
    .timeout(Duration.ofSeconds(60))
    .build();
```

### 2. 降级（多模型）

```java
ChatLanguageModel fallback = OllamaChatModel.builder().baseUrl(...).build();
try {
    return model.generate(...);
} catch (Exception e) {
    return fallback.generate(...);
}
```

### 3. 测试 Mock

```java
ChatLanguageModel mock = ChatLanguageModel.from("mocked response");
```

### 4. 本地模型

```java
ChatLanguageModel model = OllamaChatModel.builder()
    .baseUrl("http://localhost:11434")
    .modelName("qwen2.5:72b")
    .build();
```

### 5. MCP 集成

```xml
<dependency>
    <groupId>dev.langchain4j</groupId>
    <artifactId>langchain4j-mcp</artifactId>
</dependency>
```

```java
McpTransport transport = new StdioMcpTransport.Builder()
    .command(List.of("npx", "-y", "@modelcontextprotocol/server-filesystem", "/data"))
    .build();
McpClient mcpClient = new DefaultMcpClient.Builder().transport(transport).build();

List<ToolSpecification> tools = mcpClient.listTools();
```

---

## 13. 常见模式

### 1. 分类器

```java
interface Classifier {
    @UserMessage("判断情感（positive/negative/neutral）：{{text}}")
    Sentiment classify(@V("text") String text);
}

enum Sentiment { POSITIVE, NEGATIVE, NEUTRAL }
```

### 2. 结构化提取

```java
interface Extractor {
    @UserMessage("抽取信息：{{text}}")
    Invoice extract(@V("text") String text);
}

record Invoice(String number, LocalDate date, BigDecimal amount, List<Item> items) {}
```

### 3. 摘要器

```java
interface Summarizer {
    @UserMessage("生成 100 字摘要：{{article}}")
    String summarize(@V("article") String article);
}
```

### 4. Chat 对话

```java
interface Chat {
    String chat(@MemoryId String sessionId, @UserMessage String msg);
}
```

---

## 面试高频问题

**Q1：AI Services 是什么？**

LangChain4j 招牌特性：**声明式 LLM 接口**。开发者定义 Java 接口 + 注解（`@SystemMessage`、`@UserMessage`、`@V`、`@Tool`、`@MemoryId`），框架动态代理实现。

优势：
- 类型安全（返回类型自动映射）
- 声明式（接近 Spring Data Repository）
- 自动处理 Prompt 模板、记忆、工具
- 测试友好（Mock 接口）

一行代码从 Java 方法调用 → LLM 调用。

**Q2：LangChain4j 怎么做 RAG？**

两层：
- **底层**：DocumentLoader → Splitter → EmbeddingModel → EmbeddingStore 手动拼
- **高层**：`EasyRAG` + `AiServices.contentRetriever(...)` 自动注入检索结果

高级 RAG 用 `RetrievalAugmentor`：QueryTransformer（改写）+ QueryRouter（多源）+ ContentAggregator（重排序）+ ContentInjector（注入）。

**Q3：ChatMemory 如何多用户隔离？**

```java
interface MyAgent {
    String chat(@MemoryId String userId, @UserMessage String msg);
}
```

配合 `ChatMemoryProvider`，按 userId 从 Redis/DB 加载记忆：
```java
.chatMemoryProvider(id -> MessageWindowChatMemory.builder()
    .id(id).chatMemoryStore(redisStore).build())
```

**Q4：Tool Calling 怎么定义？**

```java
class Tools {
    @Tool("查询天气")
    String weather(@P("城市") String city) { ... }
}

AiServices.builder(Assistant.class).tools(new Tools()).build();
```

框架自动生成 JSON Schema、处理模型 tool_call、反射执行、结果回传循环。

**Q5：LangChain4j 和 Spring AI 区别？**

- **设计**：LangChain4j 模仿 LangChain Python；Spring AI 原生 Spring 风格
- **招牌**：LangChain4j = AiServices 声明式；Spring AI = Advisor Chain
- **集成**：两者都支持 Spring Boot；Spring AI 集成更深
- **生态**：两者都广泛，集成 15+ LLM
- **选择**：Spring 项目优先 Spring AI；非 Spring 或熟悉 LangChain 选 LangChain4j

**Q6：输出结构化数据如何保证可靠？**

- 返回类型是 POJO/Record 时，框架自动生成 JSON Schema
- OpenAI/Anthropic 支持 strict mode，保证符合 schema
- 失败时可用 `OutputGuardrail` 重试
- 生产环境建议加 try-catch 容错

**Q7：LangChain4j 支持流式吗？**

支持。用 `StreamingChatLanguageModel`：
```java
interface Chat { TokenStream chat(String q); }
Chat s = AiServices.create(Chat.class, streamingModel);
s.chat("...").onNext(System.out::print).start();
```

SSE/WebSocket 返回前端：把 `onNext` 回调拼成 Flux 发送。

**Q8：如何接入本地模型？**

Ollama 最简单：
```java
ChatLanguageModel m = OllamaChatModel.builder()
    .baseUrl("http://localhost:11434")
    .modelName("qwen2.5:72b")
    .build();
```

vLLM / LocalAI / LM Studio 暴露 OpenAI 兼容 API，用 `OpenAiChatModel` + 自定义 baseUrl 即可。

**Q9：Agentic Java 的 Multi-Agent 怎么用？**

```java
@Agent interface Researcher { ... }
@Agent interface Writer { ... }

var flow = AgenticServices.sequenceBuilder()
    .subAgents(researcher, writer)
    .build();
flow.invoke(topic);
```

支持 Sequential、Parallel、Supervisor、Loop 等模式。

**Q10：如何在生产中管理 Prompt？**

- **注解内联**：适合稳定 Prompt，版本跟代码走
- **外部文件**：`@Resource` 加载 `.txt`/`.md`，方便非技术人员改
- **DB**：从数据库动态加载，热更新
- **Prompt 版本管理平台**：Langfuse、PromptLayer

生产推荐 DB + 缓存，配合版本号和灰度。
