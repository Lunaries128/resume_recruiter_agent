import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings


load_dotenv()

llm = ChatOpenAI(
    model=(
        os.getenv("MODEL_NAME")
        or os.getenv("CHAT_MODEL", "qwen-plus")
    ),
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_API_BASE"),
    temperature=0,
    timeout=90,
    max_retries=0,
    extra_body={
        "enable_search": False,
        "enable_thinking": False,
    },
)

# 保留兼容接口。新版长期偏好使用SQLite，
# 不依赖embedding服务才能保存或读取。
embeddings = OpenAIEmbeddings(
    model=os.getenv(
        "EMBEDDING_MODEL",
        "text-embedding-v4",
    ),
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_API_BASE"),
    check_embedding_ctx_length=False,
    chunk_size=10,
)