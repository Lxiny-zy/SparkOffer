# Java 基础语法与数据类型

## 基本数据类型（8种）

| 类型 | 大小 | 默认值 | 范围 | 包装类 |
|------|------|--------|------|--------|
| byte | 1字节 | 0 | -128 ~ 127 | Byte |
| short | 2字节 | 0 | -32768 ~ 32767 | Short |
| int | 4字节 | 0 | -2^31 ~ 2^31-1 | Integer |
| long | 8字节 | 0L | -2^63 ~ 2^63-1 | Long |
| float | 4字节 | 0.0f | IEEE 754 | Float |
| double | 8字节 | 0.0d | IEEE 754 | Double |
| char | 2字节 | '\u0000' | 0 ~ 65535 | Character |
| boolean | - | false | true/false | Boolean |

### 深入理解基本数据类型

**为什么 boolean 大小不确定？**
JVM 规范并没有明确规定 boolean 的大小。在 HotSpot 实现中，boolean 在栈上占 4 字节（按 int 处理），在数组中占 1 字节。这是因为 JVM 字节码中没有直接操作 boolean 的指令，而是用 int 指令代替。

**浮点数精度问题（IEEE 754）：**
```java
// 经典陷阱：浮点数不精确
System.out.println(0.1 + 0.2);           // 0.30000000000000004
System.out.println(0.1 + 0.2 == 0.3);    // false

// 正确比较浮点数
double a = 0.1 + 0.2;
double b = 0.3;
System.out.println(Math.abs(a - b) < 1e-10); // true

// 金融计算必须用 BigDecimal
BigDecimal bd1 = new BigDecimal("0.1");
BigDecimal bd2 = new BigDecimal("0.2");
System.out.println(bd1.add(bd2)); // 0.3（精确）

// 注意：BigDecimal(double) 构造器也有精度问题
BigDecimal wrong = new BigDecimal(0.1); // 0.1000000000000000055511151231257827021181583404541015625
BigDecimal right = new BigDecimal("0.1"); // 0.1
BigDecimal alsoRight = BigDecimal.valueOf(0.1); // 0.1
```

**char 与 Unicode：**
```java
// Java 的 char 使用 UTF-16 编码，2 字节
char c = '中';       // 中文字符占一个 char
char c2 = '\u4e2d';  // Unicode 转义写法

// 但有些 Unicode 字符需要两个 char（代理对）
String emoji = "😀";
System.out.println(emoji.length());          // 2（两个 char）
System.out.println(emoji.codePointCount(0, emoji.length())); // 1（一个码点）
```

## 包装类与自动装箱

- 每个基本类型都有对应的包装类：`Integer`, `Long`, `Double`, `Boolean` 等
- **自动装箱**：基本类型自动转为包装类 `Integer i = 10;`（编译器转为 `Integer.valueOf(10)`）
- **自动拆箱**：包装类自动转为基本类型 `int n = i;`（编译器转为 `i.intValue()`）

### 缓存池机制（Integer Cache）

```java
Integer a = 127;
Integer b = 127;
System.out.println(a == b);   // true（缓存池）

Integer c = 128;
Integer d = 128;
System.out.println(c == d);   // false（新对象）
System.out.println(c.equals(d)); // true（值比较）
```

**各包装类的缓存范围：**

| 包装类 | 缓存范围 | 是否可配置 |
|--------|----------|-----------|
| Byte | -128 ~ 127 | 否 |
| Short | -128 ~ 127 | 否 |
| Integer | -128 ~ 127（默认） | 可通过 `-XX:AutoBoxCacheMax` 调整上限 |
| Long | -128 ~ 127 | 否 |
| Character | 0 ~ 127 | 否 |
| Boolean | TRUE / FALSE | 否（只有两个实例） |
| Float | 无缓存 | - |
| Double | 无缓存 | - |

**缓存池源码解析（Integer.valueOf）：**
```java
public static Integer valueOf(int i) {
    // IntegerCache.low = -128, IntegerCache.high 默认 127
    if (i >= IntegerCache.low && i <= IntegerCache.high)
        return IntegerCache.cache[i + (-IntegerCache.low)];
    return new Integer(i);
}
```

**为什么设计缓存池？**
- 小整数使用频率极高（循环计数、数组索引等），缓存减少对象创建
- 节省堆内存，减少 GC 压力
- 享元模式（Flyweight Pattern）的典型应用

### 自动装箱/拆箱的坑

