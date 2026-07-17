# 常见算法

---

## 一、排序算法

### 1.1 十大排序算法完整对比

| 算法 | 平均时间 | 最好时间 | 最坏时间 | 空间 | 稳定性 | 特点 |
|------|---------|---------|---------|------|--------|------|
| 冒泡排序 | O(n^2) | O(n) | O(n^2) | O(1) | 稳定 | 简单，可提前终止 |
| 选择排序 | O(n^2) | O(n^2) | O(n^2) | O(1) | 不稳定 | 交换次数最少 |
| 插入排序 | O(n^2) | O(n) | O(n^2) | O(1) | 稳定 | 小规模数据效率高 |
| 希尔排序 | O(n^1.3) | O(n) | O(n^2) | O(1) | 不稳定 | 插入排序改进版 |
| 快速排序 | O(n log n) | O(n log n) | O(n^2) | O(log n) | 不稳定 | 实践中最快 |
| 归并排序 | O(n log n) | O(n log n) | O(n log n) | O(n) | 稳定 | 稳定的 O(n log n) |
| 堆排序 | O(n log n) | O(n log n) | O(n log n) | O(1) | 不稳定 | 原地排序 |
| 计数排序 | O(n+k) | O(n+k) | O(n+k) | O(k) | 稳定 | 非比较，整数范围有限 |
| 桶排序 | O(n+k) | O(n) | O(n^2) | O(n+k) | 稳定 | 数据均匀分布时高效 |
| 基数排序 | O(d*(n+k)) | O(d*(n+k)) | O(d*(n+k)) | O(n+k) | 稳定 | 非比较，按位排序 |

> 注：k 为数据范围，d 为最大位数

### 1.2 快速排序（三种分区方案）

#### 方案一：Lomuto 分区（单指针）

```python
def lomuto_partition(arr, lo, hi):
    pivot = arr[hi]  # 取最后一个元素
    i = lo - 1
    for j in range(lo, hi):
        if arr[j] <= pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]
    arr[i + 1], arr[hi] = arr[hi], arr[i + 1]
    return i + 1
```

特点：实现简单，但对已排序数组退化为 O(n^2)。

#### 方案二：Hoare 分区（双指针）

```python
def hoare_partition(arr, lo, hi):
    pivot = arr[lo]
    i, j = lo - 1, hi + 1
    while True:
        i += 1
        while arr[i] < pivot:
            i += 1
        j -= 1
        while arr[j] > pivot:
            j -= 1
        if i >= j:
            return j
        arr[i], arr[j] = arr[j], arr[i]
```

特点：比 Lomuto 交换次数少约 3 倍，实际性能更好。

#### 方案三：三路分区（Dutch National Flag）

```python
def three_way_partition(arr, lo, hi):
    """将数组分为 <pivot, ==pivot, >pivot 三部分"""
    pivot = arr[lo]
    lt, i, gt = lo, lo, hi
    while i <= gt:
        if arr[i] < pivot:
            arr[lt], arr[i] = arr[i], arr[lt]
            lt += 1
            i += 1
        elif arr[i] > pivot:
            arr[i], arr[gt] = arr[gt], arr[i]
            gt -= 1
        else:
            i += 1
    return lt, gt

def quick_sort_3way(arr, lo, hi):
    if lo >= hi:
        return
    lt, gt = three_way_partition(arr, lo, hi)
    quick_sort_3way(arr, lo, lt - 1)
    quick_sort_3way(arr, gt + 1, hi)
```

特点：对大量重复元素的数组效率极高，将等于 pivot 的元素排除在递归之外。

#### 快排优化策略

1. **随机选择 pivot**：避免有序数组的最坏情况
2. **三数取中**：取 lo/mid/hi 的中位数作为 pivot
3. **小区间改用插入排序**：区间 <= 16 时切换
4. **尾递归优化**：对较长的分区用迭代代替递归

```python
import random

def quick_sort_optimized(arr, lo, hi):
    while lo < hi:
        if hi - lo < 16:
            insertion_sort(arr, lo, hi)
            return
        # 随机 pivot
        rand_idx = random.randint(lo, hi)
        arr[lo], arr[rand_idx] = arr[rand_idx], arr[lo]
        pivot_pos = hoare_partition(arr, lo, hi)
        # 尾递归优化：只递归较短的一边
        if pivot_pos - lo < hi - pivot_pos:
            quick_sort_optimized(arr, lo, pivot_pos)
            lo = pivot_pos + 1
        else:
            quick_sort_optimized(arr, pivot_pos + 1, hi)
            hi = pivot_pos
```

### 1.3 归并排序

```python
def merge_sort(arr):
    if len(arr) <= 1:
        return arr
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    return merge(left, right)

def merge(left, right):
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:  # <= 保证稳定性
            result.append(left[i])
            i += 1
        else:
            result.append(right[j])
            j += 1
    result.extend(left[i:])
    result.extend(right[j:])
    return result
```

