import os
from dotenv import load_dotenv
load_dotenv()

TOKEN = os.getenv("TOKEN", "").strip()
if not TOKEN:
    raise ValueError("Токен бота не найден. Установите TOKEN в файле .env (см. .env.example)")

ADMINS_RAW = os.getenv("ADMINS", "8295697775").strip()
try:
    ADMINS = [int(x.strip()) for x in ADMINS_RAW.split(",") if x.strip()]
    if not ADMINS:
        raise ValueError("empty")
except ValueError:
    raise ValueError("ADMINS должен содержать числовые ID через запятую, например: 123,456")

DIR = (os.getenv("DIR", "") or "").strip()

API_ID_RAW = (os.getenv("API_ID", "") or "").strip()
if not API_ID_RAW:
    raise ValueError("API_ID не найден. Установите API_ID в файле .env")
try:
    API_ID = int(API_ID_RAW)
except ValueError:
    raise ValueError("API_ID должен быть числом")

API_HASH = (os.getenv("API_HASH", "") or "").strip()
if not API_HASH:
    raise ValueError("API_HASH не найден. Установите API_HASH в файле .env")
