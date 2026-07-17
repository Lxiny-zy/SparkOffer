# MyBatis 与数据访问

## 1. MyBatis 架构与核心组件

### 1.1 整体架构

```
┌─────────────────────────────────────────────────┐
│                  接口层                           │
│    SqlSession（门面接口，提供 CRUD API）           │
├─────────────────────────────────────────────────┤
│                  核心处理层                       │
│  ┌─────────────┐  ┌─────────────┐              │
│  │ Configuration│  │MappedStatement│             │
│  │ (全局配置)    │  │(SQL 映射语句) │             │
│  └─────────────┘  └─────────────┘              │
│  ┌──────────────────────────────┐              │
│  │         Executor（执行器）     │              │
│  │  ├─ SimpleExecutor（简单）     │              │
│  │  ├─ ReuseExecutor（复用）      │              │
│  │  └─ BatchExecutor（批量）      │              │
│  │  └─ CachingExecutor（缓存装饰）│              │
│  └──────────────────────────────┘              │
│  ┌──────────────────────────────┐              │
│  │    StatementHandler（语句处理器）│             │
│  │  ├─ SimpleStatementHandler    │              │
│  │  ├─ PreparedStatementHandler  │              │
│  │  └─ CallableStatementHandler  │              │
│  └──────────────────────────────┘              │
│  ┌───────────────┐  ┌────────────┐             │
│  │ParameterHandler│  │ResultSetHandler│          │
│  │ (参数处理器)    │  │(结果集处理器)  │          │
│  └───────────────┘  └────────────┘             │
│  ┌───────────────┐                              │
│  │  TypeHandler   │                              │
│  │ (类型处理器)    │                              │
│  └───────────────┘                              │
├─────────────────────────────────────────────────┤
│                  基础支撑层                       │
│  日志、反射、数据源管理、事务管理、缓存             │
└─────────────────────────────────────────────────┘
```

### 1.2 核心组件说明

| 组件 | 作用 | 关键类 |
|------|------|--------|
| SqlSessionFactory | 创建 SqlSession 的工厂（重量级，全局唯一） | DefaultSqlSessionFactory |
| SqlSession | 与数据库交互的会话（轻量级，非线程安全） | DefaultSqlSession |
| Executor | SQL 执行器，负责 SQL 的生成和查询缓存维护 | SimpleExecutor、BatchExecutor |
| StatementHandler | 封装 JDBC Statement 操作 | PreparedStatementHandler |
| ParameterHandler | 设置 SQL 参数（Java 类型 -> JDBC 类型） | DefaultParameterHandler |
| ResultSetHandler | 处理结果集映射（JDBC ResultSet -> Java 对象） | DefaultResultSetHandler |
| TypeHandler | Java 类型与 JDBC 类型的转换器 | IntegerTypeHandler、StringTypeHandler 等 |
| MappedStatement | 封装一条 SQL 语句的完整信息 | MappedStatement |

### 1.3 SQL 执行完整流程

```
1. 调用 Mapper 接口方法
      │
      ▼
2. MapperProxy（JDK 动态代理）拦截方法调用
      │
      ▼
3. MapperMethod 解析方法签名，确定 SQL 类型（SELECT/INSERT/UPDATE/DELETE）
      │
      ▼
4. SqlSession 调用对应的方法（selectList/insert/update/delete）
      │
      ▼
5. Executor 执行：
   a. 先查询一级缓存 → 命中则直接返回
   b. 未命中 → 创建 StatementHandler
      │
      ▼
6. StatementHandler：
   a. 创建 JDBC Statement（PreparedStatement）
   b. ParameterHandler 设置参数（#{} → ?）
   c. 执行 SQL
      │
      ▼
7. ResultSetHandler 处理结果集：
   a. 根据 ResultMap 或自动映射
   b. TypeHandler 转换列值的 Java 类型
   c. 返回映射后的 Java 对象列表
      │
      ▼
8. 结果写入一级缓存，返回给调用方
```

---

## 2. #{} vs ${} 原理及 SQL 注入防护

### 2.1 核心区别

