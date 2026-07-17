# Java 异常体系

## 1. 异常继承层次

```
Throwable
├── Error（错误，JVM 层，不应捕获）
│   ├── OutOfMemoryError
│   ├── StackOverflowError
│   ├── NoClassDefFoundError
│   ├── VirtualMachineError
│   └── ...
└── Exception
    ├── RuntimeException（运行时异常，Unchecked）
    │   ├── NullPointerException
    │   ├── IndexOutOfBoundsException
    │   ├── IllegalArgumentException
    │   ├── ClassCastException
    │   ├── NumberFormatException
    │   ├── ConcurrentModificationException
    │   ├── UnsupportedOperationException
    │   └── ...
    └── 其他 Exception（Checked，受检）
        ├── IOException
        ├── SQLException
        ├── ClassNotFoundException
        ├── InterruptedException
        └── ...
```

### Error vs Exception
- **Error**：JVM/环境错误，程序无法恢复（OOM、StackOverflow）
- **Exception**：程序可处理的异常

### Checked vs Unchecked

| 维度 | Checked | Unchecked |
|------|---------|-----------|
| 父类 | Exception（非 RuntimeException） | RuntimeException |
| 编译器 | 强制处理（捕获或声明） | 可不处理 |
| 典型 | IOException、SQLException | NPE、IllegalArgument |
| 设计 | 预期可能的异常 | 编程错误 |

---

## 2. Java 异常设计哲学

### Checked 异常的初衷
强迫调用方处理可恢复的异常（文件找不到、网络断开）。

### 为什么现代 Java 趋向 Unchecked

Spring、Hibernate 等框架大量把 Checked 包装成 Runtime：

**Checked 的问题**：
- **污染 API**：`throws IOException` 签名传播到上层
- **Lambda 不友好**：`Function<T, R>` 不能抛 Checked
- **强制 try-catch**：调用方被迫加样板代码
- **异常被吞**：有人写 `catch (Exception e) {}` 摆烂

**解决方式**：
- 业务异常继承 `RuntimeException`
- 统一全局异常处理（Spring `@ControllerAdvice`）
- 调用方按需捕获

### Joshua Bloch 建议
- 可恢复 → Checked
- 编程错误 → Unchecked
- **不确定时倾向 Unchecked**

Kotlin、Scala 完全没有 Checked 异常。

---

## 3. 异常处理

### try-catch-finally

```java
try {
    // 业务
} catch (IOException e) {
    // 处理 IO 异常
} catch (SQLException e) {
    // 处理 SQL
} catch (Exception e) {
    // 兜底
} finally {
    // 清理资源，一定执行
}
```

### 多重捕获（Java 7+）

```java
try {
    ...
} catch (IOException | SQLException e) {
    // 同类处理
}
```

注意：`e` 是隐式 final，不能重新赋值。

### try-with-resources（Java 7+）

```java
try (BufferedReader reader = new BufferedReader(new FileReader("a.txt"));
     FileWriter writer = new FileWriter("b.txt")) {
    // 自动关闭 reader、writer
} catch (IOException e) {
    // 处理
}
```

资源类要实现 `AutoCloseable` / `Closeable`。

**关闭顺序**：与声明顺序**相反**。

### 抛异常

```java
throw new IllegalArgumentException("age must be positive");

// 带 cause（异常链）
throw new BusinessException("操作失败", e);
```

### 自定义异常

```java
public class BusinessException extends RuntimeException {
    private final String code;

    public BusinessException(String code, String message) {
        super(message);
        this.code = code;
    }

    public BusinessException(String code, String message, Throwable cause) {
        super(message, cause);
        this.code = code;
    }

    public String getCode() { return code; }
}
```

---

## 4. finally 陷阱

### finally 覆盖返回值

```java
public int foo() {
    try {
        return 1;
    } finally {
        return 2;  // 返回 2！
    }
}
```

**避免**在 finally 里 `return`。

### finally 吞异常

```java
try {
    throw new RuntimeException("A");
} finally {
    throw new RuntimeException("B");  // A 被丢弃
}
```

try-with-resources 自动用 `addSuppressed` 保留 A。

