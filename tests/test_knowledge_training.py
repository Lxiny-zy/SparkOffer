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
        "answer": "**结论**：GIL 保证同一时刻只有一个线程执行字节码。**机制**：它保护 CPython 解释器内部状态，避免多线程并发修改对象引用计数。**易错点**：GIL 不影响 IO 密集任务的线程并发收益。",
        "mnemonic": "GIL 锁的是字节码执行权，不是锁掉所有并发",
        "tags": ["并发"]
      }
    ]"""

    cards = kt.normalize_training_cards(raw, topic="python", sections=sections)

    assert len(cards) == 1
    card = cards[0]
    assert card["topic"] == "python"
    assert card["id"].startswith("kt-")
    assert card["source_refs"] == [{"filename": "README.md", "header_path": "Python > GIL"}]
    assert card["mnemonic"] == "GIL 锁的是字节码执行权，不是锁掉所有并发"


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


def test_normalize_training_cards_formats_markdown_and_repairs_source_refs():
    sections = [
        kt.KnowledgeSection(
            filename="docker.md",
            header_path="Docker > volume",
            content=_long("Docker volume persists container data on disk and is suitable for durable state."),
        ),
        kt.KnowledgeSection(
            filename="docker.md",
            header_path="Docker > tmpfs",
            content=_long("Docker tmpfs stores temporary files in memory and is useful for cache or sensitive transient data."),
        ),
    ]
    raw = """[
      {
        "title": "Docker tmpfs 的适用场景",
        "knowledge": [
          "Docker tmpfs 会把临时文件系统挂载在内存中，适合缓存或敏感临时数据。",
          "`--tmpfs /app/cache` 是快速写法，`--mount type=tmpfs,target=/app/cache` 适合声明更多参数。",
          "tmpfs 不会像 volume 一样持久化到磁盘，容器删除后数据会消失。"
        ],
        "example": "```bash\\ndocker run --tmpfs /app/cache myapp\\n```",
        "question": "什么时候应该使用 Docker tmpfs，而不是 volume？",
        "answer": "**结论**：数据只需临时存在且希望避免落盘时用 tmpfs，需要跨容器生命周期保存时用 volume。**机制**：tmpfs 挂载在内存中，容器停止即消失；volume 由 Docker 管理并持久化到磁盘。**易错点**：把需要持久化的数据放进 tmpfs，重启后全部丢失。",
        "tags": ["Docker", "缓存"],
        "source_index": 2,
        "source_refs": [{"filename": "docker.md", "header_path": "Docker > volume"}]
      }
    ]"""

    cards = kt.normalize_training_cards(raw, topic="docker", sections=sections)

    assert len(cards) == 1
    card = cards[0]
    assert card["knowledge"].startswith("- Docker tmpfs")
    assert "\n- `--tmpfs /app/cache`" in card["knowledge"]
    assert "```bash" in card["example"]
    # source_index=2 优先级高于错误的 source_refs，修复后应指向 tmpfs
    assert card["source_refs"] == [{"filename": "docker.md", "header_path": "Docker > tmpfs"}]


def test_normalize_training_cards_preserves_valid_multi_source_refs():
    sections = [
        kt.KnowledgeSection(
            filename="docker.md",
            header_path="Docker > volume",
            content=_long("Docker volume persists container data on disk."),
        ),
        kt.KnowledgeSection(
            filename="docker.md",
            header_path="Docker > tmpfs",
            content=_long("Docker tmpfs stores data in memory."),
        ),
        kt.KnowledgeSection(
            filename="docker.md",
            header_path="Docker > bind mount",
            content=_long("Docker bind mount maps host directory to container."),
        ),
    ]
    raw = """[
      {
        "title": "Docker 存储对比",
        "knowledge": ["volume 持久化到磁盘，tmpfs 存在内存，bind mount 映射宿主目录。"],
        "example": "根据数据生命周期选择不同的存储方式。",
        "question": "Docker 的三种存储方式有什么区别？",
        "answer": "**结论**：三者按数据生命周期分工——volume 适合持久化，tmpfs 适合临时数据，bind mount 适合开发环境。**机制**：volume 由 Docker 管理落盘，tmpfs 只存在内存，bind mount 直接映射宿主目录。",
        "tags": ["Docker"],
        "source_refs": [
          {"filename": "docker.md", "header_path": "Docker > volume"},
          {"filename": "docker.md", "header_path": "Docker > tmpfs"},
          {"filename": "docker.md", "header_path": "Docker > bind mount"}
        ]
      }
    ]"""

    cards = kt.normalize_training_cards(raw, topic="docker", sections=sections)

    assert len(cards) == 1
    card = cards[0]
    # 所有 source_refs 都在 sections 中存在，应保留全部（最多3个）
    assert len(card["source_refs"]) == 3
    assert {"filename": "docker.md", "header_path": "Docker > volume"} in card["source_refs"]
    assert {"filename": "docker.md", "header_path": "Docker > tmpfs"} in card["source_refs"]
    assert {"filename": "docker.md", "header_path": "Docker > bind mount"} in card["source_refs"]


def test_sample_topic_sections_keeps_related_sections_contiguous(monkeypatch, tmp_path):
    root = tmp_path / "data" / "users" / "u" / "knowledge" / "docker"
    root.mkdir(parents=True)
    body = "\n\n".join(
        f"### Part {i}\n\n{_long(f'Docker tmpfs part {i} explains memory-backed cache behavior and mount parameters.')}"
        for i in range(6)
    )
    (root / "README.md").write_text(f"## Docker Storage\n\n{body}", encoding="utf-8")

    monkeypatch.setattr(kt, "load_topics", lambda user_id: {"docker": {"dir": "docker", "name": "Docker"}})
    monkeypatch.setattr(kt.settings, "base_dir", tmp_path)

    sections, _ = kt.sample_topic_sections("u", "docker", 3, seed="adjacent")
    parts = [int(section.header_path.rsplit(" ", 1)[1]) for section in sections]

    assert len(parts) == 3
    assert parts == list(range(parts[0], parts[0] + 3))


def test_looks_like_low_quality_card_does_not_reject_code_blocks():
    """Bug fix: 旧逻辑只过滤 ``` 开头的行，代码块内的短命令会被误判为碎片。"""
    title = "Docker volume 管理"
    knowledge = """- Docker volume 是独立于容器生命周期的持久化存储机制，删除容器后数据仍然保留。
- 使用 docker volume 相关命令管理卷，支持创建、列出、查看和删除操作。
```bash
docker volume ls
docker volume rm a
docker volume rm b
docker volume rm c
docker volume prune
docker system df
```
- 适合数据库文件等需要长期保存的关键数据，不应用于临时缓存或日志文件。"""
    example = "docker volume create mydata"
    question = "如何创建和管理 Docker volume？"
    answer = "**结论**：volume 全生命周期都有对应子命令管理。**机制**：`docker volume create` 创建新卷，`ls` 列出所有卷，`rm` 删除指定卷。**易错点**：卷独立于容器生命周期，删容器不会删卷，要用 `prune` 清理悬空卷。"

    is_low = kt._looks_like_low_quality_card(title, knowledge, example, question, answer)
    assert not is_low, "包含代码块的高质量卡片不应被误判为低质量"


def test_answer_without_layered_structure_is_rejected():
    """单层结论（够 40 字但无 结论/机制/易错 分层、也不够长）没有回忆量，应被丢弃。"""
    assert kt._answer_lacks_depth(
        "它保护 CPython 解释器内部状态，避免多个线程同时执行字节码时破坏对象状态。"
    )
    assert not kt._answer_lacks_depth(
        "**结论**：GIL 保证同一时刻只有一个线程执行字节码。"
        "**机制**：保护解释器内部状态和引用计数。"
    )


def test_long_multi_segment_answer_passes_without_markers():
    """换 LLM 通道后可能不按加粗标记输出——明显多层展开的长回答要放行。"""
    answer = (
        "GIL 的存在是因为 CPython 的内存管理不是线程安全的，引用计数的增减必须互斥。"
        "线程执行字节码前要先拿到 GIL，每隔一定字节码指令数或遇到 IO 就释放，让其它线程有机会运行。"
        "所以 IO 密集型任务用多线程仍然有收益，CPU 密集型应改用多进程；常见误区是以为 GIL 让 Python 完全无法并发。"
    )
    assert not kt._answer_lacks_depth(answer)


def test_normalize_keeps_card_without_mnemonic_and_truncates_long_one():
    """mnemonic 可选：缺失不丢卡；超长截断到 80 字符。"""
    sections = [
        kt.KnowledgeSection(
            filename="README.md",
            header_path="Python > GIL",
            content=_long("GIL 相关知识"),
        )
    ]
    base_card = {
        "title": "GIL 的作用",
        "knowledge": ["保护解释器内部状态", "同一时刻通常只有一个线程执行 Python 字节码"],
        "example": "CPU 密集型多线程不会线性加速。",
        "question": "GIL 主要解决什么问题？",
        "answer": (
            "**结论**：GIL 保证同一时刻只有一个线程执行字节码。"
            "**机制**：保护 CPython 解释器内部状态与引用计数。"
            "**易错点**：IO 密集任务的线程并发不受影响。"
        ),
        "tags": ["并发"],
    }
    import json

    without = kt.normalize_training_cards(json.dumps([base_card], ensure_ascii=False), topic="python", sections=sections)
    assert len(without) == 1
    assert without[0]["mnemonic"] == ""

    long_card = dict(base_card, mnemonic="口" * 200)
    truncated = kt.normalize_training_cards(json.dumps([long_card], ensure_ascii=False), topic="python", sections=sections)
    assert len(truncated) == 1
    assert truncated[0]["mnemonic"] == "口" * 80

