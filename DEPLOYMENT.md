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
| frontend | **9000**  | 80      | Nginx + React 前端页面    |
| backend  | **9001**  | 8000    | FastAPI 后端接口          |
| qdrant   | 6333/6334 | 6333/6334 | 向量库（**compose 部署默认启用**，`.env` 设 `VECTOR_BACKEND=numpy` 可退回本地） |

- 前端访问地址：`http://服务器IP:9000`
- 后端 API 文档：`http://服务器IP:9001/docs`
- 前端容器内通过 Docker 内部网络（`backend:8000`）访问后端，无需直接暴露后端到公网

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
| `API_BASE`            | LLM API 接口地址（兼容 OpenAI 格式）        |
| `API_KEY`             | LLM API 密钥                              |
| `MODEL`               | 使用的模型名称                             |
| `EMBEDDING_BACKEND`   | 向量嵌入后端：`api`（推荐）或 `local`       |
| `EMBEDDING_API_BASE`  | 嵌入服务 API 地址                          |
| `EMBEDDING_API_KEY`   | 嵌入服务密钥                              |
| `EMBEDDING_API_MODEL` | 嵌入模型名称                              |
| `JWT_SECRET`          | JWT 签名密钥，生产环境务必修改              |
| `DEFAULT_EMAIL`       | 初始管理员账号                             |
| `DEFAULT_PASSWORD`    | 初始管理员密码                             |
| `ALLOW_REGISTRATION`  | 是否开放注册（建议服务器环境设为 `false`）   |
| `VECTOR_BACKEND`      | 向量后端。**compose 部署默认 `qdrant`**（见 docker-compose.yml 的 `${VECTOR_BACKEND:-qdrant}`）；显式设 `numpy` 退回本地。裸 uvicorn 本地开发默认 `numpy` |
| `QDRANT_URL`          | Qdrant 地址。**compose 部署默认 `http://qdrant:6333`**；仅裸 uvicorn 时需手动设置 |
| `QDRANT_API_KEY`      | Qdrant 鉴权密钥（自建无鉴权可留空）          |

### 2. 确认防火墙已放行端口

```bash
# CentOS / 阿里云安全组
firewall-cmd --add-port=9000/tcp --permanent
firewall-cmd --add-port=9001/tcp --permanent
firewall-cmd --reload

# Ubuntu ufw
ufw allow 9000/tcp
ufw allow 9001/tcp
```

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
| `data/.index_cache/`  | 向量索引缓存                   |
| `data/resume/`        | 上传的简历文件                  |
| `data/user_profile/`  | 用户画像数据                   |
| `data/qdrant/`        | Qdrant 向量数据（仅启用 qdrant 时） |

> ⚠️ **服务器迁移时请一并备份 `data/` 目录和 `.env` 文件。**

---

## 容器间通信架构

```
浏览器
  │  访问 http://服务器IP:9000
  ▼
Nginx (frontend 容器, 宿主机 9000 -> 容器 80)
  │  /api/* 请求通过 Docker 内网代理
  ▼
FastAPI (backend 容器, 宿主机 9001 -> 容器 8000)
  │
  ├── ./data:/app/data  (本地 Volume 挂载)
  │
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
# 查看占用 9000 或 9001 端口的进程
netstat -tulnp | grep -E '9000|9001'
# 或
ss -tulnp | grep -E '9000|9001'
```

---

## 健康检查说明

`docker-compose.yml` 已内置健康检查：

- **backend**：每 30s 检查一次 `http://localhost:8000/docs` 是否可访问，最多重试 3 次，启动宽限期 30s
- **frontend**：backend 健康后才启动，每 30s 检查一次 `http://localhost:80/`
- **qdrant**：基于 distroless 镜像无 shell，未配 exec 健康检查。backend 为 qdrant-only（不降级 numpy）：Qdrant 不可用时知识库检索降级为空上下文并委派后台重建，服务本身不崩

这确保了服务依赖顺序正确，前端不会在后端未就绪时启动。

---

## 安全建议

1. 生产环境务必修改 `.env` 中的 `JWT_SECRET` 为随机强密钥
2. `DEFAULT_PASSWORD` 首次登录后立即修改
3. `.env` 文件不得提交到 Git（已在 `.gitignore` 和 `.dockerignore` 中排除）
4. 如有条件，建议在 Nginx 容器前部署 Cloudflare 或服务器自有反向代理，统一 HTTPS 出口
