# Java 集合框架

## 集合体系结构

```
Collection
├── List（有序、可重复）
│   ├── ArrayList      —— 动态数组，随机访问快
│   ├── LinkedList      —— 双向链表，增删快
│   ├── Vector          —— 线程安全的动态数组（过时，用 CopyOnWriteArrayList 替代）
│   └── CopyOnWriteArrayList —— 写时复制，读多写少场景
├── Set（无序、不重复）
│   ├── HashSet         —— 基于 HashMap，O(1) 查找
│   ├── LinkedHashSet   —— 保持插入顺序
│   └── TreeSet         —— 红黑树，有序
└── Queue（队列）
    ├── LinkedList       —— 双端队列
    ├── PriorityQueue    —— 优先队列（小顶堆）
    ├── ArrayDeque       —— 数组双端队列（推荐替代 Stack）
    └── BlockingQueue    —— 阻塞队列（并发场景）
        ├── ArrayBlockingQueue   —— 有界数组阻塞队列
        ├── LinkedBlockingQueue  —— 可选有界链表阻塞队列
        ├── PriorityBlockingQueue —— 无界优先级阻塞队列
        ├── SynchronousQueue     —— 无容量同步队列
        └── DelayQueue           —— 延迟队列

Map（键值对，不属于 Collection）
├── HashMap            —— 数组+链表+红黑树，最常用
├── LinkedHashMap      —— 保持插入/访问顺序（可实现 LRU）
├── TreeMap            —— 红黑树，按 key 排序
├── Hashtable          —— 线程安全（过时，用 ConcurrentHashMap）
├── ConcurrentHashMap  —— 线程安全（推荐）
├── WeakHashMap        —— 弱引用 key，可被 GC 回收
└── IdentityHashMap    —— 用 == 比较 key（而非 equals）
```

## ArrayList 深度解析

### 底层结构
```java
// ArrayList 核心字段
transient Object[] elementData; // 存储元素的数组
private int size;               // 实际元素数量（不是数组长度）
```

### 扩容机制源码分析

```java
// 默认初始容量
private static final int DEFAULT_CAPACITY = 10;

// 添加元素时的扩容逻辑
public boolean add(E e) {
    ensureCapacityInternal(size + 1); // 检查是否需要扩容
    elementData[size++] = e;
    return true;
}

private void grow(int minCapacity) {
    int oldCapacity = elementData.length;
    int newCapacity = oldCapacity + (oldCapacity >> 1); // 1.5 倍
    if (newCapacity - minCapacity < 0)
        newCapacity = minCapacity;
    if (newCapacity - MAX_ARRAY_SIZE > 0)
        newCapacity = hugeCapacity(minCapacity);
    elementData = Arrays.copyOf(elementData, newCapacity); // 数组拷贝
}
```

**关键点：**
- 使用无参构造器时初始为空数组，首次 add 时才创建容量 10 的数组（懒初始化）
- 扩容为 1.5 倍（`oldCapacity + (oldCapacity >> 1)`）
- 扩容需要 `Arrays.copyOf()` 复制数组，O(n) 开销
- **最佳实践**：如果预知元素数量，构造时指定初始容量

### 删除元素

```java
// 按索引删除
public E remove(int index) {
    rangeCheck(index);
    E oldValue = elementData(index);
    int numMoved = size - index - 1;
    if (numMoved > 0)
        System.arraycopy(elementData, index+1, elementData, index, numMoved);
    elementData[--size] = null; // 帮助 GC
    return oldValue;
}

// 注意：for 循环中 remove 的坑
List<String> list = new ArrayList<>(Arrays.asList("a", "b", "b", "c"));

// 错误：普通 for 循环删除会跳过元素
for (int i = 0; i < list.size(); i++) {
    if ("b".equals(list.get(i))) {
        list.remove(i); // 删除后后面的元素前移，i++ 导致跳过
    }
}

// 错误：增强 for 循环中 remove 会 ConcurrentModificationException
for (String s : list) {
    if ("b".equals(s)) list.remove(s); // CME!
}

// 正确方式 1：倒序删除
for (int i = list.size() - 1; i >= 0; i--) {
    if ("b".equals(list.get(i))) list.remove(i);
}

// 正确方式 2：使用 Iterator
Iterator<String> it = list.iterator();
while (it.hasNext()) {
    if ("b".equals(it.next())) it.remove();
}

// 正确方式 3：Java 8+ removeIf
list.removeIf("b"::equals);
```

