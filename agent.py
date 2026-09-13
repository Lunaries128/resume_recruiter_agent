import json

from langchain.agents import (
    create_agent,
)
from langchain_core.messages import (
    HumanMessage,
    ToolMessage,
)

from guardrails import (
    safe_output_text,
    validate_filter_request,
)
from llm import llm
from memory import retrieve_preferences
from tools import (
    calculate_match_score,
    generate_candidate_report,
    parse_resume,
    query_candidates,
    remember_hr_preference,
)

from guardrails import (
    safe_output_text,
    validate_filter_request,
    validate_privacy_request,
)


SYSTEM_PROMPT = """
你是一名招聘辅助Agent。

你的职责是整理和比较候选人的岗位相关信息，
不能替代HR作出录用、淘汰或面试决定。

规则：

1. 只使用技能、工作年限、学历、工作经历、
   证书和项目经验等岗位相关信息。

2. 禁止根据年龄、性别、婚育、民族、
   宗教、残疾、健康、户籍、籍贯、照片、
   外貌或政治面貌筛选、评分和排名。

3. 不推测简历中没有的信息。

4. 信息缺失时标注“信息缺失”，
   不得直接把信息缺失理解为候选人不合格。

5. 需要筛选候选人时，可以使用
   query_candidates执行只读查询。

6. 需要评分时，必须调用
   calculate_match_score。
   评分后说明技能、经验、学历和项目得分。

7. 需要生成报告时调用
   generate_candidate_report。

8. 用户明确说“记住这个偏好”时，
   才调用remember_hr_preference。

9. 输出候选人时使用candidate_code，
   不输出手机号、邮箱、身份证号和住址。

10. 不展示隐藏思维链。
    只展示工具名称、输入参数、工具结果
    和可以核查的评分依据。

11. 所有匹配度和排名仅供HR人工复核。
"""


recruitment_agent = create_agent(
    model=llm,
    tools=[
        parse_resume,
        query_candidates,
        calculate_match_score,
        generate_candidate_report,
        remember_hr_preference,
    ],
    system_prompt=SYSTEM_PROMPT,
)


_sessions: dict[str, list] = {}


def content_to_text(content) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []

        for block in content:
            if isinstance(block, str):
                parts.append(block)

            elif isinstance(block, dict):
                text = block.get("text")

                if text:
                    parts.append(text)

        return "".join(parts)

    return str(content) if content else ""


def extract_trace(
    messages: list,
) -> list[dict]:
    trace = []

    for message in messages:
        tool_calls = getattr(
            message,
            "tool_calls",
            None,
        )

        if tool_calls:
            for call in tool_calls:
                trace.append({
                    "type": "tool_call",
                    "tool": call.get(
                        "name",
                        "unknown",
                    ),
                    "arguments": call.get(
                        "args",
                        {},
                    ),
                })

        if isinstance(
            message,
            ToolMessage,
        ):
            trace.append({
                "type": "tool_result",
                "tool_call_id": (
                    message.tool_call_id
                ),
                "summary": (
                    content_to_text(
                        message.content
                    )[:800]
                ),
            })

    return trace


def trim_history(
    messages: list,
    max_user_turns: int = 6,
) -> list:
    user_positions = [
        index
        for index, message in enumerate(
            messages
        )
        if isinstance(
            message,
            HumanMessage,
        )
    ]

    if (
        len(user_positions)
        <= max_user_turns
    ):
        return messages

    return messages[
        user_positions[-max_user_turns]:
    ]


def chat(
    session_id: str,
    hr_id: str,
    message: str,
    jd: str,
) -> dict:
    try:
        validate_filter_request(
            message
        )

        validate_privacy_request(
            message
        )

        validate_filter_request(jd)

    except ValueError as error:
        reply = str(error)

        return {
            "reply": reply,
            "trace": [{
                "type": "guardrail_block",
                "summary": reply,
            }],
        }

    history = _sessions.setdefault(
        session_id,
        [],
    )

    try:
        preferences = (
            retrieve_preferences(
                hr_id=hr_id,
                query=message + "\n" + jd,
            )
        )

    except Exception:
        preferences = []

    prompt = f"""
当前HR编号：
{hr_id}

当前岗位JD：
{jd or "尚未提供JD"}

与当前任务有关的长期偏好：
{json.dumps(
    preferences,
    ensure_ascii=False,
)}

用户当前问题：
{message}

请判断是否需要调用工具。
匹配和排名必须给出可核查依据，
并提醒HR进行人工复核。
"""

    history.append(
        HumanMessage(
            content=prompt
        )
    )

    result = recruitment_agent.invoke(
        {
            "messages": history,
        },
        config={
            "recursion_limit": 30,
        },
    )

    result_messages = result[
        "messages"
    ]

    _sessions[session_id] = (
        trim_history(result_messages)
    )

    reply = content_to_text(
        result_messages[-1].content
    )

    reply = safe_output_text(
        reply
        or "Agent没有返回文本内容。"
    )

    return {
        "reply": reply,
        "trace": extract_trace(
            result_messages
        ),
    }


def clear_session(
    session_id: str,
):
    _sessions.pop(
        session_id,
        None,
    )