```java
// 坑1：三目运算符拆箱导致 NPE
Integer a = null;
int b = (true) ? a : 0; // NPE！a 被拆箱为 int

// 坑2：包装类比较必须用 equals
Integer x = 200;
Integer y = 200;
System.out.println(x == y);      // false（超出缓存范围）
System.out.println(x.equals(y)); // true

// 坑3：循环中频繁装箱，性能差
Long sum = 0L;
for (long i = 0; i < Integer.MAX_VALUE; i++) {
    sum += i; // 每次循环都会创建新 Long 对象
}
// 改为 long sum = 0L; 快了数倍

// 坑4：Integer 和 int 混合比较
Integer m = new Integer(100);
int n = 100;
System.out.println(m == n); // true（Integer 拆箱为 int 比较值）
```

## String 深度解析

### 不可变性原理

```java
// String 类的核心定义（Java 8）
public final class String implements java.io.Serializable, Comparable<String>, CharSequence {
    private final char value[]; // final 数组引用不可变，但数组内容理论上可变
    private int hash;           // 缓存的 hash 值
}

// Java 9+ 改为 byte[] + encoding 标记（Compact Strings）
// Latin-1 字符只占 1 字节，节省约 40% 内存
private final byte[] value;
private final byte coder; // LATIN1 = 0, UTF16 = 1
```

**String 不可变的好处：**
1. **线程安全**：不可变对象天然线程安全，无需同步
2. **缓存 hashCode**：hash 值只需计算一次，HashMap 中的 key 用 String 最高效
3. **字符串常量池**：相同字面量可以共享内存
4. **安全性**：网络连接、文件路径、数据库URL等用 String，防止被篡改
5. **类加载安全**：类名以 String 存储，不可变保证类加载机制安全

### 字符串常量池

```java
String s1 = "hello";        // 常量池中创建
String s2 = "hello";        // 指向常量池中同一个对象
String s3 = new String("hello"); // 堆中创建新对象，同时常量池也有一份
String s4 = new String("hello"); // 又在堆中创建新对象

System.out.println(s1 == s2);  // true （同一常量池对象）
System.out.println(s1 == s3);  // false（不同对象）
System.out.println(s3 == s4);  // false（不同堆对象）
System.out.println(s1.equals(s3)); // true（值相同）

// intern() 方法：返回常量池中的引用
System.out.println(s3.intern() == s1); // true

// 字符串拼接
String s5 = "hel" + "lo";    // 编译期优化为 "hello"，同 s1
System.out.println(s1 == s5); // true

String prefix = "hel";
String s6 = prefix + "lo";   // 运行时拼接，通过 StringBuilder 实现
System.out.println(s1 == s6); // false

// Java 9+ 字符串拼接优化：invokedynamic（JEP 280）
// 编译器不再生成 StringBuilder，而是用 StringConcatFactory 动态生成拼接策略
```

**`new String("abc")` 创建了几个对象？**
- 如果常量池中没有 "abc"：创建 2 个对象（常量池 1 个 + 堆 1 个）
- 如果常量池中已有 "abc"：创建 1 个对象（堆 1 个）

### String / StringBuilder / StringBuffer 对比

| 特性 | String | StringBuilder | StringBuffer |
|------|--------|---------------|-------------|
| 可变性 | 不可变 | 可变 | 可变 |
| 线程安全 | 是（不可变） | 否 | 是（synchronized） |
| 性能 | 频繁修改最差 | 最好 | 比 StringBuilder 稍慢 |
| 使用场景 | 少量字符串操作 | 单线程大量拼接 | 多线程大量拼接 |

```java
// StringBuilder 内部扩容机制
// 默认容量 16，扩容为 (旧容量 * 2) + 2
StringBuilder sb = new StringBuilder();       // 容量 16
StringBuilder sb2 = new StringBuilder("abc"); // 容量 16 + 3 = 19

// 大量拼接时指定初始容量，避免频繁扩容
StringBuilder sb3 = new StringBuilder(1024);
for (int i = 0; i < 1000; i++) {
    sb3.append("x");
}
```

## 类型转换

### 隐式转换（自动类型提升）
```
byte → short → int → long → float → double
          ↑
        char
```

```java
// 自动提升
byte b = 10;
int i = b;     // 自动转换
long l = i;    // 自动转换
float f = l;   // 自动转换（可能丢失精度！long 64位 → float 32位）

// 运算时自动提升
byte a = 1;
byte c = 2;
// byte d = a + c; // 编译错误！运算结果自动提升为 int
int d = a + c;     // 正确
byte e = (byte)(a + c); // 需要强制转换
```

### 显式转换（强制类型转换）
```java
// 大转小可能丢失数据
int i = 300;
byte b = (byte) i;      // 44（溢出截断：300 - 256 = 44）

double d = 3.99;
int n = (int) d;         // 3（截断小数部分，不是四舍五入）
int m = (int) Math.round(d); // 4（先四舍五入再转换）

// 隐式转换的精度陷阱
long bigLong = 123456789012345L;
float f = bigLong; // 123456790000000.0（精度丢失！）
// float 有效位数约 7 位，long 可达 19 位
```

