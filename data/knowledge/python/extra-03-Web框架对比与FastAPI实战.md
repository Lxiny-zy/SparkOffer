# Python Web 框架对比与 FastAPI 实战

Python Web 框架百花齐放，但 LLM 应用 / Agent 系统场景下 FastAPI 已是事实标准。理解它强在哪、坑在哪是面试必备。

## 1. 主流框架定位

| 框架 | 风格 | 异步 | 类型 | 适合 |
|---|---|---|---|---|
| **Django** | 大而全（ORM/Admin/Auth） | 部分 async | 弱 | 传统 Web 应用 |
| **Flask** | 微框架，灵活 | 不原生（需 Quart） | 弱 | 小型 API、原型 |
| **FastAPI** | 现代异步 + 类型 | 原生 | 强 (Pydantic) | API、Agent、微服务 |
| **Sanic** | 极简快速 | 原生 | 弱 | 高性能 API |
| **Starlette** | FastAPI 底层 | 原生 | 弱 | 极致控制 |
| **Litestar** | FastAPI 替代品 | 原生 | 强 | 性能更敏感场景 |
| **Tornado** | 老牌异步 | 自研 IO loop | 弱 | 老项目 |

## 2. FastAPI 核心优势

### 2.1 类型即文档

```python
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class CreateOrder(BaseModel):
    user_id: str
    items: list[str]
    notes: str | None = None

@app.post("/orders")
async def create_order(order: CreateOrder) -> dict:
    return {"id": "abc", "status": "created"}
```

自动生成：
- OpenAPI schema（`/openapi.json`）
- Swagger UI（`/docs`）
- ReDoc（`/redoc`）
- 客户端 SDK 生成

### 2.2 依赖注入

```python
from fastapi import Depends

def get_db():
    db = Session()
    try: yield db
    finally: db.close()

def get_current_user(token: str = Header(), db = Depends(get_db)):
    return decode_user(token, db)

@app.get("/me")
async def me(user = Depends(get_current_user)):
    return user
```

dependency 链式注入，单元测试时 override 简单。

### 2.3 异步原生

```python
@app.get("/llm/{question}")
async def ask(question: str):
    response = await openai_client.chat.completions.create(...)
    return response.choices[0].message
```

直接 await，单 worker 能处理数百并发。

### 2.4 流式响应

```python
from fastapi.responses import StreamingResponse

@app.post("/chat")
async def chat(req: ChatReq):
    async def gen():
        async for token in stream_llm(req.message):
            yield f"data: {token}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
```

SSE / WebSocket 一行搞定。

## 3. 项目结构最佳实践

```
app/
├── main.py            # FastAPI app + 路由注册
├── api/
│   ├── deps.py        # 共享依赖（auth、db）
│   ├── v1/
│   │   ├── users.py   # APIRouter
│   │   └── orders.py
├── core/
│   ├── config.py      # 配置（Pydantic Settings）
│   ├── security.py
├── models/            # ORM models
├── schemas/           # Pydantic models（请求/响应）
├── services/          # 业务逻辑
├── db/                # session / migration
└── tests/
```

按域拆 router，按层分目录。

## 4. 配置管理

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    openai_api_key: str
    debug: bool = False
    
    class Config:
        env_file = ".env"

@lru_cache
def get_settings(): return Settings()

@app.get("/info")
async def info(settings = Depends(get_settings)):
    return {"debug": settings.debug}
```

Pydantic Settings 自动从 .env / 环境变量加载 + 类型校验。

## 5. 中间件

```python
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start
        logger.info(f"{request.method} {request.url} {response.status_code} {duration:.3f}s")
        return response

app.add_middleware(LoggingMiddleware)
```

## 6. 异常处理

```python
from fastapi import HTTPException
from fastapi.responses import JSONResponse

@app.exception_handler(ValueError)
async def value_error_handler(request, exc):
    return JSONResponse(status_code=400, content={"error": str(exc)})

@app.get("/users/{id}")
async def get_user(id: str):
    user = db.find(id)
    if not user:
        raise HTTPException(404, "User not found")
    return user
```

## 7. 后台任务

```python
from fastapi import BackgroundTasks

def send_email(to: str, content: str): ...

@app.post("/notify")
async def notify(req: NotifyReq, bg: BackgroundTasks):
    bg.add_task(send_email, req.to, req.content)
    return {"queued": True}
```

简单后台任务用 BackgroundTasks（响应后才跑）。复杂场景用 Celery / Arq / Dramatiq + Redis。

## 8. WebSocket

```python
from fastapi import WebSocket