**归并排序的应用**：
- 求逆序对（归并过程中统计跨越左右半部分的逆序对）
- 外部排序（大文件排序，内存放不下时分块排序再归并）
- 链表排序（链表归并排序不需要额外空间，比快排更适合）

### 1.4 堆排序

```python
def heap_sort(arr):
    n = len(arr)
    # 建堆：从最后一个非叶子节点开始下沉
    for i in range(n // 2 - 1, -1, -1):
        sift_down(arr, n, i)
    # 排序：逐个取出堆顶放到末尾
    for i in range(n - 1, 0, -1):
        arr[0], arr[i] = arr[i], arr[0]
        sift_down(arr, i, 0)

def sift_down(arr, n, i):
    largest = i
    left, right = 2 * i + 1, 2 * i + 2
    if left < n and arr[left] > arr[largest]:
        largest = left
    if right < n and arr[right] > arr[largest]:
        largest = right
    if largest != i:
        arr[i], arr[largest] = arr[largest], arr[i]
        sift_down(arr, n, largest)
```

**堆排序特点**：时间 O(n log n)，空间 O(1)，但不稳定且缓存不友好（跳跃访问数组）。

**LeetCode 经典题目**：
- 912 排序数组（练习各种排序）
- 75 颜色分类（三路分区/荷兰国旗）
- 148 排序链表（归并排序）
- 剑指 Offer 51 数组中的逆序对（归并排序应用）
- 315 计算右侧小于当前元素的个数

---

## 二、二分查找

### 2.1 标准模板

```python
def binary_search(nums, target):
    left, right = 0, len(nums) - 1
    while left <= right:
        mid = left + (right - left) // 2
        if nums[mid] == target:
            return mid
        elif nums[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return -1
```

### 2.2 左边界二分（第一个 >= target 的位置）

```python
def lower_bound(nums, target):
    left, right = 0, len(nums)
    while left < right:
        mid = left + (right - left) // 2
        if nums[mid] < target:
            left = mid + 1
        else:
            right = mid
    return left
```

### 2.3 右边界二分（最后一个 <= target 的位置）

```python
def upper_bound(nums, target):
    left, right = 0, len(nums)
    while left < right:
        mid = left + (right - left) // 2
        if nums[mid] <= target:
            left = mid + 1
        else:
            right = mid
    return left - 1
```

### 2.4 旋转排序数组查找

```python
def search_rotated(nums, target):
    left, right = 0, len(nums) - 1
    while left <= right:
        mid = left + (right - left) // 2
        if nums[mid] == target:
            return mid
        # 左半部分有序
        if nums[left] <= nums[mid]:
            if nums[left] <= target < nums[mid]:
                right = mid - 1
            else:
                left = mid + 1
        # 右半部分有序
        else:
            if nums[mid] < target <= nums[right]:
                left = mid + 1
            else:
                right = mid - 1
    return -1
```

### 2.5 寻找峰值

```python
def find_peak(nums):
    left, right = 0, len(nums) - 1
    while left < right:
        mid = left + (right - left) // 2
        if nums[mid] > nums[mid + 1]:
            right = mid  # 峰值在左侧（含 mid）
        else:
            left = mid + 1  # 峰值在右侧
    return left
```

### 2.6 二分查找总结

**三个关键点**：
1. 搜索区间：`[left, right]` 还是 `[left, right)`
2. 循环条件：`left <= right` 还是 `left < right`
3. 更新规则：`left = mid + 1` / `right = mid - 1` 还是 `right = mid`

**适用条件**：具有单调性或二段性的问题（不一定要求严格有序）。

**LeetCode 经典题目**：
- 704 二分查找（标准模板）
- 34 在排序数组中查找元素的第一个和最后一个位置
- 33 搜索旋转排序数组
- 153/154 寻找旋转排序数组中的最小值 I/II
- 162 寻找峰值
- 4 寻找两个正序数组的中位数
- 69 x 的平方根

---

## 三、双指针

### 3.1 快慢指针

```python
# 判断链表是否有环
def has_cycle(head):
    slow = fast = head
    while fast and fast.next:
        slow = slow.next
        fast = fast.next.next
        if slow == fast:
            return True
    return False

# 找链表环的入口
def detect_cycle(head):
    slow = fast = head
    while fast and fast.next:
        slow = slow.next
        fast = fast.next.next
        if slow == fast:
            # 相遇后，一个从 head 出发
            ptr = head
            while ptr != slow:
                ptr = ptr.next
                slow = slow.next
            return ptr
    return None

# 找链表中点
def find_middle(head):
    slow = fast = head
    while fast and fast.next:
        slow = slow.next
        fast = fast.next.next
    return slow
```

### 3.2 左右指针

