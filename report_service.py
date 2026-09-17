import hashlib
import os
import re
import threading
import uuid
from datetime import datetime, timezone, timedelta
from html import escape
from io import BytesIO
from pathlib import Path

from guardrails import FLUFF_PHRASES


PRIVATE = re.compile(
    r"性别|年龄|出生|生日|民族|籍贯|户籍|婚姻|婚育|已婚|未婚|宗教|"
    r"政治面貌|身份证|家庭|住址|健康|残疾|男性|女性|男士|女士|"
    r"(?<![A-Za-z0-9])\d{1,3}\s*岁|"
    r"(?:^|[，,；;\s])(?:男|女)(?:$|[，,；;\s])"
)

_font_lock = threading.Lock()


def clean(value):
    """过滤敏感片段、联系方式和性格套话。"""
    parts = re.split(
        r"[\n\r；;。]+",
        str(value or ""),
    )

    result = []

    for part in parts:
        if PRIVATE.search(part):
            continue

        if any(word in part for word in FLUFF_PHRASES):
            continue

        part = re.sub(
            r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
            "[联系方式已隐藏]",
            part,
        )
        part = re.sub(
            r"(?<![A-Za-z0-9])\d{17}[\dXx](?![A-Za-z0-9])",
            "[证件号已隐藏]",
            part,
        )
        part = re.sub(
            r"(?<![A-Za-z0-9])(?:\+?86[- ]?)?"
            r"1[3-9]\d{9}(?![A-Za-z0-9])",
            "[联系方式已隐藏]",
            part,
        )
        part = re.sub(
            r"(?<![A-Za-z0-9])0\d{2,3}[- ]?"
            r"\d{7,8}(?![A-Za-z0-9])",
            "[联系方式已隐藏]",
            part,
        )
        part = re.sub(
            r"(?:姓名|联系人|推荐人|证明人)"
            r"\s*[:：]\s*[^\s,，;；]+",
            "[姓名已隐藏]",
            part,
        )

        part = " ".join(part.split())

        if part:
            result.append(part)

    return "；".join(result)


def lines(values):
    return list(
        dict.fromkeys(
            text
            for value in values
            if (text := clean(value))
        )
    )


def brief(value, limit=90):
    text = clean(value)

    if len(text) <= limit:
        return text

    return text[:limit] + "…（节选）"


def build_report(session, candidates):
    if not session.get("jd", "").strip() or not candidates:
        raise ValueError(
            "请先保存JD并完成候选人核验。"
        )

    report = {
        "id": uuid.uuid4().hex,
        "version": "2.0",
        "title": "候选人初筛摘要",
        "created_at": datetime.now(
            timezone(timedelta(hours=8))
        ).isoformat(timespec="seconds"),
        "session_id": session["id"],
        "jd_hash": hashlib.sha256(
            session["jd"].encode("utf-8")
        ).hexdigest(),
        "jd": clean(session["jd"]),
        "note": (
            "仅依据当前JD和已入库简历；"
            "经历与结果未经独立核实，"
            "供HR人工初筛参考。未联网搜索。"
        ),
        "candidates": [],
        "overview": [],
    }

    for candidate in candidates:
        detail = candidate.get("score_detail") or {}

        if (
            detail.get("matching_version") != "evidence-v2"
            or not detail.get("reviews")
        ):
            raise ValueError(
                "所选候选人尚无新版核验结果，"
                "请先重新匹配当前JD。"
            )

        name = str(candidate.get("masked_name") or "")

        if not re.fullmatch(
            r"[\u4e00-\u9fff]{1,2}\*",
            name,
        ):
            name = "姓名未识别"

        checks = []

        for row in detail["reviews"]:
            quotes = lines(
                ref["quote"] for ref in row["references"]
            )
            evidence = clean(row["reason"])

            if quotes:
                evidence += (
                    "；证据节选："
                    + brief("；".join(quotes))
                )

            checks.append({
                "requirement": clean(row["name"]),
                "category": row["category"],
                "status": row["status"],
                "evidence": evidence,
            })

        core = [
            item
            for item in detail["reviews"]
            if item["category"] in ("必需条件", "岗位职责")
        ]

        order = {
            "存在明确差距": 0,
            "信息不足": 1,
            "有部分支持": 2,
            "有充分支持": 3,
        }

        priority = sorted(
            detail["reviews"],
            key=lambda item: (
                item["category"] == "优先条件",
                order[item["status"]],
            ),
        )

        focus = lines(
            f"{item['name']}：{item['reason']}"
            for item in priority
            if item["status"] != "有充分支持"
        )[:3]

        strengths = lines(
            item["name"] + "：" + item["reason"]
            for item in core
            if item["status"] == "有充分支持"
        )[:2]

        questions = lines(
            item["question"]
            for item in priority
            if item["category"] != "需确认"
        )[:3]

        sections = [
            {
                "title": "初筛结论",
                "items": [
                    clean(detail["recommendation"]),
                    clean(detail["summary"]),
                ],
            },
            {
                "title": "重点关注",
                "items": (
                    ["优势：" + item for item in strengths]
                    + ["待核实：" + item for item in focus]
                ) or [
                    "现有核心要求均有支持，"
                    "面试仍需核实经历与本人贡献。"
                ],
            },
            {
                "title": "建议面试问题",
                "items": questions or [
                    "请说明最相关经历中的本人职责、"
                    "行动和可核验结果。"
                ],
            },
        ]

        report["candidates"].append({
            "code": candidate["candidate_code"],
            "name": name,
            "job_title": (
                clean(detail.get("job_title"))
                or "待确认岗位"
            ),
            "checks": checks,
            "sections": sections,
        })

        report["overview"].append({
            "候选人": (
                name + " / " + candidate["candidate_code"]
            ),
            "初筛建议": clean(detail["recommendation"]),
            "核心要求": clean(detail["summary"]),
            "重点核实": (
                "；".join(brief(item, 50) for item in focus)
                or "经历真实性与本人贡献"
            ),
        })

    return report


