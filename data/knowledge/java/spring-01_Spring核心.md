# Spring 核心

## 1. IoC（控制反转）与 DI（依赖注入）

### 1.1 核心概念

- **IoC（Inversion of Control）**：将对象的创建、组装和管理权从应用代码转移到 Spring 容器，由容器统一管控对象的生命周期和依赖关系
- **DI（Dependency Injection）**：IoC 的具体实现方式，容器在运行时自动将依赖注入到目标对象中
- 核心好处：解耦合、便于单元测试、便于管理对象生命周期、支持面向接口编程

### 1.2 BeanFactory vs ApplicationContext

| 特性 | BeanFactory | ApplicationContext |
|------|------------|-------------------|
| 定位 | IoC 容器的根接口，最底层 | BeanFactory 的子接口，功能更丰富 |
| Bean 加载时机 | 懒加载（getBean 时才创建） | 预加载（容器启动时创建所有单例 Bean） |
| 国际化支持 | 不支持 | 支持（MessageSource） |
| 事件机制 | 不支持 | 支持（ApplicationEvent） |
| AOP 集成 | 需要手动配置 | 自动集成 |
| 资源访问 | 不支持 | 支持（ResourceLoader） |
| 常用实现 | DefaultListableBeanFactory | ClassPathXmlApplicationContext、AnnotationConfigApplicationContext |

**结论**：日常开发中几乎都使用 ApplicationContext，BeanFactory 主要出现在框架内部和面试中。

### 1.3 依赖注入方式

```java
// 1. 构造器注入（推荐）：不可变、安全、便于测试
@Service
public class UserService {
    private final UserRepository userRepo;
    private final CacheService cacheService;

    // Spring 4.3+ 单构造器可省略 @Autowired
    public UserService(UserRepository userRepo, CacheService cacheService) {
        this.userRepo = userRepo;
        this.cacheService = cacheService;
    }
}

// 2. Setter 注入：适用于可选依赖
@Service
public class OrderService {
    private NotificationService notificationService;

    @Autowired(required = false)
    public void setNotificationService(NotificationService notificationService) {
        this.notificationService = notificationService;
    }
}

// 3. 字段注入（不推荐）：无法声明 final，不便于单元测试
@Service
public class ProductService {
    @Autowired
    private ProductRepository productRepo; // 不推荐
}
```

### 1.4 三级缓存解决循环依赖

Spring 通过三级缓存机制解决单例 Bean 的 setter/字段注入循环依赖问题：

| 缓存级别 | 名称 | 存储内容 |
|---------|------|---------|
| 一级缓存 | singletonObjects | 完全初始化好的 Bean（成品） |
| 二级缓存 | earlySingletonObjects | 提前暴露的 Bean（半成品，已实例化但未完成属性注入） |
| 三级缓存 | singletonFactories | Bean 的 ObjectFactory（用于生成早期引用，支持 AOP 代理） |

**循环依赖解决流程（以 A 依赖 B，B 依赖 A 为例）**：

```
1. 创建 A：实例化 A → 将 A 的 ObjectFactory 放入三级缓存
2. 填充 A 的属性：发现依赖 B → 去创建 B
3. 创建 B：实例化 B → 将 B 的 ObjectFactory 放入三级缓存
4. 填充 B 的属性：发现依赖 A → 从三级缓存获取 A 的 ObjectFactory
   → 调用 getObject() 获取 A 的早期引用（可能是代理对象）
   → 将 A 放入二级缓存，移除三级缓存
5. B 的属性填充完成 → B 初始化完成 → B 放入一级缓存
6. 回到 A 的属性填充 → 注入 B → A 初始化完成 → A 放入一级缓存
```

**无法解决的循环依赖场景**：
- 构造器注入的循环依赖（实例化阶段就需要依赖，无法提前暴露）
- prototype 作用域的循环依赖（每次都创建新实例，不使用缓存）
- `@Async` 导致的循环依赖（代理对象替换时机不同）

**解决构造器循环依赖的方法**：
- 使用 `@Lazy` 延迟加载其中一个依赖
- 重构代码，消除循环依赖

```java
// 使用 @Lazy 打破构造器循环依赖
@Service
public class A {
    public A(@Lazy B b) { this.b = b; }
}
```

---

## 2. Bean 生命周期完整链路

### 2.1 完整生命周期流程

