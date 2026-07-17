# JVM 虚拟机

## JVM 内存结构

### 线程私有

**程序计数器（PC Register）：**
- 当前线程执行的字节码行号指示器
- 如果执行 Java 方法，记录的是 JVM 字节码指令地址
- 如果执行 Native 方法，计数器值为空（Undefined）
- 唯一不会 OutOfMemoryError 的区域

**虚拟机栈（JVM Stack）：**
- 每个方法调用创建一个栈帧（Stack Frame）
- 栈帧包含：局部变量表、操作数栈、动态链接、方法出口
- 默认大小 512KB ~ 1MB（`-Xss` 参数调整）
- 递归过深 → StackOverflowError
- 线程过多导致无法分配栈空间 → OutOfMemoryError

```
栈帧结构：
┌───────────────────────┐
│ 局部变量表             │ ← 存放方法参数和局部变量（slot）
│ (Local Variable Table) │    long/double 占 2 个 slot
├───────────────────────┤
│ 操作数栈              │ ← 方法执行的工作区
│ (Operand Stack)       │    如 iadd 从栈顶弹出两个 int 相加后压入
├───────────────────────┤
│ 动态链接              │ ← 指向运行时常量池中该方法的引用
│ (Dynamic Linking)     │    支持运行时多态（虚方法调用）
├───────────────────────┤
│ 方法返回地址          │ ← 方法正常/异常退出后返回调用处
│ (Return Address)      │
└───────────────────────┘
```

**本地方法栈（Native Method Stack）：**
- 为 native 方法服务（如 `System.currentTimeMillis()`）
- HotSpot 中与虚拟机栈合二为一

### 线程共享

**堆（Heap）：**
- JVM 中最大的内存区域，几乎所有对象实例都在堆上分配
- GC 的主要工作区域

```
堆内存分代结构（G1 之前）：
┌──────────────────────────────────────────┐
│                  Young Generation         │
│  ┌─────────┬──────────┬──────────┐       │
│  │  Eden   │ Survivor0│ Survivor1│       │
│  │  (80%)  │  (10%)   │  (10%)   │       │
│  └─────────┴──────────┴──────────┘       │
├──────────────────────────────────────────┤
│            Old Generation                 │
│           （长期存活的对象）                │
└──────────────────────────────────────────┘

默认比例：
Young : Old = 1 : 2（-XX:NewRatio=2）
Eden : S0 : S1 = 8 : 1 : 1（-XX:SurvivorRatio=8）
```

**方法区/元空间（Metaspace）：**
- 存储类信息（类名、字段、方法）、常量、静态变量、JIT 编译后的代码
- Java 7 之前：永久代（PermGen），在堆中分配，大小固定 → 容易 OOM
- Java 8+：元空间（Metaspace），使用本地内存（Native Memory）
- 运行时常量池（Runtime Constant Pool）在方法区中

```
永久代 → 元空间 的变化（Java 8）：
- 类信息：永久代 → 元空间（本地内存）
- 字符串常量池：永久代 → 堆中（Java 7 开始迁移）
- 静态变量：永久代 → 堆中

为什么废弃永久代？
1. 永久代大小固定，难以调优（PermGen space OOM 常见）
2. 永久代 GC 效率低
3. 方便 HotSpot 与 JRockit 合并
```

### 直接内存（Direct Memory）

```java
// 不属于 JVM 运行时数据区，但频繁使用
// NIO 的 DirectByteBuffer 直接分配堆外内存
ByteBuffer buffer = ByteBuffer.allocateDirect(1024 * 1024);

// 优点：减少一次 Java 堆与 Native 堆之间的数据拷贝（零拷贝）
// 缺点：分配和释放成本高于堆内存
// 参数：-XX:MaxDirectMemorySize
```

## 对象创建过程

