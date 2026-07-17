# Docker 与 Linux

## 一、Linux 核心命令

### 1.1 文件与目录操作

```bash
# 基础操作
ls -la                  # 列出所有文件（含隐藏文件），显示详细信息
ls -lhrt                # 按时间排序，最新的在最后
cd /path/to/dir         # 切换目录
pwd                     # 显示当前目录
mkdir -p a/b/c          # 递归创建目录
cp -r src/ dest/        # 递归复制目录
mv old new              # 移动/重命名
rm -rf dir/             # 递归强制删除（慎用！）
ln -s target link       # 创建软链接
ln target link          # 创建硬链接

# 文件查找
find / -name "*.log"                 # 按名称查找
find / -name "*.log" -mtime +7       # 查找7天前修改的日志
find / -size +100M                   # 查找大于100MB的文件
find / -type f -name "*.tmp" -delete # 查找并删除临时文件
which java                           # 查找命令位置
whereis nginx                        # 查找二进制、源码、手册位置
locate filename                      # 从索引数据库快速查找（需 updatedb）

# 文件权限
chmod 755 file          # rwxr-xr-x
chmod u+x file          # 给所有者加执行权限
chmod -R 644 dir/       # 递归修改权限
chown user:group file   # 修改所有者和组
chown -R user:group dir/ # 递归修改

# 权限数字对应
# r=4, w=2, x=1
# 755 = rwxr-xr-x  → 所有者读写执行，组和其他人读执行
# 644 = rw-r--r--  → 所有者读写，组和其他人只读
# 700 = rwx------  → 仅所有者读写执行

# 压缩解压
tar -czf archive.tar.gz dir/    # 压缩为 .tar.gz
tar -xzf archive.tar.gz         # 解压 .tar.gz
tar -cjf archive.tar.bz2 dir/   # 压缩为 .tar.bz2
zip -r archive.zip dir/          # 压缩为 .zip
unzip archive.zip                # 解压 .zip
```

### 1.2 文本处理三剑客：grep / sed / awk

#### grep — 文本搜索

```bash
grep "pattern" file              # 基本搜索
grep -i "pattern" file           # 忽略大小写
grep -r "pattern" dir/           # 递归搜索目录
grep -n "pattern" file           # 显示行号
grep -c "pattern" file           # 统计匹配行数
grep -v "pattern" file           # 反向匹配（不包含 pattern 的行）
grep -E "regex" file             # 使用扩展正则表达式
grep -A 3 "ERROR" log.txt       # 匹配行后显示3行
grep -B 3 "ERROR" log.txt       # 匹配行前显示3行
grep -C 3 "ERROR" log.txt       # 匹配行前后各3行

# 常用组合
grep -rn "TODO" --include="*.java" .  # 在Java文件中搜索TODO
ps aux | grep java                     # 查找Java进程
grep -E "ERROR|WARN" app.log           # 搜索ERROR或WARN
```

#### sed — 流编辑器

```bash
sed 's/old/new/' file            # 替换每行第一个匹配
sed 's/old/new/g' file           # 替换所有匹配
sed -i 's/old/new/g' file       # 直接修改文件（就地编辑）
sed -i.bak 's/old/new/g' file   # 修改前备份
sed -n '10,20p' file             # 打印第10-20行
sed '3d' file                    # 删除第3行
sed '/pattern/d' file            # 删除匹配行
sed '2a\new line' file           # 在第2行后追加
sed '2i\new line' file           # 在第2行前插入

# 实用示例
sed -i 's/localhost/192.168.1.100/g' config.yml  # 批量替换配置
sed -n '/START/,/END/p' file     # 打印两个标记之间的内容
```

#### awk — 文本分析

```bash
awk '{print $1}' file            # 打印第一列（默认空格分隔）
awk -F: '{print $1}' /etc/passwd # 以冒号分隔，打印用户名
awk '{print $1, $3}' file        # 打印第1、3列
awk '$3 > 100' file              # 第3列大于100的行
awk '{sum += $1} END {print sum}' file  # 求第1列的和
awk 'NR==10' file                # 打印第10行
awk 'NR>=10 && NR<=20' file      # 打印第10-20行
awk '{print NR, $0}' file        # 添加行号

# 实用示例
# 统计每个 IP 的访问次数
awk '{print $1}' access.log | sort | uniq -c | sort -rn | head -10

# 统计每个 HTTP 状态码的次数
awk '{print $9}' access.log | sort | uniq -c | sort -rn

# 计算某列的平均值
awk '{sum+=$3; count++} END {print sum/count}' data.txt
```

#### 其他文本处理

```bash
cat file                         # 查看文件
head -n 20 file                  # 查看前20行
tail -n 20 file                  # 查看后20行
tail -f log.txt                  # 实时追踪日志（最常用）
tail -f log.txt | grep ERROR     # 实时过滤错误日志
wc -l file                       # 统计行数
wc -w file                       # 统计单词数
sort file                        # 排序
sort -n file                     # 数值排序
sort -rn file                    # 数值逆序
uniq                             # 去重（需先排序）
cut -d: -f1 /etc/passwd          # 按分隔符切割取第一列
tr 'a-z' 'A-Z' < file           # 小写转大写
diff file1 file2                 # 比较两个文件的差异
```

