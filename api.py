import secrets
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
from config import (
    REPORT_DIR,
    UPLOAD_DIR,
)
from database import (
    confirm_delete_request,
    create_delete_request,
    get_candidate,
    list_candidates,
)
from schemas import (
    ChatRequest,
    DeleteConfirmRequest,
    DeleteRequest,
)
from tools.extract_tool import (
    ingest_resume,
)


SUPPORTED_SUFFIXES = {
    ".pdf",
    ".docx",
    ".txt",
}


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
        directory=str(REPORT_DIR)
    ),
    name="reports",
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "recruitment-agent",
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
        original_name = Path(
            uploaded_file.filename or ""
        ).name

        suffix = Path(
            original_name
        ).suffix.lower()

        if suffix not in SUPPORTED_SUFFIXES:
            results.append({
                "success": False,
                "source_filename": (
                    original_name
                ),
                "error": (
                    "只支持PDF、DOCX和TXT"
                ),
            })

            continue

        content = await uploaded_file.read()

        if len(content) > 10 * 1024 * 1024:
            results.append({
                "success": False,
                "source_filename": (
                    original_name
                ),
                "error": (
                    "单个文件不能超过10MB"
                ),
            })

            continue

        saved_name = (
            secrets.token_hex(6)
            + "_"
            + original_name
        )

        saved_path = (
            UPLOAD_DIR / saved_name
        )

        saved_path.write_bytes(content)

        try:
            profile = ingest_resume(
                file_path=str(saved_path),
                filename=original_name,
            )

            results.append({
                "success": True,
                "source_filename": (
                    original_name
                ),
                "candidate_code": profile[
                    "candidate_code"
                ],
                "profile": profile,
            })

        except Exception as error:
            results.append({
                "success": False,
                "source_filename": (
                    original_name
                ),
                "error": str(error),
            })

    return {
        "total": len(files),
        "success_count": sum(
            1
            for item in results
            if item["success"]
        ),
        "results": results,
    }


@app.post("/chat")
def chat_endpoint(
    request: ChatRequest,
):
    try:
        return agent.chat(
            session_id=request.session_id,
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
    return {
        "count": len(
            list_candidates()
        ),
        "candidates": list_candidates(),
    }


@app.get(
    "/candidates/{candidate_code}"
)
def candidate_endpoint(
    candidate_code: str,
):
    candidate = get_candidate(
        candidate_code
    )

    if not candidate:
        raise HTTPException(
            status_code=404,
            detail="候选人不存在。",
        )

    candidate.pop(
        "redacted_text",
        None,
    )

    return candidate


@app.post("/delete/request")
def delete_request_endpoint(
    request: DeleteRequest,
):
    try:
        result = create_delete_request(
            request.candidate_code
        )

    except ValueError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error

    return {
        "requires_confirmation": True,
        **result,
        "message": (
            "该操作会永久删除候选人数据。"
            "请在5分钟内人工确认。"
        ),
    }


@app.post("/delete/confirm")
def delete_confirm_endpoint(
    request: DeleteConfirmRequest,
):
    try:
        return confirm_delete_request(
            token=request.token,
            confirmed=request.confirmed,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error


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