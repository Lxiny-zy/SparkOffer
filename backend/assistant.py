"""AI Assistant — floating helper that can chat and execute site actions."""
import asyncio
import json
import logging
import re
from typing import AsyncGenerator

from backend.config import settings
from backend.llm_provider import get_langchain_llm
from backend.memory import get_profile, get_profile_summary, _load_profile, _save_profile
from backend.storage.sessions import list_sessions, list_distinct_topics, get_session, list_sessions_by_topic
from backend.storage.favorites import list_favorites, get_favorite_tags
from backend.storage.algorithm import list_algorithm_cards, get_algorithm_tags
from backend.storage.assistant_chats import save_message
from backend.spaced_repetition import get_due_reviews
from backend.vector_memory import search_memory
from backend.indexer import load_topics, retrieve_topic_context

logger = logging.getLogger("uvicorn")

IDLE_HEARTBEAT_SECONDS = 30
MAX_RESPONSE_STORE_LENGTH = 8000

SYSTEM_PROMPT = """# 角色设定

你是「小鱼」🐟，SparkOffer 的 AI 学习伙伴 —— 一个刚拿到大厂 offer 的温柔学姐。
你的核心定位：**陪伴 + 教练**。陪伴用户的情绪和学习节奏，教练用户的薄弱点和成长方向。

## 双档运行（最重要）

根据当前对话内容**切换档位**，不要混用：

### 档位 1 — 关怀/陪伴档
触发：闲聊、情绪表达、问候、轻量咨询、用户主动找你聊
风格：完整学姐风格 — 软语气词（"呀""呢""哦""嘛"）、可爱系 emoji（🐟✨🌸💕🎀）、生活化表达
示例：
> 用户："最近有点焦虑..."
> 你："面试季压力是真的大呀 🌸 不过你能主动来准备就已经超棒了～
>      要不要我帮你看下最近的训练进度？心里有数会安心很多哦～"

### 档位 2 — 技术解释档
触发：用户问技术概念、原理、实现细节、面试题
风格：保持温柔语气，但**比喻必须精准，专业术语不能被替换**
- 错误 ❌：「Redis 像小鱼一样游来游去存数据 🐟」（不准确，丢失"内存"核心）
- 正确 ✅：「Redis 是内存数据库哦～你可以把它想象成一个超大的字典，键值对都放内存里，所以读写才这么快～」

**两档过渡要自然**：用户问技术问题时，开头可以保留一句关怀（"这块确实有点绕，我帮你理一下哈～"），然后立刻切到精准模式。

## 工具调用决策树

不是所有问题都要调工具，**先判断意图再决定**：

| 用户意图 | 是否调工具 | 调什么 |
|---|---|---|
| "我的 XX 怎样" / "薄弱点" / "复习什么" / "进步多少" | ✅ 必调 | `get_full_profile` / `get_weak_points_detail` / `get_due_reviews` |
| "上次那道 XX 题" / "之前学过 XX 吗" | ✅ 必调 | `search_history` / `search_knowledge_memory` |
| "什么是 XX" / "XX 怎么实现" / "XX 和 YY 区别" | ❌ 不调 | 直接讲技术（档位 2）|
| 语气焦虑但意图模糊 | 🤔 先共情 | 共情后问"要不要看看你最近的训练情况？" |
| 想训练但没说领域 | ❌ 不调 | 引导用 `start_interview` 或 `navigate` 到首页 |

**工具返回空数据时**：温柔说明（"诶？好像还没找到相关记录呢，你之前是用什么关键词搜的呀？"），**绝不硬编内容、绝不编造数据**。

## 平台功能导航（用户问"在哪""怎么找"时用）

- `/` — 首页（选择训练模式）
- `/profile` — 我的画像（掌握度、薄弱点、得分趋势）
- `/knowledge` — 知识库（核心知识、高频题、自动沉淀）
- `/graph` — 题目图谱（已训练题目关联可视化）
- `/history` — 历史记录
- `/favorites` — 收藏夹
- `/algorithm` — 算法解题
- `/job-prep` — JD 备面
- `/recording` — 录音复盘

## 禁止行为（硬性约束）

- ❌ 用"亲""宝""亲亲"等电商客服用语
- ❌ 说"我只是一个 AI" / "作为 AI 助手"这类自降身份的话
- ❌ 用户没问的时候主动推销功能（违背"陪伴"定位）
- ❌ 过度撒娇或太幼稚 — 你是靠谱学姐，不是萌妹
- ❌ 用可爱比喻稀释技术准确性（见档位 2 正反例）
- ❌ 编造数据 — 工具没查到就如实说没查到
- ❌ 每句都堆语气词或 emoji（过度反而做作，留白才显自然）

## 回复原则

1. 中文回复
2. 拿到工具数据后给**洞察**而不是**罗列** — "你 Redis 平均 7.2 分，但最近两次都掉到 5 分以下，是不是这周训练有点累呀？"比"你 Redis 平均 7.2 分"好 100 倍
3. 建议要**具体可执行** — "今晚花 20 分钟刷下 Redis 持久化相关题"，不要"建议你多练习"
4. 焦虑/沮丧场景 — **先共情，再给建议**，不要上来就讲方法论
5. 引导功能要**自然** — 像学姐随口提一句，不像产品经理推销
6. 保持温柔但不失专业 — 你是靠谱的学姐，不是只会安慰的人"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": "导航用户到指定页面",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "enum": [
                            "/", "/profile", "/history", "/knowledge", "/graph",
                            "/favorites", "/algorithm", "/algorithm/collection",
                            "/job-prep", "/recording",
                        ],
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_interview",
            "description": "开始一场面试训练",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["resume", "topic_drill", "job_prep", "recording"]},
                    "topic": {"type": "string", "description": "训练领域（仅 topic_drill 模式需要）"},
                },
                "required": ["mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_profile",
            "description": "查询用户的学习画像和掌握度",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_history",
            "description": "搜索面试历史记录",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "mode": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_topics",
            "description": "列出所有可用的训练领域",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_favorites",
            "description": "查看用户的收藏题目",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_session_detail",
            "description": "查看某次面试的详细信息，包括题目、得分、评价和薄弱点",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "面试记录ID"},
                },
                "required": ["session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_score_trends",
            "description": "查询某个领域的得分趋势，返回历次面试的得分变化",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "训练领域（可选，不传则返回全部领域）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_training_stats",
            "description": "查询训练统计概览：各模式训练次数、各领域训练分布、总体进度",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_favorites_detail",
            "description": "搜索收藏题目的详细信息，包括题目内容、用户答案、参考答案、得分和评价",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "按领域筛选"},
                    "tag": {"type": "string", "description": "按标签筛选"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_trained_topics",
            "description": "列出用户实际训练过的所有领域（有面试记录的）",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_due_reviews",
            "description": "查看到期需要复习的薄弱点（基于间隔重复算法），包括下次复习日期和难度系数",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "按领域筛选（可选）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_algorithm_cards",
            "description": "搜索用户的算法题收藏，支持按难度、标签、关键词筛选",
            "parameters": {
                "type": "object",
                "properties": {
                    "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"], "description": "按难度筛选"},
                    "tag": {"type": "string", "description": "按标签筛选"},
                    "search": {"type": "string", "description": "搜索关键词（匹配标题和题目描述）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_full_profile",
            "description": "获取用户完整画像，包括思维模式分析（优势和短板）、沟通风格（习惯和建议）、已改善的薄弱点等深度信息",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_memory",
            "description": "语义搜索用户的历史训练记忆，找到与查询最相关的历史问答、薄弱点和洞察",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索内容（自然语言）"},
                    "topic": {"type": "string", "description": "限定领域（可选）"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_session_transcript",
            "description": "获取某次面试的完整对话记录（面试官和用户的所有对话）",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "面试记录ID"},
                },
                "required": ["session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weak_points_detail",
            "description": "获取所有薄弱点的详细信息，包括出现次数、所属领域、是否已改善、间隔重复状态（下次复习日期、难度系数、连续正确次数）",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "按领域筛选（可选）"},
                    "include_improved": {"type": "boolean", "description": "是否包含已改善的薄弱点，默认false"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_knowledge_base",
            "description": "查询某个领域的知识库内容，获取核心知识点和参考材料",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "领域标识（如 java_core, python_backend）"},
                    "query": {"type": "string", "description": "查询内容（自然语言）"},
                },
                "required": ["topic", "query"],
            },
        },
    },
]


async def _execute_tool(name: str, args: dict, user_id: str) -> dict:
    """Execute a tool and return the result."""
    if name == "navigate":
        return {"action": "navigate", "path": args["path"]}

    elif name == "start_interview":
        mode = args["mode"]
        if mode in ("job_prep", "recording"):
            path = "/job-prep" if mode == "job_prep" else "/recording"
            return {"action": "navigate", "path": path}
        return {"action": "start_interview", "mode": mode, "topic": args.get("topic")}

    elif name == "check_profile":
        profile = get_profile(user_id)
        if not profile:
            return {"data": "用户还没有训练记录，建议先进行一次面试训练。"}
        stats = profile.get("stats", {})
        mastery = profile.get("topic_mastery", {})
        weak = profile.get("weak_points", [])[:5]
        summary = f"总训练 {stats.get('total_sessions', 0)} 次，综合均分 {stats.get('avg_score', '-')}"
        if mastery:
            top = sorted(mastery.items(), key=lambda x: x[1].get("score", 0), reverse=True)[:3]
            summary += "。掌握度最高: " + ", ".join(f"{t}({d.get('score',0)}分)" for t, d in top)
        if weak:
            pts = [w.get("point", str(w)) if isinstance(w, dict) else str(w) for w in weak]
            summary += "。薄弱点: " + ", ".join(pts)
        return {"data": summary}

    elif name == "search_history":
        result = list_sessions(user_id=user_id, topic=args.get("topic"), mode=args.get("mode"), limit=5)
        sessions = result.get("sessions", []) if isinstance(result, dict) else result
        if not sessions:
            return {"data": "没有找到匹配的面试记录。"}
        lines = []
        for s in sessions:
            line = f"- {s.get('created_at', '?')[:10]} | {s.get('mode', '?')} | {s.get('topic', '-')}"
            if s.get("avg_score"):
                line += f" | 得分 {s['avg_score']}"
            lines.append(line)
        return {"data": "最近的面试记录:\n" + "\n".join(lines)}

    elif name == "list_topics":
        topics = load_topics(user_id)
        if not topics:
            return {"data": "还没有训练领域，可以在题库页面添加。"}
        names = [f"{v.get('name', k)} ({k})" for k, v in topics.items()]
        return {"data": "可用训练领域: " + ", ".join(names)}

    elif name == "list_favorites":
        result = list_favorites(user_id=user_id, topic=args.get("topic"), limit=5)
        favs = result.get("favorites", []) if isinstance(result, dict) else result
        if not favs:
            return {"data": "收藏夹为空。"}
        lines = [f"- Q: {f.get('question', '?')[:60]}" for f in favs]
        return {"data": f"收藏了 {len(favs)} 条:\n" + "\n".join(lines)}

    elif name == "get_session_detail":
        session = get_session(args["session_id"], user_id=user_id)
        if not session:
            return {"data": "未找到该面试记录。"}
        lines = [f"面试详情 — {session.get('mode', '?')} | {session.get('topic', '-')} | {session.get('created_at', '?')[:10]}"]
        overall = session.get("overall", {})
        if overall.get("avg_score"):
            lines.append(f"综合得分: {overall['avg_score']}")
        questions = session.get("questions", [])
        scores = session.get("scores", [])
        if questions:
            lines.append(f"\n共 {len(questions)} 道题:")
            for i, q in enumerate(questions):
                q_text = q.get("question", str(q))[:80]
                score_info = ""
                if i < len(scores) and isinstance(scores[i], dict):
                    score_info = f" (得分: {scores[i].get('score', '?')})"
                elif i < len(scores):
                    score_info = f" (得分: {scores[i]})"
                lines.append(f"  {i+1}. {q_text}{score_info}")
        weak = session.get("weak_points", [])
        if weak:
            pts = [w.get("point", str(w)) if isinstance(w, dict) else str(w) for w in weak[:5]]
            lines.append(f"\n薄弱点: {', '.join(pts)}")
        if session.get("review"):
            review_text = session["review"][:300]
            lines.append(f"\n评价摘要: {review_text}")
        return {"data": "\n".join(lines)}

    elif name == "get_score_trends":
        topic = args.get("topic")
        profile = get_profile(user_id)
        if not profile:
            return {"data": "还没有训练记录。"}
        history = profile.get("stats", {}).get("score_history", [])
        if topic:
            history = [h for h in history if h.get("topic") == topic]
        if not history:
            return {"data": f"{'该领域' if topic else ''}没有得分记录。"}
        lines = ["得分趋势:"]
        for h in history[-15:]:
            line = f"- {h.get('date', '?')[:10]} | {h.get('mode', '?')} | {h.get('topic', '-')} | 得分 {h.get('avg_score', '?')}"
            lines.append(line)
        if len(history) >= 2:
            first_score = history[0].get("avg_score", 0)
            last_score = history[-1].get("avg_score", 0)
            if first_score and last_score:
                diff = last_score - first_score
                trend = "上升" if diff > 0 else "下降" if diff < 0 else "持平"
                lines.append(f"\n整体趋势: {trend} ({'+' if diff > 0 else ''}{diff:.1f}分)")
        return {"data": "\n".join(lines)}

    elif name == "get_training_stats":
        profile = get_profile(user_id)
        if not profile:
            return {"data": "还没有训练记录，建议先进行一次面试训练。"}
        stats = profile.get("stats", {})
        mastery = profile.get("topic_mastery", {})
        lines = [
            "训练统计概览:",
            f"- 总训练次数: {stats.get('total_sessions', 0)}",
            f"  · 简历面试: {stats.get('resume_sessions', 0)} 次",
            f"  · 专项训练: {stats.get('drill_sessions', 0)} 次",
            f"  · JD 备面: {stats.get('job_prep_sessions', 0)} 次",
            f"- 综合均分: {stats.get('avg_score', '-')}",
        ]
        if mastery:
            lines.append("\n各领域掌握度:")
            sorted_m = sorted(mastery.items(), key=lambda x: x[1].get("score", 0), reverse=True)
            for topic_name, data in sorted_m:
                score = data.get("score", 0)
                level = data.get("level", "?")
                lines.append(f"  · {topic_name}: {score}分 (L{level})")
        weak = profile.get("weak_points", [])
        if weak:
            pts = [w.get("point", str(w)) if isinstance(w, dict) else str(w) for w in weak[:8]]
            lines.append(f"\n主要薄弱点: {', '.join(pts)}")
        strong = profile.get("strong_points", [])
        if strong:
            pts = [s.get("point", str(s)) if isinstance(s, dict) else str(s) for s in strong[:5]]
            lines.append(f"强项: {', '.join(pts)}")
        return {"data": "\n".join(lines)}

    elif name == "search_favorites_detail":
        result = list_favorites(user_id=user_id, topic=args.get("topic"), tag=args.get("tag"), limit=10)
        favs = result.get("items", []) if isinstance(result, dict) else result
        total = result.get("total", len(favs)) if isinstance(result, dict) else len(favs)
        if not favs:
            return {"data": "没有找到匹配的收藏题目。"}
        lines = [f"收藏题目详情 (共 {total} 条，显示前 {len(favs)} 条):"]
        for i, f in enumerate(favs, 1):
            lines.append(f"\n{i}. {f.get('question', '?')[:100]}")
            if f.get("topic"):
                lines.append(f"   领域: {f['topic']}")
            if f.get("score") is not None:
                lines.append(f"   得分: {f['score']}")
            if f.get("difficulty"):
                lines.append(f"   难度: {f['difficulty']}")
            if f.get("assessment"):
                lines.append(f"   评价: {f['assessment'][:100]}")
            if f.get("tags"):
                lines.append(f"   标签: {', '.join(f['tags'])}")
        return {"data": "\n".join(lines)}

    elif name == "list_trained_topics":
        topics = list_distinct_topics(user_id=user_id)
        if not topics:
            return {"data": "还没有任何训练记录。"}
        return {"data": "已训练过的领域: " + ", ".join(topics)}

    elif name == "get_due_reviews":
        topic = args.get("topic")
        due = get_due_reviews(user_id, topic=topic)
        if not due:
            return {"data": f"{'该领域' if topic else ''}没有到期需要复习的薄弱点，继续保持！"}
        lines = [f"到期需要复习的薄弱点 (共 {len(due)} 个):"]
        for wp in due:
            sr = wp.get("sr", {})
            line = f"- {wp.get('point', '?')}"
            if wp.get("topic"):
                line += f" [{wp['topic']}]"
            line += f" (出现{wp.get('times_seen', 1)}次"
            if sr.get("last_score") is not None:
                line += f", 上次得分{sr['last_score']}"
            line += f", 难度系数{sr.get('ease_factor', 2.5)}"
            line += f", 已连续正确{sr.get('repetitions', 0)}次)"
            lines.append(line)
        return {"data": "\n".join(lines)}

    elif name == "search_algorithm_cards":
        result = list_algorithm_cards(
            user_id=user_id,
            difficulty=args.get("difficulty"),
            tag=args.get("tag"),
            search=args.get("search"),
            limit=10,
        )
        items = result.get("items", [])
        total = result.get("total", 0)
        if not items:
            return {"data": "没有找到匹配的算法题收藏。"}
        lines = [f"算法题收藏 (共 {total} 题，显示前 {len(items)} 题):"]
        diff_map = {"easy": "简单", "medium": "中等", "hard": "困难"}
        for i, it in enumerate(items, 1):
            line = f"\n{i}. {it.get('title', '?')}"
            meta_parts = []
            if it.get("difficulty"):
                meta_parts.append(diff_map.get(it["difficulty"], it["difficulty"]))
            if it.get("language"):
                meta_parts.append(it["language"])
            if it.get("tags"):
                meta_parts.append(f"标签: {', '.join(it['tags'])}")
            if meta_parts:
                line += f" ({', '.join(meta_parts)})"
            lines.append(line)
            if it.get("source_url"):
                lines.append(f"   来源: {it['source_url']}")
            if it.get("note"):
                lines.append(f"   笔记: {it['note'][:80]}")
        return {"data": "\n".join(lines)}

    elif name == "get_full_profile":
        profile = _load_profile(user_id)
        if not profile or not profile.get("stats", {}).get("total_sessions"):
            return {"data": "用户还没有训练记录，建议先进行一次面试训练。"}
        lines = []

        # Thinking patterns
        tp = profile.get("thinking_patterns", {})
        if tp.get("strengths") or tp.get("gaps"):
            lines.append("思维模式分析:")
            if tp.get("strengths"):
                lines.append("  优势: " + "; ".join(tp["strengths"][:5]))
            if tp.get("gaps"):
                lines.append("  短板: " + "; ".join(tp["gaps"][:5]))

        # Communication
        comm = profile.get("communication", {})
        if comm.get("style") or comm.get("habits") or comm.get("suggestions"):
            lines.append("\n沟通风格:")
            if comm.get("style"):
                lines.append(f"  风格: {comm['style']}")
            if comm.get("habits"):
                lines.append("  习惯: " + "; ".join(comm["habits"][:5]))
            if comm.get("suggestions"):
                lines.append("  建议: " + "; ".join(comm["suggestions"][:5]))

        # Topic mastery details
        mastery = profile.get("topic_mastery", {})
        if mastery:
            lines.append("\n各领域掌握度详情:")
            for topic_name, data in sorted(mastery.items(), key=lambda x: x[1].get("score", 0), reverse=True):
                line = f"  · {topic_name}: {data.get('score', 0)}分"
                if data.get("notes"):
                    line += f" — {data['notes'][:60]}"
                lines.append(line)

        # Improved weak points
        improved = [w for w in profile.get("weak_points", []) if w.get("improved")]
        if improved:
            lines.append(f"\n已改善的薄弱点 ({len(improved)} 个):")
            for w in improved[:8]:
                lines.append(f"  ✓ {w.get('point', '?')}" + (f" [{w.get('topic', '')}]" if w.get("topic") else ""))

        # Strong points
        strong = profile.get("strong_points", [])
        if strong:
            lines.append(f"\n强项 ({len(strong)} 个):")
            for s in strong[:8]:
                lines.append(f"  ★ {s.get('point', '?')}" + (f" [{s.get('topic', '')}]" if s.get("topic") else ""))

        # Stats summary
        stats = profile.get("stats", {})
        lines.append(f"\n训练统计: 共 {stats.get('total_sessions', 0)} 次, 均分 {stats.get('avg_score', '-')}")
        lines.append(f"  简历面试 {stats.get('resume_sessions', 0)} 次 (均分 {stats.get('resume_avg_score', '-')})")
        lines.append(f"  专项训练 {stats.get('drill_sessions', 0)} 次 (均分 {stats.get('drill_avg_score', '-')})")
        lines.append(f"  JD 备面 {stats.get('job_prep_sessions', 0)} 次 (均分 {stats.get('job_prep_avg_score', '-')})")

        if not lines:
            return {"data": "画像数据较少，建议多做几次训练。"}
        return {"data": "\n".join(lines)}

    elif name == "search_knowledge_memory":
        query = args.get("query", "")
        topic = args.get("topic")
        if not query:
            return {"data": "请提供搜索内容。"}
        try:
            results = await search_memory(query, user_id, topic=topic, top_k=8)
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
            return {"data": "语义搜索暂不可用（可能未配置 Embedding 模型）。"}
        if not results:
            return {"data": "没有找到相关的历史记忆。"}
        type_labels = {
            "session_summary": "训练总结",
            "weak_point": "薄弱点",
            "insight": "洞察",
        }
        lines = [f"语义搜索结果 (与「{query}」最相关的 {len(results)} 条记忆):"]
        for i, r in enumerate(results, 1):
            label = type_labels.get(r["chunk_type"], r["chunk_type"])
            line = f"\n{i}. [{label}] {r['content'][:120]}"
            meta_parts = []
            if r.get("topic"):
                meta_parts.append(r["topic"])
            if r.get("created_at"):
                meta_parts.append(r["created_at"][:10])
            meta_parts.append(f"相关度 {r['score']:.2f}")
            line += f" ({', '.join(meta_parts)})"
            lines.append(line)
        return {"data": "\n".join(lines)}

    elif name == "get_session_transcript":
        session = get_session(args["session_id"], user_id=user_id)
        if not session:
            return {"data": "未找到该面试记录。"}
        transcript = session.get("transcript", [])
        if not transcript:
            return {"data": "该面试记录没有对话内容。"}
        lines = [
            f"面试对话记录 — {session.get('mode', '?')} | {session.get('topic', '-')} | {session.get('created_at', '?')[:10]}",
            f"共 {len(transcript)} 条消息:",
            "",
        ]
        for msg in transcript:
            role = "面试官" if msg.get("role") == "assistant" else "你"
            content = msg.get("content", "")
            # Truncate very long messages
            if len(content) > 500:
                content = content[:500] + "...(截断)"
            lines.append(f"[{role}] {content}")
        return {"data": "\n".join(lines)}

    elif name == "get_weak_points_detail":
        profile = _load_profile(user_id)
        if not profile:
            return {"data": "还没有训练记录。"}
        weak_points = profile.get("weak_points", [])
        topic = args.get("topic")
        include_improved = args.get("include_improved", False)

        if topic:
            weak_points = [w for w in weak_points if w.get("topic") == topic]
        if not include_improved:
            weak_points = [w for w in weak_points if not w.get("improved")]

        if not weak_points:
            return {"data": f"{'该领域' if topic else ''}没有{'未改善的' if not include_improved else ''}薄弱点。"}

        lines = [f"薄弱点详情 (共 {len(weak_points)} 个):"]
        for i, wp in enumerate(weak_points, 1):
            line = f"\n{i}. {wp.get('point', '?')}"
            if wp.get("improved"):
                line += " ✓已改善"
            lines.append(line)
            details = []
            if wp.get("topic"):
                details.append(f"领域: {wp['topic']}")
            details.append(f"出现次数: {wp.get('times_seen', 1)}")
            if wp.get("first_seen"):
                details.append(f"首次发现: {wp['first_seen'][:10]}")
            if wp.get("last_seen"):
                details.append(f"最近出现: {wp['last_seen'][:10]}")
            lines.append(f"   {' | '.join(details)}")

            sr = wp.get("sr", {})
            if sr:
                sr_parts = []
                if sr.get("next_review"):
                    sr_parts.append(f"下次复习: {sr['next_review']}")
                sr_parts.append(f"间隔: {sr.get('interval_days', 1)}天")
                sr_parts.append(f"难度系数: {sr.get('ease_factor', 2.5)}")
                sr_parts.append(f"连续正确: {sr.get('repetitions', 0)}次")
                if sr.get("last_score") is not None:
                    sr_parts.append(f"上次得分: {sr['last_score']}")
                lines.append(f"   SR状态: {' | '.join(sr_parts)}")

        return {"data": "\n".join(lines)}

    elif name == "query_knowledge_base":
        topic = args.get("topic", "")
        query = args.get("query", "")
        if not topic or not query:
            return {"data": "请提供领域和查询内容。"}
        try:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(retrieve_topic_context, topic, query, user_id, 5)
                results = future.result(timeout=60.0)
        except concurrent.futures.TimeoutError:
            logger.warning(f"Knowledge base query timed out (60s) for topic={topic}")
            return {"data": f"知识库查询超时（索引可能正在构建），请稍后重试。"}
        except Exception as e:
            logger.warning(f"Knowledge base query failed: {e}")
            return {"data": f"知识库查询失败，该领域可能还没有知识内容。"}
        if not results:
            return {"data": f"在「{topic}」知识库中没有找到与「{query}」相关的内容。"}
        lines = [f"「{topic}」知识库检索结果 (共 {len(results)} 条):"]
        for i, chunk in enumerate(results, 1):
            lines.append(f"\n--- 片段 {i} ---\n{chunk[:500]}")
        return {"data": "\n".join(lines)}

    return {"data": "未知操作"}


async def stream_assistant_chat(
    message: str, user_id: str
) -> AsyncGenerator[str, None]:
    """Stream SSE events for assistant chat with tool calling."""
    from backend.storage.assistant_chats import load_history

    llm = get_langchain_llm()
    llm_with_tools = llm.bind_tools(TOOLS)

    # ── Dynamic system prompt: inject user profile ──
    profile_summary = get_profile_summary(user_id)
    dynamic_prompt = SYSTEM_PROMPT + f"""

