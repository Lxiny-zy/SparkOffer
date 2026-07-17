# LeetCode 热题 100（面试必刷）

本章按专题归类精选 100 道高频面试题，每题给**核心思路 + 最优解代码 + 复杂度**。

## 一、数组（15）

### 1. 两数之和（#1）
**思路**：哈希表存已遍历元素。
```java
public int[] twoSum(int[] nums, int target) {
    Map<Integer, Integer> map = new HashMap<>();
    for (int i = 0; i < nums.length; i++) {
        if (map.containsKey(target - nums[i])) return new int[]{map.get(target - nums[i]), i};
        map.put(nums[i], i);
    }
    return null;
}
```
O(n)。

### 2. 三数之和（#15）
**思路**：排序 + 固定一个 + 双指针。
```java
public List<List<Integer>> threeSum(int[] nums) {
    Arrays.sort(nums);
    List<List<Integer>> res = new ArrayList<>();
    for (int i = 0; i < nums.length - 2; i++) {
        if (i > 0 && nums[i] == nums[i-1]) continue;
        int l = i + 1, r = nums.length - 1;
        while (l < r) {
            int sum = nums[i] + nums[l] + nums[r];
            if (sum == 0) {
                res.add(Arrays.asList(nums[i], nums[l], nums[r]));
                while (l < r && nums[l] == nums[l+1]) l++;
                while (l < r && nums[r] == nums[r-1]) r--;
                l++; r--;
            } else if (sum < 0) l++;
            else r--;
        }
    }
    return res;
}
```
O(n²)。

### 3. 下一个排列（#31）
**思路**：从右找第一个下降位 i，从右找第一个大于 nums[i] 的位 j，交换后反转 i+1 之后。

### 4. 搜索旋转排序数组（#33）
**思路**：二分查找，判断哪一半有序。
```java
public int search(int[] nums, int target) {
    int l = 0, r = nums.length - 1;
    while (l <= r) {
        int m = (l + r) >>> 1;
        if (nums[m] == target) return m;
        if (nums[l] <= nums[m]) {  // 左半有序
            if (nums[l] <= target && target < nums[m]) r = m - 1;
            else l = m + 1;
        } else {  // 右半有序
            if (nums[m] < target && target <= nums[r]) l = m + 1;
            else r = m - 1;
        }
    }
    return -1;
}
```

### 5. 合并区间（#56）
**思路**：按起点排序，遍历合并。
```java
public int[][] merge(int[][] intervals) {
    Arrays.sort(intervals, (a, b) -> a[0] - b[0]);
    List<int[]> res = new ArrayList<>();
    int[] cur = intervals[0];
    for (int i = 1; i < intervals.length; i++) {
        if (intervals[i][0] <= cur[1]) cur[1] = Math.max(cur[1], intervals[i][1]);
        else { res.add(cur); cur = intervals[i]; }
    }
    res.add(cur);
    return res.toArray(new int[0][]);
}
```

### 6. 最大子数组和（#53）
见 DP 专题。Kadane O(n)。

### 7. 跳跃游戏（#55）
**贪心**：维护能到达的最远位置。
```java
public boolean canJump(int[] nums) {
    int farthest = 0;
    for (int i = 0; i <= farthest && i < nums.length; i++) {
        farthest = Math.max(farthest, i + nums[i]);
    }
    return farthest >= nums.length - 1;
}
```

### 8. 螺旋矩阵（#54）
**四方向循环**，注意边界。

### 9. 颜色分类（#75）
**荷兰旗/三指针**：0 放左，2 放右，1 中间。
```java
public void sortColors(int[] nums) {
    int l = 0, r = nums.length - 1, i = 0;
    while (i <= r) {
        if (nums[i] == 0) swap(nums, i++, l++);
        else if (nums[i] == 2) swap(nums, i, r--);
        else i++;
    }
}
```

### 10. 缺失的第一个正数（#41）
**置换法**：把 nums[i] 放到 nums[nums[i]-1]。
```java
public int firstMissingPositive(int[] nums) {
    int n = nums.length;
    for (int i = 0; i < n; i++) {
        while (nums[i] > 0 && nums[i] <= n && nums[nums[i] - 1] != nums[i]) {
            swap(nums, i, nums[i] - 1);
        }
    }
    for (int i = 0; i < n; i++) if (nums[i] != i + 1) return i + 1;
    return n + 1;
}
```
O(n) 时间 O(1) 空间。

