"""Unit tests for the SM-2 spaced-repetition core (backend/spaced_repetition.py).

``sm2_update`` is a pure function (no IO) — the single highest-value regression
target in the scheduler: a silent bug here corrupts every user's review interval
and downstream difficulty. ``update_weak_point_sr`` / ``get_due_reviews`` touch
profile IO, so we monkeypatch ``_load_profile`` / ``_save_profile`` to keep these
tests pure, fast, and filesystem-free.

All expected values below are hand-derived from the formulas in the module —
see the inline comments for the arithmetic.
"""
from datetime import date, timedelta

import pytest

from backend.spaced_repetition import (
    sm2_update,
    update_weak_point_sr,
    get_due_reviews,
)


# ── sm2_update: score (0-10) → SM-2 quality (0-5) mapping ─────────────────────

def test_score_6_is_pass_boundary():
    # quality = int(6/2) = 3 → pass. reps 0 → first interval = 1 day.
    out = sm2_update({"repetitions": 0, "ease_factor": 2.5}, 6.0)
    assert out["repetitions"] == 1
    assert out["interval_days"] == 1


def test_score_5_is_fail_boundary():
    # quality = int(5/2) = 2 → fail → reset interval to 1, reps to 0.
    out = sm2_update({"repetitions": 4, "interval_days": 30, "ease_factor": 2.5}, 5.0)
    assert out["repetitions"] == 0
    assert out["interval_days"] == 1


# ── sm2_update: interval progression 1 → 3 → int(prev*ef) ─────────────────────

def test_interval_progression_first_three_passes():
    s = sm2_update({"repetitions": 0, "ease_factor": 2.5, "interval_days": 1}, 8.0)
    assert (s["interval_days"], s["repetitions"]) == (1, 1)   # reps 0 → 1 day
    s = sm2_update(s, 8.0)
    assert (s["interval_days"], s["repetitions"]) == (3, 2)   # reps 1 → 3 days
    s = sm2_update(s, 8.0)
    assert (s["interval_days"], s["repetitions"]) == (8, 3)   # round(3 * 2.5) = round(7.5) = 8


def test_fail_resets_repetitions_and_interval():
    out = sm2_update({"repetitions": 5, "interval_days": 60, "ease_factor": 2.8}, 2.0)
    assert out["repetitions"] == 0
    assert out["interval_days"] == 1


# ── sm2_update: ease factor update + 1.3 floor ────────────────────────────────

def test_ease_factor_increases_on_perfect_score():
    # quality 5 → delta = 0.1 - 0*(...) = +0.1
    out = sm2_update({"repetitions": 0, "ease_factor": 2.5}, 10.0)
    assert out["ease_factor"] == 2.6


def test_ease_factor_quality_4_is_neutral():
    # quality 4 → delta = 0.1 - 1*(0.08 + 1*0.02) = 0.1 - 0.10 = 0.0
    out = sm2_update({"repetitions": 0, "ease_factor": 2.5}, 8.0)
    assert out["ease_factor"] == 2.5


def test_ease_factor_clamped_at_1_3():
    # quality 0 → delta = 0.1 - 5*(0.08 + 5*0.02) = 0.1 - 0.9 = -0.8.
    # 1.3 - 0.8 = 0.5, must clamp up to the 1.3 floor.
    out = sm2_update({"repetitions": 0, "ease_factor": 1.3}, 0.0)
    assert out["ease_factor"] == 1.3


# ── sm2_update: next_review date ──────────────────────────────────────────────

def test_next_review_is_today_plus_interval():
    out = sm2_update({"repetitions": 1, "ease_factor": 2.5, "interval_days": 1}, 8.0)
    expected = (date.today() + timedelta(days=out["interval_days"])).isoformat()
    assert out["next_review"] == expected


# ── update_weak_point_sr: mastery contribution + EWMA ─────────────────────────

def _profile_with(weak_points):
    return {"weak_points": weak_points, "strong_points": []}


def _patch_profile(monkeypatch, profile):
    monkeypatch.setattr("backend.spaced_repetition._load_profile", lambda uid: profile)
    monkeypatch.setattr("backend.spaced_repetition._save_profile", lambda p, uid: None)


def test_mastery_contribution_first_time(monkeypatch):
    prof = _profile_with([{"point": "对 gil 理解不深", "topic": "python"}])
    _patch_profile(monkeypatch, prof)
    update_weak_point_sr("python", "gil", 10.0, "u", difficulty=5)
    # contribution = (5/5)*(10/10)*100 = 100; no prior mastery → round(100)
    assert prof["weak_points"][0]["mastery"] == 100


