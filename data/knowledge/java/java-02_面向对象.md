# Java 面向对象

## 三大特性

### 封装

- 使用 private 隐藏内部实现，通过 getter/setter 暴露接口
- 访问修饰符：`private` < `默认(包级)` < `protected` < `public`

| 修饰符 | 当前类 | 同包 | 子类 | 其他包 |
|--------|--------|------|------|--------|
| private | Y | N | N | N |
| 默认 | Y | Y | N | N |
| protected | Y | Y | Y | N |
| public | Y | Y | Y | Y |

**深入理解 protected：**
```java
// protected 的访问范围：同包 + 不同包的子类（通过继承关系访问）
package com.parent;
public class Parent {
    protected void method() { }
}

package com.child;
public class Child extends Parent {
    public void test() {
        method();       // 可以：继承关系
        this.method();  // 可以：继承关系
        new Parent().method(); // 不可以！不同包中只能通过子类引用访问
        new Child().method();  // 可以：通过子类引用
    }
}
```

**封装的最佳实践——JavaBean 规范：**
```java
public class User {
    private String name;
    private int age;

    // 不要暴露可变对象引用
    private Date birthday;

    // 错误：直接返回可变对象引用
    public Date getBirthday() { return birthday; }

    // 正确：返回防御性拷贝
    public Date getBirthday() { return new Date(birthday.getTime()); }

    // 正确：设置时也做防御性拷贝
    public void setBirthday(Date birthday) {
        this.birthday = new Date(birthday.getTime());
    }
}
```

### 继承

- Java 只支持**单继承**（一个类只能 extends 一个父类）
- 可以实现多个接口（implements）
- `super` 关键字访问父类成员和构造方法
- 子类构造方法第一行必须调用 `super()`（显式或隐式）

**继承中的初始化顺序（重要！）：**
```java
class Parent {
    static { System.out.println("1. Parent 静态块"); }
    { System.out.println("3. Parent 构造块"); }
    Parent() { System.out.println("4. Parent 构造方法"); }
}

class Child extends Parent {
    static { System.out.println("2. Child 静态块"); }
    { System.out.println("5. Child 构造块"); }
    Child() { System.out.println("6. Child 构造方法"); }
}

// new Child() 输出顺序：
// 1. Parent 静态块（只执行一次）
// 2. Child 静态块（只执行一次）
// 3. Parent 构造块
// 4. Parent 构造方法
// 5. Child 构造块
// 6. Child 构造方法

// 规则：静态先于非静态，父类先于子类，静态块只在类加载时执行一次
```

**继承中的隐藏与覆盖：**
```java
class Parent {
    public static void staticMethod() { System.out.println("Parent static"); }
    public int field = 1;
}

class Child extends Parent {
    public static void staticMethod() { System.out.println("Child static"); }
    public int field = 2;
}

Parent p = new Child();
p.staticMethod();           // "Parent static"（静态方法看引用类型，这是隐藏而非重写）
System.out.println(p.field); // 1（字段没有多态，看引用类型）
```

### 多态

- **编译时多态**：方法重载（Overload）—— 同名不同参数
- **运行时多态**：方法重写（Override）—— 子类重写父类方法
- 父类引用指向子类对象：`Animal a = new Dog();`
- 运行时调用实际类型的方法（动态绑定）

```java
// 重载 vs 重写
class Parent {
    // 重载：同一个类中，方法名相同，参数不同
    public void print(int n) { }
    public void print(String s) { }

    // 被子类重写的方法
    public void speak() {
        System.out.println("Parent");
    }
}

class Child extends Parent {
    @Override  // 重写：子类中，方法签名完全相同
    public void speak() {
        System.out.println("Child");
    }
}
```

**重载的规则：**
1. 方法名相同
2. 参数列表不同（个数、类型、顺序）
3. 返回值类型不作为区分标准
4. 访问权限和异常不作为区分标准

**重写的规则（"两同两小一大"）：**
1. **两同**：方法名和参数列表相同
2. **两小**：返回值类型 <= 父类（协变返回）、抛出异常 <= 父类
3. **一大**：访问权限 >= 父类

**多态的底层原理——虚方法表（vtable）：**
```
// JVM 在类加载时为每个类创建虚方法表
// 虚方法表存储了该类每个可被动态派发的方法的实际入口地址

Parent 虚方法表:
  speak() -> Parent.speak()
  toString() -> Object.toString()

Child 虚方法表:
  speak() -> Child.speak()    // 重写后指向子类方法
  toString() -> Object.toString()

// 调用时通过虚方法表查找实际方法，无需遍历继承链
// 这就是为什么多态调用的性能开销很小
```

