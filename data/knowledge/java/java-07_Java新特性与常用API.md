# Java 新特性与常用 API

## Java 8 核心特性

### Lambda 表达式

```java
// Lambda 的本质：函数式接口的匿名实现
// 编译后生成 invokedynamic 指令，由 LambdaMetafactory 动态生成实现类
// 与匿名内部类的区别：
// 1. 匿名内部类编译后生成 Outer$1.class 文件，Lambda 不会
// 2. Lambda 不会生成额外的类（动态生成），性能更好
// 3. Lambda 中的 this 指向外部类，匿名内部类的 this 指向自身

// 传统写法
List<String> names = Arrays.asList("Bob", "Alice", "Charlie");
Collections.sort(names, new Comparator<String>() {
    @Override
    public int compare(String a, String b) {
        return a.compareTo(b);
    }
});

// Lambda 写法
names.sort((a, b) -> a.compareTo(b));

// 方法引用（Method Reference）—— Lambda 的语法糖
names.sort(String::compareTo);

// 四种方法引用形式
// 1. 静态方法引用：ClassName::staticMethod
Function<String, Integer> parser = Integer::parseInt;

// 2. 实例方法引用（通过对象）：instance::method
String str = "hello";
Supplier<Integer> len = str::length;

// 3. 实例方法引用（通过类）：ClassName::method（第一个参数作为调用者）
BiFunction<String, String, Boolean> contains = String::contains;

// 4. 构造方法引用：ClassName::new
Function<String, StringBuilder> creator = StringBuilder::new;
```

### Lambda 捕获变量

```java
// Lambda 可以访问外部变量，但必须是 effectively final（事实不可变）
int factor = 2;
Function<Integer, Integer> multiply = n -> n * factor; // 可以：factor 未被修改

int counter = 0;
// Runnable r = () -> counter++; // 编译错误！counter 被修改了

// 解决方案：使用 AtomicInteger 或数组
AtomicInteger atomicCounter = new AtomicInteger(0);
Runnable r = () -> atomicCounter.incrementAndGet(); // 可以

// 为什么要求 effectively final？
// Lambda 捕获的是变量的副本（值拷贝），而非引用
// 如果允许修改，Lambda 内外看到的值不一致，容易产生 bug
```

### 函数式接口

| 接口 | 参数 | 返回 | 用途 | 示例 |
|------|------|------|------|------|
| `Supplier<T>` | 无 | T | 工厂方法 | `() -> new User()` |
| `Consumer<T>` | T | 无 | 消费处理 | `System.out::println` |
| `Function<T,R>` | T | R | 类型转换 | `String::length` |
| `Predicate<T>` | T | boolean | 条件判断 | `s -> s.isEmpty()` |
| `BiFunction<T,U,R>` | T, U | R | 双参数转换 | `(a, b) -> a + b` |
| `UnaryOperator<T>` | T | T | 一元运算 | `s -> s.toUpperCase()` |
| `BinaryOperator<T>` | T, T | T | 二元运算 | `Integer::sum` |

```java
// 自定义函数式接口
@FunctionalInterface // 编译器检查只有一个抽象方法
public interface Transformer<T, R> {
    R transform(T input);

    // default 和 static 方法不影响函数式接口的定义
    default <V> Transformer<T, V> andThen(Transformer<R, V> after) {
        return t -> after.transform(this.transform(t));
    }
}

// 组合使用
Transformer<String, Integer> length = String::length;
Transformer<String, String> describe = length.andThen(len -> "Length: " + len);
```

### Stream API 深度解析