```python
# 两数之和（有序数组）
def two_sum_sorted(nums, target):
    left, right = 0, len(nums) - 1
    while left < right:
        s = nums[left] + nums[right]
        if s == target:
            return [left, right]
        elif s < target:
            left += 1
        else:
            right -= 1
    return []

# 三数之和
def three_sum(nums):
    nums.sort()
    result = []
    for i in range(len(nums) - 2):
        if i > 0 and nums[i] == nums[i-1]:
            continue  # 去重
        left, right = i + 1, len(nums) - 1
        while left < right:
            s = nums[i] + nums[left] + nums[right]
            if s == 0:
                result.append([nums[i], nums[left], nums[right]])
                while left < right and nums[left] == nums[left+1]:
                    left += 1
                while left < right and nums[right] == nums[right-1]:
                    right -= 1
                left += 1
                right -= 1
            elif s < 0:
                left += 1
            else:
                right -= 1
    return result

# 盛最多水的容器
def max_area(height):
    left, right = 0, len(height) - 1
    result = 0
    while left < right:
        area = min(height[left], height[right]) * (right - left)
        result = max(result, area)
        if height[left] < height[right]:
            left += 1
        else:
            right -= 1
    return result
```

### 3.3 滑动窗口模板

```python
# 通用滑动窗口模板
def sliding_window(s, t):
    from collections import Counter
    need = Counter(t)
    window = {}
    left = 0
    valid = 0          # 满足条件的字符数
    start, min_len = 0, float('inf')

    for right in range(len(s)):
        # 1. 扩大窗口
        c = s[right]
        if c in need:
            window[c] = window.get(c, 0) + 1
            if window[c] == need[c]:
                valid += 1

        # 2. 收缩窗口
        while valid == len(need):
            # 更新答案
            if right - left + 1 < min_len:
                start = left
                min_len = right - left + 1
            d = s[left]
            left += 1
            if d in need:
                if window[d] == need[d]:
                    valid -= 1
                window[d] -= 1

    return s[start:start+min_len] if min_len != float('inf') else ""

# 无重复字符的最长子串
def length_of_longest_substring(s):
    char_set = set()
    left = 0
    result = 0
    for right in range(len(s)):
        while s[right] in char_set:
            char_set.remove(s[left])
            left += 1
        char_set.add(s[right])
        result = max(result, right - left + 1)
    return result
```

**LeetCode 经典题目**：
- 76 最小覆盖子串
- 3 无重复字符的最长子串
- 438 找到字符串中所有字母异位词
- 567 字符串的排列
- 209 长度最小的子数组
- 11 盛最多水的容器
- 15 三数之和
- 42 接雨水（双指针解法）

---

## 四、动态规划（DP）

### 4.1 解题框架

1. **定义状态**：dp 数组的含义
2. **状态转移方程**：dp[i] 与 dp[i-1] 等的关系
3. **初始条件**：base case
4. **计算顺序**：自底向上 or 自顶向下（记忆化搜索）
5. **空间优化**：滚动数组

### 4.2 背包问题系列

#### 0-1 背包

每件物品只能选一次。

```python
# 二维 DP
def knapsack_01(weights, values, capacity):
    n = len(weights)
    dp = [[0] * (capacity + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for w in range(capacity + 1):
            dp[i][w] = dp[i-1][w]  # 不选第 i 件
            if w >= weights[i-1]:
                dp[i][w] = max(dp[i][w],
                    dp[i-1][w - weights[i-1]] + values[i-1])  # 选第 i 件
    return dp[n][capacity]

# 一维优化（逆序遍历容量）
def knapsack_01_optimized(weights, values, capacity):
    dp = [0] * (capacity + 1)
    for i in range(len(weights)):
        for w in range(capacity, weights[i] - 1, -1):  # 逆序!
            dp[w] = max(dp[w], dp[w - weights[i]] + values[i])
    return dp[capacity]
```

#### 完全背包

每件物品可以选无限次。

```python
# 与 0-1 背包区别：正序遍历容量
def knapsack_complete(weights, values, capacity):
    dp = [0] * (capacity + 1)
    for i in range(len(weights)):
        for w in range(weights[i], capacity + 1):  # 正序!
            dp[w] = max(dp[w], dp[w - weights[i]] + values[i])
    return dp[capacity]
```

#### 多重背包

每件物品有数量限制。

```python
# 二进制优化
def knapsack_multiple(weights, values, counts, capacity):
    dp = [0] * (capacity + 1)
    for i in range(len(weights)):
        # 二进制拆分
        num = counts[i]
        k = 1
        while k <= num:
            for w in range(capacity, k * weights[i] - 1, -1):
                dp[w] = max(dp[w], dp[w - k * weights[i]] + k * values[i])
            num -= k
            k *= 2
        if num > 0:
            for w in range(capacity, num * weights[i] - 1, -1):
                dp[w] = max(dp[w], dp[w - num * weights[i]] + num * values[i])
    return dp[capacity]
```

**背包问题判断口诀**：
- 求最大/最小值 -> `dp[w] = max/min(...)`
- 求方案数 -> `dp[w] += dp[w - weight]`
- 求能否凑到 -> `dp[w] = dp[w] or dp[w - weight]`

### 4.3 区间 DP