### finally 不执行的情况
- `System.exit()` 前
- JVM crash
- 无限循环
- 线程被 `kill -9`

---

## 5. 异常链（Exception Chaining）

### 包装异常保留 cause

```java
try {
    callRemote();
} catch (IOException e) {
    throw new BusinessException("remote_call_failed", "调用失败", e);
}
```

### getCause 获取根因

```java
Throwable root = e.getCause();
while (root != null) {
    System.out.println(root);
    root = root.getCause();
}
```

### 打印完整堆栈

```java
e.printStackTrace();
// 输出：
// BusinessException: 调用失败
//     at ...
// Caused by: IOException: Connection refused
//     at ...
```

---

## 6. 生产级异常处理

### Spring 全局异常处理

```java
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(BusinessException.class)
    public ResponseEntity<ApiError> handleBusiness(BusinessException e) {
        log.warn("Business error: {}", e.getMessage());
        return ResponseEntity.badRequest()
            .body(new ApiError(e.getCode(), e.getMessage()));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiError> handleValidation(MethodArgumentNotValidException e) {
        String msg = e.getBindingResult().getFieldErrors().stream()
            .map(err -> err.getField() + ": " + err.getDefaultMessage())
            .collect(Collectors.joining(", "));
        return ResponseEntity.badRequest().body(new ApiError("INVALID", msg));
    }

    @ExceptionHandler(AccessDeniedException.class)
    public ResponseEntity<ApiError> handleDenied(AccessDeniedException e) {
        return ResponseEntity.status(HttpStatus.FORBIDDEN)
            .body(new ApiError("FORBIDDEN", "无权限"));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiError> handleAll(Exception e) {
        log.error("Unexpected error", e);
        return ResponseEntity.internalServerError()
            .body(new ApiError("SERVER_ERROR", "系统繁忙，请稍后"));
    }
}
```

### 统一响应

```java
public record ApiResponse<T>(String code, String message, T data) {
    public static <T> ApiResponse<T> success(T data) {
        return new ApiResponse<>("0", "success", data);
    }
    public static <T> ApiResponse<T> error(String code, String msg) {
        return new ApiResponse<>(code, msg, null);
    }
}
```

---

## 7. 异常最佳实践

### 1. 优先用标准异常
不要什么都包一层自定义：
- 参数非法 → `IllegalArgumentException`
- 状态非法 → `IllegalStateException`
- 不支持 → `UnsupportedOperationException`

### 2. 业务异常用自定义 RuntimeException

```java
public class OrderNotFoundException extends BusinessException {
    public OrderNotFoundException(String orderId) {
        super("ORDER_NOT_FOUND", "订单不存在: " + orderId);
    }
}
```

### 3. 不要用异常做控制流

```java
// 反模式
try {
    int n = Integer.parseInt(s);
    return n;
} catch (NumberFormatException e) {
    return 0;
}

// 正确：业务判断
if (StringUtils.isNumeric(s)) return Integer.parseInt(s);
return 0;
```

异常开销大（栈回溯），只用于**异常**。

### 4. 不要忽略异常

```java
// 反模式
try { ... } catch (Exception e) {}

// 至少记录
catch (Exception e) {
    log.error("operation failed", e);
}
```

### 5. 不要捕获 Throwable / Error

```java
// 反模式
try { ... } catch (Throwable t) {}  // 捕获了 OOM、StackOverflow
```

让 Error 向上冒泡，通常意味着应该退出程序。

### 6. 清理资源用 try-with-resources

```java
// 不要手写 try-finally 关资源，用 try-with-resources
```

### 7. 区分对外和对内错误

```java
// 对外：用户友好
throw new BusinessException("INSUFFICIENT_BALANCE", "余额不足");

// 对内：详细诊断
log.error("account {} balance={}, required={}", accountId, balance, required);
```

### 8. 异常携带足够上下文

```java
// 坏
throw new RuntimeException("not found");

// 好
throw new ResourceNotFoundException(
    "User not found: id=" + userId + ", tenant=" + tenantId);
```

### 9. 区分可重试/不可重试