```
1. 类加载检查
   → 检查 new 指令的参数能否在常量池中定位到类的符号引用
   → 该类是否已被加载、解析和初始化，否则先执行类加载

2. 分配内存
   → 指针碰撞（Bump the Pointer）：堆内存规整时，指针向后移动
   → 空闲列表（Free List）：堆内存不规整时，从空闲列表中找
   → 线程安全：CAS + 失败重试，或 TLAB（Thread Local Allocation Buffer）

3. 内存空间初始化为零值
   → 保证实例字段可以不赋初值就能使用（默认值）

4. 设置对象头
   → Mark Word：哈希码、GC 分代年龄、锁状态标志
   → 类型指针（Class Pointer）：指向类的元数据
   → 数组长度（如果是数组）

5. 执行 <init> 构造方法
   → 按照程序员的意愿进行初始化
```

### 对象内存布局

```
对象在内存中的布局（64位 JVM，开启压缩指针）：

┌────────────────────────────┐
│ 对象头（Object Header）     │
│ ┌────────────────────────┐ │
│ │ Mark Word (8 bytes)    │ │ ← hashCode/锁/GC 年龄
│ ├────────────────────────┤ │
│ │ Class Pointer (4 bytes)│ │ ← 压缩后 4 字节，指向类元数据
│ └────────────────────────┘ │
├────────────────────────────┤
│ 实例数据（Instance Data）   │ ← 字段值，按类型大小排列
├────────────────────────────┤
│ 对齐填充（Padding）         │ ← 总大小必须是 8 字节的整数倍
└────────────────────────────┘

// 查看对象大小：JOL（Java Object Layout）
System.out.println(ClassLayout.parseInstance(new Object()).toPrintable());
// java.lang.Object 空对象占 16 字节（对象头 12B + 填充 4B）
```

### TLAB（Thread Local Allocation Buffer）

```
// 每个线程在 Eden 区预先分配一块私有缓冲区
// 对象优先在 TLAB 中分配（无需加锁），TLAB 用完再 CAS 分配新的
// 默认开启：-XX:+UseTLAB
// TLAB 大小：约 Eden 的 1%

// 分配流程：
// 1. 尝试在 TLAB 中分配（最快，无锁）
// 2. TLAB 不够 → 尝试在 Eden 区 CAS 分配
// 3. Eden 区不够 → 触发 Minor GC
// 4. 大对象 → 直接在老年代分配
```

## 垃圾回收（GC）

### 判断对象是否存活

**引用计数法：**
- 每个对象有一个引用计数器，被引用时 +1，引用失效时 -1
- 计数为 0 就可以回收
- 问题：循环引用无法回收（A 引用 B，B 引用 A）
- JVM 不使用此方法

**可达性分析（Reachability Analysis）：**
- 从 GC Roots 出发，沿引用链遍历
- 不可达的对象就是垃圾
- GC Roots 包括：
  1. 虚拟机栈中引用的对象（局部变量）
  2. 方法区中静态属性引用的对象
  3. 方法区中常量引用的对象
  4. 本地方法栈中 JNI 引用的对象
  5. JVM 内部引用（Class 对象、异常对象等）
  6. synchronized 持有的对象

### 引用类型

| 类型 | 回收时机 | 用途 | 代码 |
|------|---------|------|------|
| 强引用 | 永不回收（只要引用存在） | 普通对象引用 | `Object o = new Object()` |
| 软引用 | 内存不足时回收 | 缓存 | `SoftReference<byte[]>` |
| 弱引用 | 下次 GC 时回收 | ThreadLocalMap 的 key | `WeakReference<Object>` |
| 虚引用 | 随时回收 | 跟踪对象被 GC 的状态 | `PhantomReference<Object>` |