@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            response = await process(data)
            await websocket.send_text(response)
    except WebSocketDisconnect:
        pass
```

LLM 实时对话 / 协作场景必备。

## 9. 性能优化

### 9.1 部署

- ASGI Server：**uvicorn**（推荐） / hypercorn
- 多 worker：`uvicorn main:app --workers 4`
- Gunicorn + Uvicorn workers：`gunicorn -k uvicorn.workers.UvicornWorker -w 4`
- 生产 prefork 模式，每 worker 独立进程绕 GIL

### 9.2 数据库

- 异步驱动：**asyncpg** / aiomysql / motor
- ORM：SQLAlchemy 2.0 async / Tortoise / SQLModel（FastAPI 作者）
- 连接池：必开，size = CPU 核数 × 2-4

### 9.3 缓存

- 应用内：functools.lru_cache（同进程）
- 分布式：Redis + fastapi-cache2
- HTTP：响应加 Cache-Control header，前置 nginx / CDN

### 9.4 序列化

- Pydantic v2（默认）
- 极致性能用 msgspec（10x 快但生态弱）

## 10. 监控与可观测性

### 10.1 健康检查

```python
@app.get("/healthz")
async def health():
    return {"status": "ok"}

@app.get("/readyz")
async def ready(db = Depends(get_db)):
    try:
        await db.execute("SELECT 1")
        return {"ready": True}
    except:
        raise HTTPException(503, "DB unavailable")
```

### 10.2 Prometheus 指标

```python
from prometheus_client import Counter, Histogram, make_asgi_app

requests_total = Counter("http_requests_total", "Total requests", ["method", "endpoint"])

@app.middleware("http")
async def metrics(request, call_next):
    requests_total.labels(request.method, request.url.path).inc()
    return await call_next(request)

app.mount("/metrics", make_asgi_app())
```

### 10.3 OpenTelemetry

```python
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
FastAPIInstrumentor.instrument_app(app)
```

trace 自动导出到 Jaeger / Tempo / Datadog。

## 11. 测试

```python
from fastapi.testclient import TestClient

def test_create_order():
    client = TestClient(app)
    response = client.post("/orders", json={"user_id": "u1", "items": ["a"]})
    assert response.status_code == 200

# async 测试
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_ws():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post("/orders", json={...})
```

依赖注入 override 测试隔离：

```python
def override_get_db():
    return mock_db

app.dependency_overrides[get_db] = override_get_db
```

## 12. 高频面试题

**Q1：FastAPI 比 Flask 强在哪？**
① 原生 async 支持，IO 密集任务高吞吐；② Pydantic 类型系统自动校验 + OpenAPI 文档；③ 依赖注入清晰；④ WebSocket / SSE 一等公民；⑤ 性能（基于 Starlette + uvicorn）。

**Q2：Pydantic v1 v2 区别？**
v2 核心校验逻辑用 Rust 重写，性能提升 5-50x。API 变化：`@validator` → `@field_validator`，`Config class` → `model_config dict`，`.dict()` → `.model_dump()`。生产应升级。

**Q3：FastAPI 怎么做 LLM 流式输出？**
StreamingResponse + async generator + `media_type="text/event-stream"`。每个 token yield 一行 SSE 格式（`data: {token}\n\n`）。前端用 EventSource 接收。

**Q4：FastAPI 怎么处理大量并发 LLM 调用？**
① 异步路由 + 异步 OpenAI 客户端；② Semaphore 限并发避免 provider 限流；③ 长任务进队列（Arq / Celery）异步处理 + webhook 通知；④ 多 worker 部署横向扩展。

**Q5：依赖注入有什么好处？**
- 解耦：业务代码不直接 new db、auth，由 framework 注入
- 可测试：测试时 override 依赖（mock db、mock user）
- 复用：同一个 dependency 多处用，自动共享同请求内的实例
- 链式：dependency 可依赖其他 dependency

**Q6：Django 还有市场吗？**
有。优势场景：需要 Admin 后台、传统 CRUD、内容管理（CMS）、批处理脚本。劣势：异步支持迟、笨重、不适合纯 API。新项目纯 API → FastAPI；带后台的 SaaS → 还是 Django + DRF 省心。

**Q7：FastAPI 性能瓶颈在哪？**
通常不在 FastAPI 本身（Starlette + uvicorn 已经很快）。瓶颈：① 数据库（async 驱动 + 连接池）；② 外部 API（LLM / 第三方）；③ 序列化（大对象用 msgspec 或裁剪）；④ Worker 数量与 CPU 不匹配。
