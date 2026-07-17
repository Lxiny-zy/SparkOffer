# Spring Security

## 1. 概览

### 定位
Spring 官方的安全框架，处理**认证（Authentication）**、**授权（Authorization）**、**攻击防护**（CSRF、XSS、Session Fixation 等）。Spring Boot 加入 starter 即可集成。

### 核心概念
- **Authentication（认证）**：你是谁？用户名/密码、Token、OAuth
- **Authorization（授权）**：你能做什么？角色、权限
- **Principal**：当前用户主体
- **GrantedAuthority**：权限/角色
- **SecurityContext**：线程绑定的安全上下文

---

## 2. 核心架构

### Filter Chain

Spring Security 基于 **Servlet Filter Chain**：
```
Request → FilterChain:
  [SecurityContextHolderFilter]
  [HeaderWriterFilter]
  [CsrfFilter]
  [LogoutFilter]
  [UsernamePasswordAuthenticationFilter]
  [BasicAuthenticationFilter]
  [BearerTokenAuthenticationFilter]  (OAuth2)
  [RequestCacheAwareFilter]
  [SecurityContextPersistenceFilter]
  [AnonymousAuthenticationFilter]
  [ExceptionTranslationFilter]
  [AuthorizationFilter]
  → Controller
```

每个 Filter 专注一个职责，可插拔。

### 核心组件

- **AuthenticationManager**：认证管理入口，委托给 Providers
- **AuthenticationProvider**：具体认证实现（DAO、LDAP、OAuth）
- **UserDetailsService**：加载用户信息（通常从 DB）
- **PasswordEncoder**：密码编码/校验（BCrypt 推荐）
- **SecurityContextHolder**：存当前认证信息（ThreadLocal）
- **AccessDecisionManager / AuthorizationManager**：授权决策

---

## 3. 基础配置（Spring Security 6）

### 依赖
```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-security</artifactId>
</dependency>
```

### 最简配置

```java
@Configuration
@EnableWebSecurity
public class SecurityConfig {

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/public/**").permitAll()
                .requestMatchers("/admin/**").hasRole("ADMIN")
                .requestMatchers("/user/**").hasAnyRole("USER", "ADMIN")
                .anyRequest().authenticated()
            )
            .formLogin(form -> form
                .loginPage("/login")
                .defaultSuccessUrl("/home")
                .permitAll()
            )
            .logout(logout -> logout
                .logoutUrl("/logout")
                .logoutSuccessUrl("/login")
            )
            .csrf(csrf -> csrf.ignoringRequestMatchers("/api/**"));
        return http.build();
    }

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }
}
```

### 内存用户（仅测试）

```java
@Bean
public UserDetailsService userDetailsService(PasswordEncoder encoder) {
    UserDetails user = User.builder()
        .username("alice")
        .password(encoder.encode("123456"))
        .roles("USER").build();
    UserDetails admin = User.builder()
        .username("admin")
        .password(encoder.encode("admin"))
        .roles("ADMIN").build();
    return new InMemoryUserDetailsManager(user, admin);
}
```

### DB 用户

```java
@Service
public class MyUserDetailsService implements UserDetailsService {
    @Autowired UserRepository userRepo;

    @Override
    public UserDetails loadUserByUsername(String username) throws UsernameNotFoundException {
        User user = userRepo.findByUsername(username)
            .orElseThrow(() -> new UsernameNotFoundException(username));
        return org.springframework.security.core.userdetails.User.builder()
            .username(user.getUsername())
            .password(user.getPassword())
            .roles(user.getRoles().toArray(new String[0]))
            .build();
    }
}
```

---

## 4. 密码编码

### BCrypt（推荐）
```java
@Bean
public PasswordEncoder passwordEncoder() {
    return new BCryptPasswordEncoder();
}

// 使用
String hash = encoder.encode("mypassword");
boolean match = encoder.matches("mypassword", hash);
```

### DelegatingPasswordEncoder（支持多算法）
```java
@Bean
public PasswordEncoder passwordEncoder() {
    return PasswordEncoderFactories.createDelegatingPasswordEncoder();
}
// 存储格式：{bcrypt}$2a$10$...
// 可平滑升级算法
```

**永远不要**：
- 明文存密码
- 用 MD5 / SHA1（太快，易彩虹表破解）
- 自己发明加密算法

---

## 5. JWT 认证（无状态）

### 为什么 JWT
- 无状态：服务端不存 session
- 跨域：Bearer Token 直接带
- 微服务友好：服务间传递用户身份