```java
// 软引用做缓存（内存不足时自动清理）
SoftReference<byte[]> cache = new SoftReference<>(new byte[1024 * 1024]);
byte[] data = cache.get(); // 可能为 null（已被回收）

// 弱引用（ThreadLocalMap 的 Entry 就是弱引用 key）
WeakReference<Object> weakRef = new WeakReference<>(new Object());
System.gc();
System.out.println(weakRef.get()); // null（已被回收）

// 引用队列（ReferenceQueue）：引用对象被回收时会被加入队列
ReferenceQueue<Object> queue = new ReferenceQueue<>();
WeakReference<Object> ref = new WeakReference<>(new Object(), queue);
System.gc();
Reference<?> polled = queue.poll(); // 可以感知到对象被回收
```

### 垃圾回收算法

| 算法 | 原理 | 优点 | 缺点 | 适用区域 |
|------|------|------|------|---------|
| 标记-清除 | 标记垃圾后清除 | 简单 | 内存碎片 | 老年代 |
| 标记-复制 | 存活对象复制到另一块 | 无碎片 | 空间浪费 50% | 新生代 |
| 标记-整理 | 存活对象向一端移动 | 无碎片 | 移动对象开销 | 老年代 |

```
标记-复制算法在新生代的优化（Appel 式回收）：
Eden : S0 : S1 = 8 : 1 : 1
- 新对象分配在 Eden 区
- GC 时将 Eden + S0 中存活对象复制到 S1，然后清空 Eden + S0
- 下次 GC 时 S0 和 S1 角色互换
- 空间利用率 90%（只浪费 10%）
- 如果 Survivor 放不下，直接进入老年代（分配担保）
```

### 对象进入老年代的条件

```
1. 年龄达到阈值（默认 15，-XX:MaxTenuringThreshold）
   每经历一次 Minor GC 且存活，年龄 +1

2. 大对象直接进入老年代（-XX:PretenureSizeThreshold）
   避免在 Eden 和 Survivor 之间大量复制

3. 动态年龄判断
   如果 Survivor 区中相同年龄对象大小之和 > Survivor 空间的 50%
   则年龄 >= 该年龄的对象直接进入老年代

4. 分配担保失败
   Minor GC 后 Survivor 放不下，直接进入老年代
```

### 垃圾收集器详解

```
垃圾收集器的演进：
Serial → Parallel → CMS → G1 → ZGC/Shenandoah

新生代：     老年代：
Serial     ←→ Serial Old
ParNew     ←→ CMS
Parallel   ←→ Parallel Old
         G1（整堆）
         ZGC（整堆）
         Shenandoah（整堆）
```

**Serial / Serial Old：**
- 单线程，GC 时暂停所有用户线程（STW）
- 简单高效，适合客户端模式或小内存场景
- `-XX:+UseSerialGC`

**ParNew：**
- Serial 的多线程版本（新生代）
- 是 CMS 的默认新生代收集器
- `-XX:+UseParNewGC`

**Parallel Scavenge / Parallel Old：**
- 吞吐量优先（吞吐量 = 用户代码时间 / 总时间）
- 自适应调节策略：`-XX:+UseAdaptiveSizePolicy`
- Java 8 默认收集器组合
- `-XX:+UseParallelGC`

**CMS（Concurrent Mark Sweep）：**
```
四个阶段：
1. 初始标记（Initial Mark）—— STW，标记 GC Roots 直接引用的对象，很快
2. 并发标记（Concurrent Mark）—— 并发执行，从 GC Roots 遍历对象图
3. 重新标记（Remark）—— STW，修正并发标记期间变动的引用
4. 并发清除（Concurrent Sweep）—— 并发执行，清除垃圾对象

优点：低延迟（大部分阶段与用户线程并发执行）
缺点：
- 内存碎片（标记-清除算法）
- 浮动垃圾（并发清除期间产生的新垃圾，只能下次回收）
- CPU 敏感（并发阶段占用 CPU）
- Concurrent Mode Failure 后退化为 Serial Old

Java 9 标记为 Deprecated，Java 14 移除
```

