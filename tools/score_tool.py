from langchain_core.tools import tool

import database as db
import job_service as jobs


@tool
def calculate_match_score(
    candidate_code: str,
) -> dict:
    """
    按HR已保存并选中的岗位匹配候选人。

    复用能力证据缓存。
    禁止自行修改岗位条件和权重。
    """
    sid = db.current_session()

    jid = jobs.templates(
        sid
    )["active_id"]

    if not jid:
        raise ValueError(
            "请先保存并选择岗位模板。"
        )

    return jobs.assess_candidate(
        sid,
        jid,
        candidate_code,
    )