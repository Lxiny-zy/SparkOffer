# Java 多线程与并发

## 创建线程的方式

1. **继承 Thread**：`class MyThread extends Thread { public void run() { } }`
2. **实现 Runnable**：`new Thread(new MyRunnable()).start()`
3. **实现 Callable**：有返回值，配合 `FutureTask` 使用
4. **线程池**：`ExecutorService`（推荐）

```java
// 方式1：继承 Thread（不推荐，Java 单继承限制）
class MyThread extends Thread {
    @Override
    public void run() { System.out.println("Thread: " + getName()); }
}

// 方式2：实现 Runnable（推荐，解耦任务与线程）
class MyTask implements Runnable {
    @Override
    public void run() { System.out.println("Runnable task"); }
}
new Thread(new MyTask(), "worker-1").start();

// 方式3：Callable + FutureTask（有返回值和异常）
Callable<Integer> callable = () -> {
    Thread.sleep(1000);
    return 42;
};
FutureTask<Integer> futureTask = new FutureTask<>(callable);
new Thread(futureTask).start();
Integer result = futureTask.get(); // 阻塞等待结果，返回 42

// 方式4：线程池提交（推荐）
ExecutorService pool = Executors.newFixedThreadPool(4);
Future<String> future = pool.submit(() -> "result");
```

## 线程生命周期

```
         start()
  NEW ──────────→ RUNNABLE ──────────→ TERMINATED
                    ↑  ↓                   ↑
                    │  │ synchronized       │
                    │  ↓ 获取锁失败         │ run()结束
                    │ BLOCKED ──→ 获取锁成功 │ 或异常
                    │                      │
                    │ notify()/超时         │
                    │ ←── WAITING ─────────│
                    │      ↑ wait()        │
                    │                      │
                    │ 超时到期              │
                    │ ←── TIMED_WAITING ───│
                           ↑ sleep()/wait(t)
```

- `start()`：NEW → RUNNABLE（**不能调用两次**，否则 IllegalThreadStateException）
- `sleep(ms)`：RUNNABLE → TIMED_WAITING（不释放锁）
- `wait()`：RUNNABLE → WAITING（**释放锁**，必须在 synchronized 块中调用）
- `wait(ms)`：RUNNABLE → TIMED_WAITING（释放锁）
- `synchronized` 获取锁失败：RUNNABLE → BLOCKED
- `LockSupport.park()`：RUNNABLE → WAITING

### sleep() vs wait() 区别

| 特性 | sleep() | wait() |
|------|---------|--------|
| 所属类 | Thread | Object |
| 是否释放锁 | 不释放 | 释放 |
| 使用位置 | 任意 | synchronized 块中 |
| 唤醒方式 | 时间到自动唤醒 | notify()/notifyAll() |
| 用途 | 线程暂停 | 线程间通信 |

### 线程中断机制

```java
// 中断是一种协作机制，设置中断标志位，线程自己决定如何响应
Thread t = new Thread(() -> {
    while (!Thread.currentThread().isInterrupted()) {
        try {
            Thread.sleep(1000);
        } catch (InterruptedException e) {
            // sleep/wait/join 被中断时会抛 InterruptedException 并清除中断标志
            // 需要重新设置中断标志或退出
            Thread.currentThread().interrupt(); // 重新设置中断标志
            break;
        }
    }
    System.out.println("线程正常退出");
});

t.start();
t.interrupt(); // 设置中断标志

// 为什么不推荐 stop()？
// stop() 会立即终止线程并释放所有锁，可能导致数据不一致
// 已被 @Deprecated
```

## synchronized 关键字

### 三种用法

```java
// 修饰实例方法：锁的是当前实例对象 this
public synchronized void method() { }

// 修饰静态方法：锁的是类的 Class 对象
public static synchronized void staticMethod() { }

// 修饰代码块：锁的是指定的对象
public void blockMethod() {
    synchronized (this) { }           // 锁实例
    synchronized (MyClass.class) { }  // 锁类
    synchronized (lockObj) { }        // 锁任意对象
}
```

### synchronized 底层原理

```
// 字节码层面
monitorenter  // 尝试获取 monitor 锁
...           // 临界区代码
monitorexit   // 释放 monitor 锁
monitorexit   // 异常时也要释放（编译器自动生成）

// 对象头中的 Mark Word 存储锁信息
|-----------------------------------------------|
|         Mark Word (64 bits)                    |
|-----------------------------------------------|
| 无锁    | hashCode(31) | age(4) | biased(1) | 01 |
| 偏向锁  | threadId(54) | epoch(2) | age(4) | 1 | 01 |
| 轻量级锁 | ptr_to_lock_record(62)           | 00 |
| 重量级锁 | ptr_to_heavyweight_monitor(62)   | 10 |
| GC标记   |                                  | 11 |
|-----------------------------------------------|
```