| 特性 | `#{}` | `${}` |
|------|-------|-------|
| 底层实现 | PreparedStatement 的 `?` 占位符 | 字符串直接拼接 |
| 编译时机 | SQL 预编译后再设置参数 | 拼接后再编译 SQL |
| SQL 注入 | 安全（参数会被转义） | 不安全（直接拼入 SQL） |
| 类型处理 | 自动通过 TypeHandler 转换 | 无类型转换 |
| 适用场景 | 参数值（WHERE、INSERT VALUES） | 表名、列名、ORDER BY |

### 2.2 原理分析

```java
// #{} 的处理过程（安全）
// SQL: SELECT * FROM user WHERE id = #{id}
// 1. 预编译阶段：SELECT * FROM user WHERE id = ?
// 2. 参数设置：preparedStatement.setLong(1, 123)
// 即使传入 "1 OR 1=1"，也只是作为一个字符串参数，不会改变 SQL 结构

// ${} 的处理过程（不安全）
// SQL: SELECT * FROM user WHERE id = ${id}
// 1. 直接拼接：SELECT * FROM user WHERE id = 1 OR 1=1
// 恶意输入会改变 SQL 语义！
```

### 2.3 ${} 的合理使用场景

```xml
<!-- 动态表名（不能用 #{} 因为表名不能加引号） -->
<select id="selectByTable" resultType="map">
    SELECT * FROM ${tableName} WHERE id = #{id}
</select>

<!-- 动态排序字段 -->
<select id="selectWithOrder" resultType="User">
    SELECT * FROM user
    ORDER BY ${orderColumn} ${orderDirection}
</select>

<!-- 注意：使用 ${} 时必须在代码层做白名单校验！ -->
```

```java
// 白名单校验示例
private static final Set<String> ALLOWED_COLUMNS =
    Set.of("id", "name", "create_time", "update_time");

public List<User> selectWithOrder(String column, String direction) {
    if (!ALLOWED_COLUMNS.contains(column)) {
        throw new IllegalArgumentException("非法排序字段: " + column);
    }
    if (!"ASC".equalsIgnoreCase(direction) && !"DESC".equalsIgnoreCase(direction)) {
        throw new IllegalArgumentException("非法排序方向: " + direction);
    }
    return userMapper.selectWithOrder(column, direction);
}
```

---

## 3. 一级缓存与二级缓存

### 3.1 一级缓存（Local Cache）

- **级别**：SqlSession 级别（默认开启，无法关闭）
- **存储**：PerpetualCache（HashMap）
- **作用**：同一个 SqlSession 中，相同的查询只执行一次 SQL

```java
// 一级缓存示例
SqlSession session = sqlSessionFactory.openSession();
UserMapper mapper = session.getMapper(UserMapper.class);

User user1 = mapper.selectById(1L);  // 执行 SQL
User user2 = mapper.selectById(1L);  // 命中一级缓存，不执行 SQL
System.out.println(user1 == user2);  // true（同一个对象）
```

**一级缓存失效场景**：
1. 不同的 SqlSession
2. 同一个 SqlSession，但查询条件不同
3. 同一个 SqlSession，两次查询之间执行了 INSERT/UPDATE/DELETE
4. 同一个 SqlSession，手动调用 `sqlSession.clearCache()`
5. 同一个 SqlSession，查询不同的 Mapper（不同的 namespace）

**Spring 整合下的一级缓存**：
- 未开启事务：每次查询创建新的 SqlSession，一级缓存几乎无效
- 开启事务（@Transactional）：同一事务内共享 SqlSession，一级缓存有效

### 3.2 二级缓存（Global Cache）

- **级别**：Mapper（namespace）级别
- **跨 SqlSession**：不同 SqlSession 可以共享
- **默认关闭**，需要手动开启
- **序列化要求**：缓存对象必须实现 Serializable

