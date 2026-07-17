# Java 注解、SPI 与反射机制

## 1. 注解（Annotation）

### 什么是注解
Java 5 引入的元数据机制，给类/方法/字段打标签，运行时或编译时读取。

### 内置元注解

| 元注解 | 作用 |
|--------|------|
| `@Target` | 注解可用的位置 |
| `@Retention` | 注解保留策略 |
| `@Documented` | 是否生成 Javadoc |
| `@Inherited` | 子类是否继承 |
| `@Repeatable` | 可重复（Java 8+） |

### @Target 取值
- `TYPE`：类、接口、枚举
- `FIELD`：字段
- `METHOD`：方法
- `PARAMETER`：参数
- `CONSTRUCTOR`：构造器
- `LOCAL_VARIABLE`：局部变量
- `ANNOTATION_TYPE`：注解
- `PACKAGE`：包

### @Retention 取值
- `SOURCE`：只在源码，编译后丢弃（如 `@Override`）
- `CLASS`：编译到 class，但运行时不可读（默认）
- `RUNTIME`：运行时可读（反射获取，最常用）

### 自定义注解

```java
@Target({ElementType.METHOD, ElementType.TYPE})
@Retention(RetentionPolicy.RUNTIME)
@Documented
public @interface Audit {
    String value() default "";
    AuditLevel level() default AuditLevel.INFO;
    String[] roles() default {};
}
```

### 读取注解

```java
Class<?> clazz = OrderService.class;

// 类上注解
Audit classAudit = clazz.getAnnotation(Audit.class);

// 方法注解
Method method = clazz.getMethod("deleteOrder", Long.class);
Audit methodAudit = method.getAnnotation(Audit.class);

if (methodAudit != null) {
    System.out.println(methodAudit.level());
    System.out.println(String.join(",", methodAudit.roles()));
}
```

### 重复注解（Java 8+）

```java
@Repeatable(Schedules.class)
public @interface Schedule { String day(); }

public @interface Schedules { Schedule[] value(); }

@Schedule(day = "Mon")
@Schedule(day = "Tue")
public void task() { }

// 读取
Schedule[] list = method.getAnnotationsByType(Schedule.class);
```

### 常见应用

**1. Spring**
```java
@Component, @Autowired, @Transactional, @Value, @RequestMapping, @PreAuthorize ...
```

**2. JPA**
```java
@Entity, @Id, @Column, @OneToMany ...
```

**3. Validation**
```java
@NotNull, @Size, @Email, @Pattern ...
```

**4. 自定义业务**
```java
@DistributedLock(key = "#userId")
@Audit(level = SENSITIVE)
@RateLimit(qps = 100)
```

### 实现自定义注解（Spring AOP）

```java
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface RateLimit {
    int qps() default 100;
}

@Aspect @Component
public class RateLimitAspect {
    private final Map<String, RateLimiter> limiters = new ConcurrentHashMap<>();

    @Around("@annotation(rateLimit)")
    public Object limit(ProceedingJoinPoint pjp, RateLimit rateLimit) throws Throwable {
        String key = pjp.getSignature().toLongString();
        RateLimiter limiter = limiters.computeIfAbsent(key,
            k -> RateLimiter.create(rateLimit.qps()));
        if (!limiter.tryAcquire()) throw new RateLimitException();
        return pjp.proceed();
    }
}

// 使用
@RateLimit(qps = 50)
public void fetchData() { ... }
```

---

## 2. 反射（Reflection）

### 什么是反射
运行时获取类信息、创建对象、调用方法、读写字段的能力。

### 获取 Class

```java
Class<?> c1 = String.class;              // 类字面量
Class<?> c2 = "hello".getClass();        // 对象.getClass()
Class<?> c3 = Class.forName("java.lang.String");  // 全限定名
```

### 反射创建对象