def test_mastery_ewma_blend(monkeypatch):
    prof = _profile_with([{"point": "gil", "topic": "python", "mastery": 50}])
    _patch_profile(monkeypatch, prof)
    update_weak_point_sr("python", "gil", 10.0, "u", difficulty=5)
    # 0.7*50 + 0.3*100 = 65
    assert prof["weak_points"][0]["mastery"] == 65


def test_updates_all_substring_matching_weak_points(monkeypatch):
    prof = _profile_with([
        {"point": "gil 释放时机", "topic": "python"},
        {"point": "gil", "topic": "python"},
        {"point": "asyncio 调度", "topic": "python"},
    ])
    _patch_profile(monkeypatch, prof)
    update_weak_point_sr("python", "gil", 8.0, "u", difficulty=3)
    # Both 'gil'-matching WPs updated; the asyncio one untouched.
    assert "mastery" in prof["weak_points"][0]
    assert "mastery" in prof["weak_points"][1]
    assert "mastery" not in prof["weak_points"][2]


def test_topic_filter_skips_other_topics(monkeypatch):
    prof = _profile_with([{"point": "gil", "topic": "java"}])
    saved = []
    monkeypatch.setattr("backend.spaced_repetition._load_profile", lambda uid: prof)
    monkeypatch.setattr("backend.spaced_repetition._save_profile", lambda p, uid: saved.append(p))
    result = update_weak_point_sr("python", "gil", 8.0, "u", difficulty=3)
    assert result is False
    assert saved == []  # nothing matched → no write


# ── update_weak_point_sr: graduation (reps ≥ 3 AND score ≥ 7) ─────────────────

def test_graduation_after_third_pass_with_high_score(monkeypatch):
    prof = _profile_with([{
        "point": "gil", "topic": "python",
        "sr": {"repetitions": 2, "interval_days": 3, "ease_factor": 2.5},
    }])
    _patch_profile(monkeypatch, prof)
    update_weak_point_sr("python", "gil", 8.0, "u", difficulty=3)  # reps 2→3, score 8≥7
    wp = prof["weak_points"][0]
    assert wp.get("improved") is True
    assert any("已掌握" in sp["point"] for sp in prof["strong_points"])


def test_no_graduation_when_score_below_7(monkeypatch):
    prof = _profile_with([{
        "point": "gil", "topic": "python",
        "sr": {"repetitions": 2, "interval_days": 3, "ease_factor": 2.5},
    }])
    _patch_profile(monkeypatch, prof)
    update_weak_point_sr("python", "gil", 6.0, "u", difficulty=3)  # pass (q=3) but 6 < 7
    wp = prof["weak_points"][0]
    assert wp.get("improved") is not True
    assert wp["sr"]["repetitions"] == 3  # reps still advanced


# ── get_due_reviews: filtering + hardest-first ordering ───────────────────────

def test_due_reviews_filters_and_sorts_hardest_first(monkeypatch):
    today = date.today().isoformat()
    future = (date.today() + timedelta(days=10)).isoformat()
    prof = {"weak_points": [
        {"point": "due-easy", "topic": "python", "sr": {"next_review": today, "ease_factor": 2.8}},
        {"point": "due-hard", "topic": "python", "sr": {"next_review": today, "ease_factor": 1.4}},
        {"point": "not-due", "topic": "python", "sr": {"next_review": future, "ease_factor": 2.0}},
        {"point": "graduated", "topic": "python", "improved": True,
         "sr": {"next_review": today, "ease_factor": 1.5}},
    ]}
    monkeypatch.setattr("backend.spaced_repetition._load_profile", lambda uid: prof)
    due = get_due_reviews("u", topic="python")
    points = [wp["point"] for wp in due]
    # not-due excluded, graduated excluded; lowest ease_factor (hardest) first.
    assert points == ["due-hard", "due-easy"]


def test_due_reviews_topic_filter(monkeypatch):
    today = date.today().isoformat()
    prof = {"weak_points": [
        {"point": "py", "topic": "python", "sr": {"next_review": today, "ease_factor": 2.0}},
        {"point": "jv", "topic": "java", "sr": {"next_review": today, "ease_factor": 2.0}},
    ]}
    monkeypatch.setattr("backend.spaced_repetition._load_profile", lambda uid: prof)
    due = get_due_reviews("u", topic="java")
    assert [wp["point"] for wp in due] == ["jv"]
