# 高频八股综合（Java / Python / AI）

本章集中收录 Java + Python + AI 面试最常考的硬通货题目，每题给**直接可背的答案**。

## 一、Java 核心八股

### 1. JVM 内存结构
**答**：
- **堆（Heap）**：对象实例，分新生代（Eden + 2 Survivor）和老年代，G1/ZGC 等回收
- **方法区 / 元空间（Metaspace，JDK 8+）**：类元信息、运行时常量池（移到堆），元空间用本地内存
- **虚拟机栈**：线程私有，每个方法一个栈帧（局部变量表、操作数栈、返回地址）
- **本地方法栈**：native 方法用
- **程序计数器**：当前线程字节码行号

常见 OOM：堆 OOM、栈溢出 StackOverflow、Metaspace OOM、直接内存 OOM。

### 2. GC 算法与收集器
**答**：
- 算法：标记-清除、标记-复制、标记-整理、分代收集
- 分代：新生代用复制（Eden → Survivor），老年代用标记整理
- 收集器：
  - **Serial**：单线程
  - **Parallel Scavenge/Old**：吞吐量优先（JDK 8 默认）
  - **CMS**：低延迟，已废弃
  - **G1**：JDK 9+ 默认，分 Region，可预测停顿
  - **ZGC / Shenandoah**：亚毫秒级停顿，适合超大堆
- 判断对象存活：引用计数（有循环问题，JVM 不用）、可达性分析（GC Roots）

### 3. HashMap 原理（JDK 8）
**答**：
- 数组 + 链表 + 红黑树
- 链表长度 > 8 且 数组容量 > 64 转红黑树；红黑树节点 < 6 退化链表
- 扩容：load factor 0.75，扩容 2 倍
- 计算 index：`hash ^ (hash >>> 16)` 扰动函数，再 `& (n-1)`
- **线程不安全**：1.7 并发扩容成环，1.8 修复但仍有丢数据
- 并发用 ConcurrentHashMap（1.8 用 CAS + synchronized 锁单个 bin）

### 4. synchronized vs Lock
| 维度 | synchronized | ReentrantLock |
|------|--------------|---------------|
| 级别 | JVM 关键字 | JDK 类 |
| 释放 | 自动 | 必须 unlock（finally） |
| 中断 | 不可 | 可 lockInterruptibly |
| 公平 | 非公平 | 可公平 |
| 条件 | wait/notify | Condition（可多个） |
| 尝试 | 不支持 | tryLock |
| 性能 | 1.6 后优化（偏向/轻量级/重量级），接近 | - |

### 5. volatile 作用
**答**：
- **可见性**：写立即刷回主存，读从主存
- **禁止指令重排**：插入内存屏障
- **不保证原子性**（`i++` 仍要加锁）

典型应用：双检锁单例、flag 标志位。

### 6. 线程池核心参数
**答**：
- `corePoolSize`：核心线程数
- `maxPoolSize`：最大线程数
- `keepAliveTime`：非核心空闲存活时间
- `workQueue`：任务队列（LinkedBlockingQueue/ArrayBlockingQueue/SynchronousQueue）
- `threadFactory`：线程工厂
- `handler`：拒绝策略（Abort/CallerRuns/Discard/DiscardOldest）

执行流程：core 满 → 入队 → 队满 → 扩到 max → 满则拒绝。

**Executors 四个预设为什么不推荐**：
- newFixedThreadPool / newSingleThreadExecutor：无界队列，OOM 风险
- newCachedThreadPool / newScheduledThreadPool：Integer.MAX 线程数，OOM 风险

推荐自己 new ThreadPoolExecutor 精确控制。

### 7. ThreadLocal 原理
**答**：
- 每个 Thread 有 ThreadLocalMap（key 是 ThreadLocal 对象，value 是值）
- **key 弱引用**：ThreadLocal 对象失去强引用后 key 会被 GC；但 **value 是强引用**，若线程长期存活（线程池），value 永不释放
- **内存泄漏**：必须手动 `remove()`，尤其在线程池场景
- 应用：Spring 事务上下文、SimpleDateFormat 线程封闭、请求 TraceId