**G1（Garbage First）：**
```
G1 将堆划分为大小相等的 Region（1-32MB）：
┌──┬──┬──┬──┬──┬──┬──┬──┐
│ E│ E│ S│ O│ O│ H│ H│  │  E=Eden S=Survivor O=Old H=Humongous
├──┼──┼──┼──┼──┼──┼──┼──┤
│ O│  │ E│ E│ O│ O│  │ S│
└──┴──┴──┴──┴──┴──┴──┴──┘

核心思想：
- 每个 Region 可以是 Eden、Survivor 或 Old（动态分配）
- 大对象（>Region一半）放在 Humongous Region
- 优先回收垃圾最多的 Region（Garbage First 的由来）
- 可预测的停顿时间模型：-XX:MaxGCPauseMillis=200

GC 过程：
1. 初始标记（STW，借助 Minor GC 完成）
2. 并发标记（与用户线程并发）
3. 最终标记（STW，处理并发标记遗留问题）
4. 筛选回收（STW，选择回收价值最高的 Region，复制存活对象到空 Region）

参数：
-XX:+UseG1GC（Java 9+ 默认）
-XX:MaxGCPauseMillis=200（目标暂停时间）
-XX:G1HeapRegionSize=4m（Region 大小）
-XX:InitiatingHeapOccupancyPercent=45（触发并发标记的堆占用比例）

适用场景：堆 > 4GB，要求低延迟
```

**ZGC（Z Garbage Collector）：**
```
ZGC 的设计目标：
- 暂停时间 < 10ms（甚至 < 1ms）
- 暂停时间不随堆大小增长（TB 级堆也是亚毫秒级暂停）
- 支持 8MB ~ 16TB 堆

核心技术：
1. 染色指针（Colored Pointers）
   - 利用 64 位指针中的几个 bit 存储元数据（标记、重映射等）
   - 不需要额外的对象头空间存储 GC 信息

2. 读屏障（Load Barrier）
   - 在对象引用被读取时检查是否需要处理（重映射等）
   - 只在读取引用时触发，开销可控

3. 并发整理
   - 几乎所有阶段都是并发的
   - 只有极短的 STW（仅初始标记和最终标记，约几百微秒）

ZGC 的 GC 阶段：
1. 暂停标记开始（STW，< 1ms）
2. 并发标记/重映射
3. 暂停标记结束（STW，< 1ms）
4. 并发准备重分配
5. 暂停重分配开始（STW，< 1ms）
6. 并发重分配
7. 并发重映射

参数：
-XX:+UseZGC（Java 15+ 正式可用）
-XX:+ZGenerational（Java 21 分代 ZGC，默认开启）

Java 21 分代 ZGC：
- 增加了年轻代和老年代的概念
- 年轻代对象的回收更加高效
- 进一步降低了整体 GC 开销
```

**Shenandoah GC：**
```
Shenandoah（OpenJDK 项目，非 Oracle JDK）
- 目标类似 ZGC：低延迟、并发回收
- 核心技术：Brooks 转发指针（每个对象头增加一个指针）
- 通过转发指针实现并发整理（不使用染色指针）

vs ZGC：
- ZGC 用染色指针（64位指针高位），Shenandoah 用转发指针（对象头）
- Shenandoah 对象头多 8 字节（内存开销稍大）
- 两者性能接近，都能实现亚毫秒级暂停

参数：-XX:+UseShenandoahGC
```

### Minor GC vs Major GC vs Full GC

| 类型 | 区域 | 触发条件 | 特点 |
|------|------|---------|------|
| Minor GC | 新生代 | Eden 区满 | 频繁但快（大部分对象都是垃圾） |
| Major GC | 老年代 | 老年代空间不足 | 较慢 |
| Full GC | 整个堆 + 方法区 | 多种条件 | 最慢，STW 时间长 |