## ArrayList vs LinkedList

| 特性 | ArrayList | LinkedList |
|------|-----------|------------|
| 底层结构 | 动态数组 | 双向链表 |
| 随机访问 | O(1) | O(n) |
| 头部插入/删除 | O(n)（数组拷贝） | O(1) |
| 尾部插入 | 均摊 O(1) | O(1) |
| 中间插入/删除 | O(n) | O(n)（查找 O(n) + 插入 O(1)） |
| 内存占用 | 较少（连续内存） | 较多（每个节点额外存前后指针，约 40B/节点） |
| CPU 缓存友好度 | 好（连续内存） | 差（分散在堆中） |
| 适用场景 | 绝大多数场景 | 频繁头部插入删除、作为队列 |

**实战建议：95% 的场景用 ArrayList**。即使是中间插入删除，由于 CPU 缓存友好和 System.arraycopy 的高效，ArrayList 在大多数情况下性能优于 LinkedList。LinkedList 的每个节点多消耗约 40 字节（前后指针 + 对象头），在大数据量下内存开销显著。

## HashMap 深度解析

### 底层结构

```java
// Java 8+ HashMap 核心结构
transient Node<K,V>[] table; // 哈希桶数组
transient int size;           // 键值对数量
int threshold;                // 扩容阈值 = capacity * loadFactor
final float loadFactor;       // 负载因子

// 链表节点
static class Node<K,V> {
    final int hash;
    final K key;
    V value;
    Node<K,V> next;
}

// 红黑树节点
static final class TreeNode<K,V> extends LinkedHashMap.Entry<K,V> {
    TreeNode<K,V> parent;
    TreeNode<K,V> left;
    TreeNode<K,V> right;
    TreeNode<K,V> prev;
    boolean red;
}
```

### 核心参数详解

| 参数 | 值 | 说明 |
|------|-----|------|
| DEFAULT_INITIAL_CAPACITY | 16 | 默认初始容量，必须是 2 的幂 |
| MAXIMUM_CAPACITY | 2^30 | 最大容量 |
| DEFAULT_LOAD_FACTOR | 0.75f | 默认负载因子 |
| TREEIFY_THRESHOLD | 8 | 链表转红黑树的阈值 |
| UNTREEIFY_THRESHOLD | 6 | 红黑树退化为链表的阈值 |
| MIN_TREEIFY_CAPACITY | 64 | 树化的最小表容量 |

**为什么树化阈值是 8？**
- 理想状态下，哈希冲突遵循泊松分布（Poisson Distribution）
- 在负载因子 0.75 下，链表长度达到 8 的概率约为 0.00000006（千万分之六）
- 这意味着正常情况下几乎不会触发树化，是一种极端情况下的兜底保护
- 红黑树查找 O(log n) 优于链表 O(n)，但树节点大小约是链表节点的 2 倍

**为什么退化阈值是 6（而不是 8）？**
- 避免在 7~8 之间频繁的链表/树转换（避免性能抖动）
- 中间留了一个缓冲区间

**为什么容量必须是 2 的幂？**
- 桶位置计算：`hash & (n - 1)` 等价于 `hash % n`，但位运算更快
- 当 n 是 2 的幂时，`n - 1` 的二进制全是 1（如 15 = 1111），这样 & 运算能让所有位参与定位，分布更均匀
- 扩容时的优化：元素要么在原位置，要么在 `原位置 + 旧容量`，只需检查多出的一个 bit

### put 流程详解