```java
// 无参构造
String s = String.class.getDeclaredConstructor().newInstance();

// 有参构造
Constructor<User> ctor = User.class.getDeclaredConstructor(String.class, int.class);
User u = ctor.newInstance("alice", 25);
```

### 反射调用方法

```java
Class<?> clazz = obj.getClass();
Method method = clazz.getDeclaredMethod("setName", String.class);
method.setAccessible(true);  // 访问 private
method.invoke(obj, "Alice");
```

### 反射读写字段

```java
Field field = obj.getClass().getDeclaredField("name");
field.setAccessible(true);
String name = (String) field.get(obj);
field.set(obj, "NewName");
```

### 反射获取泛型

```java
// 字段
Field field = clazz.getDeclaredField("list");
Type type = field.getGenericType();
if (type instanceof ParameterizedType pt) {
    Type[] args = pt.getActualTypeArguments();  // List<String> → [String]
}

// 方法返回值
Method m = clazz.getMethod("getList");
Type returnType = m.getGenericReturnType();
```

### 性能
反射比直接调用慢 10-100 倍（JIT 可部分优化）。优化方式：
- 缓存 `Method` / `Field` 对象
- 用 `MethodHandle`（Java 7+，更快）
- 用 `VarHandle`（Java 9+，更快）
- 用 `LambdaMetafactory` 生成函数接口

### 反射的用途
- 框架核心：Spring 依赖注入、ORM
- 动态代理
- 序列化/反序列化
- 注解处理

### 反射 vs 直接调用

| 场景 | 优先方式 |
|------|----------|
| 编译时已知类型 | 直接调用 |
| 运行时动态 | 反射 |
| 性能敏感 | MethodHandle / Direct |
| 框架层 | 反射（一次解析多次调用） |

---

## 3. 动态代理

### JDK 动态代理

**基于接口**：

```java
interface UserService { void save(User user); }

class RealUserService implements UserService {
    public void save(User user) { System.out.println("saving " + user); }
}

// 代理
UserService proxy = (UserService) Proxy.newProxyInstance(
    UserService.class.getClassLoader(),
    new Class[]{UserService.class},
    (p, method, args) -> {
        System.out.println("before " + method.getName());
        Object result = method.invoke(realService, args);
        System.out.println("after");
        return result;
    }
);
proxy.save(new User());
```

**限制**：被代理类必须实现接口。

### CGLIB 动态代理

**基于继承**，能代理普通类：

```java
Enhancer enhancer = new Enhancer();
enhancer.setSuperclass(RealUserService.class);
enhancer.setCallback((MethodInterceptor) (obj, method, args, proxy) -> {
    System.out.println("before");
    Object result = proxy.invokeSuper(obj, args);
    System.out.println("after");
    return result;
});
RealUserService proxy = (RealUserService) enhancer.create();
```

**限制**：
- 不能代理 `final` 类/方法
- 需要无参构造器

### Spring AOP 的代理选择
- 目标类实现接口 → JDK 动态代理
- 未实现接口 → CGLIB
- 配置 `proxy-target-class=true` 强制 CGLIB

### 性能
- JDK 动态代理（Java 8+）和 CGLIB 性能接近
- JDK 调用快，CGLIB 创建快
- 新项目默认 CGLIB（Spring Boot 2+ 默认）

---

## 4. Java SPI

### 什么是 SPI
**Service Provider Interface**，Java 原生的**插件化机制**。接口定义方不实现具体逻辑，由服务提供者实现，运行时动态加载。

### 核心约定
- 接口放 `com.example.MyService`
- 实现类放 `com.vendor.MyServiceImpl`
- 配置文件 `META-INF/services/com.example.MyService` 内容是实现类全限定名

### 示例

**步骤 1：定义接口**
```java
package com.example;
public interface Logger {
    void log(String msg);
}
```

**步骤 2：实现（在 JAR A）**
```java
package com.vendorA;
public class ConsoleLogger implements Logger {
    public void log(String msg) { System.out.println(msg); }
}
```

