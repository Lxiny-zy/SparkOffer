# 接口设计与 RESTful API

## 一、RESTful API 设计规范

### 1.1 REST 核心概念

REST（Representational State Transfer）是一种架构风格，核心约束包括：

- **客户端-服务端分离**：前后端通过统一接口交互
- **无状态（Stateless）**：每个请求包含所有必要信息，服务端不保存客户端状态
- **可缓存（Cacheable）**：响应应标注是否可缓存
- **统一接口（Uniform Interface）**：通过资源标识、表述操作资源、自描述消息、超媒体驱动
- **分层系统（Layered System）**：客户端无需知道是否与终端服务器直接通信
- **按需代码（Code on Demand，可选）**：服务端可返回可执行代码

### 1.2 资源命名规范

```
# 好的命名 - 使用名词复数，小写，用连字符分隔
GET /api/v1/users
GET /api/v1/user-profiles
GET /api/v1/orders/{orderId}/order-items

# 坏的命名 - 避免以下写法
GET /api/v1/getUsers          # 不要用动词
GET /api/v1/user_profiles     # 不要用下划线
GET /api/v1/UserProfiles      # 不要用驼峰
GET /api/v1/user/profile/list # 不要嵌套过深
```

**命名原则：**

| 规则 | 正确示例 | 错误示例 |
|------|----------|----------|
| 使用名词复数 | `/users` | `/user`、`/getUser` |
| 层级关系用路径表示 | `/users/1/orders` | `/getUserOrders?userId=1` |
| 过滤用查询参数 | `/users?status=active` | `/active-users` |
| 小写字母 + 连字符 | `/user-profiles` | `/userProfiles` |
| 避免文件扩展名 | `/users/1` | `/users/1.json` |
| 层级不超过 3 层 | `/users/1/orders` | `/users/1/orders/2/items/3/details` |

### 1.3 HTTP 动词语义

| 方法 | 语义 | 幂等性 | 安全性 | 请求体 | 典型状态码 |
|------|------|--------|--------|--------|------------|
| GET | 获取资源 | 幂等 | 安全 | 无 | 200 |
| POST | 创建资源/执行操作 | 不幂等 | 不安全 | 有 | 201, 202 |
| PUT | 全量替换资源 | 幂等 | 不安全 | 有 | 200, 204 |
| PATCH | 部分更新资源 | 不幂等 | 不安全 | 有 | 200, 204 |
| DELETE | 删除资源 | 幂等 | 不安全 | 可选 | 200, 204 |
| HEAD | 获取头信息（不含体） | 幂等 | 安全 | 无 | 200 |
| OPTIONS | 获取支持的方法 | 幂等 | 安全 | 无 | 204 |

```
# 完整 CRUD 示例
GET    /api/v1/users              # 获取用户列表（支持分页、筛选）
GET    /api/v1/users/123          # 获取单个用户详情
POST   /api/v1/users              # 创建新用户
PUT    /api/v1/users/123          # 全量更新用户
PATCH  /api/v1/users/123          # 部分更新用户
DELETE /api/v1/users/123          # 删除用户

# 子资源操作
GET    /api/v1/users/123/orders             # 获取用户的订单列表
POST   /api/v1/users/123/orders             # 为用户创建订单
GET    /api/v1/users/123/orders/456         # 获取具体某个订单

# 非 CRUD 操作 - 使用动词（action 子资源）
POST   /api/v1/users/123/activate           # 激活用户
POST   /api/v1/orders/456/cancel            # 取消订单
POST   /api/v1/emails/batch-send            # 批量发送邮件
```

### 1.4 状态码完整参考

#### 2xx 成功

| 状态码 | 含义 | 使用场景 |
|--------|------|----------|
| 200 OK | 请求成功 | GET、PUT、PATCH、DELETE 成功 |
| 201 Created | 资源创建成功 | POST 创建资源成功，Location 头返回新资源 URI |
| 202 Accepted | 请求已接受，异步处理中 | 异步任务提交成功 |
| 204 No Content | 成功，无返回体 | DELETE 成功、PUT/PATCH 无需返回体 |

