# Elasticsearch

## 1. 概览

### 定位
分布式搜索引擎 + 分析引擎，基于 Apache Lucene。典型用途：
- **搜索**：电商商品搜索、日志搜索
- **日志分析**：ELK/EFK 栈
- **指标分析**：时序数据
- **向量搜索**：ES 8.0+ 支持 KNN
- **全文 + 结构化混合查询**

### 核心特点
- 近实时（NRT，~1 秒）
- 分布式、自动分片、自动复制
- RESTful API
- JSON 文档存储

### 版本要点
- 7.x → 8.x：去 type（一个 index 只有一种 doc）
- 8.0：向量搜索 KNN、安全默认开启
- 8.8+：ELSER 稀疏向量、Semantic Search 成熟

---

## 2. 核心概念

### 层级关系

```
ES Cluster（集群）
 └── Node（节点）
      └── Index（索引）≈ 数据库 database
           └── Shard（分片）
                └── Lucene Segment
           └── Document（文档）≈ 一行记录
                └── Field（字段）
```

### Index（索引）
- 逻辑的数据容器
- 物理上由多个 Shard 组成
- 创建时指定分片数（之后不可改），副本数（可改）

### Shard（分片）
- **Primary Shard**：主分片，承担写入
- **Replica Shard**：副本分片，读分担 + 高可用
- 分片数 × 副本数决定总存储数

### Document
- 最小数据单元，JSON 格式
- 每个 doc 有唯一 `_id`
- 按 `_id` hash 路由到具体 shard

### Mapping
- 类似数据库 schema，定义字段类型
- 动态映射（auto）或显式定义
- 一旦定义不能改类型（需要 reindex）

---

## 3. 快速上手

### 安装（Docker）
```bash
docker run -d -p 9200:9200 -e "discovery.type=single-node" \
    -e "xpack.security.enabled=false" \
    elasticsearch:8.13.0
```

### 创建索引 + 映射

```bash
PUT /products
{
  "settings": {
    "number_of_shards": 3,
    "number_of_replicas": 1,
    "analysis": {
      "analyzer": {
        "my_analyzer": {
          "type": "custom",
          "tokenizer": "ik_max_word"
        }
      }
    }
  },
  "mappings": {
    "properties": {
      "name": {
        "type": "text",
        "analyzer": "my_analyzer",
        "fields": {
          "keyword": { "type": "keyword" }
        }
      },
      "price": { "type": "double" },
      "category_id": { "type": "keyword" },
      "created_at": { "type": "date" },
      "description_vector": {
        "type": "dense_vector",
        "dims": 1024,
        "index": true,
        "similarity": "cosine"
      }
    }
  }
}
```

### 增删改查

```bash
# 写入
POST /products/_doc/1
{ "name": "iPhone 15", "price": 7999, "category_id": "phone" }

# 更新
POST /products/_update/1
{ "doc": { "price": 6999 } }

# 查询单条
GET /products/_doc/1

# 搜索
POST /products/_search
{
  "query": {
    "match": { "name": "iPhone" }
  }
}

# 删除
DELETE /products/_doc/1
```

---

## 4. Mapping 字段类型

### 核心类型
| 类型 | 说明 | 用途 |
|------|------|------|
| `text` | 全文 | 要分词的（标题、内容） |
| `keyword` | 精确值 | ID、枚举、标签 |
| `long`/`integer`/`short`/`byte` | 整数 | - |
| `double`/`float`/`half_float`/`scaled_float` | 浮点 | 价格、得分 |
| `date` | 日期 | 时间戳 |
| `boolean` | 布尔 | - |
| `object` | 嵌套对象 | JSON 对象 |
| `nested` | 嵌套数组对象 | 对象数组需独立查询 |
| `geo_point` | 地理坐标 | 位置 |
| `ip` | IP 地址 | - |
| `dense_vector` | 密集向量 | 语义搜索（8.0+） |
| `sparse_vector` | 稀疏向量 | ELSER（8.8+） |
| `completion` | 自动补全 | Suggester |

### text vs keyword

```json
{
  "name": {
    "type": "text",           // 全文检索
    "fields": {
      "keyword": {
        "type": "keyword"      // 精确匹配、聚合、排序
      }
    }
  }
}
```

查询：
- `match` 走 `name`（分词）
- `term`、聚合、排序走 `name.keyword`

### 分词器

**内置**：
- `standard`（默认）
- `simple`、`whitespace`、`keyword`
- `english`、`chinese`（差）

**中文**：
- **IK**：ik_max_word（细）、ik_smart（粗）
- **Jieba**
- **HanLP**

```bash
# 安装 IK
bin/elasticsearch-plugin install https://github.com/infinilabs/analysis-ik/releases/download/v8.13.0/elasticsearch-analysis-ik-8.13.0.zip
```