```python
# 戳气球 (LeetCode 312)
def max_coins(nums):
    nums = [1] + nums + [1]
    n = len(nums)
    dp = [[0] * n for _ in range(n)]
    for length in range(2, n):       # 区间长度
        for i in range(0, n - length):  # 区间起点
            j = i + length              # 区间终点
            for k in range(i + 1, j):   # 最后一个戳破的气球
                dp[i][j] = max(dp[i][j],
                    dp[i][k] + dp[k][j] + nums[i] * nums[k] * nums[j])
    return dp[0][n - 1]
```

### 4.4 状态压缩 DP

用二进制位表示集合状态，适用于元素数量 <= 20 的问题。

```python
# 旅行商问题 (TSP)
def tsp(dist, n):
    INF = float('inf')
    dp = [[INF] * n for _ in range(1 << n)]
    dp[1][0] = 0  # 从城市 0 出发
    for mask in range(1, 1 << n):
        for u in range(n):
            if not (mask & (1 << u)):
                continue
            for v in range(n):
                if mask & (1 << v):
                    continue
                new_mask = mask | (1 << v)
                dp[new_mask][v] = min(dp[new_mask][v],
                    dp[mask][u] + dist[u][v])
    full_mask = (1 << n) - 1
    return min(dp[full_mask][u] + dist[u][0] for u in range(n))
```

### 4.5 树形 DP

```python
# 打家劫舍 III (LeetCode 337) - 树上的 DP
def rob(root):
    def dfs(node):
        if not node:
            return (0, 0)  # (选当前节点, 不选当前节点)
        left = dfs(node.left)
        right = dfs(node.right)
        # 选当前节点：子节点不能选
        rob_current = node.val + left[1] + right[1]
        # 不选当前节点：子节点可选可不选
        not_rob = max(left) + max(right)
        return (rob_current, not_rob)
    return max(dfs(root))
```

### 4.6 数位 DP

统计满足某些条件的数字个数，按位枚举。

```python
# 统计 [1, n] 中数字 1 出现的次数 (LeetCode 233)
def count_digit_one(n):
    if n <= 0:
        return 0
    s = str(n)
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def dp(pos, cnt, is_limit, is_num):
        if pos == len(s):
            return cnt if is_num else 0
        result = 0
        if not is_num:
            result += dp(pos + 1, cnt, False, False)  # 跳过当前位
        lo = 0 if is_num else 1
        hi = int(s[pos]) if is_limit else 9
        for d in range(lo, hi + 1):
            result += dp(pos + 1,
                cnt + (1 if d == 1 else 0),
                is_limit and d == hi,
                True)
        return result

    return dp(0, 0, True, False)
```

**LeetCode 经典题目**：
- 70 爬楼梯
- 322 零钱兑换（完全背包）
- 416 分割等和子集（0-1 背包）
- 300 最长递增子序列
- 1143 最长公共子序列
- 72 编辑距离
- 312 戳气球（区间 DP）
- 337 打家劫舍 III（树形 DP）
- 198/213 打家劫舍 I/II
- 121/122/123 买卖股票系列

---

## 五、贪心算法

### 5.1 核心思想

每一步都选择当前最优解，期望得到全局最优。适用条件：贪心选择性 + 最优子结构。

### 5.2 活动选择问题

```python
# 选择最多的互不重叠活动
def activity_selection(activities):
    # 按结束时间排序
    activities.sort(key=lambda x: x[1])
    result = [activities[0]]
    for i in range(1, len(activities)):
        if activities[i][0] >= result[-1][1]:
            result.append(activities[i])
    return result
```

### 5.3 区间调度

```python
# 无重叠区间的最大数量 (LeetCode 435 变体)
def erase_overlap_intervals(intervals):
    intervals.sort(key=lambda x: x[1])
    count = 1  # 至少保留一个
    end = intervals[0][1]
    for i in range(1, len(intervals)):
        if intervals[i][0] >= end:
            count += 1
            end = intervals[i][1]
    return len(intervals) - count  # 需要移除的数量

# 用最少数量的箭引爆气球 (LeetCode 452)
def find_min_arrows(points):
    points.sort(key=lambda x: x[1])
    arrows = 1
    end = points[0][1]
    for s, e in points[1:]:
        if s > end:
            arrows += 1
            end = e
    return arrows
```

### 5.4 Huffman 编码

```python
import heapq

def huffman_encoding(freq):
    """根据字符频率构建 Huffman 树"""
    heap = [[f, [ch, ""]] for ch, f in freq.items()]
    heapq.heapify(heap)
    while len(heap) > 1:
        lo = heapq.heappop(heap)
        hi = heapq.heappop(heap)
        for pair in lo[1:]:
            pair[1] = '0' + pair[1]
        for pair in hi[1:]:
            pair[1] = '1' + pair[1]
        heapq.heappush(heap, [lo[0] + hi[0]] + lo[1:] + hi[1:])
    return sorted(heap[0][1:], key=lambda x: (len(x[1]), x))
```