## 运算符详解

### == 与 equals() 的本质区别

```java
// == 对于基本类型比较值，对于引用类型比较地址
int a = 10, b = 10;
System.out.println(a == b); // true（值相等）

String s1 = new String("hello");
String s2 = new String("hello");
System.out.println(s1 == s2);      // false（不同对象）
System.out.println(s1.equals(s2)); // true（内容相等）

// equals() 默认实现就是 ==
// public boolean equals(Object obj) { return (this == obj); }
// String 等类重写了 equals，比较内容
```

### 位运算实战

```java
// 判断奇偶（比取模快）
boolean isOdd = (n & 1) == 1;

// 乘以/除以 2 的幂
int doubled = n << 1;    // n * 2
int halved  = n >> 1;    // n / 2

// 交换两个变量（不用临时变量）
a = a ^ b;
b = a ^ b;
a = a ^ b;

// HashMap 中的高位异或（减少哈希碰撞）
static final int hash(Object key) {
    int h;
    return (key == null) ? 0 : (h = key.hashCode()) ^ (h >>> 16);
}

// 判断一个数是否是 2 的幂
boolean isPowerOf2 = (n > 0) && (n & (n - 1)) == 0;
```

### instanceof 与类型判断

```java
// 传统用法
if (obj instanceof String) {
    String s = (String) obj; // 需要手动强转
}

// Java 16+ Pattern Matching for instanceof
if (obj instanceof String s) {
    System.out.println(s.length()); // 直接使用，无需强转
}

// instanceof 的特殊情况
null instanceof Object  // false（null 不是任何类的实例）
```

> **交叉引用**：Pattern Matching 更多内容参见 [Java新特性与常用API](./07_Java新特性与常用API.md)

## final、finally、finalize() 的区别

```java
// final：修饰符
final int MAX = 100;           // 常量，不可修改
final class Immutable {}       // 不可继承
final void doSomething() {}    // 不可重写
final List<String> list = new ArrayList<>(); // 引用不可变，但集合内容可变！
list.add("hello"); // 合法

// finally：异常处理，总会执行（即使有 return）
try {
    return 1;
} finally {
    System.out.println("一定会执行"); // 打印
    // 特例：如果这里也有 return，会覆盖 try 中的 return（不推荐）
}
// 唯一不执行的情况：System.exit() 或 JVM 崩溃

// finalize()：对象被GC回收前调用（Java 9 已标记 @Deprecated）
// 不推荐使用：执行时机不确定，可能导致对象复活，影响GC性能
// 替代方案：try-with-resources 或 Cleaner（Java 9+）
```

## 踩坑指南

### 1. 数值溢出问题
```java
int a = Integer.MAX_VALUE;
int b = a + 1;         // -2147483648（溢出为最小值）
long c = a + 1;        // 还是 -2147483648！因为右边先按 int 运算
long d = (long) a + 1; // 2147483648L（正确：先转 long 再运算）
long e = a + 1L;       // 2147483648L（正确：1L 触发自动提升）

// Java 8+ 安全的数学运算
int safe = Math.addExact(a, 1); // 抛出 ArithmeticException
```

### 2. switch 穿透
```java
// 忘记 break 导致穿透
int day = 2;
switch (day) {
    case 1: System.out.println("Mon");
    case 2: System.out.println("Tue"); // 打印
    case 3: System.out.println("Wed"); // 也打印！穿透了
}

// Java 14+ switch 表达式（无穿透问题）
String name = switch (day) {
    case 1 -> "Monday";
    case 2 -> "Tuesday";
    default -> "Unknown";
};
```

### 3. 数组与集合转换
```java
// Arrays.asList 返回固定大小列表，不支持 add/remove
List<String> list = Arrays.asList("a", "b", "c");
list.add("d"); // UnsupportedOperationException！

// 正确做法
List<String> mutableList = new ArrayList<>(Arrays.asList("a", "b", "c"));
// 或 Java 9+
List<String> immutable = List.of("a", "b", "c"); // 不可变
List<String> mutable = new ArrayList<>(List.of("a", "b", "c")); // 可变

// 基本类型数组不能直接转 List
int[] arr = {1, 2, 3};
List<int[]> wrong = Arrays.asList(arr); // 整个数组作为一个元素！
List<Integer> right = Arrays.stream(arr).boxed().collect(Collectors.toList());
```

### 4. equals 陷阱
```java
// NPE 风险
String s = null;
s.equals("hello"); // NPE！
"hello".equals(s); // false，安全写法（常量在前）
Objects.equals(s, "hello"); // false，最安全写法（Java 7+）
```

## 最佳实践