`META-INF/services/com.example.Logger`：
```
com.vendorA.ConsoleLogger
```

**步骤 3：加载**
```java
ServiceLoader<Logger> loaders = ServiceLoader.load(Logger.class);
for (Logger l : loaders) {
    l.log("hello");
}
```

### Java 9+ 模块化 SPI

```java
// module-info.java
module my.module {
    provides com.example.Logger with com.vendorA.ConsoleLogger;
    // 消费端
    uses com.example.Logger;
}
```

### JDK 中的 SPI 应用
- **JDBC Driver**：`DriverManager` 通过 SPI 加载 `java.sql.Driver`
- **日志**：SLF4J 绑定
- **Servlet**：`ServletContainerInitializer`
- **NIO**：`FileSystemProvider`

### SPI vs Spring SPI

| 维度 | JDK SPI | Spring SPI（spring.factories） |
|------|---------|-------------------------------|
| 配置 | META-INF/services | spring.factories / AutoConfiguration.imports |
| 懒加载 | 一次加载全部 | 按需 |
| 依赖注入 | 无 | 支持 |
| 条件装配 | 无 | @Conditional |
| 适用 | 标准 Java | Spring Boot |

### Dubbo SPI
Dubbo 增强了 JDK SPI：
- `@SPI` 注解指定默认实现
- 按 key 加载特定实现
- AOP（Wrapper）
- IoC（自动注入依赖）

```java
@SPI("dubbo")
public interface Protocol { ... }

// 配置
dubbo=com.alibaba.dubbo.rpc.protocol.dubbo.DubboProtocol
injvm=com.alibaba.dubbo.rpc.protocol.injvm.InjvmProtocol
```

### SPI 实战用法

**场景 1：支付方式插件化**
```java
public interface PaymentProvider {
    String name();
    PaymentResult pay(PaymentRequest req);
}

// 每个供应商提供实现，放 META-INF/services
// 应用加载：
Map<String, PaymentProvider> providers = new HashMap<>();
for (PaymentProvider p : ServiceLoader.load(PaymentProvider.class)) {
    providers.put(p.name(), p);
}
```

**场景 2：日志切换**
应用代码只依赖 `slf4j`，具体实现（Logback、Log4j2）通过 SPI 切换。

---

## 5. MethodHandle 与 VarHandle

### MethodHandle（Java 7+）
比反射更快的方法调用机制，JIT 友好。

```java
MethodHandles.Lookup lookup = MethodHandles.lookup();
MethodHandle mh = lookup.findVirtual(String.class, "length", MethodType.methodType(int.class));
int len = (int) mh.invoke("hello");  // 5
```

### VarHandle（Java 9+）
字段的低层访问，替代 `Unsafe`：

```java
static final VarHandle VH_COUNT;
static {
    try {
        VH_COUNT = MethodHandles.lookup().findVarHandle(Counter.class, "count", int.class);
    } catch (Exception e) { throw new ExceptionInInitializerError(e); }
}

// CAS
VH_COUNT.compareAndSet(counter, 0, 1);
// volatile
int v = (int) VH_COUNT.getVolatile(counter);
```

---

## 6. 字节码操作

### ASM
最底层，操作字节码指令。Spring AOP、Hibernate 用。

### Javassist
更易用的 API：
```java
ClassPool pool = ClassPool.getDefault();
CtClass cc = pool.get("com.example.Foo");
CtMethod m = cc.getDeclaredMethod("bar");
m.insertBefore("{ System.out.println(\"before\"); }");
Class<?> clazz = cc.toClass();
```

### Byte Buddy
现代化 API，Spring Boot / Mockito 用：
```java
DynamicType.Unloaded<?> dynamic = new ByteBuddy()
    .subclass(Object.class)
    .method(ElementMatchers.named("toString"))
    .intercept(FixedValue.value("Hello"))
    .make();
```

---

## 面试高频问题

**Q1：注解的本质？**