**LeetCode 经典题目**：
- 455 分发饼干
- 435 无重叠区间
- 452 用最少数量的箭引爆气球
- 55/45 跳跃游戏 I/II
- 134 加油站
- 135 分发糖果
- 763 划分字母区间

---

## 六、回溯算法

### 6.1 回溯模板

```python
def backtrack(path, choices):
    if 满足终止条件:
        result.append(path[:])
        return
    for choice in choices:
        if 不合法:
            continue      # 剪枝
        path.append(choice)  # 做选择
        backtrack(path, new_choices)
        path.pop()           # 撤销选择
```

### 6.2 排列问题

```python
# 全排列 (LeetCode 46)
def permute(nums):
    result = []
    def backtrack(path, used):
        if len(path) == len(nums):
            result.append(path[:])
            return
        for i in range(len(nums)):
            if used[i]:
                continue
            used[i] = True
            path.append(nums[i])
            backtrack(path, used)
            path.pop()
            used[i] = False
    backtrack([], [False] * len(nums))
    return result

# 全排列 II - 含重复元素 (LeetCode 47)
def permute_unique(nums):
    nums.sort()
    result = []
    def backtrack(path, used):
        if len(path) == len(nums):
            result.append(path[:])
            return
        for i in range(len(nums)):
            if used[i]:
                continue
            # 剪枝：相同元素，前一个未使用则跳过
            if i > 0 and nums[i] == nums[i-1] and not used[i-1]:
                continue
            used[i] = True
            path.append(nums[i])
            backtrack(path, used)
            path.pop()
            used[i] = False
    backtrack([], [False] * len(nums))
    return result
```

### 6.3 组合问题

```python
# 组合 (LeetCode 77)
def combine(n, k):
    result = []
    def backtrack(start, path):
        if len(path) == k:
            result.append(path[:])
            return
        # 剪枝：剩余元素不够时提前终止
        for i in range(start, n - (k - len(path)) + 2):
            path.append(i)
            backtrack(i + 1, path)
            path.pop()
    backtrack(1, [])
    return result

# 组合总和 (LeetCode 39) - 可重复选择
def combination_sum(candidates, target):
    result = []
    def backtrack(start, path, remaining):
        if remaining == 0:
            result.append(path[:])
            return
        for i in range(start, len(candidates)):
            if candidates[i] > remaining:
                break  # 剪枝（需先排序）
            path.append(candidates[i])
            backtrack(i, path, remaining - candidates[i])  # i 不加 1，可重复
            path.pop()
    candidates.sort()
    backtrack(0, [], target)
    return result
```

### 6.4 子集问题

```python
# 子集 (LeetCode 78)
def subsets(nums):
    result = []
    def backtrack(start, path):
        result.append(path[:])  # 每个路径都是一个子集
        for i in range(start, len(nums)):
            path.append(nums[i])
            backtrack(i + 1, path)
            path.pop()
    backtrack(0, [])
    return result
```

### 6.5 N 皇后

```python
# N 皇后 (LeetCode 51)
def solve_n_queens(n):
    result = []
    board = [['.' ] * n for _ in range(n)]
    cols = set()
    diag1 = set()  # 主对角线 (row - col)
    diag2 = set()  # 副对角线 (row + col)

    def backtrack(row):
        if row == n:
            result.append([''.join(r) for r in board])
            return
        for col in range(n):
            if col in cols or (row-col) in diag1 or (row+col) in diag2:
                continue
            board[row][col] = 'Q'
            cols.add(col)
            diag1.add(row - col)
            diag2.add(row + col)
            backtrack(row + 1)
            board[row][col] = '.'
            cols.remove(col)
            diag1.remove(row - col)
            diag2.remove(row + col)

    backtrack(0)
    return result
```

### 6.6 数独

```python
# 解数独 (LeetCode 37)
def solve_sudoku(board):
    rows = [set() for _ in range(9)]
    cols = [set() for _ in range(9)]
    boxes = [set() for _ in range(9)]

    # 初始化已有数字
    for i in range(9):
        for j in range(9):
            if board[i][j] != '.':
                num = board[i][j]
                rows[i].add(num)
                cols[j].add(num)
                boxes[(i//3)*3 + j//3].add(num)

    def backtrack(pos):
        if pos == 81:
            return True
        i, j = pos // 9, pos % 9
        if board[i][j] != '.':
            return backtrack(pos + 1)
        for num in '123456789':
            box_idx = (i//3)*3 + j//3
            if num in rows[i] or num in cols[j] or num in boxes[box_idx]:
                continue
            board[i][j] = num
            rows[i].add(num)
            cols[j].add(num)
            boxes[box_idx].add(num)
            if backtrack(pos + 1):
                return True
            board[i][j] = '.'
            rows[i].remove(num)
            cols[j].remove(num)
            boxes[box_idx].remove(num)
        return False

    backtrack(0)
```