### 8. 类加载机制
**答**：
- 加载 → 验证 → 准备 → 解析 → 初始化 → 使用 → 卸载
- **双亲委派**：先委托父加载器加载，父加载不到再自己加载
  - Bootstrap（rt.jar）→ Extension → App/System → 自定义
- 打破双亲委派：Tomcat（各 webapp 独立 ClassLoader）、SPI（Thread.contextClassLoader）、OSGi

### 9. Spring Bean 生命周期
**答**：
1. 实例化（`instantiateBean`）
2. 依赖注入（`populateBean`）
3. Aware 接口回调（BeanNameAware、ApplicationContextAware）
4. BeanPostProcessor.before
5. `@PostConstruct` / InitializingBean.afterPropertiesSet / init-method
6. BeanPostProcessor.after（AOP 代理在此创建）
7. 使用
8. `@PreDestroy` / DisposableBean.destroy / destroy-method

### 10. Spring 如何解决循环依赖
**答**：三级缓存
- **singletonObjects**：完整 Bean
- **earlySingletonObjects**：早期暴露的 Bean（已实例化未填充属性）
- **singletonFactories**：ObjectFactory（可产出可能被 AOP 代理的早期 Bean）

A 创建中 → 提前暴露半成品（放入三级） → 注入给 B → B 创建完放一级 → A 继续填充完成。

只能解决**单例 + setter/属性注入**的循环；构造器注入循环无法解决。

### 11. Spring 事务失效场景
**答**：
1. 方法非 public
2. 类内部调用（this 调用，没走代理）
3. 抛 checked 异常但未声明 rollbackFor
4. 异常被 catch 吞了
5. 数据库引擎不支持事务（MyISAM）
6. `@Transactional` 加在 final 方法或类上（CGLIB 代理无法继承）
7. 传播行为不当（NOT_SUPPORTED、NEVER）
8. 多线程（事务绑定当前线程）

### 12. Spring Boot 自动配置原理
**答**：
- `@SpringBootApplication` = `@Configuration` + `@EnableAutoConfiguration` + `@ComponentScan`
- `@EnableAutoConfiguration` 导入 `AutoConfigurationImportSelector`
- 它读取 `META-INF/spring.factories`（旧）或 `META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`（2.7+）中的自动配置类
- 每个自动配置类用 `@ConditionalOnXxx` 按条件生效（如 ClassPath 有某类、存在某 Bean、某配置值为 true）

---

## 二、Python 核心八股

### 1. GIL 全局解释器锁
**答**：
- CPython 为保证线程安全，同一时刻**只有一个线程执行 Python 字节码**
- **CPU 密集**：多线程无加速，用多进程或 C 扩展
- **IO 密集**：多线程仍有效（IO 期间释放 GIL）
- 3.13 实验性 no-GIL 构建（PEP 703）
- 其他实现（Jython、IronPython）无 GIL

### 2. 列表 vs 元组
- list 可变，tuple 不可变
- tuple 可做 dict key，list 不行
- tuple 占用内存略少
- 函数返回多个值实际是 tuple

### 3. 深拷贝 vs 浅拷贝
- `copy.copy()`：浅拷贝，只复制第一层，嵌套对象共享
- `copy.deepcopy()`：递归复制
- `list.copy()`、`dict.copy()` 浅拷贝
- 对不可变对象（int/str/tuple）深浅拷贝无区别

### 4. 闭包与装饰器
**闭包**：内层函数引用外层函数的变量，外层返回内层函数时该变量随之"封闭"。

**装饰器**：本质是闭包，包裹函数增强行为：
```python
def log(func):
    def wrapper(*args, **kwargs):
        print(f"call {func.__name__}")
        return func(*args, **kwargs)
    return wrapper

@log
def hello(): print("hi")
```
用 `functools.wraps` 保留被装饰函数的元信息。

### 5. 迭代器 vs 生成器
- **迭代器**：实现 `__iter__` 和 `__next__` 的对象
- **生成器**：用 yield 的函数 / 生成器表达式，本质也是迭代器
- 惰性求值，内存省
- `yield from` 委托给子生成器

### 6. async/await 原理
- 基于协程（coroutine），由事件循环调度
- async 定义协程函数，await 挂起等待
- **单线程并发**：一个事件循环调度多协程，IO 时切换
- 不适合 CPU 密集

