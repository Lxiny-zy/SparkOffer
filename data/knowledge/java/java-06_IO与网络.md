# Java I/O 与网络编程

## I/O 模型

### BIO（Blocking I/O）
- 同步阻塞，一个连接一个线程
- 适合连接数少且固定的场景
- 线程在 read/write 时完全阻塞，无法做其他事

```java
// BIO 服务器
ServerSocket serverSocket = new ServerSocket(8080);
while (true) {
    Socket socket = serverSocket.accept(); // 阻塞等待连接
    new Thread(() -> {
        try (InputStream in = socket.getInputStream();
             OutputStream out = socket.getOutputStream()) {
            byte[] buffer = new byte[1024];
            int len = in.read(buffer); // 阻塞等待数据
            out.write("response".getBytes());
        }
    }).start(); // 每个连接一个线程，连接多时线程爆炸
}

// BIO 的致命问题：
// 1. 线程资源宝贵，每个连接占一个线程（约 1MB 栈空间）
// 2. 10000 个连接需要 10000 个线程 → OOM
// 3. 即使用线程池限制线程数，队列也可能堆积
```

### NIO（Non-blocking I/O）
- 同步非阻塞，基于 Channel + Buffer + Selector
- 一个线程管理多个连接（多路复用）
- JDK 1.4 引入

**三大核心组件：**

```java
// 1. Channel（双向通道）
// 与流不同，Channel 是双向的，可以同时读写
// 常用 Channel：
// - FileChannel：文件读写（阻塞模式）
// - SocketChannel：TCP 客户端
// - ServerSocketChannel：TCP 服务端
// - DatagramChannel：UDP

// 2. Buffer（缓冲区）
// 数据必须先读到 Buffer 再从 Buffer 取出（与流的直接读写不同）
ByteBuffer buffer = ByteBuffer.allocate(1024);

// Buffer 的核心属性
// capacity：总容量
// position：当前读写位置
// limit：读写的上限
// mark：标记位置

// Buffer 操作
buffer.put("hello".getBytes()); // 写模式：position 向后移动
buffer.flip();                  // 翻转：limit=position, position=0（切换到读模式）
byte[] data = new byte[buffer.remaining()];
buffer.get(data);               // 读模式：position 向后移动
buffer.clear();                 // 清空：position=0, limit=capacity
buffer.compact();               // 压缩：未读数据移到开头，position 指向未读数据后面

// 3. Selector（选择器/多路复用器）
// 一个线程监控多个 Channel 的事件（连接、可读、可写）
```

**NIO 服务器核心流程：**

```java
Selector selector = Selector.open();
ServerSocketChannel server = ServerSocketChannel.open();
server.bind(new InetSocketAddress(8080));
server.configureBlocking(false); // 非阻塞模式
server.register(selector, SelectionKey.OP_ACCEPT); // 注册连接事件

while (true) {
    int readyCount = selector.select(); // 阻塞直到有事件就绪
    if (readyCount == 0) continue;

    Set<SelectionKey> keys = selector.selectedKeys();
    Iterator<SelectionKey> iter = keys.iterator();
    while (iter.hasNext()) {
        SelectionKey key = iter.next();

        if (key.isAcceptable()) {
            // 有新连接
            SocketChannel client = server.accept();
            client.configureBlocking(false);
            client.register(selector, SelectionKey.OP_READ);
        }

        if (key.isReadable()) {
            // 有数据可读
            SocketChannel client = (SocketChannel) key.channel();
            ByteBuffer buf = ByteBuffer.allocate(1024);
            int len = client.read(buf);
            if (len > 0) {
                buf.flip();
                // 处理数据...
            } else if (len == -1) {
                key.cancel();
                client.close();
            }
        }

        iter.remove(); // 必须移除已处理的 key！
    }
}
```

**NIO vs BIO 对比：**

| 特性 | BIO | NIO |
|------|-----|-----|
| 线程模型 | 一连接一线程 | 一线程管多连接 |
| 阻塞 | 阻塞 | 非阻塞 |
| 数据处理 | 流式，顺序读写 | Buffer 块读写 |
| 触发方式 | - | Selector 事件驱动 |
| 适用场景 | 连接数少，短连接 | 连接数多，长连接 |

