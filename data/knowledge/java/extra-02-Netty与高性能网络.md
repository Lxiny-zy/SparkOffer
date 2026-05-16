# Netty 与高性能网络编程

Netty 是 Java 异步网络框架的事实标准。Dubbo、Spring Cloud Gateway、RocketMQ、Elasticsearch transport、gRPC Java 实现等全是 Netty 底座。理解 Netty 等于理解 Java 高性能网络的所有核心模式。

## 1. 为什么需要 Netty

JDK 原生 NIO API 难用：
- 复杂的 Selector / Channel / Buffer API
- 跨平台 bug（Linux epoll 空轮询）
- 缺少协议编解码框架
- 缺少高级特性（连接池、心跳、idle 检测、零拷贝）

Netty 封装这些，提供：
- 统一的 EventLoop 模型（Reactor）
- ChannelPipeline 责任链
- 池化 ByteBuf（零拷贝、引用计数）
- 大量协议实现（HTTP、WebSocket、MQTT、Redis、Memcached、SMTP）

## 2. IO 模型回顾

| 模型 | 描述 | 代表 |
|---|---|---|
| BIO | 同步阻塞，一连接一线程 | Tomcat 8 之前 BIO connector |
| NIO | 同步非阻塞 + 多路复用 | JDK NIO |
| AIO | 异步非阻塞，OS 完成后回调 | Windows IOCP（Linux 实现差） |
| Reactor | NIO + 事件循环 + 责任链 | Netty / Nginx / Redis |
| Proactor | AIO 模式的设计模式 | Windows 上 Netty |

**Reactor 模式**：
```
[Acceptor] 接受新连接 → 注册到 EventLoop
[EventLoop] 单线程循环：select → 处理 IO 事件 → dispatch handler
[Handler] 处理具体业务（解码、业务逻辑、编码）
```

## 3. Netty 核心组件

### 3.1 EventLoop & EventLoopGroup

EventLoop = 一个线程 + 一个 Selector + 任务队列。

EventLoopGroup = 多个 EventLoop 的池。

```java
// 主从 Reactor 模型
EventLoopGroup boss = new NioEventLoopGroup(1);          // 接连接
EventLoopGroup worker = new NioEventLoopGroup();          // 处理 IO（默认 CPU * 2）

ServerBootstrap b = new ServerBootstrap();
b.group(boss, worker)
 .channel(NioServerSocketChannel.class)
 .childHandler(new ChannelInitializer<SocketChannel>() {
     protected void initChannel(SocketChannel ch) {
         ch.pipeline()
           .addLast(new HttpServerCodec())
           .addLast(new HttpObjectAggregator(65536))
           .addLast(new MyBizHandler());
     }
 });
b.bind(8080).sync();
```

**Linux 用 EpollEventLoopGroup（更快 + 修复 epoll bug）**。

### 3.2 Channel

抽象网络连接。常用：
- NioServerSocketChannel / NioSocketChannel
- EpollServerSocketChannel（Linux 优化）
- LocalChannel（同进程）
- EmbeddedChannel（测试用）

### 3.3 ChannelPipeline & ChannelHandler

每个 Channel 有自己的 Pipeline。Handler 串成链处理事件。

**Inbound**（数据进入）：解码 → 业务处理。
**Outbound**（数据外发）：业务响应 → 编码。

```java
class MyDecoder extends ByteToMessageDecoder {
    protected void decode(ChannelHandlerContext ctx, ByteBuf in, List<Object> out) {
        if (in.readableBytes() < 4) return;
        int len = in.getInt(in.readerIndex());
        if (in.readableBytes() < 4 + len) return;
        in.skipBytes(4);
        byte[] data = new byte[len];
        in.readBytes(data);
        out.add(new String(data, StandardCharsets.UTF_8));
    }
}

class MyBizHandler extends SimpleChannelInboundHandler<String> {
    protected void channelRead0(ChannelHandlerContext ctx, String msg) {
        System.out.println("received: " + msg);
        ctx.writeAndFlush("echo: " + msg);
    }
    
    public void exceptionCaught(ChannelHandlerContext ctx, Throwable cause) {
        cause.printStackTrace();
        ctx.close();
    }
}
```

### 3.4 ByteBuf

Netty 自己的 buffer，取代 JDK ByteBuffer。优势：
- 读写双指针（不需要 flip）
- 自动扩容
- 池化（PooledByteBufAllocator）减少 GC
- 引用计数（堆外内存手动管理）
- 复合 buffer（CompositeByteBuf 零拷贝合并）

```java
ByteBuf buf = ctx.alloc().buffer();   // 池化分配
buf.writeInt(42);
buf.writeBytes("hello".getBytes());
// 使用后必须 release（堆外内存）
buf.release();
```

## 4. 编解码器

### 4.1 拆包粘包

