"""把现有 11 个 topic 重组成 Python / Java / Agent 三大板块。

迁移规则（合并到三大板块，内容尽量全面覆盖）：

  python ←
    01_Python核心          (9 文件，纯 Python 基础与进阶)
    07_算法与数据结构      (5 文件，主要 Python 实现)
    06_计算机基础(副本)    (4 文件，OS/网络/数据库基础——Python 工程师也要)

  java ←
    02_Java核心            (9 文件)
    03_Spring生态与微服务  (7 文件)
    04_数据库与中间件      (6 文件，JDBC/Redis/MySQL/Mongo/MQ 等)
    05_系统设计与架构      (6 文件，分布式/微服务/性能/安全)
    10_Java_AI生态         (4 文件，Spring AI / LangChain4j)
    11_项目实战与面试题    (4 文件)
    06_计算机基础(副本)    (4 文件)

  agent ←
    08_AI大模型与应用      (8 文件，Transformer/RAG/Embedding/Prompt/LangChain)
    09_Agent工程化实战     (7 文件，MCP/Function Calling/Multi-Agent/记忆/可观测性)

特点：
- 安全：先把原 knowledge 目录整体复制为 backup，再 rsync 风格地拷到新目录；
  原始内容不会丢，可手动回滚。
- 幂等：重复运行只会刷新目标，不会报错；用 --force 跳过确认。
- 全用户：自动扫 data/users/<uid>/，每个用户的 knowledge 都重组。
- 清缓存：删 .index_cache 让用户在 UI 里点「初始化向量库」重建。

用法：
  # 干跑预览
  python scripts/migrate_to_three_topics.py --dry-run

  # 实际执行
  python scripts/migrate_to_three_topics.py

  # 跳过确认
  python scripts/migrate_to_three_topics.py --force

  # 同时拆分项目根的 面试题集.md（从 git 取回）到三个 high_freq
  python scripts/migrate_to_three_topics.py --include-question-set
"""
from __future__ import annotations

import sys as _sys
# Force UTF-8 stdout on Windows so Chinese paths print without GBK errors.
try:
    _sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

NEW_TOPICS = {
    "python": {"name": "Python 核心", "icon": "Terminal", "dir": "python"},
    "java": {"name": "Java 后端", "icon": "Code", "dir": "java"},
    "agent": {"name": "AI Agent 工程", "icon": "Workflow", "dir": "agent"},
}

# 旧目录 → 新 topic key 列表（一个旧目录可被复制到多个新 topic）
MIGRATION_MAP = {
    "01_Python核心": ["python"],
    "02_Java核心": ["java"],
    "03_Spring生态与微服务": ["java"],
    "04_数据库与中间件": ["java"],
    "05_系统设计与架构": ["java"],
    "06_计算机基础": ["python", "java"],  # 复制到两边
    "07_算法与数据结构": ["python"],
    "08_AI大模型与应用": ["agent"],
    "09_Agent工程化实战": ["agent"],
    "10_Java_AI生态": ["java"],
    "11_项目实战与面试题": ["java"],
}

# 文件名前缀去重 —— 避免不同来源目录拷过来的同名文件相互覆盖
PREFIX_BY_SOURCE = {
    "01_Python核心": "py-",
    "02_Java核心": "java-",
    "03_Spring生态与微服务": "spring-",
    "04_数据库与中间件": "db-",
    "05_系统设计与架构": "arch-",
    "06_计算机基础": "cs-",
    "07_算法与数据结构": "algo-",
    "08_AI大模型与应用": "ai-",
    "09_Agent工程化实战": "agent-",
    "10_Java_AI生态": "javaai-",
    "11_项目实战与面试题": "proj-",
}


def log(msg: str, *, dry: bool = False):
    prefix = "[dry-run] " if dry else ""
    print(f"{prefix}{msg}")


def find_user_knowledge_roots() -> list[tuple[str, Path]]:
    """Return [(label, knowledge_dir)] for global + each user."""
    roots: list[tuple[str, Path]] = []
    if (DATA_DIR / "knowledge").is_dir():
        roots.append(("global", DATA_DIR / "knowledge"))
    users_dir = DATA_DIR / "users"
    if users_dir.is_dir():
        for u in sorted(users_dir.iterdir()):
            kd = u / "knowledge"
            if kd.is_dir():
                roots.append((f"user:{u.name}", kd))
    return roots