### 锁升级过程（JDK 6+ 优化）

```
无锁 → 偏向锁 → 轻量级锁 → 重量级锁（只升不降）

1. 偏向锁（Biased Locking）
   - 第一个线程获取锁时，在 Mark Word 中记录线程ID
   - 之后该线程再次进入无需CAS操作
   - 适合单线程重复获取锁的场景
   - JDK 15 默认关闭偏向锁（JEP 374）

2. 轻量级锁（Lightweight Lock）
   - 多个线程交替获取锁（无竞争）
   - 在栈帧中创建 Lock Record，用 CAS 替换 Mark Word
   - 如果 CAS 失败，说明有竞争，升级为重量级锁

3. 重量级锁（Heavyweight Lock）
   - 有线程竞争
   - 依赖操作系统 mutex 互斥量
   - 线程阻塞、唤醒涉及用户态/内核态切换，性能差

4. 自旋锁优化
   - 升级为重量级锁前先自旋（循环尝试获取锁）
   - 适合锁持有时间短的场景
   - 自适应自旋：JVM 根据历史情况动态调整自旋次数
```

### 锁消除与锁粗化

```java
// 锁消除：JIT 发现锁对象不可能被其他线程访问，自动去掉锁
public void method() {
    Object lock = new Object(); // 局部变量，不会逃逸
    synchronized (lock) {       // JIT 会消除这个锁
        // ...
    }
}

// 锁粗化：连续多次加锁解锁 → 合并为一次
for (int i = 0; i < 100; i++) {
    synchronized (lock) { /* ... */ }
}
// JIT 可能优化为：
synchronized (lock) {
    for (int i = 0; i < 100; i++) { /* ... */ }
}
```

## volatile 关键字

### 三大特性

```java
// 1. 可见性：一个线程修改后，其他线程立即看到最新值
volatile boolean running = true;

// 线程A
while (running) { /* 工作 */ }

// 线程B
running = false; // 线程A 立即可见

// 2. 有序性：禁止指令重排序（通过内存屏障）
// 经典案例：DCL 单例模式必须加 volatile
class Singleton {
    private static volatile Singleton instance; // 必须 volatile！

    public static Singleton getInstance() {
        if (instance == null) {                 // 第一次检查
            synchronized (Singleton.class) {
                if (instance == null) {         // 第二次检查
                    instance = new Singleton(); // 非原子操作！
                    // 字节码：1.分配内存 2.初始化 3.引用赋值
                    // 无 volatile 时 2 和 3 可能重排序
                    // 其他线程可能看到未初始化完成的对象
                }
            }
        }
        return instance;
    }
}

// 3. 不保证原子性
volatile int count = 0;
count++; // 不是原子操作！（读-改-写三步）
// 等价于：int temp = count; temp = temp + 1; count = temp;
// 多线程下可能丢失更新
```

### volatile 的底层实现

```
// volatile 读：在读操作后插入 LoadLoad + LoadStore 屏障
// volatile 写：在写操作前插入 StoreStore 屏障，写操作后插入 StoreLoad 屏障

// 内存屏障的作用：
// StoreStore：确保 volatile 写之前的所有写操作都已刷到主存
// StoreLoad：确保 volatile 写对其他处理器可见后才执行后面的读
// LoadLoad：确保 volatile 读之后的读操作不会重排到前面
// LoadStore：确保 volatile 读之后的写操作不会重排到前面
```

## JUC 并发工具包

### Lock 接口与 ReentrantLock

```java
ReentrantLock lock = new ReentrantLock(true); // true = 公平锁
lock.lock();
try {
    // 临界区代码
} finally {
    lock.unlock();  // 必须在 finally 中释放！
}

// 可中断获取锁
try {
    lock.lockInterruptibly(); // 等待锁期间可被中断
    try {
        // 临界区
    } finally {
        lock.unlock();
    }
} catch (InterruptedException e) {
    // 被中断，做清理
}

// 超时获取锁
if (lock.tryLock(5, TimeUnit.SECONDS)) {
    try {
        // 获取成功
    } finally {
        lock.unlock();
    }
} else {
    // 获取超时，执行替代逻辑
}
```

### ReentrantLock vs synchronized

