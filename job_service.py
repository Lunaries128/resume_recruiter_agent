import json
import re
import uuid

from hashlib import sha256

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
                    REFERENCES job_templates(session_id, id)
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
                    REFERENCES job_templates(session_id, id)
                    ON DELETE CASCADE,
                FOREIGN KEY(session_id, candidate_code)
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
                payload=json.loads(row["payload"]),
            )
            for row in rows
        ],
        "active_id": (
            active["job_id"]
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
            WHERE session_id = ? AND id = ?
            """,
            (sid, jid),
        ).fetchone()

    if row is None:
        raise ValueError(
            "当前会话不存在这个岗位模板。"
        )

    return dict(
        row,
        payload=json.loads(row["payload"]),
    )


def activate(sid, jid):
    job = get_job(sid, jid)

    with db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO active_jobs VALUES(?, ?)
            ON CONFLICT(session_id)
            DO UPDATE SET job_id = excluded.job_id
            """,
            (sid, jid),
        )

    db.update_session(
        sid,
        jd=(
            "当前岗位："
            + job["name"]
            + "\n"
            + requirement_summary(job["payload"])
        ),
    )

    return job


def save_job(sid, payload, jid=None):
    db.get_session(sid)

    data = Job.model_validate(
        payload
    ).model_dump()

    serialized = json.dumps(
        data,
        ensure_ascii=False,
    )

    if jid:
        get_job(sid, jid)

        with db.get_connection() as conn:
            conn.execute(
                """
                UPDATE job_templates
                SET name = ?,
                    payload = ?,
                    version = version + 1,
                    updated_at = ?
                WHERE session_id = ? AND id = ?
                """,
                (
                    data["name"],
                    serialized,
                    db.now(),
                    sid,
                    jid,
                ),
            )

    else:
        jid = uuid.uuid4().hex

        with db.get_connection() as conn:
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

    was_active = (
        templates(sid)["active_id"] == jid
    )

    with db.get_connection() as conn:
        conn.execute(
            """
            DELETE FROM job_templates
            WHERE session_id = ? AND id = ?
            """,
            (sid, jid),
        )

    if was_active:
        db.update_session(
            sid,
            jd="",
        )


def terms(value):
    return [
        item.strip()
        for item in re.split(
            r"[;；、,，]",
            value,
        )
        if item.strip()
    ]


def normalized(value):
    text = re.sub(
        r"\s+",
        " ",
        str(value).lower(),
    ).strip()

    aliases = {
        "py": "python",
        "pytorch框架": "pytorch",
        "推荐算法": "推荐系统",
    }

    return aliases.get(text, text)


def candidate_values(candidate, field):
    if field == "学历":
        return [
            item.get("degree", "")
            for item in candidate.get("education", [])
            if item.get("degree")
        ]

    if field == "专业":
        return [
            item.get("major", "")
            for item in candidate.get("education", [])
            if item.get("major")
        ]

    if field == "工作年限":
        missing = " ".join(
            candidate.get("missing_fields", [])
        )

        value = candidate.get(
            "work_years",
            0,
        )

        if (
            not value
            and (
                "年限" in missing
                or not candidate.get("experiences")
            )
        ):
            return []

        return [str(value)]

    if field == "技能":
        values = list(
            candidate.get("skills", [])
        )

        items = (
            candidate.get("projects", [])
            + candidate.get("experiences", [])
        )

        for item in items:
            values += item.get(
                "technologies",
                [],
            )

        return values

    if field == "项目":
        values = []

        items = (
            candidate.get("projects", [])
            + candidate.get("experiences", [])
        )

        for item in items:
            values += [
                item.get("name", ""),
                item.get("role", ""),
                item.get("description", ""),
            ]

            values += item.get(
                "actions",
                [],
            )

            values += item.get(
                "results",
                [],
            )

            values += item.get(
                "technologies",
                [],
            )

        return [
            value
            for value in values
            if value
        ]

    awards = [
        " ".join(
            str(item.get(key, ""))
            for key in (
                "name",
                "level",
                "date",
            )
        )
        for item in candidate.get("awards", [])
    ]

    return awards + candidate.get(
        "certificates",
        [],
    )


def evaluate(candidate, condition):
    values = candidate_values(
        candidate,
        condition["field"],
    )

    requested = terms(
        condition["value"]
    )

    if not values:
        return (
            0.0,
            "待核实",
            "相关字段未提供，不能据此断定不具备能力。",
        )

    if condition["rule"] == "最低":
        if condition["field"] == "工作年限":
            matched = (
                float(values[0])
                >= float(condition["value"])
            )

        else:
            actual = max(
                [
                    level
                    for text in values
                    for name, level in LEVELS.items()
                    if name in text
                ]
                or [0]
            )

            matched = (
                actual >= LEVELS[
                    condition["value"]
                ]
            )

        ratio = float(matched)

    else:
        hits = []

        for term in requested:
            if condition["field"] == "技能":
                hit = normalized(term) in {
                    normalized(value)
                    for value in values
                }

            else:
                hit = any(
                    normalized(term)
                    in normalized(value)
                    for value in values
                )

            hits.append(hit)

        if condition["rule"] == "任一":
            ratio = float(any(hits))

        else:
            ratio = (
                sum(hits)
                / max(len(hits), 1)
            )

    if ratio == 1:
        state = "满足"

    elif ratio > 0:
        state = "部分满足"

    else:
        state = "未匹配"

    return (
        ratio,
        state,
        "；".join(values)[:800],
    )


