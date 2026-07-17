# 数据结构

---

## 一、数组

### 1.1 基础概念

- 连续内存分配，通过下标随机访问，时间复杂度 O(1)
- 插入/删除需要移动元素，时间复杂度 O(n)
- 适合频繁随机访问、写少读多的场景

### 1.2 动态扩容原理

以 Java ArrayList 为例：
1. 默认初始容量为 10
2. 当元素数量超过当前容量时，触发扩容
3. 新容量 = 旧容量 * 1.5（右移一位 `oldCapacity + (oldCapacity >> 1)`）
4. 创建新数组，将旧数组元素拷贝到新数组（`Arrays.copyOf`）
5. 均摊时间复杂度仍为 O(1)

**扩容策略对比**：
| 语言/容器 | 默认初始容量 | 扩容倍数 |
|-----------|-------------|---------|
| Java ArrayList | 10 | 1.5x |
| C++ vector | 0 | 2x (GCC) / 1.5x (MSVC) |
| Go slice | 0 | <1024: 2x; >=1024: 1.25x |
| Python list | 0 | ~1.125x |

### 1.3 前缀和

前缀和用于快速求区间和，将 O(n) 查询优化为 O(1)。

```python
# 一维前缀和
def build_prefix_sum(nums):
    n = len(nums)
    prefix = [0] * (n + 1)
    for i in range(n):
        prefix[i + 1] = prefix[i] + nums[i]
    return prefix

# 查询区间 [l, r] 的和
def range_sum(prefix, l, r):
    return prefix[r + 1] - prefix[l]

# 二维前缀和
def build_2d_prefix(matrix):
    m, n = len(matrix), len(matrix[0])
    prefix = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m):
        for j in range(n):
            prefix[i+1][j+1] = (matrix[i][j]
                + prefix[i][j+1] + prefix[i+1][j] - prefix[i][j])
    return prefix
```

**LeetCode 经典题目**：
- 303 区域和检索（一维前缀和入门）
- 304 二维区域和检索（二维前缀和）
- 560 和为 K 的子数组（前缀和 + 哈希表）
- 974 和可被 K 整除的子数组

### 1.4 差分数组

差分数组用于高效处理区间增减操作，对区间 [l, r] 整体加 val 只需 O(1)。

```python
# 差分数组
def build_diff(nums):
    n = len(nums)
    diff = [0] * n
    diff[0] = nums[0]
    for i in range(1, n):
        diff[i] = nums[i] - nums[i - 1]
    return diff

# 区间 [l, r] 加 val
def range_add(diff, l, r, val):
    diff[l] += val
    if r + 1 < len(diff):
        diff[r + 1] -= val

# 还原原数组
def restore(diff):
    nums = [0] * len(diff)
    nums[0] = diff[0]
    for i in range(1, len(diff)):
        nums[i] = nums[i - 1] + diff[i]
    return nums
```

**LeetCode 经典题目**：
- 1109 航班预订统计
- 1094 拼车
- 370 区间加法（Premium）

---

## 二、链表

### 2.1 单链表

```python
class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next
```

特点：只能单向遍历，插入/删除已知位置 O(1)，查找 O(n)。

### 2.2 双链表

```python
class DoublyListNode:
    def __init__(self, val=0, prev=None, next=None):
        self.val = val
        self.prev = prev
        self.next = next
```

特点：双向遍历，删除节点无需知道前驱。Java LinkedList 即双向链表实现。

### 2.3 循环链表

尾节点的 next 指向头节点。适用于约瑟夫环问题、循环缓冲区。

### 2.4 跳表（Skip List）

跳表是在有序链表基础上增加多级索引，实现 O(log n) 的查找、插入、删除。

**核心思想**：
- 最底层是完整的有序链表
- 每一层是下一层的"快速通道"
- 每个节点以概率 p（通常 1/2）决定是否提升到上一层
- 平均层数为 O(log n)

