import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import (
    datetime,
    timezone,
)

from config import DATABASE_PATH


@contextmanager
def get_connection():
    connection = sqlite3.connect(
        DATABASE_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    try:
        yield connection
        connection.commit()

    finally:
        connection.close()


def init_database():
    with get_connection() as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS candidates (
            candidate_code TEXT PRIMARY KEY,
            source_filename TEXT NOT NULL,
            education_json TEXT NOT NULL,
            skills_json TEXT NOT NULL,
            work_years REAL NOT NULL,
            job_titles_json TEXT NOT NULL,
            companies_json TEXT NOT NULL,
            projects_json TEXT NOT NULL,
            certificates_json TEXT NOT NULL,
            summary TEXT NOT NULL,
            missing_fields_json TEXT NOT NULL,
            redacted_text TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_code TEXT NOT NULL,
            jd_hash TEXT NOT NULL,
            total_score REAL NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(candidate_code)
                REFERENCES candidates(candidate_code)
        );

        CREATE TABLE IF NOT EXISTS deletion_requests (
            token TEXT PRIMARY KEY,
            candidate_code TEXT NOT NULL,
            expires_at REAL NOT NULL,
            used INTEGER NOT NULL DEFAULT 0
        );

        DROP VIEW IF EXISTS candidate_safe_view;

        CREATE VIEW candidate_safe_view AS
        SELECT
            candidate_code,
            source_filename,
            education_json,
            skills_json,
            work_years,
            job_titles_json,
            projects_json,
            certificates_json,
            summary,
            missing_fields_json,
            created_at
        FROM candidates;
        """)

        columns = {
            row["name"]
            for row in connection.execute(
                """
                PRAGMA table_info(
                    deletion_requests
                )
                """
            ).fetchall()
        }

        if "used" not in columns:
            connection.execute(
                """
                ALTER TABLE deletion_requests
                ADD COLUMN used INTEGER
                NOT NULL DEFAULT 0
                """
            )


def json_load(
    value,
    default=None,
):
    if default is None:
        default = []

    if not value:
        return default

    try:
        return json.loads(value)

    except (
        TypeError,
        json.JSONDecodeError,
    ):
        return default


def highest_education(
    education: list[dict],
) -> str:
    levels = {
        "高中": 1,
        "中专": 1,
        "大专": 2,
        "专科": 2,
        "本科": 3,
        "学士": 3,
        "硕士": 4,
        "研究生": 4,
        "博士": 5,
    }

    best_name = "未知"
    best_level = 0

    for item in education:
        degree = item.get(
            "degree",
            "",
        )

        for name, level in (
            levels.items()
        ):
            if (
                name in degree
                and level > best_level
            ):
                best_name = name
                best_level = level

    return best_name


def upsert_candidate(
    profile: dict,
    filename: str,
    redacted_text: str,
):
    now = datetime.now(
        timezone.utc
    ).isoformat()

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO candidates (
                candidate_code,
                source_filename,
                education_json,
                skills_json,
                work_years,
                job_titles_json,
                companies_json,
                projects_json,
                certificates_json,
                summary,
                missing_fields_json,
                redacted_text,
                created_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(candidate_code)
            DO UPDATE SET
                source_filename =
                    excluded.source_filename,
                education_json =
                    excluded.education_json,
                skills_json =
                    excluded.skills_json,
                work_years =
                    excluded.work_years,
                job_titles_json =
                    excluded.job_titles_json,
                companies_json =
                    excluded.companies_json,
                projects_json =
                    excluded.projects_json,
                certificates_json =
                    excluded.certificates_json,
                summary =
                    excluded.summary,
                missing_fields_json =
                    excluded.missing_fields_json,
                redacted_text =
                    excluded.redacted_text
            """,
            (
                profile["candidate_code"],
                filename,
                json.dumps(
                    profile["education"],
                    ensure_ascii=False,
                ),
                json.dumps(
                    profile["skills"],
                    ensure_ascii=False,
                ),
                profile["work_years"],
                json.dumps(
                    profile["job_titles"],
                    ensure_ascii=False,
                ),
                json.dumps(
                    profile["companies"],
                    ensure_ascii=False,
                ),
                json.dumps(
                    profile["projects"],
                    ensure_ascii=False,
                ),
                json.dumps(
                    profile["certificates"],
                    ensure_ascii=False,
                ),
                profile["summary"],
                json.dumps(
                    profile["missing_fields"],
                    ensure_ascii=False,
                ),
                redacted_text,
                now,
            ),
        )