```java
public V put(K key, V value) {
    return putVal(hash(key), key, value, false, true);
}

// 1. 计算 hash（扰动函数）
static final int hash(Object key) {
    int h;
    // 高16位与低16位异或，让高位也参与桶位计算，减少碰撞
    return (key == null) ? 0 : (h = key.hashCode()) ^ (h >>> 16);
}

// put 的完整流程：
// 1. 如果 table 为空或长度为 0，先 resize() 初始化
// 2. 计算桶位置 i = (n - 1) & hash
// 3. 如果 table[i] == null，直接新建 Node 放入
// 4. 如果 table[i] 不为空：
//    4a. 如果 key 相同（hash 相等且 equals 为 true），覆盖 value
//    4b. 如果是 TreeNode，调用红黑树的 putTreeVal
//    4c. 否则遍历链表，尾插法插入（Java 7 是头插法）
//       - 遍历过程中找到相同 key 则覆盖
//       - 插入后检查链表长度 >= 8 则 treeifyBin（树化）
// 5. 插入成功后 ++size，如果 > threshold 则 resize() 扩容

// Java 7 头插法 vs Java 8 尾插法
// Java 7 头插法在多线程扩容时可能导致链表成环（死循环）
// Java 8 改为尾插法，扩容时保持原有顺序，不会成环
// 但 HashMap 仍然不是线程安全的！多线程应使用 ConcurrentHashMap
```

### get 流程

```java
// get 流程：
// 1. 计算 hash
// 2. 定位桶位置 (n - 1) & hash
// 3. 如果首节点 hash 和 key 都匹配，直接返回
// 4. 如果首节点是 TreeNode，调用红黑树的 getTreeNode
// 5. 否则遍历链表查找
// 6. 找不到返回 null
```

### 扩容机制（resize）详解

```java
// 扩容触发条件：size > threshold（capacity * loadFactor）
// 扩容过程：
// 1. 新容量 = 旧容量 * 2
// 2. 新阈值 = 新容量 * 负载因子
// 3. 创建新数组
// 4. 遍历旧数组，重新分配每个元素：
//    - 如果只有一个节点，直接 hash & (newCap - 1) 定位
//    - 如果是红黑树，split 拆分树
//    - 如果是链表，利用 hash & oldCap 将链表拆分为两部分：
//      - (hash & oldCap) == 0 → 留在原位置
//      - (hash & oldCap) != 0 → 移到 原位置 + oldCap

// 为什么 Java 8 扩容比 Java 7 高效？
// Java 7：重新计算每个 key 的桶位置
// Java 8：只需检查 hash 的一个 bit 就能决定新位置
```

### HashMap 的线程安全问题

```java
// 问题1：多线程 put 数据覆盖
// 线程A和线程B同时put，计算到同一个空桶位置，都认为可以直接放入，导致一个覆盖另一个

// 问题2：put 和 get 并发导致死循环（Java 7 头插法扩容）
// 已在 Java 8 中通过尾插法修复，但仍有其他线程安全问题

// 问题3：size 不准确
// size++ 不是原子操作，多线程下 size 可能不准确

// 解决方案
// 1. Collections.synchronizedMap(new HashMap<>()) —— 全局锁，性能差
// 2. ConcurrentHashMap —— 推荐，高性能并发
// 3. Hashtable —— 全局 synchronized，性能差，不推荐
```

## ConcurrentHashMap 深度解析

### Java 7 实现：分段锁（Segment）

```java
// 结构：Segment[] -> HashEntry[]
// 默认 16 个 Segment，每个 Segment 是一个小的 HashMap
// 理论上支持 16 个线程同时写入（不同 Segment）
// 一旦初始化，Segment 的数量不可改变

// 缺点：
// - 需要两次 hash 定位（先定位 Segment，再定位桶）
// - Segment 数量固定，不够灵活
// - 跨 Segment 操作（如 size()）需要锁所有 Segment
```

### Java 8+ 实现：CAS + synchronized

```java
// 结构：Node[] table（和 HashMap 类似）
// 锁粒度：单个桶（Node），而非一个 Segment

// put 流程：
// 1. 如果桶为空，用 CAS 操作（无锁）直接写入
// 2. 如果桶不为空，对桶的头节点加 synchronized 锁
// 3. 然后遍历链表/红黑树进行插入或更新

// size() 的实现：
// 使用 CounterCell 数组 + baseCount 分散计数（类似 LongAdder）
// 避免了 Java 7 中需要锁所有 Segment 的问题

// 为什么用 synchronized 而不是 ReentrantLock？
// 1. synchronized 经过 JVM 优化（偏向锁、轻量级锁），在低竞争下性能很好
// 2. synchronized 不需要额外的内存开销（不需要存 Lock 对象）
// 3. JVM 可以进一步优化 synchronized（锁消除、锁粗化）
```