TCP 是流协议，包边界自定义。Netty 内置：
- **FixedLengthFrameDecoder**：定长
- **LineBasedFrameDecoder**：按 \n 分
- **DelimiterBasedFrameDecoder**：按指定分隔符
- **LengthFieldBasedFrameDecoder**：按长度字段（最常用）

```java
// 4 字节长度 + 数据
new LengthFieldBasedFrameDecoder(
    65535,  // maxFrameLength
    0,      // lengthFieldOffset
    4,      // lengthFieldLength
    0,      // lengthAdjustment
    4       // initialBytesToStrip
);
```

### 4.2 自定义协议

```
+--------+--------+----+--------+--------+
| Magic  | Ver    | Type | Length | Body |
| 2bytes | 1byte  |1byte | 4bytes | N    |
+--------+--------+----+--------+--------+
```

```java
class MyProtocolEncoder extends MessageToByteEncoder<Message> {
    protected void encode(ChannelHandlerContext ctx, Message msg, ByteBuf out) {
        out.writeShort(0xCAFE);     // magic
        out.writeByte(1);            // version
        out.writeByte(msg.getType());
        byte[] body = msg.serialize();
        out.writeInt(body.length);
        out.writeBytes(body);
    }
}
```

## 5. 性能特性

### 5.1 零拷贝

四个层面：
1. **CompositeByteBuf**：逻辑上合并多个 ByteBuf，不实际复制内存
2. **Direct Buffer**：堆外内存，避免 JVM 堆 → 内核缓冲区的复制
3. **FileRegion**：用 sendfile 系统调用，文件直接发到 socket
4. **wrap & slice**：包装现有数组，不复制

```java
// 零拷贝大文件传输
FileChannel fileChannel = new FileInputStream(file).getChannel();
ctx.writeAndFlush(new DefaultFileRegion(fileChannel, 0, file.length()));
```

### 5.2 内存池

PooledByteBufAllocator 内置 jemalloc 风格的内存池：
- arena 分区（每线程独立 arena 避免锁竞争）
- chunk → page → subpage 多级分配
- 池化 + 复用大幅降低 GC

默认开启。可调：`-Dio.netty.allocator.numHeapArenas` / `numDirectArenas`。

### 5.3 EventLoop 调优

```java
// 默认 worker 线程数 = CPU * 2，可调
new NioEventLoopGroup(16);
```

**绝不要在 EventLoop 线程做阻塞操作**（DB 查询、HTTP 调用、长计算），会卡死其他连接。阻塞工作扔业务线程池：

```java
class MyHandler extends SimpleChannelInboundHandler<String> {
    private static final EventExecutorGroup bizPool = new DefaultEventExecutorGroup(32);
    
    protected void channelRead0(ChannelHandlerContext ctx, String msg) {
        bizPool.submit(() -> {
            String result = slowDbQuery(msg);
            ctx.writeAndFlush(result);
        });
    }
}
```

或 pipeline 配置：
```java
pipeline.addLast(bizPool, new MyBizHandler());
```

## 6. 高级特性

### 6.1 IdleStateHandler（心跳）

```java
pipeline.addLast(new IdleStateHandler(60, 30, 0));  // read / write / all
pipeline.addLast(new ChannelInboundHandlerAdapter() {
    public void userEventTriggered(ChannelHandlerContext ctx, Object evt) {
        if (evt instanceof IdleStateEvent e) {
            if (e.state() == IdleState.READER_IDLE) {
                ctx.close();  // 长时间未收到，断开
            } else if (e.state() == IdleState.WRITER_IDLE) {
                ctx.writeAndFlush(PING);  // 发心跳
            }
        }
    }
});
```

### 6.2 ChannelOption 关键参数

```java
.option(ChannelOption.SO_BACKLOG, 1024)        // listen queue
.childOption(ChannelOption.SO_KEEPALIVE, true)
.childOption(ChannelOption.TCP_NODELAY, true)  // 禁 Nagle
.childOption(ChannelOption.SO_REUSEADDR, true)
.childOption(ChannelOption.WRITE_BUFFER_WATER_MARK,
    new WriteBufferWaterMark(32*1024, 64*1024))  // 反压
```

### 6.3 SSL/TLS

```java
SslContext sslCtx = SslContextBuilder.forServer(certFile, keyFile).build();
pipeline.addFirst(sslCtx.newHandler(ch.alloc()));
```

支持 OpenSSL（更快，需 netty-tcnative）。

### 6.4 反压（Backpressure）

```java
if (ctx.channel().isWritable()) {
    ctx.writeAndFlush(msg);
} else {
    // 高水位，暂停生产
}

// 触发条件：当 ChannelOutboundBuffer > 高水位
public void channelWritabilityChanged(ChannelHandlerContext ctx) {
    if (ctx.channel().isWritable()) {
        resumeProducer();
    } else {
        pauseProducer();
    }
}
```