### 1.3 进程管理

```bash
# 查看进程
ps aux                           # 查看所有进程
ps -ef                           # 另一种格式
ps aux | grep java               # 查找Java进程
pstree -p                        # 树状显示进程
pidof nginx                      # 查找进程PID

# 杀进程
kill PID                         # 发送 SIGTERM（优雅停止）
kill -9 PID                      # 发送 SIGKILL（强制杀死）
kill -15 PID                     # 发送 SIGTERM（等同于 kill PID）
killall java                     # 杀死所有名为 java 的进程
pkill -f "pattern"               # 按模式杀进程

# 后台运行
nohup command &                  # 后台运行，不受终端关闭影响
nohup java -jar app.jar > /dev/null 2>&1 &  # 丢弃输出
jobs                             # 查看后台任务
fg %1                            # 将后台任务1放到前台
bg %1                            # 将任务1放到后台继续运行
disown -h %1                     # 使后台任务不受终端关闭影响

# 服务管理（systemd）
systemctl start nginx            # 启动服务
systemctl stop nginx             # 停止服务
systemctl restart nginx          # 重启服务
systemctl status nginx           # 查看状态
systemctl enable nginx           # 设置开机自启
systemctl disable nginx          # 取消开机自启
journalctl -u nginx -f           # 查看服务日志
```

### 1.4 网络诊断

```bash
# 端口查看
netstat -tlnp                    # 查看监听的 TCP 端口
ss -tlnp                         # 同上（更快，推荐）
ss -s                            # 连接统计摘要
lsof -i :8080                    # 查看占用8080端口的进程

# 连通性测试
ping host                        # 测试连通性
ping -c 4 host                   # 发送4个包后停止
traceroute host                  # 路由追踪
mtr host                         # 持续的路由追踪（更直观）

# HTTP 请求
curl url                         # GET 请求
curl -X POST -d '{"key":"val"}' -H "Content-Type: application/json" url
curl -I url                      # 只获取响应头
curl -o file url                 # 下载文件
curl -w "%{http_code}" url       # 只输出状态码
wget url                         # 下载文件

# DNS 查询
nslookup domain                  # DNS 查询
dig domain                       # 更详细的 DNS 查询
dig +trace domain                # 显示完整的 DNS 查询过程
host domain                      # 简单 DNS 查询

# 防火墙
iptables -L                      # 查看防火墙规则
iptables -A INPUT -p tcp --dport 80 -j ACCEPT  # 开放80端口
firewall-cmd --list-all          # firewalld 查看规则
ufw allow 80                     # Ubuntu 简化防火墙

# 网络抓包
tcpdump -i eth0 port 80          # 抓取80端口的包
tcpdump -i eth0 host 192.168.1.1 # 抓取指定主机的包
tcpdump -w capture.pcap          # 保存为 pcap 文件
```

### 1.5 磁盘与内存

```bash
df -h                            # 查看磁盘使用情况
du -sh dir/                      # 查看目录大小
du -sh * | sort -rh | head -10   # 查找最大的文件/目录
free -h                          # 查看内存使用
cat /proc/meminfo                # 详细内存信息
cat /proc/cpuinfo                # CPU 信息
lsblk                            # 查看块设备
fdisk -l                         # 查看磁盘分区
mount /dev/sdb1 /mnt             # 挂载磁盘
umount /mnt                      # 卸载
```

---

## 二、Linux 性能分析工具

### 2.1 top / htop — 实时系统监控

```bash
top                              # 实时监控
# 关键指标说明：
# load average: 1.5, 1.2, 0.8    → 1/5/15分钟平均负载
# %Cpu(s): 20 us, 5 sy, 0 ni     → 用户态/内核态/nice CPU 使用率
# %Cpu(s): 0 wa                   → I/O等待（高说明磁盘瓶颈）
# MiB Mem: 16000 total, 8000 free → 内存使用
# MiB Swap: 4000 total, 0 used   → Swap 使用（高说明内存不足）
#
# 进程列表：
# PID  USER  PR  NI  VIRT  RES  SHR  S  %CPU  %MEM  TIME+  COMMAND
# VIRT: 虚拟内存, RES: 物理内存, SHR: 共享内存

# top 交互命令
# P - 按 CPU 排序
# M - 按内存排序
# 1 - 显示各 CPU 核心
# H - 显示线程
# c - 显示完整命令

htop                             # top 的增强版，交互更友好
```

**Load Average 理解**：
- 值等于 CPU 核心数时，CPU 刚好满载
- 值大于核心数时，有进程在排队等待
- 例如 4 核 CPU，load average > 4 表示过载

### 2.2 vmstat — 虚拟内存统计

```bash
vmstat 1 5                       # 每秒采样1次，共5次
# 输出说明：
# procs:  r(运行队列), b(阻塞进程)
# memory: swpd(swap使用), free(空闲), buff(缓冲), cache(缓存)
# swap:   si(换入), so(换出) → 频繁换入换出说明内存不足
# io:     bi(块设备读), bo(块设备写)
# system: in(中断数), cs(上下文切换数)
# cpu:    us(用户态), sy(内核态), id(空闲), wa(I/O等待), st(被虚拟化偷走)
```

