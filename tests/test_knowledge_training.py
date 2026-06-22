from backend import knowledge_training as kt


def _long(text: str) -> str:
    return (text + "。") * 20


def test_split_markdown_sections_preserves_header_path():
    text = f"""# Python

## GIL

{_long("GIL 是 CPython 的全局解释器锁，用来保护解释器内部状态")}

### 边界

{_long("GIL 不等于 Python 不能并发，IO 密集任务仍然可以通过线程切换获得收益")}
"""

    sections = kt.split_knowledge_sections("README.md", text)

    assert [s.header_path for s in sections] == ["Python > GIL", "Python > GIL > 边界"]
    assert all(s.filename == "README.md" for s in sections)


def test_markdown_headings_inside_code_fences_are_not_sections():
    text = f"""## Docker 存储

Docker 有三类常见挂载方式：volume、bind mount 和 tmpfs。tmpfs 位于内存中，适合临时缓存。

```bash
# tmpfs
docker run --tmpfs /app/cache myapp
docker run --mount type=tmpfs,target=/app/cache,tmpfs-size=100m myapp
```

{_long("敏感临时数据可以使用 tmpfs，因为它不会像 volume 一样持久化到磁盘")}
"""

    sections = kt.split_knowledge_sections("docker.md", text)

    assert len(sections) == 1
    assert sections[0].header_path == "Docker 存储"
    assert "# tmpfs" in sections[0].content


def test_plan_sections_are_filtered_from_training_pool(monkeypatch, tmp_path):
    root = tmp_path / "data" / "users" / "u" / "knowledge" / "python"
    root.mkdir(parents=True)
    (root / "README.md").write_text(
        f"""## 面试前 2 周冲刺计划

- **Day 1-3**：数组 + 字符串（25 题）
- **Day 4-5**：链表（10 题）
- **Day 6-8**：树（15 题）

## GIL 的作用

{_long("GIL 是 CPython 的全局解释器锁，用来保护解释器内部状态，同时限制多线程并行执行 Python 字节码")}
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(kt, "load_topics", lambda user_id: {"python": {"dir": "python", "name": "Python"}})
    monkeypatch.setattr(kt.settings, "base_dir", tmp_path)

    sections = kt.collect_topic_sections("u", "python")

    assert [s.header_path for s in sections] == ["GIL 的作用"]


def test_sample_topic_sections_is_reproducible(monkeypatch, tmp_path):
    root = tmp_path / "data" / "users" / "u" / "knowledge" / "python"
    root.mkdir(parents=True)
    (root / "README.md").write_text(
        "\n\n".join(
            f"## Section {i}\n\n{_long(f'第 {i} 个知识点包含足够长的正文，可以被训练场抽样')}"
            for i in range(6)
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(kt, "load_topics", lambda user_id: {"python": {"dir": "python", "name": "Python"}})
    monkeypatch.setattr(kt.settings, "base_dir", tmp_path)

    first, first_seed = kt.sample_topic_sections("u", "python", 3, seed="fixed")
    second, second_seed = kt.sample_topic_sections("u", "python", 3, seed="fixed")

    assert first_seed == second_seed == "fixed"
    assert [s.header_path for s in first] == [s.header_path for s in second]
    assert len(first) == 3


def test_normalize_training_cards_fills_topic_id_and_source_refs():
    sections = [
        kt.KnowledgeSection(
            filename="README.md",
            header_path="Python > GIL",
            content=_long("GIL 相关知识"),
        )
    ]
    raw = """[
      {
        "title": "GIL 的作用",
        "knowledge": ["保护解释器内部状态", "同一时刻通常只有一个线程执行 Python 字节码"],
        "example": "CPU 密集型多线程不会线性加速。",
        "question": "GIL 主要解决什么问题？",
        "answer": "它保护 CPython 解释器内部状态，避免多个线程同时执行字节码时破坏对象状态。",
        "tags": ["并发"]
      }
    ]"""

    cards = kt.normalize_training_cards(raw, topic="python", sections=sections)

    assert len(cards) == 1
    card = cards[0]
    assert card["topic"] == "python"
    assert card["id"].startswith("kt-")
    assert card["source_refs"] == [{"filename": "README.md", "header_path": "Python > GIL"}]


def test_normalize_training_cards_rejects_fragmented_schedule_card():
    sections = [
        kt.KnowledgeSection(
            filename="README.md",
            header_path="面试前 2 周冲刺计划",
            content="- Day 1-3：数组 + 字符串，25 题\n- Day 4-5：链表，10 题",
        )
    ]
    raw = """[
      {
        "title": "LeetCode 热题 100 面试前两周冲刺安排",
        "knowledge": ["Day 1-3：数组 + 字符串，25 题", "Day 4-5：链表，10 题"],
        "example": "冲刺计划中，Day 9-11 安排动态规划 15 题。",
        "question": "面试前 2 周冲刺计划中，各阶段分别安排哪些题型和数量？",
        "answer": "Day 1-3 数组字符串 25 题，Day 4-5 链表 10 题。",
        "tags": ["计划"]
      }
    ]"""

    assert kt.normalize_training_cards(raw, topic="python", sections=sections) == []