```
┌──────────────────────────────────────────────────────┐
│ 1. BeanDefinition 加载与解析                           │
│    - XML 解析 / 注解扫描 / Java Config                 │
│    - 生成 BeanDefinition 注册到 BeanDefinitionRegistry │
├──────────────────────────────────────────────────────┤
│ 2. Bean 实例化（Instantiation）                        │
│    - 通过反射调用构造函数创建对象                         │
│    - InstantiationAwareBeanPostProcessor              │
│      .postProcessBeforeInstantiation()                │
│    - 构造器实例化                                      │
│    - InstantiationAwareBeanPostProcessor              │
│      .postProcessAfterInstantiation()                 │
├──────────────────────────────────────────────────────┤
│ 3. 属性填充（Populate Properties）                     │
│    - 依赖注入（@Autowired、@Value、@Resource）          │
│    - InstantiationAwareBeanPostProcessor              │
│      .postProcessProperties()                         │
├──────────────────────────────────────────────────────┤
│ 4. Aware 接口回调                                     │
│    - BeanNameAware.setBeanName()                      │
│    - BeanClassLoaderAware.setBeanClassLoader()        │
│    - BeanFactoryAware.setBeanFactory()                │
│    - EnvironmentAware.setEnvironment()                │
│    - ApplicationContextAware.setApplicationContext()   │
├──────────────────────────────────────────────────────┤
│ 5. 初始化前处理                                       │
│    - BeanPostProcessor.postProcessBeforeInitialization│
│    - 此阶段执行 @PostConstruct                         │
├──────────────────────────────────────────────────────┤
│ 6. 初始化                                            │
│    - InitializingBean.afterPropertiesSet()            │
│    - 自定义 init-method                                │
├──────────────────────────────────────────────────────┤
│ 7. 初始化后处理                                       │
│    - BeanPostProcessor.postProcessAfterInitialization │
│    - AOP 代理在此阶段生成                               │
├──────────────────────────────────────────────────────┤
│ 8. Bean 就绪，放入容器，供应用使用                       │
├──────────────────────────────────────────────────────┤
│ 9. 销毁                                              │
│    - @PreDestroy                                      │
│    - DisposableBean.destroy()                         │
│    - 自定义 destroy-method                             │
└──────────────────────────────────────────────────────┘
```

### 2.2 初始化回调的执行顺序

```java
@Component
public class LifecycleDemo implements InitializingBean, DisposableBean,
        BeanNameAware, ApplicationContextAware {

    private String beanName;

    // 1. Aware 回调
    @Override
    public void setBeanName(String name) {
        this.beanName = name;
        System.out.println("1. BeanNameAware.setBeanName: " + name);
    }

    @Override
    public void setApplicationContext(ApplicationContext ctx) {
        System.out.println("2. ApplicationContextAware.setApplicationContext");
    }

    // 2. @PostConstruct（BeanPostProcessor 阶段执行）
    @PostConstruct
    public void postConstruct() {
        System.out.println("3. @PostConstruct");
    }

    // 3. InitializingBean
    @Override
    public void afterPropertiesSet() {
        System.out.println("4. InitializingBean.afterPropertiesSet");
    }

    // 4. @PreDestroy
    @PreDestroy
    public void preDestroy() {
        System.out.println("5. @PreDestroy");
    }

    // 5. DisposableBean
    @Override
    public void destroy() {
        System.out.println("6. DisposableBean.destroy");
    }
}
```

### 2.3 Bean 的作用域

| 作用域 | 说明 | 线程安全 |
|--------|------|---------|
| singleton | 默认，整个容器一个实例 | 不保证（需要自行处理） |
| prototype | 每次 getBean 创建新实例 | 通常安全（独立实例） |
| request | 每个 HTTP 请求一个实例 | Web 环境安全 |
| session | 每个 HTTP 会话一个实例 | Web 环境安全 |
| application | 每个 ServletContext 一个实例 | 类似 singleton |
| websocket | 每个 WebSocket 会话一个实例 | WebSocket 环境安全 |

---

## 3. AOP（面向切面编程）

### 3.1 核心概念

- **切面（Aspect）**：横切关注点的模块化，包含通知和切入点
- **通知（Advice）**：切面在特定连接点执行的动作
- **切入点（Pointcut）**：定义通知作用位置的表达式
- **连接点（JoinPoint）**：程序执行中的某个点，Spring AOP 仅支持方法级别
- **织入（Weaving）**：将切面应用到目标对象的过程
- **目标对象（Target）**：被代理的原始对象
- **代理对象（Proxy）**：AOP 创建的增强后的对象