```java
List<User> users = getUsers();

// 过滤 + 映射 + 收集
List<String> names = users.stream()
    .filter(u -> u.getAge() > 18)         // 过滤（惰性操作）
    .sorted(Comparator.comparing(User::getAge))  // 排序
    .map(User::getName)                    // 映射（惰性操作）
    .distinct()                            // 去重
    .limit(10)                             // 限制数量
    .collect(Collectors.toList());         // 收集（终端操作，触发执行）

// 分组
Map<String, List<User>> byCity = users.stream()
    .collect(Collectors.groupingBy(User::getCity));

// 多级分组
Map<String, Map<Integer, List<User>>> byCityAndAge = users.stream()
    .collect(Collectors.groupingBy(User::getCity,
             Collectors.groupingBy(User::getAge)));

// 分组 + 统计
Map<String, Long> countByCity = users.stream()
    .collect(Collectors.groupingBy(User::getCity, Collectors.counting()));

// 分组 + 求和
Map<String, Double> salaryByCity = users.stream()
    .collect(Collectors.groupingBy(User::getCity,
             Collectors.summingDouble(User::getSalary)));

// 统计信息
DoubleSummaryStatistics stats = users.stream()
    .mapToDouble(User::getSalary)
    .summaryStatistics();
// stats.getAverage(), stats.getMax(), stats.getMin(), stats.getCount(), stats.getSum()

// reduce 聚合
int total = numbers.stream().reduce(0, Integer::sum);
Optional<Integer> max = numbers.stream().reduce(Integer::max);

// flatMap 扁平化
List<List<Integer>> nested = List.of(List.of(1,2), List.of(3,4));
List<Integer> flat = nested.stream()
    .flatMap(Collection::stream)
    .collect(Collectors.toList()); // [1, 2, 3, 4]

// 转 Map
Map<Long, User> userMap = users.stream()
    .collect(Collectors.toMap(User::getId, Function.identity(),
        (existing, replacement) -> existing)); // 第三个参数处理 key 冲突

// toUnmodifiableList / toUnmodifiableMap（Java 10+）
List<String> immutableNames = users.stream()
    .map(User::getName)
    .collect(Collectors.toUnmodifiableList());

// Collectors.joining
String csv = users.stream()
    .map(User::getName)
    .collect(Collectors.joining(", ", "[", "]")); // [Alice, Bob, Charlie]

// partitioningBy 分区（按 boolean 分两组）
Map<Boolean, List<User>> partition = users.stream()
    .collect(Collectors.partitioningBy(u -> u.getAge() > 18));
```

**Stream 的中间操作与终端操作：**

| 类型 | 方法 | 说明 |
|------|------|------|
| 中间操作（惰性） | filter, map, flatMap, sorted, distinct, peek, limit, skip | 返回新 Stream，不触发执行 |
| 终端操作（触发） | collect, forEach, reduce, count, min, max, anyMatch, allMatch, findFirst | 触发流水线执行，返回结果 |

**parallelStream 注意事项：**

```java
// 并行流使用 ForkJoinPool.commonPool()
// 注意事项：
// 1. 数据源必须支持高效拆分（ArrayList 好，LinkedList 差）
// 2. 操作不能有副作用（不能修改共享状态）
// 3. 小数据量不要用并行（线程调度开销 > 并行收益）
// 4. IO 操作不适合用并行流（commonPool 线程有限）

// 错误：有副作用的并行操作
List<String> results = Collections.synchronizedList(new ArrayList<>());
stream.parallel().forEach(results::add); // 顺序不确定！

// 正确：使用 collect
List<String> results2 = stream.parallel().collect(Collectors.toList());

// 自定义并行流的线程池
ForkJoinPool customPool = new ForkJoinPool(8);
List<String> result = customPool.submit(() ->
    list.parallelStream()
        .map(this::expensiveOperation)
        .collect(Collectors.toList())
).get();
```

### Optional 最佳实践

```java
// Optional 的正确使用
Optional<User> user = findUserById(id);

// 链式处理
String cityName = user
    .map(User::getAddress)
    .map(Address::getCity)
    .map(City::getName)
    .orElse("未知城市");

// 存在时执行
user.ifPresent(u -> System.out.println(u.getName()));

// Java 9+ ifPresentOrElse
user.ifPresentOrElse(
    u -> System.out.println("Found: " + u.getName()),
    () -> System.out.println("Not found")
);

// 不存在时提供默认值
User u = user.orElse(new User("默认")); // 无论是否存在，都会创建默认对象
User u2 = user.orElseGet(() -> new User("默认")); // 只在不存在时才创建（推荐）

// 不存在时抛异常
User u3 = user.orElseThrow(() -> new NotFoundException("用户不存在"));
User u4 = user.orElseThrow(); // Java 10+，抛 NoSuchElementException

// Java 9+ or()
Optional<User> result = user.or(() -> findUserByName(name));

// Java 9+ stream()
List<User> users = optionalList.stream()
    .flatMap(Optional::stream) // 过滤掉 empty
    .collect(Collectors.toList());

// Optional 的反模式（不要这样做！）
// 1. 不要用 Optional 作为方法参数
void bad(Optional<String> name) { } // 不好
void good(String name) { } // 好，用 @Nullable 注解

// 2. 不要用 Optional 作为类的字段（不可序列化）
class User {
    Optional<String> nickname; // 不好
    String nickname; // 好，null 表示没有
}

// 3. 不要用 isPresent() + get()
if (user.isPresent()) {
    return user.get(); // 不好，和 null 检查没区别
}
return user.orElse(defaultUser); // 好

// 4. 不要用 Optional.of(null)
Optional.of(null);       // NPE!
Optional.ofNullable(null); // Optional.empty()
```