| 特性 | synchronized | ReentrantLock |
|------|-------------|---------------|
| 锁获取方式 | 自动获取/释放 | 手动 lock/unlock |
| 可中断 | 不可 | lockInterruptibly() |
| 超时获取 | 不可 | tryLock(timeout) |
| 公平锁 | 非公平 | 可选公平/非公平 |
| 条件变量 | 一个（wait/notify） | 多个 Condition |
| 锁绑定条件 | 一个 | 多个 newCondition() |
| 底层实现 | JVM 指令 | AQS 框架 |
| 性能 | JDK 6 后优化接近 | 稍好 |

### AQS（AbstractQueuedSynchronizer）原理

```java
// AQS 是 JUC 包的核心框架，ReentrantLock、Semaphore、CountDownLatch 都基于它
// 核心思想：
// 1. 维护一个 volatile int state（同步状态）
// 2. 维护一个 FIFO 双向等待队列（CLH 变体）

// ReentrantLock 中 state 的含义：
// state = 0：未锁定
// state > 0：锁定状态，值表示重入次数

// 获取锁（简化版）：
// 1. CAS 尝试将 state 从 0 改为 1
// 2. 成功 → 获取锁，设置 exclusiveOwnerThread 为当前线程
// 3. 失败 → 检查是否重入（当前线程是否就是持有者）
//    - 是重入 → state++
//    - 不是重入 → 封装为 Node 加入等待队列，park 挂起

// 公平锁 vs 非公平锁：
// 公平锁：先检查等待队列中是否有线程在等待，有则排队
// 非公平锁：直接 CAS 抢锁，抢不到再排队（默认，吞吐量更高）
```

### Condition 条件变量

```java
// 一个 Lock 可以创建多个 Condition，实现精准唤醒
ReentrantLock lock = new ReentrantLock();
Condition notFull = lock.newCondition();
Condition notEmpty = lock.newCondition();

// 生产者
lock.lock();
try {
    while (queue.isFull()) {
        notFull.await();   // 队列满，等待 "不满" 条件
    }
    queue.add(item);
    notEmpty.signal();     // 通知消费者 "不空"
} finally {
    lock.unlock();
}

// 消费者
lock.lock();
try {
    while (queue.isEmpty()) {
        notEmpty.await();  // 队列空，等待 "不空" 条件
    }
    item = queue.remove();
    notFull.signal();      // 通知生产者 "不满"
} finally {
    lock.unlock();
}
```

### 原子类

```java
// 基于 CAS（Compare And Swap）实现，无锁线程安全
AtomicInteger count = new AtomicInteger(0);
count.incrementAndGet();  // ++count
count.getAndIncrement();  // count++
count.compareAndSet(0, 1); // CAS：期望值 0，新值 1

// CAS 的本质
// CPU 指令 cmpxchg，比较内存值与期望值：
// 相等 → 写入新值，返回 true
// 不等 → 不修改，返回 false
// 整个过程是原子的（硬件保证）

// ABA 问题
// 线程1读取值 A
// 线程2将 A 改为 B，再改回 A
// 线程1做 CAS 时发现值仍是 A，认为没被修改（但实际上被改过了）
// 大多数场景下 ABA 不是问题，但涉及链表指针等场景需要注意

// 解决 ABA 问题
AtomicStampedReference<Integer> ref = new AtomicStampedReference<>(1, 0);
int stamp = ref.getStamp(); // 获取版本号
ref.compareAndSet(1, 2, stamp, stamp + 1); // CAS 时同时比较版本号

// Java 8+ LongAdder：比 AtomicLong 性能更好
// 原理：分散热点（类似 ConcurrentHashMap 的分段思想）
// 每个线程 CAS 自己的 Cell，最后汇总
LongAdder adder = new LongAdder();
adder.increment(); // 分散到不同 Cell
adder.sum();       // 汇总所有 Cell
// 适合统计计数场景，不适合需要精确实时值的场景
```

### 并发工具类

