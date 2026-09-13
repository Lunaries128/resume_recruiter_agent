import re
import uuid
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from database import upsert_candidate
from guardrails import redact_pii
from llm import llm
from schemas import CandidateProfile


SUPPORTED_SUFFIXES = {
    ".pdf",
    ".docx",
    ".txt",
}


def read_pdf(path: Path) -> str:
    reader = PdfReader(str(path))

    return "\n".join(
        page.extract_text() or ""
        for page in reader.pages
    )


def read_docx(path: Path) -> str:
    document = Document(str(path))

    return "\n".join(
        paragraph.text
        for paragraph
        in document.paragraphs
        if paragraph.text.strip()
    )


def read_txt(path: Path) -> str:
    for encoding in (
        "utf-8",
        "utf-8-sig",
        "gb18030",
    ):
        try:
            return path.read_text(
                encoding=encoding
            )
        except UnicodeDecodeError:
            continue

    raise ValueError(
        "无法识别文本文件编码。"
    )


def parse_resume_text(
    file_path: str,
) -> str:
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(
            "只支持 PDF、DOCX 和 TXT。"
        )

    if suffix == ".pdf":
        text = read_pdf(path)

    elif suffix == ".docx":
        text = read_docx(path)

    else:
        text = read_txt(path)

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    ).strip()

    if len(text) < 30:
        raise ValueError(
            "简历未解析出足够文本，"
            "可能是扫描版PDF。"
        )

    return text


def extract_profile(
    redacted_text: str,
    source_file: str,
) -> CandidateProfile:
    extractor = llm.with_structured_output(
        CandidateProfile
    )

    profile = extractor.invoke(f"""
你负责从已经脱敏的简历中提取岗位相关信息。

要求：

1. 不推测年龄、性别、婚姻、民族、
   宗教、残疾、家庭情况等敏感属性。
2. 只提取文本中明确出现的信息。
3. 工作年限无法确认时填 0，
   并加入 missing_fields。
4. skills 使用简短、统一的技术名称。
5. evidence 中保存支持结论的简短原文。
6. 不得因为学校名称或公司知名度
   自动判断候选人优劣。
7. candidate_id 暂时留空。
8. source_file 填写：{source_file}

脱敏简历：

{redacted_text}
""")

    profile.candidate_id = (
        "CAND-"
        + uuid.uuid4().hex[:8].upper()
    )

    profile.source_file = source_file

    return profile


def ingest_resume_file(
    file_path: str,
) -> dict:
    path = Path(file_path)

    raw_text = parse_resume_text(
        str(path)
    )

    redacted_text = redact_pii(
        raw_text
    )

    profile = extract_profile(
        redacted_text=redacted_text,
        source_file=path.name,
    )

    upsert_candidate(
        profile=profile.model_dump(),
        redacted_text=redacted_text,
    )

    return {
        "success": True,
        "candidate_id": (
            profile.candidate_id
        ),
        "source_file": path.name,
        "profile": profile.model_dump(),
    }