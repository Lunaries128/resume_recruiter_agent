import json
import sqlite3

from langchain_core.tools import tool

from database import current_session
from job_service import ranked_candidates


@tool
def query_candidates(
    sql: str,
) -> str:
    """
    查询当前会话的candidate_safe_view。
    只允许SELECT。

    可查询字段：
    candidate_code、education_json、
    skills_json、work_years、
    projects_json、experiences_json、
    awards_json、summary、match_score。

    match_score对应当前选中的岗位模板。
    """
    if not sql.strip().lower().startswith(
        "select "
    ):
        raise ValueError(
            "只允许SELECT查询。"
        )

    connection = sqlite3.connect(
        ":memory:"
    )

    connection.row_factory = sqlite3.Row

    try:
        connection.execute(
            """
            CREATE TABLE candidate_safe_view (
                candidate_code TEXT,
                education_json TEXT,
                skills_json TEXT,
                work_years REAL,
                projects_json TEXT,
                experiences_json TEXT,
                awards_json TEXT,
                summary TEXT,
                match_score REAL
            )
            """
        )

        candidates = ranked_candidates(
            current_session()
        )

        for item in candidates:
            connection.execute(
                """
                INSERT INTO candidate_safe_view
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["candidate_code"],
                    json.dumps(
                        item.get("education", []),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        item.get("skills", []),
                        ensure_ascii=False,
                    ),
                    item.get("work_years", 0),
                    json.dumps(
                        item.get("projects", []),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        item.get("experiences", []),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        item.get("awards", []),
                        ensure_ascii=False,
                    ),
                    item.get("summary", ""),
                    item.get("match_score"),
                ),
            )

        allowed_operations = {
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_FUNCTION,
        }

        def authorize(
            action,
            *args,
        ):
            if action in allowed_operations:
                return sqlite3.SQLITE_OK

            return sqlite3.SQLITE_DENY

        connection.set_authorizer(
            authorize
        )

        steps = [0]

        def limit_execution():
            steps[0] += 1

            return int(
                steps[0] > 1000
            )

        connection.set_progress_handler(
            limit_execution,
            1000,
        )

        rows = connection.execute(
            sql
        ).fetchmany(100)

        return json.dumps(
            [
                dict(row)
                for row in rows
            ],
            ensure_ascii=False,
        )

    finally:
        connection.close()