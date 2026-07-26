# TechSpar 服务器 Docker 部署指南

本文档描述如何使用 Docker Compose 在服务器上部署 TechSpar（SparkOffer）项目。

---

## 目录结构说明

```
TechSpar-main/
├── docker-compose.yml        # 主编排文件
├── .env                      # 环境变量（不提交 Git）
├── .env.example              # 环境变量模板
├── backend/
│   └── Dockerfile            # 后端镜像构建
└── frontend/
    ├── Dockerfile            # 前端镜像构建
    └── nginx.conf            # Nginx 反向代理配置
```

---

## 端口映射

| 服务     | 宿主机端口 | 容器端口 | 说明                      |
|----------|-----------|---------|--------------------------|
| frontend | `127.0.0.1:9000` | 80 | 仅供宿主 TLS 入口反代，不对公网监听 |
| backend  | 不映射    | 8000    | 仅允许 frontend 容器访问  |
| qdrant   | 不映射    | 6333/6334 | 仅允许 backend 内部网络访问 |
| redis    | 不映射    | 6379    | 仅允许 backend 内部网络访问 |

- 用户访问地址：`https://你的域名`（必须由宿主 Nginx/Caddy/负载均衡器终止 TLS）
- `127.0.0.1:9000` 只是宿主反代目标，不得直接暴露到公网
- 前端容器内通过 Docker 网络（`backend:8000`）访问后端
- 后端 API、Qdrant 和 Redis 均不直接暴露到宿主机或公网
- 需要检查 API 文档时，可在服务器执行 `docker compose exec backend python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/docs').status)"`

---

## 部署前准备

### 1. 配置环境变量

```bash
# 如果是全新部署，先复制模板
cp .env.example .env

# 编辑 .env，填写真实密钥和配置
vim .env
```

`.env` 关键字段说明：

| 字段                  | 说明                                      |
|-----------------------|-------------------------------------------|
| `APP_ENV`             | 生产必须为 `production`；仅隔离的本机开发可设 `development` |
| `FRONTEND_BIND_ADDRESS` | 默认 `127.0.0.1`；生产不得改为公网地址，HTTPS 入口反代到本机 9000 |
| `API_BASE`            | LLM API 接口地址（兼容 OpenAI 格式）        |
| `API_KEY`             | LLM API 密钥                              |
| `MODEL`               | 使用的模型名称                             |
| `EMBEDDING_BACKEND`   | 向量嵌入后端：`api`（推荐）或 `local`       |
| `EMBEDDING_API_BASE`  | 嵌入服务 API 地址                          |
| `EMBEDDING_API_KEY`   | 嵌入服务密钥                              |
| `EMBEDDING_API_MODEL` | 嵌入模型名称                              |
| `JWT_SECRET`          | JWT 签名密钥，必须使用随机强密钥             |
| `DEFAULT_EMAIL`       | 初始管理员账号                             |
| `DEFAULT_PASSWORD`    | 仅用于新建初始管理员，生产至少 12 字符；不会覆盖存量账号密码 |
| `ALLOW_REGISTRATION`  | 是否开放注册（建议服务器环境设为 `false`）   |
| `CORS_ALLOW_ORIGINS`  | 允许的浏览器来源，生产环境禁止 `*`           |
| `TRUSTED_PROXY_CIDRS` | 可提供客户端 IP 头的可信代理链；compose 默认信任宿主入口网关和固定 frontend 地址 |
| `VECTOR_BACKEND`      | 向量后端。**compose 部署默认 `qdrant`**（见 docker-compose.yml 的 `${VECTOR_BACKEND:-qdrant}`）；显式设 `numpy` 退回本地。裸 uvicorn 本地开发默认 `numpy` |
| `QDRANT_URL`          | Qdrant 地址。**compose 部署默认 `http://qdrant:6333`**；仅裸 uvicorn 时需手动设置 |
| `QDRANT_IMAGE`        | 默认固定 `qdrant/qdrant:v1.12.0`；升级前先做 snapshot 与恢复演练 |
| `QDRANT_API_KEY`      | Qdrant 鉴权密钥，compose 部署必填，backend 与 Qdrant 使用同一值 |

可用以下命令分别生成独立密钥：

```bash
openssl rand -hex 32  # JWT_SECRET
openssl rand -hex 32  # QDRANT_API_KEY
```

Compose 的代理网络固定为 `172.30.10.0/24`，网关为 `172.30.10.1`，frontend 为 `172.30.10.10`。后端默认只信任这两个地址提供的代理链；宿主 TLS 代理必须覆盖客户端传入的 `X-Forwarded-For`，再由 frontend 追加网关地址。若网段或网关不同，必须同步修改 subnet、frontend `ipv4_address` 和 `TRUSTED_PROXY_CIDRS`，并实测审计日志中的客户端 IP。

> **升级既有部署时必须核验 owner 密码。** 启动校验检查的是 `.env` 中用于新建账号的 `DEFAULT_PASSWORD`，不会重置数据库里已经存在的 owner 密码。旧实例必须登录应用修改密码并验证旧公开口令已失效；若旧凭据可能泄露，还要轮换 `JWT_SECRET` 使既有 token 失效。

### 2. 配置 HTTPS 入口（生产必需）

Compose 默认只在宿主回环地址监听 9000。以下宿主 Nginx 示例负责 TLS，并通过覆盖 `X-Forwarded-For` 丢弃客户端伪造的代理链；证书路径和域名需替换为真实值：

