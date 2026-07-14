import requests
import os
import sys
import logging
import subprocess

logger = logging.getLogger(__name__)

VERSION_URL = "https://raw.githubusercontent.com/zdebryrp/TelegramAutoposter/main/version.txt"
RAW_CODE_URL = "https://raw.githubusercontent.com/zdebryrp/TelegramAutoposter/main/main.py"


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
    try:
        response = requests.get(RAW_CODE_URL, timeout=10)
        if response.status_code == 200:
            with open("main.py", "wb") as f:
                f.write(response.content)
            logger.info("Код обновлен")
            return True
        return False
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
        return {"error": "Ошибка обновления"}