### 3.2 通知类型

```java
@Aspect
@Component
public class LogAspect {

    // 前置通知
    @Before("execution(* com.example.service.*.*(..))")
    public void before(JoinPoint jp) {
        System.out.println("方法开始: " + jp.getSignature().getName());
    }

    // 后置通知（无论是否异常都执行）
    @After("execution(* com.example.service.*.*(..))")
    public void after(JoinPoint jp) {
        System.out.println("方法结束: " + jp.getSignature().getName());
    }

    // 返回后通知
    @AfterReturning(pointcut = "execution(* com.example.service.*.*(..))",
                    returning = "result")
    public void afterReturning(JoinPoint jp, Object result) {
        System.out.println("方法返回: " + result);
    }

    // 异常后通知
    @AfterThrowing(pointcut = "execution(* com.example.service.*.*(..))",
                   throwing = "ex")
    public void afterThrowing(JoinPoint jp, Exception ex) {
        System.out.println("方法异常: " + ex.getMessage());
    }

    // 环绕通知（最强大，可以控制是否执行目标方法）
    @Around("execution(* com.example.service.*.*(..))")
    public Object around(ProceedingJoinPoint pjp) throws Throwable {
        long start = System.currentTimeMillis();
        try {
            Object result = pjp.proceed(); // 执行目标方法
            return result;
        } finally {
            long cost = System.currentTimeMillis() - start;
            System.out.println(pjp.getSignature() + " 耗时: " + cost + "ms");
        }
    }
}
```

### 3.3 切入点表达式

```java
// execution 表达式：execution(修饰符? 返回类型 包名.类名.方法名(参数) 异常?)
@Pointcut("execution(public * com.example.service..*.*(..))")
public void serviceLayer() {}

// @annotation：匹配带有特定注解的方法
@Pointcut("@annotation(com.example.annotation.Log)")
public void logAnnotation() {}

// @within：匹配带有特定注解的类中的所有方法
@Pointcut("@within(org.springframework.stereotype.Service)")
public void withinService() {}

// 组合使用
@Before("serviceLayer() && !logAnnotation()")
public void beforeAdvice() {}
```

### 3.4 AOP 实现原理：JDK 动态代理 vs CGLIB

| 特性 | JDK 动态代理 | CGLIB 代理 |
|------|-------------|-----------|
| 原理 | 基于 java.lang.reflect.Proxy 和 InvocationHandler | 基于字节码生成（ASM 库），创建目标类的子类 |
| 要求 | 目标类必须实现接口 | 目标类不能是 final 类，方法不能是 final |
| 性能 | 创建快，调用稍慢 | 创建慢，调用快（JDK 8+ 差距已很小） |
| Spring 默认策略 | Spring Framework 默认优先使用 | Spring Boot 2.x+ 默认使用 CGLIB |

**代理生成核心流程**：

```
1. Spring 启动 → 扫描所有 BeanPostProcessor
2. AbstractAutoProxyCreator（AOP 核心处理器）
   └─ postProcessAfterInitialization() 阶段
3. 判断 Bean 是否需要代理（是否有匹配的 Advisor/切面）
4. 选择代理方式：
   - proxyTargetClass = true → CGLIB
   - 目标类实现了接口且 proxyTargetClass = false → JDK 动态代理
5. 生成代理对象，放入容器替换原始 Bean
```

**织入时机**：Spring AOP 是运行时织入（Runtime Weaving），在 Bean 的后置处理阶段通过动态代理实现。与之对比，AspectJ 支持编译时织入和类加载时织入，功能更强大但配置更复杂。

---

## 4. @Transactional 事务原理

### 4.1 实现原理

```
1. Spring 通过 AOP 为 @Transactional 标注的方法创建代理
2. 代理方法执行时：
   a. TransactionInterceptor 拦截方法调用
   b. TransactionManager 获取数据库连接，设置 autoCommit=false
   c. 执行目标方法
   d. 无异常 → commit()
   e. 有异常 → 判断是否匹配 rollbackFor → rollback() 或 commit()
3. 事务信息通过 ThreadLocal（TransactionSynchronizationManager）绑定到当前线程
```

### 4.2 事务传播行为