```java
// CountDownLatch：一次性计数器，到 0 后释放所有等待线程
CountDownLatch latch = new CountDownLatch(3);

for (int i = 0; i < 3; i++) {
    new Thread(() -> {
        // 执行初始化任务
        doInit();
        latch.countDown(); // 完成一个，计数 -1
    }).start();
}
latch.await(); // 主线程等待所有初始化完成
System.out.println("所有初始化完成，系统启动");

// CyclicBarrier：可重用屏障，所有线程到达后一起继续
CyclicBarrier barrier = new CyclicBarrier(3, () -> {
    System.out.println("所有线程到达屏障，开始下一阶段");
});

for (int i = 0; i < 3; i++) {
    new Thread(() -> {
        phase1();
        barrier.await(); // 等待其他线程
        phase2();
        barrier.await(); // 再次等待（可重用！）
    }).start();
}

// Semaphore：信号量，控制并发访问数量
Semaphore semaphore = new Semaphore(5); // 最多 5 个线程同时执行

for (int i = 0; i < 20; i++) {
    new Thread(() -> {
        try {
            semaphore.acquire(); // 获取许可（获取不到则阻塞）
            accessResource();
        } finally {
            semaphore.release(); // 释放许可
        }
    }).start();
}

// ReadWriteLock：读写锁
ReentrantReadWriteLock rwLock = new ReentrantReadWriteLock();
ReadWriteLock readLock = rwLock.readLock();
ReadWriteLock writeLock = rwLock.writeLock();

// 读操作：多个线程可以同时读
readLock.lock();
try { return data; } finally { readLock.unlock(); }

// 写操作：独占，读和写都被阻塞
writeLock.lock();
try { data = newValue; } finally { writeLock.unlock(); }

// StampedLock（Java 8+）：优化版读写锁，支持乐观读
StampedLock stampedLock = new StampedLock();

// 乐观读：不加锁，读完后验证是否有写操作
long stamp = stampedLock.tryOptimisticRead();
int x = this.x, y = this.y;
if (!stampedLock.validate(stamp)) { // 验证期间是否有写
    stamp = stampedLock.readLock();  // 验证失败，升级为悲观读
    try { x = this.x; y = this.y; } finally { stampedLock.unlockRead(stamp); }
}
```

## CompletableFuture（Java 8+）

```java
// CompletableFuture 是 Future 的增强版，支持链式编程和组合操作

// 创建异步任务
CompletableFuture<String> future = CompletableFuture.supplyAsync(() -> {
    // 在 ForkJoinPool.commonPool() 中执行
    return queryDatabase();
});

// 链式处理
CompletableFuture<Integer> result = CompletableFuture
    .supplyAsync(() -> "Hello")       // 异步执行
    .thenApply(s -> s + " World")     // 同步转换
    .thenApplyAsync(s -> s.length()); // 异步转换

// 消费结果（无返回值）
future.thenAccept(System.out::println);

// 组合两个 Future
CompletableFuture<String> name = CompletableFuture.supplyAsync(() -> getUserName());
CompletableFuture<Integer> age = CompletableFuture.supplyAsync(() -> getUserAge());

// 两个都完成后合并
CompletableFuture<String> combined = name.thenCombine(age,
    (n, a) -> n + " is " + a + " years old");

// 任意一个完成
CompletableFuture<Object> anyOf = CompletableFuture.anyOf(future1, future2, future3);

// 所有完成
CompletableFuture<Void> allOf = CompletableFuture.allOf(future1, future2, future3);
allOf.thenRun(() -> {
    String r1 = future1.join();
    String r2 = future2.join();
    String r3 = future3.join();
});

// 异常处理
CompletableFuture<String> safe = future
    .exceptionally(ex -> "默认值")           // 异常时返回默认值
    .handle((result2, ex) -> {                // 无论成功失败都执行
        if (ex != null) return "error: " + ex.getMessage();
        return result2;
    });

// 实战：并行调用多个服务
public UserInfo getUserInfo(long userId) {
    CompletableFuture<User> userFuture = CompletableFuture.supplyAsync(() ->
        userService.getUser(userId));
    CompletableFuture<List<Order>> orderFuture = CompletableFuture.supplyAsync(() ->
        orderService.getOrders(userId));
    CompletableFuture<Integer> scoreFuture = CompletableFuture.supplyAsync(() ->
        scoreService.getScore(userId));

    CompletableFuture.allOf(userFuture, orderFuture, scoreFuture).join();

    return new UserInfo(userFuture.join(), orderFuture.join(), scoreFuture.join());
}

// 超时处理（Java 9+）
future.orTimeout(3, TimeUnit.SECONDS);           // 超时抛 TimeoutException
future.completeOnTimeout("默认值", 3, TimeUnit.SECONDS); // 超时返回默认值
```

## 线程池