### 依赖
```xml
<dependency>
    <groupId>io.jsonwebtoken</groupId>
    <artifactId>jjwt-api</artifactId>
    <version>0.12.6</version>
</dependency>
<dependency>
    <groupId>io.jsonwebtoken</groupId>
    <artifactId>jjwt-impl</artifactId>
</dependency>
<dependency>
    <groupId>io.jsonwebtoken</groupId>
    <artifactId>jjwt-jackson</artifactId>
</dependency>
```

### JWT 工具类

```java
@Component
public class JwtUtil {
    @Value("${jwt.secret}") private String secret;
    @Value("${jwt.expiration}") private long expiration;

    private SecretKey key() {
        return Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8));
    }

    public String generate(String username, List<String> roles) {
        return Jwts.builder()
            .subject(username)
            .claim("roles", roles)
            .issuedAt(new Date())
            .expiration(new Date(System.currentTimeMillis() + expiration))
            .signWith(key())
            .compact();
    }

    public Claims parse(String token) {
        return Jwts.parser().verifyWith(key()).build()
            .parseSignedClaims(token).getPayload();
    }
}
```

### JWT Filter

```java
@Component
public class JwtAuthFilter extends OncePerRequestFilter {
    @Autowired JwtUtil jwtUtil;

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse resp, FilterChain chain)
        throws ServletException, IOException {
        String header = req.getHeader("Authorization");
        if (header != null && header.startsWith("Bearer ")) {
            String token = header.substring(7);
            try {
                Claims claims = jwtUtil.parse(token);
                String username = claims.getSubject();
                List<String> roles = claims.get("roles", List.class);
                List<SimpleGrantedAuthority> authorities = roles.stream()
                    .map(r -> new SimpleGrantedAuthority("ROLE_" + r))
                    .toList();
                var auth = new UsernamePasswordAuthenticationToken(username, null, authorities);
                SecurityContextHolder.getContext().setAuthentication(auth);
            } catch (JwtException e) {
                // 无效 token，不设置认证
            }
        }
        chain.doFilter(req, resp);
    }
}
```

### 配置

```java
@Bean
public SecurityFilterChain chain(HttpSecurity http, JwtAuthFilter jwtFilter) throws Exception {
    http
        .csrf(AbstractHttpConfigurer::disable)
        .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
        .authorizeHttpRequests(a -> a
            .requestMatchers("/api/auth/**").permitAll()
            .anyRequest().authenticated())
        .addFilterBefore(jwtFilter, UsernamePasswordAuthenticationFilter.class);
    return http.build();
}
```

### Refresh Token
- Access Token：短期（15 分钟）
- Refresh Token：长期（7 天），存 Redis
- Access 失效用 Refresh 换新的
- 登出：Refresh 加入黑名单

---

## 6. 方法级安全

### 启用

```java
@Configuration
@EnableMethodSecurity
public class MethodSecurityConfig {}
```

### 使用

```java
@Service
public class UserService {
    @PreAuthorize("hasRole('ADMIN')")
    public void deleteUser(Long id) { ... }

    @PreAuthorize("hasAuthority('user:write') or #id == authentication.principal.id")
    public void updateUser(Long id, UserDto dto) { ... }

    @PostAuthorize("returnObject.ownerId == authentication.principal.id")
    public Document getDocument(Long id) { ... }

    @Secured("ROLE_ADMIN")  // 旧注解
    public void legacy() { ... }

    @RolesAllowed("ADMIN")  // JSR-250
    public void standard() { ... }
}
```

### SpEL
- `hasRole('ADMIN')` → 要求 ROLE_ADMIN
- `hasAuthority('read')` → 具体权限
- `hasAnyRole('A', 'B')`
- `authentication.principal.id`
- `#paramName` 方法参数
- `returnObject` 返回值（@PostAuthorize）

---

## 7. OAuth 2.0 / OIDC

### Resource Server（保护 API）

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-oauth2-resource-server</artifactId>
</dependency>
```

```yaml
spring.security.oauth2.resourceserver.jwt:
  issuer-uri: https://accounts.google.com
  # 或
  jwk-set-uri: https://auth.example.com/.well-known/jwks.json
```

```java
@Bean
public SecurityFilterChain chain(HttpSecurity http) throws Exception {
    http
        .oauth2ResourceServer(o -> o.jwt(Customizer.withDefaults()));
    return http.build();
}

// Controller 访问 JWT
@GetMapping("/me")
public Map<String, Object> me(@AuthenticationPrincipal Jwt jwt) {
    return Map.of("sub", jwt.getSubject(), "email", jwt.getClaim("email"));
}
```

### OAuth2 Client（登录第三方）

```yaml
spring.security.oauth2.client.registration.google:
  client-id: ...
  client-secret: ...
  scope: openid, profile, email
