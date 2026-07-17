# RAG 项目实战模板

本章提供一个**可直接落地**的企业 RAG 项目模板，覆盖需求设计、技术选型、关键实现、评估、上线。用于面试讲项目经验。

## 1. 项目背景（故事）

### 业务需求
某公司有大量内部文档（产品手册、技术文档、HR 政策、法务合同），员工常要花时间搜索。要求构建**企业知识库问答系统**：
- 员工自然语言提问，系统基于内部文档回答
- 附引用（哪个文档、哪段话）
- 支持多数据源：Confluence + Notion + PDF + S3
- 支持中英双语
- 权限隔离：员工只能查到其有权限的文档
- 响应时间 < 5 秒
- 日均查询 5 万

### 价值
- 新人上手从 2 周 → 2 天
- 客服一线人效 +30%
- 知识沉淀，减少专家被打扰

---

## 2. 技术选型

| 模块 | 选型 | 理由 |
|------|------|------|
| 后端 | Spring Boot 3 + Spring AI | Java 栈统一、生产成熟 |
| 前端 | React + SSE | 流式体验 |
| 模型 | Claude Sonnet（主）+ 本地 Qwen（兜底） | 质量 + 成本 |
| Embedding | bge-m3（多语） / text-embedding-3-large | 中英效果好 |
| 向量库 | Qdrant | 性能、过滤、云/自建均可 |
| 全文检索 | Elasticsearch | Hybrid Search |
| 缓存 | Redis | 语义缓存、Session |
| 元数据 | PostgreSQL | 文档/权限/审计 |
| MQ | Kafka | 异步入库 |
| 编排 | Kubernetes + Helm | 标准 |
| 可观测 | Prometheus + Grafana + Langfuse | 三件套 |

---

## 3. 架构图

```
┌──────────────┐
│  Frontend    │ (React, SSE)
└──────┬───────┘
       │
       ▼
┌──────────────────────────────────────────┐
│         API Gateway (Spring Cloud)        │
│      鉴权 / 限流 / 租户上下文             │
└──────┬───────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────┐
│            RAG Service                    │
│  ┌────────────────────────────────────┐  │
│  │ Query Understanding                │  │
│  │  - 意图分类                         │  │
│  │  - 查询改写（HyDE、多查询）         │  │
│  │  - 子问题分解                       │  │
│  └─────────────┬──────────────────────┘  │
│                │                           │
│  ┌─────────────▼──────────────────────┐  │
│  │ Retrieval                          │  │
│  │  - 向量检索 (Qdrant)               │  │
│  │  - BM25 (Elasticsearch)            │  │
│  │  - RRF 融合                        │  │
│  │  - Reranker (bge-reranker-large)   │  │
│  └─────────────┬──────────────────────┘  │
│                │                           │
│  ┌─────────────▼──────────────────────┐  │
│  │ Generation                         │  │
│  │  - Prompt 组装                     │  │
│  │  - Claude Sonnet (Streaming)       │  │
│  │  - 引用提取                        │  │
│  └─────────────┬──────────────────────┘  │
│                │                           │
│  ┌─────────────▼──────────────────────┐  │
│  │ Post-Processing                    │  │
│  │  - PII 脱敏                        │  │
│  │  - 幻觉检测                        │  │
│  │  - 引用校对                        │  │
│  └────────────────────────────────────┘  │
└──────────────────────────────────────────┘
       │                        │
       ▼                        ▼
┌──────────────┐         ┌──────────────┐
│  Qdrant      │         │  Elasticsearch│
│  (Vector)    │         │  (BM25)      │
└──────────────┘         └──────────────┘

┌──────────────────────────────────────────┐
│    Ingestion Pipeline (Async, Kafka)      │
│  Connector → Parse → Chunk → Embed →     │
│  Index → Sync to Vector & ES             │
└──────────────────────────────────────────┘
```

---

## 4. 数据 Ingestion Pipeline

### 数据源 Connector

