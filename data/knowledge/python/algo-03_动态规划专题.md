# 动态规划专题

动态规划（Dynamic Programming, DP）是面试最高频算法专题。核心思路：**把原问题分解为子问题，记录子问题解避免重复计算**。

## 一、DP 核心思路

### 适用条件
1. **最优子结构**：原问题最优解包含子问题最优解
2. **重叠子问题**：不同路径会算到相同子问题（区别于分治）
3. **无后效性**：未来决策只依赖当前状态，不依赖到达当前状态的路径

### 解题五步
1. **状态定义**：`dp[i]` 表示什么？（最难，最重要）
2. **状态转移方程**：`dp[i]` 如何由 `dp[i-1]` 等推出？
3. **初始化**：边界值
4. **遍历顺序**：正序/倒序/二维双重循环
5. **返回什么**：最终答案是哪个状态

### DP vs 递归 vs 贪心
- 递归：自顶向下，可能重复计算
- DP：递归 + 记忆化 or 迭代 + 表
- 贪心：每步最优，不考虑全局

---

## 二、线性 DP

### 1. 爬楼梯（LeetCode 70）

问题：每次走 1 或 2 阶，到第 n 阶方法数？

```java
// 状态：dp[i] = 到第 i 阶的方法数
// 转移：dp[i] = dp[i-1] + dp[i-2]
public int climbStairs(int n) {
    if (n <= 2) return n;
    int prev2 = 1, prev1 = 2;
    for (int i = 3; i <= n; i++) {
        int cur = prev1 + prev2;
        prev2 = prev1;
        prev1 = cur;
    }
    return prev1;
}
```
滚动变量优化空间 O(1)。

### 2. 打家劫舍（LeetCode 198）

问题：相邻房不能偷，最大金额？

```java
// dp[i] = max(dp[i-1], dp[i-2] + nums[i])
public int rob(int[] nums) {
    int prev2 = 0, prev1 = 0;
    for (int x : nums) {
        int cur = Math.max(prev1, prev2 + x);
        prev2 = prev1;
        prev1 = cur;
    }
    return prev1;
}
```

**变种 213（环形）**：偷 [0, n-2] 和 [1, n-1] 两次取较大值。

**变种 337（树形）**：树形 DP，每节点 `{偷, 不偷}` 两状态。

### 3. 最长递增子序列 LIS（LeetCode 300）

**O(n²) DP**
```java
public int lengthOfLIS(int[] nums) {
    int n = nums.length;
    int[] dp = new int[n];  // dp[i] = 以 nums[i] 结尾的 LIS 长度
    Arrays.fill(dp, 1);
    int res = 1;
    for (int i = 1; i < n; i++) {
        for (int j = 0; j < i; j++) {
            if (nums[j] < nums[i]) {
                dp[i] = Math.max(dp[i], dp[j] + 1);
            }
        }
        res = Math.max(res, dp[i]);
    }
    return res;
}
```

**O(n log n) 贪心 + 二分**
```java
public int lengthOfLIS(int[] nums) {
    List<Integer> tails = new ArrayList<>();
    for (int x : nums) {
        int i = Collections.binarySearch(tails, x);
        if (i < 0) i = -(i + 1);
        if (i == tails.size()) tails.add(x);
        else tails.set(i, x);
    }
    return tails.size();
}
```
`tails[i]` 表示长度为 i+1 的 LIS 的最小尾元素。

### 4. 最大子数组和（LeetCode 53）

```java
// dp[i] = max(nums[i], dp[i-1] + nums[i])
public int maxSubArray(int[] nums) {
    int maxSum = nums[0], cur = nums[0];
    for (int i = 1; i < nums.length; i++) {
        cur = Math.max(nums[i], cur + nums[i]);
        maxSum = Math.max(maxSum, cur);
    }
    return maxSum;
}
```
Kadane 算法。

### 5. 编辑距离（LeetCode 72）

```java
// dp[i][j] = word1 前 i 字符 → word2 前 j 字符 的最少操作数
public int minDistance(String w1, String w2) {
    int m = w1.length(), n = w2.length();
    int[][] dp = new int[m + 1][n + 1];
    for (int i = 0; i <= m; i++) dp[i][0] = i;
    for (int j = 0; j <= n; j++) dp[0][j] = j;
    for (int i = 1; i <= m; i++) {
        for (int j = 1; j <= n; j++) {
            if (w1.charAt(i-1) == w2.charAt(j-1)) {
                dp[i][j] = dp[i-1][j-1];
            } else {
                dp[i][j] = 1 + Math.min(dp[i-1][j-1],  // 替换
                    Math.min(dp[i-1][j],  // 删除
                             dp[i][j-1])); // 插入
            }
        }
    }
    return dp[m][n];
}
```

---