### 7. 垃圾回收
- **引用计数**（主）：对象 ref_count 归 0 立即回收
- **标记-清除**（辅）：处理循环引用
- **分代**：gen0/1/2，新对象频繁检查
- `gc` 模块可手动触发

### 8. `__new__` vs `__init__`
- `__new__`：创建对象（返回实例），第一个参数 cls
- `__init__`：初始化对象（修改实例属性），第一个参数 self
- 单例常重写 `__new__`

### 9. 元类 Metaclass
- 类的类。type 是所有类的元类
- `class Foo(metaclass=MyMeta)` 用 MyMeta 创建 Foo
- 应用：ORM（Django Model）、框架强制某些属性、DSL

### 10. multiprocessing vs threading
- `threading`：多线程，受 GIL 限制，适合 IO 密集
- `multiprocessing`：多进程，绕过 GIL，适合 CPU 密集
- `concurrent.futures`：统一高层 API
- `asyncio`：单线程多协程，IO 密集

### 11. 常用魔术方法
- `__init__` / `__new__` / `__del__`
- `__repr__` / `__str__`
- `__eq__` / `__hash__`
- `__len__` / `__getitem__` / `__setitem__` / `__iter__` / `__next__`
- `__enter__` / `__exit__`（with）
- `__call__`（可调用对象）
- `__getattr__` / `__setattr__` / `__getattribute__`
- `__slots__`（限制属性，省内存）

### 12. Pydantic 和 dataclass 区别
- **dataclass**：标准库，生成 `__init__`/`__repr__`/`__eq__`，**无运行时类型校验**
- **Pydantic**：第三方，**运行时校验**、JSON 序列化、支持复杂嵌套
- FastAPI 内置 Pydantic；LLM 应用做结构化输出首选 Pydantic

---

## 三、AI / Agent 八股

### 1. Transformer 核心
- Self-Attention：`softmax(QK^T/√d)V`
- Multi-Head：多组 QKV 并行
- Positional Encoding：弥补无序性
- Residual + LayerNorm：稳定训练
- 三架构：Encoder-Decoder（T5）、Encoder-only（BERT）、Decoder-only（GPT，主流）

### 2. 为什么 Decoder-only 成主流
- 训练简单（auto-regressive next-token）
- 预训练数据容易（任何文本）
- Scaling 效果好
- 与 SFT / RLHF 兼容
- GPT 先行、Llama 生态推动

### 3. 微调方法
- **Full Fine-tuning**：全参微调，效果最好但贵
- **LoRA**：低秩分解，在原权重旁加 `W + A@B`，只训 A/B，省 10000 倍参数
- **QLoRA**：4-bit 量化 + LoRA，消费级 GPU 可训 70B
- **Prefix / Prompt Tuning**：只学前缀 token
- **Adapter**：每层插入小网络

### 4. RAG 基础流程
1. 文档切分（chunk）
2. Embedding 入向量库
3. 查询时 Embedding → 检索 top-K
4. 组装 Prompt + 上下文 → LLM 生成

### 5. RAG 优化手段
- **索引**：语义切分、父子块、Metadata 增强
- **查询**：HyDE、多查询、子问题分解
- **检索**：Hybrid（向量+BM25）、RRF 融合
- **重排**：Cross-Encoder Reranker（bge-reranker、Cohere）
- **后处理**：去重、压缩、重写
- **评估**：Ragas、LangFuse

### 6. Embedding 是什么
- 把文本映射到高维向量，语义相似的向量相近
- 主流模型：text-embedding-3-large、bge-m3、voyage-3、Jina-embeddings-v3
- 维度通常 384-3072
- 相似度用余弦相似度

### 7. 向量数据库
- ANN 算法：HNSW（最流行）、IVF、PQ
- 产品：Qdrant、Milvus、Weaviate、Pinecone、pgvector、ES
- 选型依据：数据规模、filter 能力、运维复杂度、成本

### 8. Prompt 工程技巧
- **Zero-shot**：直接问
- **Few-shot**：给例子
- **Chain of Thought**：让逐步推理
- **ReAct**：推理 + 行动
- **Self-Consistency**：多次采样投票
- **Tree of Thoughts**：树形探索
- **Role Prompting**：设定角色

