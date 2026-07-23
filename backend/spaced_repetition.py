"""间隔重复系统 — SM-2 算法。

为每个薄弱点维护复习调度：
- 答对了 → 间隔拉长（1天 → 3天 → 7天 → ...）
- 答错了 → 间隔重置到 1 天
- 每次出题时优先出"到期需要复习"的知识点
"""
from datetime import date, timedelta
from contextlib import contextmanager

from backend import memory as _memory
from backend.memory import (
    _get_applied_operation,
    _load_profile,
    _record_applied_operation,
    _save_profile,
    ProfileTransactionAbort,
)


@contextmanager
def _profile_transaction(user_id: str):
    """Use the locked production transaction, while keeping old unit monkeypatches useful."""
    if _load_profile is _memory._load_profile and _save_profile is _memory._save_profile:
        with _memory.profile_transaction(user_id) as profile:
            yield profile
        return

    profile = _load_profile(user_id)
    try:
        yield profile
    except ProfileTransactionAbort:
        return
    _save_profile(profile, user_id)


def sm2_update(sr_state: dict, score_0_10: float) -> dict:
    """SM-2 algorithm update.

    Args:
        sr_state: Current spaced repetition state {interval_days, ease_factor, repetitions, ...}
        score_0_10: Score on 0-10 scale (mapped to SM-2 quality 0-5)

    Returns:
        Updated SR state dict
    """
    # Map 0-10 to SM-2 quality 0-5
    quality = min(5, int(score_0_10 / 2))
    ef = sr_state.get("ease_factor", 2.5)
    reps = sr_state.get("repetitions", 0)

    if quality >= 3:  # Pass
        if reps == 0:
            interval = 1
        elif reps == 1:
            # Deliberately more aggressive than SM-2's standard 6 days — interview
            # prep wants the second review sooner.
            interval = 3
        else:
            # round() not int(): truncation systematically biased intervals down,
            # which over-schedules reviews.
            interval = round(sr_state.get("interval_days", 1) * ef)
        reps += 1
    else:  # Fail — reset
        interval = 1
        reps = 0

    # Update ease factor (never below 1.3)
    ef = max(1.3, ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))

    return {
        "interval_days": interval,
        "ease_factor": round(ef, 2),
        "repetitions": reps,
        "next_review": (date.today() + timedelta(days=interval)).isoformat(),
        "last_score": score_0_10,
    }


def get_due_reviews(user_id: str, topic: str = None) -> list[dict]:
    """Get weak points that are due for review.

    Returns list of weak_point dicts sorted by ease_factor (hardest first).
    """
    profile = _load_profile(user_id)
    today = date.today().isoformat()
    due = []

    for wp in profile.get("weak_points", []):
        if wp.get("improved"):
            continue
        if topic and wp.get("topic") != topic:
            continue
        sr = wp.get("sr", {})
        next_review = sr.get("next_review", "2000-01-01")
        if next_review <= today:
            due.append(wp)

    # Hardest first (lowest ease_factor)
    due.sort(key=lambda x: x.get("sr", {}).get("ease_factor", 2.5))
    return due