```xml
<!-- mybatis-config.xml 全局开关 -->
<settings>
    <setting name="cacheEnabled" value="true"/>
</settings>

<!-- Mapper.xml 中开启 -->
<mapper namespace="com.example.mapper.UserMapper">
    <cache
        eviction="LRU"          <!-- 淘汰策略：LRU/FIFO/SOFT/WEAK -->
        flushInterval="60000"   <!-- 刷新间隔（ms） -->
        size="512"              <!-- 最大缓存对象数 -->
        readOnly="false"        <!-- 只读（true 性能高但不安全） -->
    />

    <select id="selectById" resultType="User" useCache="true">
        SELECT * FROM user WHERE id = #{id}
    </select>

    <!-- 某些查询不使用二级缓存 -->
    <select id="selectCount" resultType="int" useCache="false">
        SELECT COUNT(*) FROM user
    </select>
</mapper>
```

**二级缓存的注意事项**：
1. 二级缓存在 SqlSession 提交或关闭后才会写入（commit/close）
2. 执行 INSERT/UPDATE/DELETE 会清空该 namespace 的所有二级缓存
3. 多表联查时，如果关联表有更新，缓存不会自动失效（脏数据风险）
4. 分布式环境下，本地二级缓存不共享，建议使用 Redis 等分布式缓存替代

### 3.3 缓存查询顺序

```
查询请求
   │
   ▼
二级缓存（如果开启）── 命中 ──→ 返回结果
   │ 未命中
   ▼
一级缓存 ── 命中 ──→ 返回结果
   │ 未命中
   ▼
数据库查询 → 结果写入一级缓存
   │
   ▼
SqlSession 关闭时 → 结果写入二级缓存
```

---

## 4. 动态 SQL

### 4.1 常用动态 SQL 标签

```xml
<!-- if：条件判断 -->
<select id="selectByCondition" resultType="User">
    SELECT * FROM user
    <where>
        <if test="name != null and name != ''">
            AND name LIKE CONCAT('%', #{name}, '%')
        </if>
        <if test="status != null">
            AND status = #{status}
        </if>
        <if test="startTime != null">
            AND create_time >= #{startTime}
        </if>
    </where>
</select>

<!-- choose-when-otherwise：类似 switch-case -->
<select id="selectByType" resultType="User">
    SELECT * FROM user
    <where>
        <choose>
            <when test="type == 'name'">
                AND name = #{keyword}
            </when>
            <when test="type == 'email'">
                AND email = #{keyword}
            </when>
            <otherwise>
                AND phone = #{keyword}
            </otherwise>
        </choose>
    </where>
</select>

<!-- foreach：遍历集合 -->
<select id="selectByIds" resultType="User">
    SELECT * FROM user
    WHERE id IN
    <foreach collection="ids" item="id" open="(" separator="," close=")">
        #{id}
    </foreach>
</select>

<!-- 批量插入 -->
<insert id="batchInsert">
    INSERT INTO user (name, email) VALUES
    <foreach collection="users" item="user" separator=",">
        (#{user.name}, #{user.email})
    </foreach>
</insert>

<!-- set：动态更新 -->
<update id="updateSelective">
    UPDATE user
    <set>
        <if test="name != null">name = #{name},</if>
        <if test="email != null">email = #{email},</if>
        <if test="status != null">status = #{status},</if>
    </set>
    WHERE id = #{id}
</update>

<!-- trim：灵活的前缀/后缀处理 -->
<select id="selectByTrim" resultType="User">
    SELECT * FROM user
    <trim prefix="WHERE" prefixOverrides="AND | OR">
        <if test="name != null">AND name = #{name}</if>
        <if test="status != null">AND status = #{status}</if>
    </trim>
</select>

<!-- sql 片段复用 -->
<sql id="baseColumns">
    id, name, email, status, create_time, update_time
</sql>

<select id="selectAll" resultType="User">
    SELECT <include refid="baseColumns"/> FROM user
</select>
```

---

## 5. ResultMap 高级映射

### 5.1 基本映射

```xml
<resultMap id="userMap" type="User">
    <id property="id" column="id"/>
    <result property="userName" column="user_name"/>
    <result property="createTime" column="create_time"/>
</resultMap>
```

### 5.2 一对一关联（association）