### AIO（Asynchronous I/O）
- 异步非阻塞，操作完成后回调通知
- JDK 1.7 引入
- 适合连接数多且操作时间长的场景

```java
// AIO 异步文件读取
AsynchronousFileChannel channel = AsynchronousFileChannel.open(
    Paths.get("test.txt"), StandardOpenOption.READ);

ByteBuffer buffer = ByteBuffer.allocate(1024);
channel.read(buffer, 0, buffer, new CompletionHandler<Integer, ByteBuffer>() {
    @Override
    public void completed(Integer result, ByteBuffer attachment) {
        attachment.flip();
        System.out.println("读取完成: " + new String(attachment.array(), 0, result));
    }

    @Override
    public void failed(Throwable exc, ByteBuffer attachment) {
        exc.printStackTrace();
    }
});

// AIO 服务器
AsynchronousServerSocketChannel server = AsynchronousServerSocketChannel.open()
    .bind(new InetSocketAddress(8080));

server.accept(null, new CompletionHandler<AsynchronousSocketChannel, Void>() {
    @Override
    public void completed(AsynchronousSocketChannel client, Void attachment) {
        server.accept(null, this); // 继续接受下一个连接
        ByteBuffer buf = ByteBuffer.allocate(1024);
        client.read(buf, buf, new CompletionHandler<>() { /* ... */ });
    }
    @Override
    public void failed(Throwable exc, Void attachment) { }
});

// 注意：Linux 下 AIO 实际使用 epoll 模拟（不是真正的异步 IO）
// 因此 Linux 上 NIO（Netty）性能往往优于 AIO
// Windows 下 AIO 使用 IOCP，是真正的异步
```

### 底层多路复用实现

```
操作系统级别的 I/O 多路复用：

1. select（最早期，跨平台）
   - fd 集合大小有限（通常 1024）
   - 每次调用需要拷贝全部 fd 到内核
   - 内核线性扫描所有 fd → O(n)

2. poll（改进版 select）
   - 没有 fd 数量限制
   - 仍然需要线性扫描 → O(n)

3. epoll（Linux 特有，最优）
   - 基于事件驱动，只返回就绪的 fd → O(1)
   - 使用 mmap 减少用户态/内核态拷贝
   - 支持边缘触发（ET）和水平触发（LT）
   - Java NIO 在 Linux 上底层就是 epoll

4. kqueue（macOS/BSD）
   - 类似 epoll 的事件驱动机制
```

## 零拷贝（Zero Copy）

```java
// 传统 IO 的数据拷贝路径：
// 磁盘 → 内核缓冲区 → 用户缓冲区 → Socket 缓冲区 → 网卡
// 4 次数据拷贝 + 4 次上下文切换

// 零拷贝方式1：FileChannel.transferTo()（底层使用 sendfile 系统调用）
// 磁盘 → 内核缓冲区 → 网卡（2 次拷贝 + 2 次上下文切换）
FileChannel srcChannel = FileChannel.open(Paths.get("file.txt"));
SocketChannel destChannel = SocketChannel.open(new InetSocketAddress("host", 8080));
srcChannel.transferTo(0, srcChannel.size(), destChannel);

// 零拷贝方式2：MappedByteBuffer（内存映射，底层 mmap）
// 将文件映射到用户空间，减少内核到用户空间的拷贝
FileChannel channel = FileChannel.open(Paths.get("large_file.dat"));
MappedByteBuffer mappedBuffer = channel.map(
    FileChannel.MapMode.READ_ONLY, 0, channel.size());
// 直接操作 mappedBuffer 就是操作文件内容（按需加载到内存）

// 零拷贝方式3：DirectByteBuffer（堆外内存）
ByteBuffer directBuffer = ByteBuffer.allocateDirect(1024);
// 数据不经过 Java 堆，减少一次堆内/堆外拷贝

// Netty 中的零拷贝：
// 1. 使用 DirectByteBuffer（堆外内存）
// 2. CompositeByteBuf 合并多个 Buffer（逻辑上合并，不复制数据）
// 3. FileRegion 包装 FileChannel.transferTo
// 4. 对外提供统一的 ByteBuf 接口，减少不必要的拷贝
```

