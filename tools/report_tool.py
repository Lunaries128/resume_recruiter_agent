import json
from datetime import datetime
from pathlib import Path

from langchain_core.tools import tool

from config import REPORT_DIR
from database import get_candidate
from guardrails import safe_output_text


@tool
def generate_candidate_report(
    jd: str,
    score_results: list[dict],
) -> dict:
    """
    根据候选人评分结果生成Markdown评估报告。
    """

    sorted_results = sorted(
        score_results,
        key=lambda item: item.get(
            "total_score",
            0,
        ),
        reverse=True,
    )

    lines = [
        "# 候选人岗位匹配评估报告",
        "",
        "## 使用说明",
        "",
        (
            "本报告仅用于HR人工复核，"
            "不得作为自动录用或淘汰决定。"
        ),
        "",
        "## 岗位要求",
        "",
        safe_output_text(jd),
        "",
        "## 候选人排名",
        "",
        (
            "|排名|候选人编号|总分|"
            "技能|经验|学历|项目|"
        ),
        "|---:|---|---:|---:|---:|---:|---:|",
    ]

    for index, result in enumerate(
        sorted_results,
        start=1,
    ):
        lines.append(
            f"|{index}|"
            f"{result['candidate_code']}|"
            f"{result['total_score']}|"
            f"{result['skill_score']}|"
            f"{result['experience_score']}|"
            f"{result['education_score']}|"
            f"{result['project_score']}|"
        )

    lines.extend([
        "",
        "## 详细说明",
        "",
    ])

    for result in sorted_results:
        candidate = get_candidate(
            result["candidate_code"]
        )

        lines.extend([
            (
                f"### "
                f"{result['candidate_code']}"
            ),
            "",
            (
                f"- 综合匹配度："
                f"{result['total_score']}"
            ),
            (
                "- 已匹配技能："
                + "、".join(
                    result.get(
                        "matched_skills",
                        [],
                    )
                )
            ),
            (
                "- 缺失技能："
                + "、".join(
                    result.get(
                        "missing_skills",
                        [],
                    )
                )
            ),
            (
                "- 信息不确定项："
                + "、".join(
                    result.get(
                        "uncertainties",
                        [],
                    )
                )
            ),
            (
                "- 简历摘要："
                + (
                    candidate["summary"]
                    if candidate
                    else "无"
                )
            ),
            "",
        ])

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    path = REPORT_DIR / (
        f"candidate_report_{timestamp}.md"
    )

    path.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    return {
        "success": True,
        "report_path": str(path),
        "candidate_count": len(
            sorted_results
        ),
    }