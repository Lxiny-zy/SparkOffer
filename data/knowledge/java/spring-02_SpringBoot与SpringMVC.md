# Spring Boot 与 Spring MVC

## 1. Spring Boot 核心特性

### 1.1 核心理念

- **约定优于配置（Convention over Configuration）**：提供合理的默认配置，减少显式配置
- **起步依赖（Starter）**：`spring-boot-starter-*` 聚合常用依赖，一键引入
- **自动配置（Auto-Configuration）**：根据 classpath 中的依赖自动配置 Bean
- **内嵌服务器**：内置 Tomcat/Jetty/Undertow，打成可执行 JAR 直接运行
- **Actuator 监控**：提供健康检查、指标监控等运维端点
- **无代码生成、无 XML 配置**：纯 Java 注解驱动

### 1.2 自动配置原理（核心考点）

#### 启动注解拆解

```java
@SpringBootApplication
// 等价于以下三个注解的组合：
@SpringBootConfiguration     // 本质是 @Configuration，标识这是一个配置类
@EnableAutoConfiguration     // 开启自动配置的核心
@ComponentScan               // 扫描当前包及子包下的组件
public class Application {
    public static void main(String[] args) {
        SpringApplication.run(Application.class, args);
    }
}
```

#### @EnableAutoConfiguration 原理

```
@EnableAutoConfiguration
   └─ @Import(AutoConfigurationImportSelector.class)
       └─ selectImports() 方法
           └─ SpringFactoriesLoader.loadFactoryNames()
               ├─ Spring Boot 2.x：读取 META-INF/spring.factories
               └─ Spring Boot 3.x：读取 META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports
           └─ 过滤：
               ├─ @ConditionalOnClass：classpath 中必须存在某个类
               ├─ @ConditionalOnMissingBean：容器中不存在某个 Bean
               ├─ @ConditionalOnProperty：配置文件中指定属性满足条件
               └─ 其他 @Conditional 条件
           └─ 最终只有条件满足的自动配置类才会生效
```

#### 自动配置类示例

```java
@AutoConfiguration
@ConditionalOnClass(DataSource.class)                 // classpath 有 DataSource
@ConditionalOnProperty(prefix = "spring.datasource",
                       name = "url")                   // 配置了数据源 URL
@EnableConfigurationProperties(DataSourceProperties.class)
public class DataSourceAutoConfiguration {

    @Bean
    @ConditionalOnMissingBean                          // 用户没有自定义 DataSource
    public DataSource dataSource(DataSourceProperties properties) {
        return DataSourceBuilder.create()
            .url(properties.getUrl())
            .username(properties.getUsername())
            .password(properties.getPassword())
            .build();
    }
}
```

**自动配置的核心逻辑**：Spring Boot 在启动时加载所有候选的自动配置类，通过条件注解（@Conditional）过滤，只有满足条件的才会生效。用户自定义的 Bean 优先级高于自动配置（@ConditionalOnMissingBean 保证）。

### 1.3 自定义 Starter

```
my-spring-boot-starter/
├── src/main/java/
│   └── com/example/autoconfigure/
│       ├── MyServiceAutoConfiguration.java
│       └── MyServiceProperties.java
└── src/main/resources/
    └── META-INF/
        └── spring/
            └── org.springframework.boot.autoconfigure.AutoConfiguration.imports
            # 内容：com.example.autoconfigure.MyServiceAutoConfiguration
```

```java
@ConfigurationProperties(prefix = "my.service")
public class MyServiceProperties {
    private String name = "default";
    private int timeout = 3000;
    // getter/setter...
}

@AutoConfiguration
@EnableConfigurationProperties(MyServiceProperties.class)
@ConditionalOnClass(MyService.class)
public class MyServiceAutoConfiguration {

    @Bean
    @ConditionalOnMissingBean
    public MyService myService(MyServiceProperties properties) {
        return new MyService(properties.getName(), properties.getTimeout());
    }
}
```

---

## 2. 内嵌 Tomcat 启动流程

### 2.1 SpringApplication.run() 完整流程

