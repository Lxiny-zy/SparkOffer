"""面试记录持久化 (SQLite)."""
import json
import uuid
from datetime import datetime, timedelta

from backend.storage.database import get_db


def new_session_id() -> str:
    """Return a collision-resistant opaque id for globally keyed sessions."""
    return uuid.uuid4().hex


def create_session(session_id: str, mode: str, topic: str | None = None,
                   questions: list | None = None, meta: dict | None = None, *, user_id: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO sessions (session_id, mode, topic, meta, questions, user_id) VALUES (?, ?, ?, ?, ?, ?)",
        (
            session_id,
            mode,
            topic,
            json.dumps(meta or {}, ensure_ascii=False),
            json.dumps(questions or [], ensure_ascii=False),
            user_id,
        ),
    )
    conn.commit()


def append_messages(session_id: str, messages: list[dict], *, user_id: str) -> bool:
    """Atomically append one turn (or any message batch) to a transcript."""
    if not messages:
        return True

    conn = get_db()
    now = datetime.now().isoformat()
    encoded = [
        json.dumps(
            {
                "role": message.get("role", ""),
                "content": message.get("content", ""),
                "time": message.get("time") or now,
            },
            ensure_ascii=False,
        )
        for message in messages
    ]
    inserts = ", ".join("'$[#]', json(?)" for _ in encoded)
    cursor = conn.execute(
        "UPDATE sessions SET transcript = json_insert("
        f"COALESCE(NULLIF(transcript, ''), '[]'), {inserts}), "
        "updated_at = CURRENT_TIMESTAMP WHERE session_id = ? AND user_id = ?",
        [*encoded, session_id, user_id],
    )
    conn.commit()
    return cursor.rowcount > 0


def append_message(session_id: str, role: str, content: str, *, user_id: str):
    """Append a message to transcript using SQLite JSON function (no full reload)."""
    return append_messages(
        session_id, [{"role": role, "content": content}], user_id=user_id,
    )


