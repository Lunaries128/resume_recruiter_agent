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
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import database as db
from schemas import (
    CandidateProfile,
    ChatRequest,
    DeleteConfirmRequest,
    DeleteRequest,
    ScoreAllRequest,
    SessionEdit,
)


@asynccontextmanager
async def lifespan(app):
    db.recover_interrupted_uploads()
    yield


app = FastAPI(
    title="本地会话隔离招聘助理",
    lifespan=lifespan,
)

_locks = defaultdict(threading.RLock)
logger = logging.getLogger(__name__)

PARSE_TIMEOUT_SECONDS = 60


@app.exception_handler(ValueError)
async def invalid_request(request, exc):
    from fastapi.responses import JSONResponse

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
        "version": "local-sessions-v1",
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
    with _locks[sid]:
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


def process_upload(sid, uid):
    record = db.get_upload(sid, uid)
    db.claim_upload(sid, uid)
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
            CandidateProfile
            .model_validate(result["profile"])
            .model_dump()
        )

        db.finish_upload(
            sid,
            uid,
            profile=profile,
            confidence=max(
                0,
                min(
                    100,
                    float(
                        result.get("confidence", 0)
                    ),
                ),
            ),
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
                f"解析及结构化抽取超过"
                f"{PARSE_TIMEOUT_SECONDS}秒，"
                "已终止，可查看原文件后重试。"
            ),
        )

    except Exception as exc:
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
            error=f"{type(exc).__name__}: {exc}",
        )

    return db.get_upload(sid, uid)


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
            "上传失败：文件超过10MB，未保存原文件。"
        )

    if not content:
        raise ValueError(
            "上传失败：空文件，未保存原文件。"
        )

    uid = uuid.uuid4().hex
    suffix = Path(filename).suffix.lower()

    folder = (
        db.HISTORY_DIR
        / sid
        / "uploads"
    )
    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = folder / (uid + suffix)
    path.write_bytes(content)

    try:
        db.add_upload(
            sid,
            filename,
            path,
            uid,
        )
    except Exception:
        path.unlink(missing_ok=True)
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
        return db.get_upload(sid, uid)

    return process_upload(sid, uid)


@app.post("/sessions/{sid}/uploads/{uid}/retry")
def retry_upload(
    sid: str,
    uid: str,
):
    return process_upload(sid, uid)


@app.get("/sessions/{sid}/uploads/{uid}/original")
def original(
    sid: str,
    uid: str,
):
    record = db.get_upload(sid, uid)
    path = Path(
        record["stored_path"]
    ).resolve()

    folder = (
        db.HISTORY_DIR
        / sid
        / "uploads"
    ).resolve()

    if (
        path.parent != folder
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
    with db.session_scope(sid):
        return {
            "candidates": db.list_candidates(),
        }


@app.post("/sessions/{sid}/score")
def score(
    sid: str,
    request: ScoreAllRequest,
):
    from services.jd_service import (
        extract_jd_requirements,
    )
    from tools.score_tool import score_candidate

    with _locks[sid], db.session_scope(sid):
        records = db.list_candidates()

        if not records:
            raise ValueError(
                "当前会话尚无成功入库的候选人。"
            )

        db.update_session(
            sid,
            jd=request.jd,
        )

        try:
            requirements = (
                extract_jd_requirements(
                    request.jd
                )
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=(
                    "岗位要求解析失败："
                    f"{type(exc).__name__}: {exc}"
                ),
            ) from exc

        results = []

        for candidate in records:
            code = candidate["candidate_code"]

            try:
                results.append(
                    {
                        "success": True,
                        "candidate_code": code,
                        "result": score_candidate(
                            code,
                            requirements,
                        ),
                    }
                )
            except Exception as exc:
                logger.exception(
                    "候选人评分失败"
                )
                results.append(
                    {
                        "success": False,
                        "candidate_code": code,
                        "error": (
                            f"{type(exc).__name__}: {exc}"
                        ),
                    }
                )

        return {
            "results": results,
            "requirements": requirements,
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
                for path in folder.glob("*.md")
            )
            if folder.exists()
            else []
        ),
    }