```xml
<resultMap id="orderWithUser" type="Order">
    <id property="id" column="order_id"/>
    <result property="orderNo" column="order_no"/>
    <!-- 嵌套结果映射（一条 SQL，JOIN 查询） -->
    <association property="user" javaType="User">
        <id property="id" column="user_id"/>
        <result property="name" column="user_name"/>
    </association>
</resultMap>

<!-- 嵌套查询（N+1 问题，可配合 fetchType="lazy" 延迟加载） -->
<resultMap id="orderWithUserLazy" type="Order">
    <id property="id" column="id"/>
    <association property="user"
                 column="user_id"
                 select="com.example.mapper.UserMapper.selectById"
                 fetchType="lazy"/>
</resultMap>
```

### 5.3 一对多关联（collection）

```xml
<resultMap id="userWithOrders" type="User">
    <id property="id" column="user_id"/>
    <result property="name" column="user_name"/>
    <collection property="orders" ofType="Order">
        <id property="id" column="order_id"/>
        <result property="orderNo" column="order_no"/>
        <result property="amount" column="amount"/>
    </collection>
</resultMap>

<select id="selectUserWithOrders" resultMap="userWithOrders">
    SELECT u.id AS user_id, u.name AS user_name,
           o.id AS order_id, o.order_no, o.amount
    FROM user u
    LEFT JOIN orders o ON u.id = o.user_id
    WHERE u.id = #{userId}
</select>
```

---

## 6. MyBatis-Plus 常用功能

### 6.1 BaseMapper CRUD 接口

```java
// 实体类
@Data
@TableName("user")
public class User {
    @TableId(type = IdType.ASSIGN_ID)  // 雪花算法生成 ID
    private Long id;

    @TableField("user_name")           // 字段映射
    private String userName;

    private String email;

    @TableLogic                         // 逻辑删除字段
    private Integer deleted;

    @Version                            // 乐观锁版本号
    private Integer version;

    @TableField(fill = FieldFill.INSERT)
    private LocalDateTime createTime;

    @TableField(fill = FieldFill.INSERT_UPDATE)
    private LocalDateTime updateTime;
}

// Mapper 继承 BaseMapper
@Mapper
public interface UserMapper extends BaseMapper<User> {
    // 自动拥有以下方法：
    // insert(T entity)
    // deleteById(Serializable id)
    // deleteBatchIds(Collection idList)
    // updateById(T entity)
    // selectById(Serializable id)
    // selectBatchIds(Collection idList)
    // selectList(Wrapper queryWrapper)
    // selectCount(Wrapper queryWrapper)
    // selectPage(Page page, Wrapper queryWrapper)
}
```

### 6.2 条件构造器（Wrapper）

```java
// LambdaQueryWrapper（类型安全，推荐）
List<User> users = userMapper.selectList(
    new LambdaQueryWrapper<User>()
        .eq(User::getStatus, 1)                        // status = 1
        .like(User::getUserName, "张")                   // user_name LIKE '%张%'
        .between(User::getAge, 18, 60)                  // age BETWEEN 18 AND 60
        .in(User::getDept, Arrays.asList("技术", "产品")) // dept IN ('技术', '产品')
        .isNotNull(User::getEmail)                      // email IS NOT NULL
        .orderByDesc(User::getCreateTime)               // ORDER BY create_time DESC
        .last("LIMIT 10")                               // 拼接 SQL 末尾
);

// 条件判断：condition 参数控制是否拼接
String keyword = request.getParameter("keyword");
List<User> users = userMapper.selectList(
    new LambdaQueryWrapper<User>()
        .like(StringUtils.isNotBlank(keyword), User::getUserName, keyword)
        .eq(status != null, User::getStatus, status)
);

// LambdaUpdateWrapper
userMapper.update(null,
    new LambdaUpdateWrapper<User>()
        .set(User::getStatus, 0)
        .set(User::getUpdateTime, LocalDateTime.now())
        .eq(User::getId, userId)
);
```

### 6.3 分页插件

