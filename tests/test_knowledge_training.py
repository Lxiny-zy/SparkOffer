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