def requirement_summary(job):
    return (
        "；".join(
            f"{item['kind']}："
            f"{item['field']} {item['value']}"
            f"（{item['rule']}）"
            for item in job["conditions"]
        )
        or "无明确条件"
    )


def score_profile(candidate, job):
    rows = []

    for condition in job["conditions"]:
        ratio, state, evidence = evaluate(
            candidate,
            condition,
        )

        rows.append(
            {
                **condition,
                "ratio": ratio,
                "state": state,
                "evidence": evidence,
            }
        )

    dimensions = []

    def mean(items):
        if not items:
            return 0

        return (
            sum(item["ratio"] for item in items)
            / len(items)
        )

    for name in DIMENSIONS:
        group = [
            row
            for row in rows
            if DIMENSION_OF[row["field"]] == name
        ]

        base = [
            row
            for row in group
            if row["kind"] != "优先加分"
        ]

        preferred = [
            row
            for row in group
            if row["kind"] == "优先加分"
        ]

        ratio = (
            mean(base)
            if base
            else mean(preferred)
        )

        if base and preferred:
            ratio = min(
                1,
                ratio + 0.2 * mean(preferred),
            )

        weight = job["weights"][name]

        dimensions.append(
            {
                "name": name,
                "score": round(
                    ratio * 100,
                    2,
                ),
                "weight": weight,
                "points": round(
                    ratio * weight,
                    2,
                ),
            }
        )

    total = round(
        sum(
            item["points"]
            for item in dimensions
        ),
        2,
    )

    hard = [
        row
        for row in rows
        if row["kind"] in (
            "硬性条件",
            "必备技能",
        )
    ]

    if any(
        row["ratio"] < 1
        and row["state"] != "待核实"
        for row in hard
    ):
        hard_status = "不达标"

    elif any(
        row["state"] == "待核实"
        for row in hard
    ):
        hard_status = "待核实"

    elif hard:
        hard_status = "达标"

    else:
        hard_status = "未设置硬性条件"

    facts = []

    for field in FIELDS:
        values = candidate_values(
            candidate,
            field,
        )

        facts.append(
            field
            + "："
            + (
                "、".join(values)[:180]
                if values
                else "未提供"
            )
        )

    calculation = "；".join(
        f"{item['name']} "
        f"{item['points']:g}/"
        f"{item['weight']:g}分"
        for item in dimensions
        if item["weight"]
    )

    audit = [
        (
            "提取岗位要求："
            + requirement_summary(job)
        ),
        (
            "读取候选人结构化简历："
            + "；".join(facts)
        ),
        (
            f"分项打分计算：{calculation}；"
            f"总分 {total:g}分。"
        ),
        (
            f"结论：综合匹配 {total:g}分；"
            f"硬性条件{hard_status}。"
            "缺失项需人工核实，"
            "优先条件未匹配不构成淘汰依据。"
        ),
    ]

    return {
        "candidate_code": candidate["candidate_code"],
        "total_score": total,
        "hard_status": hard_status,
        "dimensions": dimensions,
        "conditions": rows,
        "audit_log": audit,
    }


def ranked_candidates(sid, jid=None):
    jid = jid or templates(sid)["active_id"]

    with db.session_scope(sid):
        candidates = db.list_candidates()

    if not jid:
        for item in candidates:
            item.update(
                match_score=None,
                score_detail={},
                hard_status="未选择岗位",
            )

        return candidates

    job = get_job(sid, jid)

    for candidate in candidates:
        profile = {
            key: value
            for key, value in candidate.items()
            if key not in (
                "match_score",
                "score_detail",
                "hard_status",
            )
        }

        fingerprint = sha256(
            json.dumps(
                [
                    job["payload"],
                    profile,
                ],
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        with db.get_connection() as conn:
            cached = conn.execute(
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
                    candidate["candidate_code"],
                    fingerprint,
                ),
            ).fetchone()

        if cached:
            result = json.loads(
                cached["payload"]
            )

        else:
            result = score_profile(
                candidate,
                job["payload"],
            )

            with db.get_connection() as conn:
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
                        candidate["candidate_code"],
                        fingerprint,
                        json.dumps(
                            result,
                            ensure_ascii=False,
                        ),
                    ),
                )

        candidate.update(
            match_score=result["total_score"],
            score_detail=result,
            hard_status=result["hard_status"],
        )

    return sorted(
        candidates,
        key=lambda item: item["match_score"],
        reverse=True,
    )


init()