```java
// 配置分页拦截器
@Configuration
public class MyBatisPlusConfig {
    @Bean
    public MybatisPlusInterceptor mybatisPlusInterceptor() {
        MybatisPlusInterceptor interceptor = new MybatisPlusInterceptor();
        // 分页插件
        interceptor.addInnerInterceptor(
            new PaginationInnerInterceptor(DbType.MYSQL));
        // 乐观锁插件
        interceptor.addInnerInterceptor(new OptimisticLockerInnerInterceptor());
        // 防止全表更新/删除插件
        interceptor.addInnerInterceptor(new BlockAttackInnerInterceptor());
        return interceptor;
    }
}

// 使用分页
Page<User> page = new Page<>(1, 10);  // 当前页, 每页条数
Page<User> result = userMapper.selectPage(page,
    new LambdaQueryWrapper<User>().eq(User::getStatus, 1));

long total = result.getTotal();        // 总记录数
long pages = result.getPages();        // 总页数
List<User> records = result.getRecords(); // 当前页数据
```

### 6.4 逻辑删除

```yaml
# application.yml
mybatis-plus:
  global-config:
    db-config:
      logic-delete-field: deleted     # 全局逻辑删除字段名
      logic-delete-value: 1           # 删除值
      logic-not-delete-value: 0       # 未删除值
```

```java
// 使用效果：
userMapper.deleteById(1L);
// 实际执行：UPDATE user SET deleted = 1 WHERE id = 1 AND deleted = 0

userMapper.selectList(null);
// 实际执行：SELECT * FROM user WHERE deleted = 0
// 所有查询自动拼接 deleted = 0 条件
```

### 6.5 乐观锁

```java
// 实体类中标注 @Version
@Version
private Integer version;

// 使用：先查再改
User user = userMapper.selectById(1L);     // version = 1
user.setUserName("newName");
userMapper.updateById(user);
// 实际执行：UPDATE user SET user_name='newName', version=2
//           WHERE id=1 AND version=1
// 如果 version 不匹配，更新 0 条记录（被其他线程修改过）
```

### 6.6 自动填充

```java
@Component
public class MyMetaObjectHandler implements MetaObjectHandler {

    @Override
    public void insertFill(MetaObject metaObject) {
        this.strictInsertFill(metaObject, "createTime",
            LocalDateTime.class, LocalDateTime.now());
        this.strictInsertFill(metaObject, "updateTime",
            LocalDateTime.class, LocalDateTime.now());
    }

    @Override
    public void updateFill(MetaObject metaObject) {
        this.strictUpdateFill(metaObject, "updateTime",
            LocalDateTime.class, LocalDateTime.now());
    }
}
```

### 6.7 IService 服务层封装

```java
// Service 接口
public interface UserService extends IService<User> {
    // 自定义方法
}

// Service 实现
@Service
public class UserServiceImpl extends ServiceImpl<UserMapper, User>
        implements UserService {

    // 自动拥有：
    // save(T entity), saveBatch(Collection entityList)
    // removeById(Serializable id), removeBatchByIds(Collection idList)
    // updateById(T entity), saveOrUpdate(T entity)
    // getById(Serializable id), list(), page(Page page, Wrapper queryWrapper)
    // count(), lambdaQuery(), lambdaUpdate()
}

// 链式查询
List<User> users = userService.lambdaQuery()
    .eq(User::getStatus, 1)
    .like(User::getUserName, keyword)
    .list();
```

---

## 7. Spring Data JPA vs MyBatis 对比

| 特性 | MyBatis | Spring Data JPA（Hibernate） |
|------|---------|----------------------------|
| 定位 | 半自动 ORM（SQL Mapping） | 全自动 ORM |
| SQL 控制 | 开发者手写 SQL，完全可控 | 框架自动生成 SQL |
| 学习成本 | 低（会 SQL 就行） | 高（需理解 JPA 规范、HQL/JPQL、缓存等） |
| 复杂查询 | 优秀（灵活写 SQL） | 复杂查询需写 JPQL 或原生 SQL |
| 性能优化 | 容易（直接优化 SQL） | 较难（需理解 N+1 问题、延迟加载、缓存策略） |
| 数据库移植性 | 差（SQL 可能与数据库耦合） | 好（JPQL 屏蔽方言差异） |
| 开发效率 | 简单 CRUD 需要写 XML/注解 | 简单 CRUD 效率极高（方法名推导查询） |
| 国内使用情况 | 主流（绝大多数互联网公司） | 外企/中小项目较多 |
| 增强工具 | MyBatis-Plus（大幅提升效率） | Spring Data JPA 本身已很高效 |