### 11. 接雨水（#42）
**双指针**：左右最大值决定当前柱能接的水。
```java
public int trap(int[] height) {
    int l = 0, r = height.length - 1, lMax = 0, rMax = 0, res = 0;
    while (l < r) {
        if (height[l] < height[r]) {
            lMax = Math.max(lMax, height[l]);
            res += lMax - height[l++];
        } else {
            rMax = Math.max(rMax, height[r]);
            res += rMax - height[r--];
        }
    }
    return res;
}
```

### 12. 盛最多水的容器（#11）
**双指针**：从两端，短的内移。

### 13. 乘积最大子数组（#152）
**DP**：同时维护最大值和最小值（负负得正）。

### 14. 除自身以外数组的乘积（#238）
**前缀积 + 后缀积**，O(1) 额外空间。

### 15. 和为 K 的子数组（#560）
**前缀和 + 哈希表**。
```java
public int subarraySum(int[] nums, int k) {
    Map<Integer, Integer> map = new HashMap<>();
    map.put(0, 1);
    int sum = 0, count = 0;
    for (int x : nums) {
        sum += x;
        count += map.getOrDefault(sum - k, 0);
        map.merge(sum, 1, Integer::sum);
    }
    return count;
}
```

---

## 二、字符串（10）

### 16. 无重复字符的最长子串（#3）
**滑动窗口 + 哈希**。
```java
public int lengthOfLongestSubstring(String s) {
    Map<Character, Integer> map = new HashMap<>();
    int l = 0, max = 0;
    for (int r = 0; r < s.length(); r++) {
        char c = s.charAt(r);
        if (map.containsKey(c) && map.get(c) >= l) l = map.get(c) + 1;
        map.put(c, r);
        max = Math.max(max, r - l + 1);
    }
    return max;
}
```

### 17. 最长回文子串（#5）
**中心扩散**：枚举中心（奇偶两种），向两边扩散。O(n²)。

### 18. 字符串转整数（#8）
处理空格、符号、溢出。

### 19. 正则表达式匹配（#10） / 通配符匹配（#44）
见 DP 专题。

### 20. 括号生成（#22）
**回溯**：维护剩余左右括号数。

### 21. 字母异位词分组（#49）
**排序后为 key** 或 **计数 26 字母为 key**。

### 22. 最小覆盖子串（#76）
**滑窗 + 计数**。
```java
public String minWindow(String s, String t) {
    int[] need = new int[128];
    for (char c : t.toCharArray()) need[c]++;
    int l = 0, cnt = t.length(), minL = 0, minLen = Integer.MAX_VALUE;
    for (int r = 0; r < s.length(); r++) {
        if (need[s.charAt(r)]-- > 0) cnt--;
        while (cnt == 0) {
            if (r - l + 1 < minLen) { minL = l; minLen = r - l + 1; }
            if (need[s.charAt(l++)]++ == 0) cnt++;
        }
    }
    return minLen == Integer.MAX_VALUE ? "" : s.substring(minL, minL + minLen);
}
```

### 23. 字符串相加（#415）
手动加法，进位处理。

### 24. 翻转字符串里的单词（#151）
trim + split + reverse + join，或原地双指针。

### 25. 实现 strStr（#28）
暴力 O(mn) 或 KMP O(m+n)。

---

## 三、链表（10）

### 26. 反转链表（#206）
```java
public ListNode reverseList(ListNode head) {
    ListNode prev = null, cur = head;
    while (cur != null) {
        ListNode next = cur.next;
        cur.next = prev;
        prev = cur; cur = next;
    }
    return prev;
}
```

### 27. 合并两个有序链表（#21）
归并。

### 28. 合并 K 个有序链表（#23）
**堆** O(N log K) 或 **分治** O(N log K)。

### 29. 环形链表（#141） / 环的入口（#142）
**快慢指针**。入口：相遇后一个从 head 走，一个从相遇点走，再相遇即入口。

### 30. 相交链表（#160）
**双指针**：A 走完接 B，B 走完接 A，相遇即交点。

### 31. 删除链表倒数第 N 个（#19）
快慢指针，快先走 N 步。