## 当前用户画像

{profile_summary}

**重要：** 你已经了解这位同学的情况，回复时要体现你对 ta 的了解。比如知道 ta 的薄弱点可以主动关心进展，知道 ta 的强项可以适时肯定。不要重复告诉用户你知道他们的画像，自然地融入对话即可。"""

    # ── Load history server-side ──
    history = load_history(user_id, limit=30)
    lc_messages = [{"role": "system", "content": dynamic_prompt}]
    for m in history:
        lc_messages.append({"role": m["role"], "content": m["content"]})
    lc_messages.append({"role": "user", "content": message})

    # ── Persist the user message ──
    if message:
        save_message(user_id, "user", message)

    final_content = ""
    max_tool_rounds = 3
    for round_idx in range(max_tool_rounds):
        # Stream the LLM response; detect tool calls from chunks
        full_response = None
        has_tool_chunks = False
        streamed_content = ""

        try:
            aiter = llm_with_tools.astream(lc_messages).__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(aiter.__anext__(), timeout=IDLE_HEARTBEAT_SECONDS)
                    full_response = chunk if full_response is None else full_response + chunk

                    if hasattr(chunk, "tool_call_chunks") and chunk.tool_call_chunks:
                        has_tool_chunks = True

                    token = chunk.content if hasattr(chunk, "content") else ""
                    if token and not has_tool_chunks:
                        streamed_content += token
                        yield f"data: {json.dumps({'type': 'token', 'content': token}, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
                except StopAsyncIteration:
                    break
        except Exception as e:
            logger.error("Assistant LLM call failed: %s", e)
            if not streamed_content:
                streamed_content = "抱歉，AI 服务暂时不可用，请稍后重试。"
                yield f"data: {json.dumps({'type': 'token', 'content': streamed_content}, ensure_ascii=False)}\n\n"
            final_content = streamed_content
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            break

        if not has_tool_chunks:
            final_content = streamed_content
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            break

        # Tool calling round — extract tool_calls from accumulated response
        tool_calls = full_response.tool_calls if full_response and hasattr(full_response, "tool_calls") else []
        if not tool_calls:
            final_content = full_response.content if full_response else "处理完成。"
            if not streamed_content:
                yield f"data: {json.dumps({'type': 'token', 'content': final_content}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            break

        lc_messages.append({
            "role": "assistant",
            "content": full_response.content or "",
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])},
                }
                for tc in tool_calls
            ],
        })

        # 本轮的多个 tool_call 并发执行：每个工具都是独立的 async DB/向量/索引
        # 读取，无共享可变状态，墙钟时间从"各延迟之和"降到"取最大值"。结果按
        # 原顺序消费，保证 action 事件顺序确定、tool 消息与 tool_call_id 对齐。
        results = await asyncio.gather(
            *(_execute_tool(tc["name"], tc["args"], user_id) for tc in tool_calls)
        )

        for tc, result in zip(tool_calls, results):
            # If it's a frontend action, emit it
            if "action" in result:
                yield f"data: {json.dumps({'type': 'action', **result}, ensure_ascii=False)}\n\n"

            # Feed result back to LLM
            tool_content = result.get("data", json.dumps(result, ensure_ascii=False))
            lc_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": str(tool_content),
            })
    else:
        # Fallback if max rounds exceeded
        final_content = "操作完成。"
        yield f"data: {json.dumps({'type': 'token', 'content': final_content}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    # ── Persist assistant reply ──
    if final_content:
        save_message(user_id, "assistant", final_content[:MAX_RESPONSE_STORE_LENGTH])

    # ── Lightweight preference extraction (async, non-blocking) ──
    if message:
        _extract_and_update_preferences(message, user_id)


# ── Preference extraction (keyword-based, no LLM) ──

_PREF_PATTERNS = [
    # response_style
    (re.compile(r"(回答|回复|说话).{0,4}(简洁|简短|精炼|少说点)", re.I), "response_style", "简洁"),
    (re.compile(r"(回答|回复|说话).{0,4}(详细|多说|展开|具体)", re.I), "response_style", "详细"),
    # preferred_difficulty
    (re.compile(r"(难度|题目).{0,4}(高|难|困难|提高|加大)", re.I), "preferred_difficulty", "困难"),
    (re.compile(r"(难度|题目).{0,4}(低|简单|容易|降低)", re.I), "preferred_difficulty", "简单"),
    (re.compile(r"(难度|题目).{0,4}(中等|适中)", re.I), "preferred_difficulty", "中等"),
    # interview_pace
    (re.compile(r"(节奏|速度).{0,4}(快|加快)", re.I), "interview_pace", "快"),
    (re.compile(r"(节奏|速度).{0,4}(慢|放慢)", re.I), "interview_pace", "慢"),
    # feedback_style
    (re.compile(r"(直接|直白|不用客气).{0,4}(指出|说|告诉|反馈)", re.I), "feedback_style", "直接"),
    (re.compile(r"(反馈|指出).{0,4}(直接|直白)", re.I), "feedback_style", "直接"),
    (re.compile(r"(温柔|鼓励|委婉).{0,4}(反馈|说|指出)", re.I), "feedback_style", "鼓励型"),
]

_FOCUS_PATTERN = re.compile(
    r"(?:重点|专注|集中|多练).{0,4}(?:练习|训练|学习|复习|搞定|攻克)\s*(.{2,15})",
    re.I,
)


def _extract_and_update_preferences(text: str, user_id: str):
    """Extract explicit preferences from user message and update profile."""
    profile = _load_profile(user_id)
    prefs = profile.setdefault("preferences", {
        "response_style": "", "preferred_difficulty": "",
        "focus_topics": [], "interview_pace": "",
        "feedback_style": "", "custom_notes": [],
    })

    changed = False

    # Simple pattern matching
    for pattern, key, value in _PREF_PATTERNS:
        if pattern.search(text):
            if prefs.get(key) != value:
                prefs[key] = value
                changed = True
                logger.info(f"Preference updated: {key}={value} for user {user_id}")

    # Focus topic extraction
    match = _FOCUS_PATTERN.search(text)
    if match:
        topic = match.group(1).strip().rstrip("的吧呢呀哦嘛吗了")
        if topic and topic not in prefs.get("focus_topics", []):
            prefs.setdefault("focus_topics", []).append(topic)
            changed = True
            logger.info(f"Focus topic added: {topic} for user {user_id}")

    if changed:
        _save_profile(profile, user_id)


# ── Welcome back message ──

def generate_welcome_back(user_id: str) -> str | None:
    """Generate a personalized welcome-back message, or None for new users."""
    profile = _load_profile(user_id)
    stats = profile.get("stats", {})
    if not stats.get("total_sessions"):
        return None

    parts = []

    # Last session info
    result = list_sessions(user_id=user_id, limit=1)
    sessions = result.get("sessions", []) if isinstance(result, dict) else result
    if sessions:
        last = sessions[0]
        topic = last.get("topic", "")
        score = last.get("avg_score")
        date_str = last.get("created_at", "")[:10]
        if topic:
            line = f"上次你练习了「{topic}」"
            if score:
                line += f"，得了 {score} 分"
            if date_str:
                line += f"（{date_str}）"
            parts.append(line + "～")

    # Due reviews
    try:
        due = get_due_reviews(user_id)
        if due:
            parts.append(f"对了，你有 **{len(due)} 个知识点**到期需要复习了，要不要现在看看？")
    except Exception:
        pass

    # Score trend encouragement
    score_history = stats.get("score_history", [])
    if len(score_history) >= 3:
        recent = [h.get("avg_score", 0) for h in score_history[-3:] if h.get("avg_score")]
        if len(recent) >= 2 and recent[-1] > recent[0]:
            parts.append("最近的分数一直在进步呢，继续保持！💪")

    if not parts:
        return None

    return "欢迎回来呀～ 🐟\n" + "\n".join(parts)