### 2.3 iostat — I/O 统计

```bash
iostat -xdm 1                    # 每秒输出磁盘扩展统计
# 关键指标：
# r/s, w/s     - 每秒读/写次数
# rkB/s, wkB/s - 每秒读/写 KB
# await        - 平均 I/O 等待时间（ms），高说明磁盘慢
# %util        - 磁盘利用率，>80% 说明磁盘瓶颈

iotop                            # 按进程显示 I/O 使用（类似 top）
```

### 2.4 netstat / ss — 网络统计

```bash
ss -s                            # 连接统计摘要
# Total: 1234
# TCP:   500 (estab 200, closed 100, orphaned 20, timewait 80)

ss -tnp                          # 查看 TCP 连接及对应进程
ss -tnp state established        # 只看已建立的连接
ss -tn state time-wait | wc -l   # 统计 TIME_WAIT 连接数

netstat -s                       # 协议统计信息
```

### 2.5 strace — 系统调用追踪

```bash
strace -p PID                    # 追踪运行中的进程
strace -p PID -e trace=network   # 只追踪网络相关调用
strace -p PID -e trace=file      # 只追踪文件相关调用
strace -c -p PID                 # 统计系统调用次数和时间
strace -f command                # 追踪命令及其子进程
strace -T command                # 显示每个调用的耗时
```

### 2.6 perf — Linux 性能分析

```bash
perf top                         # 实时显示 CPU 热点函数
perf record -g -p PID            # 采集指定进程的性能数据
perf report                      # 分析采集的数据
perf stat command                # 统计命令的性能计数器

# 生成火焰图
perf record -g -p PID -- sleep 30
perf script | ./stackcollapse-perf.pl | ./flamegraph.pl > flame.svg
```

### 2.7 性能分析思路

```
CPU 高？    → top/htop 找出进程 → perf/strace 分析热点
内存高？    → free/top 查看 → /proc/PID/smaps 分析内存映射
磁盘 I/O？  → iostat 查看 %util/await → iotop 找进程
网络慢？    → ss/netstat 查连接状态 → tcpdump 抓包分析
负载高？    → top 看 load average → 区分 CPU/IO/内存瓶颈
```

---

## 三、Shell 脚本基础

### 3.1 变量

```bash
# 变量定义（等号两边不能有空格！）
name="world"
age=25
readonly PI=3.14                  # 只读变量

# 变量使用
echo "Hello, $name"
echo "Hello, ${name}!"           # 推荐加花括号

# 特殊变量
$0                               # 脚本名
$1, $2, ...                      # 位置参数
$#                               # 参数个数
$@                               # 所有参数（每个独立）
$*                               # 所有参数（作为整体）
$?                               # 上一条命令的退出状态（0=成功）
$$                               # 当前脚本的 PID
$!                               # 最近后台进程的 PID

# 字符串操作
str="Hello World"
echo ${#str}                     # 字符串长度: 11
echo ${str:0:5}                  # 截取: Hello
echo ${str/World/Shell}          # 替换: Hello Shell
echo ${str,,}                    # 转小写: hello world
echo ${str^^}                    # 转大写: HELLO WORLD

# 命令替换
date=$(date +%Y-%m-%d)
files=$(ls /tmp)
count=$(wc -l < file.txt)

# 数组
arr=("apple" "banana" "cherry")
echo ${arr[0]}                   # apple
echo ${arr[@]}                   # 所有元素
echo ${#arr[@]}                  # 数组长度: 3
arr+=("date")                    # 追加元素
```

### 3.2 流程控制

```bash
# if-else
if [ "$age" -gt 18 ]; then
    echo "Adult"
elif [ "$age" -gt 12 ]; then
    echo "Teen"
else
    echo "Child"
fi

# 条件判断
# 数字比较: -eq, -ne, -gt, -ge, -lt, -le
# 字符串比较: =, !=, -z(空), -n(非空)
# 文件判断: -f(是文件), -d(是目录), -e(存在), -r(可读), -w(可写), -x(可执行)

# [[ ]] 比 [ ] 更强大
if [[ "$str" == *"hello"* ]]; then
    echo "Contains hello"
fi

# for 循环
for i in 1 2 3 4 5; do
    echo "$i"
done

for i in $(seq 1 10); do
    echo "$i"
done

for file in /tmp/*.log; do
    echo "Processing $file"
done

for ((i=0; i<10; i++)); do
    echo "$i"
done

# while 循环
count=0
while [ $count -lt 10 ]; do
    echo "$count"
    ((count++))
done

# 逐行读取文件
while IFS= read -r line; do
    echo "$line"
done < file.txt

# case 语句
case "$1" in
    start)
        echo "Starting..."
        ;;
    stop)
        echo "Stopping..."
        ;;
    restart)
        echo "Restarting..."
        ;;
    *)
        echo "Usage: $0 {start|stop|restart}"
        ;;
esac
```

### 3.3 函数

```bash
# 函数定义
function greet() {
    local name=$1               # local 变量
    echo "Hello, $name"
    return 0                    # 返回状态码（0-255）
}

# 调用
greet "World"

# 获取函数输出
result=$(greet "World")         # 通过命令替换捕获 echo 的输出
echo "$result"

# 检查返回值
if greet "World"; then
    echo "Success"
fi
```