### 日期时间 API（java.time）

```java
// 不可变、线程安全（替代 Date、Calendar、SimpleDateFormat）

// 基本类型
LocalDate date = LocalDate.now();              // 2024-01-15
LocalDate birthday = LocalDate.of(1995, 6, 15);
LocalTime time = LocalTime.now();              // 14:30:00
LocalDateTime dateTime = LocalDateTime.now();  // 2024-01-15T14:30:00
ZonedDateTime zoned = ZonedDateTime.now(ZoneId.of("Asia/Shanghai"));
Instant instant = Instant.now();               // 时间戳（UTC）

// 格式化
DateTimeFormatter formatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
String formatted = dateTime.format(formatter);
LocalDateTime parsed = LocalDateTime.parse("2024-01-15 14:30:00", formatter);

// 计算
LocalDate nextWeek = date.plusWeeks(1);
LocalDate lastMonth = date.minusMonths(1);
long daysBetween = ChronoUnit.DAYS.between(date1, date2);
boolean isAfter = date1.isAfter(date2);

// Duration（时间段：小时、分钟、秒）
Duration d = Duration.between(time1, time2);
Duration twoHours = Duration.ofHours(2);
long totalSeconds = d.getSeconds();

// Period（日期段：年、月、日）
Period p = Period.between(date1, date2);
Period halfYear = Period.ofMonths(6);

// 时区转换
ZonedDateTime shanghaiTime = ZonedDateTime.now(ZoneId.of("Asia/Shanghai"));
ZonedDateTime newYorkTime = shanghaiTime.withZoneSameInstant(ZoneId.of("America/New_York"));

// 与旧 API 互转
Date legacyDate = Date.from(instant);                   // Instant → Date
Instant backToInstant = legacyDate.toInstant();         // Date → Instant
LocalDateTime ldt = LocalDateTime.ofInstant(instant, ZoneId.systemDefault());
```

## Java 9 - 10 特性

### 模块系统（Java 9，JEP 261）

```java
// module-info.java
module com.myapp {
    requires java.sql;           // 依赖 java.sql 模块
    requires transitive java.logging; // 传递依赖
    exports com.myapp.api;       // 导出包（其他模块可以访问）
    opens com.myapp.internal to com.fasterxml.jackson.databind; // 允许反射
}

// 模块化的好处：
// 1. 强封装：未导出的包对外不可见
// 2. 更小的运行时：只包含需要的模块（jlink 工具）
// 3. 更好的安全性和可维护性
```

### 集合工厂方法（Java 9）

```java
// 创建不可变集合
List<String> list = List.of("a", "b", "c");         // 不可变
Set<Integer> set = Set.of(1, 2, 3);                  // 不可变
Map<String, Integer> map = Map.of("a", 1, "b", 2);   // 不可变

// 超过 10 对键值用 ofEntries
Map<String, Integer> bigMap = Map.ofEntries(
    Map.entry("a", 1),
    Map.entry("b", 2),
    Map.entry("c", 3)
);

// 复制为不可变（Java 10）
List<String> copy = List.copyOf(mutableList);
Map<String, Integer> mapCopy = Map.copyOf(mutableMap);

// 注意：这些集合不允许 null 元素！
List.of(null);        // NullPointerException!
List.of("a").add("b"); // UnsupportedOperationException!
```

### 接口私有方法（Java 9）

```java
interface MyInterface {
    default void publicMethod1() {
        commonLogic(); // 调用私有方法
    }

    default void publicMethod2() {
        commonLogic(); // 复用
    }

    private void commonLogic() { // Java 9 接口私有方法
        System.out.println("Shared logic");
    }
}
```

### var 局部变量推断（Java 10）

```java
// 编译器自动推断类型，只能用于局部变量
var list = new ArrayList<String>();  // ArrayList<String>
var map = Map.of("a", 1, "b", 2);   // Map<String, Integer>
var stream = list.stream();          // Stream<String>

// 适用场景
var reader = new BufferedReader(new FileReader("file.txt")); // 类型太长
var entry : map.entrySet()) { }  // 增强 for 循环

// 不能用于：
// var field = "hello";   // 不能用于类字段
// var param             // 不能用于方法参数
// var result = null;    // 不能推断 null
// var arr = {1, 2, 3};  // 不能推断数组初始化

// Java 11+ Lambda 参数可以用 var
list.stream().map((@NonNull var s) -> s.toUpperCase()); // 可以加注解
```

## Java 11 - 13 特性

### String 新方法（Java 11）

