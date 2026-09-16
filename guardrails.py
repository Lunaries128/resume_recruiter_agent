import hashlib
import re


SENSITIVE_CRITERIA = [
    "性别",
    "男性",
    "女性",
    "年龄",
    "婚姻",
    "婚育",
    "民族",
    "籍贯",
    "户籍",
    "宗教",
    "残疾",
    "健康状况",
    "政治面貌",
    "家庭成员",
    "家庭住址",
    "身份证",
]


SENSITIVE_LINE_WORDS = [
    "出生日期",
    "出生年月",
    "出生地",
    "民族",
    "籍贯",
    "户籍",
    "婚姻",
    "婚育",
    "身份证",
    "家庭住址",
    "家庭成员",
    "宗教信仰",
    "政治面貌",
]


FLUFF_PHRASES = [
    "性格开朗",
    "吃苦耐劳",
    "学习能力强",
    "沟通能力强",
    "责任心强",
    "团队意识强",
    "抗压能力强",
    "积极向上",
    "积极参与",
    "提升自我",
    "热爱生活",
    "善于沟通",
]


SECTION_TITLES = [
    "教育经历",
    "工作经历",
    "实习经历",
    "项目经历",
    "技能清单",
    "专业技能",
    "获奖经历",
    "竞赛经历",
    "证书",
]


def safe_candidate_code(
    source: str,
) -> str:
    digest = hashlib.sha256(
        source.encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()

    return f"CAND-{digest[:12].upper()}"


def mask_name(name: str) -> str:
    value = name.strip()

    if not value:
        return "候选人*"

    return value[0] + "*"


def mask_phone(phone: str) -> str:
    digits = re.sub(
        r"\D",
        "",
        phone,
    )

    if len(digits) == 11:
        return (
            digits[:3]
            + "****"
            + digits[-4:]
        )

    return ""


def extract_contact_information(
    text: str,
) -> dict:
    email_match = re.search(
        r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
        text,
    )

    phone_match = re.search(
        r"(?<!\d)1[3-9]\d{9}(?!\d)",
        text,
    )

    name_match = re.search(
        r"(?:姓名|姓\s*名)"
        r"[:：\s]*"
        r"([\u4e00-\u9fa5]{2,4})",
        text,
    )

    return {
        "masked_name": mask_name(
            name_match.group(1)
            if name_match
            else ""
        ),
        "masked_phone": mask_phone(
            phone_match.group(0)
            if phone_match
            else ""
        ),
        "email": (
            email_match.group(0)
            if email_match
            else ""
        ),
    }


def redact_pii(text: str) -> str:
    result = text

    patterns = [
        (
            r"(?i)[\w.+-]+@[\w.-]+\.[a-z]{2,}",
            "[邮箱已提取]",
        ),
        (
            r"(?<!\d)1[3-9]\d{9}(?!\d)",
            "[手机号已脱敏]",
        ),
        (
            r"(?<!\d)\d{17}[\dXx](?!\d)",
            "[身份证号已隐藏]",
        ),
    ]

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
    safe_lines = []

    for line in text.splitlines():
        lower_line = line.lower()

        if any(
            word.lower() in lower_line
            for word in SENSITIVE_LINE_WORDS
        ):
            continue

        safe_lines.append(line)

    return "\n".join(safe_lines)


def remove_fluff_lines(
    text: str,
) -> str:
    result = []
    skipping_self_evaluation = False

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        if any(
            title in line
            for title in [
                "自我评价",
                "个人评价",
                "自我介绍",
            ]
        ):
            skipping_self_evaluation = True
            continue

        if skipping_self_evaluation:
            if any(
                title in line
                for title in SECTION_TITLES
            ):
                skipping_self_evaluation = False
            else:
                continue

        if any(
            phrase in line
            for phrase in FLUFF_PHRASES
        ):
            continue

        result.append(line)

    return "\n".join(result)


def prepare_resume_text(
    text: str,
) -> str:
    text = remove_fluff_lines(text)
    text = remove_sensitive_lines(text)
    text = redact_pii(text)

    return text.strip()


def validate_filter_request(
    user_input: str,
) -> None:
    matched = [
        word
        for word in SENSITIVE_CRITERIA
        if word in user_input
    ]

    if matched:
        raise ValueError(
            "筛选条件包含禁止使用的敏感属性："
            + "、".join(matched)
        )


def validate_privacy_request(
    user_input: str,
) -> None:
    privacy_requests = [
        "身份证号",
        "家庭住址",
        "家庭成员",
        "婚育情况",
        "宗教信仰",
    ]

    matched = [
        item
        for item in privacy_requests
        if item in user_input
    ]

    if matched:
        raise ValueError(
            "不能查询候选人的敏感隐私信息："
            + "、".join(matched)
        )


def safe_output_text(
    text: str,
) -> str:
    result = re.sub(
        r"(?<!\d)1[3-9]\d{9}(?!\d)",
        "[手机号已脱敏]",
        text,
    )

    result = re.sub(
        r"(?<!\d)\d{17}[\dXx](?!\d)",
        "[身份证号已隐藏]",
        result,
    )

    return result