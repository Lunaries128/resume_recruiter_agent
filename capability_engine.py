import hashlib
import json
import re

from datetime import date

from jd_parser import (
    DIMENSIONS,
    DIMENSION_OF,
    LEVELS,
    model_json,
)

from report_service import clean


ENGINE_VERSION = "capability-evidence-v1"


def catalog(candidate):
    """
    构造可引用的结构化事实目录。

    不发送姓名、电话、邮箱、家庭信息等顶层字段。
    引文定位到结构化字段，不伪造PDF页码。
    """
    result = {}

    fields = (
        "school",
        "degree",
        "major",
        "organization",
        "role",
        "name",
        "level",
        "start_date",
        "end_date",
        "date",
        "description",
        "actions",
        "results",
        "technologies",
    )

    for group in (
        "education",
        "experiences",
        "projects",
        "awards",
    ):
        for index, item in enumerate(
            candidate.get(group, [])
        ):
            parts = []

            for field in fields:
                values = item.get(
                    field,
                    [],
                )

                if not isinstance(
                    values,
                    list,
                ):
                    values = [values]

                parts += [
                    f"{field}: {clean(value)}"
                    for value in values
                    if clean(value)
                ]

            if parts:
                result[
                    f"{group}.{index}"
                ] = " | ".join(parts)

    for group in (
        "skills",
        "certificates",
    ):
        for index, value in enumerate(
            candidate.get(group, [])
        ):
            if clean(value):
                result[
                    f"{group}.{index}"
                ] = clean(value)

    return result


def fingerprint(candidate, job):
    """
    缓存标识不包含权重。
    仅修改权重时，可复用能力证据。
    """
    import os

    from config import MODEL_NAME

    payload = [
        ENGINE_VERSION,
        MODEL_NAME,
        os.getenv(
            "OPENAI_BASE_URL",
            "",
        ),
        catalog(candidate),
        candidate.get("work_years"),
        candidate.get("missing_fields"),
        job["conditions"],
        date.today().strftime("%Y-%m"),
    ]

    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()


def atoms(index, condition):
    """
    分号分隔的多个能力要求分别判断，
    防止只命中其中一个就把“全部”判为满足。
    """
    values = [
        value.strip()
        for value in re.split(
            r"[;；]",
            condition["value"],
        )
        if value.strip()
    ]

    if condition["field"] in (
        "学历",
        "工作年限",
    ):
        values = [condition["value"]]

    return [
        (
            (
                str(index)
                if len(values) == 1
                else f"{index}.{sub_index}"
            ),
            {
                **condition,
                "value": value,
            },
        )
        for sub_index, value in enumerate(
            values
        )
    ]


def semantic_conditions(job):
    """
    学历、总工作年限交给程序。
    能力、专业相关性、专项经验等交给模型分析证据。
    """
    result = {}

    for index, condition in enumerate(
        job["conditions"]
    ):
        if condition["field"] == "学历":
            continue

        if (
            condition["field"] == "工作年限"
            and not condition.get("scope")
        ):
            continue

        for requirement_id, atom in atoms(
            index,
            condition,
        ):
            result[requirement_id] = atom

    return result