**Redis 中的跳表实现**（zset 底层结构之一）：
- Redis 使用跳表而非平衡树的原因：
  1. 实现简单，代码容易维护
  2. 范围查询效率高（直接沿链表遍历）
  3. 插入/删除只需修改局部指针，无需全局平衡
  4. 通过调整概率 p 可灵活平衡时间和空间
- Redis 跳表最高 32 层，概率 p = 0.25

```python
import random

class SkipListNode:
    def __init__(self, val, level):
        self.val = val
        self.forward = [None] * (level + 1)

class SkipList:
    MAX_LEVEL = 16
    P = 0.5

    def __init__(self):
        self.header = SkipListNode(-1, self.MAX_LEVEL)
        self.level = 0

    def random_level(self):
        lvl = 0
        while random.random() < self.P and lvl < self.MAX_LEVEL:
            lvl += 1
        return lvl

    def search(self, target):
        current = self.header
        for i in range(self.level, -1, -1):
            while current.forward[i] and current.forward[i].val < target:
                current = current.forward[i]
        current = current.forward[0]
        return current and current.val == target
```

### 2.5 链表经典题型与解题思路

| 题目 | LeetCode | 核心思路 |
|------|----------|---------|
| 反转链表 | 206 | 迭代三指针 / 递归 |
| 判断是否有环 | 141 | 快慢指针 |
| 找环入口 | 142 | 快慢相遇后，head 和相遇点同步走 |
| 合并两个有序链表 | 21 | 双指针归并 |
| 合并 K 个有序链表 | 23 | 分治 / 最小堆 |
| 删除倒数第 N 个节点 | 19 | 快慢指针，快先走 N 步 |
| 链表中点 | 876 | 快慢指针 |
| 回文链表 | 234 | 找中点 + 反转后半部分 + 比较 |
| 两数相加 | 2 | 模拟进位 |
| LRU 缓存 | 146 | 哈希表 + 双向链表 |

---

## 三、栈与队列

### 3.1 栈（LIFO）

后进先出。应用：括号匹配、表达式求值、DFS、函数调用栈、浏览器前进后退。

### 3.2 单调栈

维护栈内元素单调递增或单调递减，用于"下一个更大/更小元素"类问题。

```python
# 下一个更大元素（单调递减栈）
def next_greater_element(nums):
    n = len(nums)
    result = [-1] * n
    stack = []  # 存放下标
    for i in range(n):
        while stack and nums[i] > nums[stack[-1]]:
            idx = stack.pop()
            result[idx] = nums[i]
        stack.append(i)
    return result
```

**LeetCode 经典题目**：
- 496/503 下一个更大元素 I/II
- 739 每日温度
- 84 柱状图中最大的矩形
- 42 接雨水（单调栈解法）
- 85 最大矩形

### 3.3 单调队列

维护窗口内的最大/最小值，常用双端队列实现。

```python
from collections import deque

# 滑动窗口最大值
def max_sliding_window(nums, k):
    dq = deque()  # 存下标，维护递减队列
    result = []
    for i in range(len(nums)):
        # 移除超出窗口的元素
        while dq and dq[0] < i - k + 1:
            dq.popleft()
        # 维护单调递减
        while dq and nums[dq[-1]] < nums[i]:
            dq.pop()
        dq.append(i)
        if i >= k - 1:
            result.append(nums[dq[0]])
    return result
```

**LeetCode 经典题目**：
- 239 滑动窗口最大值
- 862 和至少为 K 的最短子数组

### 3.4 优先队列（堆实现）

基于堆的队列，每次出队优先级最高的元素。Python 中使用 `heapq`（小顶堆），Java 中使用 `PriorityQueue`。

```python
import heapq

# 数据流中第 K 大元素
class KthLargest:
    def __init__(self, k, nums):
        self.k = k
        self.heap = nums
        heapq.heapify(self.heap)
        while len(self.heap) > k:
            heapq.heappop(self.heap)

    def add(self, val):
        heapq.heappush(self.heap, val)
        if len(self.heap) > self.k:
            heapq.heappop(self.heap)
        return self.heap[0]
```

