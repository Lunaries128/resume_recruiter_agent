import os

from langchain_openai import (
    ChatOpenAI,
    OpenAIEmbeddings,
)

from config import (
    EMBEDDING_MODEL,
    MODEL_NAME,
)


llm = ChatOpenAI(
    model=MODEL_NAME,
    temperature=0,
    api_key=os.getenv(
        "OPENAI_API_KEY"
    ),
    base_url=os.getenv(
        "OPENAI_BASE_URL"
    ),
)


embeddings = OpenAIEmbeddings(
    model=EMBEDDING_MODEL,
    api_key=os.getenv(
        "OPENAI_API_KEY"
    ),
    base_url=os.getenv(
        "OPENAI_BASE_URL"
    ),
)