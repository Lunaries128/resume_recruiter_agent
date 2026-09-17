import json

from langchain_core.tools import tool

from database import (
    list_candidates,
    current_session,
    HISTORY_DIR,
    get_session,
)
from report_service import build_report


@tool
def generate_candidate_report() -> str:
    """
    生成当前会话的简明初筛报告。
    只使用已保存的新版核验结果，不搜索网络。
    """
    sid = current_session()

    report = build_report(
        get_session(sid),
        list_candidates(),
    )

    folder = HISTORY_DIR / sid / "reports"
    folder.mkdir(parents=True, exist_ok=True)

    filename = report["id"] + ".md"

    lines = [
        "# 候选人初筛摘要",
        report["created_at"],
        report["note"],
    ]

    for candidate in report["candidates"]:
        lines += [
            "## " + candidate["name"]
            + " / " + candidate["code"],
            "岗位：" + candidate["job_title"],
        ]

        for section in candidate["sections"]:
            lines += [
                "### " + section["title"],
                *section["items"],
            ]

            if section["title"] == "初筛结论":
                lines.append("### 岗位要求核验")

                lines += [
                    f"- {item['requirement']}"
                    f"（{item['category']}）："
                    f"{item['status']}。{item['evidence']}"
                    for item in candidate["checks"]
                ]

    (folder / filename).write_text(
        "\n\n".join(lines),
        encoding="utf-8",
    )

    return json.dumps(
        {
            "success": True,
            "filename": filename,
            "message": (
                "简明报告已保存，可在招聘要求页下载；"
                "PDF请在候选人信息页生成。"
            ),
        },
        ensure_ascii=False,
    )