**触发 Full GC 的条件：**
1. 老年代空间不足
2. 方法区/元空间不足
3. 调用 `System.gc()`（JVM 可能不响应）
4. Minor GC 后晋升老年代的对象大于老年代剩余空间
5. CMS 并发收集失败（Concurrent Mode Failure）

## JIT 编译优化

```
JVM 执行 Java 代码的两种方式：
1. 解释执行：逐行解释字节码，启动快但运行慢
2. JIT 编译执行：将热点代码编译为本地机器码，运行快

HotSpot 采用混合模式（Mixed Mode）：
- 先解释执行
- 发现热点代码（调用频繁的方法/循环体）后 JIT 编译
- 编译后的代码直接执行，不再解释

JIT 编译器：
- C1 编译器（Client）：快速编译，较少优化，适合短生命周期应用
- C2 编译器（Server）：深度优化，编译慢但执行快，适合长运行应用
- 分层编译（Tiered Compilation，默认开启）：先 C1 后 C2，兼顾启动和峰值性能

Graal 编译器（Java 10+）：
- 用 Java 编写的 JIT 编译器
- 支持更激进的优化
- 是 GraalVM 的核心组件
```

### JIT 常见优化

```java
// 1. 方法内联（Method Inlining）
// 将被调用方法的代码直接嵌入调用处，消除方法调用开销
// 小方法（< 35 字节码，-XX:MaxInlineSize）会被内联

// 2. 逃逸分析（Escape Analysis）
public void method() {
    Object obj = new Object(); // 不会逃逸到方法外
    // JIT 分析后可能：
    // - 栈上分配（不在堆上创建，无需 GC）
    // - 标量替换（将对象拆散为基本类型变量）
    // - 同步消除（锁消除，对不逃逸对象的 synchronized 去除）
}

// 3. 常量折叠
int a = 3 + 5; // 编译期直接计算为 8

// 4. 循环展开（Loop Unrolling）
// 减少循环次数，减少分支判断开销

// 5. 死代码消除（Dead Code Elimination）
if (false) { /* 这段代码会被消除 */ }

// 查看 JIT 编译信息
// -XX:+PrintCompilation        打印编译方法
// -XX:+UnlockDiagnosticVMOptions -XX:+PrintInlining  打印内联信息
```

## 类加载机制

### 类加载过程

```
加载（Loading）→ 链接（Linking）→ 初始化（Initialization）
                  ├─ 验证（Verification）
                  ├─ 准备（Preparation）
                  └─ 解析（Resolution）
```

- **加载**：读取 .class 文件字节流，生成 Class 对象
- **验证**：校验字节码格式、语义、字节码验证、符号引用验证
- **准备**：为**静态变量**分配内存并设**默认值**（不是初始值！）
  ```java
  static int value = 123; // 准备阶段 value = 0，初始化阶段 value = 123
  static final int CONST = 123; // 准备阶段就是 123（编译期常量）
  ```
- **解析**：符号引用替换为直接引用（方法的内存地址等）
- **初始化**：执行 `<clinit>()` 方法（静态变量赋值、静态代码块）
  - 初始化时机：new、反射、main 方法所在类、子类初始化先初始化父类

### 双亲委派模型

```
Bootstrap ClassLoader（引导类加载器，C++ 实现）
    ↑ 委派
Extension ClassLoader（扩展类加载器，Java 实现）
    ↑ 委派
Application ClassLoader（应用类加载器，Java 实现）
    ↑ 委派
Custom ClassLoader（自定义类加载器）

加载流程：
1. 收到加载请求
2. 先委托父类加载器
3. 父类无法加载（findClass 返回 null）才自己尝试

好处：
1. 避免类重复加载（父加载器加载过的不会再加载）
2. 保护核心类安全（自定义的 java.lang.String 不会被加载）
```

### 打破双亲委派

