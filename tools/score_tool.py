import json
import re
from hashlib import sha256
from typing import Any

from langchain_core.tools import tool

from database import (
    get_candidate,
    save_score,
)
from guardrails import (
    validate_filter_request,
)
from schemas import (
    ScoreResult,
    ScoreWeights,
)


# 学历等级只用于和JD明确要求的最低学历比较
EDUCATION_LEVELS = {
    "未知": 0,
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


# 常见技能名称归一化
SKILL_ALIASES = {
    "python3": "python",
    "python 3": "python",
    "py": "python",

    "pytorch框架": "pytorch",
    "torch": "pytorch",

    "tensorflow2": "tensorflow",
    "tensorflow 2": "tensorflow",

    "scikit-learn": "sklearn",
    "scikit learn": "sklearn",

    "structured query language": "sql",

    "mysql数据库": "mysql",
    "postgres": "postgresql",
    "postgres sql": "postgresql",

    "vue.js": "vue",
    "vuejs": "vue",

    "react.js": "react",
    "reactjs": "react",

    "node.js": "nodejs",
    "node js": "nodejs",

    "c plus plus": "c++",
    "cpp": "c++",

    "机器学习": "machine learning",
    "深度学习": "deep learning",
    "推荐算法": "推荐系统",
}


def load_json(
    value: Any,
    default=None,
):
    """
    将数据库中的JSON字符串转换成Python对象。

    如果value本身已经是list或dict，
    则直接返回。
    """

    if default is None:
        default = []

    if value is None:
        return default

    if isinstance(
        value,
        (list, dict),
    ):
        return value

    if not isinstance(value, str):
        return default

    try:
        return json.loads(value)

    except (
        json.JSONDecodeError,
        TypeError,
    ):
     return default


def normalize_text(
    value: str,
) -> str:
    """统一大小写、空格和部分符号。"""

    text = str(value).strip().lower()

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text


def normalize_skill(
    skill: str,
) -> str:
    """统一技能名称。"""

    normalized = normalize_text(skill)

    return SKILL_ALIASES.get(
        normalized,
        normalized,
    )


def normalize_skills(
    skills: list[str] | None,
) -> set[str]:
    """将技能列表转换成去重后的标准技能集合。"""

    if not skills:
        return set()

    return {
        normalize_skill(skill)
        for skill in skills
        if str(skill).strip()
    }


def get_education_level(
    education_items: list[dict],
) -> tuple[int, str]:
    """
    获取候选人的最高学历等级和名称。
    """

    highest_level = 0
    highest_name = "未知"

    for item in education_items:
        if not isinstance(item, dict):
            continue

        degree = str(
            item.get(
                "degree",
                "",
            )
        )

        for name, level in (
            EDUCATION_LEVELS.items()
        ):
            if (
                name in degree
                and level > highest_level
            ):
                highest_level = level
                highest_name = name

    return (
        highest_level,
        highest_name,
    )


def get_required_education_level(
    minimum_education: str,
) -> tuple[int, str]:
    """
    将JD中的最低学历要求转换为等级。
    """

    requirement = str(
        minimum_education or ""
    )

    for name, level in sorted(
        EDUCATION_LEVELS.items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        if name in requirement:
            return level, name

    return 0, "未设置"


def calculate_skill_score(
    candidate_skills: set[str],
    required_skills: set[str],
    preferred_skills: set[str],
) -> tuple[
    float,
    list[str],
    list[str],
    list[str],
]:
    """
    计算技能得分。

    同时存在必需和加分技能时：
    必需技能占技能分的80%，
    加分技能占技能分的20%。

    只有必需技能时：
    必需技能占技能分的100%。

    只有加分技能时：
    加分技能占技能分的100%。
    """

    matched_required = sorted(
        required_skills
        & candidate_skills
    )

    missing_required = sorted(
        required_skills
        - candidate_skills
    )

    matched_preferred = sorted(
        preferred_skills
        & candidate_skills
    )

    required_ratio = (
        len(matched_required)
        / len(required_skills)
        if required_skills
        else 0
    )

    preferred_ratio = (
        len(matched_preferred)
        / len(preferred_skills)
        if preferred_skills
        else 0
    )

    if (
        required_skills
        and preferred_skills
    ):
        skill_score = (
            required_ratio * 80
            + preferred_ratio * 20
        )

    elif required_skills:
        skill_score = (
            required_ratio * 100
        )

    elif preferred_skills:
        skill_score = (
            preferred_ratio * 100
        )

    else:
        skill_score = 100

    return (
        round(skill_score, 2),
        matched_required,
        missing_required,
        matched_preferred,
    )


def calculate_experience_score(
    work_years: float,
    minimum_work_years: float,
) -> float:
    """根据JD最低工作年限计算经验得分。"""

    if minimum_work_years <= 0:
        return 100

    score = (
        work_years
        / minimum_work_years
        * 100
    )

    return round(
        min(max(score, 0), 100),
        2,
    )


def calculate_education_score(
    actual_level: int,
    required_level: int,
) -> float:
    """根据JD最低学历计算学历得分。"""

    if required_level <= 0:
        return 100

    if actual_level <= 0:
        return 0

    if actual_level >= required_level:
        return 100

    score = (
        actual_level
        / required_level
        * 100
    )

    return round(
        min(max(score, 0), 100),
        2,
    )


def calculate_project_score(
    projects: list[dict],
    required_keywords: set[str],
) -> tuple[
    float,
    list[str],
    list[str],
]:
    """计算项目关键词匹配得分。"""

    if not required_keywords:
        return 100, [], []

    project_parts = []

    for project in projects:
        if not isinstance(
            project,
            dict,
        ):
            continue

        project_parts.extend([
            str(
                project.get(
                    "name",
                    "",
                )
            ),
            str(
                project.get(
                    "description",
                    "",
                )
            ),
            " ".join(
                str(item)
                for item in project.get(
                    "technologies",
                    [],
                )
            ),
        ])

    project_text = normalize_text(
        " ".join(project_parts)
    )

    matched_keywords = sorted([
        keyword
        for keyword in required_keywords
        if normalize_text(keyword)
        in project_text
    ])

    missing_keywords = sorted(
        required_keywords
        - set(matched_keywords)
    )

    score = (
        len(matched_keywords)
        / len(required_keywords)
        * 100
    )

    return (
        round(score, 2),
        matched_keywords,
        missing_keywords,
    )


@tool
def calculate_match_score(
    candidate_code: str,
    required_skills: list[str],
    preferred_skills: (
        list[str] | None
    ) = None,
    minimum_work_years: float = 0,
    minimum_education: str = "",
    required_project_keywords: (
        list[str] | None
    ) = None,
    skill_weight: float = 0.50,
    experience_weight: float = 0.25,
    education_weight: float = 0.15,
    project_weight: float = 0.10,
) -> dict:
    """
    根据JD明确提出的岗位相关要求，
    对一名候选人进行透明的加权评分。

    允许使用的评分信息：

    - 岗位技能；
    - 工作年限；
    - 最低学历；
    - 项目经验。

    禁止使用年龄、性别、婚育、民族、
    籍贯、宗教、残疾、健康状况、照片
    等敏感属性。

    匹配度仅用于HR人工复核，
    不得作为自动录用或淘汰决定。
    """

    preferred_skills = (
        preferred_skills or []
    )

    required_project_keywords = (
        required_project_keywords
        or []
    )

    # 防止模型把敏感条件传入评分工具
    criteria_text = " ".join([
        *required_skills,
        *preferred_skills,
        minimum_education,
        *required_project_keywords,
    ])

    try:
        validate_filter_request(
            criteria_text
        )

    except ValueError as error:
        return {
            "success": False,
            "error": str(error),
        }

    candidate = get_candidate(
        candidate_code
    )

    if not candidate:
        return {
            "success": False,
            "error": (
                f"候选人不存在："
                f"{candidate_code}"
            ),
        }

    # ==================================================
    # 读取候选人结构化信息
    # ==================================================

    candidate_skills = normalize_skills(
        load_json(
            candidate.get(
                "skills_json"
            )
        )
    )

    required_skill_set = (
        normalize_skills(
            required_skills
        )
    )

    preferred_skill_set = (
        normalize_skills(
            preferred_skills
        )
    )

    education_items = load_json(
        candidate.get(
            "education_json"
        )
    )

    projects = load_json(
        candidate.get(
            "projects_json"
        )
    )

    uncertainties = load_json(
        candidate.get(
            "missing_fields_json"
        )
    )

    try:
        work_years = max(
            float(
                candidate.get(
                    "work_years",
                    0,
                )
            ),
            0,
        )

    except (
        TypeError,
        ValueError,
    ):
        work_years = 0

        if "工作年限" not in uncertainties:
            uncertainties.append(
                "工作年限"
            )

    # ==================================================
    # 计算四个维度得分
    # ==================================================

    (
        skill_score,
        matched_required,
        missing_required,
        matched_preferred,
    ) = calculate_skill_score(
        candidate_skills=(
            candidate_skills
        ),
        required_skills=(
            required_skill_set
        ),
        preferred_skills=(
            preferred_skill_set
        ),
    )

    experience_score = (
        calculate_experience_score(
            work_years=work_years,
            minimum_work_years=max(
                minimum_work_years,
                0,
            ),
        )
    )

    (
        actual_education_level,
        actual_education_name,
    ) = get_education_level(
        education_items
    )

    (
        required_education_level,
        required_education_name,
    ) = get_required_education_level(
        minimum_education
    )

    education_score = (
        calculate_education_score(
            actual_level=(
                actual_education_level
            ),
            required_level=(
                required_education_level
            ),
        )
    )

    project_keyword_set = {
        normalize_text(keyword)
        for keyword in (
            required_project_keywords
        )
        if str(keyword).strip()
    }

    (
        project_score,
        matched_projects,
        missing_projects,
    ) = calculate_project_score(
        projects=projects,
        required_keywords=(
            project_keyword_set
        ),
    )

    # ==================================================
    # 权重归一化
    # ==================================================

    weights = ScoreWeights(
        skills=max(skill_weight, 0),
        experience=max(
            experience_weight,
            0,
        ),
        education=max(
            education_weight,
            0,
        ),
        projects=max(
            project_weight,
            0,
        ),
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

    total_score = round(
        min(max(total_score, 0), 100),
        2,
    )

    # ==================================================
    # 生成可核查的评分依据
    # ==================================================

    evidence = [
        (
            "候选人技能："
            + (
                "、".join(
                    sorted(
                        candidate_skills
                    )
                )
                or "信息缺失"
            )
        ),
        (
            "匹配必需技能："
            + (
                "、".join(
                    matched_required
                )
                or "无"
            )
        ),
        (
            "缺少必需技能："
            + (
                "、".join(
                    missing_required
                )
                or "无"
            )
        ),
        (
            "匹配加分技能："
            + (
                "、".join(
                    matched_preferred
                )
                or "无"
            )
        ),
        (
            f"候选人工作年限："
            f"{work_years}年；"
            f"岗位最低要求："
            f"{minimum_work_years}年"
        ),
        (
            f"候选人最高学历："
            f"{actual_education_name}；"
            f"岗位最低要求："
            f"{required_education_name}"
        ),
        (
            "匹配项目关键词："
            + (
                "、".join(
                    matched_projects
                )
                or "无"
            )
        ),
        (
            "缺少项目关键词："
            + (
                "、".join(
                    missing_projects
                )
                or "无"
            )
        ),
    ]

    result = ScoreResult(
        candidate_code=(
            candidate_code
        ),
        total_score=total_score,
        skill_score=skill_score,
        experience_score=(
            experience_score
        ),
        education_score=(
            education_score
        ),
        project_score=project_score,
        matched_skills=(
            matched_required
        ),
        missing_skills=(
            missing_required
        ),
        matched_projects=(
            matched_projects
        ),
        evidence=evidence,
        uncertainties=uncertainties,
    )

    result_data = result.model_dump()

    # ==================================================
    # 保存评分记录
    # ==================================================

    jd_payload = {
        "required_skills": sorted(
            required_skill_set
        ),
        "preferred_skills": sorted(
            preferred_skill_set
        ),
        "minimum_work_years": (
            minimum_work_years
        ),
        "minimum_education": (
            minimum_education
        ),
        "required_project_keywords": (
            sorted(project_keyword_set)
        ),
        "weights": {
            "skills": weights.skills,
            "experience": (
                weights.experience
            ),
            "education": (
                weights.education
            ),
            "projects": (
                weights.projects
            ),
        },
    }

    jd_json = json.dumps(
        jd_payload,
        ensure_ascii=False,
        sort_keys=True,
    )

    jd_hash = sha256(
        jd_json.encode("utf-8")
    ).hexdigest()

    save_score(
        candidate_code=(
            candidate_code
        ),
        jd_hash=jd_hash,
        result=result_data,
    )

    return {
        "success": True,
        "notice": (
            "该评分仅用于HR人工复核，"
            "不得作为自动录用或淘汰决定。"
        ),
        "weights": {
            "skills": round(
                weights.skills,
                4,
            ),
            "experience": round(
                weights.experience,
                4,
            ),
            "education": round(
                weights.education,
                4,
            ),
            "projects": round(
                weights.projects,
                4,
            ),
        },
        "result": result_data,
    }