### 3.4 管道与重定向

```bash
# 管道：将前一个命令的输出作为后一个命令的输入
cat file | grep "ERROR" | wc -l
ps aux | sort -rnk 3 | head -10    # CPU 使用最高的10个进程

# 重定向
command > file                    # 标准输出重定向（覆盖）
command >> file                   # 标准输出追加
command 2> error.log              # 标准错误重定向
command > out.log 2>&1            # 标准输出和错误都重定向
command > /dev/null 2>&1          # 丢弃所有输出
command < input.txt               # 标准输入重定向

# Here Document
cat <<EOF > config.txt
server:
  port: 8080
  host: localhost
EOF

# 进程替换
diff <(sort file1) <(sort file2)  # 比较两个排序后的文件
```

### 3.5 实用脚本示例

```bash
#!/bin/bash
# 服务健康检查脚本

URL="http://localhost:8080/health"
MAX_RETRY=3
RETRY_INTERVAL=5

check_health() {
    local status=$(curl -s -o /dev/null -w "%{http_code}" "$URL")
    if [ "$status" == "200" ]; then
        return 0
    else
        return 1
    fi
}

retry=0
while [ $retry -lt $MAX_RETRY ]; do
    if check_health; then
        echo "$(date): Service is healthy"
        exit 0
    fi
    retry=$((retry + 1))
    echo "$(date): Health check failed (attempt $retry/$MAX_RETRY)"
    sleep $RETRY_INTERVAL
done

echo "$(date): Service is DOWN after $MAX_RETRY attempts!"
exit 1
```

---

## 四、Docker 核心概念

### 4.1 镜像分层机制

Docker 镜像由多个**只读层（Layer）**组成，每一层对应 Dockerfile 中的一条指令。

```
┌────────────────────────┐
│  Container Layer (R/W) │  ← 容器运行时的可写层
├────────────────────────┤
│  Layer 5: CMD          │  ← 启动命令
├────────────────────────┤
│  Layer 4: COPY app.jar │  ← 复制应用
├────────────────────────┤
│  Layer 3: RUN apt-get  │  ← 安装依赖
├────────────────────────┤
│  Layer 2: ENV JAVA=17  │  ← 环境变量
├────────────────────────┤
│  Layer 1: FROM ubuntu  │  ← 基础镜像
└────────────────────────┘
```

**特性**：
- **共享基础层**：多个镜像可以共享相同的底层（如同一个 ubuntu 基础层），节省磁盘空间
- **Copy-on-Write**：容器启动时不复制镜像层，只有修改时才复制到可写层
- **层缓存**：构建时如果某层没有变化，直接使用缓存，加速构建
- **联合文件系统（UnionFS）**：将多个只读层叠加呈现为统一的文件系统

### 4.2 容器隔离原理

Docker 容器的隔离依赖 Linux 内核的两个核心特性：

#### Namespace（命名空间）— 资源隔离

| Namespace | 隔离内容 | 说明 |
|-----------|---------|------|
| PID | 进程 ID | 容器内 PID 从 1 开始 |
| Network | 网络栈 | 独立的网络接口、IP、端口 |
| Mount | 文件系统挂载点 | 独立的文件系统视图 |
| UTS | 主机名和域名 | 容器可以有自己的 hostname |
| IPC | 进程间通信 | 隔离消息队列、信号量 |
| User | 用户和用户组 | 容器内的 root != 宿主机 root |
| Cgroup | Cgroup 根目录 | 隔离 cgroup 视图 |

#### Cgroup（控制组）— 资源限制

```bash
# Docker 通过 cgroup 限制容器资源
docker run --memory=512m              # 限制内存 512MB
docker run --cpus=2                   # 限制使用 2 个 CPU 核心
docker run --memory=512m --memory-swap=1g  # 限制内存和 swap
docker run --cpu-shares=512           # CPU 权重（默认1024）
docker run --blkio-weight=500         # 磁盘 I/O 权重
docker run --pids-limit=100           # 限制容器内进程数
```

### 4.3 容器 vs 虚拟机

```
容器架构：                    虚拟机架构：
┌────┐ ┌────┐ ┌────┐        ┌────┐ ┌────┐ ┌────┐
│App1│ │App2│ │App3│        │App1│ │App2│ │App3│
├────┤ ├────┤ ├────┤        ├────┤ ├────┤ ├────┤
│Libs│ │Libs│ │Libs│        │Libs│ │Libs│ │Libs│
├────┴─┴────┴─┴────┤        ├────┤ ├────┤ ├────┤
│   Docker Engine   │        │ OS │ │ OS │ │ OS │
├──────────────────┤        ├────┴─┴────┴─┴────┤
│    Host OS        │        │    Hypervisor     │
├──────────────────┤        ├──────────────────┤
│    Hardware       │        │    Hardware       │
└──────────────────┘        └──────────────────┘
```

