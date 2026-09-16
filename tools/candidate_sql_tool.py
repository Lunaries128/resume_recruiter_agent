import json
import sqlite3
from langchain_core.tools import tool
from database import list_candidates


@tool
def query_candidates(sql: str) -> str:
    """查询当前会话candidate_safe_view，字段candidate_code,education_json,skills_json,
    work_years,projects_json,experiences_json,awards_json,summary,match_score。只允许SELECT。"""
    if not sql.strip().lower().startswith('select '):
        raise ValueError('只允许SELECT查询。')
    # 在只装入当前会话数据的临时库查询，无法访问磁盘上的其他会话。
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('''CREATE TABLE candidate_safe_view(
            candidate_code TEXT, education_json TEXT, skills_json TEXT, work_years REAL,
            projects_json TEXT, experiences_json TEXT, awards_json TEXT, summary TEXT, match_score REAL)''')
        for item in list_candidates():
            connection.execute('INSERT INTO candidate_safe_view VALUES(?,?,?,?,?,?,?,?,?)', (
                item['candidate_code'], json.dumps(item.get('education', []), ensure_ascii=False),
                json.dumps(item.get('skills', []), ensure_ascii=False), item.get('work_years', 0),
                json.dumps(item.get('projects', []), ensure_ascii=False),
                json.dumps(item.get('experiences', []), ensure_ascii=False),
                json.dumps(item.get('awards', []), ensure_ascii=False),
                item.get('summary', ''), item.get('match_score')))
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
        connection.set_authorizer(lambda action, *args: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
        steps = [0]
        def limit():
            steps[0] += 1
            return int(steps[0] > 1000)
        connection.set_progress_handler(limit, 1000)
        rows = connection.execute(sql).fetchmany(100)
        return json.dumps([dict(row) for row in rows], ensure_ascii=False)
    finally:
        connection.close()
