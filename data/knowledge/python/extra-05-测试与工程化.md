# Python 测试与工程化：pytest、mock、ruff、CI/CD

工程化能力是后端工程师的核心区分项。pytest + ruff + mypy + uv + pre-commit + GitHub Actions 是当下事实标准栈。

## 1. pytest 核心

### 1.1 基本写法

```python
def test_add():
    assert 1 + 1 == 2

def test_raises():
    with pytest.raises(ValueError, match="invalid"):
        raise ValueError("invalid input")
```

```bash
pytest                    # 运行所有
pytest tests/api/         # 指定目录
pytest -k "test_login"    # 按名字筛选
pytest -m slow            # 按 marker 筛选
pytest -v --tb=short      # 详细 + 简短 traceback
pytest -x                 # 第一个失败就停
pytest --lf               # 只跑上次失败的
pytest -n 4               # 并行 4 workers（需 pytest-xdist）
```

### 1.2 Fixture

```python
import pytest

@pytest.fixture
def user():
    return User(id=1, name="Alice")

@pytest.fixture
def db():
    conn = create_test_db()
    yield conn
    conn.close()

def test_user_in_db(user, db):
    db.add(user)
    assert db.get(1) == user
```

scope：function（默认）/ class / module / session。

### 1.3 参数化

```python
@pytest.mark.parametrize("input,expected", [
    ("hello", "HELLO"),
    ("", ""),
    ("123", "123"),
])
def test_upper(input, expected):
    assert input.upper() == expected
```

### 1.4 标记

```python
@pytest.mark.slow
@pytest.mark.integration
def test_full_pipeline():
    ...

# pytest.ini 注册
# [pytest]
# markers =
#     slow: 慢测试，CI 上跑
#     integration: 集成测试
```

### 1.5 conftest.py

共享 fixture 的位置。`tests/conftest.py` 自动加载，子目录可覆盖。

```python
# tests/conftest.py
@pytest.fixture(scope="session")
def app():
    from app.main import app
    return app

@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)
```

## 2. Mock

### 2.1 unittest.mock

```python
from unittest.mock import Mock, patch, MagicMock

# 替换属性
@patch("app.services.openai_client")
def test_chat(mock_openai):
    mock_openai.chat.completions.create.return_value = Mock(
        choices=[Mock(message=Mock(content="hello"))]
    )
    result = chat("hi")
    assert result == "hello"
    mock_openai.chat.completions.create.assert_called_once()
```

### 2.2 pytest-mock

```python
def test_chat(mocker):
    mock = mocker.patch("app.services.call_llm")
    mock.return_value = "hello"
    assert chat("hi") == "hello"
```

更简洁，自动清理。

### 2.3 异步 mock

```python
mocker.patch("module.async_func", new_callable=AsyncMock)
# 或
mock.return_value = AsyncMock(return_value="result")
```

### 2.4 HTTP mock

```python
# requests
import requests_mock

def test_api():
    with requests_mock.Mocker() as m:
        m.get("https://api.example.com/data", json={"ok": True})
        response = requests.get("https://api.example.com/data")
        assert response.json() == {"ok": True}

# httpx async
from pytest_httpx import HTTPXMock

@pytest.mark.asyncio
async def test_async(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="https://api.example.com/data", json={"ok": True})
    ...
```

### 2.5 LLM mock 策略

```python
@pytest.fixture
def mock_llm(mocker):
    """Replace ChatOpenAI with deterministic responses by query."""
    responses = {}
    def invoke(messages):
        prompt = messages[-1].content
        return Mock(content=responses.get(prompt, "default response"))
    
    mock = mocker.patch("app.services.llm")
    mock.invoke.side_effect = invoke
    mock.set_response = lambda k, v: responses.__setitem__(k, v)
    return mock

def test_agent(mock_llm):
    mock_llm.set_response("hi", "hello!")
    assert agent.run("hi") == "hello!"
```

## 3. 异步测试

```python
# pytest-asyncio
import pytest

@pytest.mark.asyncio
async def test_async_func():
    result = await async_func()
    assert result == 42

# 或在 pytest.ini 设 asyncio_mode = "auto"，自动识别 async def
```

## 4. 覆盖率

```bash
pip install pytest-cov
pytest --cov=app --cov-report=html --cov-report=term-missing
```

```toml
[tool.coverage.run]
source = ["app"]
omit = ["*/tests/*", "*/__init__.py"]

[tool.coverage.report]
exclude_lines = ["pragma: no cover", "raise NotImplementedError"]
```

**经验**：核心业务 80%+，全项目 60%+ 是合理目标。100% 是迷信。

## 5. 测试金字塔

```
        [E2E]  少（慢、贵、易脆）
       /     \
    [Integration]  适量（DB、API）
    /            \
  [Unit]  多（快、稳）
```

- 单元测试：单函数 / 类，无外部依赖（mock 掉）
- 集成测试：跨组件 + 真实 DB / Redis（docker-compose 起依赖）
- E2E：完整业务流程

LLM 应用还要：**回归集**（评估集模拟用户对话），独立于上面三层。

## 6. Linting & Formatting