| 维度 | Docker 容器 | 虚拟机 |
|------|------------|--------|
| 启动速度 | 秒级 | 分钟级 |
| 资源占用 | MB 级（共享内核） | GB 级（完整 OS） |
| 隔离级别 | 进程级（Namespace/Cgroup） | 硬件级（Hypervisor） |
| 安全性 | 较弱（共享内核） | 强（完全隔离） |
| 性能 | 接近原生 | 有虚拟化损耗（5-20%） |
| 镜像大小 | MB 级 | GB 级 |
| 密度 | 单机可运行数百容器 | 通常几十个 VM |
| 使用场景 | 微服务、CI/CD、开发环境 | 多租户隔离、异构 OS |

---

## 五、Docker 常用命令

### 5.1 镜像操作

```bash
docker build -t myapp:v1 .              # 构建镜像
docker build -t myapp:v1 -f Dockerfile.prod .  # 指定 Dockerfile
docker build --no-cache -t myapp:v1 .   # 不使用缓存
docker images                            # 查看镜像列表
docker image ls --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"
docker pull nginx:1.25                   # 拉取指定版本
docker push myrepo/myapp:v1             # 推送镜像
docker tag myapp:v1 myrepo/myapp:v1     # 打标签
docker rmi image_id                      # 删除镜像
docker image prune                       # 清理悬空镜像
docker image prune -a                    # 清理所有未使用的镜像
docker save -o myapp.tar myapp:v1       # 导出镜像
docker load -i myapp.tar                 # 导入镜像
docker history myapp:v1                  # 查看镜像构建历史
docker inspect myapp:v1                  # 查看镜像详细信息
```

### 5.2 容器操作

```bash
docker run -d -p 8080:80 --name web nginx  # 后台运行
docker run -it ubuntu bash                  # 交互式运行
docker run --rm -it alpine sh               # 退出后自动删除
docker run -e "ENV=prod" myapp              # 设置环境变量
docker run --env-file .env myapp            # 从文件加载环境变量
docker run --restart=always myapp           # 自动重启策略

docker ps                                   # 查看运行中的容器
docker ps -a                                # 查看所有容器
docker logs -f --tail 100 container_id      # 实时查看最后100行日志
docker logs --since 1h container_id         # 查看最近1小时的日志
docker exec -it container_id bash           # 进入容器
docker exec container_id cat /etc/hosts     # 在容器内执行命令
docker stop container_id                    # 优雅停止
docker kill container_id                    # 强制停止
docker rm container_id                      # 删除容器
docker rm $(docker ps -aq)                  # 删除所有容器
docker cp file container_id:/path           # 复制文件到容器
docker cp container_id:/path/file .         # 从容器复制文件
docker stats                                # 实时资源使用统计
docker top container_id                     # 查看容器内进程
docker inspect container_id                 # 查看容器详细信息
docker system prune -a                      # 清理所有未使用的资源
```

---

## 六、Dockerfile 最佳实践

### 6.1 基础 Dockerfile

```dockerfile
FROM openjdk:17-slim
WORKDIR /app
COPY target/app.jar app.jar
EXPOSE 8080
ENV JAVA_OPTS="-Xmx256m"
ENTRYPOINT ["java", "-jar", "app.jar"]
```

### 6.2 多阶段构建（Multi-Stage Build）

```dockerfile
# 第一阶段：构建
FROM maven:3.9-eclipse-temurin-17 AS builder
WORKDIR /build
COPY pom.xml .
RUN mvn dependency:go-offline           # 先下载依赖（利用缓存）
COPY src/ src/
RUN mvn package -DskipTests

# 第二阶段：运行
FROM eclipse-temurin:17-jre-alpine
WORKDIR /app
COPY --from=builder /build/target/app.jar app.jar
RUN addgroup -S appgroup && adduser -S appuser -G appgroup
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s \
    CMD curl -f http://localhost:8080/health || exit 1
ENTRYPOINT ["java", "-jar", "app.jar"]
```

**好处**：构建阶段的 Maven、源代码等不会进入最终镜像，大幅减小体积。

### 6.3 层缓存优化

```dockerfile
# 错误：任何代码变化都导致 npm install 重新执行
COPY . .
RUN npm install

# 正确：先复制 package.json，安装依赖（利用缓存），再复制源码
COPY package.json package-lock.json ./
RUN npm ci --production
COPY . .
```

**原则**：变化频率低的指令放前面，变化频率高的放后面。

### 6.4 安全最佳实践

```dockerfile
# 使用非 root 用户
RUN groupadd -r app && useradd -r -g app app
USER app

# 使用特定版本标签（不用 latest）
FROM node:18.19-alpine3.19           # 明确版本

# 最小化安装
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*   # 清理 apt 缓存

# 使用 .dockerignore
# .dockerignore 文件内容：
# .git
# node_modules
# .env
# *.md
# docker-compose*.yml
```

### 6.5 常用指令对比

| 指令 | 说明 | 注意事项 |
|------|------|---------|
| FROM | 基础镜像 | 优先选择 alpine/slim 版本 |
| RUN | 执行命令 | 合并多个 RUN 减少层数 |
| COPY | 复制文件 | 优于 ADD（更明确） |
| ADD | 复制+解压 | 仅在需要解压时使用 |
| CMD | 默认命令 | 可被 docker run 覆盖 |
| ENTRYPOINT | 入口命令 | 不易被覆盖，与 CMD 配合 |
| ENV | 环境变量 | 构建和运行时都可用 |
| ARG | 构建参数 | 仅构建时可用 |
| EXPOSE | 声明端口 | 仅文档作用，不实际映射 |
| VOLUME | 声明挂载点 | 运行时需显式挂载 |
| HEALTHCHECK | 健康检查 | 推荐添加 |