### 32. 两两交换链表节点（#24）
递归或迭代。

### 33. K 个一组反转链表（#25）
每 K 个一段反转。

### 34. 排序链表（#148）
归并排序 O(n log n) 额外 O(log n)。

### 35. 复制带随机指针的链表（#138）
哈希表 或 原地复制（交错链接）。

---

## 四、树（15）

### 36. 二叉树的中序遍历（#94）
**迭代法**：栈。
```java
List<Integer> res = new ArrayList<>();
Deque<TreeNode> stack = new ArrayDeque<>();
TreeNode cur = root;
while (cur != null || !stack.isEmpty()) {
    while (cur != null) { stack.push(cur); cur = cur.left; }
    cur = stack.pop();
    res.add(cur.val);
    cur = cur.right;
}
```

### 37. 二叉树的层序遍历（#102）
BFS。

### 38. 二叉树最大深度（#104）
`1 + max(left, right)`。

### 39. 二叉树直径（#543）
后序 DFS，维护全局 max。

### 40. 二叉树最大路径和（#124）
见 DP 专题。

### 41. 对称二叉树（#101）
递归比较左右子树。

### 42. 验证二叉搜索树（#98）
中序遍历递增 或 递归传入上下界。

### 43. 二叉搜索树第 K 小（#230）
中序遍历到第 K 个。

### 44. 前序中序构造（#105）
递归：前序第一个是根，中序中找根位置划分。

### 45. 二叉树展开为链表（#114）
Morris 遍历或递归。

### 46. 路径总和 III（#437）
前缀和 + 哈希。

### 47. 二叉树右视图（#199）
层序每层最后一个。

### 48. 最近公共祖先 LCA（#236）
递归：左右子树都找到返回当前节点。
```java
public TreeNode lowestCommonAncestor(TreeNode root, TreeNode p, TreeNode q) {
    if (root == null || root == p || root == q) return root;
    TreeNode l = lowestCommonAncestor(root.left, p, q);
    TreeNode r = lowestCommonAncestor(root.right, p, q);
    if (l != null && r != null) return root;
    return l != null ? l : r;
}
```

### 49. 序列化反序列化（#297）
前序 + null 标记。

### 50. Trie 前缀树（#208）
子节点数组或 map。

---

## 五、动态规划（15）

见 DP 专题。精选 15：

51. 爬楼梯（70）
52. 打家劫舍（198）
53. 最大子数组和（53）
54. 不同路径（62）
55. 最小路径和（64）
56. 最长递增子序列（300）
57. 最长公共子序列（1143）
58. 编辑距离（72）
59. 零钱兑换（322）
60. 分割等和子集（416）
61. 单词拆分（139）
62. 戳气球（312）
63. 买卖股票最佳时机 II/IV（122/188）
64. 最长回文子串（5）
65. 最长有效括号（32）

---

## 六、回溯（10）

### 66. 全排列（#46）
```java
public List<List<Integer>> permute(int[] nums) {
    List<List<Integer>> res = new ArrayList<>();
    backtrack(nums, new ArrayList<>(), new boolean[nums.length], res);
    return res;
}
void backtrack(int[] nums, List<Integer> cur, boolean[] used, List<List<Integer>> res) {
    if (cur.size() == nums.length) { res.add(new ArrayList<>(cur)); return; }
    for (int i = 0; i < nums.length; i++) {
        if (used[i]) continue;
        used[i] = true; cur.add(nums[i]);
        backtrack(nums, cur, used, res);
        cur.remove(cur.size() - 1); used[i] = false;
    }
}
```

### 67. 子集（#78） / 子集 II（#90）
每个元素"选/不选"。II 需去重（先排序，跳过同层相同）。

### 68. 组合总和（#39） / II（#40）
允许重复/不允许。

### 69. 括号生成（#22）
见字符串。

### 70. 单词搜索（#79）
网格 + 回溯。

### 71. N 皇后（#51）
经典。维护列、对角线占用。

### 72. 分割回文串（#131）
每次切一段检查回文。

### 73. 解数独（#37）
九宫格 + 回溯。

### 74. 电话号码组合（#17）
映射数字到字母，DFS。

### 75. 复原 IP 地址（#93）
四段切分。

---

## 七、栈与队列（6）

### 76. 有效的括号（#20）
栈。