## Netty 框架（补充）

```java
// Netty 是 Java NIO 的封装框架，简化网络编程
// 几乎所有 Java 网络框架都基于 Netty（Dubbo、gRPC、Elasticsearch、Kafka等）

// Netty 核心组件：
// 1. EventLoopGroup：线程组（Boss 接受连接，Worker 处理读写）
// 2. Channel：Netty 封装的通道
// 3. ChannelHandler：业务处理器（编解码、业务逻辑）
// 4. ChannelPipeline：处理器链（类似 Servlet Filter）
// 5. ByteBuf：替代 NIO ByteBuffer，更好用

// Netty 服务器示例
EventLoopGroup bossGroup = new NioEventLoopGroup(1);    // 接受连接
EventLoopGroup workerGroup = new NioEventLoopGroup(4);  // 处理读写

ServerBootstrap bootstrap = new ServerBootstrap();
bootstrap.group(bossGroup, workerGroup)
    .channel(NioServerSocketChannel.class)
    .childHandler(new ChannelInitializer<SocketChannel>() {
        @Override
        protected void initChannel(SocketChannel ch) {
            ch.pipeline()
                .addLast(new LengthFieldBasedFrameDecoder(1024, 0, 4, 0, 4))
                .addLast(new StringDecoder())
                .addLast(new BusinessHandler()); // 自定义业务处理器
        }
    });

ChannelFuture future = bootstrap.bind(8080).sync();
future.channel().closeFuture().sync();

// Netty 线程模型（Reactor 模式）：
// - 主从 Reactor 多线程模型
// - BossGroup（MainReactor）：接受连接，注册到 WorkerGroup
// - WorkerGroup（SubReactor）：处理连接上的读写事件
// - 每个 EventLoop 绑定一个线程，管理多个 Channel
// - 一个 Channel 只绑定一个 EventLoop（线程安全）
```

## 序列化

### Java 原生序列化

```java
// 实现 Serializable 接口
public class User implements Serializable {
    private static final long serialVersionUID = 1L; // 版本号

    private String name;
    private int age;
    private transient String password; // transient 不参与序列化

    // 自定义序列化（可选）
    private void writeObject(ObjectOutputStream out) throws IOException {
        out.defaultWriteObject(); // 默认序列化
        out.writeObject(encrypt(password)); // 自定义加密序列化
    }

    private void readObject(ObjectInputStream in) throws Exception {
        in.defaultReadObject();
        this.password = decrypt((String) in.readObject());
    }
}

// 序列化
ObjectOutputStream oos = new ObjectOutputStream(new FileOutputStream("user.dat"));
oos.writeObject(user);

// 反序列化
ObjectInputStream ois = new ObjectInputStream(new FileInputStream("user.dat"));
User user = (User) ois.readObject();
```

**serialVersionUID 的重要性：**
```java
// 反序列化时会比较 serialVersionUID
// 不一致 → InvalidClassException

// 如果不显式指定，JVM 会根据类的结构（字段、方法等）自动生成
// 任何类结构变化都会导致 UID 变化 → 反序列化失败
// 最佳实践：总是显式指定 serialVersionUID
```

**Java 原生序列化的问题：**
1. **安全风险**：反序列化可以构造恶意对象（反序列化漏洞）
2. **性能差**：比 JSON、Protobuf 慢数倍
3. **体积大**：包含类名、字段名等元数据
4. **跨语言不兼容**：只能 Java 使用
5. **版本兼容性差**：类结构变化可能导致失败

### 常用序列化框架对比