```java
// Java 11 新增
" hello ".strip();          // "hello"（支持 Unicode 空白）
" hello ".stripLeading();   // "hello "
" hello ".stripTrailing();  // " hello"
"".isBlank();               // true（空或只有空白）
"line1\nline2".lines();     // Stream<String>: ["line1", "line2"]
"ha".repeat(3);             // "hahaha"

// 与 trim() 的区别
// trim() 只去除 ASCII 空白（<= U+0020）
// strip() 去除所有 Unicode 空白（包括全角空格等）
```

### 文本块（Java 13 预览，Java 15 正式）

```java
// 多行字符串，保持格式
String json = """
    {
        "name": "张三",
        "age": 25,
        "city": "北京"
    }
    """;

// 结束的 """ 位置决定了左侧缩进的去除量
String html = """
        <html>
            <body>Hello</body>
        </html>
        """; // 去除 8 个空格的公共缩进

// 转义符
String sql = """
    SELECT * FROM users \
    WHERE age > 18 \
    AND city = 'Beijing'\
    """; // \ 连接行（不换行）

String withQuotes = """
    He said "hello" \s
    """; // \s 保留尾部空格（Java 14+）
```

### HttpClient API（Java 11 正式）

```java
// 替代 HttpURLConnection，支持 HTTP/2、WebSocket、异步
HttpClient client = HttpClient.newBuilder()
    .version(HttpClient.Version.HTTP_2)
    .connectTimeout(Duration.ofSeconds(10))
    .followRedirects(HttpClient.Redirect.NORMAL)
    .build();

// 同步请求
HttpRequest request = HttpRequest.newBuilder()
    .uri(URI.create("https://api.example.com/users"))
    .header("Content-Type", "application/json")
    .timeout(Duration.ofSeconds(30))
    .GET()
    .build();

HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
System.out.println(response.statusCode());
System.out.println(response.body());

// 异步请求
CompletableFuture<HttpResponse<String>> futureResponse =
    client.sendAsync(request, HttpResponse.BodyHandlers.ofString());

futureResponse.thenApply(HttpResponse::body)
              .thenAccept(System.out::println);

// POST 请求
HttpRequest postRequest = HttpRequest.newBuilder()
    .uri(URI.create("https://api.example.com/users"))
    .header("Content-Type", "application/json")
    .POST(HttpRequest.BodyPublishers.ofString("""
        {"name": "张三", "age": 25}
        """))
    .build();
```

## Java 14 - 16 特性

### Record 类（Java 14 预览，Java 16 正式）

```java
// Record 自动生成：构造器、getter、equals、hashCode、toString
// 所有字段都是 final（不可变）
public record Point(int x, int y) { }

Point p = new Point(1, 2);
System.out.println(p.x());       // 1（getter 不带 get 前缀）
System.out.println(p.y());       // 2
System.out.println(p);           // Point[x=1, y=2]

// 自定义紧凑构造器（参数验证）
public record Range(int min, int max) {
    public Range { // 紧凑构造器（不写参数列表）
        if (min > max) throw new IllegalArgumentException("min > max");
        // 不需要 this.min = min; 自动赋值
    }
}

// Record 可以：
// - 实现接口
// - 定义静态方法和字段
// - 定义实例方法
// - 自定义构造器

// Record 不能：
// - 继承其他类（隐式继承 java.lang.Record）
// - 被继承（隐式 final）
// - 定义非 static 字段
// - 字段可变（全部 final）

// 使用场景：DTO、值对象、方法返回多个值
public record UserDTO(String name, int age, String email) { }

// Record 与 Lombok @Data 的区别：
// Record 是语言级别，不可变；@Data 是注解处理器，可变
```

### Switch 表达式（Java 14 正式）

```java
// 传统 switch
switch (day) {
    case MONDAY:
    case FRIDAY:
        System.out.println("工作");
        break;
    case SATURDAY:
        System.out.println("休息");
        break;
}

// 新 switch 表达式（Java 14 正式）
String result = switch (day) {
    case MONDAY, FRIDAY -> "工作";
    case SATURDAY, SUNDAY -> "休息";
    default -> "未知";
};

// 多行逻辑用 yield 返回值
int numLetters = switch (day) {
    case MONDAY, FRIDAY, SUNDAY -> 6;
    case TUESDAY -> 7;
    default -> {
        String s = day.toString();
        yield s.length(); // yield 返回值
    }
};
```

### Pattern Matching for instanceof（Java 16 正式）

```java
// 传统写法
if (obj instanceof String) {
    String s = (String) obj;
    System.out.println(s.length());
}

// 新写法：自动转型并绑定变量
if (obj instanceof String s) {
    System.out.println(s.length()); // 直接使用 s
}

// 可以在条件中组合使用
if (obj instanceof String s && s.length() > 5) {
    System.out.println("Long string: " + s);
}

// 在 equals 方法中使用
@Override
public boolean equals(Object o) {
    return o instanceof Point p && x == p.x && y == p.y;
}
```

