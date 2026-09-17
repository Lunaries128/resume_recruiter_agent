import json
import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import File, UploadFile
from pydantic import BaseModel, Field

import database as db
import legacy_api as old
import hr_workflow as flow
from schemas import CandidateProfile, ScoreAllRequest


app = old.app
locks = old._locks
pool = ThreadPoolExecutor(max_workers=2)
queued = set()
queue_lock = threading.Lock()


def process(sid, uid):
    child = None

    try:
        record = db.get_upload(sid, uid)
        db.claim_upload(sid, uid)

        timeout = int(
            os.getenv('PARSE_TIMEOUT_SECONDS', '180')
        )

        child = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name('parse_worker.py')),
                record['stored_path'],
            ],
            cwd=Path(__file__).parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(
                subprocess,
                'CREATE_NO_WINDOW',
                0,
            ),
        )

        output, _ = child.communicate(timeout=timeout)

        if child.returncode:
            raise ValueError(
                '解析进程异常退出，请检查模型与OCR配置。'
            )

        result = json.loads(output.decode('utf-8'))

        if not result.get('success'):
            raise ValueError(
                result.get('error', '解析失败')
            )

        profile = CandidateProfile.model_validate(
            result['profile']
        ).model_dump()

        db.finish_upload(
            sid,
            uid,
            profile=profile,
            confidence=result.get('confidence', 0),
            metadata=result.get('metadata', {}),
        )

    except Exception as exc:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate()

        try:
            db.finish_upload(
                sid,
                uid,
                status='解析或入库失败',
                error=(
                    '解析超时，请重试。'
                    if isinstance(exc, subprocess.TimeoutExpired)
                    else str(exc)
                ),
            )
        except ValueError:
            pass

    finally:
        with queue_lock:
            queued.discard((sid, uid))


def enqueue(sid, uid):
    record = db.get_upload(sid, uid)

    if record['status'] == '成功入库':
        raise ValueError('已成功入库，无需重试。')

    with queue_lock:
        if (sid, uid) in queued:
            return

        queued.add((sid, uid))

    pool.submit(process, sid, uid)


@asynccontextmanager
async def lifespan(app):
    db.recover_interrupted_uploads()

    for session in db.list_sessions():
        for upload in db.list_uploads(session['id']):
            if upload['status'] == '等待解析':
                enqueue(session['id'], upload['id'])

    try:
        yield
    finally:
        pool.shutdown(wait=True)


app.router.lifespan_context = lifespan


class PrepareRequest(BaseModel):
    jd: str = Field(min_length=1, max_length=20000)
    hr_id: str = 'default_hr'
    scope: str = '通用'
    use_memory: bool = True


class ConfirmRequest(BaseModel):
    requirements: list[dict]
    dimension_weights: dict[str, float]


class PreferenceRequest(BaseModel):
    hr_id: str
    content: str = Field(min_length=1, max_length=1000)
    scope: str = '通用'
    enabled: bool = True


@app.post('/sessions/{sid}/uploads')
def upload(sid: str, file: UploadFile = File(...)):
    db.get_session(sid)

    name = Path(
        (file.filename or 'resume.txt').replace('\\', '/')
    ).name

    suffix = Path(name).suffix.lower()

    if suffix not in ('.pdf', '.docx', '.txt'):
        raise ValueError('只支持PDF、DOCX、TXT。')

    content = file.file.read(10 * 1024 * 1024 + 1)

    if not content or len(content) > 10 * 1024 * 1024:
        raise ValueError('文件为空或超过10MB。')

    uid = uuid.uuid4().hex
    folder = db.HISTORY_DIR / sid / 'uploads'
    folder.mkdir(parents=True, exist_ok=True)

    path = folder / (uid + suffix)
    path.write_bytes(content)

    try:
        db.add_upload(sid, name, path, uid)
    except Exception:
        path.unlink(missing_ok=True)
        raise

    enqueue(sid, uid)
    return db.get_upload(sid, uid)


@app.post('/sessions/{sid}/uploads/{uid}/retry')
def retry(sid: str, uid: str):
    enqueue(sid, uid)
    return db.get_upload(sid, uid)


@app.get('/sessions/{sid}/criteria')
def criteria(sid: str):
    return flow.get_criteria(sid)


@app.post('/sessions/{sid}/criteria/prepare')
def prepare(sid: str, body: PrepareRequest):
    with locks[sid]:
        return flow.prepare(
            sid,
            body.jd,
            body.hr_id,
            body.scope,
            body.use_memory,
        )


@app.post('/sessions/{sid}/criteria/confirm')
def confirm(sid: str, body: ConfirmRequest):
    with locks[sid]:
        return flow.confirm(
            sid,
            body.requirements,
            body.dimension_weights,
        )


@app.get('/sessions/{sid}/candidates')
def candidates(sid: str):
    with db.session_scope(sid):
        return {
            'candidates': flow.candidates(),
        }


@app.post('/sessions/{sid}/score')
def score(sid: str, body: ScoreAllRequest):
    with locks[sid], db.session_scope(sid):
        spec = flow.get_criteria(sid, True)['criteria']

        if body.jd.strip() != spec['jd_text']:
            raise ValueError('JD已修改，请重新确认标准。')

        results = []

        for item in db.list_candidates():
            code = item['candidate_code']

            try:
                results.append({
                    'success': True,
                    'candidate_code': code,
                    'result': flow.score_one(code),
                })
            except Exception as exc:
                results.append({
                    'success': False,
                    'candidate_code': code,
                    'error': str(exc),
                })

        return {
            'requirements': spec,
            'results': results,
        }


@app.post('/sessions/{sid}/screening-reports')
def report(sid: str, body: old.ReportRequest):
    with locks[sid], db.session_scope(sid):
        rows = flow.candidates()

        selected = [
            item
            for item in rows
            if not body.candidate_codes
            or item['candidate_code'] in body.candidate_codes
        ]

        if (
            not selected
            or any(
                item['match_score'] is None
                for item in selected
            )
        ):
            raise ValueError(
                '所选候选人尚未按当前确认版本完成评分。'
            )

        return old.create_screening_report(sid, body)


@app.get('/preferences')
def preferences(hr_id: str):
    return {
        'preferences': flow.preferences(hr_id),
    }


@app.post('/preferences')
def save_preference(body: PreferenceRequest):
    return flow.save_preference(
        body.hr_id,
        body.content,
        body.scope,
    )


@app.put('/preferences/{identifier}')
def edit_preference(
    identifier: str,
    body: PreferenceRequest,
):
    flow.edit_preference(
        body.hr_id,
        identifier,
        body.content,
        body.scope,
        body.enabled,
    )

    return {'success': True}


@app.delete('/preferences/{identifier}')
def delete_preference(
    identifier: str,
    hr_id: str,
    confirmed: bool = False,
):
    flow.delete_preference(
        hr_id,
        identifier,
        confirmed,
    )

    return {'success': True}