| 框架 | 格式 | 性能 | 体积 | 可读性 | 跨语言 | 典型场景 |
|------|------|------|------|--------|--------|---------|
| JSON（Jackson/Gson） | 文本 | 中等 | 较大 | 好 | 是 | REST API |
| Protobuf | 二进制 | 高 | 小 | 差 | 是 | gRPC、内部通信 |
| Kryo | 二进制 | 极高 | 极小 | 差 | 否（Java） | 本地缓存、Spark |
| Hessian | 二进制 | 高 | 小 | 差 | 是 | Dubbo（默认） |
| MessagePack | 二进制 | 高 | 小 | 差 | 是 | 嵌入式、移动端 |
| Avro | 二进制 | 高 | 小 | 差 | 是 | Hadoop、Kafka |

```java
// Jackson 序列化（最常用的 JSON 库）
ObjectMapper mapper = new ObjectMapper();
String json = mapper.writeValueAsString(user);         // 对象 → JSON
User user = mapper.readValue(json, User.class);        // JSON → 对象

// Jackson 常用注解
@JsonProperty("user_name")   // 指定 JSON 字段名
@JsonIgnore                  // 忽略字段
@JsonFormat(pattern = "yyyy-MM-dd") // 日期格式
@JsonInclude(JsonInclude.Include.NON_NULL) // null 不序列化

// Protobuf（需要 .proto 文件定义）
// message User {
//     string name = 1;
//     int32 age = 2;
// }
byte[] bytes = user.toByteArray();           // 序列化
User user = User.parseFrom(bytes);           // 反序列化

// Kryo（Java 性能最好的序列化框架）
Kryo kryo = new Kryo();
kryo.register(User.class);
Output output = new Output(new FileOutputStream("user.bin"));
kryo.writeObject(output, user);
output.close();
```

## 文件操作（NIO.2 / Java 7+）

```java
// Path + Files API（推荐，替代 File 类）
Path path = Paths.get("/data/test.txt");

// 读取文件
String content = Files.readString(path);                    // Java 11+
List<String> lines = Files.readAllLines(path, StandardCharsets.UTF_8);
byte[] bytes = Files.readAllBytes(path);

// 写入文件
Files.writeString(path, "hello", StandardOpenOption.CREATE);  // Java 11+
Files.write(path, lines, StandardCharsets.UTF_8);

// 流式读取大文件（避免 OOM）
try (Stream<String> stream = Files.lines(path)) {
    stream.filter(line -> line.contains("error"))
          .forEach(System.out::println);
}

// try-with-resources 自动关闭（Java 7+）
try (BufferedReader reader = Files.newBufferedReader(path)) {
    String line;
    while ((line = reader.readLine()) != null) {
        process(line);
    }
} // 自动关闭 reader

// 文件遍历
try (Stream<Path> walk = Files.walk(Paths.get("/data"), 3)) { // 最大深度 3
    walk.filter(Files::isRegularFile)
        .filter(p -> p.toString().endsWith(".java"))
        .forEach(System.out::println);
}

// 文件监控（WatchService）
WatchService watcher = FileSystems.getDefault().newWatchService();
Paths.get("/data").register(watcher, StandardWatchEventKinds.ENTRY_CREATE);
WatchKey key = watcher.take(); // 阻塞等待事件
for (WatchEvent<?> event : key.pollEvents()) {
    System.out.println("新文件: " + event.context());
}
```

## TCP 粘包/拆包

```
// TCP 是面向字节流的，没有消息边界的概念
// 发送方发送 ABC 和 DEF 两个包，接收方可能收到：
// 1. ABC + DEF（正常）
// 2. ABCDEF（粘包：两个包粘在一起）
// 3. AB + CDEF（拆包+粘包）

// 解决方案：
// 1. 固定长度：每个消息固定 N 字节（浪费空间）
// 2. 分隔符：用特殊字符分隔（如 \n）
// 3. 长度字段：消息头包含消息长度（最常用）
//    [4字节长度][消息体]

// Netty 中的解码器：
// FixedLengthFrameDecoder：固定长度
// LineBasedFrameDecoder：按行分隔
// DelimiterBasedFrameDecoder：自定义分隔符
// LengthFieldBasedFrameDecoder：长度字段（最通用）
```

## 踩坑指南