```java
// 推荐使用 ThreadPoolExecutor 直接创建
ThreadPoolExecutor executor = new ThreadPoolExecutor(
    5,              // corePoolSize: 核心线程数
    10,             // maximumPoolSize: 最大线程数
    60L,            // keepAliveTime: 空闲线程存活时间
    TimeUnit.SECONDS,
    new LinkedBlockingQueue<>(100),  // workQueue: 工作队列
    new ThreadFactory() {            // threadFactory: 线程工厂
        private AtomicInteger counter = new AtomicInteger(1);
        @Override
        public Thread newThread(Runnable r) {
            Thread t = new Thread(r, "my-pool-" + counter.getAndIncrement());
            t.setDaemon(false);
            return t;
        }
    },
    new ThreadPoolExecutor.CallerRunsPolicy()  // handler: 拒绝策略
);
```

### 核心参数详解

| 参数 | 说明 | 如何设置 |
|------|------|---------|
| corePoolSize | 核心线程数，即使空闲也不回收 | CPU密集型: N+1, IO密集型: 2N |
| maximumPoolSize | 最大线程数 | 根据业务峰值估算 |
| keepAliveTime | 非核心线程空闲存活时间 | 一般 30-60 秒 |
| workQueue | 任务等待队列 | 有界队列防止 OOM |
| threadFactory | 创建线程的工厂 | 自定义线程名，便于排查 |
| handler | 拒绝策略 | 根据业务选择 |

### 执行流程

```
提交任务
    │
    ↓
线程数 < corePoolSize？ ──是──→ 创建核心线程执行
    │ 否
    ↓
队列未满？ ──是──→ 放入工作队列
    │ 否
    ↓
线程数 < maximumPoolSize？ ──是──→ 创建非核心线程执行
    │ 否
    ↓
执行拒绝策略
```

### 四种拒绝策略

| 策略 | 行为 | 适用场景 |
|------|------|---------|
| AbortPolicy | 抛 RejectedExecutionException（默认） | 需要明确知道任务被拒绝 |
| CallerRunsPolicy | 调用者线程执行任务 | 不允许丢任务，可接受降速 |
| DiscardPolicy | 静默丢弃 | 允许丢任务 |
| DiscardOldestPolicy | 丢弃队列最老的任务 | 新任务优先级高 |

### 为什么不建议使用 Executors 创建线程池？

```java
// 问题1：newFixedThreadPool 和 newSingleThreadExecutor
// 使用 LinkedBlockingQueue（无界），可能堆积大量任务导致 OOM
ExecutorService fixed = Executors.newFixedThreadPool(10);
// 底层：new LinkedBlockingQueue<Runnable>() → 容量 Integer.MAX_VALUE

// 问题2：newCachedThreadPool
// maximumPoolSize = Integer.MAX_VALUE，可能创建过多线程
ExecutorService cached = Executors.newCachedThreadPool();
// 底层：new SynchronousQueue<Runnable>(), maxPoolSize = Integer.MAX_VALUE

// 问题3：newScheduledThreadPool
// 使用 DelayedWorkQueue（无界），也可能 OOM

// 阿里巴巴 Java 开发手册强制要求：线程池必须通过 ThreadPoolExecutor 创建
```

### 线程池参数调优

```java
// CPU 密集型：线程数 = CPU核心数 + 1
// IO 密集型：线程数 = CPU核心数 * 2（或根据 IO/CPU 比率计算）
// 混合型：拆分为 CPU 密集和 IO 密集两个线程池

int cpuCores = Runtime.getRuntime().availableProcessors();

// CPU 密集型
ThreadPoolExecutor cpuPool = new ThreadPoolExecutor(
    cpuCores + 1, cpuCores + 1, 0L, TimeUnit.SECONDS,
    new LinkedBlockingQueue<>(100));

// IO 密集型
ThreadPoolExecutor ioPool = new ThreadPoolExecutor(
    cpuCores * 2, cpuCores * 4, 60L, TimeUnit.SECONDS,
    new LinkedBlockingQueue<>(500));

// 动态调整线程池参数（Java 运行时可以修改）
executor.setCorePoolSize(newCoreSize);
executor.setMaximumPoolSize(newMaxSize);

// 监控线程池状态
executor.getActiveCount();    // 活跃线程数
executor.getPoolSize();       // 当前线程数
executor.getQueue().size();   // 队列中等待的任务数
executor.getCompletedTaskCount(); // 已完成任务数
```

## ThreadLocal

### 原理

