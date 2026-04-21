"""Review formatting helpers — shared by drill, job-prep, recording, and solo modes."""


def format_per_question_block(q: dict, answer: str, score_entry: dict, *, show_role_expectation: bool = False) -> list[str]:
    """Render one Q&A block; returns a list of markdown lines."""
    qid = q.get("id", "?")
    focus = q.get("focus_area", q.get("category", ""))

    if not answer:
        return [f"### Q{qid} ({focus}) — 未作答", f"**题目**: {q['question']}\n"]

    lines = []
    score = score_entry.get("score", "-")
    label = f"### Q{qid} ({focus})" if not show_role_expectation else (
        f"### Q{qid} ({q.get('category', '未分类')} / {q.get('focus_area', '')})"
    )
    lines.append(f"{label} — {score}/10")
    lines.append(f"**题目**: {q['question']}")
    lines.append(f"**你的回答**: {answer}")

    if show_role_expectation and score_entry.get("role_expectation"):
        lines.append(f"**岗位在看什么**: {score_entry['role_expectation']}")
    if score_entry.get("assessment"):
        lines.append(f"**点评**: {score_entry['assessment']}")
    if score_entry.get("improvement"):
        lines.append(f"**改进建议**: {score_entry['improvement']}")
    if score_entry.get("understanding"):
        lines.append(f"**理解程度**: {score_entry['understanding']}")
    if score_entry.get("key_missing"):
        lines.append(f"**遗漏关键点**: {', '.join(score_entry['key_missing'])}")
    lines.append("")
    return lines


def _format_points(overall: dict) -> list[str]:
    lines = []
    if overall.get("new_weak_points"):
        lines.append("---\n\n## 薄弱点")
        for wp in overall["new_weak_points"]:
            lines.append(f"- {wp.get('point', wp) if isinstance(wp, dict) else wp}")
    if overall.get("new_strong_points"):
        lines.append("\n## 亮点")
        for sp in overall["new_strong_points"]:
            lines.append(f"- {sp.get('point', sp) if isinstance(sp, dict) else sp}")
    return lines


def format_drill_review(questions, answers, scores, overall) -> str:
    answer_map = {a["question_id"]: a["answer"] for a in answers}
    score_map = {s["question_id"]: s for s in scores}

    lines = [f"## 整体评价\n\n{overall.get('summary', '')}\n\n**平均分: {overall.get('avg_score', '-')}/10**\n"]
    lines.append("---\n\n## 逐题复盘\n")

    for q in questions:
        lines.extend(format_per_question_block(
            q, answer_map.get(q["id"], ""), score_map.get(q["id"], {}),
        ))

    lines.extend(_format_points(overall))
    return "\n".join(lines)


def format_job_prep_review(questions, answers, scores, overall, meta) -> str:
    answer_map = {a["question_id"]: a["answer"] for a in answers}
    score_map = {s["question_id"]: s for s in scores}

    title = meta.get("position") or "目标岗位"
    company = meta.get("company")
    heading = f"{company} / {title}" if company else title

    lines = [f"## 岗位画像\n\n**目标岗位**: {heading}\n"]
    if meta.get("preview", {}).get("role_summary"):
        lines.append(f"\n**岗位本质**: {meta['preview']['role_summary']}\n")

    lines.append(f"\n## 整体评价\n\n{overall.get('summary', '')}\n")
    lines.append(f"\n**平均分: {overall.get('avg_score', '-')}/10**")

    if overall.get("role_fit_summary"):
        lines.append(f"\n**岗位匹配度**: {overall['role_fit_summary']}")
    if overall.get("interviewer_hotspots"):
        lines.append("\n\n## 高风险追问点")
        for item in overall["interviewer_hotspots"]:
            lines.append(f"- {item}")
    if overall.get("prep_priorities"):
        lines.append("\n## 面试前优先补强")
        for item in overall["prep_priorities"]:
            lines.append(f"- {item}")

    lines.append("\n---\n\n## 逐题复盘\n")
    for q in questions:
        lines.extend(format_per_question_block(
            q, answer_map.get(q["id"], ""), score_map.get(q["id"], {}),
            show_role_expectation=True,
        ))

    lines.extend(_format_points(overall))
    return "\n".join(lines)


def format_solo_review(topics_covered: list, overall: dict) -> str:
    lines = [f"## 整体评价\n\n{overall.get('summary', '')}\n\n**平均分: {overall.get('avg_score', '-')}/10**\n"]

    if topics_covered:
        lines.append("---\n\n## 涉及知识点\n")
        for t in topics_covered:
            score = t.get("score", "-")
            lines.append(f"### {t.get('topic', '未知')} — {score}/10")
            if t.get("assessment"):
                lines.append(f"**评价**: {t['assessment']}")
            if t.get("understanding"):
                lines.append(f"**理解程度**: {t['understanding']}")
            if t.get("errors"):
                lines.append(f"**错误**: {', '.join(t['errors'])}")
            if t.get("missing"):
                lines.append(f"**遗漏**: {', '.join(t['missing'])}")
            lines.append("")

    lines.extend(_format_points(overall))
    return "\n".join(lines)