## 三、背包问题

### 0-1 背包

问题：N 件物品重量 `w[i]` 价值 `v[i]`，背包容量 W，每件最多选 1 次，最大价值？

```java
// dp[i][j] = 前 i 件放入容量 j 的最大价值
// dp[i][j] = max(dp[i-1][j], dp[i-1][j-w[i]] + v[i])

// 空间优化：一维数组，**倒序遍历**
public int knapsack01(int[] w, int[] v, int W) {
    int[] dp = new int[W + 1];
    for (int i = 0; i < w.length; i++) {
        for (int j = W; j >= w[i]; j--) {  // 倒序！
            dp[j] = Math.max(dp[j], dp[j - w[i]] + v[i]);
        }
    }
    return dp[W];
}
```
**为什么倒序**：避免同一物品被多次选择。

### 完全背包

每件物品无限次。

```java
public int knapsackUnbounded(int[] w, int[] v, int W) {
    int[] dp = new int[W + 1];
    for (int i = 0; i < w.length; i++) {
        for (int j = w[i]; j <= W; j++) {  // 正序！
            dp[j] = Math.max(dp[j], dp[j - w[i]] + v[i]);
        }
    }
    return dp[W];
}
```

### 零钱兑换（LeetCode 322）

问题：凑成 amount 的最少硬币数。

```java
public int coinChange(int[] coins, int amount) {
    int[] dp = new int[amount + 1];
    Arrays.fill(dp, amount + 1);
    dp[0] = 0;
    for (int i = 1; i <= amount; i++) {
        for (int c : coins) {
            if (c <= i) dp[i] = Math.min(dp[i], dp[i - c] + 1);
        }
    }
    return dp[amount] > amount ? -1 : dp[amount];
}
```

### 零钱兑换 II（LeetCode 518）

凑成 amount 的组合数。

```java
public int change(int amount, int[] coins) {
    int[] dp = new int[amount + 1];
    dp[0] = 1;
    for (int c : coins) {  // 先遍历物品
        for (int j = c; j <= amount; j++) {  // 再遍历容量
            dp[j] += dp[j - c];
        }
    }
    return dp[amount];
}
```

**组合数问题**：外层遍历物品，内层容量。
**排列数问题**：外层容量，内层物品。

### 分割等和子集（LeetCode 416）

问题：能否将数组分成两个和相等的子集。
→ 转化为 0-1 背包，目标 sum/2。

```java
public boolean canPartition(int[] nums) {
    int sum = Arrays.stream(nums).sum();
    if (sum % 2 != 0) return false;
    int target = sum / 2;
    boolean[] dp = new boolean[target + 1];
    dp[0] = true;
    for (int x : nums) {
        for (int j = target; j >= x; j--) {
            dp[j] = dp[j] || dp[j - x];
        }
    }
    return dp[target];
}
```

---

## 四、二维 DP

### 最长公共子序列 LCS（LeetCode 1143）

```java
public int longestCommonSubsequence(String a, String b) {
    int m = a.length(), n = b.length();
    int[][] dp = new int[m + 1][n + 1];
    for (int i = 1; i <= m; i++) {
        for (int j = 1; j <= n; j++) {
            if (a.charAt(i-1) == b.charAt(j-1)) {
                dp[i][j] = dp[i-1][j-1] + 1;
            } else {
                dp[i][j] = Math.max(dp[i-1][j], dp[i][j-1]);
            }
        }
    }
    return dp[m][n];
}
```

### 不同路径（LeetCode 62/63）

机器人从 (0,0) 到 (m-1,n-1)，只能右/下。

```java
public int uniquePaths(int m, int n) {
    int[][] dp = new int[m][n];
    for (int i = 0; i < m; i++) dp[i][0] = 1;
    for (int j = 0; j < n; j++) dp[0][j] = 1;
    for (int i = 1; i < m; i++)
        for (int j = 1; j < n; j++)
            dp[i][j] = dp[i-1][j] + dp[i][j-1];
    return dp[m-1][n-1];
}
```

### 最小路径和（LeetCode 64）

```java
public int minPathSum(int[][] grid) {
    int m = grid.length, n = grid[0].length;
    int[][] dp = new int[m][n];
    dp[0][0] = grid[0][0];
    for (int i = 1; i < m; i++) dp[i][0] = dp[i-1][0] + grid[i][0];
    for (int j = 1; j < n; j++) dp[0][j] = dp[0][j-1] + grid[0][j];
    for (int i = 1; i < m; i++)
        for (int j = 1; j < n; j++)
            dp[i][j] = Math.min(dp[i-1][j], dp[i][j-1]) + grid[i][j];
    return dp[m-1][n-1];
}
```

---

## 五、区间 DP

### 最长回文子串（LeetCode 5）