def get_candidate(
    candidate_code: str,
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM candidates
            WHERE candidate_code = ?
            """,
            (candidate_code,),
        ).fetchone()

    return dict(row) if row else None


def get_latest_score(
    candidate_code: str,
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM scores
            WHERE candidate_code = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (candidate_code,),
        ).fetchone()

    if not row:
        return None

    return json_load(
        row["result_json"],
        {},
    )


def list_candidates():
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM candidates
            ORDER BY created_at DESC
            """
        ).fetchall()

    results = []

    for row in rows:
        item = dict(row)

        education = json_load(
            item["education_json"]
        )

        latest_score = get_latest_score(
            item["candidate_code"]
        )

        results.append({
            "candidate_code": item[
                "candidate_code"
            ],
            "source_filename": item[
                "source_filename"
            ],
            "education": education,
            "highest_education": (
                highest_education(
                    education
                )
            ),
            "skills": json_load(
                item["skills_json"]
            ),
            "work_years": item[
                "work_years"
            ],
            "job_titles": json_load(
                item["job_titles_json"]
            ),
            "projects": json_load(
                item["projects_json"]
            ),
            "certificates": json_load(
                item["certificates_json"]
            ),
            "summary": item["summary"],
            "missing_fields": json_load(
                item[
                    "missing_fields_json"
                ]
            ),
            "match_score": (
                latest_score.get(
                    "total_score"
                )
                if latest_score
                else None
            ),
            "score_detail": latest_score,
            "created_at": item[
                "created_at"
            ],
        })

    results.sort(
        key=lambda item: (
            item["match_score"]
            is not None,
            item["match_score"] or 0,
        ),
        reverse=True,
    )

    return results


def save_score(
    candidate_code: str,
    jd_hash: str,
    result: dict,
):
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO scores (
                candidate_code,
                jd_hash,
                total_score,
                result_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                candidate_code,
                jd_hash,
                result["total_score"],
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
                datetime.now(
                    timezone.utc
                ).isoformat(),
            ),
        )


def create_delete_request(
    candidate_code: str,
) -> dict:
    if not get_candidate(candidate_code):
        raise ValueError(
            "候选人不存在。"
        )

    token = secrets.token_urlsafe(24)
    expires_at = time.time() + 300

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO deletion_requests (
                token,
                candidate_code,
                expires_at,
                used
            )
            VALUES (?, ?, ?, 0)
            """,
            (
                token,
                candidate_code,
                expires_at,
            ),
        )

    return {
        "token": token,
        "candidate_code": candidate_code,
        "expires_at": expires_at,
    }


def confirm_delete_request(
    token: str,
    confirmed: bool,
) -> dict:
    with get_connection() as connection:
        request = connection.execute(
            """
            SELECT *
            FROM deletion_requests
            WHERE token = ?
            """,
            (token,),
        ).fetchone()

        if not request:
            raise ValueError(
                "删除确认不存在。"
            )

        if request["used"]:
            raise ValueError(
                "删除确认已使用。"
            )

        if float(
            request["expires_at"]
        ) < time.time():
            raise ValueError(
                "删除确认已过期。"
            )

        if not confirmed:
            connection.execute(
                """
                UPDATE deletion_requests
                SET used = 1
                WHERE token = ?
                """,
                (token,),
            )

            return {
                "success": False,
                "message": "用户取消删除。",
            }

        candidate_code = request[
            "candidate_code"
        ]

        connection.execute(
            """
            DELETE FROM scores
            WHERE candidate_code = ?
            """,
            (candidate_code,),
        )

        cursor = connection.execute(
            """
            DELETE FROM candidates
            WHERE candidate_code = ?
            """,
            (candidate_code,),
        )

        connection.execute(
            """
            UPDATE deletion_requests
            SET used = 1
            WHERE token = ?
            """,
            (token,),
        )

    return {
        "success": cursor.rowcount > 0,
        "candidate_code": candidate_code,
        "message": "候选人已删除。",
    }


init_database()