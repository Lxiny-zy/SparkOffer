# SparkOffer

> **点燃你的 Offer：AI 驱动的面试训练与营销闭环。**

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-61DAFB.svg)](https://react.dev/)
[![LangGraph](https://img.shields.io/badge/LangGraph-Powered-1C3C3C.svg)](https://www.langchain.com/langgraph)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[English](README.en.md)

---

## 项目介绍

SparkOffer 是一个面向技术岗求职者的 **AI 面试训练系统**。

它解决的问题很具体：市面上的面试工具大多“答完即结束”——题目随机、反馈泛泛、下次再来又是从零开始。SparkOffer 把每一次训练都变成一次**可累积的能力建模过程**：AI 持续记录你的薄弱点、掌握度、思维模式与表达习惯，下一轮训练自动围绕你的短板展开，并按遗忘曲线安排复习。

简而言之——**练得越多，AI 越懂你；越懂你，越能精准点燃你的下一个 Offer。**

---

## 核心亮点

### 1. 个性化出题引擎（三层信息融合）

不是从固定题库随机抽题，而是在每一轮提问前融合三层上下文：

```
┌─ Layer 3 · 长期画像 ──────────────────────┐
│  跨领域强弱项 · 思维模式 · 沟通风格        │
├─ Layer 2 · 领域掌握度 ────────────────────┤
│  0-100 掌握度 · 历史薄弱点 · 训练洞察      │
├─ Layer 1 · 会话上下文 ────────────────────┤
│  简历 · JD · 知识库 RAG · 最近 20 题去重   │
└────────────────────────────────────────────┘
              ↓ 注入 LangGraph 工作流
        AI 面试官生成 10 道高度个性化的问题
```

掌握度 0-30 主打概念辨析；30-60 进入场景应用；60-100 直接拉到系统设计与权衡分析。前 3 题精准命中历史薄弱点，再向新主题扩展。

### 2. 训练 → 评估 → 画像更新 闭环

每一次训练都不是孤立事件，而是一次可量化的能力迭代：

```
作答 → 逐题打分 + 薄弱点提取
     → 加权掌握度算法（confidence = 难度/5 × 得分/10）
     → LLM 画像更新（Mem0 风格的 ADD / UPDATE / IMPROVE）
     → 向量化记忆入库（语义检索历史洞察）
     → SM-2 调度下一轮复习
     → 下一次出题更精准
```

掌握度走的是**确定性算法**，不依赖 LLM 主观打分；画像走的是**LLM 智能合并**，避免重复堆叠、保持画像精炼。

### 3. 多场景训练入口

| 场景 | 用法 |
| --- | --- |
| **弱点狙击站** | 选定领域，10 道动态生成的高针对性问题，难度随掌握度自适应 |
| **实战模拟场** | LangGraph 状态机驱动：自我介绍 → 技术问题 → 项目深挖 → 反问 |
| **岗位特训营** | 抽取 JD 重点，结合简历与知识库生成贴合岗位的题目 |
| **知识训练场** | 知识库拆成记忆卡片，三档深度翻面强化，配合 SM-2 到期复习 |
| **算法竞技场** | 题目收藏、错题回顾、AI 解题陪练 |
| **问答演练场** | 自由追问任意技术点，好答案一键沉淀回知识库 |

### 4. 双层知识增强（RAG）

- **领域知识库**：LlamaIndex 索引你自己维护的 Markdown 知识文档，作为出题与评分的事实依据
- **历史训练记忆**：训练产生的洞察（薄弱点、错误模式、改进建议）自动向量化入库，下次出题时语义检索召回

### 5. 可观测的成长

- 逐题评分 + 改进建议
- 掌握度雷达图与趋势图
- 跨领域强弱项对比
- 长期画像（思维模式、沟通风格、习惯性问题）
- 错题与高频题热区

---

## 快速开始

### 1. 配置环境变量

```bash
cp .env.example .env
```

**模型配置必填**：LLM + Embedding（Embedding 必须二选一）。

```env
# LLM（任意 OpenAI 兼容端点）
API_BASE=https://your-llm-api-base/v1
API_KEY=sk-your-api-key
MODEL=your-model-name

# Embedding
EMBEDDING_BACKEND=api          # api | local
EMBEDDING_API_BASE=https://your-embedding-api-base/v1
EMBEDDING_API_KEY=sk-your-embedding-key
EMBEDDING_API_MODEL=BAAI/bge-m3
```

**Docker / 生产安全配置必填**：生产模式会拒绝空值、公开弱口令和不安全的跨域来源。以下值都是待替换的安全占位，不是可直接使用的默认凭据：

```env
APP_ENV=production
FRONTEND_BIND_ADDRESS=127.0.0.1
JWT_SECRET=<独立生成的随机强密钥>
DEFAULT_EMAIL=admin@sparkoffer.local
DEFAULT_PASSWORD=<至少12字符的唯一强密码>
ALLOW_REGISTRATION=false
CORS_ALLOW_ORIGINS=https://app.example.com
QDRANT_API_KEY=<另一份独立生成的随机强密钥>
```

可用 `openssl rand -hex 32` 分别生成 `JWT_SECRET` 和 `QDRANT_API_KEY`。HTTPS 证书、宿主反向代理、可信代理链和完整生产检查见 [DEPLOYMENT.md](DEPLOYMENT.md)。

### 2. Docker 启动（推荐）

```bash
docker compose up -d --build
```

Compose 只把前端 HTTP 监听绑定到宿主回环地址 `127.0.0.1:9000`，供宿主 Nginx/Caddy/负载均衡器反代；它不是浏览器的生产访问入口。配置 TLS 后请访问 `https://你的域名`，不要把 9000 暴露到公网或直接访问该明文监听。完整步骤见 [DEPLOYMENT.md](DEPLOYMENT.md)。

### 3. 手动启动

后端：
```bash
pip install -r requirements.txt
# 如需本地 embedding：pip install -r requirements.local-embedding.txt
uvicorn backend.main:app --reload --port 8000
```

前端：
```bash
cd frontend
npm install
npm run dev    # http://localhost:5173
```

---

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | FastAPI · LangChain · LangGraph · LlamaIndex |
| 前端 | React 19 · React Router v7 · Vite · Tailwind CSS v4 |
| 存储 | SQLite · 用户隔离目录 · 向量化长期记忆 |
| 向量库 | Qdrant（Docker 部署默认）/ numpy（本地开发默认） |
| 认证 | JWT · bcrypt |
| LLM | 任意 OpenAI 兼容 API |

---

## 项目结构

```text
SparkOffer/
├── backend/
│   ├── main.py                # FastAPI 入口
│   ├── auth.py                # JWT + bcrypt 鉴权
│   ├── memory.py              # 长期画像（Mem0 风格）
│   ├── vector_memory.py       # 向量化历史洞察
│   ├── indexer.py             # LlamaIndex 知识库索引（manifest 增量重建）
│   ├── spaced_repetition.py   # SM-2 复习调度
│   ├── graphs/                # LangGraph 工作流
│   │   ├── resume_interview.py
│   │   ├── job_prep.py
│   │   ├── topic_drill.py
│   │   └── review.py
│   ├── prompts/               # 角色提示词
│   ├── routers/               # FastAPI 路由
│   └── storage/               # SQLite 持久化
├── frontend/src/
│   ├── pages/                 # Home/Interview/Profile/Knowledge/...
│   ├── components/            # UI 与图表
│   └── api/                   # API 客户端
├── data/                      # 运行时数据（用户目录 gitignored）
│   ├── topics.example.json
│   ├── knowledge/             # 共享领域知识文档
│   └── users/{user_id}/       # 每用户隔离：简历/画像/知识库/索引
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## 适合谁

- 准备后端、算法、AI 应用、Agent、RAG 等技术岗位面试的求职者
- 已经刷过很多题，但缺乏“连续性 + 复盘闭环”的人
- 想围绕自己的简历项目和目标 JD 做定向训练的人
- 想长期跟踪能力曲线，而不是做一次性问答的人

---

## License

MIT