def find_topics_files() -> list[tuple[str, Path]]:
    """Return [(label, topics_json_path)] for global + each user."""
    files: list[tuple[str, Path]] = []
    g = DATA_DIR / "topics.json"
    if g.exists():
        files.append(("global", g))
    users_dir = DATA_DIR / "users"
    if users_dir.is_dir():
        for u in sorted(users_dir.iterdir()):
            t = u / "topics.json"
            if t.exists():
                files.append((f"user:{u.name}", t))
    return files


def find_index_cache_dirs() -> list[Path]:
    """All .index_cache directories under data/users/<uid>/."""
    out: list[Path] = []
    users_dir = DATA_DIR / "users"
    if users_dir.is_dir():
        for u in users_dir.iterdir():
            ic = u / ".index_cache"
            if ic.is_dir():
                out.append(ic)
    return out


def backup_dir(src: Path, *, dry: bool) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = src.parent / f"{src.name}.backup-{ts}"
    log(f"backup {src} → {dst}", dry=dry)
    if not dry:
        shutil.copytree(src, dst)
    return dst


def copy_files_to_new_layout(knowledge_root: Path, *, dry: bool):
    """Copy old <num>_<name>/*.md into new {python,java,agent}/<prefix><name>.md."""
    # Make new dirs
    for key in NEW_TOPICS:
        new_dir = knowledge_root / NEW_TOPICS[key]["dir"]
        log(f"  mkdir {new_dir}", dry=dry)
        if not dry:
            new_dir.mkdir(parents=True, exist_ok=True)

    # Copy each old dir's files to mapped new dirs (with prefix to avoid collision)
    for old_name, target_keys in MIGRATION_MAP.items():
        src_dir = knowledge_root / old_name
        if not src_dir.is_dir():
            log(f"  skip (missing): {src_dir.name}", dry=dry)
            continue
        prefix = PREFIX_BY_SOURCE.get(old_name, "")
        files = sorted(src_dir.glob("*.md"))
        for tgt_key in target_keys:
            tgt_dir = knowledge_root / NEW_TOPICS[tgt_key]["dir"]
            for f in files:
                tgt = tgt_dir / f"{prefix}{f.name}"
                log(f"  cp {old_name}/{f.name} → {tgt_key}/{tgt.name}", dry=dry)
                if not dry:
                    shutil.copy2(f, tgt)


def remove_old_dirs(knowledge_root: Path, *, dry: bool):
    """After files are copied, remove the original numbered directories."""
    for old_name in MIGRATION_MAP:
        src_dir = knowledge_root / old_name
        if src_dir.is_dir():
            log(f"  rm -rf {src_dir}", dry=dry)
            if not dry:
                shutil.rmtree(src_dir)


def sync_extras_from_global(user_kb: Path, *, dry: bool):
    """Mirror data/knowledge/<topic>/extra-*.md into each user copy so users
    actually get the curated supplemental content. Without this, users only see
    the migrated legacy files because the global knowledge tree is separate."""
    global_kb = DATA_DIR / "knowledge"
    if not global_kb.is_dir() or user_kb == global_kb:
        return
    for key in NEW_TOPICS:
        src = global_kb / key
        dst = user_kb / key
        if not src.is_dir() or not dst.is_dir():
            continue
        for f in sorted(src.glob("extra-*.md")):
            tgt = dst / f.name
            log(f"  sync extra: {f.name} → {dst}", dry=dry)
            if not dry:
                shutil.copy2(f, tgt)


