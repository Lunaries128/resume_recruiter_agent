import hashlib
import json
import os
import re
import threading
import uuid

from datetime import (
    datetime,
    timezone,
    timedelta,
)
from html import escape
from io import BytesIO
from pathlib import Path


BLOCK = re.compile(
    r"性别|年龄|出生|生日|民族|籍贯|婚育|婚姻|"
    r"身份证|家庭|住址|宗教|政治面貌|自我评价|"
    r"性格开朗|吃苦耐劳|学习能力强|积极参与|"
    r"提升自我|责任心|团队精神"
)

FONT_LOCK = threading.Lock()

FIELDS = (
    "school",
    "major",
    "degree",
    "organization",
    "role",
    "name",
    "level",
    "start_date",
    "end_date",
    "date",
    "description",
)


def clean(value):
    text = str(value or "")

    # 按句子或分句过滤，避免仅删除标签后留下隐私值。
    parts = re.split(
        r"[\n；;。]",
        text,
    )

    kept = []

    for part in parts:
        part = part.strip()

        if not part:
            continue

        if BLOCK.search(part):
            continue

        if re.search(
            r"(?<!\d)\d{1,3}\s*岁"
            r"|(?:^|\s)[男女](?:\s|$)",
            part,
        ):
            continue

        kept.append(part)

    text = "；".join(kept)

    text = re.sub(
        r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
        "[邮箱已隐藏]",
        text,
    )

    text = re.sub(
        r"(?<!\d)1[3-9]\d{9}(?!\d)",
        "[电话已隐藏]",
        text,
    )

    text = re.sub(
        r"(?<!\d)\d{17}[\dXx](?!\d)",
        "[证件号已隐藏]",
        text,
    )

    text = re.sub(
        r"(?:姓名|联系人)\s*[:：]\s*[^\s，；]+",
        "姓名已脱敏",
        text,
    )

    return text.strip()


def safe_name(value):
    value = str(value or "")

    if re.fullmatch(
        r"[\u4e00-\u9fff]{1,2}\*",
        value,
    ):
        return value

    return "候选人（姓名已隐藏）"


def facts(candidate):
    result = []

    groups = [
        ("教育经历", "education"),
        ("工作与实习", "experiences"),
        ("项目经历", "projects"),
        ("竞赛获奖", "awards"),
    ]

    for title, key in groups:
        rows = []

        for item in candidate.get(key, []):
            values = [
                clean(item.get(field))
                for field in FIELDS
            ]

            for field in (
                "actions",
                "results",
                "technologies",
            ):
                values += [
                    clean(value)
                    for value in item.get(
                        field,
                        [],
                    )
                ]

            # 去掉空值和完全重复内容。
            values = list(
                dict.fromkeys(
                    value
                    for value in values
                    if value
                )
            )

            line = " / ".join(values)

            if line:
                rows.append(line)

        result.append(
            {
                "title": title,
                "lines": (
                    rows
                    or ["未提供可核验信息"]
                ),
            }
        )

    skills = [
        clean(value)
        for value in (
            candidate.get("skills", [])
            + candidate.get("certificates", [])
        )
    ]

    skill_text = "、".join(
        value
        for value in skills
        if value
    )

    result.append(
        {
            "title": "技能与证书",
            "lines": [
                skill_text or "未提供"
            ],
        }
    )

    return result