一个特殊接口，继承 `java.lang.annotation.Annotation`。编译时编译为普通 class。运行时可通过反射读取。

`@Retention(RUNTIME)` 保留到运行时，通过 `getAnnotation()` 读取。

**Q2：Spring 如何处理 @Autowired / @Transactional？**

- `@Autowired`：`AutowiredAnnotationBeanPostProcessor` 在 Bean 实例化后扫描字段/方法，反射注入依赖
- `@Transactional`：AOP 代理（JDK 或 CGLIB）包裹方法，`TransactionInterceptor` 拦截，开始/提交/回滚事务

核心：**反射 + 动态代理**。

**Q3：反射性能差到什么程度？**

- 比直接调用慢 10-100 倍（未缓存）
- 缓存 Method 后差距缩小到 3-5 倍
- 现代 JIT 可 inline 反射调用，接近原生

大部分场景无需担心；**热点路径**避免反射（用 MethodHandle 或预生成代码）。

**Q4：JDK 动态代理和 CGLIB 区别？**

- **JDK**：基于接口，`Proxy.newProxyInstance`，被代理类必须实现接口
- **CGLIB**：基于继承，ASM 字节码生成子类，能代理普通类

JDK 创建代理慢，调用快；CGLIB 反之。Spring Boot 2+ 默认用 CGLIB，减少"必须用接口"的约束。

**Q5：SPI 的工作原理？**

- `ServiceLoader.load(ServiceInterface.class)` 扫描 classpath 下 `META-INF/services/<全限定名>` 文件
- 读取文件内容（实现类全限定名）
- 反射实例化
- 返回迭代器

关键：**接口方不依赖实现方**，实现方通过 jar 提供。JDBC、SLF4J 都用。

**Q6：Spring Boot 的自动配置和 SPI 关系？**

Spring Boot 3.0 前：`META-INF/spring.factories` + `EnableAutoConfiguration` key
Spring Boot 3.0+：`META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`

不是 JDK SPI，但是**仿 SPI 思想**的机制。加载后配合 `@ConditionalOnXxx` 按条件装配 Bean。

**Q7：@Inherited 的作用？**

只对**类上注解**生效：父类的注解会被子类继承。

```java
@Inherited @Retention(RUNTIME) @Target(TYPE)
@interface MyAnno { }

@MyAnno class Parent {}
class Child extends Parent {}

Child.class.isAnnotationPresent(MyAnno.class);  // true
```

不对**接口/字段/方法**生效。

**Q8：重复注解怎么实现？**

Java 8+ `@Repeatable`：

```java
@Repeatable(Schedules.class)
@Retention(RUNTIME)
public @interface Schedule { String day(); }

@Retention(RUNTIME)
public @interface Schedules { Schedule[] value(); }

@Schedule(day="Mon") @Schedule(day="Tue")
public void task() {}
```

编译器把多个 `@Schedule` 自动包进 `@Schedules` 容器注解。读取用 `getAnnotationsByType`。

**Q9：注解处理器（APT）是什么？**

编译期处理注解：
- `Processor` 接口实现
- `META-INF/services/javax.annotation.processing.Processor`
- 编译器扫到注解时调用 Processor，可**生成新代码**

典型应用：
- **Lombok**：`@Getter` → 编译期生成 getter
- **MapStruct**：`@Mapper` → 生成 mapper 实现
- **AutoValue**：自动生成 Value Object

优势：零运行时开销。

**Q10：反射可以破坏封装吗？有什么限制？**

可以：
```java
field.setAccessible(true);
field.set(obj, value);
```

**限制（Java 9+ 模块化）**：
- 模块内部类默认不开放给外部反射
- 需要 `opens` 声明
- `--add-opens` JVM 参数可强制开放

**安全管理器**（已废弃）：`SecurityManager.checkPermission` 拦截。

生产中反射常被用来实现框架功能（Spring、JPA），但要谨慎：破坏封装、性能、版本兼容都是考虑点。
