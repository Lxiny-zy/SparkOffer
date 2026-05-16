# JVM 调优实战：GC 选型、内存泄漏、JIT

JVM 调优是 Java 高级面试绕不开的话题。本章覆盖核心理论 + 排查工具 + 真实案例的回顾，让你能从"听过"到"会做"。

## 1. 堆内存模型

```
[Heap]
├── [Young Generation]
│   ├── Eden        (新对象首先分配)
│   ├── Survivor 0
│   └── Survivor 1
└── [Old Generation] (长期存活的对象)

[Metaspace]   类元数据（JDK 8+ 替代 PermGen，使用本地内存）
[Direct Memory]  堆外内存（NIO、Netty 用）
[Code Cache]  JIT 编译后的机器码
[Stack]       每线程独立
```

**对象生命周期**：
1. 在 Eden 分配
2. Eden 满 → Minor GC → 存活对象进 Survivor
3. 在 S0/S1 之间复制，每经历一次 GC age +1
4. age 达阈值（默认 15）或 Survivor 装不下 → 晋升 Old
5. Old 满 → Major GC / Full GC

## 2. GC 算法

### 2.1 算法分类

- **复制算法**：Young 区用（Eden + Survivor）
- **标记-清除**：老的 CMS 用
- **标记-整理**：Old 区用（避免碎片）
- **分代收集**：分区域用不同算法
- **分区算法**：G1、ZGC 用，把堆分成 region

### 2.2 主流收集器演进

| 收集器 | JDK | 特点 | 适用 |
|---|---|---|---|
| Serial | 1.4 | 单线程，简单 | 客户端、小内存 |
| Parallel (PS) | 5 | 吞吐量优先 | 后台批处理 |
| CMS | 5 | 低停顿，已废弃 | 老项目 |
| **G1** | 7→9 默认 | 分 region，可控停顿 | 通用首选（< 32GB） |
| **ZGC** | 11→15 production | 亚毫秒停顿 | 大堆（>32GB）、低延迟 |
| **Shenandoah** | 12 | 低停顿，RedHat 主推 | 类 ZGC |

### 2.3 何时选哪个

- 堆 < 4GB、吞吐优先：Parallel
- 堆 4-32GB、低延迟：G1（JDK 9+ 默认）
- 堆 > 32GB、亚毫秒延迟：ZGC
- 极致低延迟交易系统：Azul C4 / Shenandoah

## 3. G1 深入

### 3.1 核心思想

把堆分成 ~2000 个 Region（默认 1-32MB），不再固定 Young/Old 物理分区。每次 GC 选 reclaim 价值最高的几个 region 回收（Garbage First）。

### 3.2 关键参数

```bash
-XX:+UseG1GC
-Xms4g -Xmx4g                    # 堆大小（必须固定，避免动态调整）
-XX:MaxGCPauseMillis=200         # 目标停顿（软目标）
-XX:G1HeapRegionSize=8m          # region 大小
-XX:InitiatingHeapOccupancyPercent=45  # 触发并发标记的占用率
-XX:+ParallelRefProcEnabled
```

### 3.3 G1 GC 阶段

1. **Young GC**：STW，复制存活对象
2. **并发标记**：与应用并发，标记 Old 中存活对象
3. **Mixed GC**：回收 Young + 一部分 Old region
4. **Full GC**：兜底，G1 退化到单线程（要避免）

## 4. ZGC

### 4.1 优势

- 停顿 < 10ms（多数 < 1ms）
- 堆大小可达 16TB
- 停顿时间与堆大小无关
- 并发处理，几乎所有阶段不 STW

### 4.2 代价

- 吞吐量比 G1 略低 5-15%
- 堆外内存占用高（colored pointer）
- 内存开销更大

### 4.3 启用

```bash
-XX:+UseZGC -Xms16g -Xmx16g
```

JDK 15+ production-ready。LLM 推理服务等长任务首选。

## 5. 排查工具

### 5.1 GC 日志

```bash
# JDK 8
-XX:+PrintGCDetails -XX:+PrintGCDateStamps -Xloggc:gc.log

# JDK 9+
-Xlog:gc*=info:file=gc.log:time,uptime,level,tags:filecount=10,filesize=100M
```

分析工具：**GCEasy**（在线）、**GCViewer**（本地）。

关键指标：
- Throughput：>95% 健康
- Max Pause：业务可接受范围
- GC 频率：太频繁说明堆小 / 创建对象多

### 5.2 jstack（线程 dump）

```bash
jstack <pid> > thread.dump
# 或 jstack -l <pid>   带锁信息
```

查死锁、看线程状态、定位高 CPU 线程。

### 5.3 jmap（堆 dump）

```bash
jmap -dump:format=b,file=heap.hprof <pid>
jmap -histo <pid> | head -20    # 实例数量 top 20
```

OOM 自动 dump：
```bash
-XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/data/heaps/
```

### 5.4 MAT / VisualVM / JProfiler

heap dump 分析工具。**MAT 是最强的开源工具**，看 dominator tree、leak suspect 自动报告。

### 5.5 Arthas

阿里出品，生产神器：
- `dashboard`：实时面板
- `thread`：线程
- `stack <method>`：调用栈
- `watch <method> <param/return>`：实时查参数
- `trace <method>`：追踪调用链耗时
- `jad <class>`：反编译已加载 class

### 5.6 jstat

实时 GC 监控：
```bash
jstat -gcutil <pid> 1000
# 每秒打印一次：S0 S1 E O M YGC YGCT FGC FGCT GCT
```

## 6. 内存泄漏排查实战

### 6.1 现象识别

- Old 持续上涨，每次 Full GC 后无明显下降
- Full GC 频率越来越高，最终 OOM
- 响应时间随时间变长

