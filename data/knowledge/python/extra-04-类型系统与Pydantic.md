# Python 类型系统与 Pydantic v2 实战

类型提示从 Python 3.5 引入到现在已是工程标配。在 Agent / LLM 系统里，类型是「让 LLM 输出可控」「让 tool 调用安全」「让代码可维护」的核心抓手。

## 1. 类型注解基础

### 1.1 基本类型

```python
name: str = "Alice"
age: int = 30
height: float = 1.75
active: bool = True
data: bytes = b"..."
```

### 1.2 容器类型

```python
# Python 3.9+ 直接用内置
users: list[str] = ["alice", "bob"]
scores: dict[str, int] = {"alice": 95}
coords: tuple[float, float] = (1.0, 2.0)
unique: set[int] = {1, 2, 3}

# 旧版本用 typing.List / Dict
from typing import List, Dict
```

### 1.3 可选与联合

```python
# Python 3.10+
def find(name: str) -> User | None: ...
def parse(value: str | int | None) -> str: ...

# 旧版本
from typing import Optional, Union
def find(name: str) -> Optional[User]: ...
```

### 1.4 函数类型

```python
from typing import Callable

def apply(f: Callable[[int, int], int], a: int, b: int) -> int:
    return f(a, b)

# 高阶函数
ProcessorFn = Callable[[dict], dict]
def register(name: str, fn: ProcessorFn): ...
```

## 2. 进阶类型

### 2.1 Literal

枚举值的类型：

```python
from typing import Literal

def order(side: Literal["buy", "sell"], qty: int): ...
order("buy", 100)   # ✓
order("hold", 100)  # ✗ 类型错误
```

### 2.2 TypedDict

带类型的 dict（运行时仍是 dict）：

```python
from typing import TypedDict

class User(TypedDict):
    name: str
    age: int
    email: NotRequired[str]  # Python 3.11+

def process(u: User): ...
```

LangGraph 的 State 就是 TypedDict。

### 2.3 Generic

```python
from typing import TypeVar, Generic

T = TypeVar("T")

class Stack(Generic[T]):
    def __init__(self):
        self.items: list[T] = []
    def push(self, item: T): self.items.append(item)
    def pop(self) -> T: return self.items.pop()

s: Stack[int] = Stack()
s.push(1)        # ✓
s.push("hello")  # ✗
```

### 2.4 Protocol（结构化子类型）

```python
from typing import Protocol

class HasArea(Protocol):
    def area(self) -> float: ...

class Circle:
    def area(self) -> float: return 3.14
class Square:
    def area(self) -> float: return 4.0

def total_area(shapes: list[HasArea]) -> float:
    return sum(s.area() for s in shapes)

total_area([Circle(), Square()])  # ✓ 鸭子类型 + 静态检查
```

无需显式继承，比 ABC 更灵活。

### 2.5 ParamSpec / Concatenate

装饰器保留参数类型：

```python
from typing import ParamSpec, Callable
P = ParamSpec("P")

def log_calls(f: Callable[P, str]) -> Callable[P, str]:
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> str:
        print(f"calling {f.__name__}")
        return f(*args, **kwargs)
    return wrapper

@log_calls
def greet(name: str, lang: str = "en") -> str: ...
greet("Alice", lang="zh")  # 类型完美保留
```

### 2.6 Annotated（附加元数据）

```python
from typing import Annotated
from operator import add

Score = Annotated[int, "0-100 range"]
Messages = Annotated[list[dict], add]   # LangGraph reducer
```

不影响运行时，但工具能读取（Pydantic / LangGraph 都用）。

## 3. Pydantic v2 核心

### 3.1 模型定义

```python
from pydantic import BaseModel, Field, EmailStr

class User(BaseModel):
    id: int
    name: str = Field(min_length=1, max_length=50)
    email: EmailStr
    age: int = Field(default=0, ge=0, le=150)
    tags: list[str] = []
    
    model_config = {"str_strip_whitespace": True}

u = User(id=1, name=" Alice ", email="a@b.com")
print(u.name)  # "Alice" 自动 strip
```

### 3.2 校验器

```python
from pydantic import field_validator, model_validator

class User(BaseModel):
    name: str
    age: int
    
    @field_validator("name")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        return v.strip().title()
    
    @model_validator(mode="after")
    def check_adult(self):
        if self.age < 18:
            raise ValueError("must be adult")
        return self
```

### 3.3 序列化 / 反序列化

```python
u = User(...)
data = u.model_dump()        # dict
json_str = u.model_dump_json()  # str

u2 = User.model_validate(data)        # from dict
u3 = User.model_validate_json(json_str)  # from json str

# 序列化时排除字段
u.model_dump(exclude={"password"})
u.model_dump(include={"name", "email"})
u.model_dump(by_alias=True)  # 用 alias 名
```

