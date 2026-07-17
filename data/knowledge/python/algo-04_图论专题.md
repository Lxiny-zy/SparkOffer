# 图论专题

图论是面试硬骨头，涉及建图、遍历、最短路、连通性、拓扑排序等。

## 一、图的表示

### 邻接矩阵
`graph[i][j] = w` 表示 i→j 权重 w，0/INF 表示无边。
- 空间 O(V²)
- 查边 O(1)
- 适合稠密图

### 邻接表
```java
List<List<int[]>> graph = new ArrayList<>();
// graph.get(u) 存 [v, weight] 列表
```
- 空间 O(V+E)
- 查边 O(degree)
- 适合稀疏图（实际更常用）

### 边集
```java
int[][] edges = {{0, 1, 5}, {1, 2, 3}, ...};  // {u, v, weight}
```
Bellman-Ford、Kruskal 用。

---

## 二、图的遍历

### BFS（广度优先）

```java
public void bfs(int start, List<List<Integer>> graph) {
    Queue<Integer> q = new LinkedList<>();
    boolean[] visited = new boolean[graph.size()];
    q.offer(start);
    visited[start] = true;
    while (!q.isEmpty()) {
        int u = q.poll();
        for (int v : graph.get(u)) {
            if (!visited[v]) {
                visited[v] = true;
                q.offer(v);
            }
        }
    }
}
```

**层次遍历**（记录距离/层数）：
```java
int level = 0;
while (!q.isEmpty()) {
    int size = q.size();
    for (int i = 0; i < size; i++) {
        int u = q.poll();
        // ...
    }
    level++;
}
```

**应用**：
- 无权图最短路径
- 层序遍历
- 连通分量数
- 最短转换序列（单词接龙）

### DFS（深度优先）

**递归**
```java
public void dfs(int u, List<List<Integer>> graph, boolean[] visited) {
    visited[u] = true;
    for (int v : graph.get(u)) {
        if (!visited[v]) dfs(v, graph, visited);
    }
}
```

**迭代（栈）**
```java
Deque<Integer> stack = new ArrayDeque<>();
stack.push(start);
while (!stack.isEmpty()) {
    int u = stack.pop();
    if (visited[u]) continue;
    visited[u] = true;
    for (int v : graph.get(u)) stack.push(v);
}
```

**应用**：
- 连通性、连通分量
- 环检测
- 拓扑排序
- 路径枚举

---

## 三、最短路径

### Dijkstra（单源，非负权）

**适用**：单源最短路，边权非负。

**朴素 O(V²)**：
```java
public int[] dijkstra(List<List<int[]>> graph, int src) {
    int n = graph.size();
    int[] dist = new int[n];
    Arrays.fill(dist, Integer.MAX_VALUE);
    dist[src] = 0;
    boolean[] visited = new boolean[n];
    for (int i = 0; i < n; i++) {
        int u = -1, min = Integer.MAX_VALUE;
        for (int j = 0; j < n; j++) {
            if (!visited[j] && dist[j] < min) {
                min = dist[j]; u = j;
            }
        }
        if (u == -1) break;
        visited[u] = true;
        for (int[] e : graph.get(u)) {
            int v = e[0], w = e[1];
            if (dist[u] + w < dist[v]) dist[v] = dist[u] + w;
        }
    }
    return dist;
}
```

**堆优化 O((V+E) log V)**：
```java
public int[] dijkstra(int n, List<List<int[]>> graph, int src) {
    int[] dist = new int[n];
    Arrays.fill(dist, Integer.MAX_VALUE);
    dist[src] = 0;
    PriorityQueue<int[]> pq = new PriorityQueue<>((a, b) -> a[1] - b[1]);
    pq.offer(new int[]{src, 0});
    while (!pq.isEmpty()) {
        int[] cur = pq.poll();
        int u = cur[0], d = cur[1];
        if (d > dist[u]) continue;  // 过期
        for (int[] e : graph.get(u)) {
            int v = e[0], w = e[1];
            if (d + w < dist[v]) {
                dist[v] = d + w;
                pq.offer(new int[]{v, dist[v]});
            }
        }
    }
    return dist;
}
```

**不能处理负权边**。

### Bellman-Ford（单源，可负权）