**回溯剪枝技巧总结**：
1. 排序后跳过重复元素
2. 提前终止（剩余元素不够、当前和已超过目标）
3. 用集合记录已使用状态（比数组更快）
4. 对称性剪枝（如 N 皇后只需搜一半列）

---

## 七、DFS/BFS 搜索

### 7.1 网格搜索

```python
# 岛屿数量 (LeetCode 200)
def num_islands(grid):
    if not grid:
        return 0
    m, n = len(grid), len(grid[0])
    count = 0

    def dfs(i, j):
        if i < 0 or i >= m or j < 0 or j >= n or grid[i][j] != '1':
            return
        grid[i][j] = '0'  # 标记已访问
        dfs(i+1, j)
        dfs(i-1, j)
        dfs(i, j+1)
        dfs(i, j-1)

    for i in range(m):
        for j in range(n):
            if grid[i][j] == '1':
                dfs(i, j)
                count += 1
    return count

# 岛屿的最大面积 (LeetCode 695)
def max_area_of_island(grid):
    m, n = len(grid), len(grid[0])

    def dfs(i, j):
        if i < 0 or i >= m or j < 0 or j >= n or grid[i][j] != 1:
            return 0
        grid[i][j] = 0
        return 1 + dfs(i+1,j) + dfs(i-1,j) + dfs(i,j+1) + dfs(i,j-1)

    return max((dfs(i, j) for i in range(m) for j in range(n)), default=0)
```

### 7.2 BFS 最短路径

```python
from collections import deque

# 单词接龙 (LeetCode 127) - BFS 最短路径
def ladder_length(begin_word, end_word, word_list):
    word_set = set(word_list)
    if end_word not in word_set:
        return 0
    queue = deque([(begin_word, 1)])
    visited = {begin_word}
    while queue:
        word, length = queue.popleft()
        for i in range(len(word)):
            for c in 'abcdefghijklmnopqrstuvwxyz':
                new_word = word[:i] + c + word[i+1:]
                if new_word == end_word:
                    return length + 1
                if new_word in word_set and new_word not in visited:
                    visited.add(new_word)
                    queue.append((new_word, length + 1))
    return 0

# 双向 BFS 优化
def ladder_length_bidirectional(begin_word, end_word, word_list):
    word_set = set(word_list)
    if end_word not in word_set:
        return 0
    front = {begin_word}
    back = {end_word}
    visited = set()
    length = 1
    while front and back:
        if len(front) > len(back):
            front, back = back, front  # 总是扩展较小的集合
        next_front = set()
        for word in front:
            for i in range(len(word)):
                for c in 'abcdefghijklmnopqrstuvwxyz':
                    new_word = word[:i] + c + word[i+1:]
                    if new_word in back:
                        return length + 1
                    if new_word in word_set and new_word not in visited:
                        visited.add(new_word)
                        next_front.add(new_word)
        front = next_front
        length += 1
    return 0
```

**LeetCode 经典题目**：
- 200 岛屿数量
- 695 岛屿的最大面积
- 994 腐烂的橘子（多源 BFS）
- 127 单词接龙
- 542 01 矩阵（多源 BFS）
- 417 太平洋大西洋水流问题

---

## 八、分治算法

### 8.1 核心思想

将问题分解为若干规模更小的子问题，递归求解后合并结果。

### 8.2 归并排序中的分治

```python
# 求逆序对 (剑指 Offer 51)
def reverse_pairs(nums):
    count = [0]
    def merge_sort(arr):
        if len(arr) <= 1:
            return arr
        mid = len(arr) // 2
        left = merge_sort(arr[:mid])
        right = merge_sort(arr[mid:])
        return merge_count(left, right)

    def merge_count(left, right):
        result = []
        i = j = 0
        while i < len(left) and j < len(right):
            if left[i] <= right[j]:
                result.append(left[i])
                i += 1
            else:
                result.append(right[j])
                count[0] += len(left) - i  # 统计逆序对
                j += 1
        result.extend(left[i:])
        result.extend(right[j:])
        return result

    merge_sort(nums)
    return count[0]
```

### 8.3 快速选择（Quick Select）

```python
# 数组中第 K 个最大元素 (LeetCode 215)
import random

def find_kth_largest(nums, k):
    target = len(nums) - k

    def quick_select(lo, hi):
        pivot_idx = random.randint(lo, hi)
        nums[pivot_idx], nums[hi] = nums[hi], nums[pivot_idx]
        pivot = nums[hi]
        i = lo
        for j in range(lo, hi):
            if nums[j] < pivot:
                nums[i], nums[j] = nums[j], nums[i]
                i += 1
        nums[i], nums[hi] = nums[hi], nums[i]
        if i == target:
            return nums[i]
        elif i < target:
            return quick_select(i + 1, hi)
        else:
            return quick_select(lo, i - 1)

    return quick_select(0, len(nums) - 1)
```

平均时间 O(n)，最坏 O(n^2)，随机化后期望 O(n)。

### 8.4 最近点对