### ConcurrentHashMap 不允许 null 的原因

```java
// HashMap 允许 null key 和 null value
// ConcurrentHashMap 不允许，原因是二义性：
// map.get(key) 返回 null 时，无法区分是 key 不存在还是 value 就是 null
// 在非并发环境下可以用 containsKey 判断，但并发下 containsKey 和 get 之间状态可能变化
// 所以 ConcurrentHashMap 干脆禁止 null
```

## LinkedHashMap 与 LRU 缓存

```java
// LinkedHashMap 在 HashMap 基础上维护了双向链表
// 可以按插入顺序或访问顺序遍历

// 实现 LRU（最近最少使用）缓存
public class LRUCache<K, V> extends LinkedHashMap<K, V> {
    private final int capacity;

    public LRUCache(int capacity) {
        // accessOrder = true：按访问顺序排列
        super(capacity, 0.75f, true);
        this.capacity = capacity;
    }

    @Override
    protected boolean removeEldestEntry(Map.Entry<K, V> eldest) {
        return size() > capacity; // 超过容量时移除最久未访问的元素
    }
}

// 使用
LRUCache<String, Integer> cache = new LRUCache<>(3);
cache.put("a", 1);
cache.put("b", 2);
cache.put("c", 3);
cache.get("a");       // 访问 a，a 移到链表尾部
cache.put("d", 4);    // 超容量，移除最久未访问的 b
// cache: {c=3, a=1, d=4}
```

## TreeMap

```java
// 底层是红黑树，按 key 排序
TreeMap<String, Integer> map = new TreeMap<>();
map.put("banana", 2);
map.put("apple", 1);
map.put("cherry", 3);

// 遍历：按 key 字典序
// {apple=1, banana=2, cherry=3}

// 常用的导航方法
map.firstKey();              // "apple"
map.lastKey();               // "cherry"
map.lowerKey("banana");      // "apple"（小于 banana 的最大 key）
map.higherKey("banana");     // "cherry"（大于 banana 的最小 key）
map.subMap("a", "c");        // {apple=1, banana=2}（范围查询）

// 自定义排序
TreeMap<String, Integer> desc = new TreeMap<>(Comparator.reverseOrder());

// key 必须实现 Comparable 或提供 Comparator，否则 ClassCastException
```

## PriorityQueue 优先队列

```java
// 底层是小顶堆（数组实现的完全二叉树）
PriorityQueue<Integer> minHeap = new PriorityQueue<>();

// 大顶堆
PriorityQueue<Integer> maxHeap = new PriorityQueue<>(Comparator.reverseOrder());

// 常用操作
minHeap.offer(3);   // 添加元素 O(log n)
minHeap.offer(1);
minHeap.offer(2);
minHeap.peek();      // 查看堆顶 O(1)，返回 1
minHeap.poll();      // 弹出堆顶 O(log n)，返回 1

// 典型应用：Top K 问题
// 找出数组中最大的 K 个元素：维护大小为 K 的小顶堆
public int[] topK(int[] nums, int k) {
    PriorityQueue<Integer> heap = new PriorityQueue<>(k);
    for (int num : nums) {
        if (heap.size() < k) {
            heap.offer(num);
        } else if (num > heap.peek()) {
            heap.poll();
            heap.offer(num);
        }
    }
    return heap.stream().mapToInt(Integer::intValue).toArray();
}
```

## BlockingQueue 阻塞队列