#### 3xx 重定向

| 状态码 | 含义 | 使用场景 |
|--------|------|----------|
| 301 Moved Permanently | 永久重定向 | 资源 URI 永久变更 |
| 302 Found | 临时重定向 | 临时跳转 |
| 304 Not Modified | 资源未修改 | 客户端缓存仍有效 |

#### 4xx 客户端错误

| 状态码 | 含义 | 使用场景 |
|--------|------|----------|
| 400 Bad Request | 请求参数错误 | 参数校验失败、格式错误 |
| 401 Unauthorized | 未认证 | 缺少或无效的认证凭证 |
| 403 Forbidden | 无权限 | 已认证但无操作权限 |
| 404 Not Found | 资源不存在 | 请求的资源 ID 不存在 |
| 405 Method Not Allowed | 方法不允许 | 资源不支持该 HTTP 方法 |
| 409 Conflict | 资源冲突 | 并发更新冲突、唯一约束违反 |
| 413 Payload Too Large | 请求体过大 | 上传文件超限 |
| 415 Unsupported Media Type | 不支持的媒体类型 | Content-Type 不匹配 |
| 422 Unprocessable Entity | 语义错误 | 参数格式正确但语义不合法 |
| 429 Too Many Requests | 请求过多 | 触发限流 |

#### 5xx 服务端错误

| 状态码 | 含义 | 使用场景 |
|--------|------|----------|
| 500 Internal Server Error | 服务器内部错误 | 未捕获异常 |
| 502 Bad Gateway | 网关错误 | 上游服务不可用 |
| 503 Service Unavailable | 服务不可用 | 服务维护或过载 |
| 504 Gateway Timeout | 网关超时 | 上游服务响应超时 |

### 1.5 统一响应格式

```json
// 成功响应
{
  "code": 200,
  "message": "success",
  "data": {
    "id": 123,
    "name": "张三",
    "email": "zhangsan@example.com"
  },
  "timestamp": 1700000000000
}

// 分页响应
{
  "code": 200,
  "message": "success",
  "data": {
    "list": [...],
    "pagination": {
      "page": 1,
      "pageSize": 20,
      "total": 156,
      "totalPages": 8
    }
  }
}

// 错误响应
{
  "code": 400,
  "message": "参数校验失败",
  "errors": [
    { "field": "email", "message": "邮箱格式不正确" },
    { "field": "age", "message": "年龄必须大于 0" }
  ],
  "timestamp": 1700000000000,
  "path": "/api/v1/users",
  "traceId": "abc-123-def-456"
}
```

### 1.6 版本控制

| 方式 | 示例 | 优点 | 缺点 |
|------|------|------|------|
| URL 路径 | `/api/v1/users` | 直观、易缓存 | URL 冗余 |
| 请求头 | `Accept: application/vnd.myapp.v1+json` | URL 干净 | 不直观、调试困难 |
| 查询参数 | `/api/users?version=1` | 灵活 | 不符合 REST 风格 |
| 自定义头 | `X-API-Version: 1` | 清晰 | 非标准 |

**推荐：URL 路径方式**，最常用且对客户端最友好。

**版本迁移策略：**
1. 新版本发布后，老版本至少维护 6-12 个月
2. 老版本返回 `Sunset` 响应头，提示废弃时间
3. 通过监控统计老版本使用量，逐步下线

---

## 二、API 幂等性设计

### 2.1 天然幂等的方法

- **GET**：只读操作，天然幂等
- **PUT**：全量替换，多次执行结果一致
- **DELETE**：删除资源，第一次成功后再次调用返回 404 或 204，最终状态一致

### 2.2 POST 幂等方案

POST 本质上不幂等（每次创建新资源），需要额外机制保证：