```java
// Spring Data JPA 示例
public interface UserRepository extends JpaRepository<User, Long> {
    // 方法名推导查询
    List<User> findByStatusAndAgeGreaterThan(Integer status, Integer age);

    // @Query 自定义查询
    @Query("SELECT u FROM User u WHERE u.email LIKE %:keyword%")
    List<User> searchByEmail(@Param("keyword") String keyword);

    // 原生 SQL
    @Query(value = "SELECT * FROM user WHERE status = ?1", nativeQuery = true)
    List<User> findByStatusNative(Integer status);
}
```

---

## 8. 数据库连接池

### 8.1 HikariCP（Spring Boot 2.x+ 默认）

```yaml
spring:
  datasource:
    type: com.zaxxer.hikari.HikariDataSource
    url: jdbc:mysql://localhost:3306/mydb?useSSL=false&serverTimezone=Asia/Shanghai
    username: root
    password: 123456
    hikari:
      pool-name: MyHikariPool
      minimum-idle: 5                # 最小空闲连接数
      maximum-pool-size: 20          # 最大连接数
      idle-timeout: 600000           # 空闲连接超时（10分钟）
      max-lifetime: 1800000          # 连接最大存活时间（30分钟）
      connection-timeout: 30000      # 获取连接超时（30秒）
      connection-test-query: SELECT 1
```

**HikariCP 为什么快**：
- 使用 FastList 替代 ArrayList，避免 range check
- 使用 ConcurrentBag 实现无锁连接借还
- 使用 javassist 生成代理类，避免反射开销
- 精简的字节码，更少的内存占用

### 8.2 Druid（阿里巴巴，功能丰富）

```yaml
spring:
  datasource:
    type: com.alibaba.druid.pool.DruidDataSource
    druid:
      initial-size: 5                # 初始连接数
      min-idle: 5                    # 最小空闲连接数
      max-active: 20                 # 最大连接数
      max-wait: 60000                # 获取连接最大等待时间
      time-between-eviction-runs-millis: 60000  # 检测间隔
      min-evictable-idle-time-millis: 300000    # 最小空闲时间
      validation-query: SELECT 1
      test-while-idle: true
      test-on-borrow: false
      test-on-return: false
      # 监控配置
      stat-view-servlet:
        enabled: true
        url-pattern: /druid/*
        login-username: admin
        login-password: admin
      filter:
        stat:
          enabled: true
          slow-sql-millis: 1000      # 慢 SQL 阈值
        wall:
          enabled: true              # SQL 防火墙
```

### 8.3 HikariCP vs Druid 对比

| 特性 | HikariCP | Druid |
|------|----------|-------|
| 性能 | 极致性能，业界最快 | 性能优秀，略低于 HikariCP |
| 监控 | 基础监控 | 内置完善的监控页面 |
| SQL 防火墙 | 无 | 支持（WallFilter） |
| 慢 SQL 记录 | 需要额外配置 | 内置支持 |
| Spring Boot 默认 | 是（2.x+） | 否 |
| 代码量 | ~130KB，极精简 | ~2MB，功能丰富 |
| 推荐场景 | 追求极致性能 | 需要完善的监控和 SQL 防火墙 |

---

## 9. MyBatis 插件机制

### 9.1 插件原理

MyBatis 允许通过插件（Interceptor）拦截以下四大对象的方法调用：

| 对象 | 可拦截方法 | 典型用途 |
|------|----------|---------|
| Executor | update, query, commit, rollback | 缓存、事务 |
| StatementHandler | prepare, parameterize, batch | SQL 改写 |
| ParameterHandler | setParameters | 参数加密 |
| ResultSetHandler | handleResultSets | 结果解密 |

