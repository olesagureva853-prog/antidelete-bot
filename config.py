import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

load_dotenv(dotenv_path=ENV_FILE)

DATA_DIR = BASE_DIR / "data"
MEDIA_DIR = BASE_DIR / "media"
DB_PATH = DATA_DIR / "messages.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)


def str_to_bool(val: str, default: bool = True) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "y", "on")


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID_RAW = os.getenv("OWNER_ID", "").strip()
OWNER_ID = int(OWNER_ID_RAW) if OWNER_ID_RAW.isdigit() else None

SAVE_MEDIA = str_to_bool(os.getenv("SAVE_MEDIA"), default=True)
MAX_MEDIA_SIZE_MB = int(os.getenv("MAX_MEDIA_SIZE_MB", "20"))
MAX_MEDIA_SIZE_BYTES = MAX_MEDIA_SIZE_MB * 1024 * 1024
LOG_EDITED = str_to_bool(os.getenv("LOG_EDITED"), default=True)
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "7"))

# Настройки перехвата медиа по ответу (reply)
SAVE_MEDIA_ON_REPLY = str_to_bool(os.getenv("SAVE_MEDIA_ON_REPLY"), default=True)
REPLY_TRIGGER_KEYWORD = os.getenv("REPLY_TRIGGER_KEYWORD", "").strip()


def validate_config():
    if not BOT_TOKEN or BOT_TOKEN == "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz" or ":" not in BOT_TOKEN:
        raise ValueError(
            "Пожалуйста, укажите валидный BOT_TOKEN в файле .env!\n"
            "Инструкция: Получите токен у @BotFather в Telegram и вставьте его в .env"
        )