```java
public class RetryableException extends BusinessException {}
public class NonRetryableException extends BusinessException {}
```

上游按类型决定是否重试。

### 10. 别在 finally 抛异常

```java
// 反模式
finally {
    cleanup();  // 如果 cleanup 抛异常，原始异常被淹没
}

// 好
finally {
    try { cleanup(); } catch (Exception e) { log.error("cleanup failed", e); }
}
```

---

## 8. 常见异常类型详解

### NullPointerException

**Java 14+ Helpful NPE**：
```
java.lang.NullPointerException: Cannot invoke "String.length()" because "s" is null
    at ...
```
JVM 参数：`-XX:+ShowCodeDetailsInExceptionMessages`（Java 14+ 默认开启）。

**规避**：
- `Optional<T>`
- `@Nullable` / `@NonNull` 注解
- Objects.requireNonNull 提前校验
- 避免返回 null（返回空集合）

### ConcurrentModificationException

```java
List<Integer> list = new ArrayList<>(List.of(1,2,3));
for (Integer i : list) {
    if (i == 2) list.remove(i);  // CME!
}

// 解决：用 Iterator.remove() 或 CopyOnWriteArrayList
Iterator<Integer> it = list.iterator();
while (it.hasNext()) {
    if (it.next() == 2) it.remove();
}

// 或 Java 8+
list.removeIf(i -> i == 2);
```

### ClassCastException

```java
Object o = "hello";
Integer n = (Integer) o;  // CCE

// 规避：instanceof（Java 16+ pattern matching）
if (o instanceof Integer n) { ... }
```

### NumberFormatException
`Integer.parseInt("abc")`。

### StackOverflowError
递归过深。解决：改迭代、尾递归（Java 不优化）、增加栈大小 `-Xss`。

### OutOfMemoryError
堆 / Metaspace / 直接内存等 OOM。
```
java.lang.OutOfMemoryError: Java heap space
java.lang.OutOfMemoryError: Metaspace
java.lang.OutOfMemoryError: Direct buffer memory
java.lang.OutOfMemoryError: unable to create new native thread
```

---

## 9. 异常性能

### 创建异常成本
主要是**填充栈**（O(深度)）。

```java
throw new RuntimeException("");  // 栈慢
throw new RuntimeException("") {
    @Override public Throwable fillInStackTrace() { return this; }
};  // 跳过栈，但调试困难
```

### 控制流用异常的代价
比正常代码慢数百倍。不要用异常做循环控制。

### JIT 优化
频繁抛异常时 JIT 会预编译快路径，但冷路径仍然慢。

---

## 10. 日志记录异常

### 推荐方式

```java
try {
    ...
} catch (Exception e) {
    log.error("Failed to process order {}", orderId, e);  // 末尾 Throwable
}
```

SLF4J 识别最后的 `Throwable`，会打印完整堆栈。

### 避免的反模式

```java
// 丢堆栈
log.error("error: " + e.getMessage());

// 重复记录 + 抛出
catch (Exception e) {
    log.error("error", e);
    throw new RuntimeException(e);  // 上层会再 log 一次
}
```

原则：**记录一次，在能做有意义处理的层**。

### MDC 加上下文

```java
MDC.put("orderId", orderId);
MDC.put("userId", userId);
try {
    ...
} catch (Exception e) {
    log.error("process failed", e);  // 日志自带 orderId、userId
} finally {
    MDC.clear();
}
```

---

## 11. 异常监控

### Sentry / Bugsnag / 阿里 ARMS
自动收集异常，去重、统计、告警。

### Prometheus
```java
Counter exceptionCounter = Counter.build()
    .name("app_exceptions_total")
    .labelNames("type")
    .register();

catch (Exception e) {
    exceptionCounter.labels(e.getClass().getSimpleName()).inc();
    throw e;
}
```

### 告警阈值
- 单种异常 > 10/分钟
- 错误率 > 1%
- 关键异常即时告警

---

## 面试高频问题

**Q1：Error 和 Exception 区别？**

- **Error**：JVM 内部严重错误，程序无法恢复（OOM、StackOverflow）。**不应捕获**
- **Exception**：程序可处理的异常。捕获并处理