def update_weak_point_sr(topic: str, point_text: str, score: float, user_id: str,
                          difficulty: int = 3,
                          operation_id: str | None = None):
    """Update spaced repetition state + per-WP mastery for matching weak points.

    Per-WP mastery uses EWMA so it tracks recent performance rather than
    averaging over the entire history. The formula intentionally rewards
    high-difficulty wins more than low-difficulty wins.

    A question's `weak_point` hint can legitimately match multiple stored WPs
    (e.g. evaluator reports "GIL" but the user has both "对 GIL 理解不深" and
    "Python 并发模型理解薄弱"). We update **all** substring-matching WPs, not
    just the first one — otherwise the second WP's mastery silently drifts
    out of sync with the user's actual performance.
    Auto-marks weak points as "improved" when they reach mastery threshold
    (3+ consecutive passes with high scores).
    """
    from datetime import datetime

    # contribution ∈ [0, 100] — caps difficulty at 5 and score at 10.
    diff_clamped = max(1, min(5, int(difficulty)))
    score_clamped = max(0.0, min(10.0, float(score)))
    contribution = (diff_clamped / 5.0) * (score_clamped / 10.0) * 100.0

    needle = (point_text or "").lower().strip()
    if not needle:
        return False

    # Whole read-modify-write inside the per-user lock: SR state must not be
    # clobbered by a concurrent profile writer holding an older snapshot.
    matched = 0
    duplicate = False
    with _profile_transaction(user_id) as profile:
        if _get_applied_operation(profile, operation_id) is not None:
            duplicate = True
            raise ProfileTransactionAbort
        matched = 0
        for wp in profile.get("weak_points", []):
            if wp.get("improved"):
                continue
            if topic and wp.get("topic") != topic:
                continue
            wp_text = (wp.get("point") or "").lower()
            if not wp_text:
                continue
            if not (needle in wp_text or wp_text in needle):
                continue

            prev_sr = wp.get("sr", {})
            new_sr = sm2_update(prev_sr, score)
            # Graduation requires 3 *consecutive* scores >= 7. Legacy SR entries
            # did not store consecutive_high. `repetitions` counts SM-2 passes
            # (score >= 6), NOT high scores (>= 7), so it must NOT be used as the
            # high-streak baseline — that would let a long run of mediocre passes
            # graduate a weak point on its first real high score. Credit at most
            # the single known last score so graduation still needs fresh highs.
            prev_high = prev_sr.get("consecutive_high")
            if prev_high is None:
                prev_high = 1 if prev_sr.get("last_score", 0) >= 7 else 0
            new_sr["consecutive_high"] = (int(prev_high) + 1) if score >= 7 else 0
            wp["sr"] = new_sr

            prev_mastery = wp.get("mastery")
            if prev_mastery is None:
                wp["mastery"] = round(contribution)
            else:
                wp["mastery"] = round(0.7 * float(prev_mastery) + 0.3 * contribution)
            wp["attempts"] = int(wp.get("attempts", 0)) + 1

            if wp["sr"].get("consecutive_high", 0) >= 3:
                wp["improved"] = True
                wp["improved_at"] = datetime.now().isoformat()
                wp["improved_reason"] = "spaced_repetition_mastery"
                strong = profile.setdefault("strong_points", [])
                marker = f"已掌握: {wp['point']}"
                # Dedup: repeated graduations (or replayed history) must not pile
                # up duplicate "已掌握" entries — matches memory.py's merge guard.
                if not any(isinstance(sp, dict) and sp.get("point") == marker for sp in strong):
                    strong.append({
                        "point": marker,
                        "topic": wp.get("topic", ""),
                        "first_seen": datetime.now().isoformat(),
                    })
            matched += 1

        _record_applied_operation(profile, operation_id)
        if not matched and not operation_id:
            raise ProfileTransactionAbort  # nothing changed — skip the save

    return not duplicate and matched > 0


def init_sr_for_existing_points(user_id: str):
    """Initialize SR state + mastery/attempts defaults for legacy weak points."""
    with _profile_transaction(user_id) as profile:
        changed = False

        # Topic-level mastery is used as the initial per-WP mastery for legacy
        # entries that pre-date the per-WP field.
        topic_mastery = profile.get("topic_mastery", {})

        for wp in profile.get("weak_points", []):
            if wp.get("improved"):
                continue
            if "sr" not in wp:
                wp["sr"] = {
                    "interval_days": 1,
                    "ease_factor": 2.5,
                    "repetitions": 0,
                    "next_review": date.today().isoformat(),
                    "last_score": None,
                }
                changed = True
            if "mastery" not in wp:
                tm = topic_mastery.get(wp.get("topic", ""), {})
                wp["mastery"] = int(tm.get("score", 20))  # 20 = "未证明" 默认值
                changed = True
            if "attempts" not in wp:
                wp["attempts"] = int(wp.get("times_seen", 1))
                changed = True

        if not changed:
            raise ProfileTransactionAbort  # nothing to migrate — skip the save