### 9. Agent 核心组件
- **LLM**（大脑）
- **Planning**（规划：CoT、ReAct）
- **Tools**（工具）
- **Memory**（短期 + 长期）

ReAct 循环：Thought → Action → Observation → ... → Final Answer。

### 10. Function Calling / Tool Use 流程
1. 定义工具 Schema（name + description + parameters）
2. 传给 LLM
3. LLM 返回 tool_call（JSON）
4. 应用解析并执行
5. 结果回传 LLM
6. LLM 生成最终回答（可能继续调工具）

### 11. MCP 协议
- 跨 LLM 应用与外部工具的标准协议
- JSON-RPC 2.0，支持 stdio / HTTP+SSE 传输
- 三原语：Tools（调用）、Resources（读取）、Prompts（模板）
- Anthropic 2024.11 开源

### 12. LangChain vs LangGraph vs LlamaIndex
- **LangChain**：通用 LLM 应用框架（Chain / Tool / Agent）
- **LangGraph**：LangChain 出的状态图 Agent 编排，生产级
- **LlamaIndex**：数据为中心，强 RAG

生产 Agent 首选 LangGraph；RAG 为主用 LlamaIndex；通用用 LangChain。

### 13. Multi-Agent 常见模式
- **Supervisor**：中心化路由
- **Hierarchical**：多层级组织
- **Pipeline**：固定流程
- **Network**：对等通信
- **Debate**：辩论提质量
- **Group Chat**：群聊

### 14. 模型上下文窗口
- GPT-4o：128K
- Claude Opus 4.7：200K / 1M（long context）
- Gemini 1.5 Pro：1M-2M
- 窗口越长越贵、越慢

### 15. Prompt Caching
- Anthropic 显式 cache_control，5 分钟 TTL，命中减 90%
- OpenAI 自动缓存 ≥ 1024 token 前缀，50% 折扣
- 最佳实践：稳定内容（System Prompt、工具）放前面

### 16. 幻觉如何缓解
- **RAG**：基于检索回答
- **Prompt 约束**：明确"不确定请说不知道"
- **引用机制**：要求标注来源并校验
- **温度降低**：temperature=0 减少随机
- **Self-Consistency**：多次采样投票
- **Verifier Agent**：二次检查

### 17. Agent 评估
- **过程**：工具选择准确率、步骤数、无效调用
- **结果**：任务成功率、答案正确率
- **成本**：Token、延迟
- **框架**：LangSmith、LangFuse、Ragas、DeepEval
- **Benchmark**：GAIA、AgentBench、SWE-Bench

### 18. LLM 部署
- **托管 API**：OpenAI / Claude / 国内云（火山、百炼、通义）
- **自部署**：vLLM（主流生产）、TGI、Ollama（开发）、llama.cpp（CPU）
- **量化**：INT8 / INT4 / FP8 / AWQ / GPTQ
- **GPU**：L4/L40 / A10 / A100 / H100

### 19. Token 计费
- 输入/输出分开计（输出一般贵 3-5 倍）
- Prompt Caching 输入半价
- Batch API 半价（24h 交付）
- 国内模型普遍便宜 3-10 倍

### 20. 主流模型能力对比（2026 Q2）
- **Claude Opus 4.7 / Sonnet 4.6**：推理、代码、Agent 一流
- **GPT-4o / o1**：综合强，o1 系列擅长推理
- **Gemini 2.0**：超长上下文、多模态
- **Llama 3 70B/400B**：开源最强
- **DeepSeek V3 / R1**：性价比高，推理强
- **Qwen 2.5 72B**：中文优秀、开源

---

## 四、系统设计（简版）

### 1. 短链服务
- ID 生成：雪花算法 / 发号器
- ID → 短码：62 进制
- 存储：Redis + MySQL
- 热点缓存
- 过期清理

### 2. 秒杀系统
- 前端限流、页面静态化
- 网关限流
- Redis 预减库存
- MQ 削峰
- 最终扣减 DB
- 防超卖：Redis Lua 脚本 / 数据库乐观锁

### 3. 分布式 ID
- UUID：无序
- 雪花：41 位时间戳 + 10 位机器 + 12 位序列
- 号段模式：Leaf / TinyID
- Redis INCR