## Java 17 特性（LTS）

### Sealed Classes 密封类（正式）

```java
// 限制哪些类可以继承/实现
public sealed class Shape permits Circle, Rectangle, Triangle { }

public final class Circle extends Shape {          // final：不可再继承
    double radius;
}
public sealed class Rectangle extends Shape        // sealed：可以继续限制
    permits Square { }
public non-sealed class Triangle extends Shape { } // non-sealed：开放继承

public final class Square extends Rectangle { }

// Sealed 类 + Pattern Matching = 穷尽检查
double area = switch (shape) {
    case Circle c    -> Math.PI * c.radius * c.radius;
    case Rectangle r -> r.width * r.height;
    case Triangle t  -> 0.5 * t.base * t.height;
    // 不需要 default！编译器知道所有子类
};

// 使用场景：
// 1. 领域模型：支付方式（微信、支付宝、银行卡）
// 2. 状态机：订单状态（待支付、已支付、已发货、已完成）
// 3. AST 节点类型
// 4. 替代枚举（枚举不能有状态差异，sealed class 可以）
```

### 其他 Java 17 特性

```java
// 增强的伪随机数生成器
RandomGenerator random = RandomGeneratorFactory.of("L64X128MixRandom").create();
int r = random.nextInt(100);

// 废弃 SecurityManager
// 即将在未来版本移除

// switch 的模式匹配预览（后续版本正式）
```

## Java 21 特性（LTS）

### Pattern Matching for switch（正式）

```java
// switch 中使用模式匹配
String describe(Object obj) {
    return switch (obj) {
        case Integer i when i > 0  -> "正整数: " + i;
        case Integer i             -> "非正整数: " + i;
        case String s              -> "字符串: " + s;
        case int[] arr             -> "数组长度: " + arr.length;
        case null                  -> "null";
        default                    -> "其他: " + obj;
    };
}

// Record Pattern（解构赋值）
record Point(int x, int y) { }
record Circle(Point center, double radius) { }

String describe(Object obj) {
    return switch (obj) {
        case Circle(Point(var x, var y), var r) ->
            "圆心(%d,%d) 半径%.1f".formatted(x, y, r); // 嵌套解构
        default -> "未知";
    };
}

// 与 Sealed Classes 配合实现穷尽匹配
sealed interface Shape permits Circle, Rectangle {}
record Rect(double w, double h) implements Shape {}

double area(Shape shape) {
    return switch (shape) {
        case Circle(Point c, double r) -> Math.PI * r * r;
        case Rect(double w, double h) -> w * h;
        // 编译器知道所有分支已覆盖，不需要 default
    };
}
```

### Virtual Threads 虚拟线程（正式）

```java
// 虚拟线程：JVM 管理的轻量级线程（M:N 调度）
// 传统线程 1:1 映射 OS 线程（约 1MB 栈），虚拟线程约几 KB

// 创建方式1：Thread.ofVirtual()
Thread vt = Thread.ofVirtual().name("vt-", 0).start(() -> {
    System.out.println(Thread.currentThread()); // VirtualThread[#21,vt-0]
});

// 创建方式2：Executors（最推荐）
try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
    // 每个任务一个虚拟线程，不再需要线程池大小调优
    IntStream.range(0, 1_000_000).forEach(i ->
        executor.submit(() -> {
            Thread.sleep(Duration.ofSeconds(1)); // 阻塞不占平台线程
            return callRemoteService(i);
        })
    );
} // 自动等待所有任务完成

// 虚拟线程的优势：
// 1. 轻量：可以创建数百万个
// 2. 阻塞不浪费：sleep/IO 时自动释放底层平台线程
// 3. 兼容：与 Thread API 完全兼容，现有代码几乎无需修改
// 4. 简单：不需要响应式/异步编程，写同步代码享异步性能

// 虚拟线程的注意事项：
// 1. synchronized 中阻塞会 pin 住平台线程 → 改用 ReentrantLock
synchronized (lock) {
    channel.read(buffer); // 会 pin 住平台线程！
}
lock.lock(); try { channel.read(buffer); } finally { lock.unlock(); } // 推荐

// 2. ThreadLocal 可用但需注意内存（百万线程 * 每个 ThreadLocal）
// 3. 不适合 CPU 密集型（没有额外好处，反而有调度开销）
// 4. 线程池概念弱化：每个任务一个虚拟线程，不需要池化

// 虚拟线程 vs 响应式编程（WebFlux）
// 响应式：回调链式编程，调试困难，学习曲线陡峭
// 虚拟线程：同步编程风格，简单直观，性能接近
// 结论：大多数场景虚拟线程可以替代响应式编程
```

