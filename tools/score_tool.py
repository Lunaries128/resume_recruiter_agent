from langchain_core.tools import tool

from hr_workflow import score_one


def score_candidate(
    candidate_code: str,
    requirements: dict | None = None,
) -> dict:
    # 仅使用数据库中HR已确认的标准。
    # 保留requirements参数只是兼容旧接口。
    return score_one(candidate_code)


@tool
def calculate_match_score(
    candidate_code: str,
) -> dict:
    """
    使用当前会话已确认标准和权重进行证据评分，
    不搜索网络。
    """
    return score_one(candidate_code)