| 传播行为 | 说明 | 使用场景 |
|---------|------|---------|
| REQUIRED | 默认。有事务加入，没有则创建 | 绝大多数业务方法 |
| REQUIRES_NEW | 总是创建新事务，挂起当前事务 | 日志记录（不受外层事务回滚影响） |
| NESTED | 嵌套事务，使用 Savepoint | 批量操作中部分失败的回滚 |
| SUPPORTS | 有事务加入，没有就非事务执行 | 查询方法 |
| NOT_SUPPORTED | 非事务执行，挂起当前事务 | 不需要事务保证的操作 |
| MANDATORY | 必须在已有事务中执行，否则抛异常 | 强制要求调用方有事务 |
| NEVER | 必须在无事务环境中执行，否则抛异常 | 不允许在事务中执行的操作 |

```java
@Service
public class OrderService {

    @Transactional(rollbackFor = Exception.class)
    public void createOrder(Order order) {
        orderDao.insert(order);
        // 新开一个独立事务记录日志，即使外层回滚，日志也保留
        logService.recordLog(order);
    }
}

@Service
public class LogService {
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void recordLog(Order order) {
        logDao.insert(new OperationLog(order));
    }
}
```

### 4.3 事务隔离级别

| 隔离级别 | 脏读 | 不可重复读 | 幻读 | 说明 |
|---------|------|----------|------|------|
| DEFAULT | - | - | - | 使用数据库默认隔离级别 |
| READ_UNCOMMITTED | 可能 | 可能 | 可能 | 最低级别 |
| READ_COMMITTED | 不可能 | 可能 | 可能 | Oracle 默认 |
| REPEATABLE_READ | 不可能 | 不可能 | 可能 | MySQL InnoDB 默认 |
| SERIALIZABLE | 不可能 | 不可能 | 不可能 | 最高级别，性能最差 |

### 4.4 @Transactional 失效的 7 大场景

```java
// 1. 方法不是 public（Spring AOP 无法代理非 public 方法）
@Transactional
private void doSomething() { } // 失效！

// 2. 同类内部方法调用（绕过了代理对象，直接调用 this）
@Service
public class UserService {
    public void methodA() {
        this.methodB(); // 失效！没有经过代理
    }
    @Transactional
    public void methodB() { }
}
// 解决：注入自身代理 或使用 AopContext.currentProxy()

// 3. 异常被 catch 吞掉，Spring 感知不到异常
@Transactional
public void method() {
    try {
        dao.update(...);
        int i = 1 / 0;
    } catch (Exception e) {
        log.error("error", e); // 失效！异常被吞掉
    }
}

// 4. rollbackFor 未指定正确的异常
@Transactional // 默认只回滚 RuntimeException 和 Error
public void method() throws Exception {
    throw new IOException("file error"); // 不会回滚！
}
// 解决：@Transactional(rollbackFor = Exception.class)

// 5. 数据库引擎不支持事务（如 MyISAM）

// 6. Bean 没有被 Spring 管理（没有 @Service 等注解）

// 7. 多线程场景：新线程中不在同一个事务中
@Transactional
public void method() {
    new Thread(() -> {
        dao.update(...); // 不在同一个事务中！
    }).start();
}
```

---

## 5. Spring Events 事件机制

### 5.1 核心组件

```java
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
    private ApplicationEventPublisher publisher;

    public void createOrder(Order order) {
        orderDao.save(order);
        publisher.publishEvent(new OrderCreatedEvent(this, order));
    }
}

// 3. 监听事件
@Component
public class OrderEventListener {

    @EventListener
    public void handleOrderCreated(OrderCreatedEvent event) {
        // 发送通知、更新统计等
        System.out.println("订单创建: " + event.getOrder().getId());
    }

    // 异步监听
    @Async
    @EventListener
    public void handleOrderCreatedAsync(OrderCreatedEvent event) {
        // 异步处理，不阻塞主流程
    }

    // 条件监听
    @EventListener(condition = "#event.order.amount > 1000")
    public void handleLargeOrder(OrderCreatedEvent event) {
        // 只处理大额订单
    }
}
```

### 5.2 @TransactionalEventListener

```java
// 在事务提交后才执行监听逻辑，避免事务回滚后还发了通知
@TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
public void handleAfterCommit(OrderCreatedEvent event) {
    notificationService.sendOrderConfirmation(event.getOrder());
}
```

---

## 6. SpEL（Spring Expression Language）