```
SpringApplication.run()
├── 1. 创建 SpringApplication 实例
│   ├── 推断应用类型（SERVLET / REACTIVE / NONE）
│   ├── 加载 ApplicationContextInitializer
│   └── 加载 ApplicationListener
├── 2. 运行 SpringApplicationRunListeners.starting()
├── 3. 准备 Environment（加载配置文件、环境变量、命令行参数）
├── 4. 创建 ApplicationContext
│   └── SERVLET 类型 → AnnotationConfigServletWebServerApplicationContext
├── 5. prepareContext()（准备上下文，加载 BeanDefinition）
├── 6. refreshContext()（核心！）
│   └── AbstractApplicationContext.refresh()
│       ├── invokeBeanFactoryPostProcessors() → 处理自动配置
│       ├── registerBeanPostProcessors()
│       ├── onRefresh() → 创建内嵌 Web 服务器
│       │   └── ServletWebServerApplicationContext.createWebServer()
│       │       ├── 获取 ServletWebServerFactory（默认 TomcatServletWebServerFactory）
│       │       ├── 创建 Tomcat 实例
│       │       ├── 配置 Connector（端口、协议）
│       │       ├── 注册 DispatcherServlet
│       │       └── 启动 Tomcat
│       └── finishRefresh() → 发布 ContextRefreshedEvent
├── 7. 执行 ApplicationRunner / CommandLineRunner
└── 8. 运行 SpringApplicationRunListeners.started()
```

### 2.2 内嵌 Tomcat 关键配置

```yaml
server:
  port: 8080                        # 端口
  tomcat:
    max-threads: 200                # 最大工作线程数（默认 200）
    min-spare-threads: 10           # 最小空闲线程数
    max-connections: 8192           # 最大连接数
    accept-count: 100               # 等待队列长度
    connection-timeout: 20000       # 连接超时（ms）
    uri-encoding: UTF-8             # URI 编码
  servlet:
    context-path: /api              # 上下文路径
```

---

## 3. Spring MVC 请求处理全链路

### 3.1 完整请求处理流程

```
客户端发起 HTTP 请求
       │
       ▼
┌─────────────────┐
│   Filter Chain   │  Servlet 规范的过滤器链
│ (CharacterEncoding│  （编码、CORS、安全等）
│  Filter, etc.)   │
└───────┬─────────┘
        ▼
┌─────────────────┐
│DispatcherServlet │  前端控制器（核心）
│  doDispatch()    │
└───────┬─────────┘
        ▼
┌─────────────────┐
│ HandlerMapping   │  根据 URL 找到匹配的 Handler（Controller 方法）
│                  │  常用：RequestMappingHandlerMapping
│                  │  返回 HandlerExecutionChain（Handler + 拦截器）
└───────┬─────────┘
        ▼
┌─────────────────┐
│  Interceptor     │  preHandle() — 拦截器前置处理
│  .preHandle()    │  返回 false 则中断请求
└───────┬─────────┘
        ▼
┌─────────────────┐
│ HandlerAdapter   │  适配不同类型的 Handler
│                  │  RequestMappingHandlerAdapter：
│                  │  ├─ 参数解析（HandlerMethodArgumentResolver）
│                  │  │  @RequestBody → RequestResponseBodyMethodProcessor
│                  │  │  @PathVariable → PathVariableMethodArgumentResolver
│                  │  │  @RequestParam → RequestParamMethodArgumentResolver
│                  │  ├─ 数据绑定与参数校验（@Valid / @Validated）
│                  │  ├─ 调用 Controller 方法
│                  │  └─ 返回值处理（HandlerMethodReturnValueHandler）
│                  │     @ResponseBody → HttpMessageConverter (Jackson)
└───────┬─────────┘
        ▼
┌─────────────────┐
│  Interceptor     │  postHandle() — 拦截器后置处理
│  .postHandle()   │
└───────┬─────────┘
        ▼
┌─────────────────┐
│ ViewResolver     │  如果返回视图名，解析为 View 对象
│ (非 REST 场景)    │  REST 接口（@ResponseBody）跳过此步
└───────┬─────────┘
        ▼
┌─────────────────┐
│  Interceptor     │  afterCompletion() — 请求完成后处理
│.afterCompletion()│  （无论是否异常）
└───────┬─────────┘
        ▼
    响应返回客户端
```

### 3.2 核心组件详解

**HandlerMapping**：URL 到 Handler 的映射关系
```java
// RequestMappingHandlerMapping：处理 @RequestMapping 注解
// 启动时扫描所有 @Controller 类中的 @RequestMapping 方法，建立 URL -> Method 映射
```

**HandlerAdapter**：适配不同类型的 Handler 执行
```java
// RequestMappingHandlerAdapter：处理 @RequestMapping 标注的方法
// 核心流程：
// 1. ArgumentResolver 解析参数
// 2. 调用 Controller 方法
// 3. ReturnValueHandler 处理返回值
```

