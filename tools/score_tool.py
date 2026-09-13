import json
from hashlib import sha256

from langchain_core.tools import tool

from database import (
    get_candidate,
    save_score,
)
from schemas import (
    ScoreResult,
    ScoreWeights,
)


EDUCATION_LEVELS = {
    "高中": 1,
    "中专": 1,
    "大专": 2,
    "专科": 2,
    "本科": 3,
    "学士": 3,
    "硕士": 4,
    "研究生": 4,
    "博士": 5,
}


def load_json(value: str):
    try:
        return json.loads(value)

    except (
        json.JSONDecodeError,
        TypeError,
    ):
        return []


def normalize_skills(
    skills: list[str],
) -> set[str]:
    return {
        skill.strip().lower()
        for skill in skills
        if skill.strip()
    }


def education_level(
    education_items: list[dict],
) -> int:
    highest = 0

    for item in education_items:
        degree = item.get(
            "degree",
            "",
        )

        for name, level in (
            EDUCATION_LEVELS.items()
        ):
            if name in degree:
                highest = max(
                    highest,
                    level,
                )

    return highest


@tool
def calculate_match_score(
    candidate_code: str,
    required_skills: list[str],
    preferred_skills: list[str] = [],
    minimum_work_years: float = 0,
    minimum_education: str = "",
    required_project_keywords: list[str] = [],
    skill_weight: float = 0.50,
    experience_weight: float = 0.25,
    education_weight: float = 0.15,
    project_weight: float = 0.10,
) -> dict:
    """
    根据JD明确要求，对候选人进行透明的加权评分。

    禁止把年龄、性别、婚育、民族等敏感属性
    作为评分依据。
    """

    candidate = get_candidate(
        candidate_code
    )

    if not candidate:
        return {
            "success": False,
            "error": "候选人不存在",
        }

    candidate_skills = (
        normalize_skills(
            load_json(
                candidate[
                    "skills_json"
                ]
            )
        )
    )

    required = normalize_skills(
        required_skills
    )

    preferred = normalize_skills(
        preferred_skills
    )

    matched_required = sorted(
        required & candidate_skills
    )

    missing_required = sorted(
        required - candidate_skills
    )

    required_ratio = (
        len(matched_required)
        / len(required)
        if required
        else 1.0
    )

    preferred_ratio = (
        len(
            preferred
            & candidate_skills
        )
        / len(preferred)
        if preferred
        else 1.0
    )

    skill_score = (
        required_ratio * 80
        + preferred_ratio * 20
    )

    work_years = float(
        candidate["work_years"]
    )

    if minimum_work_years <= 0:
        experience_score = 100

    else:
        experience_score = min(
            work_years
            / minimum_work_years
            * 100,
            100,
        )

    education = load_json(
        candidate["education_json"]
    )

    actual_level = education_level(
        education
    )

    required_level = 0

    for name, level in (
        EDUCATION_LEVELS.items()
    ):
        if name in minimum_education:
            required_level = level
            break

    if required_level == 0:
        education_score = 100

    else:
        education_score = min(
            actual_level
            / required_level
            * 100,
            100,
        )

    projects = load_json(
        candidate["projects_json"]
    )

    project_text = json.dumps(
        projects,
        ensure_ascii=False,
    ).lower()

    project_keywords = {
        item.strip().lower()
        for item in (
            required_project_keywords
        )
        if item.strip()
    }

    matched_projects = sorted([
        keyword
        for keyword
        in project_keywords
        if keyword in project_text
    ])

    if not project_keywords:
        project_score = 100

    else:
        project_score = (
            len(matched_projects)
            / len(project_keywords)
            * 100
        )

    weights = ScoreWeights(
        skills=skill_weight,
        experience=experience_weight,
        education=education_weight,
        projects=project_weight,
    ).normalized()

    total_score = (
        skill_score * weights.skills
        + experience_score
        * weights.experience
        + education_score
        * weights.education
        + project_score
        * weights.projects
    )

    uncertainties = load_json(
        candidate[
            "missing_fields_json"
        ]
    )

    result = ScoreResult(
        candidate_code=(
            candidate_code
        ),
        total_score=round(
            total_score,
            2,
        ),
        skill_score=round(
            skill_score,
            2,
        ),
        experience_score=round(
            experience_score,
            2,
        ),
        education_score=round(
            education_score,
            2,
        ),
        project_score=round(
            project_score,
            2,
        ),
        matched_skills=(
            matched_required
        ),
        missing_skills=(
            missing_required
        ),
        evidence=[
            (
                f"简历技能："
                f"{sorted(candidate_skills)}"
            ),
            (
                f"工作年限："
                f"{work_years}"
            ),
            (
                f"匹配项目关键词："
                f"{matched_projects}"
            ),
        ],
        uncertainties=uncertainties,
    )

    result_data = (
        result.model_dump()
    )

    jd_payload = json.dumps(
        {
            "required_skills": (
                required_skills
            ),
            "minimum_work_years": (
                minimum_work_years
            ),
            "minimum_education": (
                minimum_education
            ),
            "required_project_keywords": (
                required_project_keywords
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
    )

    jd_hash = sha256(
        jd_payload.encode("utf-8")
    ).hexdigest()

    save_score(
        candidate_code,
        jd_hash,
        result_data,
    )

    return {
        "success": True,
        "result": result_data,
    }