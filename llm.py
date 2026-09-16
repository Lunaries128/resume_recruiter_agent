import hashlib
import json
import os
import threading
from pathlib import Path

from langchain_core.embeddings import Embeddings
from langchain_openai import ChatOpenAI

# 导入config时，会执行你原来的load_dotenv()。
from config import BASE_DIR, MODEL_NAME


def required_env(name):
    value = os.getenv(name, "").strip()

    if not value:
        raise ValueError(
            f"缺少配置：{name}，"
            "请检查项目根目录的 .env 文件。"
        )

    return value


# 聊天、简历信息提取、Agent工具调用：
# 仍然使用SenseNova在线API。
llm = ChatOpenAI(
    model=MODEL_NAME,
    api_key=required_env("OPENAI_API_KEY"),
    base_url=required_env("OPENAI_API_BASE"),
    temperature=0,
    timeout=50,
    max_retries=0,
)


class LocalEmbeddings(Embeddings):
    """
    与LangChain/Chroma兼容的本地向量模型。

    首次真正使用时加载模型。
    向量计算在本机完成，不调用在线Embeddings接口。
    """

    def __init__(self):
        self.model_name = os.getenv(
            "EMBEDDING_MODEL",
            "BAAI/bge-small-zh-v1.5",
        ).strip()

        self.device = os.getenv(
            "EMBEDDING_DEVICE",
            "cpu",
        ).strip()

        self.cache_dir = (
            Path(BASE_DIR)
            / "model_cache"
        )

        # BGE中文检索使用的查询前缀。
        # 文档本身不添加此前缀。
        self.query_instruction = (
            "为这个句子生成表示以用于检索相关文章："
        )

        self._model = None

        self._load_lock = threading.Lock()
        self._encode_lock = threading.Lock()

        # 不同模型和编码规则使用不同的索引目录，
        # 防止与旧向量混用。
        identity = {
            "model": self.model_name,
            "query_instruction": self.query_instruction,
            "normalize_embeddings": True,
            "version": "local-bge-v1",
        }

        self.index_tag = hashlib.sha256(
            json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:16]

    def _get_model(self):
        if self._model is not None:
            return self._model

        with self._load_lock:
            if self._model is not None:
                return self._model

            try:
                from sentence_transformers import (
                    SentenceTransformer,
                )
            except ImportError as exc:
                raise RuntimeError(
                    "缺少本地向量模型依赖，请执行："
                    "python -m pip install "
                    "sentence-transformers"
                ) from exc

            self.cache_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            try:
                self._model = SentenceTransformer(
                    self.model_name,
                    device=self.device,
                    cache_folder=str(self.cache_dir),
                    trust_remote_code=False,
                )

            except Exception as exc:
                raise RuntimeError(
                    "本地向量模型加载失败。"
                    "首次使用需要下载完整模型，"
                    "请检查网络、磁盘空间以及"
                    "EMBEDDING_MODEL配置。"
                    f"错误类型：{type(exc).__name__}"
                ) from exc

        return self._model

    def _encode(self, texts):
        if not texts:
            return []

        model = self._get_model()

        # 避免多个请求同时使用CPU模型造成资源争抢。
        with self._encode_lock:
            vectors = model.encode(
                texts,
                batch_size=16,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )

        return vectors.tolist()

    def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        return self._encode(
            [str(text) for text in texts]
        )

    def embed_query(
        self,
        text: str,
    ) -> list[float]:
        query = (
            self.query_instruction
            + str(text)
        )

        return self._encode([query])[0]


# 保留原来的变量名，
# 因而其他文件仍然可以：
# from llm import embeddings
embeddings = LocalEmbeddings()