**HttpMessageConverter**：请求体/响应体转换
```java
// MappingJackson2HttpMessageConverter：JSON <-> Java 对象
// StringHttpMessageConverter：String <-> 文本
// 自定义 Converter：
@Configuration
public class WebConfig implements WebMvcConfigurer {
    @Override
    public void configureMessageConverters(List<HttpMessageConverter<?>> converters) {
        converters.add(new MappingJackson2HttpMessageConverter(objectMapper()));
    }
}
```

### 3.3 常用注解

```java
@RestController  // = @Controller + @ResponseBody
@RequestMapping("/api/users")
public class UserController {

    @GetMapping("/{id}")
    public ResponseEntity<User> getUser(@PathVariable Long id) {
        User user = userService.getById(id);
        return user != null
            ? ResponseEntity.ok(user)
            : ResponseEntity.notFound().build();
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public User createUser(@RequestBody @Validated(Create.class) UserDTO dto) {
        return userService.create(dto);
    }

    @PutMapping("/{id}")
    public User updateUser(@PathVariable Long id,
                           @RequestBody @Validated(Update.class) UserDTO dto) {
        return userService.update(id, dto);
    }

    @GetMapping
    public Page<User> listUsers(
        @RequestParam(defaultValue = "1") int page,
        @RequestParam(defaultValue = "10") int size,
        @RequestParam(required = false) String keyword
    ) {
        return userService.search(keyword, PageRequest.of(page - 1, size));
    }

    @DeleteMapping("/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void deleteUser(@PathVariable Long id) {
        userService.delete(id);
    }
}
```

### 3.4 参数绑定注解

| 注解 | 来源 | 示例 | 说明 |
|------|------|------|------|
| @PathVariable | URL 路径变量 | `/users/{id}` | RESTful 路径参数 |
| @RequestParam | 查询字符串 | `?name=xxx&age=20` | URL 查询参数 |
| @RequestBody | 请求体 | POST/PUT 的 JSON body | 需要 HttpMessageConverter |
| @RequestHeader | 请求头 | `Authorization: Bearer xxx` | 获取 HTTP 头 |
| @CookieValue | Cookie | `sessionId=abc123` | 获取 Cookie 值 |
| @ModelAttribute | 表单数据 | form-data 提交 | 自动绑定到对象 |
| @RequestPart | Multipart | 文件上传 | 处理 multipart 请求 |

---

## 4. 拦截器 vs 过滤器 vs AOP 对比

| 特性 | Filter（过滤器） | Interceptor（拦截器） | AOP（切面） |
|------|-----------------|---------------------|------------|
| 规范 | Servlet 规范 | Spring MVC | Spring AOP |
| 作用范围 | 所有请求（包括静态资源） | Controller 方法 | 任何 Spring Bean 方法 |
| 能否访问 Handler | 不能 | 能（HandlerMethod） | 能（通过 JoinPoint） |
| 能否获取 Bean | 需要特殊处理 | 可以 | 可以 |
| 异常处理 | 不走 @ExceptionHandler | 不走 @ExceptionHandler | 可以配合使用 |
| 执行顺序 | 最先执行 | Filter 之后 | Interceptor 之后 |
| 典型场景 | 编码、CORS、安全过滤 | 登录校验、日志、权限 | 事务、缓存、审计日志 |
| 配置方式 | @WebFilter / FilterRegistrationBean | WebMvcConfigurer | @Aspect |

### 执行顺序

```
Filter.doFilter (before)
  └─ Interceptor.preHandle
      └─ AOP @Before / @Around (before)
          └─ Controller 方法
      └─ AOP @After / @Around (after)
  └─ Interceptor.postHandle
  └─ Interceptor.afterCompletion
Filter.doFilter (after)
```

### 拦截器实现示例

