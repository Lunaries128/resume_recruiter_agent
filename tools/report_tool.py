import json
import uuid
from langchain_core.tools import tool
from database import list_candidates, current_session, HISTORY_DIR, get_session


@tool
def generate_candidate_report() -> str:
    """生成当前会话候选人的岗位匹配报告，不接受其他会话的候选人数据。"""
    sid = current_session()
    candidates = list_candidates()
    folder = HISTORY_DIR / sid / 'reports'
    folder.mkdir(parents=True, exist_ok=True)
    filename = uuid.uuid4().hex + '.md'
    lines = ['# 候选人匹配报告', '', '## 招聘要求', get_session(sid)['jd'], '']
    for candidate in candidates:
        lines += ['## ' + candidate['candidate_code'],
                  '匹配分：' + str(candidate.get('match_score') if candidate.get('match_score') is not None else '未评分')]
        lines += candidate.get('score_detail', {}).get('audit_log', [])
        lines.append('')
    (folder / filename).write_text('\n\n'.join(lines), encoding='utf-8')
    return json.dumps({'success': True, 'filename': filename,
                      'message': '报告已保存，可从本会话招聘要求页面下载。',
                      'candidates': [{'candidate_code': c['candidate_code'], 'match_score': c['match_score']}
                                     for c in candidates]}, ensure_ascii=False)
