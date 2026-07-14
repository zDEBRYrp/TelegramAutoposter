import os
from dotenv import load_dotenv

# Загрузить переменные из .env файла
load_dotenv()

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise ValueError("Токен бота не найден. Установите TOKEN в файле .env")

ADMINS = os.getenv("ADMINS", "8295697775")
try:
    ADMINS = [int(x.strip()) for x in ADMINS.split(",")]
except ValueError:
    raise ValueError("ADMINS должен содержать числовые ID через запятую")

DIR = os.getenv("DIR", "")

API_ID = os.getenv("API_ID")
if not API_ID:
    raise ValueError("API_ID не найден. Установите API_ID в файле .env")
try:
    API_ID = int(API_ID)
except ValueError:
    raise ValueError("API_ID должен быть числом")

API_HASH = os.getenv("API_HASH")
if not API_HASH:
    raise ValueError("API_HASH не найден. Установите API_HASH в файле .env")