```nginx
server {
    listen 80;
    server_name app.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name app.example.com;
    ssl_certificate /etc/letsencrypt/live/app.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/app.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:9000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
}
```

`.env` 中的 `CORS_ALLOW_ORIGINS` 必须使用完全一致的 HTTPS Origin，例如 `https://app.example.com`。生产校验会拒绝非回环地址的明文 HTTP Origin。

### 3. 确认防火墙已放行 HTTPS

```bash
# CentOS / 阿里云安全组（80 仅用于跳转/ACME）
firewall-cmd --add-service=http --permanent
firewall-cmd --add-service=https --permanent
firewall-cmd --reload

# Ubuntu ufw
ufw allow 80/tcp
ufw allow 443/tcp
```

不要在安全组或防火墙中放行 9000、6333、6334、6379。

---

## 部署命令

### 首次部署（全量构建）

```bash
docker compose build --no-cache
docker compose up -d
```

### 查看服务状态

```bash
docker compose ps
docker compose logs -f
```

### 仅查看某个服务日志

```bash
docker compose logs -f backend
docker compose logs -f frontend
```

### 停止服务

```bash
docker compose down
```

### 更新代码后重新部署

```bash
# 拉取最新代码（或上传新代码到服务器）
git pull   # 或 scp / rsync

# 重新构建并启动（只重建有变化的镜像）
docker compose build
docker compose up -d
```

---

## 数据持久化

后端运行数据通过 Volume 挂载保存在宿主机：

```yaml
volumes:
  - ./data:/app/data
```

`data/` 目录包含：

| 子目录/文件           | 内容                           |
|-----------------------|-------------------------------|
| `data/interviews.db`  | SQLite 数据库（面试记录等）     |
| `data/knowledge/`     | 知识库文档                     |
| `data/users/<user_id>/` | 每个用户的简历、知识库、画像、索引缓存（`resume/`、`profile/`、`.index_cache/` 等，当前布局） |
| `data/.index_cache/`  | 向量索引缓存（legacy 根级路径，兼容保留） |
| `data/resume/`        | 上传的简历文件（legacy 根级路径，兼容保留） |
| `data/user_profile/`  | 用户画像数据（legacy 根级路径，兼容保留） |
| `data/qa_notes/<user_id>/` | Q&A 总结文件                  |
| `data/qdrant/`        | Qdrant 向量数据（仅启用 qdrant 时） |

> ⚠️ **服务器迁移时请一并备份 `data/` 目录和 `.env` 文件。**

---

## 容器间通信架构

```
浏览器
  │  HTTPS :443
  ▼
宿主 TLS 入口（Nginx/Caddy/负载均衡器）
  │  http://127.0.0.1:9000
  ▼
Nginx (frontend 容器, 宿主回环 9000 -> 容器 80)
  │  /api/* 请求通过 Docker 内网代理
  ▼
FastAPI (backend 容器, 仅 proxy Docker 网络 -> 容器 8000)
  │
  ├── ./data:/app/data（Qdrant/Redis 子目录在 backend 内被只读 tmpfs 遮蔽）
  │
  ├── Qdrant / Redis（仅 data 内部网络）
  └── 外部 LLM / Embedding API
```

---

## 常见问题

### 前端页面空白或 API 请求失败

检查 backend 容器是否健康：

```bash
docker compose ps
# backend 状态应为 healthy
```

查看 backend 启动日志：

```bash
docker compose logs backend
```

### 内存不足导致容器重启

`docker-compose.yml` 中 backend 容器限制最大内存 2GB：

```yaml
deploy:
  resources:
    limits:
      memory: 2G
```

如果服务器内存紧张，可适当调低该值，但建议不低于 1GB。

### 端口被占用

```bash
# 查看占用 9000 端口的进程
netstat -tulnp | grep -E '9000'
# 或
ss -tulnp | grep -E '9000'
```

---

## 健康检查说明

`docker-compose.yml` 已内置健康检查：

- **backend**：每 30s 检查一次 `http://localhost:8000/docs` 是否可访问，最多重试 3 次，启动宽限期 30s
- **frontend**：backend 健康后才启动，每 30s 检查一次 `http://localhost:80/`
- **qdrant**：基于 distroless 镜像无 shell，未配 exec 健康检查；服务只在内部网络监听并启用 API key。backend 为 qdrant-only（不降级 numpy）：Qdrant 不可用时知识库检索降级为空上下文并委派后台重建，服务本身不崩

这保证基础服务启动顺序，但不是 Qdrant 业务 readiness：真实上线仍要对知识检索、写入和恢复执行 smoke test。

---

## 安全建议

1. 生产环境必须填写随机的 `JWT_SECRET`、至少 12 字符的 `DEFAULT_PASSWORD` 和至少 32 字节的 `QDRANT_API_KEY`；后端会拒绝弱 bootstrap 配置
2. `DEFAULT_PASSWORD` 不会覆盖存量 owner；首次部署后和每次旧版本升级时都要在应用内改密并确认旧口令失效，必要时轮换 `JWT_SECRET`
3. `.env` 文件不得提交到 Git（已在 `.gitignore` 和 `.dockerignore` 中排除）
4. `TRUSTED_PROXY_CIDRS` 只填写实际反向代理容器/网段，不要填写 `0.0.0.0/0`
5. 生产必须通过宿主反向代理、Cloudflare 或负载均衡器提供 HTTPS；9000 保持 loopback，禁止公网直连
