import json
import logging
import subprocess
import sys
import threading
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    UploadFile,
)
from fastapi.responses import (
    FileResponse,
    JSONResponse,
)

import database as db
import job_service as jobs

from schemas import (
    CandidateProfile,
    ChatRequest,
    DeleteConfirmRequest,
    DeleteRequest,
    ScoreAllRequest,
    SessionEdit,
)


PARSE_TIMEOUT_SECONDS = 60

locks = defaultdict(threading.RLock)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    db.recover_interrupted_uploads()
    yield


app = FastAPI(
    title="本地会话隔离招聘助理",
    lifespan=lifespan,
)


@app.exception_handler(ValueError)
async def invalid_request(
    request,
    exc,
):
    return JSONResponse(
        status_code=400,
        content={
            "detail": str(exc),
        },
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "job-templates-v1",
    }


@app.get("/sessions")
def sessions():
    return {
        "sessions": db.list_sessions(),
    }


@app.post("/sessions")
def create_session():
    return db.create_session()


@app.get("/sessions/{sid}")
def session_detail(sid: str):
    return db.get_session(sid)


@app.patch("/sessions/{sid}")
def edit_session(
    sid: str,
    request: SessionEdit,
):
    with locks[sid]:
        return db.update_session(
            sid,
            **request.model_dump(
                exclude_none=True
            ),
        )


@app.get("/sessions/{sid}/uploads")
def uploads(sid: str):
    return {
        "uploads": db.list_uploads(sid),
    }


def process_upload(
    sid: str,
    uid: str,
):
    record = db.get_upload(
        sid,
        uid,
    )

    db.claim_upload(
        sid,
        uid,
    )

    process = None

    try:
        process = subprocess.Popen(
            [
                sys.executable,
                str(
                    Path(__file__).with_name(
                        "parse_worker.py"
                    )
                ),
                record["stored_path"],
            ],
            cwd=Path(__file__).parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            ),
        )

        output, _ = process.communicate(
            timeout=PARSE_TIMEOUT_SECONDS
        )

        if process.returncode != 0:
            raise ValueError(
                "解析进程异常退出，"
                "请检查OCR依赖和模型配置。"
            )

        result = json.loads(
            output.decode("utf-8")
        )

        if not result.get("success"):
            raise ValueError(
                result.get(
                    "error",
                    "格式无法解析",
                )
            )

        profile = (
            CandidateProfile.model_validate(
                result["profile"]
            ).model_dump()
        )

        confidence = max(
            0,
            min(
                100,
                float(
                    result.get(
                        "confidence",
                        0,
                    )
                ),
            ),
        )

        db.finish_upload(
            sid,
            uid,
            profile=profile,
            confidence=confidence,
            metadata=result.get(
                "metadata",
                {},
            ),
        )

    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()

        db.finish_upload(
            sid,
            uid,
            status="解析超时",
            error=(
                "解析及结构化抽取超过60秒，"
                "已终止，可查看原文件后重试。"
            ),
        )

    except Exception as error:
        if (
            process is not None
            and process.poll() is None
        ):
            process.kill()
            process.communicate()

        db.finish_upload(
            sid,
            uid,
            status="解析或入库失败",
            error=(
                f"{type(error).__name__}: "
                f"{error}"
            ),
        )

    return db.get_upload(
        sid,
        uid,
    )


@app.post("/sessions/{sid}/uploads")
def upload(
    sid: str,
    file: UploadFile = File(...),
):
    db.get_session(sid)

    filename = Path(
        (file.filename or "简历").replace(
            "\\",
            "/",
        )
    ).name

    content = file.file.read(
        10 * 1024 * 1024 + 1
    )

    if len(content) > 10 * 1024 * 1024:
        raise ValueError(
            "上传失败：文件超过10MB，"
            "未保存原文件。"
        )

    if not content:
        raise ValueError(
            "上传失败：空文件，"
            "未保存原文件。"
        )

    uid = uuid.uuid4().hex

    suffix = Path(
        filename
    ).suffix.lower()

    folder = (
        db.HISTORY_DIR
        / sid
        / "uploads"
    )

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = folder / (
        uid + suffix
    )

    path.write_bytes(content)

    try:
        db.add_upload(
            sid,
            filename,
            path,
            uid,
        )

    except Exception:
        path.unlink(
            missing_ok=True
        )
        raise

    if suffix not in (
        ".pdf",
        ".docx",
        ".txt",
    ):
        db.finish_upload(
            sid,
            uid,
            status="格式无法解析",
            error=(
                "只支持PDF、DOCX和TXT；"
                "原文件已保留。"
            ),
        )

        return db.get_upload(
            sid,
            uid,
        )

    return process_upload(
        sid,
        uid,
    )