```java
public interface DataSourceConnector {
    String sourceName();
    Flux<RawDocument> fetchIncremental(Instant since);
}

@Component
public class ConfluenceConnector implements DataSourceConnector {
    @Override
    public Flux<RawDocument> fetchIncremental(Instant since) {
        return Flux.from(confluenceClient.searchPages()
            .modifiedSince(since)
            .stream())
            .map(page -> new RawDocument(
                page.id(), page.title(), page.body(),
                Map.of("space", page.space(), "url", page.url(),
                    "author", page.author(), "acl", page.restrictions())
            ));
    }
}
```

### Parse（格式转换）

```java
@Component
public class DocumentParser {
    public String parse(RawDocument doc) {
        return switch (doc.contentType()) {
            case "text/html" -> htmlToMarkdown(doc.content());
            case "application/pdf" -> pdfToMarkdown(doc.content());
            case "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                -> docxToMarkdown(doc.content());
            default -> doc.content();
        };
    }
}
```

**PDF 推荐**：复杂版面用 LlamaParse / Marker / Unstructured。

### Chunk（切分）

```java
public List<Chunk> chunk(String markdown, ChunkConfig cfg) {
    // 先按 Markdown 结构切（H1/H2/H3）
    List<Section> sections = splitByHeadings(markdown);
    List<Chunk> chunks = new ArrayList<>();
    for (Section s : sections) {
        if (countTokens(s.content()) <= cfg.maxTokens()) {
            chunks.add(new Chunk(s.content(), s.headings()));
        } else {
            // 大段再按句子滑动
            chunks.addAll(slidingWindow(s, cfg.maxTokens(), cfg.overlap()));
        }
    }
    return chunks;
}
```

**关键**：保留 heading 路径作为 context（例如 "产品手册 > API > 鉴权 > OAuth 流程"）。

### Embedding（批量）

```java
public List<float[]> embedBatch(List<String> texts) {
    return texts.stream()
        .map(cache::get)
        .collect(...);
    // 未命中的批量调用 Embedding API
}
```

**优化**：
- Embedding 结果按文本 hash 缓存（重复片段直接返回）
- 批量大小按 API 限制（OpenAI 2048、BGE 32）
- 并发调用，控制在限流范围

### 入库（双写）

```java
@Transactional
public void index(Chunk chunk, float[] embedding) {
    // 1. Postgres 存元数据
    chunkRepo.save(new ChunkEntity(chunk.id(), chunk.docId(),
        chunk.content(), chunk.metadata()));
    // 2. Qdrant 存向量
    qdrant.upsert("chunks", List.of(new Point(chunk.id(), embedding, chunk.metadata())));
    // 3. ES 存文本（BM25）
    esClient.index(req -> req.index("chunks").id(chunk.id())
        .document(Map.of("content", chunk.content(), "metadata", chunk.metadata())));
}
```

**一致性**：失败时走补偿任务重试。

### 增量同步

- **轮询**：每 10 分钟拉取各数据源 `modified_since`
- **Webhook**：Confluence/Notion 支持，实时
- **删除**：源端删除 → 触发软删除（标记），定期硬删

---

## 5. 查询流程

### Query Understanding

```java
@Component
public class QueryProcessor {
    public ProcessedQuery process(String rawQuery, Context ctx) {
        // 1. 意图分类（简单/复杂/多源）
        Intent intent = classifier.classify(rawQuery);
        // 2. 查询改写（HyDE）
        String hypothetical = hyde.generate(rawQuery);
        // 3. 多查询（提升召回）
        List<String> expanded = multiQuery.expand(rawQuery, 3);
        // 4. 子问题分解（复杂问题）
        List<String> subQuestions = intent == Intent.COMPLEX
            ? decomposer.decompose(rawQuery)
            : List.of(rawQuery);
        return new ProcessedQuery(rawQuery, hypothetical, expanded, subQuestions);
    }
}
```

### Retrieval（Hybrid）