```java
// 阻塞队列：为空时取阻塞，满了放阻塞
// 是生产者-消费者模式的核心数据结构

// 操作对比
// |        | 抛异常     | 返回特殊值  | 阻塞     | 超时阻塞               |
// | 插入   | add(e)     | offer(e)    | put(e)   | offer(e, time, unit)   |
// | 移除   | remove()   | poll()      | take()   | poll(time, unit)       |
// | 检查   | element()  | peek()      | -        | -                      |

// 常用实现对比
// ArrayBlockingQueue：数组实现，有界，公平/非公平锁
// LinkedBlockingQueue：链表实现，默认无界（Integer.MAX_VALUE），生产消费分离锁
// SynchronousQueue：无容量，每个 put 必须等待 take（适合线程间直接传递）
// PriorityBlockingQueue：无界优先级队列
// DelayQueue：延迟队列，元素到期才能取出

// 生产者-消费者模式
BlockingQueue<Task> queue = new ArrayBlockingQueue<>(100);

// 生产者
new Thread(() -> {
    while (true) {
        queue.put(produceTask()); // 队列满了自动阻塞
    }
}).start();

// 消费者
new Thread(() -> {
    while (true) {
        Task task = queue.take(); // 队列空了自动阻塞
        processTask(task);
    }
}).start();
```

> **交叉引用**：线程池中使用 BlockingQueue 参见 [多线程与并发](./04_多线程与并发.md)

## CopyOnWriteArrayList

```java
// 写时复制：写操作时复制整个数组，读操作不加锁
// 适合读多写少的场景（如事件监听器列表、白名单配置等）

// 源码核心
public boolean add(E e) {
    synchronized (lock) {
        Object[] es = getArray();
        int len = es.length;
        es = Arrays.copyOf(es, len + 1); // 复制新数组
        es[len] = e;
        setArray(es); // 替换引用
        return true;
    }
}

// 读操作无锁
public E get(int index) {
    return elementAt(getArray(), index); // 直接读，不加锁
}

// 注意：
// 1. 每次写操作都要复制数组，大数组写操作代价很高
// 2. 读到的可能是旧数据（弱一致性），但对很多场景可以接受
// 3. 不适合写操作频繁的场景
```

## Collections 工具类常用方法

```java
// 不可修改集合
List<String> unmodifiable = Collections.unmodifiableList(list); // 返回只读视图
// Java 9+
List<String> immutable = List.of("a", "b", "c");           // 真正的不可变集合
List<String> copy = List.copyOf(mutableList);               // 复制为不可变

// 线程安全包装
List<String> syncList = Collections.synchronizedList(new ArrayList<>());
Map<String, Integer> syncMap = Collections.synchronizedMap(new HashMap<>());
// 注意：迭代时仍需手动同步
synchronized (syncList) {
    for (String s : syncList) { /* ... */ }
}

// 单元素集合
List<String> single = Collections.singletonList("only");
Map<String, Integer> singleMap = Collections.singletonMap("key", 1);

// 排序
Collections.sort(list);                           // 自然排序
Collections.sort(list, Comparator.reverseOrder()); // 自定义排序
list.sort(Comparator.comparing(User::getAge));     // Java 8+ List.sort

// 查找
int idx = Collections.binarySearch(sortedList, key); // 二分查找（需已排序）
String max = Collections.max(list);                  // 最大值
String min = Collections.min(list);                  // 最小值

// 其他
Collections.reverse(list);           // 反转
Collections.shuffle(list);           // 随机打乱
Collections.frequency(list, "a");    // 统计出现次数
Collections.disjoint(list1, list2);  // 是否没有共同元素
```

## Comparable vs Comparator

```java
// Comparable：自然排序，在类内部定义（侵入式）
public class Student implements Comparable<Student> {
    private String name;
    private int score;

    @Override
    public int compareTo(Student other) {
        return Integer.compare(this.score, other.score); // 按分数升序
    }
}

// Comparator：外部排序，更灵活（非侵入式）
List<Student> students = getStudents();

// 按分数降序
students.sort(Comparator.comparingInt(Student::getScore).reversed());

// 多级排序：先按分数降序，再按姓名升序
students.sort(Comparator.comparingInt(Student::getScore).reversed()
                        .thenComparing(Student::getName));

// 处理 null
students.sort(Comparator.nullsLast(Comparator.comparingInt(Student::getScore)));
```

## 踩坑指南