### Structured Concurrency 结构化并发（预览）

```java
// 将并发任务组织为子任务，与作用域绑定
// 所有子任务完成后作用域才结束（类似 try-with-resources 管理并发）

try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
    // 并行执行两个子任务
    Subtask<User> userTask = scope.fork(() -> findUser(userId));
    Subtask<List<Order>> orderTask = scope.fork(() -> findOrders(userId));

    scope.join();            // 等待所有子任务完成
    scope.throwIfFailed();   // 有子任务失败则抛异常

    // 所有子任务成功后获取结果
    return new UserProfile(userTask.get(), orderTask.get());
}

// ShutdownOnSuccess：第一个成功就取消其他
try (var scope = new StructuredTaskScope.ShutdownOnSuccess<String>()) {
    scope.fork(() -> fetchFromServiceA());
    scope.fork(() -> fetchFromServiceB());
    scope.fork(() -> fetchFromServiceC());

    scope.join();
    return scope.result(); // 返回第一个成功的结果
}
```

### Scoped Values（预览）

```java
// 替代 ThreadLocal 的新方案，与虚拟线程更配合
// 不可变，作用域明确，无泄漏风险

static final ScopedValue<User> CURRENT_USER = ScopedValue.newInstance();

// 绑定值并在作用域内使用
ScopedValue.where(CURRENT_USER, authenticatedUser)
    .run(() -> {
        processRequest(); // 在此范围内 CURRENT_USER.get() 有效
    });

// 好处（vs ThreadLocal）：
// 1. 不可变 → 线程安全
// 2. 作用域明确 → 无泄漏风险
// 3. 自动传递给子虚拟线程
// 4. 性能更好（不需要 Map 查找）
```

### Sequenced Collections（Java 21 正式）

```java
// 新的接口层次，统一了有序集合的操作
// SequencedCollection：有顺序的 Collection
// SequencedSet：有顺序的 Set
// SequencedMap：有顺序的 Map

SequencedCollection<String> list = new ArrayList<>(List.of("a", "b", "c"));
list.getFirst();      // "a"（替代 list.get(0)）
list.getLast();       // "c"（替代 list.get(list.size()-1)）
list.addFirst("z");   // 头部添加
list.addLast("z");    // 尾部添加
list.reversed();      // 反转视图

SequencedMap<String, Integer> map = new LinkedHashMap<>();
map.firstEntry();    // 第一个键值对
map.lastEntry();     // 最后一个键值对
map.pollFirstEntry(); // 移除并返回第一个
map.sequencedKeySet(); // 有序的 key 集合
```

## Java 版本选择指南

| 版本 | 类型 | 推荐度 | 核心特性 |
|------|------|--------|---------|
| Java 8 | LTS | 仍在大量使用 | Lambda、Stream、Optional、java.time |
| Java 11 | LTS | 推荐升级目标 | var、HttpClient、String 新方法 |
| Java 17 | LTS | 强烈推荐 | Record、Sealed Class、Pattern Matching |
| Java 21 | LTS | 最新推荐 | Virtual Threads、Pattern Matching for switch |

## 常用工具类

### Arrays
```java
Arrays.sort(arr);                    // 排序（基本类型用双轴快排，对象用 TimSort）
Arrays.binarySearch(arr, key);       // 二分查找（需已排序）
Arrays.copyOf(arr, newLength);       // 复制
Arrays.copyOfRange(arr, from, to);   // 范围复制
Arrays.fill(arr, value);             // 填充
Arrays.equals(arr1, arr2);           // 内容比较
Arrays.deepEquals(arr1, arr2);       // 多维数组内容比较
Arrays.asList(1, 2, 3);             // 转 List（固定大小！不可增删）
Arrays.stream(arr);                  // 转 Stream
Arrays.mismatch(arr1, arr2);         // 第一个不同元素的索引（Java 9+）
```

### Collections
```java
Collections.sort(list);              // 排序
Collections.reverse(list);           // 反转
Collections.shuffle(list);           // 打乱
Collections.swap(list, i, j);       // 交换
Collections.rotate(list, distance);  // 旋转
Collections.frequency(list, obj);    // 统计出现次数
Collections.disjoint(c1, c2);       // 是否无交集
Collections.unmodifiableList(list);  // 不可修改视图
Collections.synchronizedList(list);  // 线程安全包装
Collections.singletonList(item);     // 单元素不可变 List
Collections.emptyList();             // 空不可变 List
Collections.nCopies(10, "x");       // n 个相同元素的不可变 List
```

