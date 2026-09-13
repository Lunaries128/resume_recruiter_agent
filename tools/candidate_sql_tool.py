import json
import re

from langchain_core.tools import tool

from database import get_connection


FORBIDDEN_WORDS = [
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
]


@tool
def query_candidates(
    sql: str,
) -> str:
    """
    使用只读SQL查询候选人库。

    只能查询 candidates 表，
    只能执行单条 SELECT。
    """

    normalized = (
        re.sub(
            r"\s+",
            " ",
            sql.strip().lower(),
        )
    )

    if not normalized.startswith(
        "select "
    ):
        return json.dumps({
            "success": False,
            "error": "只允许SELECT查询",
        }, ensure_ascii=False)

    if any(
        word in normalized
        for word in FORBIDDEN_WORDS
    ):
        return json.dumps({
            "success": False,
            "error": "SQL包含禁止操作",
        }, ensure_ascii=False)

    if normalized.count(";") > 1:
        return json.dumps({
            "success": False,
            "error": "只允许单条SQL",
        }, ensure_ascii=False)

    if "candidates" not in normalized:
        return json.dumps({
            "success": False,
            "error": (
                "只能查询candidates表"
            ),
        }, ensure_ascii=False)

    try:
        with get_connection() as connection:
            rows = connection.execute(
                sql
            ).fetchall()

        safe_rows = []

        for row in rows[:100]:
            item = dict(row)

            # 不向Agent返回整份简历正文
            item.pop(
                "redacted_text",
                None,
            )

            safe_rows.append(item)

        return json.dumps(
            {
                "success": True,
                "count": len(safe_rows),
                "rows": safe_rows,
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