### 77. 最小栈（#155）
辅助栈维护最小值。

### 78. 每日温度（#739）
**单调栈**（递减）。
```java
public int[] dailyTemperatures(int[] t) {
    int[] res = new int[t.length];
    Deque<Integer> stack = new ArrayDeque<>();
    for (int i = 0; i < t.length; i++) {
        while (!stack.isEmpty() && t[stack.peek()] < t[i]) {
            int j = stack.pop();
            res[j] = i - j;
        }
        stack.push(i);
    }
    return res;
}
```

### 79. 柱状图最大矩形（#84）
**单调栈**（递增）。

### 80. 滑动窗口最大值（#239）
**单调队列**。

### 81. 用栈实现队列（#232） / 队列实现栈（#225）

---

## 八、二分查找（5）

### 82. 二分查找（#704）

### 83. 搜索插入位置（#35）

### 84. 在排序数组中查找元素第一个和最后一个位置（#34）
两次二分，一次找左边界，一次找右边界。

### 85. 寻找两个正序数组的中位数（#4）
二分第 K 小。

### 86. 寻找旋转数组最小值（#153）
标准二分变种。

---

## 九、贪心（5）

### 87. 跳跃游戏（#55） / II（#45）
维护当前能到达的最远 / 当前步数能到达的最远。

### 88. 加油站（#134）
总油 >= 总耗就能绕一圈；起点是累计最小值之后的位置。

### 89. 分发糖果（#135）
两次遍历：从左到右，从右到左。

### 90. 划分字母区间（#763）
记录每个字母最后出现位置，贪心划分。

### 91. 任务调度器（#621）
数学公式：`max(len, (maxCount-1)*(n+1) + maxCountNum)`。

---

## 十、图与高级（9）

### 92. 岛屿数量（#200）
BFS/DFS/并查集。

### 93. 课程表（#207）
拓扑排序。

### 94. 单词接龙（#127）
BFS。

### 95. 克隆图（#133）
DFS/BFS + 哈希。

### 96. LRU 缓存（#146）
**双向链表 + 哈希**，或用 LinkedHashMap。

### 97. 实现 Trie（#208）
见树。

### 98. 数据流中位数（#295）
**两个堆**：大顶堆（较小一半）+ 小顶堆（较大一半）。

### 99. 天际线问题（#218）
**扫描线 + 优先队列**。

### 100. 滑动窗口中位数（#480）
**两个有序集合** 或 双堆（懒删除）。

---

## 额外必刷补充

- #200 岛屿数量
- #215 数组中第 K 大（快选）
- #283 移动零（双指针）
- #287 寻找重复数（Floyd 环检测）
- #300 LIS
- #309 最佳买卖股票含冷冻期（DP）
- #322 零钱兑换
- #394 字符串解码（栈）
- #448 找到所有消失的数字
- #461 汉明距离
- #494 目标和（DP）

---

## 刷题建议

### 面试前 2 周冲刺计划
- **Day 1-3**：数组 + 字符串（25 题）
- **Day 4-5**：链表（10 题）
- **Day 6-8**：树（15 题）
- **Day 9-11**：动态规划（15 题）
- **Day 12-13**：回溯（10 题）
- **Day 14**：栈/队列/二分/贪心/图（25 题）

### 每天固定时间
- 2-3 题，每题 30-45 分钟
- 做不出看题解，理解后合上独立写一遍
- 第二天复盘昨天的题

### 面试时答题流程
1. **理解题意**：重复一遍确认
2. **举例**：自己给个 case 跑一遍
3. **说思路**：先说思路，再写
4. **复杂度**：主动说时空
5. **验证**：用例子走一遍代码
6. **优化**：有更好方案主动说

### 常见错误
- 数组越界（边界条件）
- 整数溢出（long）
- 空指针（null 检查）
- 循环条件 `<=` vs `<`
- 引用 vs 拷贝（Java 对象）
- 深浅拷贝（二维数组）

### 复杂度速记
| n 规模 | 可接受复杂度 |
|--------|--------------|
| 10 | O(n!) |
| 20 | O(2^n) |
| 50-500 | O(n³) |
| 10³-10⁴ | O(n²) |
| 10⁵-10⁶ | O(n log n) |
| 10⁷-10⁸ | O(n) |
| > 10⁸ | O(log n) |
