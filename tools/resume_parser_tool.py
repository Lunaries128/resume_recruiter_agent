from pathlib import Path

from docx import Document
from langchain_core.tools import tool
from pypdf import PdfReader

from guardrails import (
    prepare_resume_text,
)


def parse_file(
    file_path: str,
) -> str:
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"文件不存在：{file_path}"
        )

    suffix = path.suffix.lower()

    if suffix == ".pdf":
        reader = PdfReader(
            str(path)
        )

        text = "\n".join(
            page.extract_text() or ""
            for page in reader.pages
        )

    elif suffix == ".docx":
        document = Document(
            str(path)
        )

        text = "\n".join(
            paragraph.text
            for paragraph
            in document.paragraphs
        )

    elif suffix == ".txt":
        text = path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

    else:
        raise ValueError(
            "只支持 PDF、DOCX 和 TXT"
        )

    text = text.strip()

    if not text:
        raise ValueError(
            "没有从简历中提取到文本"
        )

    return prepare_resume_text(
        text
    )


@tool
def parse_resume(
    file_path: str,
) -> str:
    """
    读取PDF、DOCX或TXT简历并返回脱敏后的文本。
    """

    return parse_file(
        file_path
    )