---

## 四、哈希表

### 4.1 哈希函数设计

好的哈希函数应满足：
1. **确定性**：相同输入产生相同输出
2. **均匀性**：输出尽量均匀分布
3. **高效性**：计算速度快

常见哈希函数：
- **除留余数法**：`h(key) = key % m`，m 取质数效果更好
- **乘法哈希**：`h(key) = floor(m * (key * A mod 1))`，A 推荐黄金比例
- **MurmurHash**：非加密哈希，Redis/Kafka 使用
- **CityHash/FarmHash**：Google 开发，针对短字符串优化

### 4.2 冲突解决策略

#### 链地址法（Separate Chaining）
- 每个桶维护一个链表（或红黑树）
- Java HashMap：桶数组 + 链表，链表长度 >= 8 且数组长度 >= 64 时转红黑树
- 负载因子 = 元素数 / 桶数。Java HashMap 默认 0.75，超过则扩容为 2 倍

#### 开放寻址法（Open Addressing）
- 所有元素都存储在数组中，冲突时按规则探测下一个位置
- **线性探测**：`h(key, i) = (h(key) + i) % m`，容易产生聚集
- **二次探测**：`h(key, i) = (h(key) + c1*i + c2*i^2) % m`
- **双重哈希**：`h(key, i) = (h1(key) + i * h2(key)) % m`
- Python dict 使用开放寻址法

#### 布隆过滤器（Bloom Filter）
- 概率型数据结构，用于判断元素"可能存在"或"一定不存在"
- 由位数组 + k 个哈希函数组成
- 特点：有误判（false positive），无漏判（no false negative）
- 应用：缓存穿透防护、垃圾邮件过滤、爬虫 URL 去重

```python
import mmh3
from bitarray import bitarray

class BloomFilter:
    def __init__(self, size, hash_count):
        self.size = size
        self.hash_count = hash_count
        self.bit_array = bitarray(size)
        self.bit_array.setall(0)

    def add(self, item):
        for i in range(self.hash_count):
            idx = mmh3.hash(item, i) % self.size
            self.bit_array[idx] = 1

    def contains(self, item):
        for i in range(self.hash_count):
            idx = mmh3.hash(item, i) % self.size
            if not self.bit_array[idx]:
                return False
        return True  # 可能存在
```

**LeetCode 经典题目**：
- 1 两数之和（哈希表经典入门）
- 49 字母异位词分组
- 128 最长连续序列
- 146 LRU 缓存（哈希表 + 双向链表）
- 706 设计哈希映射

---

## 五、树

### 5.1 二叉树遍历

#### 递归遍历

```python
def preorder(root):    # 前序：根-左-右
    if not root: return []
    return [root.val] + preorder(root.left) + preorder(root.right)

def inorder(root):     # 中序：左-根-右
    if not root: return []
    return inorder(root.left) + [root.val] + inorder(root.right)

def postorder(root):   # 后序：左-右-根
    if not root: return []
    return postorder(root.left) + postorder(root.right) + [root.val]
```

#### 迭代遍历（显式栈）

```python
# 前序迭代
def preorder_iterative(root):
    if not root: return []
    stack, result = [root], []
    while stack:
        node = stack.pop()
        result.append(node.val)
        if node.right: stack.append(node.right)  # 先右后左
        if node.left: stack.append(node.left)
    return result

# 中序迭代
def inorder_iterative(root):
    stack, result, curr = [], [], root
    while curr or stack:
        while curr:
            stack.append(curr)
            curr = curr.left
        curr = stack.pop()
        result.append(curr.val)
        curr = curr.right
    return result
```

#### Morris 遍历（O(1) 空间）

利用叶子节点的空指针建立临时线索，遍历完恢复树结构。