---

## 5. 查询 DSL

### match（全文）

```json
{
  "query": {
    "match": { "title": "elasticsearch 教程" }
  }
}
```

### term（精确）

```json
{
  "query": {
    "term": { "status": "active" }
  }
}
```

### range（范围）

```json
{
  "query": {
    "range": {
      "price": { "gte": 100, "lte": 1000 }
    }
  }
}
```

### bool（组合）

```json
{
  "query": {
    "bool": {
      "must": [ { "match": { "title": "java" } } ],
      "filter": [ { "term": { "category": "book" } } ],
      "must_not": [ { "term": { "status": "deleted" } } ],
      "should": [ { "match": { "tag": "backend" } } ],
      "minimum_should_match": 1
    }
  }
}
```

- **must**：必须满足，参与评分
- **filter**：必须满足，**不评分、可缓存**（性能好）
- **must_not**：必须不满足
- **should**：应该满足（OR 关系）

### 多字段匹配

```json
{
  "query": {
    "multi_match": {
      "query": "java 教程",
      "fields": ["title^3", "content", "tags^2"],  // ^ 权重
      "type": "best_fields"  // / most_fields / cross_fields / phrase
    }
  }
}
```

### 短语匹配

```json
{
  "query": {
    "match_phrase": {
      "content": "Spring Boot"  // 保持顺序
    }
  }
}
```

### 通配符 / 正则

```json
{ "query": { "wildcard": { "name": "iph*" } } }
{ "query": { "regexp": { "name": "iphone[0-9]+" } } }
```

### prefix（前缀）

```json
{ "query": { "prefix": { "name": "iph" } } }
```

### fuzzy（模糊）

```json
{ "query": { "fuzzy": { "name": { "value": "iphne", "fuzziness": 2 } } } }
```

### function_score（自定义评分）

```json
{
  "query": {
    "function_score": {
      "query": { "match": { "name": "phone" } },
      "functions": [
        { "filter": { "term": { "featured": true } }, "weight": 2 },
        { "gauss": { "created_at": { "origin": "now", "scale": "30d" } } }
      ],
      "score_mode": "sum",
      "boost_mode": "multiply"
    }
  }
}
```

---

## 6. 分页

### from / size（浅分页）
```json
{ "from": 0, "size": 20 }
```
**问题**：`from + size` 越大越慢，默认上限 `from + size <= 10000`。

### search_after（推荐深分页）
```json
{
  "size": 20,
  "sort": [{ "created_at": "desc" }, { "_id": "asc" }],
  "search_after": [1680000000000, "abc123"]
}
```
只能向后翻页，用上一页最后一条的 sort 值。

### scroll（大数据量导出，已不推荐）
快照，适合一次性遍历。`search_after` 已经替代。

### PIT（Point in Time，8+）
`search_after` + PIT 形成快照式深分页。

---

## 7. 聚合（Aggregation）

### Metric（指标）

```json
{
  "aggs": {
    "avg_price": { "avg": { "field": "price" } },
    "max_price": { "max": { "field": "price" } },
    "stats": { "stats": { "field": "price" } }
  }
}
```

### Bucket（分桶）

```json
{
  "aggs": {
    "by_category": {
      "terms": { "field": "category.keyword", "size": 10 }
    },
    "by_day": {
      "date_histogram": {
        "field": "created_at",
        "calendar_interval": "day"
      }
    },
    "price_ranges": {
      "range": {
        "field": "price",
        "ranges": [{ "to": 100 }, { "from": 100, "to": 500 }, { "from": 500 }]
      }
    }
  }
}
```

### 嵌套聚合

```json
{
  "aggs": {
    "by_category": {
      "terms": { "field": "category.keyword" },
      "aggs": {
        "avg_price": { "avg": { "field": "price" } }
      }
    }
  }
}
```

---

## 8. 全文检索原理

### 倒排索引

```
文档 1: "ES is fast"
文档 2: "ES is powerful"

倒排索引：
es       → [1, 2]
is       → [1, 2]
fast     → [1]
powerful → [2]
```

查询 "ES fast" → 求 `[1,2] ∩ [1]` = `[1]`。

### 分析器流程
```
原文 → Character Filter（预处理，如去 HTML） → Tokenizer（分词）→ Token Filter（小写、stop words、同义词）→ 索引
```

### 评分（BM25）
默认评分算法（7.x 后替换 TF-IDF），考虑：
- TF：词频
- IDF：逆文档频率
- 文档长度归一化

### Segment
- 每个 Shard 由多个 Lucene Segment 组成
- Segment 不可变：写入缓冲 → Refresh（生成新 Segment）→ Merge
- `refresh_interval` 决定"近实时"延迟（默认 1s）

