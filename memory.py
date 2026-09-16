import os
import uuid

from langchain_chroma import Chroma
from langchain_core.documents import (
    Document,
)

from llm import embeddings


CHROMA_PATH = os.getenv(
    "CHROMA_PATH",
    "data/hr_memory",
)


vector_store = Chroma(
    collection_name="hr_preferences",
    embedding_function=embeddings,
    persist_directory=CHROMA_PATH,
)


def save_hr_preference(
    hr_id: str,
    preference: str,
) -> dict:
    """
    只保存HR明确要求记住的、
    与岗位相关的筛选偏好。
    """

    vector_store.add_documents([
        Document(
            page_content=preference,
            metadata={
                "hr_id": hr_id,
                "type": (
                    "screening_preference"
                ),
            },
        )
    ], ids=[str(uuid.uuid4())])

    return {
        "success": True,
        "message": "偏好已保存",
        "preference": preference,
    }


def search_hr_preferences(
    hr_id: str,
    query: str,
    k: int = 4,
) -> list[str]:
    results = (
        vector_store.similarity_search(
            query=query,
            k=k,
            filter={
                "hr_id": hr_id,
            },
        )
    )

    return [
        document.page_content
        for document in results
    ]