### 6.1 Ruff（推荐）

Rust 写的极速 linter + formatter。替代 flake8 + isort + black + 部分 mypy。

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = [
    "E", "W",   # pycodestyle
    "F",         # pyflakes
    "I",         # isort
    "B",         # bugbear (常见 bug)
    "C4",        # comprehensions
    "UP",        # pyupgrade (语法升级)
    "SIM",       # simplify
    "RUF",       # ruff specific
]
ignore = ["E501"]  # line too long (Black 已处理)

[tool.ruff.format]
quote-style = "double"
```

```bash
ruff check .       # lint
ruff check --fix . # 自动修
ruff format .      # format
```

### 6.2 mypy

类型检查，见上一章。

### 6.3 pre-commit

git commit 前自动跑：

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.11.2
    hooks:
      - id: mypy
        additional_dependencies: [pydantic]
```

```bash
pre-commit install              # 装 hook
pre-commit run --all-files      # 全量检查
```

## 7. 依赖与环境

### 7.1 uv（新标准）

Rust 写的依赖管理器，比 pip 快 10-100x，统一虚拟环境 + 依赖锁。

```bash
uv init my-project
uv add fastapi pydantic openai
uv add --dev pytest ruff mypy
uv sync          # 安装锁定版本
uv run pytest    # 在虚拟环境跑
```

替代 pip + venv + pip-tools + pipenv + Poetry。Astral（Ruff 作者）出品，社区快速 adoption。

### 7.2 Poetry

老牌依赖管理 + 打包工具。pyproject.toml + poetry.lock。

### 7.3 旧栈：pip + venv + requirements.txt

仍可用，配 pip-tools 生成锁文件：
```bash
pip-compile requirements.in   # → requirements.txt
pip-sync requirements.txt
```

## 8. 打包与发布

### 8.1 现代 pyproject.toml

```toml
[project]
name = "my-package"
version = "1.0.0"
description = "..."
authors = [{name = "Alice"}]
dependencies = ["fastapi>=0.110", "pydantic>=2.0"]
requires-python = ">=3.11"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

```bash
uv build                # 或 python -m build
uv publish              # 上传 PyPI
```

### 8.2 容器化

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev
COPY . .
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0"]
```

多阶段构建减小镜像：

```dockerfile
FROM python:3.12-slim AS builder
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv export --frozen --no-dev -o requirements.txt

FROM python:3.12-slim
COPY --from=builder requirements.txt .
RUN pip install -r requirements.txt
COPY app/ ./app/
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0"]
```

## 9. CI/CD

### 9.1 GitHub Actions

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env: { POSTGRES_PASSWORD: test }
        ports: [5432:5432]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --all-extras
      - run: uv run ruff check .
      - run: uv run mypy app/
      - run: uv run pytest --cov=app
      - uses: codecov/codecov-action@v4
```

### 9.2 自动化发布

打 tag 触发：

```yaml
on:
  push:
    tags: ['v*']
jobs:
  release:
    steps:
      - run: uv build
      - run: uv publish
        env: { UV_PUBLISH_TOKEN: ${{ secrets.PYPI_TOKEN }} }
```

## 10. 高频面试题

**Q1：pytest vs unittest？**
pytest 优势：① 简洁断言（直接 `assert`，框架自动 diff）；② Fixture 系统强大；③ 参数化方便；④ 插件生态丰富（asyncio / cov / mock）。除非维护老代码，新项目选 pytest。

**Q2：什么时候用 mock，什么时候用真实依赖？**
- 单元测试：mock 外部依赖（DB、HTTP、文件系统、时间），保证快速 + 确定
- 集成测试：用真实依赖（docker-compose 起 PG/Redis），验证整链路
- 永远不要在 CI 调真实 LLM API（贵 + 慢 + 不稳定）→ mock 或本地小模型

**Q3：怎么测异步代码？**
pytest-asyncio + `@pytest.mark.asyncio`。mock 用 AsyncMock。httpx 用 pytest-httpx。fixture 也可以是 async。

**Q4：覆盖率多少算够？**
依业务关键性。核心订单 / 支付逻辑 90%+；CRUD 60-70%；脚本 / 临时代码可不测。100% 是过度——很多代码（错误处理、edge case）不值得维护测试。

**Q5：ruff 跟 black + flake8 + isort 比？**
ruff 一个工具 + 一份配置 + 100x 速度。功能覆盖：lint、format、import sort、部分类型检查、安全检查。新项目直接 ruff。

**Q6：uv vs poetry？**
uv 速度碾压 + 配置极简 + 兼容 pyproject.toml + 内置 Python 安装。poetry 生态更成熟、文档完善。新项目 → uv；已用 poetry 且团队熟悉 → 保持。

**Q7：怎么测 LLM 系统？**
四层：① 单元测试 mock LLM（确定性）；② Schema 校验测试（Pydantic 输出）；③ 评估集测试（一组 golden case，跑真实 LLM 算 metrics）；④ 在线 A/B（部分流量 + 监控核心指标）。评估集与单元测试分开，独立 CI job 跑（慢 + 偶发 flaky 需 retry）。
