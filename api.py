import os
import secrets
import time
from pathlib import Path

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    UploadFile,
)
from fastapi.middleware.cors import (
    CORSMiddleware,
)
from fastapi.staticfiles import (
    StaticFiles,
)

import agent
from database import (
    delete_candidate,
    get_candidate,
    get_delete_request,
    list_candidates,
    save_delete_request,
)
from resume_service import (
    SUPPORTED_SUFFIXES,
    ingest_resume_file,
)
from schemas import (
    ChatRequest,
    DeleteConfirm,
    DeleteRequest,
)


UPLOAD_PATH = Path(
    os.getenv(
        "UPLOAD_PATH",
        "uploads",
    )
)

REPORT_PATH = Path(
    os.getenv(
        "REPORT_PATH",
        "reports",
    )
)

UPLOAD_PATH.mkdir(
    parents=True,
    exist_ok=True,
)

REPORT_PATH.mkdir(
    parents=True,
    exist_ok=True,
)


app = FastAPI(
    title="智能简历筛选与招聘助理Agent"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


app.mount(
    "/reports",
    StaticFiles(
        directory=str(REPORT_PATH)
    ),
    name="reports",
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": (
            "recruitment-agent"
        ),
    }


@app.post("/upload")
async def upload_resumes(
    files: list[UploadFile] = File(...),
):
    if len(files) > 30:
        raise HTTPException(
            status_code=400,
            detail="单次最多上传30份简历。",
        )

    results = []

    for uploaded_file in files:
        suffix = Path(
            uploaded_file.filename
        ).suffix.lower()

        if suffix not in SUPPORTED_SUFFIXES:
            results.append({
                "success": False,
                "source_file": (
                    uploaded_file.filename
                ),
                "error": (
                    "不支持的文件格式"
                ),
            })

            continue

        safe_name = (
            Path(uploaded_file.filename)
            .name
        )

        unique_name = (
            secrets.token_hex(6)
            + "_"
            + safe_name
        )

        saved_path = (
            UPLOAD_PATH / unique_name
        )

        content = await (
            uploaded_file.read()
        )

        if len(content) > 10 * 1024 * 1024:
            results.append({
                "success": False,
                "source_file": safe_name,
                "error": (
                    "单个文件不能超过10MB"
                ),
            })

            continue

        saved_path.write_bytes(content)

        try:
            result = ingest_resume_file(
                str(saved_path)
            )

            results.append(result)

        except Exception as error:
            results.append({
                "success": False,
                "source_file": safe_name,
                "error": str(error),
            })

    return {
        "total": len(files),
        "success_count": sum(
            1
            for item in results
            if item.get("success")
        ),
        "results": results,
    }


@app.post("/chat")
def chat_endpoint(
    request: ChatRequest,
):
    try:
        return agent.chat(
            session_id=(
                request.session_id
            ),
            hr_id=request.hr_id,
            message=request.message,
            jd=request.jd,
        )

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error


@app.get("/candidates")
def candidates_endpoint():
    candidates = list_candidates()

    # 不返回脱敏后的完整简历文本
    for candidate in candidates:
        candidate.pop(
            "redacted_text",
            None,
        )

    return {
        "count": len(candidates),
        "candidates": candidates,
    }


@app.get(
    "/candidates/{candidate_id}"
)
def candidate_endpoint(
    candidate_id: str,
):
    candidate = get_candidate(
        candidate_id
    )

    if not candidate:
        raise HTTPException(
            status_code=404,
            detail="候选人不存在",
        )

    candidate.pop(
        "redacted_text",
        None,
    )

    return candidate


@app.post("/delete/request")
def request_delete(
    request: DeleteRequest,
):
    candidate = get_candidate(
        request.candidate_id
    )

    if not candidate:
        raise HTTPException(
            status_code=404,
            detail="候选人不存在",
        )

    token = secrets.token_urlsafe(24)
    expires_at = time.time() + 300

    save_delete_request(
        token=token,
        candidate_id=(
            request.candidate_id
        ),
        expires_at=expires_at,
    )

    return {
        "requires_confirmation": True,
        "token": token,
        "candidate_id": (
            request.candidate_id
        ),
        "message": (
            "该操作会永久删除候选人。"
            "请在5分钟内人工确认。"
        ),
    }


@app.post("/delete/confirm")
def confirm_delete(
    request: DeleteConfirm,
):
    record = get_delete_request(
        request.token
    )

    if not record:
        raise HTTPException(
            status_code=404,
            detail="确认请求不存在",
        )

    if record["used"]:
        raise HTTPException(
            status_code=400,
            detail="确认请求已使用",
        )

    if record["expires_at"] < time.time():
        raise HTTPException(
            status_code=400,
            detail="确认请求已过期",
        )

    if not request.confirmed:
        return {
            "success": False,
            "message": "用户取消删除",
        }

    delete_candidate(
        candidate_id=(
            record["candidate_id"]
        ),
        token=request.token,
    )

    return {
        "success": True,
        "candidate_id": (
            record["candidate_id"]
        ),
        "message": "候选人已删除",
    }


@app.post(
    "/sessions/{session_id}/clear"
)
def clear_session_endpoint(
    session_id: str,
):
    agent.clear_session(session_id)

    return {
        "success": True,
    }