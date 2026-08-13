from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent

ENV_FILE = PROJECT_ROOT / ".env"
REQUEST_FILE = PROJECT_ROOT / "config" / "request.json"
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"


def load_environment() -> None:
    """Загрузить переменные окружения из .env."""
    load_dotenv(ENV_FILE)


def require_env(name: str) -> str:
    """Получить обязательную переменную окружения."""
    value = os.getenv(name)

    if value is None or not value.strip():
        raise RuntimeError(
            f"Не задана обязательная переменная окружения: {name}"
        )

    return value.strip()


def get_sbis_url() -> str:
    """Получить URL endpoint СБИС."""
    return os.getenv(
        "SBIS_URL",
        "https://online.sbis.ru/service/",
    ).strip()