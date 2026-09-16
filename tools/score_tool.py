from langchain_core.tools import tool

import database as db
import job_service as jobs


@tool
def calculate_match_score(
    candidate_code: str,
) -> dict:
    """
    使用HR当前选中的岗位模板评分。
    不能自行修改岗位条件或权重。
    """
    session_id = db.current_session()

    active_id = jobs.templates(
        session_id
    )["active_id"]

    if not active_id:
        raise ValueError(
            "请先保存岗位模板，"
            "并在候选人信息页选择岗位。"
        )

    candidate = next(
        (
            item
            for item in jobs.ranked_candidates(
                session_id
            )
            if item["candidate_code"]
            == candidate_code
        ),
        None,
    )

    if candidate is None:
        raise ValueError(
            "当前会话中不存在该候选人。"
        )

    return candidate["score_detail"]