```java
// 场景1：Thread.setContextClassLoader() — SPI 机制
// JDBC 的 Driver 接口在 rt.jar（Bootstrap 加载），
// 但实现类（mysql-connector）在 classpath（Application 加载）
// Bootstrap 加载器无法向下委托，通过线程上下文类加载器解决

// 场景2：自定义类加载器
// 重写 loadClass() 方法（打破委派）或 findClass() 方法（不打破委派）
public class HotSwapClassLoader extends ClassLoader {
    @Override
    protected Class<?> findClass(String name) throws ClassNotFoundException {
        byte[] classData = loadClassFromFile(name);
        return defineClass(name, classData, 0, classData.length);
    }
}

// 场景3：OSGi 模块化
// 每个 Bundle 有自己的类加载器，网状委派而非树状

// 场景4：Tomcat
// 每个 Web 应用有独立的类加载器，实现应用隔离
// WebApp ClassLoader → Common ClassLoader → System ClassLoader
```

## JVM 调优

### 常用参数

```bash
# 堆内存
-Xms2g           # 初始堆大小（建议与 Xmx 相同，避免动态扩展）
-Xmx2g           # 最大堆大小
-Xmn1g           # 新生代大小
-XX:NewRatio=2   # 老年代:新生代 = 2:1

# 元空间
-XX:MetaspaceSize=256m      # 元空间初始大小
-XX:MaxMetaspaceSize=256m   # 元空间最大大小

# 栈
-Xss512k         # 每个线程的栈大小

# GC 收集器选择
-XX:+UseG1GC              # 使用 G1
-XX:+UseZGC               # 使用 ZGC
-XX:+UseShenandoahGC      # 使用 Shenandoah

# G1 调优
-XX:MaxGCPauseMillis=200              # 目标暂停时间
-XX:G1HeapRegionSize=4m               # Region 大小
-XX:InitiatingHeapOccupancyPercent=45 # 触发并发标记的堆占用比例

# GC 日志（Java 9+统一日志框架）
-Xlog:gc*:file=gc.log:time,level,tags:filecount=5,filesize=20m

# 堆转储
-XX:+HeapDumpOnOutOfMemoryError       # OOM 时自动 dump
-XX:HeapDumpPath=/tmp/heap.hprof      # dump 文件路径
```

### OOM 排查流程

```
1. 确认 OOM 类型
   - java.lang.OutOfMemoryError: Java heap space  → 堆内存不足
   - java.lang.OutOfMemoryError: Metaspace         → 元空间不足
   - java.lang.OutOfMemoryError: unable to create native thread → 线程过多
   - java.lang.OutOfMemoryError: Direct buffer memory → 直接内存溢出

2. 获取堆转储（Heap Dump）
   - OOM 时自动 dump（-XX:+HeapDumpOnOutOfMemoryError）
   - 手动 dump：jmap -dump:format=b,file=heap.hprof <pid>
   - arthas: heapdump /tmp/heap.hprof

3. 分析堆转储
   - MAT（Memory Analyzer Tool）：最常用
   - VisualVM
   - JProfiler

4. 定位大对象和泄漏链
   - MAT 的 Leak Suspects 自动分析
   - Dominator Tree 查看对象支配树
   - 查找 GC Roots 到泄漏对象的引用链

5. 常见原因
   - 集合只加不删（如 static HashMap 不断 put）
   - 连接未关闭（数据库连接、HTTP 连接）
   - ThreadLocal 未 remove
   - 缓存过大（未设上限或淘汰策略）
   - 内存中加载大文件（应该流式处理）
```

### 常用排查工具