---

## 七、Docker Compose 编排

### 7.1 完整示例

```yaml
version: "3.8"

services:
  app:
    build:
      context: .
      dockerfile: Dockerfile
    image: myapp:latest
    container_name: myapp
    ports:
      - "8080:8080"
    environment:
      - SPRING_PROFILES_ACTIVE=prod
      - DB_HOST=db
      - REDIS_HOST=redis
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_started
    volumes:
      - ./logs:/app/logs
    networks:
      - app-network
    restart: unless-stopped
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 512M

  db:
    image: mysql:8.0
    container_name: mysql
    ports:
      - "3306:3306"
    environment:
      MYSQL_ROOT_PASSWORD: root
      MYSQL_DATABASE: mydb
      MYSQL_USER: app
      MYSQL_PASSWORD: app123
    volumes:
      - db_data:/var/lib/mysql
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql
    networks:
      - app-network
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: redis
    ports:
      - "6379:6379"
    command: redis-server --requirepass redis123
    volumes:
      - redis_data:/data
    networks:
      - app-network

  nginx:
    image: nginx:1.25-alpine
    container_name: nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ssl:/etc/nginx/ssl:ro
    depends_on:
      - app
    networks:
      - app-network

volumes:
  db_data:
  redis_data:

networks:
  app-network:
    driver: bridge
```

### 7.2 常用命令

```bash
docker compose up -d               # 后台启动所有服务
docker compose up -d --build        # 重新构建并启动
docker compose down                 # 停止并删除容器/网络
docker compose down -v              # 同时删除 volumes
docker compose logs -f              # 查看所有服务日志
docker compose logs -f app          # 查看指定服务日志
docker compose ps                   # 查看服务状态
docker compose exec app bash        # 进入服务容器
docker compose restart app          # 重启指定服务
docker compose pull                 # 拉取最新镜像
docker compose config               # 验证并查看最终配置
docker compose top                  # 查看各服务进程
```

---

## 八、Docker 网络模式

### 8.1 四种网络模式

| 模式 | 说明 | 使用场景 |
|------|------|---------|
| bridge（默认） | 容器通过虚拟网桥通信 | 单机多容器通信 |
| host | 容器直接使用宿主机网络 | 性能要求高，不需要隔离 |
| none | 无网络 | 安全敏感的计算任务 |
| overlay | 跨主机容器通信 | Docker Swarm / 多机部署 |

### 8.2 Bridge 网络详解

```
宿主机网络
┌─────────────────────────────────┐
│  eth0 (192.168.1.100)           │
│       |                          │
│  docker0 (172.17.0.1)  ← 虚拟网桥│
│    /      |       \              │
│  veth    veth    veth  ← 虚拟网卡│
│   |       |       |              │
│ ┌───┐  ┌───┐  ┌───┐             │
│ │C1 │  │C2 │  │C3 │   容器      │
│ │.2 │  │.3 │  │.4 │             │
│ └───┘  └───┘  └───┘             │
└─────────────────────────────────┘
```

```bash
# 创建自定义网络
docker network create --driver bridge my-network

# 容器加入网络
docker run -d --network my-network --name app myapp

# 自定义网络中容器可通过名称互相访问
docker run -d --network my-network --name db mysql
# app 容器中可以直接用 "db" 作为主机名访问 mysql
```

### 8.3 Host 网络

```bash
docker run -d --network host nginx
# 容器直接使用宿主机的网络栈
# 不需要端口映射，性能最好
# 端口冲突由用户自行避免
```

---

## 九、Docker 存储

### 9.1 三种存储方式

| 方式 | 说明 | 管理者 | 持久化 | 适用场景 |
|------|------|--------|--------|---------|
| Volume | Docker 管理的存储卷 | Docker | 是 | 数据库数据、应用数据 |
| Bind Mount | 挂载宿主机目录 | 用户 | 是 | 配置文件、开发共享代码 |
| tmpfs | 内存中的临时存储 | — | 否 | 敏感数据、临时缓存 |

### 9.2 Volume 操作

```bash
# Volume 管理
docker volume create mydata              # 创建
docker volume ls                          # 列表
docker volume inspect mydata             # 详情
docker volume rm mydata                   # 删除
docker volume prune                       # 清理未使用的

# 使用 Volume
docker run -v mydata:/app/data myapp     # 命名卷
docker run -v /app/data myapp            # 匿名卷

# Bind Mount
docker run -v /host/path:/container/path myapp          # 读写
docker run -v /host/path:/container/path:ro myapp       # 只读
docker run --mount type=bind,source=/host,target=/container myapp

# tmpfs
docker run --tmpfs /app/cache myapp
docker run --mount type=tmpfs,target=/app/cache,tmpfs-size=100m myapp
```

### 9.3 存储最佳实践

