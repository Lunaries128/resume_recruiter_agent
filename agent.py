import json
from database import add_message, list_messages


def chat(session_id, hr_id, message, jd):
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
    from guardrails import validate_filter_request, validate_privacy_request, safe_output_text
    from llm import llm
    from tools.candidate_sql_tool import query_candidates
    from tools.score_tool import calculate_match_score
    from tools.report_tool import generate_candidate_report
    validate_filter_request(message)
    validate_privacy_request(message)
    validate_filter_request(jd)
    history = [HumanMessage(content=x['content']) if x['role'] == 'user' else AIMessage(content=x['content'])
               for x in list_messages(session_id)[-12:]]
    tools = [query_candidates, calculate_match_score, generate_candidate_report]
    # 保留原有HR长期偏好能力；向量库暂不可用时仍可正常聊天。
    preferences = []
    try:
        from memory import retrieve_preferences
        from tools.preference_tool import remember_hr_preference
        tools.append(remember_hr_preference)
        preferences = retrieve_preferences(hr_id, message + '\n' + jd)
    except Exception:
        pass
    prompt = f'''你是招聘助理。只查询工具提供的当前会话数据。
不得凭空编造候选人、经历和评分。评分只能调用工具，不能自动决定录用淘汰。
只使用岗位相关条件，不使用敏感属性。缺失信息不等于不具备能力。
解释简历证据和计算结果，不输出隐藏思维链。
用户明确要求记住偏好时才保存；HR编号：{hr_id}。
岗位要求：{jd or '未设置'}。
已有岗位偏好：{json.dumps(preferences, ensure_ascii=False)}。
SQL安全视图字段：candidate_code,education_json,skills_json,work_years,
projects_json,experiences_json,awards_json,summary,match_score。
'''
    runner = create_agent(model=llm, tools=tools, system_prompt=prompt)
    result = runner.invoke({'messages': history + [HumanMessage(content=message)]},
                           config={'recursion_limit': 20})
    all_messages = result['messages']
    content = all_messages[-1].content
    if isinstance(content, list):
        content = ''.join(block.get('text', '') if isinstance(block, dict) else str(block) for block in content)
    reply = safe_output_text(str(content))
    trace = [{'tool': getattr(x, 'name', '') or '工具', 'result': safe_output_text(str(x.content))[:1500]}
             for x in all_messages[len(history)+1:] if isinstance(x, ToolMessage)]
    add_message(session_id, 'user', message)
    add_message(session_id, 'assistant', reply, trace)
    return {'reply': reply, 'trace': trace}
