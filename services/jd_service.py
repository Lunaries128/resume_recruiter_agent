from guardrails import validate_filter_request
from llm import llm
from schemas import JobRequirements


jd_extractor = llm.with_structured_output(JobRequirements)


def extract_jd_requirements(jd: str) -> dict:
    jd = jd.strip()
    if not jd:
        raise ValueError("招聘要求不能为空。")

    validate_filter_request(jd)

    prompt = f"""
请从招聘要求中抽取可核验、与岗位直接相关的匹配条件。

规则：
1. 不得抽取年龄、性别、民族、籍贯、婚育、宗教、健康等敏感条件；
2. 没有明确提出的内容保持为空，不得猜测；
3. required_skills仅填写明确要求掌握的技能；
4. preferred_skills填写“优先、加分、熟悉更佳”的技能；
5. project_keywords填写项目领域或项目类型；
6. award_keywords填写竞赛、奖项或证书要求；
7. minimum_work_years只填写明确出现的最低年限。

招聘要求：
{jd}
"""
    return jd_extractor.invoke(prompt).model_dump()