```

```java
http.oauth2Login(Customizer.withDefaults());
```

### 授权服务器
`spring-authorization-server`，可自建 OAuth2/OIDC Provider。

---

## 8. CSRF 防护

### 默认启用
Spring Security 默认对 POST/PUT/DELETE 要求 CSRF Token。

### 无状态 API 可关闭
```java
http.csrf(AbstractHttpConfigurer::disable);
```

### SPA 场景
```java
http.csrf(csrf -> csrf
    .csrfTokenRepository(CookieCsrfTokenRepository.withHttpOnlyFalse()));
```
Token 放 Cookie，前端 JS 读取后放到请求头 `X-XSRF-TOKEN`。

---

## 9. CORS

```java
@Bean
public SecurityFilterChain chain(HttpSecurity http) throws Exception {
    http.cors(cors -> cors.configurationSource(corsConfig()));
    return http.build();
}

@Bean
public CorsConfigurationSource corsConfig() {
    CorsConfiguration cfg = new CorsConfiguration();
    cfg.setAllowedOrigins(List.of("https://app.example.com"));
    cfg.setAllowedMethods(List.of("GET", "POST", "PUT", "DELETE"));
    cfg.setAllowedHeaders(List.of("*"));
    cfg.setAllowCredentials(true);
    UrlBasedCorsConfigurationSource src = new UrlBasedCorsConfigurationSource();
    src.registerCorsConfiguration("/**", cfg);
    return src;
}
```

---

## 10. Session 管理

### 最大会话数

```java
http.sessionManagement(s -> s
    .maximumSessions(1)
    .maxSessionsPreventsLogin(false)  // 后登录者踢前者
);
```

### Session Fixation 防护

默认启用，登录后换 session ID。

### Session 存储
- 默认内存
- 分布式：Redis + `spring-session-data-redis`
```xml
<dependency>
    <groupId>org.springframework.session</groupId>
    <artifactId>spring-session-data-redis</artifactId>
</dependency>
```

---

## 11. 常见安全威胁防护

### XSS
- 输出转义（Thymeleaf/JSP 默认）
- `X-XSS-Protection` 头（浏览器已废弃）
- Content Security Policy（CSP）：
```java
http.headers(h -> h.contentSecurityPolicy(c ->
    c.policyDirectives("default-src 'self'")));
```

### SQL 注入
- **永远用参数化查询**（JPA/MyBatis 默认）
- 不要字符串拼接 SQL

### 点击劫持
```java
http.headers(h -> h.frameOptions(f -> f.sameOrigin()));
```

### HTTPS 强制
```java
http.requiresChannel(c -> c.anyRequest().requiresSecure());
```

### HSTS
```java
http.headers(h -> h.httpStrictTransportSecurity(hsts -> hsts
    .includeSubDomains(true).maxAgeInSeconds(31536000)));
```

---

## 12. 获取当前用户

### Controller 方式

```java
// 方式 1：注解
@GetMapping("/me")
public User me(@AuthenticationPrincipal UserDetails user) {
    return userService.findByUsername(user.getUsername());
}

// 方式 2：Principal
@GetMapping("/me2")
public String me(Principal principal) {
    return principal.getName();
}

// 方式 3：SecurityContextHolder
public User currentUser() {
    Authentication auth = SecurityContextHolder.getContext().getAuthentication();
    return (User) auth.getPrincipal();
}
```

### 线程传递（异步）
```java
@Async
public CompletableFuture<Data> fetch() {
    // 默认 SecurityContext 不传
}
```

解法：`DelegatingSecurityContextAsyncTaskExecutor`：
```java
@Bean
public TaskExecutor taskExecutor() {
    ThreadPoolTaskExecutor exec = new ThreadPoolTaskExecutor();
    exec.initialize();
    return new DelegatingSecurityContextAsyncTaskExecutor(exec);
}
```

---

## 13. 自定义异常处理

```java
http
    .exceptionHandling(e -> e
        .authenticationEntryPoint((req, resp, ex) -> {
            resp.setStatus(401);
            resp.setContentType("application/json");
            resp.getWriter().write("{\"error\":\"unauthorized\"}");
        })
        .accessDeniedHandler((req, resp, ex) -> {
            resp.setStatus(403);
            resp.setContentType("application/json");
            resp.getWriter().write("{\"error\":\"forbidden\"}");
        })
    );
```

---

## 14. Remember Me

```java
http.rememberMe(r -> r
    .tokenValiditySeconds(7 * 24 * 60 * 60)
    .key("my-secret")
    .userDetailsService(userDetailsService));