def compose_report(job, candidate):
    detail = (
        candidate.get("score_detail")
        or {}
    )

    if not detail:
        raise ValueError(
            "候选人尚未完成当前岗位评分。"
        )

    rows = detail.get("conditions", [])

    sections = []

    def add(title, lines):
        sections.append(
            {
                "title": title,
                "lines": (
                    lines
                    or ["暂无可核验记录"]
                ),
            }
        )

    requirements = []

    for row in job["payload"]["conditions"]:
        value = (
            clean(row["value"])
            or "内容已过滤，需复核"
        )

        requirements.append(
            f"{row['kind']} / "
            f"{row['field']} / "
            f"{row['rule']}：{value}"
        )

    add(
        "一、岗位要求与评估口径",
        requirements
        + [
            (
                "依据本次选定并保存的岗位模板评估；"
                "人工修改后的条件优先于原始JD。"
            ),
            (
                "分数衡量简历证据与岗位条件的"
                "规则匹配程度，不代表录用概率。"
                "信息缺失不等于不具备能力。"
            ),
        ],
    )

    add(
        "二、岗位匹配综合结论",
        [
            (
                f"综合匹配分："
                f"{detail['total_score']:g}/100；"
                f"硬性条件状态："
                f"{detail['hard_status']}。"
            ),
            (
                "本报告仅供初筛辅助。"
                "未匹配和待核实项应由HR结合"
                "原文件及面试复核，"
                "不自动作出录用或淘汰决定。"
            ),
        ],
    )

    dimension_lines = []

    for item in detail["dimensions"]:
        dimension_lines.append(
            f"{item['name']}："
            f"维度得分 {item['score']:g}/100，"
            f"权重 {item['weight']:g}%，"
            f"计入总分 {item['points']:g} 分。"
        )

    dimension_lines.append(
        "总分 = 各维度得分 × 对应权重后求和；"
        "权重为0的维度不参与总分。"
    )

    add(
        "三、各维度分项得分",
        dimension_lines,
    )

    advantages = []
    risks = []
    audit = []
    questions = []

    for index, row in enumerate(rows, 1):
        value = (
            clean(row["value"])
            or "条件内容需人工复核"
        )

        evidence = (
            clean(row.get("evidence"))
            or "证据为空或含已过滤内容，请核验原文件"
        )

        source = (
            clean(row.get("source"))
            or "人工填写或未记录原句"
        )

        description = (
            f"{row['field']}：{value}；"
            f"结果：{row['state']}；"
            f"结构化字段证据：{evidence}。"
        )

        if row["state"] == "满足":
            advantages.append(description)

        else:
            risks.append(description)

        audit.append(
            f"条件{index} | "
            f"{row['kind']} / {row['field']}；"
            f"要求：{value}；"
            f"JD来源：{source}；"
            f"规则：{row['rule']}；"
            f"匹配比例：{row['ratio']:.0%}；"
            f"状态：{row['state']}；"
            f"证据：{evidence}。"
        )

        if row["state"] != "满足":
            questions.append(
                f"针对岗位要求“{value}”，"
                f"请补充可核验的{row['field']}材料"
                "或实际案例，并说明本人承担的工作"
                "及结果。"
            )

        elif row["field"] in ("技能", "项目"):
            questions.append(
                f"请围绕“{value}”介绍一个实际案例，"
                "说明技术选择、本人贡献、"
                "验证方法及结果。"
            )

    add(
        "四、已识别优势",
        advantages,
    )

    add(
        "五、短板与待核实风险",
        risks,
    )

    audit_lines = [
        (
            "提取岗位要求："
            + "；".join(requirements)
        ),
        (
            "读取候选人结构化简历："
            "仅使用入库后的客观字段，"
            "不使用人口统计或家庭信息。"
        ),
        (
            "匹配计算："
            "条件按任一、全部或最低规则计算比例；"
            "维度内基础条件取平均。"
            "基础条件与优先条件并存时，"
            "在基础比例上增加"
            "0.2×优先条件平均比例，上限为1；"
            "仅有优先条件时取其平均比例。"
        ),
        *audit,
        (
            "分项打分计算："
            + "；".join(
                dimension_lines[:-1]
            )
        ),
        (
            f"结论：综合匹配 "
            f"{detail['total_score']:g} 分；"
            f"硬性条件{detail['hard_status']}。"
        ),
        (
            "以上为可核验的规则执行记录，"
            "不是模型的内部思维链。"
            "来源定位到结构化字段；"
            "未记录原PDF页码，"
            "不生成虚构页码或引文。"
        ),
    ]

    add(
        "六、评分审计与证据溯源",
        audit_lines,
    )

    resume_lines = [
        section["title"] + "：" + line
        for section in facts(candidate)
        for line in section["lines"]
    ]

    add(
        "七、清洗后的客观简历",
        resume_lines,
    )

    unique_questions = list(
        dict.fromkeys(questions)
    )

    add(
        "八、针对性面试提问建议",
        unique_questions
        or [
            "请核实岗位所需经历、"
            "实际贡献及可验证成果。"
        ],
    )

    snapshot_source = json.dumps(
        [
            job["payload"],
            candidate,
        ],
        ensure_ascii=False,
        sort_keys=True,
    )

    fingerprint = hashlib.sha256(
        snapshot_source.encode("utf-8")
    ).hexdigest()

    generated_at = datetime.now(
        timezone(timedelta(hours=8))
    ).isoformat(timespec="seconds")

    return {
        "report_id": uuid.uuid4().hex,
        "generated_at": generated_at,
        "job_name": (
            clean(job["name"])
            or "岗位名称已过滤"
        ),
        "job_version": job.get(
            "version",
            1,
        ),
        "candidate_name": safe_name(
            candidate.get("masked_name")
        ),
        "snapshot_hash": fingerprint,
        "sections": sections,
    }


