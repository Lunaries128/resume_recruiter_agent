import json
import threading
import uuid

from collections import defaultdict

import database as db

from jd_parser import (
    Condition,
    ParsedJob,
    Job,
    FIELDS,
    KINDS,
    DIMENSIONS,
    DIMENSION_OF,
    LEVELS,
    parse_jd,
)

from capability_engine import (
    fingerprint,
    extract_assessment,
    score_profile,
)


_locks = defaultdict(
    threading.Lock
)


def init():
    with db.get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS job_templates (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL
                    REFERENCES sessions(id)
                    ON DELETE CASCADE,
                name TEXT NOT NULL,
                payload TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                UNIQUE(session_id, id)
            );

            CREATE TABLE IF NOT EXISTS active_jobs (
                session_id TEXT PRIMARY KEY
                    REFERENCES sessions(id)
                    ON DELETE CASCADE,
                job_id TEXT NOT NULL,
                FOREIGN KEY(session_id, job_id)
                    REFERENCES job_templates(
                        session_id,
                        id
                    )
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS job_results (
                session_id TEXT NOT NULL,
                job_id TEXT NOT NULL,
                candidate_code TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY(
                    session_id,
                    job_id,
                    candidate_code
                ),
                FOREIGN KEY(session_id, job_id)
                    REFERENCES job_templates(
                        session_id,
                        id
                    )
                    ON DELETE CASCADE,
                FOREIGN KEY(
                    session_id,
                    candidate_code
                )
                    REFERENCES candidates(
                        session_id,
                        candidate_code
                    )
                    ON DELETE CASCADE
            );
            """
        )


def templates(sid):
    db.get_session(sid)

    with db.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM job_templates
            WHERE session_id = ?
            ORDER BY updated_at DESC
            """,
            (sid,),
        ).fetchall()

        active = conn.execute(
            """
            SELECT job_id
            FROM active_jobs
            WHERE session_id = ?
            """,
            (sid,),
        ).fetchone()

    return {
        "templates": [
            dict(
                row,
                payload=json.loads(
                    row["payload"]
                ),
            )
            for row in rows
        ],
        "active_id": (
            active[0]
            if active
            else None
        ),
    }


def get_job(sid, jid):
    with db.get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM job_templates
            WHERE session_id = ?
                AND id = ?
            """,
            (sid, jid),
        ).fetchone()

    if row is None:
        raise ValueError(
            "当前会话不存在该岗位模板。"
        )

    payload = Job.model_validate(
        json.loads(row["payload"])
    ).model_dump()

    return dict(
        row,
        payload=payload,
    )


def requirement_summary(job):
    return "；".join(
        f"{item['kind']}："
        f"{item['field']} "
        f"{item['value']} "
        f"{item.get('scope', '')}"
        for item in job["conditions"]
    )


def activate(sid, jid):
    job = get_job(sid, jid)

    with db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO active_jobs
            VALUES(?, ?)
            ON CONFLICT(session_id)
            DO UPDATE SET
                job_id = excluded.job_id
            """,
            (sid, jid),
        )

    db.update_session(
        sid,
        jd=(
            "当前岗位："
            + job["name"]
            + "\n"
            + requirement_summary(
                job["payload"]
            )
        ),
    )

    return job