```java
// 每个 Thread 有一个 ThreadLocalMap 字段
// ThreadLocal 作为 key，值作为 value
// 实现了线程间数据隔离

ThreadLocal<String> threadLocal = new ThreadLocal<>();

// 实际存储在 Thread.threadLocals（ThreadLocalMap）中
// ThreadLocalMap 底层是 Entry[] 数组
// Entry 继承 WeakReference<ThreadLocal>，key 是弱引用

// set 操作本质
public void set(T value) {
    Thread t = Thread.currentThread();
    ThreadLocalMap map = t.threadLocals;
    if (map != null)
        map.set(this, value); // this 就是 ThreadLocal 对象
    else
        createMap(t, value);
}
```

### 内存泄漏问题

```
Thread → ThreadLocalMap → Entry(WeakReference<ThreadLocal>, value)

// 如果 ThreadLocal 对象被回收（无强引用）：
// Entry 的 key（弱引用）变成 null
// 但 value 还在！因为 Entry 被 ThreadLocalMap 强引用
// 导致 value 无法被 GC → 内存泄漏

// 特别是在线程池场景：
// 线程不会被销毁，ThreadLocalMap 一直存在
// 积累大量 key=null 的 Entry

// 解决方案：用完必须 remove()
ThreadLocal<User> userContext = new ThreadLocal<>();
try {
    userContext.set(currentUser);
    // 业务逻辑
} finally {
    userContext.remove(); // 必须清理！
}

// ThreadLocal 的 get/set/remove 会顺带清理 key=null 的 Entry
// 但不能完全依赖这个，因为不一定会触发
```

### ThreadLocal 应用场景

```java
// 1. 用户上下文传递
public class UserContext {
    private static final ThreadLocal<User> holder = new ThreadLocal<>();
    public static void set(User user) { holder.set(user); }
    public static User get() { return holder.get(); }
    public static void clear() { holder.remove(); }
}

// 2. SimpleDateFormat 线程不安全，用 ThreadLocal 包装
private static final ThreadLocal<SimpleDateFormat> dateFormat =
    ThreadLocal.withInitial(() -> new SimpleDateFormat("yyyy-MM-dd"));
// 更推荐用 DateTimeFormatter（Java 8+ 线程安全）

// 3. 数据库连接/事务管理
// Spring 的 @Transactional 就是通过 ThreadLocal 绑定 Connection

// 4. InheritableThreadLocal：父线程的值可以传递给子线程
InheritableThreadLocal<String> itl = new InheritableThreadLocal<>();
itl.set("parent-value");
new Thread(() -> {
    System.out.println(itl.get()); // "parent-value"
}).start();

// 注意：线程池中 InheritableThreadLocal 不生效（线程复用，只在创建时传递）
// 解决：阿里的 TransmittableThreadLocal
```

## Virtual Threads 虚拟线程（Java 21）

```java
// 传统平台线程（Platform Thread）：
// - 1:1 映射到操作系统线程
// - 创建成本高（约 1MB 栈空间）
// - 受操作系统限制，通常几千个

// 虚拟线程（Virtual Thread）：
// - 由 JVM 管理，运行在少量平台线程上（M:N 模型）
// - 极轻量（约几 KB 栈空间）
// - 可以创建数百万个

// 创建虚拟线程
Thread vt = Thread.ofVirtual().name("vt-1").start(() -> {
    System.out.println("Virtual thread: " + Thread.currentThread());
});

// 虚拟线程执行器（最推荐的方式）
try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
    // 每个任务一个虚拟线程，无需担心线程数量
    for (int i = 0; i < 1_000_000; i++) {
        executor.submit(() -> {
            Thread.sleep(Duration.ofSeconds(1)); // 阻塞时释放平台线程！
            return callRemoteService();
        });
    }
} // try-with-resources 自动关闭执行器

// 虚拟线程的关键特性：
// 1. 阻塞不占用平台线程（JVM 自动挂载/卸载）
// 2. 不需要线程池（每个任务一个虚拟线程）
// 3. 与传统 Thread API 完全兼容

// 虚拟线程的限制：
// 1. synchronized 块中阻塞会 pin 住平台线程（改用 ReentrantLock）
// 2. 不适合 CPU 密集型任务（没有额外好处）
// 3. ThreadLocal 仍然可用，但内存占用需注意（百万级虚拟线程）

// 虚拟线程 vs 响应式编程
// 虚拟线程：写同步代码，享异步性能，代码简单易懂
// 响应式（WebFlux）：回调/链式编程，代码复杂，调试困难
// 结论：虚拟线程让很多场景不再需要响应式编程
```

## 死锁

### 四个必要条件

