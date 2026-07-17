# 设计模式

## 一、设计原则

### 1.1 SOLID 原则

| 原则 | 全称 | 说明 | 违反示例 |
|------|------|------|----------|
| **S** | 单一职责（SRP） | 一个类只负责一件事 | User 类同时负责数据持久化和邮件发送 |
| **O** | 开闭原则（OCP） | 对扩展开放，对修改关闭 | 新增支付方式需要修改 PayService 的 if-else |
| **L** | 里氏替换（LSP） | 子类可以替换父类且程序行为不变 | 正方形继承长方形后 setWidth 破坏约束 |
| **I** | 接口隔离（ISP） | 客户端不应依赖不需要的接口 | 一个大接口包含 20 个方法，实现类只用 3 个 |
| **D** | 依赖倒置（DIP） | 高层模块不依赖低层模块，都依赖抽象 | Service 直接 new Dao() 而非依赖注入 |

### 1.2 其他重要原则

| 原则 | 说明 |
|------|------|
| **DRY** (Don't Repeat Yourself) | 不要重复自己，抽取公共逻辑 |
| **KISS** (Keep It Simple, Stupid) | 保持简单，不要过度设计 |
| **YAGNI** (You Aren't Gonna Need It) | 不要实现当前不需要的功能 |
| **LoD** (Law of Demeter / 迪米特法则) | 最少知识原则，只与直接朋友通信 |
| **CRP** (Composite Reuse Principle) | 优先使用组合而非继承 |

---

## 二、创建型模式（5 种）

### 2.1 单例模式（Singleton）

**场景/问题：** 确保一个类全局只有一个实例，并提供全局访问点。如配置管理器、线程池、数据库连接池。

#### 6 种实现及线程安全分析

```java
// 1. 饿汉式 -- 线程安全，类加载时初始化
public class Singleton1 {
    private static final Singleton1 INSTANCE = new Singleton1();
    private Singleton1() {}
    public static Singleton1 getInstance() { return INSTANCE; }
}
// 优点: 简单，线程安全
// 缺点: 类加载即初始化，可能浪费资源

// 2. 懒汉式（synchronized）-- 线程安全，性能差
public class Singleton2 {
    private static Singleton2 instance;
    private Singleton2() {}
    public static synchronized Singleton2 getInstance() {
        if (instance == null) instance = new Singleton2();
        return instance;
    }
}
// 优点: 懒加载
// 缺点: 每次获取都要加锁，性能差

// 3. 双重检查锁（DCL）-- 线程安全，推荐
public class Singleton3 {
    private static volatile Singleton3 instance; // volatile 防止指令重排
    private Singleton3() {}
    public static Singleton3 getInstance() {
        if (instance == null) {                    // 第一次检查（无锁）
            synchronized (Singleton3.class) {
                if (instance == null) {            // 第二次检查（有锁）
                    instance = new Singleton3();
                    // new 操作分为3步: 1.分配内存 2.初始化 3.引用赋值
                    // 无 volatile 时可能重排为 1->3->2，其他线程看到非null但未初始化的对象
                }
            }
        }
        return instance;
    }
}

// 4. 静态内部类 -- 线程安全，懒加载，推荐
public class Singleton4 {
    private Singleton4() {}
    private static class Holder {
        static final Singleton4 INSTANCE = new Singleton4();
        // 利用类加载机制保证线程安全
        // 只有调用 getInstance() 时才加载 Holder 类
    }
    public static Singleton4 getInstance() {
        return Holder.INSTANCE;
    }
}

// 5. 枚举 -- 线程安全，防反射防序列化，最佳实践
public enum Singleton5 {
    INSTANCE;
    public void doSomething() { }
}
// JVM 保证枚举实例唯一，天然防止反射和反序列化破坏

// 6. CAS 无锁实现 -- 线程安全，无阻塞
public class Singleton6 {
    private static final AtomicReference<Singleton6> INSTANCE = new AtomicReference<>();
    private Singleton6() {}
    public static Singleton6 getInstance() {
        for (;;) {
            Singleton6 current = INSTANCE.get();
            if (current != null) return current;
            current = new Singleton6();
            if (INSTANCE.compareAndSet(null, current)) return current;
        }
    }
}
// 缺点: 可能创建多个实例（只有一个被使用），高竞争下浪费
```

**Spring 中的应用：** Spring Bean 默认是单例的（@Scope("singleton")），由 IoC 容器管理生命周期。

**Python 实现：**

```python
# 方式一：模块级单例（Python 模块天然是单例）
# singleton.py
class Singleton:
    pass
instance = Singleton()

# 方式二：__new__ 方法
class Singleton:
    _instance = None
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

# 方式三：装饰器
def singleton(cls):
    instances = {}
    def get_instance(*args, **kwargs):
        if cls not in instances:
            instances[cls] = cls(*args, **kwargs)
        return instances[cls]
    return get_instance

@singleton
class MyClass:
    pass

# 方式四：元类
class SingletonMeta(type):
    _instances = {}
    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]

class MyClass(metaclass=SingletonMeta):
    pass
```

### 2.2 工厂方法模式（Factory Method）

**场景/问题：** 定义创建对象的接口，让子类决定实例化哪个类。解耦对象创建和使用。

**UML 关系：** Creator(抽象工厂) -> ConcreteCreator(具体工厂) -> Product(产品接口) -> ConcreteProduct(具体产品)

```java
// 产品接口
public interface Logger {
    void log(String message);
}

// 具体产品
public class FileLogger implements Logger {
    public void log(String message) { /* 写文件 */ }
}

public class ConsoleLogger implements Logger {
    public void log(String message) { System.out.println(message); }
}

public class DatabaseLogger implements Logger {
    public void log(String message) { /* 写数据库 */ }
}

// 工厂接口
public interface LoggerFactory {
    Logger createLogger();
}

// 具体工厂
public class FileLoggerFactory implements LoggerFactory {
    public Logger createLogger() { return new FileLogger(); }
}

public class ConsoleLoggerFactory implements LoggerFactory {
    public Logger createLogger() { return new ConsoleLogger(); }
}

// 使用 - 通过工厂创建，不依赖具体类
LoggerFactory factory = new FileLoggerFactory();
Logger logger = factory.createLogger();
logger.log("Hello");
```

**Spring 中的应用：** `FactoryBean` 接口就是工厂方法模式，通过 `getObject()` 创建 Bean。

```python
# Python 实现
from abc import ABC, abstractmethod

class Logger(ABC):
    @abstractmethod
    def log(self, message: str): ...

class FileLogger(Logger):
    def log(self, message: str):
        print(f"[FILE] {message}")

class ConsoleLogger(Logger):
    def log(self, message: str):
        print(f"[CONSOLE] {message}")

class LoggerFactory(ABC):
    @abstractmethod
    def create_logger(self) -> Logger: ...

class FileLoggerFactory(LoggerFactory):
    def create_logger(self) -> Logger:
        return FileLogger()
```

### 2.3 抽象工厂模式（Abstract Factory）

**场景/问题：** 创建一组相关联的对象（产品族），保证产品之间的兼容性。如 UI 主题（暗色主题的按钮 + 输入框 + 对话框）。

```java
// 抽象产品
public interface Button { void render(); }
public interface Input { void render(); }
public interface Dialog { void show(); }

// 具体产品 - 暗色主题
public class DarkButton implements Button { public void render() { /* 暗色按钮 */ } }
public class DarkInput implements Input { public void render() { /* 暗色输入框 */ } }
public class DarkDialog implements Dialog { public void show() { /* 暗色对话框 */ } }

// 具体产品 - 亮色主题
public class LightButton implements Button { public void render() { /* 亮色按钮 */ } }
public class LightInput implements Input { public void render() { /* 亮色输入框 */ } }
public class LightDialog implements Dialog { public void show() { /* 亮色对话框 */ } }

// 抽象工厂
public interface UIFactory {
    Button createButton();
    Input createInput();
    Dialog createDialog();
}

// 具体工厂
public class DarkUIFactory implements UIFactory {
    public Button createButton() { return new DarkButton(); }
    public Input createInput() { return new DarkInput(); }
    public Dialog createDialog() { return new DarkDialog(); }
}

public class LightUIFactory implements UIFactory {
    public Button createButton() { return new LightButton(); }
    public Input createInput() { return new LightInput(); }
    public Dialog createDialog() { return new LightDialog(); }
}
```

**与工厂方法的区别：** 工厂方法创建单个产品，抽象工厂创建一组相关产品（产品族）。

### 2.4 建造者模式（Builder）

**场景/问题：** 创建复杂对象，分步骤构建，避免构造函数参数过多（"伸缩构造函数"问题）。

```java
public class HttpRequest {
    private final String url;
    private final String method;
    private final Map<String, String> headers;
    private final String body;
    private final int timeout;
    private final boolean followRedirects;

    private HttpRequest(Builder builder) {
        this.url = builder.url;
        this.method = builder.method;
        this.headers = builder.headers;
        this.body = builder.body;
        this.timeout = builder.timeout;
        this.followRedirects = builder.followRedirects;
    }

    public static class Builder {
        private final String url;                          // 必填
        private String method = "GET";                     // 默认值
        private Map<String, String> headers = new HashMap<>();
        private String body;
        private int timeout = 30000;
        private boolean followRedirects = true;

        public Builder(String url) { this.url = url; }

        public Builder method(String method) { this.method = method; return this; }
        public Builder header(String key, String value) { headers.put(key, value); return this; }
        public Builder body(String body) { this.body = body; return this; }
        public Builder timeout(int timeout) { this.timeout = timeout; return this; }
        public Builder followRedirects(boolean follow) { this.followRedirects = follow; return this; }

        public HttpRequest build() {
            // 可在此做参数校验
            return new HttpRequest(this);
        }
    }
}

// 使用
HttpRequest request = new HttpRequest.Builder("https://api.example.com/users")
    .method("POST")
    .header("Content-Type", "application/json")
    .body("{\"name\":\"张三\"}")
    .timeout(5000)
    .build();
```

**Spring 中的应用：** `StringBuilder`、`Stream.Builder`、Lombok `@Builder` 注解。

```python
# Python 实现（链式调用）
class HttpRequest:
    def __init__(self):
        self.url = None
        self.method = "GET"
        self.headers = {}
        self.body = None
        self.timeout = 30000

class HttpRequestBuilder:
    def __init__(self, url: str):
        self._request = HttpRequest()
        self._request.url = url

    def method(self, method: str):
        self._request.method = method
        return self

    def header(self, key: str, value: str):
        self._request.headers[key] = value
        return self

    def body(self, body: str):
        self._request.body = body
        return self

    def build(self) -> HttpRequest:
        return self._request

# Python 更常用 dataclass + 关键字参数
from dataclasses import dataclass, field

@dataclass
class HttpRequest:
    url: str
    method: str = "GET"
    headers: dict = field(default_factory=dict)
    body: str = None
    timeout: int = 30000
```

### 2.5 原型模式（Prototype）

**场景/问题：** 通过复制现有对象创建新对象，避免昂贵的初始化操作。

```java
public class PrototypeDocument implements Cloneable {
    private String title;
    private List<String> sections;
    private Map<String, Object> metadata;

    // 浅拷贝
    @Override
    public PrototypeDocument clone() {
        try {
            return (PrototypeDocument) super.clone();
            // 注意：sections 和 metadata 是引用拷贝！
        } catch (CloneNotSupportedException e) {
            throw new RuntimeException(e);
        }
    }

    // 深拷贝
    public PrototypeDocument deepClone() {
        try {
            PrototypeDocument clone = (PrototypeDocument) super.clone();
            clone.sections = new ArrayList<>(this.sections);
            clone.metadata = new HashMap<>(this.metadata);
            return clone;
        } catch (CloneNotSupportedException e) {
            throw new RuntimeException(e);
        }
    }
}
```

**Spring 中的应用：** `@Scope("prototype")` 每次获取 Bean 都创建新实例。

```python
import copy

class Document:
    def __init__(self, title, sections):
        self.title = title
        self.sections = sections

# 浅拷贝
doc2 = copy.copy(doc1)
# 深拷贝
doc3 = copy.deepcopy(doc1)
```

---

## 三、结构型模式（7 种）

### 3.1 代理模式（Proxy）

**场景/问题：** 为对象提供代理以控制访问。可用于延迟加载、权限控制、日志记录、远程调用。

```java
// 静态代理
public interface UserService {
    User getUser(Long id);
    void saveUser(User user);
}

public class UserServiceProxy implements UserService {
    private final UserService target;

    public UserServiceProxy(UserService target) {
        this.target = target;
    }

    @Override
    public User getUser(Long id) {
        System.out.println("[LOG] getUser called, id=" + id);
        long start = System.currentTimeMillis();
        User user = target.getUser(id);
        System.out.println("[LOG] getUser cost " + (System.currentTimeMillis() - start) + "ms");
        return user;
    }

    @Override
    public void saveUser(User user) {
        // 权限检查
        if (!SecurityContext.hasPermission("user:write")) {
            throw new UnauthorizedException("无权限");
        }
        target.saveUser(user);
    }
}

// JDK 动态代理（基于接口）
UserService proxy = (UserService) Proxy.newProxyInstance(
    UserService.class.getClassLoader(),
    new Class[]{UserService.class},
    (proxyObj, method, args) -> {
        System.out.println("Before: " + method.getName());
        Object result = method.invoke(target, args);
        System.out.println("After: " + method.getName());
        return result;
    }
);

// CGLIB 动态代理（基于继承，无需接口）
Enhancer enhancer = new Enhancer();
enhancer.setSuperclass(UserServiceImpl.class);
enhancer.setCallback((MethodInterceptor) (obj, method, args, proxy) -> {
    System.out.println("Before: " + method.getName());
    Object result = proxy.invokeSuper(obj, args);
    System.out.println("After: " + method.getName());
    return result;
});
UserServiceImpl proxy = (UserServiceImpl) enhancer.create();
```

**JDK 动态代理 vs CGLIB：**

| 维度 | JDK 动态代理 | CGLIB |
|------|-------------|-------|
| 实现方式 | 基于接口（Proxy + InvocationHandler） | 基于继承（生成子类） |
| 要求 | 目标类必须实现接口 | 目标类不能是 final |
| 性能 | JDK 8+ 性能已优化，差距不大 | 生成字节码，首次创建较慢 |
| Spring 默认 | 有接口时默认使用 | 无接口时使用 / SpringBoot 2.0+ 默认 |

**Spring 中的应用：** Spring AOP（`@Transactional`、`@Cacheable`、`@Async` 都是代理实现）。

```python
# Python 实现（装饰器 + 魔术方法）
class LoggingProxy:
    def __init__(self, target):
        self._target = target

    def __getattr__(self, name):
        attr = getattr(self._target, name)
        if callable(attr):
            def wrapper(*args, **kwargs):
                print(f"Before: {name}")
                result = attr(*args, **kwargs)
                print(f"After: {name}")
                return result
            return wrapper
        return attr
```

### 3.2 适配器模式（Adapter）

**场景/问题：** 将不兼容的接口转换为客户端期望的接口。如对接多个第三方支付、统一多种日志框架。

```java
// 目标接口（系统期望的统一接口）
public interface MessageSender {
    void send(String to, String content);
}

// 已有的不兼容类（第三方 SDK）
public class AliyunSmsSdk {
    public void sendSms(String phoneNumber, String templateCode, Map<String, String> params) { ... }
}

public class TencentSmsSdk {
    public boolean pushMessage(String mobile, String text) { ... }
}

// 适配器
public class AliyunSmsAdapter implements MessageSender {
    private final AliyunSmsSdk sdk;

    public AliyunSmsAdapter(AliyunSmsSdk sdk) { this.sdk = sdk; }

    @Override
    public void send(String to, String content) {
        Map<String, String> params = Map.of("content", content);
        sdk.sendSms(to, "SMS_TEMPLATE_001", params);
    }
}

public class TencentSmsAdapter implements MessageSender {
    private final TencentSmsSdk sdk;

    public TencentSmsAdapter(TencentSmsSdk sdk) { this.sdk = sdk; }

    @Override
    public void send(String to, String content) {
        sdk.pushMessage(to, content);
    }
}

// 使用 - 统一接口
MessageSender sender = new AliyunSmsAdapter(new AliyunSmsSdk());
sender.send("13800138000", "验证码是 1234");
```

**Spring 中的应用：** `HandlerAdapter`（适配不同类型的 Controller）、`MessageConverter`。

### 3.3 装饰器模式（Decorator）

**场景/问题：** 动态地给对象添加额外功能，比继承更灵活（可自由组合）。

```java
// 组件接口
public interface DataSource {
    void writeData(String data);
    String readData();
}

// 基础实现
public class FileDataSource implements DataSource {
    private final String filename;
    public FileDataSource(String filename) { this.filename = filename; }
    public void writeData(String data) { /* 写文件 */ }
    public String readData() { /* 读文件 */ return ""; }
}

// 装饰器基类
public abstract class DataSourceDecorator implements DataSource {
    protected final DataSource wrappee;
    public DataSourceDecorator(DataSource source) { this.wrappee = source; }
    public void writeData(String data) { wrappee.writeData(data); }
    public String readData() { return wrappee.readData(); }
}

// 具体装饰器 - 加密
public class EncryptionDecorator extends DataSourceDecorator {
    public EncryptionDecorator(DataSource source) { super(source); }

    @Override
    public void writeData(String data) {
        super.writeData(encrypt(data)); // 先加密再写
    }

    @Override
    public String readData() {
        return decrypt(super.readData()); // 先读再解密
    }
}

// 具体装饰器 - 压缩
public class CompressionDecorator extends DataSourceDecorator {
    public CompressionDecorator(DataSource source) { super(source); }

    @Override
    public void writeData(String data) {
        super.writeData(compress(data)); // 先压缩再写
    }
}

// 自由组合装饰器
DataSource source = new CompressionDecorator(
    new EncryptionDecorator(
        new FileDataSource("data.txt")
    )
);
source.writeData("Hello"); // 先加密，再压缩，再写文件
```

**Spring 中的应用：** Java I/O 流（`BufferedInputStream(FileInputStream)`）、`HttpServletRequestWrapper`。

**代理 vs 装饰器：** 代理侧重控制访问（客户端不知道代理的存在），装饰器侧重增强功能（客户端可自由组合装饰器）。

### 3.4 外观模式（Facade）

**场景/问题：** 为复杂子系统提供简化的统一接口。

```java
// 子系统（复杂）
public class InventoryService { public void deduct(Long productId, int count) { ... } }
public class PaymentService { public void charge(Long userId, BigDecimal amount) { ... } }
public class LogisticsService { public void ship(Long orderId, String address) { ... } }
public class NotificationService { public void notify(Long userId, String message) { ... } }

// 外观类（简化接口）
public class OrderFacade {
    private final InventoryService inventoryService;
    private final PaymentService paymentService;
    private final LogisticsService logisticsService;
    private final NotificationService notificationService;

    public OrderResult placeOrder(OrderRequest req) {
        inventoryService.deduct(req.getProductId(), req.getCount());
        paymentService.charge(req.getUserId(), req.getAmount());
        logisticsService.ship(req.getOrderId(), req.getAddress());
        notificationService.notify(req.getUserId(), "订单已提交");
        return new OrderResult(true);
    }
}

// 客户端只和 Facade 交互
OrderResult result = orderFacade.placeOrder(request);
```

**Spring 中的应用：** `JdbcTemplate`（封装 JDBC 复杂操作）、`RestTemplate`。

### 3.5 桥接模式（Bridge）

**场景/问题：** 将抽象与实现分离，使二者可以独立变化。避免多维度继承导致的类爆炸。

```java
// 实现维度 - 消息发送方式
public interface MessageSender {
    void send(String message, String to);
}

public class SmsSender implements MessageSender {
    public void send(String message, String to) { /* 短信发送 */ }
}

public class EmailSender implements MessageSender {
    public void send(String message, String to) { /* 邮件发送 */ }
}

// 抽象维度 - 消息类型
public abstract class Message {
    protected MessageSender sender; // 桥接

    public Message(MessageSender sender) { this.sender = sender; }
    abstract void sendMessage(String content, String to);
}

public class UrgentMessage extends Message {
    public UrgentMessage(MessageSender sender) { super(sender); }
    void sendMessage(String content, String to) {
        sender.send("[紧急] " + content, to);
    }
}

public class NormalMessage extends Message {
    public NormalMessage(MessageSender sender) { super(sender); }
    void sendMessage(String content, String to) {
        sender.send(content, to);
    }
}

// 自由组合: 紧急消息 + 短信 / 普通消息 + 邮件 / ...
Message msg = new UrgentMessage(new SmsSender());
msg.sendMessage("服务器宕机", "13800138000");
```

**Spring 中的应用：** JDBC Driver（`DriverManager` 桥接不同数据库的 `Driver` 实现）。

### 3.6 组合模式（Composite）

**场景/问题：** 将对象组合成树形结构，使客户端统一对待单个对象和组合对象。如文件系统、组织架构、菜单树。

```java
// 组件接口
public interface MenuComponent {
    String getName();
    void print(int indent);
    default void add(MenuComponent component) { throw new UnsupportedOperationException(); }
    default List<MenuComponent> getChildren() { return Collections.emptyList(); }
}

// 叶子节点
public class MenuItem implements MenuComponent {
    private final String name;
    private final String url;

    public MenuItem(String name, String url) { this.name = name; this.url = url; }
    public String getName() { return name; }
    public void print(int indent) { System.out.println(" ".repeat(indent) + "- " + name); }
}

// 组合节点
public class Menu implements MenuComponent {
    private final String name;
    private final List<MenuComponent> children = new ArrayList<>();

    public Menu(String name) { this.name = name; }
    public String getName() { return name; }
    public void add(MenuComponent component) { children.add(component); }
    public List<MenuComponent> getChildren() { return children; }

    public void print(int indent) {
        System.out.println(" ".repeat(indent) + "+ " + name);
        children.forEach(c -> c.print(indent + 2));
    }
}

// 构建树
Menu root = new Menu("系统管理");
Menu userMenu = new Menu("用户管理");
userMenu.add(new MenuItem("用户列表", "/users"));
userMenu.add(new MenuItem("角色管理", "/roles"));
root.add(userMenu);
root.add(new MenuItem("系统设置", "/settings"));
root.print(0);
```

### 3.7 享元模式（Flyweight）

**场景/问题：** 共享细粒度对象，减少内存占用。如字符串常量池、Integer 缓存（-128~127）、数据库连接池。

```java
// 享元对象（不可变，可共享）
public class ChessPiece {
    private final String color; // 内部状态（共享）
    private final String shape;

    public ChessPiece(String color, String shape) {
        this.color = color;
        this.shape = shape;
    }

    // 外部状态（不共享，由客户端传入）
    public void place(int x, int y) {
        System.out.println(color + " " + shape + " at (" + x + "," + y + ")");
    }
}

// 享元工厂
public class ChessPieceFactory {
    private static final Map<String, ChessPiece> cache = new HashMap<>();

    public static ChessPiece get(String color, String shape) {
        String key = color + ":" + shape;
        return cache.computeIfAbsent(key, k -> new ChessPiece(color, shape));
    }
}
```

**Spring 中的应用：** `String.intern()`、`Integer.valueOf()` 缓存、`ThreadPoolExecutor` 线程复用。

---

## 四、行为型模式（11 种）

### 4.1 策略模式（Strategy）

**场景/问题：** 定义一系列算法，使它们可以互换。消除大量 if-else / switch。

```java
// 策略接口
@FunctionalInterface
public interface DiscountStrategy {
    BigDecimal calculate(BigDecimal originalPrice);
}

// 具体策略
public class NoDiscount implements DiscountStrategy {
    public BigDecimal calculate(BigDecimal price) { return price; }
}

public class PercentageDiscount implements DiscountStrategy {
    private final BigDecimal rate;
    public PercentageDiscount(BigDecimal rate) { this.rate = rate; }
    public BigDecimal calculate(BigDecimal price) { return price.multiply(rate); }
}

public class FullReductionDiscount implements DiscountStrategy {
    private final BigDecimal threshold;
    private final BigDecimal reduction;
    public FullReductionDiscount(BigDecimal threshold, BigDecimal reduction) {
        this.threshold = threshold; this.reduction = reduction;
    }
    public BigDecimal calculate(BigDecimal price) {
        return price.compareTo(threshold) >= 0 ? price.subtract(reduction) : price;
    }
}

// 策略注册（Spring 自动注入）
@Component
public class DiscountStrategyFactory {
    private final Map<String, DiscountStrategy> strategies;

    @Autowired
    public DiscountStrategyFactory(List<DiscountStrategy> strategyList) {
        strategies = strategyList.stream()
            .collect(Collectors.toMap(s -> s.getClass().getSimpleName(), Function.identity()));
    }

    public DiscountStrategy getStrategy(String type) {
        return strategies.getOrDefault(type, new NoDiscount());
    }
}
```

**Spring 中的应用：** `Resource` 接口的不同实现（ClassPathResource, FileSystemResource）。

```python
# Python 实现 - 函数即策略
from typing import Callable
from decimal import Decimal

def no_discount(price: Decimal) -> Decimal:
    return price

def percentage_discount(rate: Decimal):
    def calculate(price: Decimal) -> Decimal:
        return price * rate
    return calculate

# 策略 map
strategies = {
    "none": no_discount,
    "vip": percentage_discount(Decimal("0.8")),
}

# 使用
discount_fn = strategies.get("vip", no_discount)
final_price = discount_fn(Decimal("100"))
```

### 4.2 观察者模式（Observer）

**场景/问题：** 定义对象间一对多依赖，当状态改变时自动通知所有观察者。

```java
// Spring 事件机制（观察者模式的典型应用）
// 1. 定义事件
public class OrderCreatedEvent extends ApplicationEvent {
    private final Order order;
    public OrderCreatedEvent(Object source, Order order) {
        super(source);
        this.order = order;
    }
    public Order getOrder() { return order; }
}

// 2. 发布事件
@Service
public class OrderService {
    @Autowired
    private ApplicationEventPublisher eventPublisher;

    public Order createOrder(OrderRequest req) {
        Order order = doCreate(req);
        eventPublisher.publishEvent(new OrderCreatedEvent(this, order));
        return order;
    }
}

// 3. 监听事件（观察者）
@Component
public class InventoryListener {
    @EventListener
    public void onOrderCreated(OrderCreatedEvent event) {
        inventoryService.deduct(event.getOrder().getProductId(), event.getOrder().getCount());
    }
}

@Component
public class NotificationListener {
    @Async // 异步处理
    @EventListener
    public void onOrderCreated(OrderCreatedEvent event) {
        emailService.sendOrderConfirmation(event.getOrder());
    }
}
```

```python
# Python 实现
class EventEmitter:
    def __init__(self):
        self._listeners = {}

    def on(self, event: str, callback):
        self._listeners.setdefault(event, []).append(callback)

    def emit(self, event: str, *args, **kwargs):
        for callback in self._listeners.get(event, []):
            callback(*args, **kwargs)

emitter = EventEmitter()
emitter.on("order_created", lambda order: print(f"扣减库存: {order}"))
emitter.on("order_created", lambda order: print(f"发送通知: {order}"))
emitter.emit("order_created", {"id": 1, "product": "手机"})
```

### 4.3 模板方法模式（Template Method）

**场景/问题：** 定义算法骨架，将某些步骤延迟到子类实现。

```java
public abstract class AbstractDataExporter {
    // 模板方法（final 防止子类覆盖流程）
    public final void export(String destination) {
        List<Map<String, Object>> data = fetchData();       // 子类实现
        List<Map<String, Object>> processed = processData(data); // 可选钩子
        String formatted = formatData(processed);           // 子类实现
        writeToFile(formatted, destination);                // 通用步骤
        afterExport(destination);                           // 钩子方法
    }

    protected abstract List<Map<String, Object>> fetchData();
    protected abstract String formatData(List<Map<String, Object>> data);

    // 钩子方法（子类可选覆盖）
    protected List<Map<String, Object>> processData(List<Map<String, Object>> data) {
        return data; // 默认不处理
    }

    protected void afterExport(String destination) {
        // 默认空实现
    }

    private void writeToFile(String content, String destination) {
        // 通用的写文件逻辑
    }
}

public class CsvExporter extends AbstractDataExporter {
    protected List<Map<String, Object>> fetchData() { /* 查数据库 */ return List.of(); }
    protected String formatData(List<Map<String, Object>> data) { /* 转 CSV */ return ""; }
}

public class ExcelExporter extends AbstractDataExporter {
    protected List<Map<String, Object>> fetchData() { /* 查 ES */ return List.of(); }
    protected String formatData(List<Map<String, Object>> data) { /* 转 Excel */ return ""; }
    @Override
    protected void afterExport(String destination) {
        notifyUser(destination); // 导出后通知用户
    }
}
```

**Spring 中的应用：** `JdbcTemplate`、`AbstractApplicationContext.refresh()` 就是模板方法。

### 4.4 责任链模式（Chain of Responsibility）

**场景/问题：** 多个处理者组成链条，请求沿链传递直到被处理。解耦请求发送者和接收者。

```java
// 处理器接口
public abstract class Handler {
    protected Handler next;

    public Handler setNext(Handler next) {
        this.next = next;
        return next; // 支持链式设置
    }

    public final void handle(Request request) {
        if (canHandle(request)) {
            doHandle(request);
        } else if (next != null) {
            next.handle(request);
        } else {
            throw new RuntimeException("没有处理器能处理该请求");
        }
    }

    protected abstract boolean canHandle(Request request);
    protected abstract void doHandle(Request request);
}

// 具体处理器
public class AuthHandler extends Handler {
    protected boolean canHandle(Request req) { return true; } // 所有请求都需要认证
    protected void doHandle(Request req) {
        if (!isAuthenticated(req)) throw new UnauthorizedException();
        if (next != null) next.handle(req); // 通过后继续下一个
    }
}

public class RateLimitHandler extends Handler {
    protected boolean canHandle(Request req) { return true; }
    protected void doHandle(Request req) {
        if (isRateLimited(req)) throw new TooManyRequestsException();
        if (next != null) next.handle(req);
    }
}

public class BusinessHandler extends Handler {
    protected boolean canHandle(Request req) { return true; }
    protected void doHandle(Request req) {
        // 处理业务逻辑
    }
}

// 构建责任链
Handler chain = new AuthHandler();
chain.setNext(new RateLimitHandler()).setNext(new BusinessHandler());
chain.handle(request);
```

**Spring 中的应用：** Servlet Filter 链、Spring Interceptor 链、Spring Security 过滤器链。

### 4.5 命令模式（Command）

**场景/问题：** 将请求封装为对象，从而支持参数化、排队、撤销、日志记录。

```java
// 命令接口
public interface Command {
    void execute();
    void undo();
}

// 具体命令
public class AddTextCommand implements Command {
    private final TextEditor editor;
    private final String text;
    private final int position;

    public AddTextCommand(TextEditor editor, String text, int position) {
        this.editor = editor; this.text = text; this.position = position;
    }

    public void execute() { editor.insert(text, position); }
    public void undo() { editor.delete(position, text.length()); }
}

// 命令管理器（支持撤销/重做）
public class CommandManager {
    private final Deque<Command> undoStack = new ArrayDeque<>();
    private final Deque<Command> redoStack = new ArrayDeque<>();

    public void execute(Command command) {
        command.execute();
        undoStack.push(command);
        redoStack.clear();
    }

    public void undo() {
        if (!undoStack.isEmpty()) {
            Command command = undoStack.pop();
            command.undo();
            redoStack.push(command);
        }
    }

    public void redo() {
        if (!redoStack.isEmpty()) {
            Command command = redoStack.pop();
            command.execute();
            undoStack.push(command);
        }
    }
}
```

**Spring 中的应用：** `Runnable`（命令对象）、事务的提交/回滚。

### 4.6 迭代器模式（Iterator）

**场景/问题：** 提供一种方法顺序访问集合元素，而不暴露其内部结构。

```java
// Java 内置迭代器
public interface Iterator<E> {
    boolean hasNext();
    E next();
}

// 自定义可迭代集合
public class PagedResult<T> implements Iterable<T> {
    private final Function<Integer, List<T>> pageFetcher;
    private final int pageSize;

    public PagedResult(Function<Integer, List<T>> pageFetcher, int pageSize) {
        this.pageFetcher = pageFetcher;
        this.pageSize = pageSize;
    }

    @Override
    public Iterator<T> iterator() {
        return new Iterator<T>() {
            private int currentPage = 0;
            private List<T> currentBatch = null;
            private int index = 0;

            public boolean hasNext() {
                if (currentBatch == null || index >= currentBatch.size()) {
                    currentBatch = pageFetcher.apply(currentPage++);
                    index = 0;
                }
                return currentBatch != null && !currentBatch.isEmpty() && index < currentBatch.size();
            }

            public T next() {
                if (!hasNext()) throw new NoSuchElementException();
                return currentBatch.get(index++);
            }
        };
    }
}

// 使用 - 透明地分页遍历
PagedResult<User> users = new PagedResult<>(page -> userMapper.selectPage(page, 100), 100);
for (User user : users) {
    process(user);
}
```

```python
# Python 中迭代器是语言核心特性
class PagedIterator:
    def __init__(self, fetch_fn, page_size=100):
        self.fetch_fn = fetch_fn
        self.page_size = page_size
        self.page = 0

    def __iter__(self):
        return self

    def __next__(self):
        # 使用 yield 更简洁
        pass

# 生成器（更 Pythonic）
def paged_query(fetch_fn, page_size=100):
    page = 0
    while True:
        batch = fetch_fn(page, page_size)
        if not batch:
            return
        yield from batch
        page += 1
```

### 4.7 中介者模式（Mediator）

**场景/问题：** 用中介对象封装一系列对象之间的交互，使对象之间不直接引用，降低耦合。

```java
// 中介者（聊天室）
public class ChatRoom {
    private final Map<String, User> users = new HashMap<>();

    public void register(User user) {
        users.put(user.getName(), user);
        user.setChatRoom(this);
    }

    public void sendMessage(String from, String to, String message) {
        User target = users.get(to);
        if (target != null) {
            target.receive(from, message);
        }
    }

    public void broadcast(String from, String message) {
        users.values().stream()
            .filter(u -> !u.getName().equals(from))
            .forEach(u -> u.receive(from, message));
    }
}
```

**Spring 中的应用：** `DispatcherServlet`（协调 Controller、ViewResolver 等组件的交互）。

### 4.8 备忘录模式（Memento）

**场景/问题：** 在不破坏封装的前提下，保存对象的内部状态，以便以后恢复。如编辑器撤销、游戏存档。

```java
// 备忘录
public class EditorMemento {
    private final String content;
    private final int cursorPosition;
    private final Instant timestamp;

    public EditorMemento(String content, int cursorPosition) {
        this.content = content;
        this.cursorPosition = cursorPosition;
        this.timestamp = Instant.now();
    }
    // getters
}

// 发起人
public class TextEditor {
    private String content;
    private int cursorPosition;

    public EditorMemento save() {
        return new EditorMemento(content, cursorPosition);
    }

    public void restore(EditorMemento memento) {
        this.content = memento.getContent();
        this.cursorPosition = memento.getCursorPosition();
    }
}

// 管理者
public class History {
    private final Deque<EditorMemento> snapshots = new ArrayDeque<>();

    public void push(EditorMemento memento) { snapshots.push(memento); }
    public EditorMemento pop() { return snapshots.pop(); }
}
```

### 4.9 状态模式（State）

**场景/问题：** 允许对象在内部状态改变时改变其行为。消除基于状态的大量 if-else。

```java
// 状态接口
public interface OrderState {
    void pay(OrderContext context);
    void ship(OrderContext context);
    void deliver(OrderContext context);
    void cancel(OrderContext context);
}

// 具体状态
public class PendingPayState implements OrderState {
    public void pay(OrderContext ctx) {
        System.out.println("支付成功");
        ctx.setState(new PaidState()); // 状态转移
    }
    public void ship(OrderContext ctx) { throw new IllegalStateException("未支付不能发货"); }
    public void deliver(OrderContext ctx) { throw new IllegalStateException("未支付不能收货"); }
    public void cancel(OrderContext ctx) {
        System.out.println("订单已取消");
        ctx.setState(new CancelledState());
    }
}

public class PaidState implements OrderState {
    public void pay(OrderContext ctx) { throw new IllegalStateException("已支付，请勿重复支付"); }
    public void ship(OrderContext ctx) {
        System.out.println("已发货");
        ctx.setState(new ShippedState());
    }
    public void deliver(OrderContext ctx) { throw new IllegalStateException("未发货不能收货"); }
    public void cancel(OrderContext ctx) {
        System.out.println("退款中");
        ctx.setState(new RefundingState());
    }
}

// 上下文
public class OrderContext {
    private OrderState state;

    public OrderContext() { this.state = new PendingPayState(); }
    public void setState(OrderState state) { this.state = state; }
    public void pay() { state.pay(this); }
    public void ship() { state.ship(this); }
    public void deliver() { state.deliver(this); }
    public void cancel() { state.cancel(this); }
}
```

### 4.10 访问者模式（Visitor）

**场景/问题：** 将算法与对象结构分离，在不修改已有类的情况下新增操作。适合结构稳定但操作多变的场景。

```java
// 元素
public interface DocumentElement {
    void accept(DocumentVisitor visitor);
}

public class Paragraph implements DocumentElement {
    private String text;
    public void accept(DocumentVisitor visitor) { visitor.visit(this); }
    public String getText() { return text; }
}

public class Image implements DocumentElement {
    private String url;
    public void accept(DocumentVisitor visitor) { visitor.visit(this); }
    public String getUrl() { return url; }
}

// 访问者
public interface DocumentVisitor {
    void visit(Paragraph paragraph);
    void visit(Image image);
}

// 具体访问者 - HTML 导出
public class HtmlExportVisitor implements DocumentVisitor {
    private StringBuilder html = new StringBuilder();

    public void visit(Paragraph p) { html.append("<p>").append(p.getText()).append("</p>"); }
    public void visit(Image img) { html.append("<img src=\"").append(img.getUrl()).append("\"/>"); }
}

// 具体访问者 - 字数统计
public class WordCountVisitor implements DocumentVisitor {
    private int count = 0;

    public void visit(Paragraph p) { count += p.getText().split("\\s+").length; }
    public void visit(Image img) { /* 图片不计字数 */ }
    public int getCount() { return count; }
}
```

**Spring 中的应用：** `BeanDefinitionVisitor`、ASM 字节码框架的 `ClassVisitor`。

### 4.11 解释器模式（Interpreter）

**场景/问题：** 为特定语言定义文法并提供解释器。适合简单的 DSL（领域特定语言）。

```java
// 表达式接口
public interface Expression {
    boolean interpret(Map<String, Object> context);
}

// 终结符表达式
public class EqualsExpression implements Expression {
    private final String key;
    private final Object value;

    public EqualsExpression(String key, Object value) {
        this.key = key; this.value = value;
    }

    public boolean interpret(Map<String, Object> context) {
        return value.equals(context.get(key));
    }
}

// 非终结符表达式
public class AndExpression implements Expression {
    private final Expression left, right;
    public AndExpression(Expression left, Expression right) {
        this.left = left; this.right = right;
    }
    public boolean interpret(Map<String, Object> context) {
        return left.interpret(context) && right.interpret(context);
    }
}

public class OrExpression implements Expression {
    private final Expression left, right;
    public OrExpression(Expression left, Expression right) {
        this.left = left; this.right = right;
    }
    public boolean interpret(Map<String, Object> context) {
        return left.interpret(context) || right.interpret(context);
    }
}

// 使用: age == 18 AND city == "北京"
Expression rule = new AndExpression(
    new EqualsExpression("age", 18),
    new EqualsExpression("city", "北京")
);
Map<String, Object> ctx = Map.of("age", 18, "city", "北京");
boolean result = rule.interpret(ctx); // true
```

**Spring 中的应用：** SpEL（Spring Expression Language）。

---

## 五、设计模式在 Spring 中的应用汇总

| 模式 | Spring 中的应用 |
|------|-----------------|
| 单例 | Bean 默认作用域（singleton） |
| 工厂方法 | `FactoryBean`、`BeanFactory` |
| 抽象工厂 | `FactoryBean` 创建产品族 |
| 建造者 | `BeanDefinitionBuilder`、`UriComponentsBuilder` |
| 原型 | `@Scope("prototype")` |
| 代理 | AOP（JDK 动态代理 / CGLIB）、`@Transactional`、`@Async` |
| 适配器 | `HandlerAdapter`、`MessageConverter` |
| 装饰器 | `HttpServletRequestWrapper`、`TransactionAwareCacheDecorator` |
| 外观 | `JdbcTemplate`、`RestTemplate` |
| 模板方法 | `JdbcTemplate.execute()`、`AbstractApplicationContext.refresh()` |
| 策略 | `Resource`（ClassPath/FileSystem/URL）、`HandlerMapping` |
| 观察者 | `ApplicationEvent` + `@EventListener` |
| 责任链 | Servlet Filter、`HandlerInterceptor` |
| 命令 | `Runnable`、`JdbcTemplate` 中的 `StatementCallback` |
| 状态 | Spring State Machine |
| 访问者 | `BeanDefinitionVisitor` |
| 解释器 | SpEL 表达式引擎 |
| 中介者 | `DispatcherServlet` |
| 迭代器 | `CompositeIterator` |
| 组合 | `CompositeCacheManager` |

---

## 六、面试高频题

### Q1: 单例模式有几种实现？为什么 DCL 需要 volatile？

**答：** 六种实现：饿汉式、懒汉式（synchronized）、DCL、静态内部类、枚举、CAS。DCL 需要 volatile 的原因：`new Singleton()` 在字节码层面分三步（分配内存 -> 初始化 -> 引用赋值），JVM 可能重排序为（分配 -> 引用赋值 -> 初始化），导致其他线程看到非 null 但未完成初始化的对象。volatile 禁止了这种指令重排。

### Q2: 工厂方法和抽象工厂的区别？

**答：** 工厂方法关注单一产品的创建，定义一个创建产品的接口让子类决定实例化哪个类。抽象工厂关注产品族（一组相关产品）的创建，如 "暗色主题" 工厂同时创建暗色按钮、暗色输入框。工厂方法是一个方法，抽象工厂是多个方法的组合。

### Q3: 代理模式和装饰器模式的区别？

**答：** 代理侧重控制访问（访问控制、延迟加载、权限校验），客户端通常不知道代理的存在，代理和真实对象由同一方创建。装饰器侧重增强功能（新增行为），客户端显式选择装饰器组合（如 `new BufferedInputStream(new FileInputStream(...))`），强调灵活的功能叠加。

### Q4: 策略模式怎么消除 if-else？

**答：** 将每个分支逻辑抽取为独立的策略类，实现同一接口。用 Map 或 Spring 自动注入将策略注册为映射表。运行时根据类型从 Map 中获取对应策略执行。新增策略只需新增类并注册，无需修改已有代码（符合开闭原则）。

### Q5: Spring 框架中用了哪些设计模式？

**答：** (1) 单例 -- Bean 默认单例；(2) 工厂 -- BeanFactory/FactoryBean；(3) 代理 -- AOP（@Transactional, @Cacheable）；(4) 模板方法 -- JdbcTemplate；(5) 观察者 -- ApplicationEvent；(6) 适配器 -- HandlerAdapter；(7) 策略 -- Resource 的多种实现；(8) 责任链 -- Filter/Interceptor；(9) 外观 -- JdbcTemplate 简化 JDBC；(10) 装饰器 -- HttpServletRequestWrapper。

### Q6: 什么是 SOLID 原则？举例说明开闭原则。

**答：** SOLID 是五大设计原则：单一职责、开闭原则、里氏替换、接口隔离、依赖倒置。开闭原则指对扩展开放、对修改关闭。例如支付功能，不应在 PayService 中用 if-else 判断支付类型（修改），而应定义 PayStrategy 接口，新增支付方式只需新增实现类（扩展），注册到策略工厂即可。

### Q7: 观察者模式和发布-订阅模式的区别？

**答：** 观察者模式中 Subject（被观察者）直接维护 Observer 列表并通知，两者紧耦合。发布-订阅模式引入中间件（消息代理/事件总线），Publisher 和 Subscriber 互不知道对方，通过 Topic 解耦。Spring 的 ApplicationEvent 更接近观察者模式（同进程），Kafka/RabbitMQ 是发布-订阅模式（跨进程）。

### Q8: 模板方法和策略模式的区别？

**答：** 模板方法基于继承，在父类定义算法骨架，子类实现具体步骤（编译时确定）。策略模式基于组合，将算法封装为独立对象，运行时动态切换。模板方法是 "我来定义流程，你来实现细节"；策略模式是 "我来定义接口，你来选择实现"。模板方法控制了整个流程，策略模式只替换某个环节。

### Q9: 责任链模式在实际项目中如何应用？

**答：** 典型应用：(1) Servlet Filter 链 -- 认证 -> 日志 -> 编码 -> 业务；(2) Spring Interceptor -- 权限校验 -> 参数校验 -> 日志记录；(3) 审批流程 -- 组长 -> 经理 -> 总监 -> VP，逐级审批；(4) 参数校验链 -- 非空检查 -> 格式检查 -> 业务规则检查。好处是可以灵活添加/删除/排序处理器，符合开闭原则。

### Q10: 如何选择设计模式？有什么原则？

**答：** (1) 不要为了用模式而用模式，KISS 优先；(2) 识别变化点：将变化的部分抽象出来，用模式封装变化；(3) 优先组合而非继承；(4) 常见场景映射：消除 if-else 用策略模式，对象创建复杂用工厂/建造者，增强功能用装饰器，控制访问用代理，解耦通知用观察者，流程固定细节变化用模板方法。(5) 避免过度设计：只在确实需要扩展时才引入模式，YAGNI 原则。

### Q11: 深拷贝和浅拷贝的区别？原型模式为什么要注意这个？

**答：** 浅拷贝只复制基本类型和引用地址，引用类型指向同一个对象。深拷贝递归复制所有引用对象，完全独立。原型模式 clone 默认是浅拷贝（Java 的 `Object.clone()`），如果对象包含可变引用类型（List, Map），修改克隆体会影响原始对象，必须手动深拷贝。实现方式：(1) 手动逐个 new/copy；(2) 序列化/反序列化（如 JSON）；(3) Java 的 `Serializable` + `ObjectOutputStream`。