#### 方案一：Token 机制（前置 Token）

```
1. 客户端先请求 Token：GET /api/v1/tokens -> { "token": "abc123" }
2. 提交业务请求时携带 Token：POST /api/v1/orders (Header: Idempotency-Key: abc123)
3. 服务端用 Redis SETNX 验证 Token：
   - Token 存在 -> 执行业务 -> 删除 Token -> 返回结果
   - Token 不存在 -> 返回重复提交提示
```

```java
@PostMapping("/orders")
public Result createOrder(@RequestHeader("Idempotency-Key") String token,
                          @RequestBody OrderRequest req) {
    // 尝试获取并删除 Token（原子操作）
    Boolean acquired = redisTemplate.delete("idempotent:" + token);
    if (Boolean.FALSE.equals(acquired)) {
        return Result.fail(409, "请勿重复提交");
    }
    // 执行业务逻辑
    Order order = orderService.create(req);
    return Result.success(order);
}
```

#### 方案二：唯一业务 ID + 去重表

```java
@Transactional
public Order createOrder(String bizId, OrderRequest req) {
    // 1. 先查去重表
    if (idempotentRecordMapper.existsByBizId(bizId)) {
        return orderMapper.selectByBizId(bizId); // 返回已有结果
    }
    // 2. 插入去重记录（唯一索引保证并发安全）
    try {
        idempotentRecordMapper.insert(new IdempotentRecord(bizId));
    } catch (DuplicateKeyException e) {
        return orderMapper.selectByBizId(bizId);
    }
    // 3. 执行业务
    Order order = doCreateOrder(req);
    return order;
}
```

#### 方案三：乐观锁（更新场景）

```sql
-- 更新时带版本号
UPDATE account SET balance = balance - 100, version = version + 1
WHERE id = 1 AND version = 5;

-- 影响行数为 0 表示版本号不匹配，说明已被其他请求处理
```

#### 方案四：状态机（状态流转场景）

```sql
-- 只有 PENDING 状态才能更新为 PAID
UPDATE orders SET status = 'PAID' WHERE id = 1 AND status = 'PENDING';
-- 如果已经是 PAID，影响行数为 0，自然幂等
```

### 2.3 各方案对比

| 方案 | 适用场景 | 复杂度 | 性能 |
|------|----------|--------|------|
| Token 机制 | 表单提交、创建操作 | 中 | 高（依赖 Redis） |
| 唯一业务 ID | 有天然唯一标识的业务 | 低 | 中 |
| 乐观锁 | 更新操作 | 低 | 高 |
| 状态机 | 状态流转场景 | 低 | 高 |
| Redis SETNX | 通用去重 | 低 | 高 |

---

## 三、认证鉴权

### 3.1 Session + Cookie

```
                    +-----------+
                    |  Browser  |
                    +-----+-----+
                          |
              1. POST /login (username, password)
                          |
                    +-----v-----+
                    |  Server   |
                    | (Session  |
                    |   Store)  |
                    +-----+-----+
                          |
              2. Set-Cookie: JSESSIONID=abc123
                          |
                    +-----v-----+
                    |  Browser  |
                    +-----+-----+
                          |
              3. GET /api/users (Cookie: JSESSIONID=abc123)
                          |
                    +-----v-----+
                    |  Server   | -> 从 Session Store 查找 abc123
                    +-----------+
```

**优点：** 服务端可主动销毁会话，安全性较好
**缺点：**
- 分布式环境需要 Session 共享（Redis / 数据库存储）
- Cookie 受同源策略限制，不适合跨域
- 占用服务端内存

### 3.2 JWT（JSON Web Token）

#### JWT 结构

