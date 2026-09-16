import base64

from fastapi import HTTPException

from api import app

import job_service as jobs

from report_service import (
    compose_report,
    render_pdf,
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
    import database as db

    # 先固定岗位模板快照。
    # 后续即使用户切换当前岗位，
    # 本报告仍然使用这里选定的模板。
    job = jobs.get_job(
        sid,
        jid,
    )

    # 使用会话作用域，禁止跨会话读取候选人。
    with db.session_scope(sid):
        candidate = db.get_candidate(code)

    if not candidate:
        raise HTTPException(
            status_code=404,
            detail="当前会话不存在该候选人。",
        )

    # 复用现有匹配算法。
    # 不让大模型重新生成分数。
    candidate["score_detail"] = (
        jobs.score_profile(
            candidate,
            job["payload"],
        )
    )

    report = compose_report(
        job,
        candidate,
    )

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