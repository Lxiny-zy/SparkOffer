# asyncio 深入：事件循环、协程、并发模式

Python 异步是面向"IO 密集 + 高并发"场景的核心利器。Agent 系统天然 IO 密集（LLM API、Vector store、Tool API），必须用好 asyncio 才有吞吐量。

## 1. 同步 vs 异步本质区别

同步：阻塞当前线程等结果。10 个并发请求 = 10 线程 × 等待时间。
异步：发起请求后让出 CPU 给其他任务，回调或 await 时切回。10 个并发请求 = 1 线程 × 最长等待时间。

GIL 让 Python 多线程在 CPU 密集任务上无加速；但 IO 密集任务中，线程在等待 IO 时会释放 GIL，所以多线程也能并发——只是 asyncio 更轻量（协程 KB 级 vs 线程 MB 级）。

## 2. 事件循环（Event Loop）

asyncio 的核心。负责：
- 维护就绪队列（runnable coroutines）
- 维护 IO 注册表（select/epoll/kqueue 监听 fd）
- 调度：取就绪协程跑，IO 阻塞时挂起，IO ready 后唤醒

```python
import asyncio

async def main():
    await asyncio.sleep(1)
    print("done")

asyncio.run(main())   # 启动 event loop、跑 main、跑完关闭
```

**一个线程同时只能跑一个 loop**。子线程要单独 loop。

## 3. 协程、Task、Future 区别

| 概念 | 是什么 | 何时用 |
|---|---|---|
| **Coroutine** | `async def` 调用返回的对象，未启动 | 单纯定义 |
| **Task** | Coroutine 被包到 Task 后已被 loop 调度 | 并发触发，await 取结果 |
| **Future** | 低层抽象，代表"将来会有结果" | 与 Task 互转，集成第三方 |

```python
async def fetch(url): ...

# 直接 await：串行
r1 = await fetch(u1)
r2 = await fetch(u2)

# Task：并行
t1 = asyncio.create_task(fetch(u1))
t2 = asyncio.create_task(fetch(u2))
r1, r2 = await t1, await t2

# gather：批量
results = await asyncio.gather(fetch(u1), fetch(u2), fetch(u3))
```

## 4. 常见并发模式

### 4.1 gather（等所有完成）

```python
results = await asyncio.gather(*[fetch(u) for u in urls])
# 任一异常会向上传，其他任务继续跑
# 想忽略异常：gather(..., return_exceptions=True)
```

### 4.2 wait（更灵活）

```python
done, pending = await asyncio.wait(
    [asyncio.create_task(fetch(u)) for u in urls],
    return_when=asyncio.FIRST_COMPLETED,  # 或 ALL_COMPLETED / FIRST_EXCEPTION
    timeout=5.0,
)
for t in pending:
    t.cancel()  # 取消未完成的
```

### 4.3 as_completed（流式拿结果）

```python
for coro in asyncio.as_completed([fetch(u) for u in urls]):
    result = await coro
    process(result)   # 拿到一个处理一个，不等齐
```

### 4.4 TaskGroup（3.11+，结构化并发）

```python
async with asyncio.TaskGroup() as tg:
    t1 = tg.create_task(fetch(u1))
    t2 = tg.create_task(fetch(u2))
# 离开 with 块时所有 task 必须完成；任一异常其他自动取消
# 比 gather 更安全，推荐
```

### 4.5 Semaphore 限并发

```python
sem = asyncio.Semaphore(10)
async def limited_fetch(url):
    async with sem:
        return await fetch(url)
await asyncio.gather(*[limited_fetch(u) for u in 1000_urls])
# 同时最多 10 个 fetch 在跑
```

## 5. 异步上下文管理 / 迭代

```python
async with aiofiles.open("data.txt") as f:
    async for line in f:
        print(line)
```

实现：`__aenter__/__aexit__` 是 async 方法；`__aiter__/__anext__` 是 async 方法。常用库：`aiohttp`、`aiofiles`、`asyncpg`、`motor`（async MongoDB）。

## 6. 异步生成器（async generator）

```python
async def fetch_paginated(url):
    page = 0
    while True:
        data = await api.get(f"{url}?page={page}")
        if not data:
            break
        for item in data:
            yield item
        page += 1

async for item in fetch_paginated(url):
    process(item)
```

用于流式输出（LLM token stream、文件分块读、分页 API）。