```
Header.Payload.Signature

# Header（Base64 编码）
{
  "alg": "HS256",  // 签名算法
  "typ": "JWT"
}

# Payload（Base64 编码）
{
  "sub": "1234567890",       // 主题（用户 ID）
  "name": "张三",
  "iat": 1700000000,         // 签发时间
  "exp": 1700003600,         // 过期时间
  "roles": ["admin", "user"] // 自定义字段
}

# Signature
HMACSHA256(
  base64UrlEncode(header) + "." + base64UrlEncode(payload),
  secret
)
```

#### JWT 双 Token 刷新机制

```
1. 登录 -> 返回 AccessToken（短期，如 15 分钟）+ RefreshToken（长期，如 7 天）
2. AccessToken 过期 -> 用 RefreshToken 换新的 AccessToken
3. RefreshToken 过期 -> 重新登录

Client                         Server
  |                               |
  |-- POST /login --------------->|
  |<-- accessToken + refreshToken-|
  |                               |
  |-- GET /api (accessToken) ---->|  (accessToken 有效)
  |<-- 200 data ------------------|
  |                               |
  |-- GET /api (accessToken) ---->|  (accessToken 过期)
  |<-- 401 Token Expired ---------|
  |                               |
  |-- POST /refresh (refreshToken)|  (用 refreshToken 换新 token)
  |<-- new accessToken -----------|
  |                               |
  |-- GET /api (new accessToken)->|
  |<-- 200 data ------------------|
```

```java
// RefreshToken 实现
@PostMapping("/auth/refresh")
public Result refresh(@RequestBody RefreshRequest req) {
    // 1. 验证 refreshToken
    Claims claims = jwtUtil.parse(req.getRefreshToken());

    // 2. 检查 refreshToken 是否在黑名单（已注销）
    if (redisTemplate.hasKey("blacklist:" + req.getRefreshToken())) {
        return Result.fail(401, "Token 已失效");
    }

    // 3. 签发新的 accessToken
    String newAccessToken = jwtUtil.createAccessToken(claims.getSubject());

    // 4. 可选：旋转 refreshToken（更安全）
    String newRefreshToken = jwtUtil.createRefreshToken(claims.getSubject());
    // 旧 refreshToken 加入黑名单
    redisTemplate.opsForValue().set(
        "blacklist:" + req.getRefreshToken(), "1",
        7, TimeUnit.DAYS
    );

    return Result.success(new TokenResponse(newAccessToken, newRefreshToken));
}
```

#### JWT 主动失效方案

JWT 本身无状态，要实现主动失效（用户注销）可以：

1. **黑名单机制**：注销时将 Token 加入 Redis 黑名单，每次验证时检查
2. **版本号机制**：用户表增加 `tokenVersion` 字段，注销时 +1，验证时比对
3. **短过期时间 + RefreshToken**：AccessToken 设短过期（15 分钟），降低风险窗口

### 3.3 Session vs Token 对比

| 维度 | Session | JWT |
|------|---------|-----|
| 状态 | 有状态（服务端存储） | 无状态（Token 自包含） |
| 扩展性 | 需要 Session 共享 | 天然支持分布式 |
| 安全性 | 服务端可主动销毁 | 需要黑名单机制主动失效 |
| 性能 | 每次请求查 Session Store | 直接解析 Token，无需查询 |
| 存储位置 | Cookie | Header（Authorization: Bearer xxx） |
| 跨域 | 受 Cookie 同源限制 | 不受限制 |
| 信息量 | 仅 SessionID | 可携带用户信息 |
| 适用场景 | 传统 Web 应用 | 前后端分离、移动端、微服务 |

### 3.4 OAuth 2.0 四种授权模式

#### 授权码模式（Authorization Code）--- 最安全，推荐