def load_font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    with FONT_LOCK:
        if (
            "ReportChinese"
            not in pdfmetrics.getRegisteredFontNames()
        ):
            font_path = Path(
                os.getenv("REPORT_FONT_PATH")
                or "C:/Windows/Fonts/simhei.ttf"
            )

            if not font_path.is_file():
                raise ValueError(
                    "缺少中文字体，请设置"
                    "REPORT_FONT_PATH为可嵌入的"
                    "中文TTF字体绝对路径。"
                )

            pdfmetrics.registerFont(
                TTFont(
                    "ReportChinese",
                    str(font_path),
                )
            )

    return "ReportChinese"


def render_pdf(report):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import (
        ParagraphStyle,
    )
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
    )

    font = load_font()

    body_style = ParagraphStyle(
        "body",
        fontName=font,
        fontSize=10,
        leading=16,
        wordWrap="CJK",
        spaceAfter=7,
        splitLongWords=True,
    )

    heading_style = ParagraphStyle(
        "heading",
        parent=body_style,
        fontSize=13,
        leading=20,
        textColor=colors.HexColor(
            "#355783"
        ),
        spaceBefore=12,
        keepWithNext=True,
    )

    title_style = ParagraphStyle(
        "title",
        parent=body_style,
        fontSize=20,
        leading=28,
        alignment=TA_CENTER,
        spaceAfter=18,
    )

    story = [
        Paragraph(
            "招聘筛选评估报告",
            title_style,
        )
    ]

    metadata = [
        f"报告编号：{report['report_id']}",
        f"生成时间：{report['generated_at']}",
        (
            f"岗位：{report['job_name']}；"
            f"模板版本：{report['job_version']}"
        ),
        f"候选人：{report['candidate_name']}",
        (
            "数据快照摘要："
            + report["snapshot_hash"][:16]
        ),
    ]

    for line in metadata:
        story.append(
            Paragraph(
                escape(line),
                body_style,
            )
        )

    story.append(Spacer(1, 8))

    for section in report["sections"]:
        story.append(
            Paragraph(
                escape(section["title"]),
                heading_style,
            )
        )

        for line in section["lines"]:
            story.append(
                Paragraph(
                    escape(line),
                    body_style,
                )
            )

    buffer = BytesIO()

    def footer(canvas, document):
        canvas.saveState()

        canvas.setFont(font, 9)

        canvas.setFillColor(
            colors.HexColor("#667085")
        )

        canvas.drawString(
            42,
            25,
            "招聘初筛辅助资料 - 仅供授权人员参考",
        )

        canvas.drawRightString(
            A4[0] - 42,
            25,
            f"第 {document.page} 页",
        )

        canvas.restoreState()

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=42,
        rightMargin=42,
        topMargin=40,
        bottomMargin=48,
        title="招聘筛选评估报告",
        author="招聘助理系统",
    )

    document.build(
        story,
        onFirstPage=footer,
        onLaterPages=footer,
    )

    return buffer.getvalue()