### 6.2 排查步骤

1. **保留 heap dump**：`-XX:+HeapDumpOnOutOfMemoryError`
2. **MAT 打开**，看 Leak Suspects Report
3. **看 Dominator Tree**：找占用最大的对象
4. **看 Path to GC Roots**：找谁在 hold 这个对象
5. **定位代码**：通常是静态集合、ThreadLocal、缓存无淘汰、监听器未注销

### 6.3 常见泄漏模式

```java
// 1. 静态 Map 无淘汰
private static Map<String, User> cache = new HashMap<>();

// 2. ThreadLocal 没 remove（线程池场景）
threadLocal.set(...); // 没 remove() 就把对象 hold 在线程上

// 3. 监听器未注销
button.addListener(listener); // dispose 时没 removeListener

// 4. 内部类持有外部引用
new Thread(new Runnable() {  // 持有外部 this
    public void run() { ... }
});

// 5. 连接池泄漏
Connection conn = pool.getConnection();
// ... 异常路径忘了 conn.close()
```

## 7. JIT 编译

### 7.1 分层编译（Tiered Compilation）

JDK 8+ 默认开启：
- Level 0：解释执行
- Level 1-3：C1 编译（快速、轻量优化）
- Level 4：C2 编译（深度优化，慢）

热点方法自动升级到 C2。**预热是必须的**——服务上线先跑预热请求让 JIT 起来。

### 7.2 关键参数

```bash
-XX:CICompilerCount=4                # 编译线程数
-XX:+PrintCompilation                # 打印编译事件（debug 用）
-XX:CompileThreshold=10000           # 触发 C2 的调用次数
-XX:ReservedCodeCacheSize=512m       # JIT 代码缓存
-XX:+UnlockDiagnosticVMOptions -XX:+PrintInlining  # 看 inline 决策
```

### 7.3 GraalVM

替代 JVM 的 JIT（Graal 编译器），支持 AOT（Native Image）。Spring Native / Quarkus 用。
启动 ms 级 + 内存极小，但峰值吞吐略低。Serverless 场景理想。

## 8. 典型问题诊断

### 8.1 突发高延迟

可能原因：
- Full GC（看 jstat）
- 锁竞争（jstack 看 BLOCKED 线程）
- 外部依赖慢（trace）
- 突发流量 + 自动扩容慢

### 8.2 CPU 100%

```bash
top -H -p <pid>                          # 找高 CPU 线程 tid
printf "%x\n" <tid>                      # tid 转 16 进制
jstack <pid> | grep -A 20 "0x<hex_tid>"  # 看堆栈
```

通常是死循环、JIT 编译风暴、GC 风暴。

### 8.3 内存持续增长

参考 6.2。

### 8.4 频繁 Full GC

可能：
- 堆太小（加 Xmx）
- 大对象直接进 Old（看 G1HeapRegionSize 是否太小）
- 内存泄漏（堆 dump 分析）
- 元数据爆（看 Metaspace 大小，加 `-XX:MaxMetaspaceSize`）

## 9. 调优心法

1. **先测后调**：监控 + GC 日志数据驱动，别拍脑袋
2. **一次只改一个参数**：方便归因
3. **生产同等压测**：dev 调出来的参数可能在生产无效
4. **关注业务指标**：GC 调优最终是为了延迟 / 吞吐，不是 GC 次数
5. **保守原则**：默认配置 + 堆大小 + GC 选型，多数场景够用

## 10. 高频面试题

**Q1：G1 GC 工作流程？**
分 region，并发标记 + 增量回收。Young GC 复制 Eden+Survivor → Survivor；混合 GC 同时回收 Young 和部分 Old region（reclaim 价值最高的）；触发并发标记看 IHOP 阈值；Full GC 兜底，要尽量避免。

**Q2：ZGC 怎么做到亚毫秒停顿？**
颜色指针（color pointer）+ load barrier + region 化堆。读屏障在每次对象访问时判断是否需要 remap，把"标记-整理"分散到应用线程执行。所有阶段都尽量并发，STW 时间只跟根集大小相关，与堆大小无关。

**Q3：CMS 为什么被废弃？**
- 并发收集容易产生碎片，最终需要 STW 整理
- 浮动垃圾问题（并发期间产生的新垃圾要下次回收）
- Remark 阶段 STW 时间难控制
- G1 在多数场景已经更优

**Q4：怎么排查 OOM？**
1. 看 OOM 类型：HeapSpace（堆满）/ Metaspace（元数据满）/ DirectBuffer（堆外满）/ ThreadCreate（线程数超）
2. 加 `-XX:+HeapDumpOnOutOfMemoryError` 保留 dump
3. MAT 打开 dump，Leak Suspects 报告 + Dominator Tree
4. 定位 GC Root 路径找泄漏代码

**Q5：怎么决定堆大小？**
经验：物理内存的 50-70%。压测找拐点：堆太小 → GC 频繁吞吐降；堆太大 → 单次 GC 时间长。生产先用估算值（如 4-8GB），观测 1 周后调整。**Xms = Xmx**，避免动态调整带来抖动。

**Q6：ThreadLocal 内存泄漏原理？**
ThreadLocalMap 用线程对象引用 Entry，Entry 的 key 是 ThreadLocal 弱引用，value 是强引用。线程池中线程不死，ThreadLocal 被回收后 key=null，但 value 仍被强引用——泄漏。**用完必须 remove()**。

**Q7：什么时候考虑 ZGC？**
- 堆 > 16GB
- 业务对延迟敏感（金融、推荐、实时风控）
- LLM 推理 / 大数据分析等长任务
- JDK 17+（Production-ready）
吞吐量损失 5-15% 可以接受。