```python
# Morris 中序遍历
def morris_inorder(root):
    result = []
    curr = root
    while curr:
        if not curr.left:
            result.append(curr.val)
            curr = curr.right
        else:
            # 找左子树的最右节点（前驱）
            predecessor = curr.left
            while predecessor.right and predecessor.right != curr:
                predecessor = predecessor.right
            if not predecessor.right:
                # 建立线索
                predecessor.right = curr
                curr = curr.left
            else:
                # 恢复树结构
                predecessor.right = None
                result.append(curr.val)
                curr = curr.right
    return result
```

### 5.2 二叉搜索树（BST）

- 左子树所有节点 < 根 < 右子树所有节点
- 中序遍历结果有序
- 查找/插入/删除平均 O(log n)，最坏退化为链表 O(n)

**删除节点三种情况**：
1. 叶子节点：直接删除
2. 只有一个子节点：用子节点替代
3. 有两个子节点：用中序后继（右子树最小值）或中序前驱替代

### 5.3 AVL 树

- 严格平衡的 BST，任意节点左右子树高度差不超过 1
- 通过四种旋转保持平衡：LL（右旋）、RR（左旋）、LR（先左旋后右旋）、RL（先右旋后左旋）
- 查找效率高，但插入/删除时旋转操作频繁

### 5.4 红黑树

近似平衡的 BST，牺牲一定平衡性换取更少的旋转操作。

**五条性质**：
1. 每个节点是红色或黑色
2. 根节点是黑色
3. 叶子节点（NIL）是黑色
4. 红色节点的子节点必须是黑色（不能连续两个红色）
5. 从任意节点到其所有叶子的路径，经过的黑色节点数相同

**应用**：
- Java TreeMap、TreeSet
- Java HashMap（链表长度 >= 8 转红黑树）
- Linux 内核 CFS 调度器
- C++ std::map、std::set

**AVL 树 vs 红黑树**：
| 特性 | AVL 树 | 红黑树 |
|------|--------|--------|
| 平衡条件 | 严格（高度差 <= 1） | 宽松（最长路径 <= 2倍最短） |
| 查找效率 | 略高 | 略低 |
| 插入/删除 | 旋转多 | 旋转少 |
| 适用场景 | 读多写少 | 读写均衡 |

### 5.5 B 树与 B+ 树

#### B 树
- m 阶 B 树：每个节点最多 m 个子节点，至少 ceil(m/2) 个子节点
- 所有叶子节点在同一层
- 数据存储在所有节点中

#### B+ 树
- 数据只存储在叶子节点
- 叶子节点通过指针串联成有序链表
- 非叶子节点仅存索引

**B+ 树为什么适合做数据库索引**：
1. 非叶子节点不存数据，能容纳更多索引，树高更低，IO 次数更少
2. 叶子节点链表相连，范围查询效率高
3. 查询效率稳定（每次都要查到叶子节点）
4. 适合磁盘顺序读取

**LeetCode 经典题目**：
- 94/144/145 二叉树中序/前序/后序遍历
- 102 二叉树的层序遍历
- 104 二叉树的最大深度
- 226 翻转二叉树
- 236 二叉树的最近公共祖先
- 98 验证二叉搜索树
- 450 删除二叉搜索树中的节点
- 105 从前序与中序遍历序列构造二叉树
- 124 二叉树中的最大路径和
- 297 二叉树的序列化与反序列化

---

## 六、堆

### 6.1 基础概念

堆是完全二叉树，用数组实现：
- 父节点下标 `i`，左子 `2*i+1`，右子 `2*i+2`
- 子节点下标 `i`，父节点 `(i-1)//2`

### 6.2 大顶堆与小顶堆

- **大顶堆**：父节点 >= 子节点，堆顶为最大值
- **小顶堆**：父节点 <= 子节点，堆顶为最小值

### 6.3 堆的核心操作

