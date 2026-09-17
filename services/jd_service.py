from semantic_match import extract_job


def extract_jd_requirements(jd: str) -> dict:
    """仅依据JD独立抽取岗位特征，不读取简历、不使用搜索工具。"""
    return extract_job(jd)