def extract_assessment(candidate, job):
    sources = catalog(candidate)

    requirements = semantic_conditions(
        job
    )

    if not requirements or not sources:
        return {
            "abilities": [],
            "matches": [],
            "warnings": [],
        }

    raw = model_json(
        "将简历事实映射到岗位能力，"
        "禁止只比较关键词，禁止给总分。"
        "先提炼候选人能力，再把每项要求关联能力。"

        '输出{"abilities":['
        '{"id":"A1","name":"能力名称","level":2,'
        '"evidence":[{"source_id":"experiences.0",'
        '"quote":"输入字段中的连续原文"}]}],'
        '"matches":['
        '{"requirement_id":"0","ability_ids":["A1"],'
        '"alignment":"直接相关",'
        '"reason":"简短的证据对比结论"}]}。'

        "level只允许"
        "1了解/宣称掌握、"
        "2参与执行、"
        "3独立负责、"
        "4带领统筹。"

        "参与不等于独立负责；"
        "团队成绩不自动等于个人贡献。"

        "alignment只允许"
        "直接相关/可迁移/无证据；"

        "专业名称可判断专业相关性，"
        "不能由专业名称推断实际工作能力；"
        "只有技能清单最多level1。"

        "项目和经历中有具体行为证据"
        "才能提炼业务能力。"

        "任一表示备选条件满足一个即可，"
        "全部则必须覆盖全部内容。"

        "工作年限scope是专项领域，"
        "关联的能力证据只能引用该领域工作经历，"
        "不能把项目或总年限冒充专项年限。"

        "学历、证书、语言、技术名称"
        "不因语义接近就视为等同；"
        "没有对应证据返回无证据。"

        "无证据不等于不具备。"
        "禁止推断性格、健康、年龄、性别。"

        "quote必须是输入source_id文本中的原文片段，"
        "不得改写或新增。"

        "每个requirement_id必须返回一条。"
        "reason只写可复核结论，"
        "不输出内部思维链。",
        {
            "requirements": requirements,
            "sources": sources,
        },
    )

    if (
        not isinstance(
            raw.get("abilities"),
            list,
        )
        or not isinstance(
            raw.get("matches"),
            list,
        )
    ):
        raise ValueError(
            "模型缺少能力或匹配列表，请重试。"
        )

    abilities = []
    warnings = []
    ids = set()

    for item in raw["abilities"]:
        ability_id = str(
            item.get("id", "")
        )

        name = clean(
            item.get("name")
        )

        level = item.get("level")

        if (
            not ability_id
            or ability_id in ids
            or not name
            or type(level) is not int
            or level not in range(1, 5)
        ):
            warnings.append(
                "忽略了一条格式不合法的能力。"
            )
            continue

        evidence = []

        for citation in item.get(
            "evidence",
            [],
        ):
            source_id = citation.get(
                "source_id"
            )

            quote = citation.get(
                "quote"
            )

            if (
                source_id in sources
                and isinstance(quote, str)
                and len(quote.strip()) >= 2
                and quote in sources[source_id]
            ):
                evidence.append(
                    {
                        "source_id": source_id,
                        "quote": quote,
                    }
                )

            else:
                warnings.append(
                    "发现无法回溯的引文，"
                    "已删除该引文。"
                )

        if not evidence:
            continue

        # 只有技能清单或教育信息，
        # 不能支持“独立负责”或“带领团队”。
        if all(
            item["source_id"].startswith(
                (
                    "skills.",
                    "certificates.",
                    "education.",
                )
            )
            for item in evidence
        ):
            level = 1

        quotes = "；".join(
            item["quote"]
            for item in evidence
        )

        # 对高等级增加保守的证据约束。
        # 这些约束不能代替人工判断。
        if (
            level == 4
            and not re.search(
                r"带领|领导|统筹|管理团队|团队负责人",
                quotes,
            )
        ):
            level = 3

        if (
            level >= 3
            and not re.search(
                r"独立|主导|负责|牵头|"
                r"带领|领导|统筹|管理团队",
                quotes,
            )
        ):
            level = 2

        abilities.append(
            {
                "id": ability_id,
                "name": name,
                "level": level,
                "evidence": evidence,
            }
        )

        ids.add(ability_id)

    matches = []
    seen = set()

    for item in raw["matches"]:
        requirement_id = str(
            item.get(
                "requirement_id",
                "",
            )
        )

        if (
            requirement_id not in requirements
            or requirement_id in seen
        ):
            raise ValueError(
                "模型返回重复或未知要求编号，"
                "请重试。"
            )

        seen.add(requirement_id)

        linked = [
            ability_id
            for ability_id in item.get(
                "ability_ids",
                [],
            )
            if ability_id in ids
        ]

        alignment = item.get(
            "alignment"
        )

        if alignment not in (
            "直接相关",
            "可迁移",
            "无证据",
        ):
            raise ValueError(
                "模型返回未知匹配类型，请重试。"
            )

        matches.append(
            {
                "requirement_id": requirement_id,
                "ability_ids": linked,
                "alignment": (
                    alignment
                    if linked
                    else "无证据"
                ),
                "reason": clean(
                    item.get("reason")
                ),
            }
        )

    if seen != set(requirements):
        raise ValueError(
            "模型漏评部分岗位条件，请重试；"
            "未写入0分。"
        )

    return {
        "abilities": abilities,
        "matches": matches,
        "warnings": list(
            dict.fromkeys(warnings)
        ),
    }


def month(value, ending=False):
    text = str(
        value or ""
    ).strip()

    if ending and text in (
        "至今",
        "现在",
        "目前",
        "present",
        "Present",
    ):
        return (
            date.today().year * 12
            + date.today().month
        )

    matched = re.fullmatch(
        r"(\d{4})[-/.年](\d{1,2})"
        r"(?:月|[-/.]\d{1,2})?",
        text,
    )

    if not matched:
        return None

    year, number = map(
        int,
        matched.groups(),
    )

    if (
        1950 <= year <= date.today().year
        and 1 <= number <= 12
    ):
        return year * 12 + number

    return None