---

## 9. 向量搜索（8.0+）

### 定义字段

```json
{
  "embedding": {
    "type": "dense_vector",
    "dims": 1024,
    "index": true,
    "similarity": "cosine"  // or dot_product, l2_norm, max_inner_product
  }
}
```

### KNN 查询

```json
{
  "knn": {
    "field": "embedding",
    "query_vector": [0.1, 0.2, ...],
    "k": 10,
    "num_candidates": 100,
    "filter": [{ "term": { "category": "tech" } }]
  }
}
```

### Hybrid Search（混合检索）

```json
{
  "query": {
    "match": { "content": "java spring" }
  },
  "knn": {
    "field": "embedding",
    "query_vector": [...],
    "k": 10,
    "num_candidates": 100
  },
  "rank": {
    "rrf": {}  // Reciprocal Rank Fusion，8.8+
  }
}
```

### ELSER（稀疏向量）
ES 内置的稀疏嵌入模型，免部署：
```json
{ "sparse_vector": { "inference_id": "my-elser" } }
```

---

## 10. 集群架构

### 节点角色
- **Master**：管理集群元数据
- **Data**：存储数据、执行查询
- **Ingest**：预处理数据
- **Coordinating**：协调请求（默认所有节点）

### 高可用
- 分片必须有副本（`number_of_replicas >= 1`）
- 节点数 ≥ 分片副本数
- Master 节点至少 3（quorum）

### 分片策略
- 初始分片数 = 预估数据量 / 30-50GB 每分片
- 分片过多：查询扇出大、小文件多
- 分片过少：单分片过大、难扩容

### 数据热冷
- Hot（SSD，近数据）
- Warm（HDD，近期数据）
- Cold（S3，归档）

---

## 11. 性能调优

### 索引阶段
- **批量**：`_bulk` API（5-15MB/批）
- **Refresh 间隔**：日志类场景设 `30s` 或 `-1`（写完再开启）
- **副本延迟**：大批量导入前 `number_of_replicas: 0`，完后再开
- **Translog**：`index.translog.durability: async`（可接受数据丢失时）

### 查询阶段
- **filter > query**：能用 filter 就别用 query（filter 可缓存）
- **避免 wildcard 前缀通配**：`*abc` 极慢
- **聚合用 keyword**：不要 text
- **分页**：深分页用 search_after
- **字段过滤**：`_source` 只取需要字段

### Mapping
- 不需要搜索的字段设 `index: false`
- 不需要聚合的字段设 `doc_values: false`
- 动态映射字段爆炸 → 设 `dynamic: strict`

### JVM
- 堆内存 ≤ 32GB（压缩指针）
- 堆外内存 ≥ 堆内存（Lucene 用）

---

## 12. Spring Data Elasticsearch

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-data-elasticsearch</artifactId>
</dependency>
```

```yaml
spring.elasticsearch:
  uris: http://localhost:9200
```

### Entity

```java
@Document(indexName = "products")
public class Product {
    @Id private String id;
    @Field(type = FieldType.Text, analyzer = "ik_max_word") private String name;
    @Field(type = FieldType.Keyword) private String category;
    @Field(type = FieldType.Double) private Double price;
    @Field(type = FieldType.Date) private Instant createdAt;
}
```

### Repository

```java
public interface ProductRepository extends ElasticsearchRepository<Product, String> {
    List<Product> findByNameContainingAndCategoryEquals(String name, String category);
    @Query("{\"match\": {\"name\": \"?0\"}}")
    List<Product> searchByName(String name);
}
```

### 高级查询（ElasticsearchOperations）

```java
@Autowired ElasticsearchOperations operations;

Query query = new CriteriaQuery(
    new Criteria("name").matches("iphone")
        .and(new Criteria("price").greaterThan(1000))
);
SearchHits<Product> hits = operations.search(query, Product.class);
```

### NativeQuery（8.5+，推荐）

```java
NativeQuery nativeQuery = NativeQuery.builder()
    .withQuery(q -> q
        .bool(b -> b
            .must(m -> m.match(mq -> mq.field("name").query("iphone")))
            .filter(f -> f.range(r -> r.field("price").gte(JsonData.of(1000))))
        ))
    .withPageable(PageRequest.of(0, 20))
    .build();
