import re
from hashlib import sha256


SENSITIVE_CRITERIA = [
    "性别",
    "男性",
    "女性",
    "年龄",
    "婚姻",
    "婚育",
    "已婚",
    "未婚",
    "民族",
    "籍贯",
    "户籍",
    "宗教",
    "残疾",
    "健康状况",
    "怀孕",
    "照片",
    "政治面貌",
]

PRIVATE_OUTPUT_TERMS = [
    "手机号",
    "电话",
    "联系方式",
    "邮箱",
    "身份证",
    "家庭住址",
    "住址",
    "微信",
    "QQ",
]


def redact_pii(text: str) -> str:
    """对简历正文中的隐私信息进行脱敏。"""

    patterns = [
        (
            r"(?i)[a-z0-9._%+-]+"
            r"@[a-z0-9.-]+\.[a-z]{2,}",
            "[邮箱已脱敏]",
        ),
        (
            r"(?<!\d)1[3-9]\d{9}(?!\d)",
            "[手机号已脱敏]",
        ),
        (
            r"(?<!\d)\d{17}[\dXx](?!\d)",
            "[身份证号已脱敏]",
        ),
        (
            r"(?<!\d)\d{15}(?!\d)",
            "[身份证号已脱敏]",
        ),
        (
            r"(?i)(微信|wechat|qq)"
            r"[\s：:]*[a-z0-9_-]+",
            r"\1：[账号已脱敏]",
        ),
        (
            r"(住址|地址)"
            r"[\s：:]*[^\n]{4,60}",
            r"\1：[地址已脱敏]",
        ),
    ]

    result = text

    for pattern, replacement in patterns:
        result = re.sub(
            pattern,
            replacement,
            result,
        )

    return result


def remove_sensitive_lines(
    text: str,
) -> str:
    """
    删除不应参与招聘评分的敏感属性行。
    """

    safe_lines = []

    for line in text.splitlines():
        lower_line = line.lower()

        if any(
            word.lower() in lower_line
            for word in SENSITIVE_CRITERIA
        ):
            continue

        safe_lines.append(line)

    return "\n".join(safe_lines)


def prepare_resume_text(
    text: str,
) -> str:
    return remove_sensitive_lines(
        redact_pii(text)
    )


def validate_filter_request(
    user_input: str,
) -> None:
    """拒绝使用敏感属性筛选候选人。"""

    matched = [
        word
        for word in SENSITIVE_CRITERIA
        if word in user_input
    ]

    if matched:
        raise ValueError(
            "筛选条件包含禁止使用的"
            "敏感属性："
            + "、".join(matched)
            + "。请改用与岗位直接相关的"
            "技能、经验、学历或项目要求。"
        )


def safe_candidate_code(
    filename: str,
) -> str:
    digest = sha256(
        filename.encode("utf-8")
    ).hexdigest()[:10]

    return f"CAND-{digest.upper()}"


def safe_output_text(
    text: str,
) -> str:
    return redact_pii(text)

def validate_privacy_request(
    user_input: str,
) -> None:
    matched = [
        word
        for word in PRIVATE_OUTPUT_TERMS
        if word.lower()
        in user_input.lower()
    ]

    if matched:
        raise ValueError(
            "不能输出候选人的手机号、邮箱、"
            "身份证号、住址或社交账号等"
            "隐私信息。"
        )