1. **基本类型优先**：性能优于包装类，避免不必要的装箱拆箱
2. **包装类比较用 equals()**：永远不要用 `==` 比较包装类
3. **金融计算用 BigDecimal**：构造器传 String，不要传 double
4. **字符串拼接**：少量用 `+`，循环内用 `StringBuilder`，多线程用 `StringBuffer`
5. **常量定义**：用 `static final`，命名全大写+下划线
6. **避免魔法数字**：定义为有意义的常量名
7. **null 安全**：使用 `Objects.equals()`、Optional、字符串常量在前等技巧

## 面试高频问题及详细解答

### Q1：== 和 equals() 的区别？
**答**：`==` 对基本类型比较值，对引用类型比较内存地址（是否同一个对象）。`equals()` 是 Object 的方法，默认实现同 `==`，但 String、Integer 等类重写了它来比较内容。使用时，基本类型用 `==`，对象比较用 `equals()`。

### Q2：String 为什么是不可变的？有什么好处？
**答**：String 类被 `final` 修饰不可继承，内部 `char[]`（Java 9+ 为 `byte[]`）被 `private final` 修饰且没有暴露修改方法。好处：(1) 线程安全 (2) 可以缓存 hashCode (3) 字符串常量池可以复用 (4) 安全性（防止篡改URL、文件路径等）(5) 类加载机制安全。

### Q3：Integer 缓存池的范围？为什么设计缓存池？
**答**：默认 -128 ~ 127，可通过 JVM 参数 `-XX:AutoBoxCacheMax` 调整上限。设计缓存池是因为小整数使用极其频繁（循环变量、数组索引等），缓存后可减少对象创建、节省内存、降低 GC 压力。这是享元模式（Flyweight Pattern）的应用。

### Q4：String s = new String("abc") 创建了几个对象？
**答**：如果常量池中没有 "abc"，则创建 2 个：一个在常量池，一个在堆。如果常量池中已有 "abc"，则只在堆创建 1 个。关键是理解字面量 "abc" 会先在编译期被放入 class 文件的常量池，类加载时进入运行时常量池。

### Q5：final, finally, finalize() 的区别？
**答**：`final` 是修饰符，修饰类不可继承、方法不可重写、变量不可重新赋值。`finally` 是异常处理块，无论是否异常都会执行。`finalize()` 是 Object 的方法，GC 前调用，Java 9 已废弃，推荐用 try-with-resources 或 Cleaner 替代。

### Q6：Java 中的参数传递是值传递还是引用传递？
**答**：**Java 只有值传递**。基本类型传递值的副本，引用类型传递引用的副本（地址值的副本）。所以方法内重新赋值不影响外部变量，但可以通过引用修改对象内部状态。

```java
void change(int num, StringBuilder sb) {
    num = 100;               // 不影响外部
    sb.append(" world");     // 影响外部（通过引用修改对象内容）
    sb = new StringBuilder("new"); // 不影响外部（只是改了本地副本的指向）
}
```

### Q7：String.intern() 的作用和原理？
**答**：`intern()` 检查字符串常量池中是否存在等值字符串，有则返回池中引用，没有则将当前字符串（Java 7+ 是引用）放入池中并返回。JDK 6 中常量池在永久代，intern 会复制字符串到永久代；JDK 7+ 常量池在堆中，intern 只复制引用。滥用 intern 可能导致常量池过大，GC 压力增大。

### Q8：为什么浮点数不能精确表示？金融计算怎么处理？
**答**：浮点数采用 IEEE 754 标准，用二进制科学计数法表示，很多十进制小数（如 0.1）无法精确表示。金融计算应使用 `BigDecimal`，构造时传 String 或用 `BigDecimal.valueOf()`，避免 `new BigDecimal(double)`。比较用 `compareTo()` 而非 `equals()`（因为 `new BigDecimal("1.0").equals(new BigDecimal("1"))` 返回 false）。

### Q9：Java 有哪些隐式类型转换？可能带来什么问题？
**答**：小类型到大类型自动转换（byte→short→int→long→float→double），char→int 也可以。问题：(1) long→float 可能丢失精度（long 64位有效，float 只有约 7 位十进制精度）(2) 运算时自动提升为 int，byte/short 运算后不能直接赋回 byte/short。

### Q10：如何理解 Java 中的编码？char 和 String 的编码关系？
**答**：Java 内部使用 UTF-16 编码，一个 char 是 2 字节。但 UTF-16 是变长编码，BMP（基本多语言平面）字符用 1 个 char，增补平面字符（如 emoji）需要 2 个 char（代理对）。所以 `String.length()` 返回的是 char 数量而非字符数，要获取真实字符数用 `codePointCount()`。Java 9+ String 内部用 Compact Strings 优化，Latin-1 字符用 byte[] 存储，节省内存。

> **交叉引用**：BigDecimal 的使用参见 [Java新特性与常用API](./07_Java新特性与常用API.md) 中常用工具类部分