```
1. 互斥：资源只能被一个线程持有
2. 占有且等待：持有资源的线程可以等待其他资源
3. 不可抢占：已持有的资源不能被强制释放
4. 循环等待：多个线程形成环形等待链
```

### 死锁代码示例与解决

```java
// 死锁示例
Object lockA = new Object();
Object lockB = new Object();

// 线程1：先锁A再锁B
new Thread(() -> {
    synchronized (lockA) {
        Thread.sleep(100);
        synchronized (lockB) { /* ... */ }
    }
}).start();

// 线程2：先锁B再锁A
new Thread(() -> {
    synchronized (lockB) {
        Thread.sleep(100);
        synchronized (lockA) { /* ... */ } // 死锁！
    }
}).start();

// 解决方案1：统一加锁顺序（破坏循环等待）
// 所有线程都先锁 A 再锁 B

// 解决方案2：超时获取锁（破坏占有且等待）
ReentrantLock lock1 = new ReentrantLock();
ReentrantLock lock2 = new ReentrantLock();
if (lock1.tryLock(1, TimeUnit.SECONDS)) {
    try {
        if (lock2.tryLock(1, TimeUnit.SECONDS)) {
            try { /* ... */ } finally { lock2.unlock(); }
        }
    } finally { lock1.unlock(); }
}

// 死锁排查
// 1. jstack <pid>：打印线程栈，查找 BLOCKED 线程
// 2. jconsole 或 VisualVM 图形界面检测
// 3. ThreadMXBean 编程检测
ThreadMXBean bean = ManagementFactory.getThreadMXBean();
long[] deadlockedThreads = bean.findDeadlockedThreads();
```

## 踩坑指南

### 1. 线程安全的单例

```java
// 最推荐：枚举单例（防反射、防序列化）
public enum Singleton {
    INSTANCE;
    public void doWork() { /* ... */ }
}

// 静态内部类（懒加载、线程安全）
public class Singleton {
    private Singleton() {}
    private static class Holder {
        static final Singleton INSTANCE = new Singleton();
    }
    public static Singleton getInstance() {
        return Holder.INSTANCE;
    }
}
```

### 2. HashMap 在多线程下的问题
```java
// 即使只是读，多线程同时读和写 HashMap 也不安全
// 因为写操作（put/扩容）会修改内部结构，读可能读到中间状态

// 解决：ConcurrentHashMap 或 Collections.synchronizedMap()
```

### 3. volatile 不能替代加锁
```java
// volatile 不保证原子性，以下代码不是线程安全的
volatile int count = 0;
// 20 个线程同时执行 count++，结果可能小于期望值
// 应使用 AtomicInteger 或 synchronized
```

### 4. 线程池异常处理
```java
// submit() 提交的任务，异常会被吞掉
executor.submit(() -> { throw new RuntimeException("oops"); });
// 不会打印异常！异常被封装在 Future 中

// 解决方式1：try-catch
executor.submit(() -> {
    try {
        riskyTask();
    } catch (Exception e) {
        log.error("Task failed", e);
    }
});

// 解决方式2：使用 execute()（但无法获取返回值）
executor.execute(() -> { throw new RuntimeException("oops"); });
// 会输出异常到 UncaughtExceptionHandler

// 解决方式3：获取 Future 并调用 get()
Future<?> future = executor.submit(() -> { throw new RuntimeException(); });
try {
    future.get(); // 异常会被重新抛出为 ExecutionException
} catch (ExecutionException e) {
    log.error("Task failed", e.getCause());
}
```

## 最佳实践

1. **线程命名**：使用有意义的线程名，便于日志排查
2. **线程池优先**：不要手动创建线程，使用线程池管理
3. **ThreadPoolExecutor 创建**：不用 Executors，直接构造 ThreadPoolExecutor
4. **有界队列**：线程池的工作队列必须有界，防止 OOM
5. **finally 释放锁**：ReentrantLock 必须在 finally 中 unlock
6. **ThreadLocal 用完 remove**：尤其在线程池场景，防止内存泄漏
7. **能用 concurrent 包就不要自己实现同步**：ConcurrentHashMap、AtomicInteger 等
8. **优先使用更高层的并发工具**：CompletableFuture > Future > Thread
9. **Java 21+ 考虑虚拟线程**：IO 密集型任务可大幅简化并发编程

## 面试高频问题及详细解答