```python
# 小顶堆实现
class MinHeap:
    def __init__(self):
        self.heap = []

    def push(self, val):
        self.heap.append(val)
        self._sift_up(len(self.heap) - 1)

    def pop(self):
        if not self.heap:
            return None
        self.heap[0], self.heap[-1] = self.heap[-1], self.heap[0]
        val = self.heap.pop()
        if self.heap:
            self._sift_down(0)
        return val

    def _sift_up(self, i):
        while i > 0:
            parent = (i - 1) // 2
            if self.heap[i] < self.heap[parent]:
                self.heap[i], self.heap[parent] = self.heap[parent], self.heap[i]
                i = parent
            else:
                break

    def _sift_down(self, i):
        n = len(self.heap)
        while 2 * i + 1 < n:
            smallest = i
            left, right = 2 * i + 1, 2 * i + 2
            if left < n and self.heap[left] < self.heap[smallest]:
                smallest = left
            if right < n and self.heap[right] < self.heap[smallest]:
                smallest = right
            if smallest == i:
                break
            self.heap[i], self.heap[smallest] = self.heap[smallest], self.heap[i]
            i = smallest
```

### 6.4 堆排序

```python
def heap_sort(arr):
    n = len(arr)
    # 建堆（从最后一个非叶子节点开始）
    for i in range(n // 2 - 1, -1, -1):
        heapify(arr, n, i)
    # 逐个取出堆顶
    for i in range(n - 1, 0, -1):
        arr[0], arr[i] = arr[i], arr[0]
        heapify(arr, i, 0)

def heapify(arr, n, i):
    largest = i
    left, right = 2 * i + 1, 2 * i + 2
    if left < n and arr[left] > arr[largest]:
        largest = left
    if right < n and arr[right] > arr[largest]:
        largest = right
    if largest != i:
        arr[i], arr[largest] = arr[largest], arr[i]
        heapify(arr, n, largest)
```

### 6.5 Top-K 问题

求最大的 K 个元素：维护大小为 K 的**小顶堆**。
求最小的 K 个元素：维护大小为 K 的**大顶堆**。

```python
import heapq

def top_k_largest(nums, k):
    return heapq.nlargest(k, nums)

# 手动实现：维护小顶堆
def top_k_largest_manual(nums, k):
    heap = nums[:k]
    heapq.heapify(heap)
    for num in nums[k:]:
        if num > heap[0]:
            heapq.heapreplace(heap, num)
    return heap
```

**LeetCode 经典题目**：
- 215 数组中的第 K 个最大元素
- 347 前 K 个高频元素
- 295 数据流的中位数（大顶堆 + 小顶堆）
- 23 合并 K 个升序链表（最小堆）
- 703 数据流中的第 K 大元素

---

## 七、图

### 7.1 存储方式

#### 邻接矩阵
```python
# n 个节点的邻接矩阵
graph = [[0] * n for _ in range(n)]
graph[u][v] = weight  # 有向图
graph[u][v] = graph[v][u] = weight  # 无向图
```
- 空间 O(V^2)，适合稠密图
- 判断边是否存在 O(1)

#### 邻接表
```python
from collections import defaultdict
graph = defaultdict(list)
graph[u].append((v, weight))
```
- 空间 O(V+E)，适合稀疏图
- 遍历邻居高效

### 7.2 BFS 与 DFS

```python
from collections import deque

def bfs(graph, start):
    visited = {start}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for neighbor in graph[node]:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)

def dfs(graph, start, visited=None):
    if visited is None:
        visited = set()
    visited.add(start)
    for neighbor in graph[start]:
        if neighbor not in visited:
            dfs(graph, neighbor, visited)
```

### 7.3 最短路径算法

#### Dijkstra 算法（单源最短路，非负权）

```python
import heapq

def dijkstra(graph, start, n):
    dist = [float('inf')] * n
    dist[start] = 0
    heap = [(0, start)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist[u]:
            continue
        for v, w in graph[u]:
            if dist[u] + w < dist[v]:
                dist[v] = dist[u] + w
                heapq.heappush(heap, (dist[v], v))
    return dist
```