```java
public List<Document> retrieve(String query, Context ctx, int topK) {
    // 1. 向量检索
    float[] qEmb = embedder.embed(query);
    Filter filter = buildAclFilter(ctx.user());
    List<ScoredPoint> vecResults = qdrant.search("chunks", qEmb, filter, topK * 2);

    // 2. BM25 检索
    List<Hit> bmResults = es.search("chunks", query, filter, topK * 2);

    // 3. RRF 融合
    Map<String, Double> fused = rrf(vecResults, bmResults);

    // 4. Top K
    return fused.entrySet().stream()
        .sorted(Map.Entry.<String, Double>comparingByValue().reversed())
        .limit(topK)
        .map(e -> loadDoc(e.getKey()))
        .toList();
}

private Map<String, Double> rrf(List<ScoredPoint> vec, List<Hit> bm25) {
    Map<String, Double> scores = new HashMap<>();
    int k = 60;
    for (int i = 0; i < vec.size(); i++) {
        scores.merge(vec.get(i).id(), 1.0 / (k + i + 1), Double::sum);
    }
    for (int i = 0; i < bm25.size(); i++) {
        scores.merge(bm25.get(i).id(), 1.0 / (k + i + 1), Double::sum);
    }
    return scores;
}
```

### Rerank（精排）

```java
@Component
public class Reranker {
    public List<Document> rank(String query, List<Document> docs, int topN) {
        List<Pair> pairs = docs.stream()
            .map(d -> new Pair(query, d.content())).toList();
        List<Float> scores = bgeReranker.score(pairs);
        return IntStream.range(0, docs.size())
            .boxed()
            .sorted(Comparator.comparing(i -> -scores.get(i)))
            .limit(topN)
            .map(docs::get)
            .toList();
    }
}
```

### Prompt 组装

`prompts/rag-qa.md`：
```
你是公司知识助手。请基于以下文档回答用户问题：

文档：
{{#documents}}
[文档 {{index}}] 来源：{{source}}
{{content}}
---
{{/documents}}

规则：
1. 只基于文档内容回答，不要编造
2. 若文档不足以回答，回复"我在现有资料中没找到相关信息"
3. 在回答末尾标注引用，格式：[^1][^2]

用户问题：{{question}}
```

### 生成与流式

```java
@Service
public class RagService {
    public Flux<RagEvent> query(String question, Context ctx) {
        return Flux.create(sink -> {
            // 检索
            sink.next(new RagEvent.Retrieving());
            List<Document> docs = pipeline.retrieve(question, ctx, 10);
            List<Document> ranked = reranker.rank(question, docs, 5);
            sink.next(new RagEvent.Retrieved(ranked.size()));

            // 生成（流式）
            String prompt = promptLoader.render("rag-qa",
                Map.of("documents", ranked, "question", question));

            chatClient.prompt()
                .user(prompt)
                .stream()
                .content()
                .doOnNext(chunk -> sink.next(new RagEvent.Token(chunk)))
                .doOnComplete(() -> {
                    // 引用提取
                    List<Citation> citations = extractCitations(accumulated, ranked);
                    sink.next(new RagEvent.Citations(citations));
                    sink.complete();
                })
                .doOnError(sink::error)
                .subscribe();
        });
    }
}
```

---

## 6. 引用机制

### 引用标注
在 Prompt 中要求 LLM 输出 `[^1]` 格式，Generation 完成后解析：

```java
Pattern p = Pattern.compile("\\[\\^(\\d+)\\]");
Matcher m = p.matcher(answer);
Set<Integer> indices = new HashSet<>();
while (m.find()) {
    indices.add(Integer.parseInt(m.group(1)));
}
List<Citation> citations = indices.stream()
    .map(i -> new Citation(i, documents.get(i - 1).source(), documents.get(i - 1).url()))
    .toList();
```

### 引用校验
抽查：检查引用的段落是否真的支撑结论（避免幻觉引用）：

```java
boolean isFaithful = judge.evaluate("""
    答案：{answer}
    引用文档：{cited}
    答案是否严格基于引用文档？是/否
    """);
```

---

## 7. 权限与安全

### 文档级 ACL

```sql
CREATE TABLE document_acl (
    doc_id VARCHAR(64),
    principal VARCHAR(128),  -- user_id or group
    PRIMARY KEY (doc_id, principal)
);
```

### 检索时过滤

```java
Filter buildAclFilter(User user) {
    Set<String> principals = new HashSet<>();
    principals.add(user.id());
    principals.addAll(user.groups());
    return Filter.match("allowed_principals", principals);
}
```

