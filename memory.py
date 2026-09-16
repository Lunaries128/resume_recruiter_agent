import threading

from datetime import datetime, timezone

from langchain_chroma import Chroma
from langchain_core.documents import Document

from config import CHROMA_DIR
from llm import embeddings


# 与旧的在线向量模型索引分开保存。
LOCAL_MEMORY_DIR = (
    CHROMA_DIR
    / f"local_{embeddings.index_tag}"
)

_vector_store = None
_store_lock = threading.Lock()


def get_vector_store():
    global _vector_store

    if _vector_store is not None:
        return _vector_store

    with _store_lock:
        if _vector_store is not None:
            return _vector_store

        LOCAL_MEMORY_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        _vector_store = Chroma(
            collection_name="hr_preferences",
            embedding_function=embeddings,
            persist_directory=str(
                LOCAL_MEMORY_DIR
            ),
        )

    return _vector_store


def save_preference(
    hr_id: str,
    preference: str,
):
    """
    保存HR明确提出的岗位相关偏好。

    先执行原有安全校验，
    再用本地模型计算向量并保存。
    """
    from guardrails import (
        validate_filter_request,
    )

    hr_id = str(hr_id).strip()
    preference = str(preference).strip()

    if not hr_id:
        raise ValueError(
            "缺少HR标识，不能保存偏好。"
        )

    if not preference:
        raise ValueError(
            "偏好内容不能为空。"
        )

    validate_filter_request(
        preference
    )

    document = Document(
        page_content=preference,
        metadata={
            "hr_id": hr_id,
            "created_at": datetime.now(
                timezone.utc
            ).isoformat(),
        },
    )

    get_vector_store().add_documents(
        [document]
    )

    return {
        "success": True,
        "message": "偏好已保存至本地长期记忆。",
    }


def retrieve_preferences(
    hr_id: str,
    query: str,
    top_k: int = 4,
) -> list[str]:
    """
    按HR标识隔离检索偏好。
    向量计算使用本地模型。
    """
    hr_id = str(hr_id).strip()
    query = str(query).strip()

    if not hr_id:
        raise ValueError(
            "缺少HR标识，不能读取偏好。"
        )

    if not query or top_k <= 0:
        return []

    results = (
        get_vector_store()
        .similarity_search(
            query=query,
            k=min(int(top_k), 20),
            filter={
                "hr_id": hr_id,
            },
        )
    )

    return [
        document.page_content
        for document in results
    ]