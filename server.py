import base64

from fastapi import HTTPException
from pydantic import BaseModel, Field

from api import app, locks

import database as db
import job_service as jobs

from jd_parser import (
    Condition,
    recommend_weights,
)

from report_service import (
    compose_report,
    render_pdf,
    clean,
)


class WeightRequest(BaseModel):
    conditions: list[Condition]

    preference: str = Field(
        default="",
        max_length=10000,
    )


@app.post(
    "/sessions/{sid}"
    "/jobs/suggest-weights"
)
def suggest_weights(
    sid: str,
    request: WeightRequest,
):
    db.get_session(sid)

    conditions = [
        item.model_dump()
        for item in request.conditions
    ]

    weights, note = recommend_weights(
        conditions,
        request.preference,
    )

    return {
        "weights": weights,
        "weight_note": note,
    }


@app.post(
    "/sessions/{sid}"
    "/jobs/{jid}"
    "/candidates/{code}"
    "/assess"
)
def assess(
    sid: str,
    jid: str,
    code: str,
):
    return jobs.assess_candidate(
        sid,
        jid,
        code,
    )


@app.post(
    "/sessions/{sid}"
    "/jobs/{jid}"
    "/candidates/{code}"
    "/pdf-report"
)
def pdf_report(
    sid: str,
    jid: str,
    code: str,
):
    # 只读取已保存的能力证据。
    # 报告生成阶段不再让模型重新打分。
    with locks[sid]:
        job = jobs.get_job(
            sid,
            jid,
        )

        candidate = next(
            (
                item
                for item in jobs.ranked_candidates(
                    sid,
                    jid,
                )
                if item["candidate_code"]
                == code
            ),
            None,
        )

        if not candidate:
            raise HTTPException(
                status_code=404,
                detail=(
                    "当前会话不存在该候选人。"
                ),
            )

        if not candidate.get(
            "score_detail"
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "请先完成当前岗位的能力匹配，"
                    "再生成报告。"
                ),
            )

        report = compose_report(
            job,
            candidate,
        )

        # 原报告的其他章节和PDF排版保持不变。
        # 将旧规则审计替换为本次能力匹配审计。
        for section in report["sections"]:
            if section["title"].startswith(
                "六、"
            ):
                section["lines"] = [
                    clean(line)
                    for line in candidate[
                        "score_detail"
                    ]["audit_log"]
                    if clean(line)
                ]

        content = render_pdf(report)

    return {
        "report": report,
        "pdf_base64": (
            base64.b64encode(content)
            .decode("ascii")
        ),
        "filename": (
            "recruitment-report-"
            + report["report_id"]
            + ".pdf"
        ),
    }