时间复杂度：O(E log V)（优先队列优化）

#### Floyd 算法（多源最短路）

```python
def floyd(graph, n):
    dist = [[float('inf')] * n for _ in range(n)]
    for i in range(n):
        dist[i][i] = 0
    for u in range(n):
        for v, w in graph[u]:
            dist[u][v] = w
    for k in range(n):
        for i in range(n):
            for j in range(n):
                dist[i][j] = min(dist[i][j], dist[i][k] + dist[k][j])
    return dist
```

时间复杂度：O(V^3)

#### Bellman-Ford 算法（单源最短路，可处理负权）

```python
def bellman_ford(edges, n, start):
    dist = [float('inf')] * n
    dist[start] = 0
    for _ in range(n - 1):
        for u, v, w in edges:
            if dist[u] + w < dist[v]:
                dist[v] = dist[u] + w
    # 检测负环
    for u, v, w in edges:
        if dist[u] + w < dist[v]:
            return None  # 存在负环
    return dist
```

时间复杂度：O(V * E)

### 7.4 最小生成树

#### Prim 算法（从顶点出发）

```python
import heapq

def prim(graph, n):
    visited = [False] * n
    heap = [(0, 0)]  # (权重, 节点)
    total_weight = 0
    edges_used = 0
    while heap and edges_used < n:
        w, u = heapq.heappop(heap)
        if visited[u]:
            continue
        visited[u] = True
        total_weight += w
        edges_used += 1
        for v, weight in graph[u]:
            if not visited[v]:
                heapq.heappush(heap, (weight, v))
    return total_weight
```

#### Kruskal 算法（从边出发，配合并查集）

```python
def kruskal(edges, n):
    edges.sort(key=lambda x: x[2])  # 按权重排序
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        px, py = find(x), find(y)
        if px == py:
            return False
        parent[px] = py
        return True

    total_weight = 0
    for u, v, w in edges:
        if union(u, v):
            total_weight += w
    return total_weight
```

### 7.5 拓扑排序

用于有向无环图（DAG）的线性排序。应用：任务依赖、编译顺序、课程安排。

```python
from collections import deque

def topological_sort(graph, n):
    indegree = [0] * n
    for u in range(n):
        for v in graph[u]:
            indegree[v] += 1
    queue = deque([i for i in range(n) if indegree[i] == 0])
    order = []
    while queue:
        u = queue.popleft()
        order.append(u)
        for v in graph[u]:
            indegree[v] -= 1
            if indegree[v] == 0:
                queue.append(v)
    return order if len(order) == n else []  # 空表示有环
```

**LeetCode 经典题目**：
- 200 岛屿数量（BFS/DFS）
- 207/210 课程表 I/II（拓扑排序）
- 743 网络延迟时间（Dijkstra）
- 787 K 站中转内最便宜的航班（Bellman-Ford）
- 1584 连接所有点的最小费用（Prim/Kruskal）

---

## 八、并查集

### 8.1 基本实现

```python
class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))
        self.rank = [0] * n
        self.count = n  # 连通分量数

    def find(self, x):
        """路径压缩"""
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x, y):
        """按秩合并"""
        px, py = self.find(x), self.find(y)
        if px == py:
            return False
        if self.rank[px] < self.rank[py]:
            px, py = py, px
        self.parent[py] = px
        if self.rank[px] == self.rank[py]:
            self.rank[px] += 1
        self.count -= 1
        return True

    def connected(self, x, y):
        return self.find(x) == self.find(y)
```

### 8.2 优化策略

- **路径压缩**：find 时让节点直接指向根，近似 O(1)
- **按秩合并**：矮树合并到高树下，保持树高平衡
- 两者结合后，单次操作近似 O(alpha(n))，alpha 为反阿克曼函数，实际可视为常数

