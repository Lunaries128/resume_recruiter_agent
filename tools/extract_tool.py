from langchain_core.tools import tool

from database import upsert_candidate
from guardrails import (
    safe_candidate_code,
)
from llm import llm
from schemas import CandidateProfile
from tools.resume_parser_tool import (
    parse_file,
)


extractor = llm.with_structured_output(
    CandidateProfile
)


def ingest_resume(
    file_path: str,
    filename: str,
) -> dict:
    redacted_text = parse_file(
        file_path
    )

    candidate_code = (
        safe_candidate_code(
            filename
        )
    )

    prompt = f"""
请从下面的脱敏简历中抽取岗位相关信息。

要求：

1. 不推断年龄、性别、婚姻、民族、宗教、
   健康状况等敏感信息；
2. 不根据姓名、照片、联系方式进行判断；
3. 工作年限无法确认时填写0，并加入missing_fields；
4. 技能使用统一、简洁的名称；
5. 只抽取简历中有证据的信息；
6. candidate_code必须填写为：
   {candidate_code}

脱敏简历：

{redacted_text}
"""

    profile = extractor.invoke(
        prompt
    )

    profile.candidate_code = (
        candidate_code
    )

    profile_data = (
        profile.model_dump()
    )

    upsert_candidate(
        profile=profile_data,
        filename=filename,
        redacted_text=redacted_text,
    )

    return profile_data


@tool
def extract_resume_information(
    file_path: str,
    filename: str,
) -> dict:
    """
    解析简历并抽取学历、技能、工作年限、
    项目和证书等结构化信息，然后写入候选人库。
    """

    return ingest_resume(
        file_path=file_path,
        filename=filename,
    )