**DP 法**
```java
public String longestPalindrome(String s) {
    int n = s.length();
    boolean[][] dp = new boolean[n][n];
    int start = 0, maxLen = 1;
    for (int i = 0; i < n; i++) dp[i][i] = true;
    for (int len = 2; len <= n; len++) {
        for (int i = 0; i + len - 1 < n; i++) {
            int j = i + len - 1;
            if (s.charAt(i) == s.charAt(j)) {
                dp[i][j] = (len == 2) || dp[i+1][j-1];
                if (dp[i][j] && len > maxLen) {
                    maxLen = len;
                    start = i;
                }
            }
        }
    }
    return s.substring(start, start + maxLen);
}
```

**中心扩散法** 更简单 O(n²) 空间 O(1)。

### 戳气球（LeetCode 312）

```java
// dp[i][j] = 戳破开区间 (i,j) 内所有气球的最大收益
public int maxCoins(int[] nums) {
    int n = nums.length;
    int[] vals = new int[n + 2];
    vals[0] = vals[n + 1] = 1;
    for (int i = 0; i < n; i++) vals[i + 1] = nums[i];
    int[][] dp = new int[n + 2][n + 2];
    for (int len = 3; len <= n + 2; len++) {
        for (int i = 0; i + len - 1 <= n + 1; i++) {
            int j = i + len - 1;
            for (int k = i + 1; k < j; k++) {
                dp[i][j] = Math.max(dp[i][j],
                    vals[i] * vals[k] * vals[j] + dp[i][k] + dp[k][j]);
            }
        }
    }
    return dp[0][n + 1];
}
```
**思路**：最后戳哪个气球，反向枚举。

---

## 六、树形 DP

### 二叉树最大路径和（LeetCode 124）

```java
int maxSum = Integer.MIN_VALUE;

public int maxPathSum(TreeNode root) {
    dfs(root);
    return maxSum;
}

private int dfs(TreeNode node) {
    if (node == null) return 0;
    int left = Math.max(0, dfs(node.left));
    int right = Math.max(0, dfs(node.right));
    maxSum = Math.max(maxSum, node.val + left + right);  // 经过本节点
    return node.val + Math.max(left, right);  // 向上返回
}
```

### 打家劫舍 III（LeetCode 337）

```java
public int rob(TreeNode root) {
    int[] res = dfs(root);
    return Math.max(res[0], res[1]);
}

// 返回 [不偷当前节点, 偷当前节点]
private int[] dfs(TreeNode node) {
    if (node == null) return new int[2];
    int[] l = dfs(node.left), r = dfs(node.right);
    int notRob = Math.max(l[0], l[1]) + Math.max(r[0], r[1]);
    int rob = node.val + l[0] + r[0];
    return new int[]{notRob, rob};
}
```

---

## 七、状态机 DP

### 买卖股票系列

**I（LeetCode 121）**：只能 1 次
```java
public int maxProfit(int[] prices) {
    int minPrice = Integer.MAX_VALUE, max = 0;
    for (int p : prices) {
        minPrice = Math.min(minPrice, p);
        max = Math.max(max, p - minPrice);
    }
    return max;
}
```

**II**：可多次
```java
int profit = 0;
for (int i = 1; i < prices.length; i++)
    if (prices[i] > prices[i-1]) profit += prices[i] - prices[i-1];
```

**III**：最多 2 次 / **IV**：最多 k 次
```java
// dp[i][j][0/1] = 第 i 天，进行了 j 次交易，持股 or 不持股
public int maxProfit(int k, int[] prices) {
    int n = prices.length;
    if (n == 0) return 0;
    int[][][] dp = new int[n][k+1][2];
    for (int j = 0; j <= k; j++) dp[0][j][1] = -prices[0];
    for (int i = 1; i < n; i++) {
        for (int j = 1; j <= k; j++) {
            dp[i][j][0] = Math.max(dp[i-1][j][0], dp[i-1][j][1] + prices[i]);
            dp[i][j][1] = Math.max(dp[i-1][j][1], dp[i-1][j-1][0] - prices[i]);
        }
    }
    return dp[n-1][k][0];
}
```

**V（含冷冻期）/ VI（含手续费）**：增加状态。

---

## 八、字符串 DP

### 通配符匹配（LeetCode 44）

```java
// dp[i][j] = s[0..i-1] 与 p[0..j-1] 是否匹配
public boolean isMatch(String s, String p) {
    int m = s.length(), n = p.length();
    boolean[][] dp = new boolean[m+1][n+1];
    dp[0][0] = true;
    for (int j = 1; j <= n; j++)
        if (p.charAt(j-1) == '*') dp[0][j] = dp[0][j-1];
    for (int i = 1; i <= m; i++) {
        for (int j = 1; j <= n; j++) {
            if (p.charAt(j-1) == '?' || s.charAt(i-1) == p.charAt(j-1)) {
                dp[i][j] = dp[i-1][j-1];
            } else if (p.charAt(j-1) == '*') {
                dp[i][j] = dp[i-1][j] || dp[i][j-1];  // * 匹配空 or 匹配当前字符
            }
        }
    }
    return dp[m][n];
}
```