### 1. Arrays.asList 的坑
```java
// 返回的是 java.util.Arrays.ArrayList（内部类），不支持增删
List<String> list = Arrays.asList("a", "b", "c");
list.add("d");    // UnsupportedOperationException!
list.set(0, "x"); // 可以修改元素

// 基本类型数组的坑
int[] arr = {1, 2, 3};
List<int[]> wrong = Arrays.asList(arr); // 整个数组作为一个元素！
Integer[] boxed = {1, 2, 3};
List<Integer> right = Arrays.asList(boxed); // 正确

// 安全做法
List<String> safe = new ArrayList<>(Arrays.asList("a", "b", "c"));
// Java 9+
List<String> immutable = List.of("a", "b", "c");
```

### 2. subList 的坑
```java
List<String> original = new ArrayList<>(Arrays.asList("a", "b", "c", "d"));
List<String> sub = original.subList(1, 3); // [b, c]

// sub 是 original 的视图，修改 sub 会影响 original
sub.set(0, "x"); // original 变为 [a, x, c, d]

// 修改 original 后使用 sub 会 ConcurrentModificationException
original.add("e");
sub.get(0); // ConcurrentModificationException!

// 安全做法：创建新的独立 List
List<String> safeSub = new ArrayList<>(original.subList(1, 3));
```

### 3. HashMap 中用可变对象作 key
```java
// 如果 key 对象的字段被修改，hashCode 变化，导致无法 get
List<String> key = new ArrayList<>(Arrays.asList("hello"));
Map<List<String>, String> map = new HashMap<>();
map.put(key, "value");
key.add("world");        // 修改了 key 的内容
map.get(key);            // null！hashCode 变了，找不到了

// 最佳实践：HashMap 的 key 应该是不可变的（String、Integer 等）
```

### 4. fail-fast 与 fail-safe
```java
// fail-fast：在遍历时修改集合会抛 ConcurrentModificationException
// HashMap, ArrayList 等非线程安全集合使用 fail-fast
// 通过 modCount 检测

// fail-safe：在遍历时可以修改集合，不抛异常
// CopyOnWriteArrayList、ConcurrentHashMap 使用 fail-safe
// 原理：遍历的是快照（CopyOnWrite）或弱一致性（ConcurrentHashMap）
```

### 5. Map 的 computeIfAbsent 替代 get + put
```java
// 传统写法（非原子操作，多线程不安全）
Map<String, List<String>> map = new HashMap<>();
if (!map.containsKey("key")) {
    map.put("key", new ArrayList<>());
}
map.get("key").add("value");

// 优雅写法（Java 8+）
map.computeIfAbsent("key", k -> new ArrayList<>()).add("value");

// ConcurrentHashMap 中 computeIfAbsent 是原子的
ConcurrentHashMap<String, List<String>> concurrentMap = new ConcurrentHashMap<>();
concurrentMap.computeIfAbsent("key", k -> new ArrayList<>()).add("value");
```

## 最佳实践

1. **选择正确的集合类型**：随机访问用 ArrayList，频繁头部增删用 ArrayDeque，键值查找用 HashMap，需要排序用 TreeMap
2. **指定初始容量**：`new ArrayList<>(100)`、`new HashMap<>(64, 0.75f)`，减少扩容开销
3. **面向接口编程**：`List<String> list = new ArrayList<>()`，不要用具体类型声明
4. **不可变集合**：如果集合不需要修改，用 `List.of()`（Java 9+）或 `Collections.unmodifiableList()`
5. **HashMap 的 key 用不可变对象**：String、Integer 等，避免 key 被修改后找不到
6. **并发场景用 Concurrent 集合**：ConcurrentHashMap 而非 synchronizedMap，CopyOnWriteArrayList 而非 synchronizedList（读多写少）
7. **避免在 foreach 中修改集合**：使用 Iterator.remove() 或 removeIf()
8. **Java 9+ 优先用工厂方法**：`List.of()`、`Map.of()`、`Set.of()` 创建不可变集合

## 面试高频问题及详细解答

### Q1：HashMap 的底层原理？put/get 流程？
**答**：HashMap 底层是数组+链表+红黑树（Java 8+）。put 时先计算 key 的 hash（高16位异或低16位），再通过 `hash & (n-1)` 定位桶位置。桶为空直接放入；不为空则遍历链表/红黑树，key 相同则覆盖 value，不同则插入。链表长度>=8 且数组>=64 时转红黑树。插入后检查是否需要扩容。get 类似，定位桶后遍历查找。