```
+--------+                               +---------------+
|        |-- (A) Authorization Request -->|   Resource    |
|        |                                |     Owner     |
|        |<-(B) Authorization Grant ------|   (User)      |
|        |                                +---------------+
|        |
| Client |                                +---------------+
|        |-- (C) Auth Code + Redirect --> |   Auth        |
|        |                                |   Server      |
|        |<-(D) Access Token ------------|               |
|        |                                +---------------+
|        |
|        |                                +---------------+
|        |-- (E) Access Token ---------->|   Resource    |
|        |                                |   Server      |
|        |<-(F) Protected Resource ------|               |
+--------+                                +---------------+

具体流程：
1. 用户访问客户端 -> 客户端重定向到授权服务器
2. 用户登录并授权 -> 授权服务器返回授权码（code）给客户端回调地址
3. 客户端用 code + client_secret 向授权服务器换取 access_token
4. 客户端用 access_token 访问资源服务器
```

#### 简化模式（Implicit）--- 已不推荐

- 省去授权码步骤，直接返回 access_token
- 适用于纯前端应用（无后端）
- 安全性低，Token 暴露在 URL 中
- 已被 OAuth 2.1 废弃，推荐使用 PKCE 扩展的授权码模式

#### 密码模式（Resource Owner Password）

- 用户直接把账号密码给客户端，客户端用密码换 Token
- 仅适用于高度信任的第一方应用
- 如公司内部系统使用自家认证服务

#### 客户端凭证模式（Client Credentials）

- 客户端以自己的身份（非用户身份）获取 Token
- 适用于服务间通信（M2M，Machine to Machine）
- 如微服务之间的调用

---

## 四、接口安全

### 4.1 HTTPS

- **TLS 握手流程**：TCP 三次握手 -> TLS 握手（证书验证、密钥协商）-> 加密通信
- 确保传输过程中数据不被窃听和篡改
- 生产环境必须全站 HTTPS

### 4.2 签名验证

```
# 签名流程
1. 将所有非空参数按 key 字典序排列
2. 拼接成 key1=value1&key2=value2&...&keyN=valueN
3. 在末尾拼接 secret（密钥）
4. 对拼接字符串做 MD5/HMAC-SHA256 计算得到 sign
5. 将 sign 放入请求参数中

# 示例
参数: { "name": "张三", "amount": 100, "timestamp": 1700000000 }
排序拼接: amount=100&name=张三&timestamp=1700000000
加密钥: amount=100&name=张三&timestamp=1700000000&key=mySecret
MD5: sign = md5("amount=100&name=张三&timestamp=1700000000&key=mySecret")
```

```java
public String generateSign(Map<String, String> params, String secret) {
    String sortedParams = params.entrySet().stream()
        .filter(e -> e.getValue() != null && !e.getValue().isEmpty())
        .sorted(Map.Entry.comparingByKey())
        .map(e -> e.getKey() + "=" + e.getValue())
        .collect(Collectors.joining("&"));

    String signStr = sortedParams + "&key=" + secret;
    return DigestUtils.md5Hex(signStr).toUpperCase();
}
```

### 4.3 防重放攻击

```
# 方案：timestamp + nonce + sign
1. 请求携带 timestamp（时间戳）和 nonce（随机字符串）
2. 服务端验证：
   - timestamp 与当前时间差不超过 5 分钟（防止旧请求重放）
   - nonce 在 Redis 中不存在（防止短时间内重放），验证后存入 Redis 并设置 5 分钟过期
   - sign 验证通过
```

### 4.4 限流

```java
// Spring Boot + Guava RateLimiter（单机限流）
@RestController
public class ApiController {
    private final RateLimiter rateLimiter = RateLimiter.create(100); // 每秒 100 个请求

    @GetMapping("/api/data")
    public Result getData() {
        if (!rateLimiter.tryAcquire(500, TimeUnit.MILLISECONDS)) {
            return Result.fail(429, "请求过于频繁，请稍后重试");
        }
        return Result.success(dataService.getData());
    }
}

// 分布式限流：Redis + Lua 脚本实现滑动窗口
```

### 4.5 其他安全措施

