from datetime import datetime, timezone

from langchain_chroma import Chroma
from langchain_core.documents import (
    Document,
)

from config import CHROMA_DIR
from llm import embeddings


vector_store = Chroma(
    collection_name="hr_preferences",
    embedding_function=embeddings,
    persist_directory=str(CHROMA_DIR),
)


def save_preference(
    hr_id: str,
    preference: str,
):
    """
    只保存HR明确要求记住的岗位相关偏好。
    不允许保存敏感筛选条件。
    """

    from guardrails import (
        validate_filter_request,
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

    vector_store.add_documents([
        document
    ])

    return {
        "success": True,
        "message": "偏好已保存",
    }


def retrieve_preferences(
    hr_id: str,
    query: str,
    top_k: int = 4,
) -> list[str]:
    results = (
        vector_store.similarity_search(
            query=query,
            k=top_k,
            filter={
                "hr_id": hr_id
            },
        )
    )

    return [
        document.page_content
        for document in results
    ]