**多态的经典面试题：**
```java
class A {
    public void method() { System.out.println("A"); }
}
class B extends A {
    public void method() { System.out.println("B"); }
}
class C extends B {
    public void method() { System.out.println("C"); }
}

A obj = new C();
obj.method(); // 输出 "C"（运行时看实际类型）

// 向下转型
A a = new B();
B b = (B) a;   // 成功：实际类型是 B
C c = (C) a;   // ClassCastException！实际类型是 B，不是 C

// 安全的向下转型
if (a instanceof B) {
    B safe = (B) a;
}
// Java 16+
if (a instanceof B bb) {
    bb.method(); // 直接使用，无需强转
}
```

## 抽象类与接口

| 特性 | 抽象类 | 接口 |
|------|--------|------|
| 关键字 | abstract class | interface |
| 构造方法 | 有 | 无 |
| 成员变量 | 任意 | 默认 public static final |
| 方法 | 可以有实现 | Java 8+ 可以有 default/static 方法 |
| 继承 | 单继承 | 多实现 |
| 设计含义 | "is-a" 关系 | "can-do" 能力 |
| 静态方法 | 可以 | Java 8+ 可以 |
| private 方法 | 可以 | Java 9+ 可以 |

```java
// 抽象类：有共同属性和行为的基类
abstract class Shape {
    String color;
    abstract double area();

    // 抽象类可以有构造方法（供子类调用）
    Shape(String color) {
        this.color = color;
    }

    // 抽象类可以有非抽象方法
    void printInfo() {
        System.out.println("颜色: " + color + ", 面积: " + area());
    }
}

// 接口：定义能力/契约
interface Drawable {
    void draw();
    default void fill() {  // Java 8 默认方法
        System.out.println("Filling...");
    }
    static Drawable create() { // Java 8 静态方法
        return () -> System.out.println("Default drawing");
    }
    private void helper() { // Java 9 私有方法（供 default 方法调用）
        System.out.println("Helper");
    }
}

// 接口的多继承冲突解决
interface A { default void hello() { System.out.println("A"); } }
interface B { default void hello() { System.out.println("B"); } }

class C implements A, B {
    @Override
    public void hello() {
        A.super.hello(); // 必须显式选择调用哪个
    }
}

class Circle extends Shape implements Drawable {
    double radius;

    Circle(String color, double radius) {
        super(color);
        this.radius = radius;
    }

    @Override
    double area() { return Math.PI * radius * radius; }

    @Override
    public void draw() { System.out.println("Drawing circle"); }
}
```

**什么时候用抽象类？什么时候用接口？**
- **抽象类**：描述 "是什么"（is-a），有共同属性和部分实现，如 `AbstractList`
- **接口**：描述 "能做什么"（can-do），定义行为契约，如 `Serializable`、`Comparable`
- 如果需要共享状态（字段）→ 抽象类
- 如果需要多继承 → 接口
- Java 8 之后接口可以有默认方法实现，二者界限变得模糊

## Object 类的核心方法

- `equals()`：判断对象是否相等，重写时必须同时重写 `hashCode()`
- `hashCode()`：返回哈希值，相等的对象必须有相同的 hashCode
- `toString()`：返回对象的字符串表示
- `clone()`：克隆对象（浅拷贝），需要实现 Cloneable 接口
- `getClass()`：返回运行时类信息
- `wait()/notify()/notifyAll()`：线程间通信
- `finalize()`：GC 前调用（已废弃，Java 9+）

### 正确重写 equals 方法

```java
public class Person {
    private String name;
    private int age;

    @Override
    public boolean equals(Object o) {
        // 1. 自反性：自己和自己比较
        if (this == o) return true;
        // 2. 判空 + 类型检查
        if (o == null || getClass() != o.getClass()) return false;
        // 3. 强制转换后逐字段比较
        Person person = (Person) o;
        return age == person.age && Objects.equals(name, person.name);
    }

    @Override
    public int hashCode() {
        return Objects.hash(name, age);
    }

    // equals 必须满足的五个原则：
    // 1. 自反性：x.equals(x) == true
    // 2. 对称性：x.equals(y) == y.equals(x)
    // 3. 传递性：x.equals(y) && y.equals(z) → x.equals(z)
    // 4. 一致性：多次调用结果一致
    // 5. 非空性：x.equals(null) == false
}
```

