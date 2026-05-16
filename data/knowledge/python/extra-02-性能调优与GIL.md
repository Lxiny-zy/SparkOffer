# Python 性能调优：GIL、多进程、Cython、Profiling

Python 慢是出了名的，但"慢在哪里"和"怎么救"才是面试和实战的关键。本章覆盖 GIL 原理、并行选型、原生加速、Profiling 实战。

## 1. GIL 是什么、为什么存在

GIL（Global Interpreter Lock）= CPython 解释器在执行字节码时持有的全局互斥锁。同一时刻只有一个线程能运行 Python 字节码。

**为什么存在**：
- CPython 内存管理基于引用计数，多线程并发改引用计数需细粒度锁，代价高
- GIL 是个简单粗暴的方案，让单线程 CPython 极快、扩展模块（C 实现）也好写
- 历史包袱：去掉 GIL 会让单线程性能下降 ~40%，且破坏所有依赖 GIL 的 C 扩展

## 2. GIL 真正的影响

**会受影响**：纯 Python CPU 密集任务（数学计算、字符串处理、解析器）。多线程无加速。

**不受影响**：
- IO 密集：等待 IO 时 GIL 释放，多线程能并发
- C 扩展释放 GIL：NumPy / Pandas / TensorFlow / asyncpg 等大头计算释放 GIL，多线程能用满 CPU
- 多进程：每进程独立 GIL，互不干扰

```python
# CPU 密集，多线程无加速
def cpu_bound():
    return sum(i*i for i in range(10_000_000))

# IO 密集，多线程提速明显
def io_bound():
    return requests.get(url).text
```

## 3. 并行方案选型

| 方案 | 适用 | 优点 | 缺点 |
|---|---|---|---|
| **threading** | IO 密集 + 阻塞 API | 共享内存、轻量 | 受 GIL 限、纯 CPU 无加速 |
| **asyncio** | IO 密集 + async 库 | 极轻量、可扩展到数万并发 | 需要全链路 async |
| **multiprocessing** | CPU 密集 | 真并行 | 进程开销大、IPC 慢 |
| **concurrent.futures** | 通用 | 接口一致（Thread/Process pool） | 略 abstract |
| **Joblib** | NumPy 数值并行 | 优化共享内存数据 | 不通用 |
| **Ray / Dask** | 集群级并行 | 分布式 | 重 |

**经验决策树**：
1. 任务是 IO 密集？有 async 库？→ asyncio
2. 任务是 IO 密集？无 async 库？→ threading
3. 任务是 CPU 密集？数据小？→ multiprocessing
4. 任务是 CPU 密集？数据大？→ Ray / Dask
5. NumPy 密集运算？→ 已经多线程，单进程就够

## 4. multiprocessing 实战

```python
from concurrent.futures import ProcessPoolExecutor

def heavy(n):
    return sum(i*i for i in range(n))

with ProcessPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(heavy, [10_000_000] * 8))
```

**陷阱**：
- 函数和参数必须可 pickle（lambda、closure、open file 都不行）
- 大数据 IPC 成本高（用 shared memory：`multiprocessing.shared_memory` 或 numpy memmap）
- Windows 下进程启动慢（spawn 方式，重新 import 所有模块）

## 5. 原生加速

### 5.1 NumPy / Pandas 向量化

```python
# ❌ 慢
result = [x * 2 + 1 for x in data]

# ✓ 快 100x（C 实现 + SIMD）
result = data * 2 + 1
```

向量化是 Python 数值计算第一规则。

### 5.2 Numba JIT

装饰一下就编译成机器码：

```python
from numba import jit

@jit(nopython=True, parallel=True)
def heavy(arr):
    return np.sum(arr ** 2)
```

适合数值计算热点。

### 5.3 Cython

写带类型注解的 .pyx 文件编译成 C 扩展：

```cython
def compute(double[:] arr):
    cdef int i
    cdef double total = 0
    for i in range(arr.shape[0]):
        total += arr[i] ** 2
    return total
```

效果接近 C，但开发成本高。

### 5.4 mypyc

把 mypy 类型注解的 Python 编译成 C 扩展。无需改语法。Black、mypy 自己都用。

### 5.5 ctypes / cffi

调用已有 C 库的快速通道。

### 5.6 Rust 扩展（PyO3 / Maturin）

性能与 C 持平，安全性更好。Polars、Pydantic v2、Ruff 都是 Rust 写的。新热点。

## 6. PyPy

CPython 的 JIT 替代实现。纯 Python 代码运行速度 3-10x。代价：
- C 扩展兼容性差（NumPy 慢、PyTorch 不支持）
- 启动慢、内存占用高
- Web 服务 / 长跑脚本受益，CLI / 短任务不划算