```java
// @Value 中使用 SpEL
@Value("#{systemProperties['user.home']}")
private String userHome;

@Value("#{T(java.lang.Math).random() * 100}")
private double randomNumber;

@Value("#{userService.getDefaultName()}")
private String defaultName;

// @Cacheable 中使用 SpEL
@Cacheable(value = "users", key = "#id")
public User getUser(Long id) { }

@Cacheable(value = "users", key = "#user.id", condition = "#user.age > 18")
public User saveUser(User user) { }

// @PreAuthorize 中使用 SpEL
@PreAuthorize("hasRole('ADMIN') or #userId == authentication.principal.id")
public void deleteUser(Long userId) { }
```

---

## 7. 条件装配

### @Conditional 系列注解

```java
// Spring Boot 自动配置大量使用条件注解
@Configuration
@ConditionalOnClass(DataSource.class)            // classpath 存在某个类
@ConditionalOnMissingBean(DataSource.class)      // 容器中不存在某个 Bean
@ConditionalOnProperty(name = "app.cache.enabled", havingValue = "true")  // 配置属性
@ConditionalOnBean(RedisConnectionFactory.class)  // 容器中存在某个 Bean
@ConditionalOnWebApplication                      // Web 应用环境
@ConditionalOnExpression("#{${app.feature.enabled:false}}")  // SpEL 表达式
public class CacheAutoConfiguration {

    @Bean
    @ConditionalOnMissingBean
    public CacheManager cacheManager() {
        return new ConcurrentMapCacheManager();
    }
}

// 自定义条件
public class OnLinuxCondition implements Condition {
    @Override
    public boolean matches(ConditionContext context, AnnotatedTypeMetadata metadata) {
        return context.getEnvironment().getProperty("os.name").contains("Linux");
    }
}

@Bean
@Conditional(OnLinuxCondition.class)
public FileService linuxFileService() {
    return new LinuxFileService();
}
```

### @Profile 环境配置

```java
@Configuration
@Profile("dev")
public class DevDataSourceConfig {
    @Bean
    public DataSource dataSource() {
        return new HikariDataSource(); // 开发环境配置
    }
}

@Configuration
@Profile("prod")
public class ProdDataSourceConfig {
    @Bean
    public DataSource dataSource() {
        return new HikariDataSource(); // 生产环境配置
    }
}
```

---

## 8. Spring 中的设计模式

| 设计模式 | 在 Spring 中的应用 |
|---------|-------------------|
| 工厂模式 | BeanFactory 创建和管理 Bean |
| 单例模式 | Bean 默认作用域为 singleton |
| 代理模式 | AOP 通过 JDK/CGLIB 代理实现 |
| 模板方法 | JdbcTemplate、RestTemplate、TransactionTemplate |
| 观察者模式 | ApplicationEvent 事件机制 |
| 适配器模式 | HandlerAdapter 适配不同类型的 Handler |
| 策略模式 | Resource 接口的不同实现（ClassPathResource、FileSystemResource） |
| 责任链模式 | Interceptor 拦截器链 |
| 装饰者模式 | BeanWrapper 对 Bean 的增强 |
| 委派模式 | DispatcherServlet 将请求委派给具体 Handler |

---

## 9. 常用注解速查

| 注解 | 作用 | 所属层 |
|------|------|--------|
| @Component | 通用组件注册 | 通用 |
| @Service | 业务层组件 | Service |
| @Repository | 数据层组件（自动转换异常） | DAO |
| @Controller | 控制层组件 | Controller |
| @RestController | @Controller + @ResponseBody | Controller |
| @Configuration | 声明配置类 | 配置 |
| @Bean | 在配置类中声明一个 Bean | 配置 |
| @Autowired | 按类型自动注入 | 注入 |
| @Qualifier | 按名称指定注入的 Bean | 注入 |
| @Resource | JSR-250 标准，默认按名称注入 | 注入 |
| @Value | 注入配置值或 SpEL 表达式 | 注入 |
| @Scope | 指定 Bean 作用域 | 配置 |
| @Lazy | 延迟初始化 | 配置 |
| @Primary | 多个同类型 Bean 时标记优先 | 配置 |
| @DependsOn | 指定 Bean 的加载顺序 | 配置 |
| @Order | 指定 Bean 排序 | 配置 |
| @PostConstruct | 初始化回调 | 生命周期 |
| @PreDestroy | 销毁回调 | 生命周期 |

