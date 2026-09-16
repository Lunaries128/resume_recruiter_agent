import os

from dotenv import load_dotenv
from langchain_openai import (
    ChatOpenAI,
    OpenAIEmbeddings,
)


load_dotenv()


llm = ChatOpenAI(
    model=os.getenv(
        "CHAT_MODEL",
        "qwen-plus",
    ),
    api_key=os.getenv(
        "OPENAI_API_KEY"
    ),
    base_url=os.getenv(
        "OPENAI_API_BASE"
    ),
    temperature=0,
)


embeddings = OpenAIEmbeddings(
    model=os.getenv(
        "EMBEDDING_MODEL",
        "text-embedding-v4",
    ),
    api_key=os.getenv(
        "OPENAI_API_KEY"
    ),
    base_url=os.getenv(
        "OPENAI_API_BASE"
    ),
)