SearchHits<Product> hits = operations.search(nativeQuery, Product.class);
```

---

## 13. 实战：商品搜索

### 需求
- 商品名/描述全文
- 按类目/品牌过滤
- 价格区间
- 排序：相关性/销量/价格
- 聚合：类目分布、价格区间

### 查询

```json
POST /products/_search
{
  "from": 0, "size": 20,
  "query": {
    "bool": {
      "must": [
        {
          "multi_match": {
            "query": "苹果手机",
            "fields": ["name^3", "description"],
            "type": "best_fields"
          }
        }
      ],
      "filter": [
        { "term": { "category_id": "phone" } },
        { "range": { "price": { "gte": 1000, "lte": 10000 } } }
      ]
    }
  },
  "sort": [
    { "_score": "desc" },
    { "sales_count": "desc" }
  ],
  "aggs": {
    "brands": { "terms": { "field": "brand.keyword", "size": 10 } },
    "price_ranges": {
      "range": {
        "field": "price",
        "ranges": [{ "to": 2000 }, { "from": 2000, "to": 5000 }, { "from": 5000 }]
      }
    }
  },
  "highlight": {
    "fields": { "name": {}, "description": {} },
    "pre_tags": ["<em>"], "post_tags": ["</em>"]
  }
}
```

---

## 14. ELK 日志栈

```
Application → Filebeat → Logstash/Kafka → Elasticsearch → Kibana
```

- **Filebeat**：轻量采集
- **Logstash**：解析/过滤
- **Elasticsearch**：存储 + 搜索
- **Kibana**：可视化

### 替代方案
- **EFK**：用 Fluentd 替 Logstash
- **Loki**：Grafana 栈，成本低
- **ClickHouse**：日志量巨大时更经济

---

## 面试高频问题

**Q1：ES 的倒排索引原理？**

每个 term 维护一个"哪些文档包含该词 + 位置信息"的列表。查询时：
1. 分析器把 query 分词
2. 在倒排索引中找每个 term 对应的 doc list
3. 求交集/并集（bool query）
4. 按 BM25 评分排序
5. 返回 top N

对比 B+ 树：B+ 树按 key 查值；倒排索引按值查 key。

**Q2：text 和 keyword 的区别？**

- **text**：分词后索引，用于全文检索（match）
- **keyword**：不分词，整体索引，用于精确匹配（term）、聚合、排序

常用 `"type": "text", "fields": {"keyword": {"type": "keyword"}}` 双字段。

**Q3：ES 写入流程？**

1. 请求到协调节点
2. 路由到主分片（`hash(_id) % primary_shards`）
3. 写 In-memory Buffer + Translog
4. 默认 1s refresh：buffer → Segment（可搜索）
5. 副本同步
6. 定期 flush：Segment 刷盘，清 Translog
7. 定期 merge：小 Segment 合并

**Q4：ES 如何保证高可用？**

- **副本**：每个主分片至少 1 个副本
- **自动故障转移**：主分片节点宕机，副本升主
- **Master 选举**：Quorum 机制
- **分片再平衡**：节点加入/离开自动调整

**Q5：深分页怎么解决？**

- `from + size`：默认上限 10000
- **search_after**（推荐）：基于 sort 值向后翻
- **scroll**：全量扫描用（已不推荐，PIT 替代）
- **PIT**：搜索快照

**Q6：filter 和 query 区别？**

- **query**：参与评分，不可缓存
- **filter**：不评分，结果可缓存（bitset）

能用 filter 就别用 query。`bool.must` 改 `bool.filter` 性能显著提升。

**Q7：ES 如何分词中文？**

内置 standard 分词器按字切分，不适合中文。用：
- **IK 插件**：`ik_max_word`（细粒度）、`ik_smart`（粗粒度）
- **Jieba**、**HanLP** 等

字段 mapping 指定：`"analyzer": "ik_max_word"`。

**Q8：ES 适合做主存储吗？**

不适合：
- 非事务（无 ACID）
- Update 成本高（删除重建）
- 磁盘占用大（2-3 倍原始数据）
- 不适合频繁更新小字段

适合：
- 搜索、日志、分析副库
- 主库（MySQL/Mongo）+ ES 双写
- 用 Canal / Debezium 同步

**Q9：ES 向量搜索和专业向量库区别？**

| 维度 | ES 向量 | 专业向量库（Qdrant/Milvus） |
|------|---------|----------------------------|
| 混合检索 | 原生（RRF） | 需自拼 |
| 规模 | 亿级可行 | 十亿级优化好 |
| 索引 | HNSW | HNSW/IVF/PQ 等 |
| 成本 | 通用集群共用 | 专用，可能更省 |
| 生态 | Kibana 等成熟 | 轻量 |

**选择**：已有 ES 栈 → ES 向量；新项目纯向量 → 专业库；重度 Hybrid → ES。

**Q10：ES 集群规划经验？**

- **分片数**：初始按 `数据量 / 30GB` 估算，不易后改
- **副本数**：生产至少 1
- **节点**：Master 3 个（奇数防脑裂），Data 根据数据量
- **JVM 堆**：最大 32GB（压缩指针），剩余给 Lucene cache
- **硬件**：SSD、高内存、CPU 和网络充足