def save_drill_answers(
    session_id: str,
    answers: list[dict],
    *,
    user_id: str,
    evaluation_token: str | None = None,
) -> bool:
    """Save canonical Q&A pairs, fenced to the active evaluation generation."""
    conn = get_db()
    row = conn.execute(
        "SELECT questions FROM sessions WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        return False
    questions = json.loads(row["questions"])
    answer_map = {}
    for answer_row in answers or []:
        if not isinstance(answer_row, dict):
            continue
        question_id = answer_row.get("question_id", answer_row.get("id"))
        if question_id is None:
            continue
        try:
            question_key = str(question_id)
        except Exception:
            continue
        answer_map[question_key] = (
            "" if answer_row.get("answer") is None
            else str(answer_row.get("answer", ""))
        )

    transcript = []
    for q in questions:
        transcript.append({
            "role": "assistant",
            "content": q["question"],
            "question_id": q["id"],
            "time": datetime.now().isoformat(),
        })
        answer = answer_map.get(str(q["id"]), "")
        if answer:
            transcript.append({
                "role": "user",
                "content": answer,
                "question_id": q["id"],
                "time": datetime.now().isoformat(),
            })

    doc = "COALESCE(NULLIF(meta, ''), '{}')"
    where = "session_id = ? AND user_id = ?"
    params = [json.dumps(transcript, ensure_ascii=False), session_id, user_id]
    if evaluation_token is not None:
        where += f" AND json_extract({doc}, '$.evaluation_claim_token') = ?"
        params.append(evaluation_token)
    cursor = conn.execute(
        "UPDATE sessions SET transcript = ?, updated_at = CURRENT_TIMESTAMP "
        f"WHERE {where}",
        params,
    )
    conn.commit()
    return cursor.rowcount > 0


def save_review(session_id: str, review: str, scores: list = None,
                weak_points: list = None, overall: dict = None, *, user_id: str,
                evaluation_token: str | None = None) -> bool:
    conn = get_db()
    where = "session_id = ? AND user_id = ?"
    params = [
        review, json.dumps(scores or [], ensure_ascii=False),
        json.dumps(weak_points or [], ensure_ascii=False),
        json.dumps(overall or {}, ensure_ascii=False),
        session_id, user_id,
    ]
    if evaluation_token is not None:
        where += " AND json_extract(COALESCE(NULLIF(meta, ''), '{}'), '$.evaluation_claim_token') = ?"
        params.append(evaluation_token)
    cursor = conn.execute(
        "UPDATE sessions SET review = ?, scores = ?, weak_points = ?, overall = ?, updated_at = CURRENT_TIMESTAMP "
        f"WHERE {where}",
        params,
    )
    conn.commit()
    return cursor.rowcount > 0


def save_drill_progress(session_id: str, current_index: int,
                        partial_answers: dict, hints: dict, *, user_id: str) -> bool:
    """中途保存 drill / job_prep 进度到 meta.progress，不需要 schema 迁移。

    Returns True if the row was updated, False if no such session exists for
    this user — callers should treat False as 404. Completed sessions (review
    written) are read-only: a late progress POST (e.g. the frontend's unload
    flush racing the evaluation) must not overwrite their stored progress.
    """
    conn = get_db()
    progress = {
        "current_index": current_index,
        "partial_answers": {str(k): v for k, v in (partial_answers or {}).items()},
        "hints": {str(k): v for k, v in (hints or {}).items()},
        "updated_at": datetime.now().isoformat(),
    }
    cursor = conn.execute(
        "UPDATE sessions SET meta = json_set(COALESCE(NULLIF(meta, ''), '{}'), "
        "'$.progress', json(?)), updated_at = CURRENT_TIMESTAMP "
        "WHERE session_id = ? AND user_id = ? AND (review IS NULL OR review = '')",
        (json.dumps(progress, ensure_ascii=False), session_id, user_id),
    )
    conn.commit()
    if cursor.rowcount > 0:
        return True
    return conn.execute(
        "SELECT 1 FROM sessions WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone() is not None


SYNC_CLAIM_TTL_SECONDS = 30 * 60
EVALUATION_CLAIM_TTL_SECONDS = 60 * 60
RESUME_TURN_CLAIM_TTL_SECONDS = 30 * 60


def try_claim_session_sync(
    session_id: str,
    *,
    user_id: str,
    evaluation_token: str | None = None,
    target_group: str | None = None,
    target_topics: list[str] | None = None,
) -> str | None:
    """Atomically claim one-time profile / SR / knowledge side-effects.

    ``meta.synced_at`` is the terminal idempotency marker. ``sync_claimed_at``
    prevents concurrent /interview/end and /interview/sync calls from both
    applying side-effects before either has a chance to stamp ``synced_at``.
    A stale claim can be taken over after the TTL so an interrupted worker does
    not block manual repair forever.
    """
    conn = get_db()
    now = datetime.now()
    claimed_at = now.isoformat()
    claim_token = uuid.uuid4().hex
    stale_before = (now - timedelta(seconds=SYNC_CLAIM_TTL_SECONDS)).isoformat()
    evaluation_stale_before = (
        now - timedelta(seconds=EVALUATION_CLAIM_TTL_SECONDS)
    ).isoformat()
    resume_turn_stale_before = (
        now - timedelta(seconds=RESUME_TURN_CLAIM_TTL_SECONDS)
    ).isoformat()
    doc = "COALESCE(NULLIF(meta, ''), '{}')"
    if evaluation_token is not None:
        evaluation_gate = (
            f"AND json_extract({doc}, '$.evaluation_claim_token') = ?"
        )
        claim_doc = doc
    else:
        evaluation_gate = (
            f"AND (json_extract({doc}, '$.evaluation_claim_token') IS NULL "
            f"OR json_extract({doc}, '$.evaluation_claimed_at') IS NULL "
            f"OR json_extract({doc}, '$.evaluation_claimed_at') < ?)"
        )
        # Manual recovery may take over an evaluation claim left by a crashed
        # worker. Remove the old token in the same UPDATE so its later writes
        # fail the token fence.
        claim_doc = (
            f"json_remove({doc}, '$.evaluation_claimed_at', "
            "'$.evaluation_claim_token')"
        )
    claim_doc = (
        f"json_remove({claim_doc}, '$.resume_turn_claimed_at', "
        "'$.resume_turn_claim_token')"
    )
    freeze_params = []
    if isinstance(target_group, str) and target_group.strip():
        target_group = target_group.strip()
        normalized_targets = _normalize_sync_targets(target_topics or [])
        targets_doc = (
            f"COALESCE(json_extract({doc}, '$.sync_targets'), '{{}}')"
        )
        steps_doc = f"COALESCE(json_extract({doc}, '$.sync_steps'), '{{}}')"
        # Fold topics already touched by pre-freeze deployments into the first
        # frozen set. The supplied order wins; completed legacy topics follow.
        merged_targets = (
            "COALESCE((SELECT json_group_array(topic) FROM ("
            "SELECT topic, MIN(ord) AS first_ord FROM ("
            "SELECT value AS topic, CAST(key AS INTEGER) AS ord "
            "FROM json_each(json(?)) UNION ALL "
            "SELECT substr(key, instr(key, ':') + 1) AS topic, "
            f"1000000 + id AS ord FROM json_each({steps_doc}) "
            "WHERE key LIKE 'knowledge_extract:%' "
            "OR key LIKE 'high_freq:%') "
            "WHERE typeof(topic) = 'text' AND trim(topic) != '' "
            "GROUP BY topic ORDER BY first_ord)), '[]')"
        )
        claim_doc = (
            f"(CASE WHEN EXISTS (SELECT 1 FROM json_each({targets_doc}) "
            f"WHERE key = ?) THEN {claim_doc} ELSE json_set({claim_doc}, "
            f"'$.sync_targets', json_patch({targets_doc}, "
            f"json_object(?, json({merged_targets})))) END)"
        )
        freeze_params = [
            target_group,
            target_group,
            json.dumps(normalized_targets, ensure_ascii=False),
        ]
    # Keep this marker after a failed claim release. It freezes the persisted
    # evaluation as the recovery payload and prevents a newer drill/JD eval
    # from mixing its scores with partially applied side effects.
    pending_doc = (
        f"json_set({claim_doc}, '$.sync_pending_at', "
        f"COALESCE(json_extract({doc}, '$.sync_pending_at'), ?))"
    )
    claim_doc = (
        f"json_set(json_set({pending_doc}, '$.sync_claimed_at', ?), "
        "'$.sync_claim_token', ?)"
    )
    params = [
        *freeze_params,
        claimed_at,
        claimed_at,
        claim_token,
        session_id,
        user_id,
    ]
    if evaluation_token is not None:
        params.append(evaluation_token)
    else:
        params.append(evaluation_stale_before)
    params.append(resume_turn_stale_before)
    params.append(stale_before)
    cursor = conn.execute(
        f"""
        UPDATE sessions
        SET meta = {claim_doc},
            updated_at = CURRENT_TIMESTAMP
        WHERE session_id = ? AND user_id = ?
          AND json_extract({doc}, '$.synced_at') IS NULL
          {evaluation_gate}
          AND (
            mode != 'resume'
            OR json_extract({doc}, '$.resume_turn_claim_token') IS NULL
            OR json_extract({doc}, '$.resume_turn_claimed_at') IS NULL
            OR json_extract({doc}, '$.resume_turn_claimed_at') < ?
          )
          AND (
            json_extract({doc}, '$.sync_claimed_at') IS NULL
            OR json_extract({doc}, '$.sync_claimed_at') < ?
          )
        """,
        params,
    )
    conn.commit()
    return claim_token if cursor.rowcount > 0 else None


def _normalize_sync_targets(targets: object) -> list[str]:
    if not isinstance(targets, list):
        return []
    normalized = []
    seen = set()
    for target in targets:
        if not isinstance(target, str):
            continue
        target = target.strip()
        if not target or target in seen:
            continue
        normalized.append(target)
        seen.add(target)
    return normalized


def session_sync_targets(
    session_id: str,
    group: str,
    *,
    user_id: str,
    claim_token: str,
) -> list[str]:
    """Return a frozen side-effect target set to the current sync owner."""
    if not claim_token or not isinstance(group, str) or not group.strip():
        return []
    group = group.strip()
    conn = get_db()
    doc = "COALESCE(NULLIF(meta, ''), '{}')"
    row = conn.execute(
        f"""
        SELECT meta FROM sessions
        WHERE session_id = ? AND user_id = ?
          AND json_extract({doc}, '$.sync_claim_token') = ?
        """,
        (session_id, user_id, claim_token),
    ).fetchone()
    if not row:
        return []
    try:
        stored = json.loads(row["meta"] or "{}").get("sync_targets", {}).get(group)
    except (AttributeError, json.JSONDecodeError, TypeError):
        return []
    return _normalize_sync_targets(stored)


def mark_session_sync_step(session_id: str, step: str, *, user_id: str,
                           claim_token: str, result: dict | None = None) -> bool:
    """Persist one completed side-effect step while the caller owns the claim.

    ``result`` lets a retry reuse output produced by a completed side-effect.
    Older timestamp-only step values remain valid and readable.
    """
    if not claim_token or not step:
        return False
    conn = get_db()
    doc = "COALESCE(NULLIF(meta, ''), '{}')"
    completed_at = datetime.now().isoformat()
    if result is None:
        value_sql = "?"
        value_params = [completed_at]
    else:
        value_sql = "json(?)"
        value_params = [json.dumps({
            "completed_at": completed_at,
            "result": result,
        }, ensure_ascii=False)]
    cursor = conn.execute(
        f"UPDATE sessions SET meta = json_set({doc}, '$.sync_steps', "
        f"json_patch(COALESCE(json_extract({doc}, '$.sync_steps'), '{{}}'), "
        f"json_object(?, {value_sql}))), updated_at = CURRENT_TIMESTAMP "
        "WHERE session_id = ? AND user_id = ? "
        f"AND json_extract({doc}, '$.sync_claim_token') = ?",
        (step, *value_params, session_id, user_id, claim_token),
    )
    conn.commit()
    return cursor.rowcount > 0


def session_sync_steps(session_id: str, *, user_id: str) -> set[str]:
    """Return durable completed step names for an interrupted synchronization."""
    conn = get_db()
    row = conn.execute(
        "SELECT meta FROM sessions WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        return set()
    try:
        steps = json.loads(row["meta"] or "{}").get("sync_steps", {})
    except (json.JSONDecodeError, TypeError):
        return set()
    return set(steps) if isinstance(steps, dict) else set()


def session_sync_step_result(session_id: str, step: str, *, user_id: str) -> dict | None:
    """Return a persisted step result, tolerating legacy timestamp-only values."""
    conn = get_db()
    row = conn.execute(
        "SELECT meta FROM sessions WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        return None
    try:
        value = json.loads(row["meta"] or "{}").get("sync_steps", {}).get(step)
    except (AttributeError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    result = value.get("result")
    return result if isinstance(result, dict) else None

def mark_session_synced(session_id: str, *, user_id: str, claim_token: str) -> bool:
    """Stamp ``meta.synced_at`` to record that the profile / SR / knowledge
    side-effects have been applied for this session.

    This is the authoritative idempotency marker for the manual "同步" fallback
    (and is also set on the normal eval path): once present, a re-sync is a
    no-op so EWMA / SR / high-freq counters are never double-counted.
    """
    if not claim_token:
        return False
    conn = get_db()
    doc = "COALESCE(NULLIF(meta, ''), '{}')"
    cursor = conn.execute(
        f"UPDATE sessions SET meta = json_remove(json_set({doc}, "
        "'$.synced_at', ?), '$.sync_claimed_at', '$.sync_claim_token', "
        "'$.sync_pending_at'), "
        "updated_at = CURRENT_TIMESTAMP WHERE session_id = ? AND user_id = ? "
        f"AND json_extract({doc}, '$.sync_claim_token') = ?",
        (datetime.now().isoformat(), session_id, user_id, claim_token),
    )
    conn.commit()
    return cursor.rowcount > 0


def release_session_sync_claim(session_id: str, *, user_id: str, claim_token: str) -> bool:
    """Release a failed claim only while this worker still owns it."""
    if not claim_token:
        return False
    conn = get_db()
    doc = "COALESCE(NULLIF(meta, ''), '{}')"
    cursor = conn.execute(
        f"UPDATE sessions SET meta = json_remove({doc}, "
        "'$.sync_claimed_at', '$.sync_claim_token'), updated_at = CURRENT_TIMESTAMP "
        "WHERE session_id = ? AND user_id = ? "
        f"AND json_extract({doc}, '$.sync_claim_token') = ?",
        (session_id, user_id, claim_token),
    )
    conn.commit()
    return cursor.rowcount > 0


def abort_session_sync_claim(
    session_id: str, *, user_id: str, claim_token: str,
) -> bool:
    """Abandon a fresh sync claim before any side-effect step was applied.

    Unlike ``release_session_sync_claim``, this clears ``sync_pending_at`` so a
    payload rejected before its first side effect does not leave the session in
    recovery mode. Once a step exists, callers must use the normal release path.
    """
    if not claim_token:
        return False
    conn = get_db()
    doc = "COALESCE(NULLIF(meta, ''), '{}')"
    cursor = conn.execute(
        f"UPDATE sessions SET meta = json_remove({doc}, "
        "'$.sync_claimed_at', '$.sync_claim_token', '$.sync_pending_at'), "
        "updated_at = CURRENT_TIMESTAMP WHERE session_id = ? AND user_id = ? "
        f"AND json_extract({doc}, '$.sync_claim_token') = ? "
        "AND NOT EXISTS (SELECT 1 FROM json_each(COALESCE("
        f"json_extract({doc}, '$.sync_steps'), '{{}}')))",
        (session_id, user_id, claim_token),
    )
    conn.commit()
    return cursor.rowcount > 0


def try_claim_resume_turn(session_id: str, *, user_id: str) -> str | None:
    """Atomically claim one in-flight resume chat turn.

    A turn claim is mutually exclusive with evaluation and side-effect sync
    claims. Expired claims may be taken over; the old token is removed in the
    same UPDATE so a paused worker cannot commit its transcript afterwards.
    """
    conn = get_db()
    now = datetime.now()
    claimed_at = now.isoformat()
    claim_token = uuid.uuid4().hex
    evaluation_stale_before = (
        now - timedelta(seconds=EVALUATION_CLAIM_TTL_SECONDS)
    ).isoformat()
    sync_stale_before = (
        now - timedelta(seconds=SYNC_CLAIM_TTL_SECONDS)
    ).isoformat()
    turn_stale_before = (
        now - timedelta(seconds=RESUME_TURN_CLAIM_TTL_SECONDS)
    ).isoformat()
    doc = "COALESCE(NULLIF(meta, ''), '{}')"
    # Remove stale competing claims as part of the takeover. Active claims are
    # rejected by the predicates below, while a stale worker's later writes are
    # fenced by its now-absent token.
    claimed_doc = (
        f"json_remove({doc}, '$.evaluation_claimed_at', "
        "'$.evaluation_claim_token', '$.sync_claimed_at', "
        "'$.sync_claim_token')"
    )
    claimed_doc = (
        f"json_set(json_set({claimed_doc}, '$.resume_turn_claimed_at', ?), "
        "'$.resume_turn_claim_token', ?)"
    )
    cursor = conn.execute(
        f"""
        UPDATE sessions
        SET meta = {claimed_doc}, updated_at = CURRENT_TIMESTAMP
        WHERE session_id = ? AND user_id = ? AND mode = 'resume'
          AND (review IS NULL OR review = '')
          AND json_extract({doc}, '$.sync_pending_at') IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM json_each(
              COALESCE(json_extract({doc}, '$.sync_steps'), '{{}}')
            )
          )
          AND (
            json_extract({doc}, '$.evaluation_claim_token') IS NULL
            OR json_extract({doc}, '$.evaluation_claimed_at') IS NULL
            OR json_extract({doc}, '$.evaluation_claimed_at') < ?
          )
          AND (
            json_extract({doc}, '$.sync_claim_token') IS NULL
            OR json_extract({doc}, '$.sync_claimed_at') IS NULL
            OR json_extract({doc}, '$.sync_claimed_at') < ?
          )
          AND (
            json_extract({doc}, '$.resume_turn_claim_token') IS NULL
            OR json_extract({doc}, '$.resume_turn_claimed_at') IS NULL
            OR json_extract({doc}, '$.resume_turn_claimed_at') < ?
          )
        """,
        (
            claimed_at, claim_token, session_id, user_id,
            evaluation_stale_before, sync_stale_before, turn_stale_before,
        ),
    )
    conn.commit()
    return claim_token if cursor.rowcount > 0 else None


def renew_resume_turn_claim(
    session_id: str, *, user_id: str, claim_token: str,
) -> bool:
    """Refresh an owned resume-turn lease without changing its fencing token."""
    if not claim_token:
        return False
    conn = get_db()
    doc = "COALESCE(NULLIF(meta, ''), '{}')"
    cursor = conn.execute(
        f"UPDATE sessions SET meta = json_set({doc}, "
        "'$.resume_turn_claimed_at', ?), updated_at = CURRENT_TIMESTAMP "
        "WHERE session_id = ? AND user_id = ? AND mode = 'resume' "
        "AND (review IS NULL OR review = '') "
        f"AND json_extract({doc}, '$.resume_turn_claim_token') = ?",
        (datetime.now().isoformat(), session_id, user_id, claim_token),
    )
    conn.commit()
    return cursor.rowcount > 0


def commit_resume_turn(
    session_id: str,
    messages: list[dict],
    *,
    user_id: str,
    claim_token: str,
    phase: str | None = None,
    is_finished: bool | None = None,
) -> bool:
    """Append one resume turn only while its durable claim is still owned.

    The transcript append and claim release are one SQLite transaction. A
    stale/reclaimed token, a completed session, or an empty batch is a no-op.
    """
    if not claim_token or not messages:
        return False
    conn = get_db()
    now = datetime.now().isoformat()
    encoded = [
        json.dumps(
            {
                "role": message.get("role", ""),
                "content": message.get("content", ""),
                "time": message.get("time") or now,
            },
            ensure_ascii=False,
        )
        for message in messages
        if isinstance(message, dict)
    ]
    if not encoded:
        return False
    inserts = ", ".join("'$[#]', json(?)" for _ in encoded)
    doc = "COALESCE(NULLIF(meta, ''), '{}')"
    meta_expr = (
        f"json_remove({doc}, '$.resume_turn_claimed_at', "
        "'$.resume_turn_claim_token')"
    )
    state_params = []
    if phase is not None:
        meta_expr = f"json_set({meta_expr}, '$.resume_phase', ?)"
        state_params.append(str(phase))
    if is_finished is not None:
        meta_expr = f"json_set({meta_expr}, '$.resume_is_finished', json(?))"
        state_params.append("true" if is_finished else "false")
    cursor = conn.execute(
        "UPDATE sessions SET transcript = json_insert("
        f"COALESCE(NULLIF(transcript, ''), '[]'), {inserts}), "
        f"meta = {meta_expr}, updated_at = CURRENT_TIMESTAMP "
        "WHERE session_id = ? AND user_id = ? AND mode = 'resume' "
        "AND (review IS NULL OR review = '') "
        f"AND json_extract({doc}, '$.resume_turn_claim_token') = ?",
        [*encoded, *state_params, session_id, user_id, claim_token],
    )
    conn.commit()
    return cursor.rowcount > 0


def replace_resume_reply(
    session_id: str,
    *,
    user_id: str,
    claim_token: str,
    expected_user_message: str,
    assistant_message: str,
    phase: str | None = None,
    is_finished: bool | None = None,
) -> bool:
    """Replace the trailing assistant reply while an owned turn claim is held."""
    if not claim_token or not assistant_message:
        return False
    conn = get_db()
    now = datetime.now().isoformat()
    transcript = "COALESCE(NULLIF(transcript, ''), '[]')"
    doc = "COALESCE(NULLIF(meta, ''), '{}')"
    meta_expr = (
        f"json_remove({doc}, '$.resume_turn_claimed_at', "
        "'$.resume_turn_claim_token')"
    )
    state_params = []
    if phase is not None:
        meta_expr = f"json_set({meta_expr}, '$.resume_phase', ?)"
        state_params.append(str(phase))
    if is_finished is not None:
        meta_expr = f"json_set({meta_expr}, '$.resume_is_finished', json(?))"
        state_params.append("true" if is_finished else "false")
    replacement = json.dumps(
        {"role": "assistant", "content": assistant_message, "time": now},
        ensure_ascii=False,
    )
    cursor = conn.execute(
        f"UPDATE sessions SET transcript = json_set({transcript}, '$[#-1]', json(?)), "
        f"meta = {meta_expr}, updated_at = CURRENT_TIMESTAMP "
        "WHERE session_id = ? AND user_id = ? AND mode = 'resume' "
        "AND (review IS NULL OR review = '') "
        f"AND json_extract({doc}, '$.resume_turn_claim_token') = ? "
        f"AND json_extract({transcript}, '$[#-1].role') = 'assistant' "
        f"AND json_extract({transcript}, '$[#-2].role') = 'user' "
        f"AND json_extract({transcript}, '$[#-2].content') = ?",
        [replacement, *state_params, session_id, user_id, claim_token,
         expected_user_message],
    )
    conn.commit()
    return cursor.rowcount > 0


def release_resume_turn_claim(
    session_id: str, *, user_id: str, claim_token: str,
) -> bool:
    """Release a resume turn claim only while this worker owns its token."""
    if not claim_token:
        return False
    conn = get_db()
    doc = "COALESCE(NULLIF(meta, ''), '{}')"
    cursor = conn.execute(
        f"UPDATE sessions SET meta = json_remove({doc}, "
        "'$.resume_turn_claimed_at', '$.resume_turn_claim_token'), "
        "updated_at = CURRENT_TIMESTAMP WHERE session_id = ? AND user_id = ? "
        "AND mode = 'resume' "
        f"AND json_extract({doc}, '$.resume_turn_claim_token') = ?",
        (session_id, user_id, claim_token),
    )
    conn.commit()
    return cursor.rowcount > 0


def withdraw_resume_user_tail(
    session_id: str, *, user_id: str, claim_token: str, expected_message: str,
) -> bool:
    """Remove the trailing unanswered user message while an owned claim is held.

    Used by the "edit my answer" recovery path: the candidate abandons a turn
    whose assistant reply never committed. The tail must still be that exact
    user message — a completed reply or a different pending turn is a no-op so
    a concurrent worker's commit can never be clobbered. Removal and claim
    release are one transaction, mirroring ``commit_resume_turn``.
    """
    if not claim_token:
        return False
    conn = get_db()
    transcript = "COALESCE(NULLIF(transcript, ''), '[]')"
    doc = "COALESCE(NULLIF(meta, ''), '{}')"
    meta_expr = (
        f"json_remove({doc}, '$.resume_turn_claimed_at', "
        "'$.resume_turn_claim_token')"
    )
    cursor = conn.execute(
        f"UPDATE sessions SET transcript = json_remove({transcript}, '$[#-1]'), "
        f"meta = {meta_expr}, updated_at = CURRENT_TIMESTAMP "
        "WHERE session_id = ? AND user_id = ? AND mode = 'resume' "
        "AND (review IS NULL OR review = '') "
        f"AND json_extract({doc}, '$.resume_turn_claim_token') = ? "
        f"AND json_extract({transcript}, '$[#-1].role') = 'user' "
        f"AND json_extract({transcript}, '$[#-1].content') = ?",
        (session_id, user_id, claim_token, expected_message),
    )
    conn.commit()
    return cursor.rowcount > 0


def try_claim_session_evaluation(session_id: str, *, user_id: str) -> str | None:
    """Claim report generation so concurrent /end calls cannot race writes."""
    conn = get_db()
    now = datetime.now()
    claim_token = uuid.uuid4().hex
    stale_before = (now - timedelta(seconds=EVALUATION_CLAIM_TTL_SECONDS)).isoformat()
    sync_stale_before = (now - timedelta(seconds=SYNC_CLAIM_TTL_SECONDS)).isoformat()
    resume_turn_stale_before = (
        now - timedelta(seconds=RESUME_TURN_CLAIM_TTL_SECONDS)
    ).isoformat()
    doc = "COALESCE(NULLIF(meta, ''), '{}')"
    # Fencing an expired sync token is part of the same UPDATE. A stale worker
    # may still be unwinding in another thread, but its later step/terminal
    # writes will fail the token predicate instead of mutating this generation.
    evaluation_doc = (
        f"json_remove({doc}, '$.sync_claimed_at', '$.sync_claim_token', "
        "'$.resume_turn_claimed_at', '$.resume_turn_claim_token')"
    )
    cursor = conn.execute(
        f"UPDATE sessions SET meta = json_set(json_set({evaluation_doc}, "
        "'$.evaluation_claimed_at', ?), '$.evaluation_claim_token', ?), "
        "updated_at = CURRENT_TIMESTAMP WHERE session_id = ? AND user_id = ? "
        f"AND (json_extract({doc}, '$.evaluation_claimed_at') IS NULL "
        f"OR json_extract({doc}, '$.evaluation_claimed_at') < ?) "
        f"AND (json_extract({doc}, '$.sync_claimed_at') IS NULL "
        f"OR json_extract({doc}, '$.sync_claimed_at') < ?) "
        f"AND (mode != 'resume' "
        f"OR json_extract({doc}, '$.resume_turn_claim_token') IS NULL "
        f"OR json_extract({doc}, '$.resume_turn_claimed_at') IS NULL "
        f"OR json_extract({doc}, '$.resume_turn_claimed_at') < ?) "
        f"AND (mode != 'resume' "
        f"OR COALESCE(json_extract(COALESCE(NULLIF(transcript, ''), '[]'), "
        f"'$[#-1].role'), '') != 'user') "
        # Drill/JD recovery must use the persisted scores until every prior
        # side-effect step is complete. Resume can re-enter through its graph;
        # its profile operation marker remains idempotent across retries.
        f"AND (mode = 'resume' "
        f"OR json_extract({doc}, '$.synced_at') IS NOT NULL "
        f"OR (json_extract({doc}, '$.sync_pending_at') IS NULL AND NOT EXISTS ("
        f"SELECT 1 FROM json_each(COALESCE(json_extract({doc}, '$.sync_steps'), '{{}}'))"
        f")))",
        (now.isoformat(), claim_token, session_id, user_id,
         stale_before, sync_stale_before, resume_turn_stale_before),
    )
    conn.commit()
    return claim_token if cursor.rowcount > 0 else None


def release_session_evaluation_claim(session_id: str, *, user_id: str,
                                     claim_token: str) -> bool:
    """Release only the evaluation generation still owned by this worker."""
    if not claim_token:
        return False
    conn = get_db()
    doc = "COALESCE(NULLIF(meta, ''), '{}')"
    cursor = conn.execute(
        f"UPDATE sessions SET meta = json_remove({doc}, "
        "'$.evaluation_claimed_at', '$.evaluation_claim_token'), "
        "updated_at = CURRENT_TIMESTAMP WHERE session_id = ? AND user_id = ? "
        f"AND json_extract({doc}, '$.evaluation_claim_token') = ?",
        (session_id, user_id, claim_token),
    )
    conn.commit()
    return cursor.rowcount > 0


def get_session(session_id: str, *, user_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM sessions WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["transcript"] = json.loads(result["transcript"])
    result["meta"] = json.loads(result.get("meta", "{}") or "{}")
    result["questions"] = json.loads(result.get("questions", "[]"))
    result["scores"] = json.loads(result["scores"])
    result["weak_points"] = json.loads(result["weak_points"])
    result["overall"] = json.loads(result.get("overall", "{}") or "{}")
    result["reference_answers"] = json.loads(result.get("reference_answers", "{}") or "{}")
    return result


def mark_resume_session_initialized(session_id: str, *, user_id: str) -> bool:
    conn = get_db()
    cur = conn.execute(
        "UPDATE sessions SET meta = json_set("
        "COALESCE(NULLIF(meta, ''), '{}'), "
        "'$.initialization_status', 'ready', "
        "'$.resume_phase', 'greeting', "
        "'$.resume_is_finished', json('false')"
        "), updated_at = CURRENT_TIMESTAMP WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    )
    conn.commit()
    return cur.rowcount > 0


def list_sessions_by_topic(topic: str, *, user_id: str, limit: int = 50) -> list[dict]:
    """Get all sessions for a topic with reviews and scores."""
    conn = get_db()
    rows = conn.execute(
        "SELECT session_id, mode, topic, review, scores, created_at, updated_at FROM sessions "
        "WHERE topic = ? AND user_id = ? AND review IS NOT NULL ORDER BY created_at ASC LIMIT ?",
        (topic, user_id, limit),
    ).fetchall()
    results = []
    for r in rows:
        results.append({
            "session_id": r["session_id"],
            "review": r["review"],
            "scores": json.loads(r["scores"]) if r["scores"] else [],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        })
    return results


def list_sessions(
    *, user_id: str,
    limit: int = 20,
    offset: int = 0,
    mode: str | None = None,
    topic: str | None = None,
    status: str = "completed",
) -> dict:
    conn = get_db()

    if status == "in_progress":
        where = ["review IS NULL", "user_id = ?"]
    elif status == "all":
        where = ["user_id = ?"]
    else:
        where = ["review IS NOT NULL", "user_id = ?"]
    params: list = [user_id]
    if mode:
        where.append("mode = ?")
        params.append(mode)
    if topic:
        where.append("topic = ?")
        params.append(topic)
    where_sql = " AND ".join(where)

    total = conn.execute(
        f"SELECT COUNT(*) FROM sessions WHERE {where_sql}", params,
    ).fetchone()[0]

    rows = conn.execute(
        f"SELECT session_id, mode, topic, meta, questions, transcript, created_at, overall FROM sessions "
        f"WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    items = []
    for r in rows:
        overall = json.loads(r["overall"] or "{}")
        meta = json.loads(r["meta"] or "{}")
        item = {
            "session_id": r["session_id"],
            "mode": r["mode"],
            "topic": r["topic"],
            "meta": meta,
            "created_at": r["created_at"],
            "avg_score": overall.get("avg_score"),
        }
        # For in-progress batch drills, surface whether an evaluation was already
        # attempted but didn't complete (review IS NULL) — lets the UI offer a
        # "re-evaluate" affordance instead of "continue".
        #
        # Signal: a non-empty transcript. For drill/jd, the transcript is written
        # ONLY by save_drill_answers when 提交评估 is clicked — never during
        # answering (mid-drill progress goes to meta.progress, not transcript).
        # So in_progress + transcript-with-answers == evaluation failed/incomplete.
        # This is far more reliable than counting partial_answers, whose last
        # entry is often still in the autosave debounce window at submit time.
        if status == "in_progress" and r["mode"] in ("topic_drill", "jd_prep"):
            questions = json.loads(r["questions"] or "[]")
            transcript = json.loads(r["transcript"] or "[]")
            answered_count = sum(
                1 for m in transcript
                if m.get("role") == "user" and (m.get("content") or "").strip()
            )
            item["question_count"] = len(questions)
            item["answered_count"] = answered_count
            item["awaiting_eval"] = answered_count > 0
        items.append(item)
    return {"items": items, "total": total}


def delete_session(session_id: str, *, user_id: str) -> bool:
    conn = get_db()
    cursor = conn.execute(
        "DELETE FROM sessions WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    )
    if cursor.rowcount > 0:
        # No FK cascade — clean up companion rows so a deleted session can't be
        # resumed from live_sessions and doesn't leave orphan metrics behind.
        conn.execute(
            "DELETE FROM live_sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        )
        conn.execute(
            "DELETE FROM rag_metrics WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        )
    conn.commit()
    return cursor.rowcount > 0


def list_distinct_topics(*, user_id: str) -> list[str]:
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT topic FROM sessions "
        "WHERE topic IS NOT NULL AND review IS NOT NULL AND user_id = ? ORDER BY topic",
        (user_id,),
    ).fetchall()
    return [r["topic"] for r in rows]


def get_reference_answer(session_id: str, question_id, *, user_id: str) -> str | None:
    conn = get_db()
    row = conn.execute(
        "SELECT reference_answers FROM sessions WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        return None
    answers = json.loads(row["reference_answers"] or "{}")
    return answers.get(str(question_id))


def save_reference_answer(session_id: str, question_id, answer: str, *, user_id: str):
    """Atomically cache one answer without clobbering concurrent questions."""
    conn = get_db()
    conn.execute(
        "UPDATE sessions SET reference_answers = json_patch("
        "COALESCE(NULLIF(reference_answers, ''), '{}'), json_object(?, ?)), "
        "updated_at = CURRENT_TIMESTAMP WHERE session_id = ? AND user_id = ?",
        (str(question_id), answer or "", session_id, user_id),
    )
    conn.commit()