### Q1：synchronized 和 ReentrantLock 的区别？
**答**：(1) synchronized 是 JVM 内置关键字，自动获取/释放；ReentrantLock 是 API 级别，需手动 lock/unlock (2) ReentrantLock 支持可中断获取(lockInterruptibly)、超时获取(tryLock)、公平锁选择 (3) ReentrantLock 支持多个 Condition 条件变量 (4) synchronized 经过 JDK 6+ 优化（偏向锁、轻量级锁）性能接近 (5) 建议：简单场景用 synchronized，需要高级功能用 ReentrantLock。

### Q2：volatile 能保证线程安全吗？
**答**：不能完全保证。volatile 保证可见性和有序性，但不保证原子性。例如 `volatile int count; count++;` 不是线程安全的，因为 count++ 包含读-改-写三步非原子操作。适用场景：一个线程写，其他线程只读的标志变量；DCL单例模式中防止指令重排。需要原子性时使用 AtomicInteger 或加锁。

### Q3：线程池的核心参数和执行流程？
**答**：7个参数：corePoolSize（核心线程数）、maximumPoolSize（最大线程数）、keepAliveTime（空闲线程存活时间）、workQueue（工作队列）、threadFactory（线程工厂）、handler（拒绝策略）。执行流程：任务提交→核心线程未满则创建核心线程→核心满则放入队列→队列满则创建非核心线程→都满则执行拒绝策略。

### Q4：CAS 是什么？有什么问题？
**答**：CAS（Compare And Swap）是一种乐观锁，比较内存值与期望值，相等则更新。硬件指令保证原子性。问题：(1) **ABA问题**：值从A改为B再改回A，CAS误认为没变。解决：AtomicStampedReference加版本号 (2) **自旋开销**：竞争激烈时CPU空转。解决：LongAdder分散热点 (3) **只能保证单个变量原子性**。解决：AtomicReference封装多个变量。

### Q5：ThreadLocal 的原理？为什么会内存泄漏？
**答**：每个Thread有一个ThreadLocalMap，ThreadLocal对象作为key（弱引用），值作为value（强引用）。当ThreadLocal对象被GC（无强引用时），key变为null，但value仍被Entry强引用，无法回收→内存泄漏。在线程池中尤其严重（线程长期存活）。解决：使用后在finally中调用remove()。

### Q6：死锁的条件和解决方法？
**答**：四个必要条件：互斥、占有且等待、不可抢占、循环等待。预防：(1) 统一加锁顺序（破坏循环等待）(2) tryLock超时获取（破坏占有且等待）(3) 一次性获取所有锁。排查：jstack打印线程栈、jconsole检测、ThreadMXBean编程检测。

### Q7：CompletableFuture 相比 Future 有什么优势？
**答**：(1) Future.get() 是阻塞的，CompletableFuture 支持回调（thenApply/thenAccept）(2) 支持链式编程和组合操作（thenCombine/allOf/anyOf）(3) 支持异常处理（exceptionally/handle）(4) Java 9+ 支持超时处理（orTimeout）(5) 可以手动完成（complete）。极大简化了异步编程模型。

### Q8：Java 21 虚拟线程和传统线程的区别？适用场景？
**答**：传统平台线程 1:1 映射操作系统线程，创建成本高（约1MB栈），受OS限制约几千个。虚拟线程由 JVM 管理（M:N模型），极轻量（约几KB），可创建数百万个。阻塞时自动释放平台线程。适用于IO密集型任务（Web请求、数据库调用），不适合CPU密集型。注意：synchronized块中阻塞会pin住平台线程，建议改用ReentrantLock。

### Q9：线程池如何设置合理的线程数？
**答**：(1) CPU密集型：N+1（N为CPU核数），多一个线程处理页缺失等情况 (2) IO密集型：2N 或 N * (1 + IO时间/CPU时间) (3) 混合型：拆分为CPU密集和IO密集两个线程池。实际需要压测调优，可以用动态调整的方式（setCorePoolSize）。

### Q10：什么是 AQS？它是如何实现的？
**答**：AQS（AbstractQueuedSynchronizer）是JUC包的核心框架，ReentrantLock、Semaphore、CountDownLatch等都基于它。核心：(1) volatile int state 表示同步状态 (2) FIFO双向等待队列（CLH变体）管理排队线程。获取锁时CAS修改state，失败则封装为Node入队park挂起。释放锁时修改state并unpark队列中的下一个线程。子类通过实现tryAcquire/tryRelease来定义state的含义。

> **交叉引用**：线程安全的集合类参见 [集合框架](./03_集合框架.md)；JVM 对 synchronized 的优化参见 [JVM](./05_JVM.md)；虚拟线程的更多细节参见 [Java新特性](./07_Java新特性与常用API.md)