- 数据库数据使用命名 Volume（Docker 管理、可备份）
- 配置文件使用 Bind Mount（方便修改）
- 敏感临时数据使用 tmpfs（不落盘）
- 避免在容器可写层存储大量数据（性能差、容器删除即丢失）

---

## 十、Kubernetes 基础

### 10.1 核心概念

```
┌──────────────────────────────────────────────────┐
│                    Kubernetes Cluster             │
│                                                    │
│  ┌──────────────────┐                              │
│  │   Master Node     │                              │
│  │  ┌─────────────┐ │                              │
│  │  │ API Server   │ │  ← 所有操作的入口            │
│  │  │ Scheduler    │ │  ← 调度 Pod 到合适的 Node    │
│  │  │ Controller   │ │  ← 维护集群状态              │
│  │  │ etcd         │ │  ← 分布式 KV 存储（集群状态） │
│  │  └─────────────┘ │                              │
│  └──────────────────┘                              │
│                                                    │
│  ┌──────────────┐  ┌──────────────┐                │
│  │  Worker Node  │  │  Worker Node  │                │
│  │ ┌──────────┐ │  │ ┌──────────┐ │                │
│  │ │ kubelet  │ │  │ │ kubelet  │ │  ← 管理节点上的Pod│
│  │ │ kube-proxy│ │ │ │ kube-proxy│ │  ← 网络代理     │
│  │ │ ┌──────┐ │ │  │ │ ┌──────┐ │ │                │
│  │ │ │ Pod  │ │ │  │ │ │ Pod  │ │ │                │
│  │ │ │┌────┐│ │ │  │ │ │┌────┐│ │ │                │
│  │ │ ││ C1 ││ │ │  │ │ ││ C1 ││ │ │                │
│  │ │ │└────┘│ │ │  │ │ │└────┘│ │ │                │
│  │ │ └──────┘ │ │  │ │ └──────┘ │ │                │
│  │ └──────────┘ │  │ └──────────┘ │                │
│  └──────────────┘  └──────────────┘                │
└──────────────────────────────────────────────────┘
```

### 10.2 核心资源对象

#### Pod — 最小调度单位

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: myapp-pod
  labels:
    app: myapp
spec:
  containers:
    - name: myapp
      image: myapp:v1
      ports:
        - containerPort: 8080
      resources:
        requests:
          memory: "128Mi"
          cpu: "250m"
        limits:
          memory: "256Mi"
          cpu: "500m"
      livenessProbe:
        httpGet:
          path: /health
          port: 8080
        initialDelaySeconds: 30
        periodSeconds: 10
      readinessProbe:
        httpGet:
          path: /ready
          port: 8080
        initialDelaySeconds: 5
        periodSeconds: 5
```

- 一个 Pod 可以包含一个或多个容器
- 同一个 Pod 内的容器共享网络和存储
- Pod 是临时的，随时可能被重建

#### Deployment — 管理 Pod 的副本和更新

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: myapp-deployment
spec:
  replicas: 3                          # 期望副本数
  selector:
    matchLabels:
      app: myapp
  strategy:
    type: RollingUpdate                # 滚动更新
    rollingUpdate:
      maxSurge: 1                      # 最多多创建1个
      maxUnavailable: 0                # 最少可用数不减少
  template:
    metadata:
      labels:
        app: myapp
    spec:
      containers:
        - name: myapp
          image: myapp:v2
          ports:
            - containerPort: 8080
```

功能：声明式管理、滚动更新、回滚、水平扩缩容。

#### Service — 服务发现与负载均衡

```yaml
apiVersion: v1
kind: Service
metadata:
  name: myapp-service
spec:
  type: ClusterIP                      # 集群内部访问
  selector:
    app: myapp
  ports:
    - port: 80                         # Service 端口
      targetPort: 8080                 # Pod 端口
```

Service 类型：
| 类型 | 说明 |
|------|------|
| ClusterIP（默认） | 集群内部虚拟 IP |
| NodePort | 在每个节点开放端口（30000-32767） |
| LoadBalancer | 云厂商负载均衡器 |
| ExternalName | 映射外部 DNS 名称 |

#### Ingress — HTTP 路由

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: myapp-ingress
spec:
  rules:
    - host: app.example.com
      http:
        paths:
          - path: /api
            pathType: Prefix
            backend:
              service:
                name: myapp-service
                port:
                  number: 80
          - path: /
            pathType: Prefix
            backend:
              service:
                name: frontend-service
                port:
                  number: 80
  tls:
    - hosts:
        - app.example.com
      secretName: tls-secret
```

#### ConfigMap / Secret — 配置管理

```yaml
# ConfigMap - 非敏感配置
apiVersion: v1
kind: ConfigMap
metadata:
  name: myapp-config
data:
  APP_ENV: "production"
  DB_HOST: "mysql-service"
  config.yaml: |
    server:
      port: 8080
      timeout: 30s

---
# Secret - 敏感信息（Base64编码）
apiVersion: v1
kind: Secret
metadata:
  name: myapp-secret
type: Opaque
data:
  DB_PASSWORD: cGFzc3dvcmQxMjM=     # base64(password123)
  API_KEY: bXlhcGlrZXk=