def pdf_bytes(report):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        PageBreak,
        LongTable,
        TableStyle,
    )

    with _font_lock:
        if "ReportCN" not in pdfmetrics.getRegisteredFontNames():
            paths = [
                os.getenv("PDF_FONT_PATH", ""),
                "C:/Windows/Fonts/simsun.ttc",
                "C:/Windows/Fonts/simhei.ttf",
                "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            ]

            for path in paths:
                if not path or not Path(path).is_file():
                    continue

                try:
                    pdfmetrics.registerFont(
                        TTFont(
                            "ReportCN",
                            path,
                            subfontIndex=0,
                        )
                    )
                    break
                except Exception:
                    continue
            else:
                raise ValueError(
                    "请将PDF_FONT_PATH设置为"
                    "可嵌入的中文TTF或TTC字体。"
                )

    body = ParagraphStyle(
        "body",
        fontName="ReportCN",
        fontSize=9,
        leading=13,
        spaceAfter=4,
        wordWrap="CJK",
    )
    heading = ParagraphStyle(
        "heading",
        parent=body,
        fontSize=11,
        leading=16,
        spaceBefore=8,
        spaceAfter=5,
        keepWithNext=True,
    )
    title = ParagraphStyle(
        "title",
        parent=body,
        fontSize=17,
        leading=24,
        spaceAfter=10,
    )
    small = ParagraphStyle(
        "small",
        parent=body,
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#64748B"),
    )

    def p(text, style=body):
        return Paragraph(
            escape(str(text)).replace("\n", "<br/>"),
            style,
        )

    story = []
    width = A4[0] - 72

    def table(headers, rows, ratios):
        data = [
            [p(item) for item in headers],
            *[
                [p(item) for item in row]
                for row in rows
            ],
        ]

        result = LongTable(
            data,
            colWidths=[width * ratio for ratio in ratios],
            repeatRows=1,
            splitInRow=1,
            hAlign="LEFT",
        )

        result.setStyle(TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (-1, 0),
                colors.HexColor("#EAF0F8"),
            ),
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.3,
                colors.HexColor("#CCD5E0"),
            ),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))

        story.append(result)

    if len(report["candidates"]) > 1:
        story += [
            p("候选人初筛汇总", title),
            p("生成时间：" + report["created_at"], small),
        ]

        table(
            ["候选人", "初筛建议", "核心要求与重点核实"],
            [
                [
                    item["候选人"],
                    item["初筛建议"],
                    item["核心要求"]
                    + "\n"
                    + item["重点核实"],
                ]
                for item in report["overview"]
            ],
            [0.25, 0.22, 0.53],
        )

        story += [
            Spacer(1, 8),
            p(report["note"], small),
            PageBreak(),
        ]

    for index, candidate in enumerate(report["candidates"]):
        if index:
            story.append(PageBreak())

        story += [
            p(report["title"], title),
            p(
                "岗位：" + candidate["job_title"]
                + "　候选人：" + candidate["name"]
            ),
            p(
                "编号：" + candidate["code"]
                + "　生成时间：" + report["created_at"],
                small,
            ),
        ]

        conclusion, focus, questions = candidate["sections"]

        story.append(
            p(conclusion["title"], heading)
        )
        story += [
            p(item) for item in conclusion["items"]
        ]

        story.append(p("岗位要求核验", heading))

        table(
            ["岗位要求", "判断", "简历依据 / 待核实内容"],
            [
                [
                    item["requirement"]
                    + "\n（"
                    + item["category"]
                    + "）",
                    item["status"],
                    item["evidence"],
                ]
                for item in candidate["checks"]
            ],
            [0.25, 0.17, 0.58],
        )

        for section in (focus, questions):
            story.append(
                p(section["title"], heading)
            )
            story += [
                p(f"{number}. {item}")
                for number, item in enumerate(
                    section["items"], 1
                )
            ]

        story += [
            Spacer(1, 8),
            p(report["note"], small),
        ]

    buffer = BytesIO()

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=32,
        bottomMargin=35,
        title=report["title"],
        author="招聘助理",
    )

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("ReportCN", 7)
        canvas.drawString(
            36,
            18,
            "初筛摘要 / " + report["id"][:12],
        )
        canvas.drawRightString(
            A4[0] - 36,
            18,
            f"第 {doc.page} 页",
        )
        canvas.restoreState()

    document.build(
        story,
        onFirstPage=footer,
        onLaterPages=footer,
    )

    return buffer.getvalue()