### Q2：HashMap 为什么线程不安全？
**答**：(1) 多线程 put 可能导致数据覆盖（两个线程同时判断桶为空并写入）(2) Java 7 的头插法扩容可能导致链表成环（死循环）(3) size++ 非原子操作导致计数不准。解决方案：使用 ConcurrentHashMap。

### Q3：HashMap 和 ConcurrentHashMap 的区别？
**答**：(1) HashMap 非线程安全，ConcurrentHashMap 线程安全 (2) HashMap 允许 null key/value，ConcurrentHashMap 不允许（避免并发下的二义性）(3) ConcurrentHashMap Java 7 用分段锁（Segment），Java 8+ 用 CAS+synchronized 锁单个桶 (4) ConcurrentHashMap 的 size() 使用分布式计数（类似 LongAdder）。

### Q4：ArrayList 和 LinkedList 的区别？怎么选？
**答**：ArrayList 底层是动态数组，支持 O(1) 随机访问，尾部增删均摊 O(1)。LinkedList 底层是双向链表，头尾增删 O(1)，但随机访问 O(n)，且每个节点额外占用约 40 字节内存。实际使用中 95% 场景选 ArrayList，因为 CPU 缓存友好度和 arraycopy 的效率使得 ArrayList 综合性能更优。

### Q5：HashMap 的扩容机制？为什么容量必须是 2 的幂？
**答**：当 size 超过 threshold（capacity * loadFactor）时触发扩容，容量翻倍。2 的幂使得 `hash & (n-1)` 等价于 `hash % n`（位运算更快），且保证 hash 的低位全部参与定位，分布更均匀。扩容时 Java 8 只需检查 hash 的一个 bit 就能决定新位置（原位置或原位置+旧容量），比 Java 7 重新计算效率高。

### Q6：HashSet 的底层实现？
**答**：HashSet 内部封装了一个 HashMap，元素作为 key，value 是一个共享的固定 Object 对象（`private static final Object PRESENT = new Object()`）。add() 调用 HashMap.put()，contains() 调用 HashMap.containsKey()。所以 HashSet 的去重依赖 hashCode() 和 equals()。

### Q7：ConcurrentHashMap 在 Java 7 和 Java 8 中的实现有什么不同？
**答**：Java 7 使用分段锁（Segment），默认 16 段，每段是一个独立的 HashMap，最多 16 个线程同时写。Java 8 放弃分段锁，改用 CAS+synchronized：桶为空时 CAS 写入（无锁），桶不空时 synchronized 锁头节点。Java 8 锁粒度更细（单个桶 vs 一个段），并发度更高，且 size() 用分布式计数不需要锁所有段。

### Q8：如何实现一个 LRU 缓存？
**答**：最简单的方式是继承 LinkedHashMap 并开启 accessOrder=true（按访问顺序排序），重写 removeEldestEntry() 在超过容量时返回 true。也可以自己用 HashMap + 双向链表实现：HashMap 存 key->Node 映射实现 O(1) 查找，双向链表维护访问顺序，get/put 时将节点移到链表尾部，容量满时删除链表头部。

### Q9：Collection 和 Collections 的区别？
**答**：`Collection` 是集合框架的根接口，定义了集合的基本操作（add、remove、contains 等）。`Collections` 是工具类，提供静态方法操作集合（sort、shuffle、unmodifiable、synchronized 等）。类似的还有 `Array` vs `Arrays`。

### Q10：如何选择合适的 Map 实现？
**答**：(1) 一般场景用 HashMap (2) 需要线程安全用 ConcurrentHashMap (3) 需要按 key 排序用 TreeMap (4) 需要保持插入顺序用 LinkedHashMap (5) 需要 LRU 功能用 LinkedHashMap(accessOrder=true) (6) key 可以被 GC 回收用 WeakHashMap。选择时考虑：是否并发、是否有序、key 的类型、读写比例。

> **交叉引用**：集合的线程安全与并发场景参见 [多线程与并发](./04_多线程与并发.md)；equals/hashCode 契约参见 [面向对象](./02_面向对象.md)