```

使用方式：
```yaml
# 在 Pod 中使用
spec:
  containers:
    - name: myapp
      envFrom:
        - configMapRef:
            name: myapp-config       # 作为环境变量
        - secretRef:
            name: myapp-secret
      volumeMounts:
        - name: config-volume
          mountPath: /etc/config     # 作为文件挂载
  volumes:
    - name: config-volume
      configMap:
        name: myapp-config
```

### 10.3 常用 kubectl 命令

```bash
# 集群信息
kubectl cluster-info                   # 集群信息
kubectl get nodes                      # 查看节点
kubectl top nodes                      # 节点资源使用

# Pod 操作
kubectl get pods                       # 查看 Pod
kubectl get pods -o wide               # 详细信息（含 IP、Node）
kubectl describe pod myapp             # 查看 Pod 详情
kubectl logs myapp-pod                 # 查看日志
kubectl logs -f myapp-pod              # 实时查看日志
kubectl exec -it myapp-pod -- bash     # 进入 Pod

# 资源管理
kubectl apply -f deployment.yaml       # 创建/更新资源
kubectl delete -f deployment.yaml      # 删除资源
kubectl scale deployment myapp --replicas=5  # 扩缩容
kubectl rollout status deployment myapp      # 查看部署状态
kubectl rollout undo deployment myapp        # 回滚
kubectl rollout history deployment myapp     # 查看更新历史

# 调试
kubectl get events --sort-by='.lastTimestamp'  # 查看事件
kubectl port-forward svc/myapp 8080:80         # 端口转发
```

---

## 十一、面试高频问题

### Q1：Docker 容器和虚拟机的区别？
**答**：容器共享宿主机内核，通过 Namespace 和 Cgroup 实现隔离和资源限制，启动快（秒级）、资源占用小（MB 级）、性能接近原生。虚拟机通过 Hypervisor 运行完整的客户 OS，隔离性更强但启动慢（分钟级）、资源占用大（GB 级）。容器适合微服务，VM 适合需要强隔离的场景。

### Q2：Docker 镜像为什么是分层的？
**答**：分层存储实现层共享（多个镜像共用基础层节省磁盘）、构建缓存（未变化的层直接复用加速构建）、Copy-on-Write（容器启动不复制，修改时才拷贝到可写层）。联合文件系统（如 OverlayFS）将多个只读层叠加呈现为统一文件系统。

### Q3：如何优化 Docker 镜像大小？
**答**：使用多阶段构建（编译和运行分离）；选择轻量基础镜像（alpine/slim/distroless）；合并 RUN 指令减少层数并清理缓存；合理利用层缓存（变化少的放前面）；使用 .dockerignore 排除无关文件。

### Q4：Docker 的网络模式有哪些？
**答**：bridge（默认，虚拟网桥，容器间通信）、host（直接使用宿主机网络栈）、none（无网络）、overlay（跨主机通信，Docker Swarm/K8s）。自定义 bridge 网络中容器可通过名称互相访问。

### Q5：Namespace 和 Cgroup 分别解决什么问题？
**答**：Namespace 解决**隔离**问题，让容器拥有独立的 PID、网络、文件系统、用户等视图。Cgroup 解决**资源限制**问题，控制容器的 CPU、内存、磁盘 I/O、进程数等上限。两者结合实现容器的轻量级隔离。

### Q6：K8s 中 Pod 和容器的关系？
**答**：Pod 是 K8s 最小调度单位，一个 Pod 可以包含一个或多个容器。同一 Pod 内的容器共享网络（相同 IP 和端口空间）和存储卷，通过 localhost 通信。通常一个 Pod 运行一个主容器，可能有 sidecar 容器（如日志收集、代理）。

### Q7：K8s 中 Deployment 的滚动更新原理？
**答**：Deployment 通过创建新的 ReplicaSet 逐步替换旧的 ReplicaSet。maxSurge 控制最多多创建几个 Pod，maxUnavailable 控制最多不可用几个 Pod。更新过程：新建新版 Pod → 就绪后删除旧版 Pod → 直到全部替换。可通过 rollout undo 快速回滚。

### Q8：如何查看 Linux 系统资源使用情况？
**答**：CPU 用 top/htop；内存用 free -h；磁盘空间用 df -h；磁盘 I/O 用 iostat；网络连接用 ss/netstat；综合性能用 vmstat。负载高时先看 top 的 load average 和 %wa（I/O 等待），区分是 CPU 密集、I/O 密集还是内存不足。

### Q9：grep、sed、awk 各自的适用场景？
**答**：grep 用于文本搜索/过滤（找到包含特定模式的行）；sed 用于文本替换/编辑（批量修改文件内容）；awk 用于文本分析/处理（按列提取、计算、统计）。三者常组合使用。

### Q10：K8s 中 Service 和 Ingress 的区别？
**答**：Service 提供 L4（TCP/UDP）负载均衡和服务发现，类型包括 ClusterIP（集群内部）、NodePort（节点端口）、LoadBalancer（云 LB）。Ingress 提供 L7（HTTP/HTTPS）路由，基于域名和路径将请求转发到不同的 Service，支持 TLS 终止。通常外部流量通过 Ingress → Service → Pod。