def scoped_years(
    candidate,
    evidence,
    scope="",
):
    intervals = []

    source_ids = {
        item["source_id"]
        for item in evidence
    }

    for source_id in source_ids:
        if not source_id.startswith(
            "experiences."
        ):
            continue

        index = int(
            source_id.split(".")[1]
        )

        item = candidate[
            "experiences"
        ][index]

        # 在某份工作中做过一件事，不代表整个任职期
        # 都从事该专项工作。
        # 只有岗位名称明确覆盖专项领域时，
        # 才使用任职区间估计；否则交由人工核实。
        if (
            scope
            and scope.casefold()
            not in str(
                item.get("role", "")
            ).casefold()
        ):
            continue

        start = month(
            item.get("start_date")
        )

        end = month(
            item.get("end_date"),
            True,
        )

        current = (
            date.today().year * 12
            + date.today().month
        )

        if (
            start
            and end
            and start <= end <= current
        ):
            intervals.append(
                (start, end)
            )

    covered = set()

    for start, end in intervals:
        covered.update(
            range(start, end)
        )

    if intervals:
        return len(covered) / 12

    return None


def evaluate(
    candidate,
    condition,
    linked,
    match,
):
    field = condition["field"]

    if field == "学历":
        records = candidate.get(
            "education",
            [],
        )

        degrees = [
            str(
                item.get("degree", "")
            )
            for item in records
        ]

        if any(
            re.search(
                r"在读|预计|肄业",
                value,
            )
            for value in degrees
        ):
            return (
                0,
                "待核实",
                "education：含在读或未完成学历，"
                "请核实毕业状态。",
            )

        levels = [
            level
            for degree in degrees
            for name, level in LEVELS.items()
            if name in degree
        ]

        if not levels:
            return (
                0,
                "待核实",
                "education.degree："
                "未提供可识别学历。",
            )

        if condition["rule"] == "最低":
            ratio = float(
                max(levels)
                >= LEVELS[
                    condition["value"]
                ]
            )

        else:
            values = [
                value.strip()
                for value in re.split(
                    r"[;；、,，]",
                    condition["value"],
                )
                if value.strip()
            ]

            hits = [
                any(
                    value in degree
                    for degree in degrees
                )
                for value in values
            ]

            ratio = float(
                any(hits)
                if condition["rule"] == "任一"
                else all(hits)
            )

        return (
            ratio,
            "满足" if ratio else "未匹配",
            "education.degree："
            + "、".join(degrees),
        )

    if (
        field == "工作年限"
        and not condition.get("scope")
    ):
        years = candidate.get(
            "work_years"
        )

        missing = " ".join(
            candidate.get(
                "missing_fields",
                [],
            )
        )

        if (
            years is None
            or years <= 0
            or re.search(
                r"年限|work_years",
                missing,
            )
        ):
            return (
                0,
                "待核实",
                "work_years：总年限未知或需核实；"
                "默认0不作为零年经验。",
            )

        ratio = float(
            years >= float(
                condition["value"]
            )
        )

        return (
            ratio,
            "满足" if ratio else "未匹配",
            f"work_years：{years}年"
            "（来自入库结构化字段）。",
        )

    evidence = [
        item
        for ability in linked
        for item in ability["evidence"]
    ]

    description = "；".join(
        f"{item['source_id']}：{item['quote']}"
        for item in evidence
    )

    if (
        not linked
        or match.get("alignment") == "无证据"
    ):
        return (
            0,
            "待核实",
            "未找到足够证据，"
            "不表示候选人不具备该能力。",
        )

    if field == "工作年限":
        years = scoped_years(
            candidate,
            evidence,
            condition.get("scope", ""),
        )

        if (
            years is not None
            and years >= float(
                condition["value"]
            )
            and match["alignment"]
            == "直接相关"
        ):
            return (
                1,
                "满足",
                f"相关工作区间去重后至少"
                f"{years:.2f}年；"
                + description,
            )

        return (
            0,
            "待核实",
            "专项年限证据不完整或不足，"
            "不能用总年限替代；"
            + description,
        )

    alignment = (
        1
        if match["alignment"] == "直接相关"
        else 0.6
    )

    if field in (
        "专业",
        "竞赛证书",
    ):
        ratio = alignment

    else:
        actual_level = max(
            item["level"]
            for item in linked
        )

        required_level = condition.get(
            "target_level",
            2,
        )

        ratio = (
            min(
                actual_level / required_level,
                1,
            )
            * alignment
        )

    description += "；能力：" + "、".join(
        f"{item['name']}（等级{item['level']}）"
        for item in linked
    )

    description += (
        "；判断："
        + match.get("reason", "")
    )

    return (
        ratio,
        "满足"
        if ratio == 1
        else "部分满足",
        description,
    )