| 措施 | 说明 |
|------|------|
| 参数校验 | 使用 `@Valid` / `@Validated` 注解做入参校验 |
| SQL 注入防护 | 参数化查询（MyBatis `#{}` 而非 `${}`） |
| XSS 防护 | 输出转义，Content-Type 设置正确 |
| CSRF 防护 | Token 验证、SameSite Cookie、检查 Referer |
| CORS 配置 | 明确允许的域名，不要使用 `*` |
| 敏感数据脱敏 | 手机号、身份证号等返回时脱敏处理 |
| 日志脱敏 | 密码、Token 等不得记录到日志中 |

---

## 五、API 文档与规范

### 5.1 OpenAPI / Swagger

```java
// SpringDoc (OpenAPI 3.0) 注解示例
@Operation(summary = "创建用户", description = "注册新用户账号")
@ApiResponses({
    @ApiResponse(responseCode = "201", description = "创建成功",
        content = @Content(schema = @Schema(implementation = UserVO.class))),
    @ApiResponse(responseCode = "400", description = "参数校验失败"),
    @ApiResponse(responseCode = "409", description = "用户名已存在")
})
@PostMapping("/users")
public ResponseEntity<UserVO> createUser(
    @RequestBody @Valid CreateUserRequest request) {
    // ...
}

// 模型注解
@Schema(description = "创建用户请求")
public class CreateUserRequest {
    @Schema(description = "用户名", example = "zhangsan", requiredMode = REQUIRED)
    @NotBlank(message = "用户名不能为空")
    @Size(min = 2, max = 32)
    private String username;

    @Schema(description = "邮箱", example = "zhangsan@example.com")
    @Email(message = "邮箱格式不正确")
    private String email;
}
```

### 5.2 接口版本管理策略

```java
// 方案一：URL 路径版本（推荐）
@RestController
@RequestMapping("/api/v1/users")
public class UserControllerV1 { ... }

@RestController
@RequestMapping("/api/v2/users")
public class UserControllerV2 { ... }

// 方案二：请求头版本
@RestController
@RequestMapping("/api/users")
public class UserController {
    @GetMapping(headers = "X-API-Version=1")
    public Result<UserV1VO> getUserV1(@PathVariable Long id) { ... }

    @GetMapping(headers = "X-API-Version=2")
    public Result<UserV2VO> getUserV2(@PathVariable Long id) { ... }
}
```

---

## 六、GraphQL vs REST vs gRPC 对比

| 维度 | REST | GraphQL | gRPC |
|------|------|---------|------|
| 通信协议 | HTTP/1.1 | HTTP/1.1 | HTTP/2 |
| 数据格式 | JSON/XML | JSON | Protobuf（二进制） |
| 定义方式 | 路径 + 方法 | Schema + Query | .proto 文件 |
| 数据获取 | 固定结构 | 客户端按需查询 | 固定结构 |
| 过度获取 | 常见 | 可避免 | 常见 |
| 欠获取 | 需多次请求 | 单次请求获取关联数据 | 需多次请求 |
| 实时通信 | WebSocket/SSE | Subscription | 双向流 |
| 性能 | 中 | 中 | 高（二进制序列化） |
| 学习成本 | 低 | 中 | 高 |
| 适用场景 | 通用 Web API | 复杂查询、聚合 | 微服务间高性能通信 |
| 缓存 | HTTP 缓存友好 | 缓存复杂 | 需自行实现 |
| 文件上传 | 原生支持 | 需额外处理 | 流式支持 |
| 浏览器支持 | 原生 | 原生 | 需 gRPC-Web 代理 |

```graphql
# GraphQL 查询示例 - 一次请求获取用户及其订单
query {
  user(id: 123) {
    name
    email
    orders(first: 5) {
      id
      totalAmount
      items {
        productName
        quantity
      }
    }
  }
}
```