### 4. 分布式事务
- **2PC**：准备 + 提交
- **TCC**：Try-Confirm-Cancel
- **Saga**：正向补偿
- **本地消息表**
- **最大努力通知**
- **Seata**：AT 模式自动化

### 5. CAP 与 BASE
- CAP：一致性/可用性/分区容忍性，三选二
- BASE：基本可用、软状态、最终一致
- 互联网系统多数选 AP + 最终一致

---

## 五、数据库高频

### 1. MySQL 索引失效
- 函数、运算：`WHERE YEAR(date) = 2024`
- 类型不匹配
- LIKE 前缀通配
- `OR` 部分列无索引
- 最左前缀不满足
- `!=` / `<>`
- `IS NULL` / `IS NOT NULL`（部分版本）
- 隐式类型转换

### 2. MVCC
- 通过 undo log + 事务版本号实现
- 快照读（SELECT）不加锁
- RR 隔离在事务开始时生成 Read View
- RC 每次 SELECT 生成 Read View
- 解决不可重复读（RR），但非完全解决幻读

### 3. 事务隔离级别
- RU（脏读）→ RC（不可重复读）→ RR（幻读，MySQL 默认，实际通过 next-key lock 基本解决）→ Serializable

### 4. 乐观锁 vs 悲观锁
- 悲观锁：`SELECT ... FOR UPDATE`
- 乐观锁：版本号或时间戳，`UPDATE SET v=v+1 WHERE id=? AND v=?`

### 5. Redis 数据类型与底层
- String（SDS）、List（QuickList）、Hash（Ziplist/HashTable）、Set（Intset/HashTable）、ZSet（Ziplist/SkipList）、Stream、Bitmap、HyperLogLog、Geo

### 6. Redis 持久化
- RDB：快照，fork 子进程
- AOF：追加日志，三种 fsync 策略
- RDB + AOF 混合（4.0+）：启动快 + 数据安全

### 7. Redis 缓存问题
- **穿透**：查不存在的 key → 布隆过滤器 / 缓存空值
- **击穿**：热 key 失效 → 互斥锁 / 永不过期
- **雪崩**：大量 key 同时过期 → 随机 TTL / 多级缓存

---

## 六、计算机基础

### 1. TCP 三次握手四次挥手
- 三次：SYN → SYN+ACK → ACK
- 四次：FIN → ACK → FIN → ACK
- TIME_WAIT：2MSL，防止旧连接报文干扰新连接

### 2. TCP vs UDP
- TCP：可靠、有序、面向连接、流控、慢启动
- UDP：无连接、快、不可靠

### 3. HTTPS 流程
1. Client 发 ClientHello（随机数、支持的套件）
2. Server 发 ServerHello + 证书 + 公钥
3. Client 验证证书，生成 pre-master，用公钥加密发给 Server
4. 双方用三个随机数生成对称密钥
5. 开始对称加密通信

### 4. HTTP 版本
- HTTP/1.1：长连接、管线化
- HTTP/2：多路复用、头部压缩（HPACK）、二进制帧
- HTTP/3：基于 QUIC（UDP）、连接迁移

### 5. 进程 vs 线程 vs 协程
- 进程：资源单位，独立地址空间
- 线程：调度单位，共享进程资源
- 协程：用户态调度，轻量，一个线程跑 N 个协程

### 6. 死锁四条件
- 互斥、占有且等待、不可剥夺、循环等待
- 破任一条件可解

---

## 面试回答结构模板

### "讲一个你做过的项目"
1. **背景**（30s）：业务场景、我的角色
2. **挑战**（30s）：最难/最关键的技术点
3. **方案**（60-90s）：技术选型、关键设计、量化数据
4. **收益**（30s）：业务/技术收益，数字说话
5. **反思**（30s）：踩过的坑、如果重来会怎么做

### "讲一下某个技术点"
1. **是什么**（定义）
2. **为什么**（解决什么问题）
3. **怎么做**（原理/实现）
4. **在哪用**（场景）
5. **有什么坑**（常见问题）

### 不会的如何应对
- **诚实**：不会就说不会
- **延伸**：说"我了解类似的 XX，原理是…，可能 YY 也差不多"
- **求学**：表达学习意愿，"面试后我会查资料补上"