def update_topics_file(path: Path, *, dry: bool):
    log(f"rewrite {path}", dry=dry)
    if not dry:
        path.write_text(json.dumps(NEW_TOPICS, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_index_cache(cache_dir: Path, *, dry: bool):
    log(f"  clear cache {cache_dir}", dry=dry)
    if not dry:
        shutil.rmtree(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)


def split_question_set(knowledge_roots: list[tuple[str, Path]], *, dry: bool):
    """Recover 面试题集.md from git history and split it into three high_freq files."""
    try:
        text = subprocess.check_output(
            ["git", "show", "HEAD:面试题集.md"],
            cwd=str(REPO_ROOT),
            stderr=subprocess.DEVNULL,
        ).decode("utf-8")
    except subprocess.CalledProcessError:
        log("WARN: cannot read 面试题集.md from git HEAD; skipping", dry=False)
        return

    # Naive split: assign each top-level section by keyword
    sections: dict[str, list[str]] = {"python": [], "java": [], "agent": []}
    current_target = "agent"
    for line in text.splitlines():
        if line.startswith("# ") or line.startswith("## "):
            low = line.lower()
            if any(k in low for k in ("python", "py ", "django", "flask", "asyncio")):
                current_target = "python"
            elif any(k in low for k in ("java", "spring", "jvm", "mybatis", "tomcat")):
                current_target = "java"
            elif any(k in low for k in ("agent", "langgraph", "langchain", "rag", "llm", "prompt", "embedding", "mcp")):
                current_target = "agent"
        sections[current_target].append(line)

    for label, root in knowledge_roots:
        if label == "global":
            hf_root = DATA_DIR / "high_freq"
        else:
            uid = label.split(":", 1)[1]
            hf_root = DATA_DIR / "users" / uid / "high_freq"
        log(f"high-freq split → {hf_root}", dry=dry)
        if not dry:
            hf_root.mkdir(parents=True, exist_ok=True)
        for key, lines in sections.items():
            if not lines:
                continue
            target = hf_root / f"{key}.md"
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            payload = (
                "\n\n<!-- imported from 面试题集.md -->\n\n"
                + "\n".join(lines).strip()
                + "\n"
            )
            log(f"  append → {target}", dry=dry)
            if not dry:
                target.write_text(existing.rstrip() + payload, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Migrate 11-topic knowledge base to 3 topics.")
    parser.add_argument("--dry-run", action="store_true", help="Show actions without writing.")
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompt.")
    parser.add_argument("--include-question-set", action="store_true",
                        help="Recover 面试题集.md from git HEAD and split into high_freq files.")
    parser.add_argument("--keep-old-dirs", action="store_true",
                        help="Don't delete original numbered directories after copy (default deletes them).")
    args = parser.parse_args()

    knowledge_roots = find_user_knowledge_roots()
    topics_files = find_topics_files()
    cache_dirs = find_index_cache_dirs()

    print("=" * 70)
    print("迁移目标：")
    print(f"  -{len(knowledge_roots)} 个 knowledge 目录将被重组")
    for label, root in knowledge_roots:
        print(f"      - {label}: {root}")
    print(f"  -{len(topics_files)} 个 topics.json 将被重写")
    print(f"  -{len(cache_dirs)} 个 .index_cache 将被清空（之后请在 UI 里点重建）")
    print("=" * 70)

    if not args.force and not args.dry_run:
        ans = input("继续？该操作会把所有现有 11 个 topic 目录复制为 backup 后重组成 3 个。[y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            print("已取消。")
            sys.exit(0)

    # 1. Backup each knowledge root
    for label, root in knowledge_roots:
        log(f"\n=== {label} ===")
        backup_dir(root, dry=args.dry_run)
        copy_files_to_new_layout(root, dry=args.dry_run)
        if not args.keep_old_dirs:
            remove_old_dirs(root, dry=args.dry_run)
        # Mirror global extra-*.md into each user copy
        if label.startswith("user:"):
            sync_extras_from_global(root, dry=args.dry_run)

    # 2. Rewrite topics.json
    log("\n=== topics.json ===")
    for label, path in topics_files:
        update_topics_file(path, dry=args.dry_run)

    # 3. Clear index caches
    log("\n=== index caches ===")
    for c in cache_dirs:
        clear_index_cache(c, dry=args.dry_run)

    # 4. Optional: split 面试题集.md
    if args.include_question_set:
        log("\n=== split 面试题集.md ===")
        split_question_set(knowledge_roots, dry=args.dry_run)

    print("\n" + "=" * 70)
    print("[OK] 迁移完成。下一步：")
    print("  1. 重启后端使配置生效（topics.json）")
    print("  2. 进 Knowledge 页面 → 点「初始化向量库」→「全量重建」")
    print("  3. 检查三个新目录下文件是否齐全")
    print("  备份目录：data/knowledge.backup-* （可手动删除以释放空间）")
    print("=" * 70)


if __name__ == "__main__":
    main()