### 1. 流/连接未关闭
```java
// 错误：流未关闭导致资源泄漏
InputStream in = new FileInputStream("file.txt");
in.read();
// 如果这里抛异常，in 就不会被关闭

// 正确：try-with-resources（Java 7+）
try (InputStream in2 = new FileInputStream("file.txt")) {
    in2.read();
} // 自动关闭

// 多个资源
try (InputStream in3 = new FileInputStream("a.txt");
     OutputStream out = new FileOutputStream("b.txt")) {
    // 使用 in 和 out
} // 按声明的逆序关闭
```

### 2. ByteBuffer 的 flip() 和 clear()
```java
ByteBuffer buf = ByteBuffer.allocate(1024);
buf.put("hello".getBytes()); // 写入后 position=5, limit=1024

// 忘记 flip() 就读，读不到数据！
buf.flip(); // position=0, limit=5（切换为读模式）
byte[] data = new byte[buf.remaining()]; // remaining = limit - position = 5
buf.get(data);

buf.clear(); // 重置为写模式（不是真正清除数据）
// 或 buf.compact() 压缩（保留未读数据）
```

### 3. 大文件读取 OOM
```java
// 错误：一次性读取大文件到内存
byte[] data = Files.readAllBytes(Paths.get("huge_file.dat")); // OOM!

// 正确：流式处理
try (BufferedReader reader = Files.newBufferedReader(Paths.get("huge_file.txt"))) {
    reader.lines()
          .filter(line -> line.contains("keyword"))
          .forEach(this::process);
}

// 或使用 MappedByteBuffer（内存映射，按需加载）
try (FileChannel channel = FileChannel.open(Paths.get("huge_file.dat"))) {
    MappedByteBuffer mapped = channel.map(FileChannel.MapMode.READ_ONLY, 0, channel.size());
    // 操作 mapped，OS 会按需将文件页加载到物理内存
}
```

### 4. 编码问题
```java
// 指定编码，不要依赖系统默认编码
String content = new String(bytes, StandardCharsets.UTF_8);
byte[] data2 = content.getBytes(StandardCharsets.UTF_8);

// Files API 默认使用 UTF-8（Java 18+ 系统默认编码也改为 UTF-8）
// 但旧版本中系统默认编码可能是 GBK（Windows 中文系统）
```

### 5. Selector 空轮询 Bug
```java
// Linux 下 NIO Selector.select() 可能因为 epoll bug 立即返回
// 导致空轮询，CPU 100%

// Netty 的解决方案：
// 检测到空轮询次数超过阈值（默认 512 次）后
// 重建 Selector（关闭旧的，创建新的，重新注册所有 Channel）
```

## 最佳实践

1. **优先使用 NIO.2 的 Files/Path API**：比传统 File 类功能强大且安全
2. **大文件用流式处理**：避免一次性加载到内存
3. **网络编程使用 Netty**：不要直接用原生 NIO（API 复杂、Bug多）
4. **try-with-resources 关闭资源**：永远不要手动 close
5. **指定字符编码**：不要依赖系统默认编码
6. **序列化选择 JSON 或 Protobuf**：不要用 Java 原生序列化
7. **Buffer 操作注意 flip/clear**：读写模式切换容易出错
8. **零拷贝优化文件传输**：大文件传输用 transferTo 或 MappedByteBuffer

## 面试高频问题及详细解答

### Q1：BIO、NIO、AIO 的区别？
**答**：**BIO** 同步阻塞，一个连接一个线程，read/write 时线程阻塞；**NIO** 同步非阻塞，基于 Selector 多路复用，一个线程管理多个连接，Channel 是非阻塞的但 Selector.select() 是阻塞的；**AIO** 异步非阻塞，操作完成后回调通知，不需要轮询。Linux 下 NIO（epoll）性能最好，AIO 实际用 epoll 模拟，所以主流框架（Netty）都用 NIO。

### Q2：NIO 的核心组件是什么？Selector 的作用？
**答**：三大核心组件：Channel（双向通道）、Buffer（数据缓冲区）、Selector（选择器/多路复用器）。Selector 允许一个线程监控多个 Channel 的 IO 事件（连接、可读、可写），避免每个连接都需要一个线程。底层在 Linux 上使用 epoll 实现，事件驱动，效率高。

