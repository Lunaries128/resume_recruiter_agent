import json

from langchain.agents import create_agent
from langchain_core.messages import (
    HumanMessage,
    ToolMessage,
)

from guardrails import (
    validate_screening_request,
)
from llm import llm
from memory import search_hr_preferences
from tools import (
    calculate_candidate_matches,
    extract_resume_information,
    generate_candidate_report,
    query_candidate_database,
    read_resume,
    remember_hr_preference,
)


SYSTEM_PROMPT = """
你是一名招聘辅助 Agent。

你的职责是帮助HR整理和比较候选人的
岗位相关信息，但不能替代HR作出录用、
淘汰或面试决定。

工作规则：

1. 只使用技能、工作年限、学历要求、
   工作经历、证书和项目经验等
   与岗位直接相关的信息。

2. 禁止根据年龄、性别、婚姻、
   民族、宗教、残疾、户籍、照片、
   外貌等敏感信息筛选、评分或排名。

3. 不得推测简历中没有的信息。

4. 用户要求筛选候选人时，
   先确认JD中的必需技能、最低年限、
   学历和项目要求，再调用评分或SQL工具。

5. 匹配度只是辅助指标。
   回答中必须提醒HR进行人工复核。

6. 解释排名时，只能引用结构化字段、
   评分明细和证据，不能生成隐藏的
   思维链或主观人格评价。

7. SQL只能查询candidate_safe_view，
   不允许修改或删除数据。

8. 删除候选人不能通过工具直接执行，
   必须使用API的人工确认流程。

9. 用户明确说“记住我的偏好”时，
   才调用remember_hr_preference。

10. 输出候选人时优先使用candidate_id，
    不输出手机号、邮箱、身份证和地址。

11. 信息不足时明确写“信息缺失”，
    不得把缺失信息视为不合格。

12. 用户要求比较时使用结构化表格，
    并说明每项得分依据。
"""


recruitment_agent = create_agent(
    model=llm,
    tools=[
        read_resume,
        extract_resume_information,
        query_candidate_database,
        calculate_candidate_matches,
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
    """
    返回工具审计轨迹。

    这里只展示调用了什么工具和执行结果，
    不展示模型隐藏思维链。
    """

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
            content = content_to_text(
                message.content
            )

            trace.append({
                "type": "tool_result",
                "tool_call_id": (
                    message.tool_call_id
                ),
                "summary": content[:500],
            })

    return trace


def chat(
    session_id: str,
    hr_id: str,
    message: str,
    jd: str,
) -> dict:
    valid, guardrail_message = (
        validate_screening_request(
            message
        )
    )

    if not valid:
        return {
            "reply": guardrail_message,
            "trace": [{
                "type": (
                    "guardrail_block"
                ),
                "summary": (
                    guardrail_message
                ),
            }],
        }

    history = _sessions.setdefault(
        session_id,
        [],
    )

    preferences = search_hr_preferences(
        hr_id=hr_id,
        query=message + "\n" + jd,
    )

    prompt = f"""
当前HR编号：
{hr_id}

当前岗位JD：
{jd or "用户尚未提供JD"}

与当前任务相关的长期偏好：
{json.dumps(
    preferences,
    ensure_ascii=False
)}

用户问题：
{message}

请判断是否需要调用工具。
所有匹配和排名仅作为人工决策辅助。
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

    # 保留最近6个用户回合附近的消息
    human_positions = [
        index
        for index, item in enumerate(
            result_messages
        )
        if isinstance(
            item,
            HumanMessage,
        )
    ]

    if len(human_positions) > 6:
        start = human_positions[-6]
        result_messages = (
            result_messages[start:]
        )

    _sessions[session_id] = (
        result_messages
    )

    reply = content_to_text(
        result["messages"][-1].content
    )

    return {
        "reply": (
            reply
            or "Agent没有返回文本内容。"
        ),
        "trace": extract_trace(
            result["messages"]
        ),
    }


def clear_session(
    session_id: str,
) -> None:
    _sessions.pop(
        session_id,
        None,
    )