```java
@Component
public class AuthInterceptor implements HandlerInterceptor {

    @Override
    public boolean preHandle(HttpServletRequest request,
                             HttpServletResponse response,
                             Object handler) throws Exception {
        // handler 可能不是 HandlerMethod（如静态资源）
        if (!(handler instanceof HandlerMethod)) {
            return true;
        }

        String token = request.getHeader("Authorization");
        if (token == null || !tokenService.validate(token)) {
            response.setStatus(401);
            response.getWriter().write("{\"code\":401,\"msg\":\"未授权\"}");
            return false;
        }

        // 将用户信息存入 request 属性，后续可用
        UserInfo user = tokenService.parseUser(token);
        request.setAttribute("currentUser", user);
        return true;
    }

    @Override
    public void postHandle(HttpServletRequest request,
                           HttpServletResponse response,
                           Object handler, ModelAndView modelAndView) {
        // Controller 执行后、视图渲染前
    }

    @Override
    public void afterCompletion(HttpServletRequest request,
                                HttpServletResponse response,
                                Object handler, Exception ex) {
        // 请求完成后（无论是否异常），适合清理资源
    }
}

@Configuration
public class WebConfig implements WebMvcConfigurer {
    @Autowired
    private AuthInterceptor authInterceptor;

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(authInterceptor)
            .addPathPatterns("/api/**")
            .excludePathPatterns("/api/login", "/api/register", "/api/public/**")
            .order(1);
    }
}
```

---

## 5. 全局异常处理

### 5.1 @RestControllerAdvice + @ExceptionHandler

```java
@RestControllerAdvice
public class GlobalExceptionHandler {

    // 业务异常
    @ExceptionHandler(BusinessException.class)
    public ResponseEntity<Result<?>> handleBusiness(BusinessException e) {
        log.warn("业务异常: {}", e.getMessage());
        return ResponseEntity.status(e.getHttpStatus())
            .body(Result.fail(e.getCode(), e.getMessage()));
    }

    // 参数校验异常（@Valid/@Validated）
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<Result<?>> handleValidation(MethodArgumentNotValidException e) {
        String message = e.getBindingResult().getFieldErrors().stream()
            .map(error -> error.getField() + ": " + error.getDefaultMessage())
            .collect(Collectors.joining("; "));
        return ResponseEntity.badRequest()
            .body(Result.fail(400, message));
    }

    // 请求参数缺失
    @ExceptionHandler(MissingServletRequestParameterException.class)
    public ResponseEntity<Result<?>> handleMissingParam(
            MissingServletRequestParameterException e) {
        return ResponseEntity.badRequest()
            .body(Result.fail(400, "缺少参数: " + e.getParameterName()));
    }

    // 请求方法不支持
    @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
    public ResponseEntity<Result<?>> handleMethodNotAllowed(
            HttpRequestMethodNotSupportedException e) {
        return ResponseEntity.status(HttpStatus.METHOD_NOT_ALLOWED)
            .body(Result.fail(405, "不支持的请求方法: " + e.getMethod()));
    }

    // 兜底：未知异常
    @ExceptionHandler(Exception.class)
    public ResponseEntity<Result<?>> handleException(Exception e) {
        log.error("未知异常", e);
        return ResponseEntity.status(500)
            .body(Result.fail(500, "服务器内部错误"));
    }
}
```

### 5.2 统一返回格式

```java
@Data
@AllArgsConstructor
public class Result<T> {
    private int code;
    private String message;
    private T data;

    public static <T> Result<T> ok(T data) {
        return new Result<>(200, "success", data);
    }

    public static <T> Result<T> fail(int code, String message) {
        return new Result<>(code, message, null);
    }
}
```

---

## 6. 参数校验

### 6.1 JSR 380（Bean Validation）常用注解

```java
public class UserDTO {
    @NotBlank(message = "用户名不能为空")
    @Size(min = 2, max = 20, message = "用户名长度 2-20")
    private String username;

    @NotBlank(message = "邮箱不能为空")
    @Email(message = "邮箱格式不正确")
    private String email;

    @NotNull(message = "年龄不能为空")
    @Min(value = 1, message = "年龄最小为1")
    @Max(value = 150, message = "年龄最大为150")
    private Integer age;

    @Pattern(regexp = "^1[3-9]\\d{9}$", message = "手机号格式不正确")
    private String phone;

    @NotEmpty(message = "角色列表不能为空")
    private List<@NotBlank String> roles;
}

// Controller 中使用
@PostMapping
public User createUser(@RequestBody @Validated UserDTO dto) {
    return userService.create(dto);
}
```

### 6.2 分组校验

```java
public interface Create {}
public interface Update {}

public class UserDTO {
    @Null(groups = Create.class, message = "创建时不能指定ID")
    @NotNull(groups = Update.class, message = "更新时必须指定ID")
    private Long id;

    @NotBlank(groups = {Create.class, Update.class})
    private String username;
}

@PostMapping
public User create(@RequestBody @Validated(Create.class) UserDTO dto) { }

@PutMapping("/{id}")
public User update(@RequestBody @Validated(Update.class) UserDTO dto) { }
```