Qdrant 支持 payload 过滤：
```python
vectorstore 索引时 metadata 加 allowed_principals: ["user-1", "group-eng"]
检索时 filter = {"allowed_principals": {"$in": [current_user_id, ...groups]}}
```

### 跨权限警示
用户问"HR 政策"但无权限时：明确告知"您无权访问相关文档"，**不要泄露文档存在性**。

---

## 8. 评估体系

### 黄金集构建
- 运营 / 产品标注 300 个高频问题 + 标准答案 + 引用
- 每月新增 50 个
- 每季度抽查 100 个线上真实 query 标注

### 自动评估指标

**Ragas 三件套**：
- **Faithfulness**：回答是否基于检索结果（抗幻觉）
- **Answer Relevancy**：答案是否切题
- **Context Precision**：检索结果是否相关

```python
from ragas import evaluate
result = evaluate(dataset, metrics=[faithfulness, answer_relevancy, context_precision])
```

### 人工评估
- 每周抽样 100 个线上 case 盲评
- 维度：正确性（是否正确）、完整性、清晰度、引用准确

### 线上反馈
前端提供 👍/👎 按钮 + 自由反馈文本，持续采集。

### 评估流水线
```
PR 合并 → 跑黄金集评估 → 若分数降 > 2% 则告警 → 人工审核
        → 通过后部署到 staging → staging 每日自动跑
        → 每月生产 A/B 全量对比
```

---

## 9. 生产问题与优化

### 问题 1：首 Token 延迟高（> 3s）
**原因**：检索 + Rerank 串行，LLM 冷启动。
**优化**：
- 检索和意图分类并行
- Prompt Caching（System Prompt + 工具定义稳定）
- 预热：热门 query 定时刷
- 流式输出，首 Token 出现早

### 问题 2：幻觉率高
**原因**：检索结果质量不够，LLM 自行补。
**优化**：
- 加强 Prompt "严禁编造"
- 检索 top-k 从 5 提到 10
- 加 Reranker 提精度
- 检索结果 < 3 条或分数 < 阈值时直接答"未找到"

### 问题 3：长尾问题召回差
**原因**：专业术语 Embedding 不敏感。
**优化**：
- 查询扩展（同义词词典、多查询）
- HyDE（先生成假设答案再检索）
- 精调 Embedding 模型（用点击日志做对比学习）

### 问题 4：并发下 Qdrant 压力大
**优化**：
- 热门向量本地 Caffeine 缓存（热点文档检索结果缓存 10s）
- Qdrant 水平分片
- Rerank 放独立服务

### 问题 5：文档频繁更新
**优化**：
- Webhook 实时同步
- Chunk 粒度标识 version，旧版本延迟删除（10 分钟），防止在途查询失败
- 语义缓存的 TTL 按文档更新频率动态

---

## 10. 性能与容量指标（示例）

| 指标 | 目标 | 实现 |
|------|------|------|
| 首 Token | < 1.5s | 缓存 + 流式 |
| 总响应 | < 5s | - |
| QPS | 50 | 4 副本 Pod |
| 文档总量 | 10 万篇 | - |
| Chunk 总量 | 500 万 | Qdrant 16GB |
| 向量维度 | 1024（bge-m3） | - |
| 召回率@10 | > 85% | Hybrid + Rerank |
| Faithfulness | > 0.9 | Prompt 约束 + 引用 |
| 月度成本 | 2 万 USD | - |

---

## 11. 关键代码骨架（Spring AI 版）