```java
@Intercepts({
    @Signature(type = StatementHandler.class,
               method = "prepare",
               args = {Connection.class, Integer.class})
})
public class SqlLogPlugin implements Interceptor {

    @Override
    public Object intercept(Invocation invocation) throws Throwable {
        StatementHandler handler = (StatementHandler) invocation.getTarget();
        BoundSql boundSql = handler.getBoundSql();
        String sql = boundSql.getSql();

        long start = System.currentTimeMillis();
        Object result = invocation.proceed();
        long cost = System.currentTimeMillis() - start;

        System.out.println("SQL: " + sql + " | 耗时: " + cost + "ms");
        return result;
    }
}
```

---

## 10. 高频面试题

### Q1：#{} 和 ${} 的区别？为什么 #{} 能防止 SQL 注入？
`#{}` 使用 PreparedStatement 的参数占位符（?），SQL 先预编译再填充参数值，参数会被转义，无法改变 SQL 结构。`${}` 是字符串直接拼接到 SQL 中，恶意输入可以改变 SQL 语义。`${}` 仅用于表名、列名等不能加引号的场景，且必须做白名单校验。

### Q2：MyBatis 的一级缓存和二级缓存？
一级缓存是 SqlSession 级别，默认开启，同一个 SqlSession 内相同查询直接返回缓存。二级缓存是 Mapper（namespace）级别，需手动开启，跨 SqlSession 共享。缓存查询顺序：二级缓存 -> 一级缓存 -> 数据库。注意多表联查时二级缓存可能有脏数据问题。

### Q3：MyBatis 的执行流程？
Mapper 接口方法调用 -> MapperProxy 代理 -> SqlSession -> Executor -> StatementHandler 创建 PreparedStatement -> ParameterHandler 设置参数 -> 执行 SQL -> ResultSetHandler 映射结果 -> TypeHandler 类型转换 -> 返回 Java 对象。

### Q4：MyBatis-Plus 的分页原理？
分页插件（PaginationInnerInterceptor）拦截 Executor 的 query 方法。先执行 COUNT 查询获取总数，再改写原 SQL 追加 LIMIT offset, size 子句。开发者只需传入 Page 对象，插件自动处理分页逻辑。

### Q5：MyBatis 如何处理延迟加载（懒加载）？
通过动态代理实现。当主查询返回结果时，关联对象用代理对象占位。当真正访问关联对象的属性时，代理拦截方法调用，执行关联查询 SQL 获取数据。配置：lazyLoadingEnabled=true，aggressiveLazyLoading=false。

### Q6：HikariCP 和 Druid 怎么选？
追求极致性能选 HikariCP（Spring Boot 默认），需要完善监控（SQL 统计、慢日志、防火墙）选 Druid。大多数项目使用 HikariCP + 外部 APM 监控即可满足需求。

### Q7：MyBatis 和 JPA 怎么选？
国内互联网公司首选 MyBatis（+MyBatis-Plus），SQL 可控，性能优化容易。外企或中小型项目可选 JPA，开发效率高，数据库移植性好。复杂报表查询建议 MyBatis。

### Q8：乐观锁和悲观锁在 MyBatis 中如何实现？
乐观锁：MyBatis-Plus @Version 注解，UPDATE 时 WHERE 条件带上 version，更新同时递增 version。悲观锁：在 SQL 中使用 SELECT ... FOR UPDATE，锁定查询行直到事务结束。乐观锁适合读多写少，悲观锁适合写多读少。

### Q9：MyBatis 的 Mapper 接口为什么不需要实现类？
MyBatis 使用 JDK 动态代理。启动时为每个 Mapper 接口创建 MapperProxy 代理对象，MapperProxy 实现了 InvocationHandler 接口。调用 Mapper 方法时，MapperProxy 根据方法签名找到对应的 MappedStatement，委托 SqlSession 执行 SQL。

### Q10：MyBatis 中如何处理批量操作？
方式一：foreach 标签拼接 VALUES 子句（推荐，一条 SQL），注意 MySQL 的 max_allowed_packet 限制。方式二：使用 BatchExecutor（多条 SQL 批量提交，适合超大数据量）。方式三：MyBatis-Plus 的 saveBatch 方法（底层使用 BatchExecutor）。