### 正则匹配（LeetCode 10）

`.` 任意单字符，`*` 前面字符 0 或多次。

```java
public boolean isMatch(String s, String p) {
    int m = s.length(), n = p.length();
    boolean[][] dp = new boolean[m+1][n+1];
    dp[0][0] = true;
    for (int j = 2; j <= n; j++)
        if (p.charAt(j-1) == '*') dp[0][j] = dp[0][j-2];
    for (int i = 1; i <= m; i++) {
        for (int j = 1; j <= n; j++) {
            char c = p.charAt(j-1);
            if (c == '.' || c == s.charAt(i-1)) {
                dp[i][j] = dp[i-1][j-1];
            } else if (c == '*') {
                dp[i][j] = dp[i][j-2];  // * 算 0 次
                if (p.charAt(j-2) == '.' || p.charAt(j-2) == s.charAt(i-1)) {
                    dp[i][j] = dp[i][j] || dp[i-1][j];  // * 算多次
                }
            }
        }
    }
    return dp[m][n];
}
```

---

## 九、数位 DP

### 数字 1 的个数（LeetCode 233）

统计 [0, n] 所有数字中 1 出现的次数。

```java
public int countDigitOne(int n) {
    int count = 0;
    long factor = 1;
    while (factor <= n) {
        long low = n % factor;
        long cur = (n / factor) % 10;
        long high = n / (factor * 10);
        if (cur == 0) count += high * factor;
        else if (cur == 1) count += high * factor + low + 1;
        else count += (high + 1) * factor;
        factor *= 10;
    }
    return count;
}
```

---

## 十、压缩状态 DP（位运算）

### 旅行商问题 TSP

n 个城市，从 0 出发访问所有城市再回到 0 的最短路径。

```java
// dp[mask][i] = 访问集合 mask，当前在 i 的最短距离
public int tsp(int[][] dist) {
    int n = dist.length;
    int[][] dp = new int[1 << n][n];
    for (int[] row : dp) Arrays.fill(row, Integer.MAX_VALUE / 2);
    dp[1][0] = 0;
    for (int mask = 1; mask < (1 << n); mask++) {
        for (int i = 0; i < n; i++) {
            if ((mask & (1 << i)) == 0) continue;
            for (int j = 0; j < n; j++) {
                if ((mask & (1 << j)) != 0 || i == j) continue;
                int next = mask | (1 << j);
                dp[next][j] = Math.min(dp[next][j], dp[mask][i] + dp[i][j] + dist[i][j]);
                // 注意：这里 dp[i][j] 应该是 dist[i][j]
            }
        }
    }
    int res = Integer.MAX_VALUE;
    for (int i = 1; i < n; i++) {
        res = Math.min(res, dp[(1 << n) - 1][i] + dist[i][0]);
    }
    return res;
}
```

---

## 常见 DP 优化技巧

### 空间滚动
二维 DP 只依赖上一行 → 压一维 + 倒序。

### 单调队列优化
```
dp[i] = min(dp[j] + cost(j, i)) for j in [i-k, i-1]
```
单调队列维护窗口最小值，O(n) 代替 O(nk)。

### 斜率优化
某些 DP 可以转化为直线方程求最小值，用凸包维护。

### 矩阵快速幂
线性递推 + n 非常大时，用矩阵幂 O(log n)。

---

## 面试常见 DP 题（必刷 20 道）

1. 爬楼梯（70）
2. 打家劫舍 I/II/III（198/213/337）
3. 最大子数组和（53）
4. 最长递增子序列（300）
5. 最长公共子序列（1143）
6. 编辑距离（72）
7. 最长回文子串（5）
8. 零钱兑换（322）
9. 分割等和子集（416）
10. 不同路径（62）
11. 最小路径和（64）
12. 戳气球（312）
13. 买卖股票 I-VI（121/122/123/188/309/714）
14. 通配符匹配（44）
15. 正则匹配（10）
16. 单词拆分（139）
17. 最长有效括号（32）
18. 俄罗斯套娃信封（354）
19. 不同的子序列（115）
20. 解码方法（91）

---

## 面试答题模板

遇到 DP 题，**说这四句**：
1. "我定义 dp[i] 表示…"
2. "状态转移方程是 dp[i] = …"
3. "初始化 dp[0] = …"
4. "遍历顺序是…，最后返回…"

然后分析**时间空间复杂度**，如果能优化再优化。