def save_job(
    sid,
    payload,
    jid=None,
):
    db.get_session(sid)

    data = Job.model_validate(
        payload
    ).model_dump()

    serialized = json.dumps(
        data,
        ensure_ascii=False,
    )

    with db.get_connection() as conn:
        if jid:
            cursor = conn.execute(
                """
                UPDATE job_templates
                SET name = ?,
                    payload = ?,
                    version = version + 1,
                    updated_at = ?
                WHERE session_id = ?
                    AND id = ?
                """,
                (
                    data["name"],
                    serialized,
                    db.now(),
                    sid,
                    jid,
                ),
            )

            if not cursor.rowcount:
                raise ValueError(
                    "岗位不存在或不属于当前会话。"
                )

        else:
            jid = uuid.uuid4().hex

            conn.execute(
                """
                INSERT INTO job_templates(
                    id,
                    session_id,
                    name,
                    payload,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    jid,
                    sid,
                    data["name"],
                    serialized,
                    db.now(),
                ),
            )

    activate(sid, jid)

    return get_job(sid, jid)


def delete_job(sid, jid):
    get_job(sid, jid)

    active = (
        templates(sid)["active_id"]
        == jid
    )

    with db.get_connection() as conn:
        conn.execute(
            """
            DELETE FROM job_templates
            WHERE session_id = ?
                AND id = ?
            """,
            (sid, jid),
        )

    if active:
        db.update_session(
            sid,
            jd="",
        )


def get_profile(sid, code):
    with db.session_scope(sid):
        profile = db.get_candidate(code)

    if not profile:
        raise ValueError(
            "当前会话不存在该候选人。"
        )

    return profile


def cached(
    sid,
    jid,
    code,
    key,
):
    with db.get_connection() as conn:
        row = conn.execute(
            """
            SELECT payload
            FROM job_results
            WHERE session_id = ?
                AND job_id = ?
                AND candidate_code = ?
                AND fingerprint = ?
            """,
            (
                sid,
                jid,
                code,
                key,
            ),
        ).fetchone()

    return (
        json.loads(row[0])
        if row
        else {}
    )


def assess_candidate(
    sid,
    jid,
    code,
):
    """
    显式执行匹配。

    模型调用不持有数据库写事务。
    模型返回后重新验证数据是否变化，
    防止把过期结果写入数据库。
    """
    lock = _locks[
        (sid, jid, code)
    ]

    if not lock.acquire(
        blocking=False
    ):
        raise ValueError(
            "该候选人正在匹配，"
            "请稍后刷新，不要重复提交。"
        )

    try:
        job = get_job(
            sid,
            jid,
        )

        candidate = get_profile(
            sid,
            code,
        )

        key = fingerprint(
            candidate,
            job["payload"],
        )

        saved = cached(
            sid,
            jid,
            code,
            key,
        )

        if (
            saved.get("status")
            == "completed"
        ):
            return score_profile(
                candidate,
                job["payload"],
                saved["assessment"],
            )

        try:
            assessment = (
                extract_assessment(
                    candidate,
                    job["payload"],
                )
            )

            payload = {
                "status": "completed",
                "assessment": assessment,
            }

        except Exception as exc:
            # 不保存模型提供商的原始响应，
            # 避免其中包含凭据或原始个人资料。
            payload = {
                "status": "failed",
                "error": (
                    "匹配失败"
                    f"（{type(exc).__name__}），"
                    "请检查模型配置、网络"
                    "或稍后重试；"
                    "未计为0分。"
                ),
            }

        with db.get_connection() as conn:
            conn.execute(
                "BEGIN IMMEDIATE"
            )

            current_job = get_job(
                sid,
                jid,
            )

            current_profile = get_profile(
                sid,
                code,
            )

            current_key = fingerprint(
                current_profile,
                current_job["payload"],
            )

            if current_key != key:
                raise ValueError(
                    "匹配期间岗位条件或简历已变化，"
                    "请重新匹配。"
                )

            conn.execute(
                """
                INSERT INTO job_results
                VALUES(?, ?, ?, ?, ?)

                ON CONFLICT(
                    session_id,
                    job_id,
                    candidate_code
                )
                DO UPDATE SET
                    fingerprint = excluded.fingerprint,
                    payload = excluded.payload
                """,
                (
                    sid,
                    jid,
                    code,
                    key,
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                    ),
                ),
            )

        if payload["status"] == "failed":
            raise ValueError(
                payload["error"]
            )

        return score_profile(
            current_profile,
            current_job["payload"],
            assessment,
        )

    finally:
        lock.release()


def ranked_candidates(
    sid,
    jid=None,
):
    """
    只读取缓存和进行本地计算。
    这里绝不调用大模型。
    """
    jid = (
        jid
        or templates(sid)["active_id"]
    )

    with db.session_scope(sid):
        candidates = db.list_candidates()

    job = (
        get_job(sid, jid)["payload"]
        if jid
        else None
    )

    for item in candidates:
        item.update(
            match_score=None,
            score_detail={},
            hard_status=(
                "未匹配"
                if job
                else "未选择岗位"
            ),
            matching_status="pending",
            matching_error="",
        )

        if not job:
            continue

        key = fingerprint(
            item,
            job,
        )

        saved = cached(
            sid,
            jid,
            item["candidate_code"],
            key,
        )

        if (
            saved.get("status")
            == "completed"
        ):
            detail = score_profile(
                item,
                job,
                saved["assessment"],
            )

            item.update(
                match_score=detail[
                    "total_score"
                ],
                score_detail=detail,
                hard_status=detail[
                    "hard_status"
                ],
                matching_status="completed",
            )

        elif (
            saved.get("status")
            == "failed"
        ):
            item.update(
                hard_status="匹配失败",
                matching_status="failed",
                matching_error=saved["error"],
            )

    return sorted(
        candidates,
        key=lambda item: (
            item["match_score"] is not None,
            item["match_score"] or 0,
        ),
        reverse=True,
    )


init()