**适用**：有负权，或需检测负环。

```java
public int[] bellmanFord(int n, int[][] edges, int src) {
    int[] dist = new int[n];
    Arrays.fill(dist, Integer.MAX_VALUE);
    dist[src] = 0;
    for (int i = 0; i < n - 1; i++) {
        for (int[] e : edges) {
            int u = e[0], v = e[1], w = e[2];
            if (dist[u] != Integer.MAX_VALUE && dist[u] + w < dist[v]) {
                dist[v] = dist[u] + w;
            }
        }
    }
    // 检测负环（第 n 轮仍能松弛）
    for (int[] e : edges) {
        if (dist[e[0]] + e[2] < dist[e[1]]) return null;  // 有负环
    }
    return dist;
}
```
O(VE)。

### SPFA
Bellman-Ford 的队列优化，平均情况更快，最坏同为 O(VE)。

### Floyd-Warshall（多源）

**适用**：所有点对之间最短路。

```java
public int[][] floyd(int n, int[][] graph) {
    int[][] dist = new int[n][n];
    // 初始化 dist = graph，自己到自己 = 0，无边 = INF
    for (int k = 0; k < n; k++) {
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < n; j++) {
                if (dist[i][k] + dist[k][j] < dist[i][j]) {
                    dist[i][j] = dist[i][k] + dist[k][j];
                }
            }
        }
    }
    return dist;
}
```
O(V³)，适合小图（n < 500）。

---

## 四、拓扑排序

### 应用
- 课程安排
- 任务依赖
- 编译顺序
- 构建依赖

### Kahn 算法（BFS）

```java
public int[] topoSort(int n, List<List<Integer>> graph) {
    int[] indegree = new int[n];
    for (int u = 0; u < n; u++)
        for (int v : graph.get(u)) indegree[v]++;
    Queue<Integer> q = new LinkedList<>();
    for (int i = 0; i < n; i++) if (indegree[i] == 0) q.offer(i);
    int[] order = new int[n];
    int idx = 0;
    while (!q.isEmpty()) {
        int u = q.poll();
        order[idx++] = u;
        for (int v : graph.get(u)) {
            if (--indegree[v] == 0) q.offer(v);
        }
    }
    return idx == n ? order : null;  // null 表示有环
}
```

### DFS 法

```java
List<Integer> order = new ArrayList<>();
int[] state;  // 0 未访问, 1 访问中, 2 已完成

public List<Integer> topoSortDfs(int n, List<List<Integer>> graph) {
    state = new int[n];
    for (int i = 0; i < n; i++) {
        if (state[i] == 0 && !dfs(i, graph)) return null;
    }
    Collections.reverse(order);
    return order;
}

boolean dfs(int u, List<List<Integer>> graph) {
    if (state[u] == 1) return false;  // 有环
    if (state[u] == 2) return true;
    state[u] = 1;
    for (int v : graph.get(u)) if (!dfs(v, graph)) return false;
    state[u] = 2;
    order.add(u);
    return true;
}
```

**LeetCode 207 课程表 / 210 课程表 II** 都是拓扑排序。

---

## 五、并查集（Union-Find / DSU）

### 应用
- 连通分量
- Kruskal MST
- 动态连通性
- 岛屿数量（加强版）

### 实现（路径压缩 + 按秩合并）

```java
class UnionFind {
    int[] parent, rank;

    public UnionFind(int n) {
        parent = new int[n];
        rank = new int[n];
        for (int i = 0; i < n; i++) parent[i] = i;
    }

    public int find(int x) {
        if (parent[x] != x) parent[x] = find(parent[x]);  // 路径压缩
        return parent[x];
    }

    public boolean union(int x, int y) {
        int px = find(x), py = find(y);
        if (px == py) return false;
        if (rank[px] < rank[py]) parent[px] = py;
        else if (rank[px] > rank[py]) parent[py] = px;
        else { parent[py] = px; rank[px]++; }
        return true;
    }
}
```
均摊 O(α(n))，接近 O(1)。

### 典型问题