@app.post(
    "/sessions/{sid}/uploads/{uid}/retry"
)
def retry_upload(
    sid: str,
    uid: str,
):
    return process_upload(
        sid,
        uid,
    )


@app.get(
    "/sessions/{sid}/uploads/{uid}/original"
)
def original(
    sid: str,
    uid: str,
):
    record = db.get_upload(
        sid,
        uid,
    )

    path = Path(
        record["stored_path"]
    ).resolve()

    expected_folder = (
        db.HISTORY_DIR
        / sid
        / "uploads"
    ).resolve()

    if (
        path.parent != expected_folder
        or not path.is_file()
    ):
        raise HTTPException(
            status_code=404,
            detail="原文件不存在。",
        )

    return FileResponse(
        path,
        filename=record["filename"],
        content_disposition_type="inline",
    )


@app.get("/sessions/{sid}/candidates")
def candidates(sid: str):
    with locks[sid]:
        return {
            "candidates": (
                jobs.ranked_candidates(sid)
            ),
        }


@app.post("/sessions/{sid}/score")
def legacy_score(
    sid: str,
    request: ScoreAllRequest,
):
    raise HTTPException(
        status_code=400,
        detail=(
            "请先解析并保存岗位模板，"
            "再在候选人信息页选择岗位。"
        ),
    )


@app.get("/sessions/{sid}/jobs")
def list_jobs(sid: str):
    return jobs.templates(sid)


@app.post("/sessions/{sid}/jobs/parse")
def parse_job(
    sid: str,
    request: ScoreAllRequest,
):
    db.get_session(sid)

    try:
        return jobs.parse_jd(
            request.jd
        )

    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "岗位解析失败，"
                "可直接人工填写条件："
                f"{error}"
            ),
        ) from error


@app.post("/sessions/{sid}/jobs")
def create_job(
    sid: str,
    request: jobs.Job,
):
    with locks[sid]:
        return jobs.save_job(
            sid,
            request.model_dump(),
        )


@app.put("/sessions/{sid}/jobs/{jid}")
def update_job(
    sid: str,
    jid: str,
    request: jobs.Job,
):
    with locks[sid]:
        return jobs.save_job(
            sid,
            request.model_dump(),
            jid,
        )


@app.post(
    "/sessions/{sid}/jobs/{jid}/activate"
)
def select_job(
    sid: str,
    jid: str,
):
    with locks[sid]:
        jobs.activate(
            sid,
            jid,
        )

        return {
            "candidates": (
                jobs.ranked_candidates(sid)
            ),
        }


@app.delete(
    "/sessions/{sid}/jobs/{jid}"
)
def delete_job(
    sid: str,
    jid: str,
):
    with locks[sid]:
        jobs.delete_job(
            sid,
            jid,
        )

        return {
            "success": True,
        }


@app.get("/sessions/{sid}/messages")
def messages(sid: str):
    return {
        "messages": db.list_messages(sid),
    }


@app.get("/sessions/{sid}/reports")
def reports(sid: str):
    db.get_session(sid)

    folder = (
        db.HISTORY_DIR
        / sid
        / "reports"
    )

    return {
        "reports": (
            sorted(
                path.name
                for path in folder.glob(
                    "*.md"
                )
            )
            if folder.exists()
            else []
        ),
    }


@app.get(
    "/sessions/{sid}/reports/{filename}"
)
def report_file(
    sid: str,
    filename: str,
):
    db.get_session(sid)

    folder = (
        db.HISTORY_DIR
        / sid
        / "reports"
    ).resolve()

    path = (
        folder / filename
    ).resolve()

    if (
        path.parent != folder
        or path.suffix != ".md"
        or not path.is_file()
    ):
        raise HTTPException(
            status_code=404,
            detail="报告不存在。",
        )

    return FileResponse(
        path,
        filename=filename,
    )


@app.post("/sessions/{sid}/chat")
def chat(
    sid: str,
    request: ChatRequest,
):
    import agent

    with locks[sid], db.session_scope(sid):
        return agent.chat(
            sid,
            request.hr_id,
            request.message,
            db.get_session(sid)["jd"],
        )


@app.post("/sessions/{sid}/clear")
def clear_chat(sid: str):
    with locks[sid]:
        db.clear_messages(sid)

    return {
        "success": True,
    }


@app.post(
    "/sessions/{sid}/delete/request"
)
def delete_request(
    sid: str,
    request: DeleteRequest,
):
    return db.create_delete_request(
        sid,
        request.kind,
        request.targets,
    )


@app.post(
    "/sessions/{sid}/delete/confirm"
)
def delete_confirm(
    sid: str,
    request: DeleteConfirmRequest,
):
    if not request.confirmed:
        raise ValueError(
            "尚未确认删除。"
        )

    with locks[sid]:
        return db.confirm_delete(
            sid,
            request.token,
        )