def score_profile(
    candidate,
    job,
    assessment,
):
    abilities = {
        item["id"]: item
        for item in assessment["abilities"]
    }

    matches = {
        item["requirement_id"]: item
        for item in assessment["matches"]
    }

    rows = []

    for index, condition in enumerate(
        job["conditions"]
    ):
        results = []

        for requirement_id, atom in atoms(
            index,
            condition,
        ):
            match = matches.get(
                requirement_id,
                {},
            )

            linked = [
                abilities[ability_id]
                for ability_id in match.get(
                    "ability_ids",
                    [],
                )
                if ability_id in abilities
            ]

            results.append(
                evaluate(
                    candidate,
                    atom,
                    linked,
                    match,
                )
            )

        if len(results) == 1:
            ratio, state, evidence = (
                results[0]
            )

        else:
            if condition["rule"] == "任一":
                ratio = max(
                    result[0]
                    for result in results
                )

            else:
                ratio = (
                    sum(
                        result[0]
                        for result in results
                    )
                    / len(results)
                )

            state = (
                "满足"
                if ratio == 1
                else "部分满足"
                if ratio
                else "待核实"
            )

            evidence = "；".join(
                result[2]
                for result in results
            )

        pending = (
            state != "满足"
            and any(
                result[1] == "待核实"
                for result in results
            )
        )

        rows.append(
            {
                **condition,
                "ratio": ratio,
                "state": state,
                "evidence": evidence,
                "pending_evidence": pending,
            }
        )

    def mean(items):
        if not items:
            return 0

        return (
            sum(
                item["ratio"]
                for item in items
            )
            / len(items)
        )

    dimensions = []

    for name in DIMENSIONS:
        group = [
            item
            for item in rows
            if DIMENSION_OF[item["field"]]
            == name
        ]

        base = [
            item
            for item in group
            if item["kind"] != "优先加分"
        ]

        bonus = [
            item
            for item in group
            if item["kind"] == "优先加分"
        ]

        if base:
            ratio = min(
                1,
                mean(base)
                + 0.2 * mean(bonus),
            )

        else:
            ratio = mean(bonus)

        weight = job["weights"].get(
            name,
            0,
        )

        dimensions.append(
            {
                "name": name,
                "score": round(
                    ratio * 100,
                    2,
                ),
                "weight": weight,
                "points": round(
                    ratio * weight,
                    2,
                ),
            }
        )

    hard = [
        item
        for item in rows
        if item["kind"] != "优先加分"
    ]

    status = "未设置硬性条件"

    if hard:
        if any(
            item["state"] == "未匹配"
            for item in hard
        ):
            status = "不达标"

        elif any(
            item["state"] != "满足"
            for item in hard
        ):
            status = "待核实"

        else:
            status = "达标"

    total = round(
        sum(
            item["points"]
            for item in dimensions
        ),
        2,
    )

    pending = sum(
        item["pending_evidence"]
        for item in rows
    )

    audit = [
        (
            "提取岗位要求："
            + "；".join(
                f"{item['field']} "
                f"{item['value']} "
                f"{item.get('scope', '')}"
                for item in rows
            )
        ),
        (
            "读取候选人结构化简历："
            + "；".join(
                f"{item['name']}"
                f"（等级{item['level']}）"
                for item in abilities.values()
            )
        ),
        (
            "评分口径：能力等级1了解、2参与、"
            "3独立负责、4带领。"
            "直接相关系数1，可迁移0.6；"
            "能力比例=min(证据等级/要求等级,1)"
            "×相关系数。"
            "专业及证书只比较相关性，"
            "学历与年限程序比较。"
        ),
        (
            "分号分隔的多个要求分别匹配："
            "任一取最高比例，全部取平均比例；"
            "全部要求仅在各项均满足时标为满足。"
        ),
        (
            "维度基础条件取平均，"
            "优先项额外增加其平均比例×0.2"
            "并封顶1；"
            "仅有优先项取其平均。"
            "缺失证据暂不计分，"
            "但标记待核实，不自动淘汰。"
        ),
        *[
            (
                f"{item['field']} "
                f"{item['value']}："
                f"{item['state']}，"
                f"比例{item['ratio']:.0%}；"
                f"{item['evidence']}"
            )
            for item in rows
        ],
        (
            "分项打分计算："
            + "；".join(
                f"{item['name']} "
                f"{item['points']}/"
                f"{item['weight']}分"
                for item in dimensions
            )
        ),
        (
            f"结论：综合匹配{total}分；"
            f"硬性条件{status}；"
            f"{pending}项证据待核实。"
            "分数是简历证据匹配分，"
            "不是实际能力或录用概率。"
        ),
        (
            "以上为证据与计算审计，"
            "不是模型内部思维链。"
            "引用仅定位入库结构化字段，"
            "需人工核对原简历。"
        ),
        *assessment.get(
            "warnings",
            [],
        ),
    ]

    return {
        "candidate_code": candidate[
            "candidate_code"
        ],
        "total_score": total,
        "hard_status": status,
        "pending_count": pending,
        "dimensions": dimensions,
        "conditions": rows,
        "abilities": list(
            abilities.values()
        ),
        "audit_log": audit,
    }