**岛屿数量（LeetCode 200）**
```java
public int numIslands(char[][] grid) {
    int m = grid.length, n = grid[0].length;
    UnionFind uf = new UnionFind(m * n);
    int land = 0;
    for (int i = 0; i < m; i++) {
        for (int j = 0; j < n; j++) {
            if (grid[i][j] == '1') {
                land++;
                int[][] dirs = {{-1, 0}, {1, 0}, {0, -1}, {0, 1}};
                for (int[] d : dirs) {
                    int ni = i + d[0], nj = j + d[1];
                    if (ni >= 0 && ni < m && nj >= 0 && nj < n && grid[ni][nj] == '1') {
                        if (uf.union(i * n + j, ni * n + nj)) land--;
                    }
                }
            }
        }
    }
    return land;
}
```
（BFS/DFS 也可解，此为并查集示例）

---

## 六、最小生成树（MST）

### Kruskal（基于并查集）

```java
public int kruskalMst(int n, int[][] edges) {
    Arrays.sort(edges, (a, b) -> a[2] - b[2]);  // 按权重排序
    UnionFind uf = new UnionFind(n);
    int total = 0, count = 0;
    for (int[] e : edges) {
        if (uf.union(e[0], e[1])) {
            total += e[2];
            if (++count == n - 1) break;
        }
    }
    return count == n - 1 ? total : -1;
}
```
O(E log E)。适合稀疏图。

### Prim（基于堆）

```java
public int primMst(List<List<int[]>> graph) {
    int n = graph.size();
    boolean[] inMst = new boolean[n];
    PriorityQueue<int[]> pq = new PriorityQueue<>((a, b) -> a[1] - b[1]);
    pq.offer(new int[]{0, 0});  // 从 0 开始
    int total = 0, count = 0;
    while (!pq.isEmpty() && count < n) {
        int[] cur = pq.poll();
        int u = cur[0], w = cur[1];
        if (inMst[u]) continue;
        inMst[u] = true;
        total += w;
        count++;
        for (int[] e : graph.get(u)) {
            if (!inMst[e[0]]) pq.offer(e);
        }
    }
    return count == n ? total : -1;
}
```
O((V+E) log V)。适合稠密图。

---

## 七、强连通分量（SCC）

有向图中，任意两点互相可达。

### Tarjan 算法（一次 DFS）

```java
int[] dfn, low;
int time = 0;
Deque<Integer> stack = new ArrayDeque<>();
boolean[] onStack;
List<List<Integer>> sccs = new ArrayList<>();

void tarjan(int u, List<List<Integer>> graph) {
    dfn[u] = low[u] = ++time;
    stack.push(u);
    onStack[u] = true;
    for (int v : graph.get(u)) {
        if (dfn[v] == 0) {
            tarjan(v, graph);
            low[u] = Math.min(low[u], low[v]);
        } else if (onStack[v]) {
            low[u] = Math.min(low[u], dfn[v]);
        }
    }
    if (low[u] == dfn[u]) {
        List<Integer> scc = new ArrayList<>();
        int v;
        do {
            v = stack.pop();
            onStack[v] = false;
            scc.add(v);
        } while (v != u);
        sccs.add(scc);
    }
}
```

### Kosaraju 算法
1. 原图 DFS 得到完成顺序
2. 反图按完成顺序倒序 DFS，每次 DFS 得一个 SCC

---

## 八、二分图判定与匹配

### 判定（染色法）

```java
public boolean isBipartite(List<List<Integer>> graph) {
    int n = graph.size();
    int[] color = new int[n];  // 0 未染, 1/-1 两种颜色
    for (int i = 0; i < n; i++) {
        if (color[i] != 0) continue;
        Queue<Integer> q = new LinkedList<>();
        q.offer(i);
        color[i] = 1;
        while (!q.isEmpty()) {
            int u = q.poll();
            for (int v : graph.get(u)) {
                if (color[v] == 0) {
                    color[v] = -color[u];
                    q.offer(v);
                } else if (color[v] == color[u]) return false;
            }
        }
    }
    return true;
}
```

### 匈牙利算法（二分图最大匹配）