## equals 和 hashCode 的契约

1. `equals` 相等的对象，`hashCode` **必须**相等
2. `hashCode` 相等的对象，`equals` **不一定**相等（哈希冲突）
3. 重写 `equals` **必须**重写 `hashCode`

**为什么必须遵守这个契约？**

```java
// 不重写 hashCode 会导致 HashMap 出问题
class Key {
    int id;
    Key(int id) { this.id = id; }

    @Override
    public boolean equals(Object o) {
        return o instanceof Key && ((Key) o).id == this.id;
    }
    // 故意不重写 hashCode
}

Map<Key, String> map = new HashMap<>();
map.put(new Key(1), "value");
map.get(new Key(1)); // null！因为两个 Key(1) 的 hashCode 不同，可能落在不同桶中
```

## 深拷贝与浅拷贝

```java
// 浅拷贝：只复制引用，共享内部对象
class Person implements Cloneable {
    String name;
    Address address; // 引用类型字段

    @Override
    protected Person clone() throws CloneNotSupportedException {
        return (Person) super.clone(); // 浅拷贝：address 引用不变
    }
}

// 深拷贝方式1：手动递归复制
@Override
protected Person clone() throws CloneNotSupportedException {
    Person copy = (Person) super.clone();
    copy.address = address.clone(); // 内部对象也要克隆
    return copy;
}

// 深拷贝方式2：序列化
public Person deepClone() {
    try {
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        ObjectOutputStream oos = new ObjectOutputStream(bos);
        oos.writeObject(this);

        ByteArrayInputStream bis = new ByteArrayInputStream(bos.toByteArray());
        ObjectInputStream ois = new ObjectInputStream(bis);
        return (Person) ois.readObject();
    } catch (Exception e) {
        throw new RuntimeException(e);
    }
}

// 深拷贝方式3：JSON 序列化/反序列化（最简单但性能差）
Person copy = JSON.parseObject(JSON.toJSONString(original), Person.class);
```

## 内部类详解

```java
public class Outer {
    private int x = 10;

    // 1. 成员内部类：持有外部类引用，可以访问外部类所有成员
    class Inner {
        void method() {
            System.out.println(x); // 可以访问外部类 private 成员
            System.out.println(Outer.this.x); // 显式引用外部类
        }
    }
    // 创建：Outer.Inner inner = new Outer().new Inner();
    // 缺点：持有外部类引用，可能导致内存泄漏

    // 2. 静态内部类：不持有外部类引用，最常用
    static class StaticInner {
        void method() {
            // 不能访问外部类非静态成员
            // System.out.println(x); // 编译错误
        }
    }
    // 创建：Outer.StaticInner si = new Outer.StaticInner();
    // 推荐：HashMap.Node, Builder 模式等常用

    // 3. 局部内部类：定义在方法中
    void test() {
        final int localVar = 20; // 必须 effectively final
        class Local {
            void method() {
                System.out.println(localVar);
            }
        }
    }

    // 4. 匿名内部类：没有名字的类
    Runnable task = new Runnable() {
        @Override
        public void run() {
            System.out.println("Anonymous inner class");
        }
    };
    // Java 8+ 用 Lambda 替代：Runnable task = () -> System.out.println("Lambda");
}
```

**静态内部类 vs 成员内部类：**
| 特性 | 成员内部类 | 静态内部类 |
|------|-----------|-----------|
| 外部类引用 | 持有（隐式） | 不持有 |
| 访问外部类成员 | 可以（包括私有） | 只能访问静态成员 |
| 创建方式 | `outer.new Inner()` | `new Outer.StaticInner()` |
| 内存泄漏风险 | 有（持有外部类引用） | 无 |
| 使用建议 | 尽量少用 | 优先使用 |

## 枚举（enum）

```java
// 枚举本质是 final class，继承自 java.lang.Enum
public enum Season {
    SPRING("春天", 1), SUMMER("夏天", 2),
    AUTUMN("秋天", 3), WINTER("冬天", 4);

    private final String name;
    private final int code;

    Season(String name, int code) { // 构造方法默认 private
        this.name = name;
        this.code = code;
    }

    public String getName() { return name; }
    public int getCode() { return code; }

    // 可以实现接口
    // 可以有抽象方法（每个枚举常量提供实现）
}

// 枚举的最佳单例模式
public enum Singleton {
    INSTANCE;

    private SomeResource resource;

    Singleton() {
        resource = new SomeResource();
    }

    public void doSomething() { /* ... */ }
}
// 枚举单例优势：线程安全、防止反射攻击、防止序列化破坏
```