@app.get("/sessions/{sid}/reports/{filename}")
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
        or path.suffix not in (".md", ".pdf")
        or not path.is_file()
    ):
        raise HTTPException(
            status_code=404,
            detail="报告不存在。",
        )

    return FileResponse(
        path,
        filename=filename,
        media_type=(
            "application/pdf"
            if path.suffix == ".pdf"
            else "text/markdown"
        ),
    )


@app.post("/sessions/{sid}/chat")
def chat(
    sid: str,
    request: ChatRequest,
):
    import agent

    with _locks[sid], db.session_scope(sid):
        return agent.chat(
            sid,
            request.hr_id,
            request.message,
            db.get_session(sid)["jd"],
        )


@app.post("/sessions/{sid}/clear")
def clear_chat(sid: str):
    with _locks[sid]:
        db.clear_messages(sid)

    return {
        "success": True,
    }


@app.post("/sessions/{sid}/delete/request")
def delete_request(
    sid: str,
    request: DeleteRequest,
):
    return db.create_delete_request(
        sid,
        request.kind,
        request.targets,
    )


@app.post("/sessions/{sid}/delete/confirm")
def delete_confirm(
    sid: str,
    request: DeleteConfirmRequest,
):
    if not request.confirmed:
        raise ValueError(
            "尚未确认删除。"
        )

    with _locks[sid]:
        result = db.confirm_delete(
            sid,
            request.token,
        )

        # 删除数据后，清理当前会话PDF及预览快照。
        folder = (
            db.HISTORY_DIR
            / sid
            / "reports"
        ).resolve()

        for pattern in (
            "*.pdf",
            "*.json",
        ):
            for path in folder.glob(pattern):
                if (
                    path.resolve().parent == folder
                    and path.is_file()
                ):
                    try:
                        path.unlink()
                    except OSError:
                        result["warnings"].append(
                            "有PDF报告被占用，"
                            "请稍后手动清理。"
                        )

        return result


class ReportRequest(BaseModel):
    candidate_codes: list[str] = Field(
        default_factory=list
    )


@app.post("/sessions/{sid}/screening-reports")
def create_screening_report(
    sid: str,
    request: ReportRequest,
):
    from report_service import (
        build_report,
        pdf_bytes,
    )

    with _locks[sid], db.session_scope(sid):
        session = db.get_session(sid)
        records = db.list_candidates()

        selected = set(
            request.candidate_codes
        )

        available = {
            item["candidate_code"]
            for item in records
        }

        if selected - available:
            raise ValueError(
                "所选候选人不存在或不属于当前会话。"
            )

        if selected:
            records = [
                item
                for item in records
                if item["candidate_code"] in selected
            ]

        report = build_report(
            session,
            records,
        )

        content = pdf_bytes(report)

        folder = (
            db.HISTORY_DIR
            / sid
            / "reports"
        )
        folder.mkdir(
            parents=True,
            exist_ok=True,
        )

        filename = report["id"] + ".pdf"
        pdf_path = folder / filename
        json_path = folder / (
            report["id"] + ".json"
        )

        try:
            pdf_path.write_bytes(content)

            json_path.write_text(
                json.dumps(
                    report,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except Exception:
            pdf_path.unlink(missing_ok=True)
            json_path.unlink(missing_ok=True)
            raise

        return {
            "filename": filename,
        }


@app.get("/sessions/{sid}/screening-reports")
def screening_reports(sid: str):
    db.get_session(sid)

    folder = (
        db.HISTORY_DIR
        / sid
        / "reports"
    )

    return {
        "reports": sorted(
            path.name
            for path in folder.glob("*.pdf")
            if path.with_suffix(".json").is_file()
        ),
    }


@app.get("/sessions/{sid}/screening-reports/{filename}")
def screening_report_preview(
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
        or path.suffix != ".pdf"
        or not path.is_file()
    ):
        raise HTTPException(
            status_code=404,
            detail=(
                "报告不存在或已随候选人数据删除。"
            ),
        )

    snapshot = path.with_suffix(".json")

    if not snapshot.is_file():
        raise HTTPException(
            status_code=404,
            detail="报告预览不存在。",
        )

    return json.loads(
        snapshot.read_text(
            encoding="utf-8"
        )
    )