## 7. 常见 Bug & 陷阱

### 7.1 内存泄漏

```java
// ❌ 忘了 release
public void channelRead(ChannelHandlerContext ctx, Object msg) {
    ByteBuf buf = (ByteBuf) msg;
    process(buf);
    // 丢了 release，每次连接漏一点
}

// ✓ 用 SimpleChannelInboundHandler（自动 release）
class MyHandler extends SimpleChannelInboundHandler<ByteBuf> {
    protected void channelRead0(...) { process(msg); }
}

// ✓ 启用泄漏检测
-Dio.netty.leakDetection.level=PARANOID
```

### 7.2 死锁

EventLoop A 等 EventLoop B 上的 future，B 又等 A。
**绝不在 EventLoop 同步 wait 另一个 EventLoop 任务**。

### 7.3 业务阻塞 EventLoop

参 5.3 节。生产事故高发区。

### 7.4 ByteBuf 跨线程

ByteBuf 不是线程安全的。跨线程传必须 duplicate / retain。

## 8. 监控

```java
ChannelOption.WRITE_BUFFER_WATER_MARK
ChannelOption.SO_RCVBUF / SO_SNDBUF
```

JMX / Micrometer 暴露：
- 连接数
- 每秒读写字节
- 每秒消息数
- EventLoop 任务队列长度
- ByteBuf 池利用率

## 9. 实战案例：实现一个简化版 RPC

```java
// Server
ServerBootstrap b = new ServerBootstrap();
b.group(boss, worker)
 .channel(NioServerSocketChannel.class)
 .childHandler(new ChannelInitializer<SocketChannel>() {
     protected void initChannel(SocketChannel ch) {
         ch.pipeline()
           .addLast(new LengthFieldBasedFrameDecoder(64*1024, 0, 4, 0, 4))
           .addLast(new LengthFieldPrepender(4))
           .addLast(new RpcRequestDecoder())
           .addLast(new RpcResponseEncoder())
           .addLast(bizPool, new RpcServerHandler(serviceRegistry));
     }
 });

// Client
Bootstrap c = new Bootstrap();
c.group(worker)
 .channel(NioSocketChannel.class)
 .handler(new ChannelInitializer<SocketChannel>() {
     protected void initChannel(SocketChannel ch) {
         ch.pipeline()
           .addLast(new LengthFieldBasedFrameDecoder(64*1024, 0, 4, 0, 4))
           .addLast(new LengthFieldPrepender(4))
           .addLast(new RpcRequestEncoder())
           .addLast(new RpcResponseDecoder())
           .addLast(new RpcClientHandler(pendingRequests));
     }
 });
```

## 10. 高频面试题

**Q1：Netty Reactor 模型？**
主从 Reactor：boss EventLoop 只负责 accept 新连接，分配给 worker EventLoop；worker 处理该连接的所有 IO 事件。一个 worker EventLoop 可服务多个连接（多路复用）。线程数 = CPU * 2 默认。

**Q2：Netty 零拷贝有哪些？**
四种：① CompositeByteBuf 逻辑合并；② Direct Buffer 避免 JVM 堆复制；③ FileRegion + sendfile 文件零拷贝；④ wrap/slice 包装而非复制。本质是减少内核-用户态、用户态-用户态的 copy。

**Q3：ByteBuf 跟 JDK ByteBuffer 区别？**
- ByteBuf 读写双指针（readerIndex / writerIndex），不需要 flip
- 自动扩容
- 池化分配（减少 GC）
- 引用计数管理堆外内存
- 复合 buffer 支持

**Q4：怎么处理 TCP 粘包拆包？**
用 LengthFieldBasedFrameDecoder（长度字段）或 LineBasedFrameDecoder（按分隔符）或 FixedLengthFrameDecoder（定长）。自定义协议时设计明确的边界标识。

**Q5：EventLoop 能不能阻塞？**
绝对不能。EventLoop 是 IO 调度线程，阻塞会卡住所有连接的 IO 事件。阻塞工作（DB、HTTP、计算）必须扔到业务线程池（DefaultEventExecutorGroup 或自定义 ExecutorService），处理完用 `ctx.executor().execute()` 切回 EventLoop 发响应。

**Q6：Netty 怎么实现高性能？**
① Reactor 模型 + EventLoop 线程绑定 channel 避免线程切换；② 池化 ByteBuf 减 GC；③ 零拷贝多手段；④ epoll 替代 select；⑤ 业务责任链解耦；⑥ JIT 友好（短方法、final 类）。

**Q7：怎么排查 Netty 内存泄漏？**
启动加 `-Dio.netty.leakDetection.level=PARANOID`（每个 buf 都检测），日志里看 LEAK 记录的栈。检查 ChannelHandler 是否漏 release，或用 SimpleChannelInboundHandler 自动释放。
