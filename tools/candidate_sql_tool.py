import json
import re

from langchain_core.tools import tool

from database import get_connection


FORBIDDEN_WORDS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "attach",
    "detach",
    "pragma",
    "replace",
    "create",
    "truncate",
    "vacuum",
}


def validate_sql(
    sql: str,
) -> tuple[bool, str]:
    normalized = re.sub(
        r"\s+",
        " ",
        sql.strip().lower(),
    )

    if not normalized.startswith(
        "select "
    ):
        return (
            False,
            "只允许SELECT查询。",
        )

    statement = normalized.rstrip(";")

    if ";" in statement:
        return (
            False,
            "只允许执行单条SQL。",
        )

    for word in FORBIDDEN_WORDS:
        if re.search(
            rf"\b{word}\b",
            statement,
        ):
            return (
                False,
                f"SQL禁止使用{word}。",
            )

    if not re.search(
        r"\bfrom\s+candidate_safe_view\b",
        statement,
    ):
        return (
            False,
            "只能查询candidate_safe_view。",
        )

    return True, ""


@tool
def query_candidates(
    sql: str,
) -> str:
    """
    使用只读SQL查询候选人安全视图。

    只能查询candidate_safe_view，
    只能执行单条SELECT。
    """

    valid, message = validate_sql(sql)

    if not valid:
        return json.dumps(
            {
                "success": False,
                "error": message,
            },
            ensure_ascii=False,
        )

    try:
        with get_connection() as connection:
            rows = connection.execute(
                sql
            ).fetchall()

        return json.dumps(
            {
                "success": True,
                "count": len(rows),
                "rows": [
                    dict(row)
                    for row in rows[:100]
                ],
            },
            ensure_ascii=False,
        )

    except Exception as error:
        return json.dumps(
            {
                "success": False,
                "error": str(error),
            },
            ensure_ascii=False,
        )