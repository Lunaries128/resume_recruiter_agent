import json
import re
from hashlib import sha256

from langchain_core.tools import tool

from database import get_candidate, save_score
from guardrails import validate_filter_request
from schemas import MatchDimension, ScoreResult


EDUCATION_LEVELS = {
    "高中": 1, "中专": 1, "大专": 2, "专科": 2,
    "本科": 3, "学士": 3, "硕士": 4, "研究生": 4, "博士": 5,
}
SKILL_ALIASES = {
    "py": "python", "pytorch框架": "pytorch", "机器学习": "machine learning",
    "深度学习": "deep learning", "推荐算法": "推荐系统",
}
DEFAULT_WEIGHTS = {
    "education": 0.15, "major": 0.15, "projects": 0.25,
    "awards": 0.10, "skills": 0.35,
}


def normalize(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def normalize_terms(values):
    return {
        SKILL_ALIASES.get(normalize(value), normalize(value))
        for value in (values or []) if normalize(value)
    }


def contains_term(text, term):
    return normalize(term) in normalize(text)


def status_of(score):
    if score >= 80:
        return "satisfied"
    if score >= 40:
        return "partial"
    return "unsatisfied"


def highest_education(education):
    best_level, best_name = 0, "未知"
    for item in education:
        value = str(item.get("degree", ""))
        for name, level in EDUCATION_LEVELS.items():
            if name in value and level > best_level:
                best_level, best_name = level, name
    return best_level, best_name


def required_education(value):
    for name, level in sorted(
        EDUCATION_LEVELS.items(), key=lambda item: item[1], reverse=True
    ):
        if name in str(value or ""):
            return level, name
    return 0, "未设置"


def keyword_score(required, text):
    terms = normalize_terms(required)
    if not terms:
        return None, [], []
    matched = sorted(term for term in terms if contains_term(text, term))
    missing = sorted(terms - set(matched))
    return round(len(matched) / len(terms) * 100, 2), matched, missing


def make_dimension(key, name, score, requirement, candidate_value,
                   source, evidence):
    return MatchDimension(
        key=key, name=name, score=round(score, 2), weight=0,
        status=status_of(score), requirement=requirement,
        candidate_value=candidate_value, evidence_source=source,
        evidence=evidence,
    )


def score_candidate(candidate_code: str, requirements: dict) -> dict:
    criteria_text = json.dumps(requirements, ensure_ascii=False)
    validate_filter_request(criteria_text)
    candidate = get_candidate(candidate_code)
    if not candidate:
        raise ValueError(f"候选人不存在：{candidate_code}")

    dimensions = []
    education = candidate.get("education", [])
    actual_level, actual_name = highest_education(education)
    required_level, required_name = required_education(
        requirements.get("minimum_education", "")
    )
    if required_level:
        education_score = 100 if actual_level >= required_level else (
            round(actual_level / required_level * 100, 2) if actual_level else 0
        )
        dimensions.append(make_dimension(
            "education", "学历匹配", education_score,
            f"最低学历：{required_name}", actual_name, "教育经历",
            [f"候选人最高学历为{actual_name}"]
        ))

    majors = [item.get("major", "") for item in education if item.get("major")]
    major_score, matched, missing = keyword_score(
        requirements.get("required_majors", []), " ".join(majors)
    )
    if major_score is not None:
        # 专业列表视为可接受的替代项，满足一个即可。
        major_score = 100 if matched else 0
        dimensions.append(make_dimension(
            "major", "专业匹配", major_score,
            "、".join(requirements.get("required_majors", [])),
            "、".join(majors) or "信息缺失", "教育经历",
            [f"匹配专业：{'、'.join(matched) or '无'}",
             f"未匹配专业：{'、'.join(missing) or '无'}"]
        ))

    projects = candidate.get("projects", [])
    experiences = candidate.get("experiences", [])
    project_text = json.dumps([projects, experiences], ensure_ascii=False)
    project_score, matched, missing = keyword_score(
        requirements.get("project_keywords", []), project_text
    )
    minimum_years = float(requirements.get("minimum_work_years", 0) or 0)
    actual_years = float(candidate.get("work_years", 0) or 0)
    if project_score is not None or minimum_years > 0:
        parts = []
        if project_score is not None:
            parts.append(project_score)
        if minimum_years > 0:
            parts.append(min(actual_years / minimum_years * 100, 100))
        combined = round(sum(parts) / len(parts), 2)
        dimensions.append(make_dimension(
            "projects", "项目经验匹配", combined,
            f"项目方向：{'、'.join(requirements.get('project_keywords', [])) or '未设置'}；"
            f"最低年限：{minimum_years}年",
            f"工作年限：{actual_years}年；项目数量：{len(projects)}",
            "项目经历、工作经历",
            [f"匹配项目关键词：{'、'.join(matched) or '无'}",
             f"缺少项目关键词：{'、'.join(missing) or '无'}"]
        ))

    award_text = json.dumps([candidate.get("awards", []), candidate.get("certificates", [])], ensure_ascii=False)
    award_score, matched, missing = keyword_score(
        requirements.get("award_keywords", []), award_text
    )
    if award_score is not None:
        dimensions.append(make_dimension(
            "awards", "竞赛证书匹配", award_score,
            "、".join(requirements.get("award_keywords", [])),
            award_text if award_text != "[]" else "信息缺失", "竞赛获奖",
            [f"匹配竞赛证书：{'、'.join(matched) or '无'}",
             f"缺少竞赛证书：{'、'.join(missing) or '无'}"]
        ))

    required_skills = normalize_terms(requirements.get("required_skills", []))
    preferred_skills = normalize_terms(requirements.get("preferred_skills", []))
    candidate_skills = normalize_terms(candidate.get("skills", []))
    for item in candidate.get("projects", []) + candidate.get("experiences", []):
        candidate_skills |= normalize_terms(item.get("technologies", []))
    if required_skills or preferred_skills:
        required_ratio = len(required_skills & candidate_skills) / len(required_skills) if required_skills else 0
        preferred_ratio = len(preferred_skills & candidate_skills) / len(preferred_skills) if preferred_skills else 0
        if required_skills and preferred_skills:
            skill_score = required_ratio * 80 + preferred_ratio * 20
        elif required_skills:
            skill_score = required_ratio * 100
        else:
            skill_score = preferred_ratio * 100
        missing_skills = sorted(required_skills - candidate_skills)
        matched_skills = sorted((required_skills | preferred_skills) & candidate_skills)
        dimensions.append(make_dimension(
            "skills", "技术栈匹配", skill_score,
            "必需：" + ("、".join(sorted(required_skills)) or "无")
            + "；加分：" + ("、".join(sorted(preferred_skills)) or "无"),
            "、".join(sorted(candidate_skills)) or "信息缺失", "技能清单",
            [f"匹配技能：{'、'.join(matched_skills) or '无'}",
             f"缺少必需技能：{'、'.join(missing_skills) or '无'}"]
        ))

    if not dimensions:
        raise ValueError("JD中没有提取到可评分的岗位要求。")

    weight_total = sum(DEFAULT_WEIGHTS[item.key] for item in dimensions)
    for item in dimensions:
        item.weight = round(DEFAULT_WEIGHTS[item.key] / weight_total, 6)
    total_score = round(sum(item.score * item.weight for item in dimensions), 2)

    satisfied, partial, unsatisfied = [], [], []
    for item in dimensions:
        message = f"{item.name} {item.score}分（来源：{item.evidence_source}）"
        {"satisfied": satisfied, "partial": partial,
         "unsatisfied": unsatisfied}[item.status].append(message)

    audit_log = [
        "从岗位要求中提取可评分条件：" + criteria_text,
        f"读取候选人结构化简历：{candidate_code}",
        *[f"{item.name}：{item.score}分，权重{item.weight:.2%}；"
          + "；".join(item.evidence) for item in dimensions],
        f"加权总分：{total_score}分",
    ]
    result = ScoreResult(
        candidate_code=candidate_code, total_score=total_score,
        dimensions=dimensions, satisfied=satisfied, partial=partial,
        unsatisfied=unsatisfied, audit_log=audit_log,
        uncertainties=candidate.get("missing_fields", []),
    ).model_dump()
    jd_hash = sha256(
        json.dumps(requirements, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    save_score(candidate_code, jd_hash, result)
    return result


@tool
def calculate_match_score(
    candidate_code: str,
    required_skills: list[str],
    preferred_skills: list[str] | None = None,
    minimum_work_years: float = 0,
    minimum_education: str = "",
    required_majors: list[str] | None = None,
    project_keywords: list[str] | None = None,
    award_keywords: list[str] | None = None,
) -> dict:
    """按照学历、专业、项目、竞赛证书和技术栈进行可审计评分。"""
    return score_candidate(candidate_code, {
        "required_skills": required_skills,
        "preferred_skills": preferred_skills or [],
        "minimum_work_years": minimum_work_years,
        "minimum_education": minimum_education,
        "required_majors": required_majors or [],
        "project_keywords": project_keywords or [],
        "award_keywords": award_keywords or [],
    })
