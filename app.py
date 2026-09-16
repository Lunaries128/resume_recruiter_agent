"""本地会话数据库。所有候选人查询必须处于明确的会话作用域。"""
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
HISTORY_DIR = Path(os.getenv("RECRUITMENT_HISTORY_DIR", str(BASE_DIR / "history"))).resolve()
HISTORY_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = HISTORY_DIR / "sessions.db"
_session = ContextVar("recruitment_session", default=None)


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_connection():
    connection = sqlite3.connect(DATABASE_PATH, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_database():
    with get_connection() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, title TEXT NOT NULL,
            pinned INTEGER NOT NULL DEFAULT 0, jd TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS uploads (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            filename TEXT NOT NULL, stored_path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '等待解析', error TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0, metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL, UNIQUE(session_id,id)
        );
        CREATE TABLE IF NOT EXISTS candidates (
            candidate_code TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            upload_id TEXT NOT NULL UNIQUE,
            profile_json TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(session_id,candidate_code),
            FOREIGN KEY(session_id,upload_id) REFERENCES uploads(session_id,id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            candidate_code TEXT NOT NULL,
            jd_text TEXT NOT NULL, jd_hash TEXT NOT NULL,
            result_json TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY(session_id,candidate_code)
                REFERENCES candidates(session_id,candidate_code) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL, content TEXT NOT NULL,
            trace_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS delete_requests (
            token TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, targets_json TEXT NOT NULL,
            expires REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_upload_session ON uploads(session_id);
        CREATE INDEX IF NOT EXISTS idx_candidate_session ON candidates(session_id);
        CREATE INDEX IF NOT EXISTS idx_score_lookup ON scores(session_id,candidate_code,jd_text,id);
        ''')


def json_load(value, default=None):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return [] if default is None else default


def get_session(sid):
    with get_connection() as db:
        row = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if row is None:
        raise ValueError("会话不存在或已被删除。")
    return dict(row)


@contextmanager
def session_scope(sid):
    get_session(sid)
    token = _session.set(sid)
    try:
        yield
    finally:
        _session.reset(token)


def current_session():
    sid = _session.get()
    if sid is None:
        raise ValueError("缺少会话作用域，已拒绝查询共享候选人库。")
    return sid


def create_session(title="新招聘会话"):
    sid = uuid.uuid4().hex
    with get_connection() as db:
        db.execute("INSERT INTO sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)",
                   (sid, title.strip() or "新招聘会话", now(), now()))
    return get_session(sid)


def list_sessions():
    with get_connection() as db:
        rows = db.execute('''SELECT s.*,
          (SELECT COUNT(*) FROM uploads u WHERE u.session_id=s.id) AS upload_count,
          (SELECT COUNT(*) FROM candidates c WHERE c.session_id=s.id) AS candidate_count
          FROM sessions s ORDER BY pinned DESC,updated_at DESC''').fetchall()
    return [dict(row) for row in rows]


def update_session(sid, *, title=None, pinned=None, jd=None):
    get_session(sid)
    values = {}
    if title is not None:
        if not title.strip():
            raise ValueError("会话名称不能为空。")
        values["title"] = title.strip()[:80]
    if pinned is not None:
        values["pinned"] = int(pinned)
    if jd is not None:
        values["jd"] = jd
    if values:
        values["updated_at"] = now()
        with get_connection() as db:
            db.execute("UPDATE sessions SET " + ",".join(f"{key}=?" for key in values)
                       + " WHERE id=?", (*values.values(), sid))
    return get_session(sid)


def add_upload(sid, filename, stored_path, uid):
    get_session(sid)
    with get_connection() as db:
        db.execute("INSERT INTO uploads(id,session_id,filename,stored_path,created_at) VALUES(?,?,?,?,?)",
                   (uid, sid, filename, str(stored_path), now()))
    return get_upload(sid, uid)


def get_upload(sid, uid):
    with get_connection() as db:
        row = db.execute("SELECT * FROM uploads WHERE session_id=? AND id=?", (sid, uid)).fetchone()
    if row is None:
        raise ValueError("当前会话中不存在该上传记录。")
    data = dict(row)
    data["metadata"] = json_load(data["metadata"], {})
    return data


def list_uploads(sid):
    get_session(sid)
    with get_connection() as db:
        rows = db.execute('''SELECT u.*, c.candidate_code FROM uploads u
          LEFT JOIN candidates c ON c.session_id=u.session_id AND c.upload_id=u.id
          WHERE u.session_id=? ORDER BY u.created_at DESC''', (sid,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["metadata"] = json_load(item["metadata"], {})
        result.append(item)
    return result


def claim_upload(sid, uid):
    with get_connection() as db:
        cursor = db.execute("UPDATE uploads SET status='解析中',error='',confidence=0 "
                            "WHERE session_id=? AND id=? AND status NOT IN ('解析中','成功入库')", (sid, uid))
        if cursor.rowcount != 1:
            raise ValueError("该文件正在解析或已经成功入库，请勿重复提交。")


def recover_interrupted_uploads():
    # 本地部署采用一个后端进程。重启后旧请求已失效，允许用户重试原文件。
    with get_connection() as db:
        db.execute("UPDATE uploads SET status='解析中断',confidence=0,error='后端上次运行中断，请点击重试。' WHERE status='解析中'")


def finish_upload(sid, uid, *, profile=None, confidence=0, metadata=None, status="上传失败", error=""):
    with get_connection() as db:
        # 会话已删除时不允许后台任务将数据重新写回。
        row = db.execute("SELECT id FROM uploads WHERE session_id=? AND id=?", (sid, uid)).fetchone()
        if row is None:
            raise ValueError("上传记录已被删除，解析结果不再保存。")
        if profile is not None:
            code = f"CAND-{uid}"
            profile = dict(profile, candidate_code=code)
            db.execute("INSERT INTO candidates(candidate_code,session_id,upload_id,profile_json,created_at) "
                       "VALUES(?,?,?,?,?)", (code, sid, uid, json.dumps(profile, ensure_ascii=False), now()))
            status = "成功入库"
        db.execute("UPDATE uploads SET status=?,error=?,confidence=?,metadata=? WHERE session_id=? AND id=?",
                   (status, error, confidence if profile is not None else 0,
                    json.dumps(metadata or {}, ensure_ascii=False), sid, uid))


def get_candidate(candidate_code):
    sid = current_session()
    with get_connection() as db:
        row = db.execute('''SELECT c.*,u.filename,u.stored_path,u.confidence FROM candidates c
          JOIN uploads u ON u.id=c.upload_id AND u.session_id=c.session_id
          WHERE c.session_id=? AND c.candidate_code=?''', (sid, candidate_code)).fetchone()
    if row is None:
        return None
    result = json_load(row["profile_json"], {})
    result.update(candidate_code=row["candidate_code"], upload_id=row["upload_id"],
                  source_filename=row["filename"], parse_confidence=row["confidence"])
    return result


def list_candidates():
    sid = current_session()
    jd = get_session(sid)["jd"]
    with get_connection() as db:
        rows = db.execute('''SELECT c.*,u.filename,u.confidence,
          (SELECT result_json FROM scores s WHERE s.session_id=c.session_id
           AND s.candidate_code=c.candidate_code AND s.jd_text=? ORDER BY s.id DESC LIMIT 1) AS score_json
          FROM candidates c JOIN uploads u ON u.id=c.upload_id AND u.session_id=c.session_id
          WHERE c.session_id=? ORDER BY c.created_at DESC''', (jd, sid)).fetchall()
    result = []
    for row in rows:
        item = json_load(row["profile_json"], {})
        detail = json_load(row["score_json"], {})
        item.update(candidate_code=row["candidate_code"], upload_id=row["upload_id"],
                    source_filename=row["filename"], parse_confidence=row["confidence"],
                    score_detail=detail, match_score=detail.get("total_score"))
        result.append(item)
    result.sort(key=lambda x: (x["match_score"] is not None, x["match_score"] or 0), reverse=True)
    return result


def save_score(candidate_code, jd_hash, result):
    sid = current_session()
    if not get_candidate(candidate_code):
        raise ValueError("当前会话中不存在该候选人。")
    jd = get_session(sid)["jd"]
    with get_connection() as db:
        db.execute("INSERT INTO scores(session_id,candidate_code,jd_text,jd_hash,result_json,created_at) "
                   "VALUES(?,?,?,?,?,?)", (sid, candidate_code, jd, jd_hash,
                                          json.dumps(result, ensure_ascii=False), now()))


def list_messages(sid):
    get_session(sid)
    with get_connection() as db:
        rows = db.execute("SELECT role,content,trace_json FROM messages WHERE session_id=? ORDER BY id", (sid,)).fetchall()
    return [{"role": row["role"], "content": row["content"], "trace": json_load(row["trace_json"])} for row in rows]


def add_message(sid, role, content, trace=None):
    with get_connection() as db:
        db.execute("INSERT INTO messages(session_id,role,content,trace_json,created_at) VALUES(?,?,?,?,?)",
                   (sid, role, content, json.dumps(trace or [], ensure_ascii=False), now()))


def clear_messages(sid):
    get_session(sid)
    with get_connection() as db:
        db.execute("DELETE FROM messages WHERE session_id=?", (sid,))


def create_delete_request(sid, kind, targets):
    get_session(sid)
    if kind not in ("session", "uploads"):
        raise ValueError("删除类型无效。")
    targets = list(dict.fromkeys(targets)) if kind == "uploads" else []
    if kind == "uploads":
        if not targets:
            raise ValueError("请先选择记录。")
        for uid in targets:
            get_upload(sid, uid)
    token = secrets.token_urlsafe(24)
    with get_connection() as db:
        db.execute("INSERT INTO delete_requests VALUES(?,?,?,?,?)",
                   (token, sid, kind, json.dumps(targets), time.time()+300))
    return {"token": token, "kind": kind, "count": 1 if kind == "session" else len(targets)}


def confirm_delete(sid, token):
    with get_connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM delete_requests WHERE token=? AND session_id=?", (token, sid)).fetchone()
        if row is None or row["expires"] < time.time():
            raise ValueError("删除确认不存在、已使用或已过期，请重新申请。")
        kind = row["kind"]
        targets = json_load(row["targets_json"])
        if kind == "session":
            files = db.execute("SELECT stored_path FROM uploads WHERE session_id=?", (sid,)).fetchall()
            db.execute("DELETE FROM sessions WHERE id=?", (sid,))
        else:
            marks = ",".join("?" for _ in targets)
            args = (sid, *targets)
            files = db.execute(f"SELECT stored_path FROM uploads WHERE session_id=? AND id IN ({marks})", args).fetchall()
            db.execute(f"DELETE FROM uploads WHERE session_id=? AND id IN ({marks})", args)
            db.execute("DELETE FROM delete_requests WHERE token=?", (token,))
    # 仅删除当前会话的上传文件；不递归删除任何目录。
    root = (HISTORY_DIR / sid / "uploads").resolve()
    warnings = []
    for row in files:
        path = Path(row["stored_path"]).resolve()
        if path.parent != root:
            warnings.append("原文件路径异常，未删除该文件。")
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            warnings.append("有原文件正在被占用，数据库记录已删除，原文件需稍后手动清理。")
    if kind == "session":
        report_root = (HISTORY_DIR / sid / "reports").resolve()
        if report_root.parent == (HISTORY_DIR / sid).resolve():
            for report in report_root.glob("*.md"):
                if report.resolve().parent == report_root and report.is_file():
                    try:
                        report.unlink()
                    except OSError:
                        warnings.append("有报告被占用，请稍后手动清理。")
    return {"success": True, "kind": kind, "deleted_count": len(files), "warnings": warnings}


init_database()