```java
@Service
public class EnterpriseRagService {
    private final ChatClient chatClient;
    private final VectorStore vectorStore;
    private final SearchClient esClient;
    private final Reranker reranker;
    private final QueryProcessor queryProcessor;
    private final PromptTemplateLoader promptLoader;

    public Flux<RagEvent> chat(String question, User user) {
        ProcessedQuery pq = queryProcessor.process(question, user);

        return Flux.concat(
            Flux.just(RagEvent.status("retrieving")),
            Mono.fromCallable(() -> retrieveAndRank(pq, user))
                .flatMapMany(docs -> {
                    String prompt = promptLoader.render("rag-qa",
                        Map.of("documents", docs, "question", question));
                    return Flux.concat(
                        Flux.just(RagEvent.retrieved(docs.size())),
                        chatClient.prompt()
                            .user(prompt)
                            .stream()
                            .content()
                            .map(RagEvent::token),
                        Mono.just(RagEvent.done(extractCitations(docs)))
                    );
                })
        );
    }

    private List<Document> retrieveAndRank(ProcessedQuery pq, User user) {
        List<Document> all = new ArrayList<>();
        for (String q : pq.expandedQueries()) {
            all.addAll(vectorStore.similaritySearch(
                SearchRequest.builder()
                    .query(q)
                    .topK(10)
                    .filterExpression(buildAclFilter(user))
                    .build()));
            all.addAll(esClient.search(q, user, 10));
        }
        List<Document> deduped = deduplicate(all);
        return reranker.rank(pq.raw(), deduped, 5);
    }
}
```

---

## 12. 讲项目的结构（面试用）

### STAR 框架

**Situation**：公司文档分散在 4 个系统，员工日均搜索时间 1 小时，新人上手周期长。

**Task**：构建统一知识库问答系统，目标首月覆盖 80% 常见问题，P95 < 5s，成本可控。

**Action**（按此结构讲）：
1. **技术选型**：Spring AI + Claude + Qdrant + ES + bge-m3，理由…
2. **架构**：分层（接入/RAG 服务/Ingestion/向量库/ES），Hybrid 检索 + Rerank
3. **关键难点**：
   - ACL 权限（用 Qdrant metadata 过滤）
   - 长文档 chunk（按 Markdown 结构 + 滑窗）
   - 幻觉（Prompt 约束 + 引用校验）
   - 首 Token 延迟（并行检索 + 流式）
4. **评估**：Ragas 自动 + 人工抽检 + 线上 👍/👎
5. **生产**：K8s 部署、监控告警、灰度、A/B

**Result**：
- 日均查询 6 万（超预期）
- 首 Token P50 1.2s、P99 3.5s
- Faithfulness 0.92、用户满意度 4.3/5
- 员工日均节省 45 分钟
- 月度成本 1.8 万 USD（低于预算）

---

## 13. 面试常见追问

**Q：Chunk 大小怎么选？**
- 太小（< 200 tokens）：语义不完整
- 太大（> 1000 tokens）：检索不精
- 经验：**300-500 tokens**，overlap 50-100
- 结构化文档按 H2/H3 切，代码按函数切

**Q：为什么用 Hybrid 而非纯向量？**
向量擅语义但不擅精确（如专有名词、代码、版本号），BM25 反之。RRF 融合优势互补。实测 Hybrid 比纯向量召回+10-20%。

**Q：Rerank 为什么不只用向量相似度？**
向量只用余弦相似，Reranker 基于 Cross-Encoder 深度匹配 query-doc，精度更高。向量做粗排（top 50），Reranker 精排 top 5。

**Q：如何判断检索结果足够？**
- 分数阈值（< 0.7 视为无关）
- Top1 分数分布（和 Top2 差距小说明未找到明显答案）
- 让 LLM 先判断"能否基于这些文档回答"

**Q：多租户如何扩展？**
- 小租户：同一 collection，metadata 过滤
- 大租户：独立 collection
- 超大租户：独立向量库实例

**Q：上线 1 个月用户反馈"答不准"，如何优化？**
不要瞎改，先定位：
1. 采样差评 case
2. 看是检索问题还是生成问题（检索出来没有相关文档？还是检索对了 LLM 没答对？）
3. 分别优化：检索差 → Chunk / Embedding / Rerank；生成差 → Prompt / 模型
4. A/B 验证改动
5. 回归黄金集，确保没有退步

**Q：成本控制？**
- Claude Sonnet 作主模型（贵但质量好）
- 简单问题路由到 Haiku 或本地 Qwen
- Prompt Caching（System 稳定）
- 语义缓存（重复 query 命中）
- Embedding 缓存（文本 hash 命中）
- 批量 API（离线入库用 Batch，半价）

**Q：如何应对 LLM 切换？**
抽象 `LlmClient`，配置驱动。已准备 fallback 链：Claude → GPT-4o → 本地 Qwen。评估在不同模型上跑黄金集。