**LeetCode 经典题目**：
- 547 省份数量
- 684 冗余连接
- 200 岛屿数量（并查集解法）
- 128 最长连续序列（并查集解法）
- 399 除法求值

---

## 九、字典树（Trie）

### 9.1 基本实现

```python
class TrieNode:
    def __init__(self):
        self.children = {}
        self.is_end = False

class Trie:
    def __init__(self):
        self.root = TrieNode()

    def insert(self, word):
        node = self.root
        for ch in word:
            if ch not in node.children:
                node.children[ch] = TrieNode()
            node = node.children[ch]
        node.is_end = True

    def search(self, word):
        node = self._find(word)
        return node is not None and node.is_end

    def starts_with(self, prefix):
        return self._find(prefix) is not None

    def _find(self, prefix):
        node = self.root
        for ch in prefix:
            if ch not in node.children:
                return None
            node = node.children[ch]
        return node
```

### 9.2 应用场景

- 自动补全（搜索引擎提示）
- 拼写检查
- IP 路由（最长前缀匹配）
- 词频统计
- 字符串去重

**LeetCode 经典题目**：
- 208 实现 Trie
- 211 添加与搜索单词
- 212 单词搜索 II（Trie + 回溯）
- 648 单词替换
- 720 词典中最长的单词

---

## 十、线段树与树状数组

### 10.1 树状数组（Binary Indexed Tree / Fenwick Tree）

支持单点更新和前缀查询，时间复杂度均为 O(log n)。

```python
class BIT:
    def __init__(self, n):
        self.n = n
        self.tree = [0] * (n + 1)

    def update(self, i, delta):
        """单点更新：下标 i 加 delta"""
        while i <= self.n:
            self.tree[i] += delta
            i += i & (-i)  # lowbit

    def query(self, i):
        """前缀查询：求 [1, i] 的和"""
        s = 0
        while i > 0:
            s += self.tree[i]
            i -= i & (-i)
        return s

    def range_query(self, l, r):
        """区间查询：求 [l, r] 的和"""
        return self.query(r) - self.query(l - 1)
```

### 10.2 线段树（Segment Tree）

支持区间查询和区间更新，时间复杂度均为 O(log n)。

```python
class SegmentTree:
    def __init__(self, nums):
        self.n = len(nums)
        self.tree = [0] * (4 * self.n)
        self.lazy = [0] * (4 * self.n)
        self._build(nums, 1, 0, self.n - 1)

    def _build(self, nums, node, start, end):
        if start == end:
            self.tree[node] = nums[start]
            return
        mid = (start + end) // 2
        self._build(nums, 2 * node, start, mid)
        self._build(nums, 2 * node + 1, mid + 1, end)
        self.tree[node] = self.tree[2 * node] + self.tree[2 * node + 1]

    def _push_down(self, node, start, end):
        if self.lazy[node]:
            mid = (start + end) // 2
            self.tree[2*node] += self.lazy[node] * (mid - start + 1)
            self.tree[2*node+1] += self.lazy[node] * (end - mid)
            self.lazy[2*node] += self.lazy[node]
            self.lazy[2*node+1] += self.lazy[node]
            self.lazy[node] = 0

    def update_range(self, node, start, end, l, r, val):
        if l <= start and end <= r:
            self.tree[node] += val * (end - start + 1)
            self.lazy[node] += val
            return
        self._push_down(node, start, end)
        mid = (start + end) // 2
        if l <= mid:
            self.update_range(2*node, start, mid, l, r, val)
        if r > mid:
            self.update_range(2*node+1, mid+1, end, l, r, val)
        self.tree[node] = self.tree[2*node] + self.tree[2*node+1]

    def query_range(self, node, start, end, l, r):
        if l <= start and end <= r:
            return self.tree[node]
        self._push_down(node, start, end)
        mid = (start + end) // 2
        result = 0
        if l <= mid:
            result += self.query_range(2*node, start, mid, l, r)
        if r > mid:
            result += self.query_range(2*node+1, mid+1, end, l, r)
        return result
```