### Objects
```java
Objects.equals(a, b);                // 空安全的 equals
Objects.hashCode(obj);               // 空安全的 hashCode
Objects.hash(field1, field2);        // 多字段 hashCode
Objects.requireNonNull(obj, "不能为空");  // 空值检查（NPE with message）
Objects.requireNonNullElse(obj, defaultValue); // 空值默认值（Java 9+）
Objects.toString(obj, "默认值");     // 空安全的 toString
Objects.isNull(obj);                 // obj == null
Objects.nonNull(obj);                // obj != null
Objects.compare(a, b, comparator);   // 空安全的比较
```

### Math
```java
Math.abs(n);              // 绝对值
Math.max(a, b);           // 最大值
Math.min(a, b);           // 最小值
Math.pow(2, 10);          // 幂运算 2^10 = 1024
Math.sqrt(9);             // 平方根 = 3.0
Math.ceil(3.1);           // 向上取整 = 4.0
Math.floor(3.9);          // 向下取整 = 3.0
Math.round(3.5);          // 四舍五入 = 4
Math.random();            // [0, 1) 随机数
Math.addExact(a, b);      // 溢出安全的加法（溢出抛异常）
Math.multiplyExact(a, b); // 溢出安全的乘法
```

## 踩坑指南

### 1. Stream 只能消费一次
```java
Stream<String> stream = list.stream();
stream.forEach(System.out::println);
stream.forEach(System.out::println); // IllegalStateException! 流已关闭
// 需要重新创建 stream
```

### 2. Optional.of(null) 导致 NPE
```java
Optional.of(null);          // NullPointerException!
Optional.ofNullable(null);  // Optional.empty()（正确）
```

### 3. Collectors.toMap 的 key 冲突
```java
// 如果有重复 key，会抛 IllegalStateException
List<User> users = List.of(new User(1, "A"), new User(1, "B"));
users.stream().collect(Collectors.toMap(User::getId, User::getName));
// IllegalStateException: Duplicate key 1

// 提供合并函数
users.stream().collect(Collectors.toMap(
    User::getId, User::getName,
    (existing, replacement) -> existing)); // 保留第一个
```

### 4. LocalDateTime 没有时区信息
```java
// LocalDateTime 是"本地"时间，没有时区信息
// 存储到数据库或传输时必须考虑时区
LocalDateTime ldt = LocalDateTime.now(); // 本地时间
ZonedDateTime zdt = ldt.atZone(ZoneId.of("Asia/Shanghai")); // 关联时区
Instant instant = zdt.toInstant(); // 转为 UTC 时间戳
// 存储推荐用 Instant 或 ZonedDateTime
```

### 5. Record 的序列化注意事项
```java
// Record 天然支持序列化（实现 Serializable 即可）
public record Point(int x, int y) implements Serializable { }

// Record 的反序列化使用规范构造器（canonical constructor）
// 而非传统的 readObject()，更安全
// 但注意：Jackson 需要特殊配置来支持 Record
```

## 最佳实践

1. **Stream 优先**：集合操作优先用 Stream API，代码更简洁
2. **Optional 返回值**：方法可能返回 null 时返回 Optional，但不要用作字段或参数
3. **不可变集合**：用 `List.of()`、`Map.of()` 创建不可变集合
4. **文本块**：多行字符串（JSON、SQL、HTML）使用文本块
5. **Record 做 DTO**：数据传输对象用 Record 代替 Lombok @Data
6. **Sealed Class 限制继承**：领域模型中明确继承关系
7. **虚拟线程处理 IO**：IO 密集型任务用虚拟线程
8. **java.time 替代 Date**：所有日期时间操作用 java.time 包
9. **var 适度使用**：类型明显时用 var，复杂泛型不要用 var（降低可读性）
10. **跟进 LTS 版本**：生产环境使用 LTS 版本（Java 17 或 21）

## 面试高频问题及详细解答

### Q1：Lambda 表达式的本质？和匿名内部类的区别？
**答**：Lambda 本质是函数式接口的实现。编译时生成 invokedynamic 指令，运行时由 LambdaMetafactory 动态生成实现类。与匿名内部类区别：(1) 匿名内部类编译后生成独立 class 文件，Lambda 不会 (2) Lambda 的 this 指向外部类，匿名内部类的 this 指向自身 (3) Lambda 只能实现函数式接口（一个抽象方法），匿名内部类可以实现任意接口/类 (4) Lambda 性能更好（避免了类加载和对象创建）。