```java
int[] match;  // match[右点] = 左点

public int hungarian(int leftSize, int rightSize, List<List<Integer>> graph) {
    match = new int[rightSize];
    Arrays.fill(match, -1);
    int result = 0;
    for (int u = 0; u < leftSize; u++) {
        boolean[] visited = new boolean[rightSize];
        if (tryMatch(u, graph, visited)) result++;
    }
    return result;
}

boolean tryMatch(int u, List<List<Integer>> graph, boolean[] visited) {
    for (int v : graph.get(u)) {
        if (visited[v]) continue;
        visited[v] = true;
        if (match[v] == -1 || tryMatch(match[v], graph, visited)) {
            match[v] = u;
            return true;
        }
    }
    return false;
}
```

---

## 九、网络流（简介）

### 最大流
从源点 s 到汇点 t 的最大流量。

**Ford-Fulkerson** / **Dinic** / **ISAP** 等算法。

### 最小割
边的最小容量和，删除后 s、t 不连通。

**最大流 = 最小割定理**。

### 应用
- 二分图最大匹配（转化为最大流）
- 任务分配
- 项目选择
- 图像分割

**面试中**：基本只考概念，代码量大，实际手写少。

---

## 十、典型题解析

### 单词接龙（LeetCode 127）

```java
public int ladderLength(String begin, String end, List<String> wordList) {
    Set<String> dict = new HashSet<>(wordList);
    if (!dict.contains(end)) return 0;
    Queue<String> q = new LinkedList<>();
    Set<String> visited = new HashSet<>();
    q.offer(begin);
    visited.add(begin);
    int step = 1;
    while (!q.isEmpty()) {
        int size = q.size();
        for (int i = 0; i < size; i++) {
            String cur = q.poll();
            if (cur.equals(end)) return step;
            char[] chars = cur.toCharArray();
            for (int j = 0; j < chars.length; j++) {
                char old = chars[j];
                for (char c = 'a'; c <= 'z'; c++) {
                    chars[j] = c;
                    String next = new String(chars);
                    if (dict.contains(next) && !visited.contains(next)) {
                        visited.add(next);
                        q.offer(next);
                    }
                }
                chars[j] = old;
            }
        }
        step++;
    }
    return 0;
}
```
BFS 求最短路，关键是如何枚举邻居。

### 网络延迟时间（LeetCode 743）
标准 Dijkstra。

### 课程表（LeetCode 207/210）
标准拓扑排序。

### 除法求值（LeetCode 399）
转化为带权图：a/b = w → 边 a→b 权 w，b→a 权 1/w。查询路径乘积。可用 DFS/BFS 或并查集（带权）。

### 冗余连接（LeetCode 684）
并查集，第一次出现环的边即答案。

### 最小高度树（LeetCode 310）
拓扑排序思路，从叶子向内层层剥离，最后剩余的 1-2 个节点即答案。

### 最便宜航班（LeetCode 787）
限定 K 次中转的最短路：Bellman-Ford 变种，或 Dijkstra + 状态扩展（加中转次数维度）。

---

## 面试高频图论题

1. 岛屿数量（200）
2. 课程表 I/II（207/210）
3. 单词接龙（127）
4. 克隆图（133）
5. 网络延迟时间（743）
6. 冗余连接（684）
7. 除法求值（399）
8. 最小高度树（310）
9. 判断二分图（785）
10. 朋友圈 / 省份数量（547）
11. 被围绕的区域（130）
12. 太平洋大西洋水流（417）
13. 重新安排行程（332）
14. 最便宜航班（787）
15. 连接所有点的最小费用（1584）

---

## 答题技巧

**识别图论题的特征**：
- 有"连接"、"邻接"、"距离"、"最短"、"路径"关键字
- 矩阵网格（隐式图，邻居是上下左右）
- 依赖关系（拓扑）
- 互相转换（状态图，BFS）

**选算法**：
- 无权最短路 → BFS
- 有权非负 → Dijkstra
- 有负权 → Bellman-Ford
- 多源最短 → Floyd
- 依赖顺序 → 拓扑排序
- 连通性 → 并查集或 DFS/BFS
- MST → Kruskal/Prim
- 二分图 → 染色

**复杂度心里有数**：
- V, E 量级 → 选合适算法
- n ≤ 500：可以 Floyd O(n³)
- n ≤ 10⁴：Dijkstra O((V+E) log V)
- E 稀疏 (≈ V)：堆优化 Dijkstra；E 稠密：朴素 Dijkstra 反而可能更快
