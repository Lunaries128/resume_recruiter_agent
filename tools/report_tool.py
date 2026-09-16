import json
import uuid

from langchain_core.tools import tool

from database import (
    HISTORY_DIR,
    current_session,
    get_session,
)
from job_service import ranked_candidates


@tool
def generate_candidate_report() -> str:
    """
    根据当前会话选中的岗位模板，
    生成候选人匹配报告。
    """
    session_id = current_session()

    candidates = ranked_candidates(
        session_id
    )

    folder = (
        HISTORY_DIR
        / session_id
        / "reports"
    )

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    filename = (
        uuid.uuid4().hex
        + ".md"
    )

    lines = [
        "# 候选人匹配报告",
        "",
        "## 招聘要求",
        get_session(session_id)["jd"],
        "",
    ]

    for candidate in candidates:
        score = candidate.get(
            "match_score"
        )

        lines.extend([
            (
                "## "
                + candidate["candidate_code"]
            ),
            (
                "匹配分："
                + (
                    str(score)
                    if score is not None
                    else "未评分"
                )
            ),
        ])

        lines.extend(
            candidate.get(
                "score_detail",
                {},
            ).get(
                "audit_log",
                [],
            )
        )

        lines.append("")

    (folder / filename).write_text(
        "\n\n".join(lines),
        encoding="utf-8",
    )

    return json.dumps(
        {
            "success": True,
            "filename": filename,
            "message": (
                "报告已保存，可从本会话"
                "招聘要求页面下载。"
            ),
            "candidates": [
                {
                    "candidate_code": item[
                        "candidate_code"
                    ],
                    "match_score": item.get(
                        "match_score"
                    ),
                }
                for item in candidates
            ],
        },
        ensure_ascii=False,
    )