---

## 10. 高频面试题

### Q1：IoC 和 AOP 分别是什么？解决什么问题？
**IoC**：控制反转，将对象创建和依赖管理交给 Spring 容器。解决对象之间的耦合问题，实现松耦合、便于测试。
**AOP**：面向切面编程，将日志、事务、权限等横切关注点从业务逻辑中分离。解决代码重复和关注点分散的问题。

### Q2：BeanFactory 和 ApplicationContext 的区别？
BeanFactory 是 Spring IoC 容器的根接口，提供最基本的 Bean 管理能力，懒加载。ApplicationContext 继承 BeanFactory 并扩展了国际化、事件机制、AOP 集成、资源访问等能力，启动时预加载所有单例 Bean。开发中一般使用 ApplicationContext。

### Q3：Spring 如何解决循环依赖？
通过三级缓存：一级缓存存放成品 Bean，二级缓存存放半成品 Bean，三级缓存存放 ObjectFactory。当发生循环依赖时，通过三级缓存提前暴露未完成初始化的 Bean 引用。注意：构造器注入和 prototype 作用域的循环依赖无法解决。

### Q4：Bean 的完整生命周期？
实例化 -> 属性填充 -> Aware 接口回调 -> BeanPostProcessor.postProcessBeforeInitialization -> @PostConstruct -> InitializingBean.afterPropertiesSet -> 自定义 init-method -> BeanPostProcessor.postProcessAfterInitialization -> 使用 -> @PreDestroy -> DisposableBean.destroy -> 自定义 destroy-method。

### Q5：@Transactional 的原理？什么情况下会失效？
原理：基于 AOP 代理，TransactionInterceptor 拦截方法调用，通过 TransactionManager 管理事务。失效场景：非 public 方法、同类内部调用、异常被 catch、rollbackFor 不匹配、数据库引擎不支持事务、Bean 未被 Spring 管理、多线程。

### Q6：JDK 动态代理和 CGLIB 的区别？
JDK 动态代理基于接口（Proxy + InvocationHandler），要求目标类实现接口。CGLIB 基于字节码生成子类，不要求接口但不能代理 final 类/方法。Spring Boot 2.x 默认使用 CGLIB。

### Q7：Spring 事务传播行为有哪些？
七种：REQUIRED（默认，有则加入无则创建）、REQUIRES_NEW（新建事务）、NESTED（嵌套事务）、SUPPORTS（有则加入）、NOT_SUPPORTED（非事务）、MANDATORY（必须有事务）、NEVER（必须无事务）。

### Q8：@Autowired 和 @Resource 的区别？
@Autowired 是 Spring 注解，默认按类型注入，需要 @Qualifier 指定名称。@Resource 是 JSR-250 标准注解，默认按名称注入，找不到再按类型。推荐使用构造器注入替代字段注入。

### Q9：Spring 中用到了哪些设计模式？
工厂模式（BeanFactory）、单例模式（singleton Bean）、代理模式（AOP）、模板方法（JdbcTemplate）、观察者模式（ApplicationEvent）、适配器模式（HandlerAdapter）、策略模式（Resource 实现）、责任链模式（拦截器链）、装饰者模式（BeanWrapper）。

### Q10：同类内部调用 @Transactional 方法为什么会失效？怎么解决？
因为内部调用走的是 `this` 而非代理对象，事务增强逻辑不会执行。解决方案：(1) 注入自身代理（`@Autowired private UserService self;`）；(2) 使用 `AopContext.currentProxy()` 获取代理对象；(3) 将被调方法拆到另一个 Service 类中。

### Q11：三级缓存为什么不能是两级缓存？
如果没有三级缓存（ObjectFactory），在存在 AOP 的情况下，无法在需要时才创建代理对象。三级缓存的 ObjectFactory 在被调用时才决定返回原始对象还是代理对象，做到了延迟代理创建。如果只用二级缓存，就需要在实例化后立刻创建代理，打破了 Spring "在初始化后才创建代理" 的设计原则。

### Q12：@PostConstruct、InitializingBean、init-method 的执行顺序？
@PostConstruct（BeanPostProcessor 阶段执行） -> InitializingBean.afterPropertiesSet() -> 自定义 init-method。销毁阶段相反：@PreDestroy -> DisposableBean.destroy() -> 自定义 destroy-method。