都继承自 `Throwable`。

**Q2：Checked 和 Unchecked 异常区别？为什么现代倾向 Unchecked？**

- **Checked**：继承 Exception（非 RuntimeException），**编译器强制处理**
- **Unchecked**：继承 RuntimeException，可不处理

**倾向 Unchecked**：
- Checked 污染 API，`throws IOException` 传播
- Lambda / Stream 不支持 Checked
- 大量样板 try-catch
- 被吞的情况多
- Kotlin、Scala 已去除 Checked

Spring、JDBC 4.0+ 等框架把 Checked 包装为 Runtime。

**Q3：try-catch-finally 执行顺序？finally 一定执行吗？**

- 正常：try → finally
- 异常：try（到异常）→ catch → finally
- catch 内抛出：catch 未完部分不执行 → finally → 向上抛

**finally 不执行**：
- `System.exit()` 调用
- JVM crash
- `Thread.stop()`（已废弃）
- 进程被杀

**避免**：finally 内 return、throw（会覆盖 try 的结果）。

**Q4：try-with-resources 原理？**

Java 7 语法糖，编译器展开为：
```java
try {
    Resource r = ...;
    try { ... }
    finally { r.close(); }
}
```

**优势**：
- 自动关闭，不忘
- 抛异常时 close 异常不淹没原异常（`addSuppressed`）
- 多资源倒序关闭

资源类需实现 `AutoCloseable`。

**Q5：自定义异常如何设计？**

```java
public abstract class BusinessException extends RuntimeException {
    private final String code;
    public BusinessException(String code, String msg) { super(msg); this.code = code; }
    public BusinessException(String code, String msg, Throwable cause) { super(msg, cause); this.code = code; }
    public String getCode() { return code; }
}

public class OrderNotFoundException extends BusinessException {
    public OrderNotFoundException(String orderId) {
        super("ORDER_NOT_FOUND", "订单不存在: " + orderId);
    }
}
```

**原则**：
- 继承 RuntimeException
- 带业务 code（前端/对接识别）
- 提供 cause 构造器
- 粒度适中（不要每种错一个异常）

**Q6：NullPointerException 如何规避？**

- `Optional<T>` 表示可空
- 参数校验：`Objects.requireNonNull(x, "x 不能为空")`
- `@Nullable`/`@NonNull` 注解 + 静态检查
- 避免返回 null（返回空集合/Optional）
- Java 14+ Helpful NPE 定位精确

**Q7：ConcurrentModificationException 什么时候抛？**

遍历集合时修改集合结构（add/remove）触发。
- `for-each` 中 `list.remove(x)` → CME
- 原因：迭代器 modCount 校验失败

**解决**：
- `Iterator.remove()`
- `list.removeIf(...)`（Java 8+）
- `CopyOnWriteArrayList`（并发）
- 另建集合后替换

**Q8：throw 和 throws 区别？**

- `throw` 是**语句**：在代码中主动抛异常
- `throws` 是**声明**：方法签名声明可能抛的 Checked 异常

```java
public void read() throws IOException {
    if (...) throw new IOException();
}
```

**Q9：异常性能开销多大？**

- 创建异常时填充栈（fillInStackTrace）慢
- 栈越深越慢
- 抛出/捕获比正常返回慢 10-1000 倍

**优化**（性能敏感场景）：
- 缓存常量异常
- 覆盖 `fillInStackTrace` 返回 this（不推荐，调试困难）
- **根本**：不要用异常做控制流

**Q10：全局异常处理怎么设计？**

Spring 方式：
```java
@RestControllerAdvice
public class GlobalHandler {
    @ExceptionHandler(BusinessException.class)
    public ApiError handle(BusinessException e) { ... }

    @ExceptionHandler(Exception.class)
    public ApiError handleAll(Exception e) { log.error("unexpected", e); ... }
}
```

**原则**：
- 按异常类型分发
- 业务异常记 warn，系统异常记 error
- 统一响应格式（含 code、message、traceId）
- 敏感信息不暴露给前端
- 配合 Prometheus / Sentry 监控