> **交叉引用**：Sealed Classes（密封类）限制继承参见 [Java新特性](./07_Java新特性与常用API.md)

## 泛型基础

```java
// 泛型类
public class Box<T> {
    private T value;
    public void set(T value) { this.value = value; }
    public T get() { return value; }
}

// 泛型方法
public <T> T getFirst(List<T> list) {
    return list.get(0);
}

// 泛型通配符
List<?> anyList;                       // 无界通配符
List<? extends Number> numList;        // 上界：Number 及其子类
List<? super Integer> intList;         // 下界：Integer 及其父类

// PECS 原则：Producer Extends, Consumer Super
// 生产者（读取）用 extends，消费者（写入）用 super
public void copy(List<? extends T> src, List<? super T> dest) {
    for (T item : src) {   // 从 src 读取（extends）
        dest.add(item);    // 向 dest 写入（super）
    }
}

// 类型擦除：泛型信息在编译后被擦除
List<String> stringList = new ArrayList<>();
List<Integer> intList2 = new ArrayList<>();
System.out.println(stringList.getClass() == intList2.getClass()); // true（都是 ArrayList.class）
```

## 反射

```java
// 获取 Class 对象的三种方式
Class<?> clazz1 = String.class;                     // 类名.class
Class<?> clazz2 = "hello".getClass();               // 对象.getClass()
Class<?> clazz3 = Class.forName("java.lang.String"); // 全限定名

// 创建实例
Object obj = clazz.getDeclaredConstructor().newInstance();

// 获取和调用方法
Method method = clazz.getDeclaredMethod("privateMethod", String.class);
method.setAccessible(true); // 突破 private 限制
Object result = method.invoke(obj, "参数");

// 获取和设置字段
Field field = clazz.getDeclaredField("name");
field.setAccessible(true);
field.set(obj, "新值");

// 反射的开销：
// 1. 方法查找、安全检查、参数装箱等
// 2. 无法进行 JIT 内联优化
// 3. 性能约比直接调用慢 10-100 倍
// 优化：缓存 Method/Field 对象，使用 MethodHandle（Java 7+）
```

## 踩坑指南

### 1. 构造方法中调用可被重写的方法
```java
class Parent {
    Parent() {
        init(); // 危险！如果子类重写了 init()
    }
    void init() { System.out.println("Parent init"); }
}

class Child extends Parent {
    private int value;
    Child(int value) {
        super(); // 此时 init() 调用的是 Child.init()，但 value 还未赋值！
        this.value = value;
    }
    @Override
    void init() {
        System.out.println("Child init: " + value); // value = 0（默认值）
    }
}
// 规则：构造方法中不要调用可被重写的方法
```

### 2. equals 的对称性问题
```java
class Animal {
    String name;
    @Override
    public boolean equals(Object o) {
        if (o instanceof Animal a) return name.equals(a.name);
        return false;
    }
}

class Dog extends Animal {
    String breed;
    @Override
    public boolean equals(Object o) {
        if (o instanceof Dog d) return super.equals(d) && breed.equals(d.breed);
        return false;
    }
}

Animal a = new Animal(); a.name = "Rex";
Dog d = new Dog(); d.name = "Rex"; d.breed = "Husky";
a.equals(d); // true（Animal 的 equals）
d.equals(a); // false（Dog 的 equals 要求 instanceof Dog）
// 违反了 equals 的对称性！推荐用 getClass() 而非 instanceof
```

### 3. 接口默认方法的菱形继承
```java
interface A { default void hello() { System.out.println("A"); } }
interface B extends A { default void hello() { System.out.println("B"); } }
interface C extends A { }

class D implements B, C {
    // B.hello() 比 A.hello() 更"具体"，所以使用 B 的
    // 如果 B 和 C 都重写了 hello()，则必须在 D 中显式选择
}
```

## 最佳实践

1. **组合优于继承**：继承破坏封装，优先使用组合 + 接口实现代码复用
2. **面向接口编程**：依赖抽象而非具体实现，如 `List<String> list = new ArrayList<>()`
3. **不可变对象**：尽量设计不可变类（所有字段 final、不提供 setter、防御性拷贝）
4. **内部类用静态的**：除非确实需要访问外部类实例成员，否则用 static 内部类
5. **@Override 注解**：重写方法必须加 @Override，防止手误
6. **Lombok 简化代码**：@Data、@Builder、@AllArgsConstructor 减少样板代码
7. **枚举代替常量**：类型安全，可以有方法和字段