### 3.4 Settings（环境变量）

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str
    redis_url: str = "redis://localhost"
    debug: bool = False
    openai_api_key: str = Field(alias="OPENAI_API_KEY")
    
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_")

settings = Settings()
```

### 3.5 嵌套与递归

```python
class Address(BaseModel):
    city: str
    zip: str

class User(BaseModel):
    name: str
    home: Address
    friends: list["User"] = []  # 递归引用

User.model_rebuild()  # 解析前向引用

User(
    name="Alice",
    home={"city": "SH", "zip": "200000"},  # dict 自动转 Address
    friends=[{"name": "Bob", "home": {"city": "BJ", "zip": "100000"}}],
)
```

## 4. LLM 输出结构化

```python
from pydantic import BaseModel
from langchain_openai import ChatOpenAI

class Recipe(BaseModel):
    name: str
    ingredients: list[str]
    steps: list[str]
    cooking_time: int = Field(description="minutes")

llm = ChatOpenAI(model="gpt-4o")
structured = llm.with_structured_output(Recipe)

result: Recipe = structured.invoke("一道番茄炒蛋的做法")
print(result.name, result.ingredients)
```

底层走 function calling，schema 校验不通过自动 retry。比 prompt 里说"返回 JSON"可靠 100 倍。

## 5. 类型检查工具

### 5.1 mypy（事实标准）

```bash
pip install mypy
mypy app/
```

配置 `pyproject.toml`：
```toml
[tool.mypy]
python_version = "3.11"
strict = true
warn_unused_ignores = true
```

### 5.2 pyright / pylance（VS Code 默认）

更快，类型推导更智能。Microsoft 维护。

### 5.3 ruff（lint + format + 部分类型）

Rust 写的，比 flake8/black 快 10-100x。Pydantic 团队用。

```toml
[tool.ruff]
select = ["E", "W", "F", "I", "TID", "UP", "B"]
```

## 6. 类型最佳实践

### 6.1 渐进式引入

不必一次全标。从 public API 开始，逐步深入。`# type: ignore` 临时跳过。

### 6.2 不要 over-engineer

```python
# ❌ 过度泛型
def get_first(items: list[T]) -> T | None: ...

# ✓ 业务清晰
def get_first_user(users: list[User]) -> User | None: ...
```

### 6.3 Any 是逃生舱

`Any` 跟没标一样。第三方库无类型时用 `cast`：

```python
from typing import cast
result = cast(dict, untyped_function())
```

### 6.4 Type 实体优先

```python
# ❌ dict 丧失类型信息
def get_user(id) -> dict: ...

# ✓ Pydantic / dataclass 实体
@dataclass
class User:
    id: str
    name: str

def get_user(id: str) -> User: ...
```

## 7. 高频面试题

**Q1：Python 类型注解运行时有用吗？**
默认无（解释器只存在 `__annotations__` 字典里）。运行时校验需要工具：① Pydantic（模型校验）；② @typeguard 装饰器；③ runtime type checker 库。生产 API 边界用 Pydantic，内部代码靠 mypy 静态检查。

**Q2：Pydantic v2 跟 dataclass 区别？**
- dataclass：纯结构、无校验、零依赖
- Pydantic：自动校验、序列化、JSON Schema 生成
- attrs：类似 dataclass 但更灵活
- TypedDict：dict 加类型，运行时仍是 dict
- NamedTuple：immutable + 索引访问

Agent / API 边界用 Pydantic；内部 DTO 用 dataclass；LangGraph State 用 TypedDict。

**Q3：mypy strict mode 都开了什么？**
`disallow_untyped_defs`、`disallow_any_generics`、`disallow_incomplete_defs`、`no_implicit_optional`、`warn_redundant_casts` 等约 12 项。新项目直接 strict；老项目逐文件加 `# mypy: strict`。

**Q4：怎么让 LLM 输出严格 JSON？**
首选 `llm.with_structured_output(PydanticModel)`，底层走 function calling 强约束。备选：OpenAI `response_format={"type": "json_object"}` + 后置 Pydantic 校验。最差：prompt 里说"返回 JSON" + 后处理（不可靠）。

**Q5：TypedDict vs Pydantic 怎么选？**
- TypedDict：性能敏感、与现有 dict-based 代码兼容、不需要运行时校验 → LangGraph state、缓存数据
- Pydantic：API 边界、需要校验、需要序列化 → 请求/响应、配置、LLM 输出

**Q6：Protocol 比 ABC 好在哪？**
ABC 需要显式继承。Protocol 鸭子类型 —— 任何实现了接口方法的类自动符合，且静态检查能 catch 错误。第三方库不能改源码时 Protocol 更灵活。

**Q7：Pydantic v2 为什么这么快？**
核心 validator 用 Rust 实现（pydantic-core），Python 层只是 thin wrapper。模型构建时把所有 validator 编译成 schema 树，运行时直接走 native code。比 v1 快 5-50x，比 marshmallow / cerberus 等纯 Python 库快得多。