### Q2：Stream 的中间操作和终端操作？parallelStream 的注意事项？
**答**：中间操作（filter/map/sorted 等）是惰性的，只定义操作不执行，返回新 Stream。终端操作（collect/forEach/reduce 等）触发整个流水线执行。parallelStream 注意：(1) 数据源需支持高效拆分（ArrayList好，LinkedList差）(2) 操作不能有副作用 (3) 小数据量不用并行 (4) 默认使用 ForkJoinPool.commonPool，IO操作不适合。

### Q3：Optional 的正确使用方式？什么时候不该用？
**答**：正确用法：方法返回可能为空的值时返回 Optional，配合 map/orElse/ifPresent 链式处理。不该用的场景：(1) 不要作为方法参数 (2) 不要作为类的字段（不可序列化）(3) 不要用 isPresent()+get() 代替 null 检查 (4) 不要用于集合类型（返回空集合而非 Optional）。核心理念：Optional 是为了让 API 设计者明确表达"值可能不存在"的语义。

### Q4：Java 8 到 Java 21 有哪些重要新特性？
**答**：Java 8 (Lambda/Stream/Optional/java.time) → Java 9 (模块化/集合工厂) → Java 10 (var) → Java 11 (HttpClient/String新方法) → Java 14 (Record/switch表达式) → Java 16 (Pattern Matching instanceof) → Java 17 (Sealed Classes) → Java 21 (Virtual Threads/Pattern Matching switch/Sequenced Collections)。LTS 版本：8/11/17/21。

### Q5：虚拟线程和传统线程的区别？适用场景？
**答**：传统平台线程 1:1 映射 OS 线程（约 1MB 栈），受 OS 限制约几千个。虚拟线程由 JVM 管理（M:N 模型），约几 KB 栈，可创建百万个。阻塞时自动释放底层平台线程。适合 IO 密集型（Web 请求、DB 调用），不适合 CPU 密集型。注意：synchronized 中阻塞会 pin 平台线程，建议用 ReentrantLock。简化了并发编程，大多数场景可替代响应式编程。

### Q6：Record 和普通类的区别？
**答**：Record 是不可变的数据载体：(1) 所有字段 final，自动生成构造器/getter/equals/hashCode/toString (2) 不能继承其他类（隐式继承 Record）(3) 不能被继承（隐式 final）(4) 不能定义非 static 字段。适合做 DTO、值对象、方法多返回值。与 Lombok @Data 区别：Record 是语言级别且不可变，@Data 是注解处理器且可变。

### Q7：Sealed Class 的作用和使用场景？
**答**：Sealed Class 限制哪些类可以继承它（permits 子句），子类必须是 final、sealed 或 non-sealed。作用：(1) 配合 Pattern Matching switch 实现穷尽检查（不需要 default）(2) 限制领域模型的继承结构（如支付方式、订单状态）(3) 比枚举灵活（每个子类可以有不同的字段和行为）。

### Q8：Stream 和 for 循环哪个性能好？
**答**：简单操作（遍历/过滤）for 循环稍快（避免 Lambda 开销和对象创建）。复杂操作（分组/多级转换）Stream 代码更简洁易读，性能差异可忽略。大数据量下 parallelStream 可以利用多核。建议：除非性能极其敏感的热路径，优先选择 Stream（可读性 > 微小性能差异）。不要过早优化。

### Q9：Java 时间 API 中 LocalDateTime 和 ZonedDateTime 的区别？
**答**：LocalDateTime 是"本地"日期时间，没有时区信息，适合表示"生日"等与时区无关的时间。ZonedDateTime 包含时区信息，适合需要精确时间点的场景。存储和传输应使用 Instant（UTC 时间戳）或 ZonedDateTime。注意：同一个 LocalDateTime 在不同时区表示的是不同的时刻。

### Q10：Java 各版本的 LTS 策略是什么？生产环境该用哪个版本？
**答**：Oracle 每 6 个月发一个版本，每 2 年一个 LTS（Long Term Support）版本。LTS 版本：8、11、17、21。非 LTS 版本只支持 6 个月。生产环境必须用 LTS 版本。新项目推荐 Java 21（虚拟线程、Pattern Matching、Record 等）；存量项目至少升到 Java 17（License 友好、性能提升、新特性丰富）；Java 8 虽然仍在广泛使用，但已错过很多重要特性。

> **交叉引用**：虚拟线程的并发模型参见 [多线程与并发](./04_多线程与并发.md)；Stream 操作的集合类选择参见 [集合框架](./03_集合框架.md)；Lambda 中捕获的变量与 GC 参见 [JVM](./05_JVM.md)
