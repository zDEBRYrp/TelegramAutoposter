import requests
import os
import sys
import logging
import shutil

logger = logging.getLogger(__name__)

REPO = "zDEBRYrp/TelegramAutoposter"
BRANCH = "main"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/version.txt"
# Файлы, которые обновляем с GitHub (раньше обновлялся только main.py,
# из-за чего version.txt никогда не менялся и апдейт предлагался вечно)
UPDATE_FILES = ["main.py", "user.py", "sqliter.py", "config.py", "updater.py", "version.txt"]


def _raw_url(filename: str) -> str:
    return f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{filename}"


def get_current_version():
    try:
        with open("version.txt", "r") as f:
            version = f.read().strip()
        return version
    except Exception as e:
        logger.error(f"Ошибка чтения версии: {e}")
        return "unknown"


def get_latest_version():
    try:
        response = requests.get(VERSION_URL, timeout=10)
        if response.status_code == 200:
            return response.text.strip()
        return None
    except Exception as e:
        logger.error(f"Ошибка получения версии: {e}")
        return None


def update_code():
    """Скачать свежие файлы с GitHub с бэкапом старых (.bak)."""
    updated = []
    try:
        for filename in UPDATE_FILES:
            response = requests.get(_raw_url(filename), timeout=15)
            if response.status_code != 200:
                logger.error(f"Ошибка обновления {filename}: HTTP {response.status_code}")
                return False
            if os.path.exists(filename):
                shutil.copyfile(filename, filename + ".bak")
            mode = "wb"
            with open(filename, mode) as f:
                f.write(response.content)
            updated.append(filename)
        logger.info(f"Код обновлен: {updated}")
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления кода: {e}")
        return False


def check_update():
    current = get_current_version()
    latest = get_latest_version()
    
    if not latest:
        return {"error": "Не удалось получить последнюю версию"}
    
    if current != latest:
        return {
            "update_available": True,
            "current": current,
            "latest": latest
        }
    else:
        return {
            "update_available": False,
            "current": current,
            "latest": latest
        }


def run_update():
    check = check_update()

    if check.get("error"):
        return {"error": check["error"]}

    if not check.get("update_available"):
        return {"message": "Нет доступных обновлений"}

    logger.info(f"Доступно обновление {check['latest']} (сейчас {check['current']})")

    success = update_code()

    if success:
        return {
            "message": "Обновление завершено успешно",
            "new_version": get_current_version()
        }
    else:
        return {"error": "Ошибка обновления (старые файлы сохранены как .bak)"}