### Q3：什么是零拷贝？Java 中如何实现？
**答**：零拷贝是减少数据在用户空间和内核空间之间拷贝次数的技术。传统 IO 需要 4 次拷贝，零拷贝减少到 2 次。Java 实现：(1) `FileChannel.transferTo()` 底层使用 sendfile 系统调用 (2) `MappedByteBuffer` 底层使用 mmap 内存映射 (3) `DirectByteBuffer` 堆外内存减少堆内外拷贝。Netty 的零拷贝还包括 CompositeByteBuf 逻辑合并多个 Buffer。

### Q4：序列化和反序列化是什么？为什么需要 serialVersionUID？
**答**：序列化是将对象转为字节流（用于网络传输或持久化），反序列化是将字节流还原为对象。`serialVersionUID` 是序列化版本号，反序列化时会比较发送方和接收方的 UID，不一致则抛 InvalidClassException。如果不显式指定，JVM 根据类结构自动生成，任何字段变化都会导致 UID 变化。最佳实践：总是显式指定。

### Q5：Netty 为什么比原生 NIO 好？
**答**：(1) 封装了 NIO 的复杂 API，使用简单 (2) 修复了原生 NIO 的 bug（如 Selector 空轮询）(3) 内置了丰富的编解码器（HTTP、WebSocket、Protobuf）(4) 高性能的内存管理（PooledByteBuf 池化、零拷贝）(5) 优雅的线程模型（Reactor 模式）(6) 完善的异常处理和连接管理。

### Q6：TCP 粘包/拆包是什么？如何解决？
**答**：TCP 是字节流协议，没有消息边界。发送的多个消息可能被合并（粘包）或拆分（拆包）。解决方案：(1) 固定长度消息 (2) 分隔符（如换行符）(3) 消息头包含长度字段（最常用）。Netty 提供了对应的解码器：FixedLengthFrameDecoder、DelimiterBasedFrameDecoder、LengthFieldBasedFrameDecoder。

### Q7：select、poll、epoll 的区别？
**答**：(1) **select**：fd 数量有限（1024），每次调用拷贝全部 fd 到内核，线性扫描 O(n)；(2) **poll**：无 fd 数量限制，但仍线性扫描 O(n)；(3) **epoll**：Linux 特有，事件驱动只返回就绪 fd O(1)，使用 mmap 减少拷贝。Java NIO Selector 在 Linux 上底层使用 epoll。

### Q8：Java NIO 中 Buffer 的 flip() 和 clear() 分别做什么？
**答**：`flip()` 将 Buffer 从写模式切换到读模式：limit 设为当前 position，position 设为 0。`clear()` 将 Buffer 重置为写模式：position 设为 0，limit 设为 capacity（不真正清除数据）。另外 `compact()` 会将未读数据移到 Buffer 开头，position 指向数据后面，limit 设为 capacity。

### Q9：如何优雅地关闭 IO 资源？
**答**：使用 try-with-resources（Java 7+），实现 AutoCloseable 接口的资源在 try 块结束时自动关闭。多个资源按声明的逆序关闭。即使发生异常也能保证关闭。不要手动在 finally 中调用 close()（容易写错、不美观）。Java 9+ try-with-resources 还支持有效最终变量（effectively final）。

### Q10：Reactor 线程模型有哪几种？Netty 用的哪种？
**答**：(1) **单 Reactor 单线程**：一个线程处理所有事件（Redis 6.0 之前）(2) **单 Reactor 多线程**：一个线程接受连接，线程池处理业务 (3) **主从 Reactor 多线程**：主 Reactor 接受连接，从 Reactor 处理 IO，线程池处理业务。Netty 默认使用主从 Reactor：BossGroup（MainReactor）接受连接，WorkerGroup（SubReactor）处理读写。

> **交叉引用**：NIO 中的 ByteBuffer 与直接内存参见 [JVM](./05_JVM.md)；Netty 线程池参见 [多线程与并发](./04_多线程与并发.md)；序列化在微服务中的应用参见相关框架模块