```protobuf
// gRPC .proto 文件示例
syntax = "proto3";

service UserService {
  rpc GetUser (GetUserRequest) returns (UserResponse);
  rpc ListUsers (ListUsersRequest) returns (stream UserResponse); // 服务端流
}

message GetUserRequest {
  int64 id = 1;
}

message UserResponse {
  int64 id = 1;
  string name = 2;
  string email = 3;
}
```

**选型建议：**
- **对外 API**（面向第三方/前端）：REST 或 GraphQL
- **微服务内部通信**：gRPC（高性能）或 REST（简单场景）
- **复杂数据聚合**：GraphQL（避免多次请求）
- **实时推送**：gRPC（双向流）或 WebSocket

---

## 七、接口性能优化

### 7.1 批量接口

```java
// 批量查询（替代循环单个查询）
@PostMapping("/users/batch")
public Result<List<UserVO>> batchGetUsers(@RequestBody List<Long> ids) {
    // 一次数据库查询，避免 N+1 问题
    List<User> users = userMapper.selectBatchIds(ids);
    return Result.success(convert(users));
}

// 批量操作
@PostMapping("/orders/batch-create")
public Result<BatchResult> batchCreateOrders(@RequestBody List<OrderRequest> orders) {
    // 批量插入
    orderService.batchInsert(orders);
    return Result.success(new BatchResult(orders.size(), 0));
}
```

### 7.2 异步化

```java
// 异步接口 - 提交后立即返回任务 ID
@PostMapping("/reports/generate")
public Result<TaskResult> generateReport(@RequestBody ReportRequest req) {
    String taskId = taskService.submitAsync(req); // 提交到线程池/MQ
    return Result.success(new TaskResult(taskId, "PROCESSING"));
}

// 轮询查结果
@GetMapping("/tasks/{taskId}")
public Result<TaskResult> getTaskStatus(@PathVariable String taskId) {
    return Result.success(taskService.getStatus(taskId));
}
```

### 7.3 缓存策略

```java
// HTTP 缓存头
@GetMapping("/products/{id}")
public ResponseEntity<Product> getProduct(@PathVariable Long id) {
    Product product = productService.getById(id);
    return ResponseEntity.ok()
        .cacheControl(CacheControl.maxAge(30, TimeUnit.MINUTES))
        .eTag(String.valueOf(product.getVersion()))
        .body(product);
}

// 应用层缓存
@Cacheable(value = "products", key = "#id", unless = "#result == null")
public Product getById(Long id) {
    return productMapper.selectById(id);
}
```

### 7.4 其他优化手段

| 手段 | 说明 |
|------|------|
| 字段裁剪 | `?fields=id,name,email` 只返回需要的字段 |
| 压缩 | 开启 Gzip / Brotli 压缩 |
| 连接池 | 数据库连接池（HikariCP）、HTTP 连接池 |
| CDN | 静态资源走 CDN |
| 分页 | 大列表必须分页，避免一次返回全部数据 |
| 游标分页 | 数据量极大时用 `cursor` 替代 `page`，避免深度分页 |

---

## 八、面试高频题

### Q1: RESTful API 的设计原则有哪些？

**答：** 核心原则包括：(1) 以资源为中心，URL 用名词复数表示资源；(2) 用 HTTP 方法（GET/POST/PUT/PATCH/DELETE）表示操作；(3) 无状态，每个请求自包含；(4) 使用正确的 HTTP 状态码；(5) 统一的响应格式；(6) 版本控制。

### Q2: 什么是幂等性？如何保证 POST 接口的幂等？

**答：** 幂等性指同一个请求执行一次和执行多次的效果完全一致。GET/PUT/DELETE 天然幂等，POST 需要额外机制保证。常用方案包括：(1) 前置 Token 机制（先获取 Token，提交时验证并删除）；(2) 唯一业务 ID + 去重表（数据库唯一索引）；(3) Redis SETNX 去重。

### Q3: JWT 和 Session 的区别？各自优缺点？