```python
# 二维平面最近点对 - O(n log n)
def closest_pair(points):
    points.sort()

    def distance(p1, p2):
        return ((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2) ** 0.5

    def solve(pts):
        n = len(pts)
        if n <= 3:
            min_d = float('inf')
            for i in range(n):
                for j in range(i+1, n):
                    min_d = min(min_d, distance(pts[i], pts[j]))
            return min_d
        mid = n // 2
        mid_x = pts[mid][0]
        d = min(solve(pts[:mid]), solve(pts[mid:]))
        # 检查跨越中线的点对
        strip = [p for p in pts if abs(p[0] - mid_x) < d]
        strip.sort(key=lambda p: p[1])
        for i in range(len(strip)):
            j = i + 1
            while j < len(strip) and strip[j][1] - strip[i][1] < d:
                d = min(d, distance(strip[i], strip[j]))
                j += 1
        return d

    return solve(points)
```

---

## 九、字符串算法

### 9.1 KMP 算法

模式匹配，时间复杂度 O(n+m)。核心是构建 next 数组（部分匹配表）。

```python
def kmp_search(text, pattern):
    def build_next(p):
        next_arr = [0] * len(p)
        j = 0
        for i in range(1, len(p)):
            while j > 0 and p[i] != p[j]:
                j = next_arr[j - 1]
            if p[i] == p[j]:
                j += 1
            next_arr[i] = j
        return next_arr

    next_arr = build_next(pattern)
    j = 0
    result = []
    for i in range(len(text)):
        while j > 0 and text[i] != pattern[j]:
            j = next_arr[j - 1]
        if text[i] == pattern[j]:
            j += 1
        if j == len(pattern):
            result.append(i - len(pattern) + 1)
            j = next_arr[j - 1]
    return result
```

### 9.2 Rabin-Karp 算法

基于哈希的字符串匹配，平均 O(n+m)。

```python
def rabin_karp(text, pattern):
    n, m = len(text), len(pattern)
    if m > n:
        return []
    base, mod = 26, 10**9 + 7
    # 计算 pattern 的哈希值
    p_hash = 0
    t_hash = 0
    power = 1
    for i in range(m - 1):
        power = power * base % mod
    for i in range(m):
        p_hash = (p_hash * base + ord(pattern[i])) % mod
        t_hash = (t_hash * base + ord(text[i])) % mod
    result = []
    for i in range(n - m + 1):
        if p_hash == t_hash and text[i:i+m] == pattern:
            result.append(i)
        if i < n - m:
            t_hash = ((t_hash - ord(text[i]) * power) * base
                      + ord(text[i + m])) % mod
    return result
```

### 9.3 Manacher 算法

O(n) 求最长回文子串。

```python
def manacher(s):
    # 预处理：插入分隔符
    t = '#' + '#'.join(s) + '#'
    n = len(t)
    p = [0] * n  # p[i]: 以 t[i] 为中心的回文半径
    center = right = 0  # 当前回文的中心和右边界
    max_len = max_center = 0

    for i in range(n):
        if i < right:
            mirror = 2 * center - i
            p[i] = min(right - i, p[mirror])
        # 尝试扩展
        while (i - p[i] - 1 >= 0 and i + p[i] + 1 < n
               and t[i - p[i] - 1] == t[i + p[i] + 1]):
            p[i] += 1
        # 更新中心和右边界
        if i + p[i] > right:
            center, right = i, i + p[i]
        if p[i] > max_len:
            max_len = p[i]
            max_center = i

    start = (max_center - max_len) // 2
    return s[start:start + max_len]
```

**LeetCode 经典题目**：
- 28 找出字符串中第一个匹配项的下标（KMP）
- 5 最长回文子串（Manacher / DP）
- 214 最短回文串（KMP 应用）
- 459 重复的子字符串（KMP 应用）
- 686 重复叠加字符串匹配（Rabin-Karp）

---

## 十、数学与位运算

### 10.1 GCD 与 LCM

```python
import math

def gcd(a, b):
    while b:
        a, b = b, a % b
    return a

def lcm(a, b):
    return a * b // gcd(a, b)

# Python 3.9+ 可直接使用 math.gcd, math.lcm
```

### 10.2 快速幂

```python
def fast_pow(base, exp, mod):
    result = 1
    base %= mod
    while exp > 0:
        if exp & 1:
            result = result * base % mod
        exp >>= 1
        base = base * base % mod
    return result
```

### 10.3 质数筛（埃氏筛 / 线性筛）

```python
# 埃拉托斯特尼筛法 O(n log log n)
def sieve_eratosthenes(n):
    is_prime = [True] * (n + 1)
    is_prime[0] = is_prime[1] = False
    for i in range(2, int(n**0.5) + 1):
        if is_prime[i]:
            for j in range(i*i, n+1, i):
                is_prime[j] = False
    return [i for i in range(n+1) if is_prime[i]]

# 欧拉筛（线性筛）O(n)
def sieve_euler(n):
    is_prime = [True] * (n + 1)
    primes = []
    for i in range(2, n + 1):
        if is_prime[i]:
            primes.append(i)
        for p in primes:
            if i * p > n:
                break
            is_prime[i * p] = False
            if i % p == 0:
                break
    return primes
```