## 7. 同步代码混入异步世界

### 7.1 阻塞代码扔线程池

```python
result = await asyncio.to_thread(blocking_func, arg1, arg2)
```

或 `loop.run_in_executor(None, blocking_func, ...)`。**永远不要在 async 函数里直接调阻塞代码**（如 `time.sleep`、同步 `requests`）——会冻整个 loop。

### 7.2 CPU 密集扔进程池

```python
loop = asyncio.get_running_loop()
with ProcessPoolExecutor() as pool:
    result = await loop.run_in_executor(pool, cpu_heavy_func, data)
```

绕过 GIL。

### 7.3 反向：在同步代码里跑 async

```python
asyncio.run(my_async())   # 顶层
# 已有 loop 时（如 Jupyter）用 nest_asyncio 或专门 API
```

## 8. 异步 LLM 调用示例

```python
import asyncio, openai

client = openai.AsyncOpenAI()

async def ask(question: str) -> str:
    resp = await client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": question}],
    )
    return resp.choices[0].message.content

async def batch_ask(questions: list[str]) -> list[str]:
    sem = asyncio.Semaphore(20)
    async def one(q):
        async with sem:
            return await ask(q)
    return await asyncio.gather(*[one(q) for q in questions])

# 1000 个问题并发请求，限并发 20，总耗时 ≈ 1000/20 × 单次延迟
results = asyncio.run(batch_ask(questions))
```

同步版本要跑 1000 × 单次延迟。提速 20 倍。

## 9. 流式 token 处理

```python
async def stream_chat(question):
    stream = await client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": question}],
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta

async def main():
    async for token in stream_chat("..."):
        print(token, end="", flush=True)
```

## 10. 调试与陷阱

### 10.1 常见错误

- `RuntimeError: This event loop is already running`：在已有 loop 内调 `asyncio.run`。改用 `await`。
- 协程被丢弃（"coroutine was never awaited"）：忘了 `await` 或没 `create_task`。
- `asyncio.gather` 一个异常其他继续跑但结果不可见：用 `return_exceptions=True` 或 TaskGroup。
- 阻塞代码混入：冷启动后 latency 突然变高排查首选项。

### 10.2 调试工具

- `asyncio.run(main(), debug=True)`：开 debug 模式，检测长任务、未 await 协程
- `PYTHONASYNCIODEBUG=1`：环境变量同效果
- `loop.set_debug(True)`：手动开
- `aiomonitor` / `pyinstrument`：性能 profiling

## 高频面试题

**Q1：asyncio 跟多线程区别？什么时候选哪个？**
asyncio：协作式（必须主动 await 让出）、轻量（KB 级）、单线程；适合纯 IO 密集任务且能用 async 库。多线程：抢占式、重（MB 级）、能处理同步阻塞代码（释放 GIL）。**asyncio 适合数千并发的 IO 任务（HTTP、DB、LLM）**；多线程适合少量阻塞代码 + 不愿全量改造。

**Q2：协程怎么实现的？**
基于 generator 加 `async/await` 语法糖。`async def` 编译成 generator function，`await` 编译成 `yield from`。事件循环 send 值进 generator、生成器 yield 出 Future 告诉 loop 等什么，loop 调度其他任务直到 Future ready。

**Q3：怎么避免 asyncio 死锁？**
- 不要在协程内调阻塞函数（用 `to_thread`）
- 锁的获取/释放成对（用 async with）
- 限制 Semaphore 容量大于实际并发需求
- TaskGroup 替代手动管理（异常时自动取消）

**Q4：`gather` 一个任务失败其他怎么办？**
默认：抛异常但其他任务继续跑（不会自动取消）。两种处理：
- `return_exceptions=True`：异常作为返回值
- 用 TaskGroup（3.11+）：任一异常其他自动取消，clean shutdown

**Q5：协程能在多线程间迁移吗？**
不能跨 loop 迁移。每个协程绑定到创建它的 loop。跨线程通信用 `asyncio.run_coroutine_threadsafe(coro, loop)`。

**Q6：异步 Web 框架选 FastAPI 还是 Sanic 还是 aiohttp？**
- FastAPI：基于 Starlette + Pydantic，生态最好、类型安全、自动 OpenAPI。生产首选。
- Sanic：极简快速，单一目标。
- aiohttp：底层灵活，需要更多自己写。

LLM 应用基本 FastAPI。