```bash
# JDK 自带工具
jps          # 查看 Java 进程
jstat -gc <pid> 1000  # 每秒打印 GC 统计
jmap -heap <pid>      # 堆内存摘要
jmap -histo <pid>     # 对象数量和大小统计
jstack <pid>          # 线程栈快照（死锁检测）
jcmd <pid> VM.flags   # 查看 JVM 参数

# 第三方工具
arthas       # 阿里开源的 Java 诊断工具（强烈推荐）
async-profiler  # 低开销的性能分析器
MAT          # Eclipse Memory Analyzer
JProfiler    # 商业性能分析器
VisualVM     # 图形化监控工具

# Arthas 常用命令
dashboard    # 仪表盘：线程、内存、GC 概览
thread -n 5  # 最忙的 5 个线程
trace class method  # 方法调用链路耗时
watch class method '{params,returnObj,throwExp}' # 监控方法出入参
heapdump /tmp/heap.hprof  # 堆转储
```

## 踩坑指南

### 1. Full GC 频繁
```
原因排查：
1. 老年代空间不足 → 检查是否有内存泄漏
2. 大对象频繁创建 → 调大新生代或设置 PretenureSizeThreshold
3. Metaspace 不足 → 调大 MetaspaceSize
4. System.gc() 被调用 → -XX:+DisableExplicitGC 禁用
5. CMS 并发模式失败 → 降低 CMSInitiatingOccupancyFraction

解决：
- 检查内存泄漏（MAT 分析 dump）
- 调整堆大小和分代比例
- 选择合适的 GC 收集器（G1 或 ZGC）
```

### 2. CPU 飙高排查
```bash
# 1. 找到 CPU 高的 Java 进程
top -Hp <pid>

# 2. 找到 CPU 高的线程（nid）
printf "%x\n" <tid>  # 线程 ID 转十六进制

# 3. jstack 查看该线程在做什么
jstack <pid> | grep <nid> -A 20

# 常见原因：
# - 死循环
# - 频繁 GC（查看 GC 日志）
# - 锁竞争激烈
# - 正则表达式回溯
```

### 3. 元空间 OOM
```
原因：
- 加载了过多的类（动态代理、CGLib、反射等）
- 类加载器泄漏（ClassLoader 未被 GC 回收）

解决：
- 调大 -XX:MaxMetaspaceSize
- 排查是否有类加载器泄漏（MAT 分析）
- 减少动态生成类的数量
```

## 最佳实践

1. **Xms = Xmx**：避免堆大小动态调整的开销
2. **G1 作为默认选择**：堆 > 2GB 场景，Java 9+ 默认
3. **ZGC 追求低延迟**：对延迟敏感的服务（Java 15+），Java 21 分代 ZGC 更优
4. **开启 OOM Dump**：`-XX:+HeapDumpOnOutOfMemoryError` 是生产环境必开参数
5. **GC 日志必须开启**：线上环境必须配置 GC 日志（Java 9+ `-Xlog:gc*`）
6. **避免 Full GC**：合理设置堆大小和分代比例
7. **监控 GC 指标**：GC 频率、GC 耗时、堆使用率纳入监控告警
8. **线程栈不要太大**：默认 1MB 可以缩小到 512KB 或 256KB，节省内存

## 面试高频问题及详细解答

### Q1：JVM 内存结构有哪些区域？各放什么？
**答**：线程私有：(1) 程序计数器（字节码行号）(2) 虚拟机栈（栈帧：局部变量表、操作数栈等）(3) 本地方法栈（native方法）。线程共享：(4) 堆（对象实例，GC主要区域，分新生代和老年代）(5) 方法区/元空间（类信息、常量、静态变量、JIT代码）。另外还有直接内存（NIO DirectByteBuffer）。

### Q2：对象在哪里分配内存？什么时候进入老年代？
**答**：优先在 TLAB（Eden区线程本地缓冲区）分配，TLAB不够在 Eden CAS 分配。进入老年代的条件：(1) 年龄达到阈值（默认15）(2) 大对象直接分配 (3) Survivor 区同龄对象超过 50% 则该年龄及以上直接晋升 (4) Minor GC 后 Survivor 放不下。