### 6.3 自定义校验注解

```java
@Target({ElementType.FIELD})
@Retention(RetentionPolicy.RUNTIME)
@Constraint(validatedBy = PhoneValidator.class)
public @interface Phone {
    String message() default "手机号格式不正确";
    Class<?>[] groups() default {};
    Class<? extends Payload>[] payload() default {};
}

public class PhoneValidator implements ConstraintValidator<Phone, String> {
    private static final Pattern PATTERN = Pattern.compile("^1[3-9]\\d{9}$");

    @Override
    public boolean isValid(String value, ConstraintValidatorContext context) {
        if (value == null) return true; // @NotBlank 负责非空校验
        return PATTERN.matcher(value).matches();
    }
}
```

---

## 7. Actuator 监控

### 7.1 常用端点

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health,info,metrics,env,beans,loggers,threaddump,heapdump
      base-path: /actuator
  endpoint:
    health:
      show-details: always     # 显示详细健康信息
    shutdown:
      enabled: true            # 开启优雅关机端点（生产慎用）
  info:
    env:
      enabled: true
```

| 端点 | 路径 | 说明 |
|------|------|------|
| health | /actuator/health | 健康检查（数据库、Redis、磁盘等） |
| info | /actuator/info | 应用信息 |
| metrics | /actuator/metrics | 指标数据（JVM、HTTP 请求等） |
| env | /actuator/env | 环境变量和配置属性 |
| beans | /actuator/beans | 所有 Bean 列表 |
| loggers | /actuator/loggers | 日志级别管理（可动态修改） |
| threaddump | /actuator/threaddump | 线程转储 |
| heapdump | /actuator/heapdump | 堆转储（下载文件） |
| mappings | /actuator/mappings | 所有 URL 映射 |
| conditions | /actuator/conditions | 自动配置条件评估报告 |

### 7.2 自定义健康指标

```java
@Component
public class CustomHealthIndicator implements HealthIndicator {
    @Override
    public Health health() {
        boolean serviceUp = checkExternalService();
        if (serviceUp) {
            return Health.up()
                .withDetail("service", "running")
                .withDetail("version", "1.0")
                .build();
        }
        return Health.down()
            .withDetail("error", "Service unavailable")
            .build();
    }
}
```

### 7.3 集成 Prometheus + Grafana

```xml
<dependency>
    <groupId>io.micrometer</groupId>
    <artifactId>micrometer-registry-prometheus</artifactId>
</dependency>
```

```yaml
management:
  endpoints:
    web:
      exposure:
        include: prometheus,health
  metrics:
    tags:
      application: my-app
```

---

## 8. 配置管理

### 8.1 配置文件优先级（从高到低）

```
1. 命令行参数：--server.port=9090
2. SPRING_APPLICATION_JSON 环境变量中的 JSON
3. java:comp/env 中的 JNDI 属性
4. System.getProperties() 系统属性
5. OS 环境变量
6. jar 包外的 application-{profile}.yml
7. jar 包内的 application-{profile}.yml
8. jar 包外的 application.yml
9. jar 包内的 application.yml
10. @PropertySource 注解指定的文件
```

### 8.2 多环境配置

```yaml
# application.yml（公共配置）
spring:
  profiles:
    active: dev   # 默认激活 dev 环境

---
# application-dev.yml
server:
  port: 8080
spring:
  datasource:
    url: jdbc:mysql://localhost:3306/dev_db

---
# application-prod.yml
server:
  port: 80
spring:
  datasource:
    url: jdbc:mysql://prod-host:3306/prod_db
```

### 8.3 @ConfigurationProperties 类型安全绑定

```java
@Data
@ConfigurationProperties(prefix = "app.mail")
public class MailProperties {
    private String host;
    private int port = 25;
    private String username;
    private String password;
    private boolean ssl = false;
}

