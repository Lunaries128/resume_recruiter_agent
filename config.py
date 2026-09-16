import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"
REPORT_DIR = BASE_DIR / "reports"
CHROMA_DIR = BASE_DIR / "chroma_hr_memory"

for directory in [
    DATA_DIR,
    UPLOAD_DIR,
    REPORT_DIR,
    CHROMA_DIR,
]:
    directory.mkdir(
        parents=True,
        exist_ok=True,
    )


DATABASE_PATH = DATA_DIR / "recruitment.db"

MODEL_NAME = os.getenv(
    "MODEL_NAME",
    "sensenova-6.8-flash-lite",
)

EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "sensenova/piccolo-large-zh-v2",
)

API_URL = os.getenv(
    "API_URL",
    "http://127.0.0.1:8000",
)