### 10.4 位运算技巧

```python
# 常用位运算
x & (x - 1)       # 消除最低位的 1（判断 2 的幂）
x & (-x)          # 获取最低位的 1（lowbit，树状数组核心）
x ^ x == 0        # 相同数异或为 0
x ^ 0 == x        # 任何数异或 0 不变
(x >> i) & 1      # 获取第 i 位
x | (1 << i)      # 将第 i 位设为 1
x & ~(1 << i)     # 将第 i 位设为 0
x ^ (1 << i)      # 翻转第 i 位

# 只出现一次的数字 (LeetCode 136)
def single_number(nums):
    result = 0
    for num in nums:
        result ^= num
    return result

# 只出现一次的数字 II (LeetCode 137) - 其余出现 3 次
def single_number_ii(nums):
    ones = twos = 0
    for num in nums:
        ones = (ones ^ num) & ~twos
        twos = (twos ^ num) & ~ones
    return ones

# 位运算实现加法 (LeetCode 371)
def get_sum(a, b):
    mask = 0xFFFFFFFF
    while b & mask:
        carry = (a & b) << 1
        a = a ^ b
        b = carry
    return a if b == 0 else a & mask
```

**LeetCode 经典题目**：
- 136/137/260 只出现一次的数字系列
- 191 位 1 的个数
- 231 2 的幂
- 371 两整数之和
- 50 Pow(x, n)（快速幂）
- 204 计数质数（质数筛）

---

## 面试高频问题（10 道精选）

### 1. 快排的原理？时间复杂度？最坏情况怎么优化？

快排通过选取 pivot 将数组分为两部分，左边 <= pivot，右边 >= pivot，递归排序。平均 O(n log n)，最坏 O(n^2)（数组已排序且 pivot 选第一个）。优化方式：随机选择 pivot、三数取中、三路分区（处理大量重复元素）、小区间改用插入排序。

### 2. 动态规划和贪心的区别？

DP 通过穷举所有子问题找全局最优，可以处理有后效性的问题。贪心每步选局部最优，不回头，效率更高但不一定能得到全局最优。DP 适用于有最优子结构 + 重叠子问题的场景，贪心适用于有贪心选择性质的场景。

### 3. 0-1 背包和完全背包的区别？代码上如何区分？

0-1 背包每件物品只能选一次，一维 DP 中容量**逆序**遍历（保证每件只选一次）。完全背包每件物品可选无限次，容量**正序**遍历（允许重复选取）。

### 4. 如何判断一个问题能用 DP 解？

满足两个条件：(1) 最优子结构 -- 问题的最优解包含子问题的最优解；(2) 重叠子问题 -- 子问题会被重复计算。如果只有最优子结构没有重叠子问题，可以用分治。

### 5. 二分查找的边界条件怎么处理？

关键在于明确搜索区间。`[left, right]` 闭区间用 `while left <= right`，更新 `left = mid + 1, right = mid - 1`。`[left, right)` 左闭右开用 `while left < right`，更新 `left = mid + 1, right = mid`。求左边界时找到 target 不返回而是收缩右边界，求右边界时收缩左边界。

### 6. 回溯法怎么剪枝？举例说明。

常见剪枝策略：排序后跳过重复元素（全排列 II）；提前终止不可能达到目标的分支（组合总和中当前和已超目标）；用集合记录状态避免重复计算（N 皇后用集合记录列和对角线）；利用对称性只搜索一半空间。

### 7. KMP 算法的 next 数组含义是什么？

next[i] 表示 pattern[0..i] 中最长的相等前后缀长度。匹配失败时，利用 next 数组跳过已匹配的部分，避免从头比较。相比暴力匹配的 O(nm)，KMP 时间复杂度为 O(n+m)。

### 8. 什么时候用 BFS，什么时候用 DFS？

BFS 适合求最短路径（无权图）、层序遍历、多源扩散问题。DFS 适合搜索所有路径、连通性判断、拓扑排序。空间上 BFS 需要队列存整层节点，DFS 只需栈深度的空间。网格问题两者都可，但 DFS 代码更简洁。

### 9. 滑动窗口适合解决什么问题？怎么判断是否可以用滑动窗口？

滑动窗口适合在连续子数组/子串中寻找满足条件的最优解。判断依据：问题涉及连续子序列；窗口具有单调性（扩大窗口使条件更容易满足或更不容易满足）。典型问题：最长无重复子串、最小覆盖子串、长度最小的子数组。

### 10. 位运算有哪些实际应用？

权限管理（Linux 文件权限 rwx 用 3 位二进制表示）；状态压缩 DP（用二进制位表示集合）；快速判断奇偶（`x & 1`）；快速乘除 2（左移右移）；去重和查找（异或找唯一数）；布隆过滤器（位数组）；网络子网掩码计算。