```

---

## 15. 常见配置反模式

1. **CSRF 无脑关闭**：即使是无状态 API，也应审视
2. **密码明文**：用 BCrypt
3. **JWT Secret 硬编码**：放配置 + Secret Manager
4. **权限判断在 Controller**：用 `@PreAuthorize`
5. **所有 API 都要认证**：静态资源、健康检查应放行
6. **过度宽松的 CORS**：`allowedOrigins("*")` + `allowCredentials(true)` 会被浏览器拒绝，且不安全

---

## 面试高频问题

**Q1：Spring Security 核心工作原理？**

基于 Servlet Filter 链：
1. 请求进入 `DelegatingFilterProxy` → `FilterChainProxy` → 一系列 SecurityFilter
2. 身份认证：`UsernamePasswordAuthenticationFilter` 等捕获登录请求，调 `AuthenticationManager` 认证
3. 认证结果存 `SecurityContextHolder`（ThreadLocal）
4. `AuthorizationFilter` 检查当前用户是否有权限访问目标资源
5. 未认证/无权限 → `ExceptionTranslationFilter` 转换为 401/403

**Q2：认证和授权区别？**

- **认证（Authentication）**：你是谁。通过用户名密码、Token、证书等证明身份
- **授权（Authorization）**：你能做什么。基于角色（RBAC）或权限（ABAC）决策

认证先于授权。

**Q3：JWT 和 Session 的区别？如何选？**

**Session**：
- 服务端存，stateful
- 每次请求带 JSESSIONID
- 多实例要共享 Session（Redis）
- 服务端可主动失效

**JWT**：
- 客户端存，stateless
- 每次请求带 Bearer Token
- 无需共享存储
- **失效难**（要黑名单）

**选择**：
- 单体/少量实例：Session 简单
- 微服务/跨域：JWT
- 需要及时吊销：Session 或 JWT + Redis 黑名单

**Q4：JWT 有什么坑？**

- **无法主动失效**：签发后到期前一直有效；需要黑名单机制
- **Payload 不加密**：仅签名防篡改，内容可解码
- **Token 体积大**：比 session id 大得多，每次请求都带
- **算法选择**：别用 `none`，别混用 HS/RS
- **Secret 泄露**：整个系统认证破防
- **无刷新机制**：需自己实现 Refresh Token

**Q5：如何实现 RBAC？**

数据模型：
```
用户 ↔ 用户角色 ↔ 角色
          角色 ↔ 角色权限 ↔ 权限
```

```java
@Entity
class User {
    @ManyToMany Set<Role> roles;
}
@Entity
class Role {
    @ManyToMany Set<Permission> permissions;
}
```

Spring Security：
```java
@PreAuthorize("hasAuthority('user:delete')")
public void deleteUser(Long id) { ... }
```

**Q6：密码如何安全存储？**

- **加盐 + 慢哈希**：BCrypt（内置加盐）、Argon2（更新，抗 GPU 破解）
- **别用 MD5/SHA1**：太快，易彩虹表
- **Peppering**：应用级 secret 加盐（盐存 DB，pepper 存配置）
- **算法可升级**：`DelegatingPasswordEncoder` 支持平滑升级

**Q7：CSRF 是什么？如何防？**

**CSRF（跨站请求伪造）**：恶意网站利用用户在目标网站的登录态发送请求。
**防护**：
- CSRF Token（Spring Security 默认）
- SameSite Cookie
- 验证 Referer
- 对 GET 幂等、不做敏感操作
- API 用 Bearer Token 天然免疫（不依赖 Cookie）

**Q8：XSS 是什么？如何防？**

**XSS（跨站脚本）**：注入恶意 JS 到目标网站。
**防护**：
- 输出转义（HTML 特殊字符）
- Content Security Policy（CSP）
- Cookie HttpOnly（JS 读不到）
- 输入校验（但不是唯一防线）

**Q9：微服务场景 Spring Security 怎么用？**

**方案 1：Gateway 统一认证**
- 网关校验 JWT，注入用户信息（Header）到下游
- 下游信任网关，直接读 Header

**方案 2：每服务独立校验 JWT**
- JWT 解析 + 签名验证
- 公钥分发（jwks.json 端点）
- Resource Server 模式

**方案 3：OAuth2 + Gateway**
- Gateway 作为 BFF（Backend for Frontend）
- 存 access token，下游只见用户 ID

**Q10：Spring Security 和 Shiro 区别？**

| 维度 | Spring Security | Shiro |
|------|-----------------|-------|
| 归属 | Spring 官方 | Apache |
| 复杂度 | 高、功能多 | 相对简单 |
| 集成 | Spring 深度集成 | 独立，任意框架 |
| OAuth2 | 官方支持 | 需要扩展 |
| 社区 | 活跃 | 一般 |

Spring 项目选 Spring Security；非 Spring 或追求简单选 Shiro。现在企业主流是 Spring Security。