## 7. Profiling

### 7.1 找瓶颈：cProfile

```bash
python -m cProfile -o out.prof script.py
python -m pstats out.prof
# 在交互式 shell 里 sort cumulative / stats 20
```

或 snakeviz 可视化：`snakeviz out.prof`。

### 7.2 行级：line_profiler

```python
@profile  # 需要 kernprof 启动
def slow_function():
    ...
```

```bash
kernprof -l -v script.py
# 输出每行耗时
```

### 7.3 内存：memory_profiler / tracemalloc

```python
from memory_profiler import profile

@profile
def f():
    x = [1] * 10**7
    return x
```

```python
import tracemalloc
tracemalloc.start()
# ... code ...
snapshot = tracemalloc.take_snapshot()
for stat in snapshot.statistics("lineno")[:10]:
    print(stat)
```

### 7.4 火焰图：py-spy

无需修改代码、对生产进程无侵入：

```bash
py-spy top --pid 12345
py-spy record -o profile.svg --pid 12345
```

生产 profiling 首选。

### 7.5 现代工具：scalene

CPU + 内存 + GPU 一体化，行级精度，对生产无负担。

## 8. 优化通用法则

1. **测量先于优化**：别瞎猜，profile 找瓶颈
2. **算法优于实现**：O(n²) → O(n log n) 比任何语言优化都强
3. **缓存优先**：`functools.lru_cache` 一行救命
4. **数据结构选对**：set 查找 O(1) vs list O(n)；defaultdict 省判断
5. **避免重复计算**：循环里别反复调相同函数
6. **生成器代替列表**：内存友好
7. **批量操作代替循环**：`list.extend(items)` vs 多次 append
8. **C 扩展优先**：NumPy / re / json / pickle 都是 C
9. **JIT / 编译**：Numba / Cython / mypyc 提升热点
10. **多进程并行**：搞不定单进程时

## 9. 内存优化

### 9.1 __slots__

```python
class Point:
    __slots__ = ("x", "y")
    def __init__(self, x, y):
        self.x, self.y = x, y
```

省 50% 内存（无 dict 存属性）。百万级对象立竿见影。

### 9.2 弱引用

`weakref.WeakValueDictionary`：缓存对象但不阻止 GC。

### 9.3 生成器 + itertools

```python
# ❌ 占大量内存
data = [transform(x) for x in huge_list]
result = process(data)

# ✓ 流式，常量内存
result = process(transform(x) for x in huge_list)
```

### 9.4 array / numpy

存 100 万个 int，list 占 ~28MB，array 占 ~8MB，numpy 占 ~8MB（且向量化更快）。

## 10. 高频面试题

**Q1：GIL 是什么？为什么 Python 多线程对 CPU 任务没用？**
GIL 是 CPython 解释器锁，同一时刻只允许一个线程执行 Python 字节码。CPU 密集任务每个线程都在抢 GIL，最终还是串行；IO 密集任务因为 IO 等待时释放 GIL，所以多线程能并发。

**Q2：怎么绕开 GIL？**
- 多进程（multiprocessing）：真并行
- C 扩展（NumPy、Cython）在密集计算时释放 GIL
- asyncio：IO 密集场景比线程更轻
- 换实现：PyPy（JIT）、no-GIL Python（3.13 实验）

**Q3：Python 3.13 的 no-GIL 是什么？**
PEP 703 提供可选的 GIL-free 构建。重写引用计数为线程安全（biased reference counting + immortal objects），单线程性能损失 ~10%。生产可用还需 1-2 年生态适配。

**Q4：怎么 profile 线上 Python 服务？**
首选 py-spy（无侵入、对生产无影响）。`py-spy top --pid` 实时查热点；`py-spy record -o flame.svg` 出火焰图。也可以挂 sentry / datadog 的持续 profiling。

**Q5：何时该上 Cython？**
profile 后发现纯 Python 热点（非 NumPy 能 vectorize），且 Numba 不够时考虑。开发成本高，先尝试：算法优化 → vectorize → Numba → Rust(PyO3) → Cython。

**Q6：Pydantic v2 为什么快？**
核心校验逻辑用 Rust 重写（pydantic-core），Python 层只是 thin wrapper。比 v1 快 5-50x，且类型安全。

**Q7：list comprehension 跟 for 循环谁快？**
list comp 略快（字节码优化 + 无 append 函数调用开销），但差距 <2x。重点不是这种微优化，是减少 Python 字节码执行总量（用 NumPy、生成器、C 扩展）。