### 10.3 对比

| 特性 | 树状数组 | 线段树 |
|------|---------|-------|
| 实现复杂度 | 简单 | 较复杂 |
| 空间 | O(n) | O(4n) |
| 单点更新 + 区间查询 | 支持 | 支持 |
| 区间更新 + 区间查询 | 需要技巧 | 原生支持（lazy） |
| 适用范围 | 求和、最值（有限） | 任意可合并操作 |

**LeetCode 经典题目**：
- 307 区域和检索 - 数组可修改
- 315 计算右侧小于当前元素的个数
- 327 区间和的个数
- 218 天际线问题

---

## 面试高频问题（10 道精选）

### 1. 数组和链表的区别？各自适合什么场景？

**数组**：连续内存，支持随机访问 O(1)，插入/删除 O(n)。适合读多写少、需要随机访问的场景。
**链表**：非连续内存，插入/删除已知位置 O(1)，查找 O(n)。适合频繁插入/删除、不需要随机访问的场景。

### 2. HashMap 的底层原理？为什么链表长度 >= 8 转红黑树？

HashMap = 数组 + 链表/红黑树。put 时通过 `hash & (n-1)` 定位桶位置。链表长度达到 8 时，查找从 O(n) 退化为 O(8)，而红黑树为 O(log 8) = O(3)，提升明显。同时要求数组长度 >= 64，否则优先扩容。阈值 8 基于泊松分布计算，正常情况下链表长度达到 8 的概率极低（约千万分之六）。

### 3. 红黑树和 AVL 树的区别？为什么 Java 选择红黑树？

AVL 严格平衡（高度差 <= 1），查找效率略高但插入/删除旋转多。红黑树近似平衡（最长路径不超过最短的 2 倍），旋转少。Java 集合框架读写频繁，红黑树在插入/删除性能上更优。

### 4. B+ 树为什么适合做数据库索引？

- 非叶子节点不存数据，每个磁盘块可容纳更多索引，树更矮，IO 次数少
- 叶子节点链表串联，范围查询只需顺序遍历
- 所有查询都到叶子，查询效率稳定
- 适合磁盘的顺序读取模式

### 5. 堆和 BST 的区别？Top-K 问题用哪种？

堆是完全二叉树，只保证父子关系；BST 保证左 < 根 < 右。Top-K 用小顶堆（求最大 K 个），维护大小为 K 的堆，时间 O(n log K)，空间 O(K)。

### 6. 并查集的路径压缩和按秩合并分别起什么作用？

路径压缩：find 时让节点直接指向根，缩短后续查找路径。按秩合并：矮树接到高树下，防止树退化为链表。两者结合使单次操作近乎 O(1)。

### 7. 什么是布隆过滤器？为什么会有误判？

布隆过滤器用位数组 + 多个哈希函数判断元素存在性。多个不同元素的哈希位置可能重叠，导致不存在的元素被误判为存在（false positive），但存在的元素不会被误判为不存在（no false negative）。

### 8. 图的 BFS 和 DFS 分别适合解决什么问题？

BFS 适合最短路径（无权图）、层序遍历、多源扩散。DFS 适合连通性判断、路径搜索、拓扑排序、强连通分量。

### 9. 跳表和红黑树的对比？Redis 为什么用跳表？

两者时间复杂度相当（O(log n)），但跳表实现更简单、范围查询更高效（沿链表遍历）、并发友好（只需局部锁）。Redis zset 需要频繁的范围查询和排名操作，跳表更合适。

### 10. 线段树和树状数组的使用场景？

树状数组：实现简单，适合单点更新 + 区间求和。线段树：功能更强，支持区间更新 + 区间查询，适合更复杂的区间操作（最值、区间赋值等）。如果树状数组能解决的问题，优先用树状数组（代码短、常数小）。