@Configuration
@EnableConfigurationProperties(MailProperties.class)
public class MailConfig {
    @Bean
    public MailSender mailSender(MailProperties properties) {
        return new MailSender(properties);
    }
}
```

---

## 9. CORS 跨域配置

```java
// 方式一：全局配置
@Configuration
public class CorsConfig implements WebMvcConfigurer {
    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/api/**")
            .allowedOrigins("http://localhost:3000")
            .allowedMethods("GET", "POST", "PUT", "DELETE", "OPTIONS")
            .allowedHeaders("*")
            .allowCredentials(true)
            .maxAge(3600);
    }
}

// 方式二：注解方式（单个 Controller）
@CrossOrigin(origins = "http://localhost:3000")
@RestController
public class UserController { }

// 方式三：Filter 方式（最早执行，推荐 Spring Cloud Gateway 场景）
@Bean
public CorsFilter corsFilter() {
    CorsConfiguration config = new CorsConfiguration();
    config.addAllowedOrigin("http://localhost:3000");
    config.addAllowedMethod("*");
    config.addAllowedHeader("*");
    config.setAllowCredentials(true);
    UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
    source.registerCorsConfiguration("/api/**", config);
    return new CorsFilter(source);
}
```

---

## 10. 高频面试题

### Q1：Spring Boot 自动配置的原理？
@SpringBootApplication 包含 @EnableAutoConfiguration，通过 AutoConfigurationImportSelector 加载 META-INF/spring.factories（2.x）或 AutoConfiguration.imports（3.x）中的候选配置类。每个配置类上有 @Conditional 系列条件注解，只有满足条件的才会生效。用户自定义 Bean 优先于自动配置（@ConditionalOnMissingBean）。

### Q2：Spring MVC 的请求处理流程？
请求 -> Filter 链 -> DispatcherServlet -> HandlerMapping（找 Handler） -> Interceptor.preHandle -> HandlerAdapter（参数解析、调用 Controller、返回值处理） -> Interceptor.postHandle -> ViewResolver（非 REST 场景） -> Interceptor.afterCompletion -> 返回响应。

### Q3：@Controller 和 @RestController 的区别？
@RestController = @Controller + @ResponseBody。@Controller 返回视图名，@RestController 的方法返回值直接通过 HttpMessageConverter 转换为 JSON/XML 写入响应体。

### Q4：过滤器和拦截器的区别？
Filter 是 Servlet 规范，作用于所有请求，不能获取 Handler 信息。Interceptor 是 Spring MVC 规范，只作用于 Controller 方法，可以获取 HandlerMethod 信息。Filter 先执行，Interceptor 后执行。

### Q5：Spring Boot 如何实现多环境配置？
通过 spring.profiles.active 指定激活的环境，加载对应的 application-{profile}.yml。可通过命令行参数、环境变量、配置文件等方式指定。

### Q6：如何自定义一个 Spring Boot Starter？
创建自动配置类（@AutoConfiguration + @Conditional 条件注解 + @EnableConfigurationProperties），在 META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports 中注册。

### Q7：SpringBoot 内嵌 Tomcat 的启动流程？
SpringApplication.run() -> 创建 ApplicationContext -> refresh() -> onRefresh() 阶段创建 WebServer -> 获取 ServletWebServerFactory -> 创建 Tomcat 实例 -> 配置 Connector 和 Host -> 注册 DispatcherServlet -> 启动 Tomcat。

### Q8：如何实现统一异常处理？
@RestControllerAdvice + @ExceptionHandler 注解。定义全局异常处理类，用 @ExceptionHandler 标注方法处理不同类型异常，返回统一的错误响应格式。

### Q9：@Validated 和 @Valid 的区别？
@Valid 是 JSR-303 标准注解，不支持分组校验。@Validated 是 Spring 扩展注解，支持分组校验。在 Controller 方法参数上使用 @Validated 可指定校验组。

### Q10：Spring Boot Actuator 的作用？常用端点有哪些？
提供生产级别的监控和管理功能。常用端点：/health（健康检查）、/metrics（指标数据）、/env（环境配置）、/loggers（日志级别动态调整）、/threaddump（线程转储）、/beans（Bean 列表）。可集成 Prometheus + Grafana 实现监控可视化。

### Q11：Spring MVC 中 HandlerMapping 和 HandlerAdapter 的作用？
HandlerMapping 根据请求 URL 找到对应的 Handler（Controller 方法），返回 HandlerExecutionChain（包含 Handler 和拦截器链）。HandlerAdapter 负责适配并执行不同类型的 Handler，包括参数解析、方法调用和返回值处理。

### Q12：如何自定义 HttpMessageConverter？
实现 HttpMessageConverter 接口或继承 AbstractHttpMessageConverter，重写 readInternal/writeInternal 方法。通过 WebMvcConfigurer.configureMessageConverters() 注册到 Spring MVC 中。