### Q3：如何判断对象可以被回收？
**答**：JVM 使用可达性分析：从 GC Roots（虚拟机栈引用、静态变量、常量、JNI引用等）出发，沿引用链遍历，不可达的对象即为垃圾。不使用引用计数法（因为循环引用问题）。对象被回收前还有一次"缓刑"机会：如果重写了 finalize() 且未被调用过，会放入 F-Queue，由 Finalizer 线程执行（不推荐依赖此机制）。

### Q4：G1 和 CMS 的区别？
**答**：(1) CMS 基于标记-清除，G1 基于标记-复制+标记-整理（无碎片）(2) CMS 分新生代/老年代，G1 将堆划分为等大的 Region (3) G1 可以预测暂停时间（-XX:MaxGCPauseMillis），CMS 不能 (4) CMS 已被废弃（Java 14 移除），G1 是 Java 9+ 默认 (5) G1 在大堆（>4GB）表现更好。

### Q5：什么是双亲委派模型？为什么需要？
**答**：类加载请求先委托父类加载器，父类无法加载才自己处理。层次：Bootstrap→Extension→Application→Custom。好处：(1) 避免类重复加载 (2) 保护核心类不被篡改（自定义 java.lang.String 不会被加载）。打破场景：SPI（线程上下文类加载器）、Tomcat（每个应用独立加载器）、OSGi（网状委派）。

### Q6：如何排查 OOM 问题？
**答**：(1) 先确认 OOM 类型（heap/metaspace/thread）(2) 获取堆转储（-XX:+HeapDumpOnOutOfMemoryError 或 jmap）(3) MAT 分析 dump 文件 (4) 查看 Leak Suspects 和 Dominator Tree 定位大对象 (5) 找到 GC Roots 到泄漏对象的引用链。常见原因：集合只加不删、连接未关闭、ThreadLocal 未 remove、缓存无上限。

### Q7：ZGC 是什么？和 G1 有什么区别？
**答**：ZGC 是超低延迟收集器（暂停 <1ms），支持 TB 级堆。核心技术：染色指针（指针高位存 GC 元数据）+ 读屏障 + 并发整理。与 G1 区别：(1) G1 暂停约几十到几百 ms，ZGC <1ms (2) ZGC 几乎全阶段并发 (3) ZGC 不分代（Java 21 分代 ZGC 已支持）(4) ZGC 适合大堆低延迟场景。Java 21 的分代 ZGC 进一步优化了年轻代回收效率。

### Q8：什么是逃逸分析？有什么优化？
**答**：JIT 编译器分析对象的作用域是否逃逸出方法或线程。如果不逃逸：(1) **栈上分配**：对象直接在栈帧上分配，方法结束自动回收，无需 GC (2) **标量替换**：将对象拆散为基本类型变量 (3) **锁消除**：对不逃逸对象去除 synchronized。这些优化可以显著减少堆分配和 GC 压力。`-XX:+DoEscapeAnalysis`（默认开启）。

### Q9：JVM 调优的一般步骤？
**答**：(1) 设定目标（延迟/吞吐量/内存占用）(2) 开启 GC 日志 (3) 分析 GC 日志（频率、耗时、回收效果）(4) 调整堆大小（Xms=Xmx）和分代比例 (5) 选择合适的 GC 收集器 (6) 排查内存泄漏（Full GC 频繁时）(7) 持续监控和微调。常用工具：GCViewer、GCEasy、Arthas。

### Q10：类加载过程中"准备"和"初始化"有什么区别？
**答**："准备"阶段为静态变量分配内存并设**默认零值**（int=0, boolean=false, 引用=null），此时 `static int value = 123` 的 value 是 0。"初始化"阶段执行 `<clinit>()` 方法，将静态变量赋为程序员指定的值（value=123），并执行静态代码块。特例：`static final` 修饰的编译期常量在准备阶段就直接赋值。

> **交叉引用**：synchronized 的锁升级机制参见 [多线程与并发](./04_多线程与并发.md)；对象内存布局与 hashCode 参见 [面向对象](./02_面向对象.md)