**答：** Session 是有状态的，信息存在服务端，适合传统 Web 应用，优点是可主动销毁，缺点是分布式环境需要 Session 共享。JWT 是无状态的，信息编码在 Token 中，适合前后端分离和微服务，优点是天然支持分布式、不需要存储，缺点是无法主动失效（需黑名单机制）、Token 体积较大。

### Q4: OAuth 2.0 的授权码模式流程？为什么最安全？

**答：** 流程：用户被重定向到授权服务器 -> 用户登录并授权 -> 授权服务器返回授权码给回调地址 -> 客户端用授权码 + client_secret 换取 access_token -> 用 access_token 访问资源。最安全是因为 access_token 不经过浏览器（在服务端换取），授权码一次性使用，且需要 client_secret 验证客户端身份。

### Q5: 如何设计一个安全的开放 API？

**答：** (1) 全站 HTTPS；(2) 身份认证（API Key / OAuth 2.0）；(3) 请求签名（参数排序 + 密钥 + HMAC）；(4) 防重放（timestamp + nonce）；(5) 限流（令牌桶/滑动窗口）；(6) 输入校验（防注入）；(7) 敏感数据脱敏；(8) 日志审计。

### Q6: GraphQL 和 REST 的区别？什么场景用 GraphQL？

**答：** REST 按资源设计多个端点，返回固定结构，可能导致过度获取（返回不需要的字段）或欠获取（需多次请求）。GraphQL 单端点，客户端声明需要什么数据，服务端精确返回。适合以下场景：前端页面需要聚合多个资源的数据、移动端需要减少请求次数和数据量、多端共享后端但数据需求不同。

### Q7: 如何解决接口的 N+1 问题？

**答：** N+1 问题指查询列表后，对每条记录再发一次查询。解决方案：(1) 批量查询替代循环查询（`WHERE id IN (...)`)；(2) JOIN 查询一次性获取关联数据；(3) 数据冗余/宽表设计；(4) GraphQL 的 DataLoader 批量加载机制。

### Q8: 接口的限流策略有哪些？怎么选？

**答：** (1) 固定窗口计数器 -- 简单但有边界问题；(2) 滑动窗口计数器 -- 解决边界问题；(3) 漏桶算法 -- 恒速处理，不允许突发；(4) 令牌桶算法 -- 允许一定突发，最常用。单机可用 Guava RateLimiter（令牌桶），分布式可用 Redis + Lua（滑动窗口）或 Sentinel（阿里限流组件）。

### Q9: API 版本如何管理？老版本如何下线？

**答：** 常用 URL 路径方式（`/api/v1/xxx`），优点是直观、可独立缓存。下线流程：(1) 发布新版本时，老版本标记为 Deprecated（响应头 `Sunset: 日期`）；(2) 通过监控统计老版本流量；(3) 通知调用方迁移，给出至少 6 个月过渡期；(4) 流量为零后关闭老版本。

### Q10: PATCH 和 PUT 的区别？PATCH 如何保证幂等？

**答：** PUT 是全量替换（客户端发送完整资源表示），天然幂等。PATCH 是部分更新（只发送需要修改的字段），不一定幂等（如 `{ "op": "increment", "path": "/count", "value": 1 }` 就不幂等）。要保证 PATCH 幂等，可以使用绝对值赋值（`{ "count": 5 }` 而非增量操作），或结合乐观锁版本号。

### Q11: 如何设计一个高可用的接口网关？

**答：** (1) 多实例部署 + 负载均衡（Nginx / LVS）；(2) 统一认证鉴权，减少后端重复逻辑；(3) 限流熔断（保护后端服务）；(4) 请求路由与负载均衡（按路径/Header 路由到不同服务）；(5) 协议转换（外部 HTTP -> 内部 gRPC）；(6) 缓存热点数据；(7) 灰度发布能力；(8) 日志与链路追踪（traceId 贯穿全链路）。常用方案：Spring Cloud Gateway、Kong、APISIX。