## 面试高频问题及详细解答

### Q1：重载和重写的区别？
**答**：**重载(Overload)**：同一个类中方法名相同、参数列表不同（个数、类型、顺序），与返回值无关，是编译时多态。**重写(Override)**：子类重写父类方法，方法名和参数完全相同，遵循"两同两小一大"规则（方法名参数同、返回值和异常范围小于等于父类、访问权限大于等于父类），是运行时多态。

### Q2：抽象类和接口的区别？什么时候用哪个？
**答**：(1) 抽象类可以有构造方法、成员变量和方法实现；接口只能有常量和 Java 8+ 的 default/static 方法。(2) 一个类只能继承一个抽象类，但可以实现多个接口。(3) 抽象类表示 "is-a"，接口表示 "can-do"。选择：如果需要共享状态或部分实现用抽象类；如果需要多继承、定义行为契约用接口。Java 8+ 二者界限模糊，优先用接口。

### Q3：为什么重写 equals 必须重写 hashCode？
**答**：因为 HashMap、HashSet 等集合先用 hashCode 确定桶位置，再用 equals 判断是否相同。如果 equals 返回 true 但 hashCode 不同，两个"相等"的对象会被放到不同桶中，导致 HashMap 无法正确工作（put 两次、get 找不到）。

### Q4：Java 为什么不支持多继承？
**答**：避免菱形继承问题（Diamond Problem）。如果 A 有方法 m()，B 和 C 都继承 A 并重写 m()，D 同时继承 B 和 C，调用 m() 时就不知道用哪个版本。Java 通过接口的多实现来提供类似能力，Java 8 的接口 default 方法遇到冲突时必须显式选择。

### Q5：多态的实现原理？
**答**：JVM 通过**虚方法表（vtable）**实现多态。类加载时为每个类创建虚方法表，其中每个方法指向实际的方法实现。子类的虚方法表复制父类的，并将重写的方法指向自己的实现。调用时通过对象头的类型指针找到类信息，再从虚方法表中查找实际方法地址，从而实现运行时动态派发。

### Q6：深拷贝和浅拷贝的区别？如何实现深拷贝？
**答**：**浅拷贝**只复制对象本身和基本类型字段，引用类型字段仍指向同一对象。**深拷贝**递归复制所有引用类型字段。实现方式：(1) 手动 clone 并递归复制内部对象 (2) 序列化/反序列化（Java序列化、JSON等）(3) 拷贝构造方法。实际推荐方式 2 或 3，clone 方法设计有缺陷。

### Q7：静态方法能被重写吗？
**答**：**不能**。静态方法属于类而非实例，不参与多态。子类中定义同名静态方法叫做"隐藏(hiding)"而非重写。调用时看引用类型，而非实际对象类型。这也是为什么 `@Override` 注解不能用于静态方法。

### Q8：Java 的泛型是怎么实现的？有什么限制？
**答**：Java 使用**类型擦除**实现泛型，编译后泛型信息被擦除为原始类型（如 `List<String>` 变为 `List`），通过编译器插入的强制转换保证类型安全。限制：(1) 不能 `new T()` (2) 不能 `new T[]` (3) 不能用基本类型作泛型参数 (4) 运行时无法获取泛型类型（可通过匿名子类、TypeToken 等技巧获取）。

### Q9：反射的应用场景和优缺点？
**答**：**场景**：框架（Spring IOC、MyBatis ORM）、动态代理、注解处理、序列化/反序列化。**优点**：运行时动态操作类，灵活性高。**缺点**：(1) 性能差（无法JIT优化，约慢10-100倍）(2) 破坏封装（可访问 private）(3) 安全风险 (4) 编译期无法检查类型。优化建议：缓存反射对象、使用 MethodHandle。

### Q10：Object 类有哪些常用方法？分别什么作用？
**答**：(1) `equals()` 判断相等 (2) `hashCode()` 返回哈希值 (3) `toString()` 返回字符串表示 (4) `clone()` 克隆对象（需实现 Cloneable）(5) `getClass()` 获取运行时类型 (6) `wait()/notify()/notifyAll()` 线程间通信（必须在 synchronized 块内调用）(7) `finalize()` GC前回调（已废弃）。

> **交叉引用**：equals/hashCode 在集合框架中的作用参见 [集合框架](./03_集合框架.md)；wait/notify 在并发中的使用参见 [多线程与并发](./04_多线程与并发.md)
