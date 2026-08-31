"""
Централизованная конфигурация ProjectSbis.

Назначение:
- загружать переменные окружения из подходящего .env-файла;
- использовать локальный .env при разработке на Windows;
- использовать production-конфигурацию
  /etc/projectsbis/projectsbis.env на VPS;
- предоставлять единые функции получения обязательных
  и необязательных параметров;
- хранить основные пути и идентификаторы проекта.

Приоритет настроек:
1. Уже заданные переменные окружения процесса.
2. Production env:
   /etc/projectsbis/projectsbis.env
3. Локальный .env в корне проекта.

Секретные значения не должны попадать в Git.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_ENV_FILE = PROJECT_ROOT / ".env"
REQUEST_FILE = PROJECT_ROOT / "config" / "request.json"
    
PRODUCTION_ENV_FILE = Path(
    "/etc/projectsbis/projectsbis.env"
)

def get_sbis_url() -> str:
    """
    Получить URL endpoint СБИС.

    Возвращает:
        Значение SBIS_URL из окружения.

        Если переменная не задана, используется:
        https://online.sbis.ru/service/
    """
    return (
        optional_env(
            "SBIS_URL",
            "https://online.sbis.ru/service/",
        )
        or "https://online.sbis.ru/service/"
    ).strip()

def load_project_env() -> Path | None:
    """
    Загрузить конфигурацию ProjectSbis.

    Что делает:
    - если существует production env-файл,
      загружает его;
    - иначе пытается загрузить локальный .env;
    - не перезаписывает переменные, которые уже
      были явно переданы процессу.

    Возвращает:
        Path:
            Путь к реально загруженному env-файлу.

        None:
            Если ни один env-файл не найден.
    """
    if PRODUCTION_ENV_FILE.is_file():
        load_dotenv(
            PRODUCTION_ENV_FILE,
            override=False,
        )
        return PRODUCTION_ENV_FILE

    if LOCAL_ENV_FILE.is_file():
        load_dotenv(
            LOCAL_ENV_FILE,
            override=False,
        )
        return LOCAL_ENV_FILE

    return None


ENV_FILE = load_project_env()


def require_env(
    name: str,
) -> str:
    """
    Получить обязательную переменную окружения.

    Аргументы:
        name:
            Имя переменной.

    Возвращает:
        Непустое строковое значение.

    Исключения:
        RuntimeError:
            Если переменная отсутствует или пуста.
    """
    value = os.getenv(name)

    if value is None or not value.strip():
        raise RuntimeError(
            "Не задана обязательная переменная "
            f"окружения: {name}"
        )

    return value.strip()


def optional_env(
    name: str,
    default: str | None = None,
) -> str | None:
    """
    Получить необязательную переменную окружения.

    Аргументы:
        name:
            Имя переменной.

        default:
            Значение по умолчанию.

    Возвращает:
        Значение переменной или default.
    """
    value = os.getenv(name)

    if value is None:
        return default

    value = value.strip()

    if not value:
        return default

    return value


DATABASE_FILE = Path(
    optional_env(
        "PROJECT_DB_PATH",
        str(PROJECT_ROOT / "data" / "project.db"),
    )
)

SBIS_BROWSER_COOKIE = optional_env(
    "SBIS_BROWSER_COOKIE"
)

SBIS_AUTH_ALERT_EMAILS = optional_env(
    "SBIS_AUTH_ALERT_EMAILS"
)

DEFAULT_SBIS_AUTH_STATE_FILE = (
    PROJECT_ROOT / "data" / "sbis_auth_state.json"
    if os.name == "nt"
    else Path("/var/lib/projectsbis/sbis_auth_state.json")
)

_configured_sbis_auth_state_file = Path(
    optional_env(
        "SBIS_AUTH_STATE_PATH",
        str(DEFAULT_SBIS_AUTH_STATE_FILE),
    )
)
SBIS_AUTH_STATE_FILE = (
    _configured_sbis_auth_state_file
    if _configured_sbis_auth_state_file.is_absolute()
    else PROJECT_ROOT / _configured_sbis_auth_state_file
)

SBIS_SELECTION_ID = int(
    optional_env(
        "SBIS_SELECTION_ID",
        "5984",
    )
)

MAIL_FROM_EMAIL = optional_env(
    "MAIL_FROM_EMAIL",
    "info@atlantis.ooo",
)

MAIL_FROM_NAME = optional_env(
    "MAIL_FROM_NAME",
    "Атлантис",
)

MAIL_SMTP_HOST = optional_env(
    "MAIL_SMTP_HOST",
    "mail.projectsbis.ru",
)

MAIL_SMTP_PORT = int(
    optional_env(
        "MAIL_SMTP_PORT",
        "587",
    )
)

MAIL_SMTP_USERNAME = optional_env(
    "MAIL_SMTP_USERNAME"
)

MAIL_SMTP_PASSWORD = optional_env(
    "MAIL_SMTP_PASSWORD"
)

TEST_MAIL_EMAIL = optional_env(
    "TEST_MAIL_EMAIL"
)

DAILY_REPORT_EMAILS = optional_env(
    "DAILY_REPORT_EMAILS"
)

ADMIN_PUBLIC_URL = optional_env(
    "ADMIN_PUBLIC_URL",
    "https://mail.projectsbis.ru/admin",
)

CONTACT_PHONE_DISPLAY = optional_env(
    "CONTACT_PHONE_DISPLAY",
    "7‒952‒080‒22‒20",
)

CONTACT_PHONE_URL = optional_env(
    "CONTACT_PHONE_URL",
    "tel:+79520802220",
)


def build_whatsapp_message_url(base_url: str, text: str) -> str:
    """Заменить только предзаполненный текст в существующей WhatsApp-ссылке."""
    parts = urlsplit(base_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "text"
    ]
    query.append(("text", text))
    return urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        urlencode(query),
        parts.fragment,
    ))


CONTACT_WHATSAPP_URL = optional_env(
    "CONTACT_WHATSAPP_URL",
    (
        "https://wa.me/79520802220?"
        + urlencode(
            {
                "text": (
                    "Обращение из почты\n"
                    "Здравствуйте! Меня заинтересовало "
                    "ваше предложение"
                )
            }
        )
    ),
)

ETRN_WHATSAPP_TEXT = (
    "Обращение по ЭТрН из письма\n"
    "Здравствуйте! Меня заинтересовало ваше предложение по подключению ЭТрН."
)

CONTACT_ETRN_WHATSAPP_URL = build_whatsapp_message_url(
    CONTACT_WHATSAPP_URL,
    ETRN_WHATSAPP_TEXT,
)

CONTACT_TELEGRAM_URL = optional_env(
    "CONTACT_TELEGRAM_URL",
    "https://t.me/+79520802220",
)

CONTACT_MAX_URL = optional_env(
    "CONTACT_MAX_URL",
    "https://max.ru/id614023297728_bot",
)

CONTACT_EMAIL = optional_env(
    "CONTACT_EMAIL",
    MAIL_FROM_EMAIL,
)


def build_contact_email_url(
    email: str | None = None,
) -> str:
    """
    Сформировать безопасный mailto URL для отдельной email-ссылки.

    По умолчанию используется CONTACT_EMAIL, который наследует
    MAIL_FROM_EMAIL. Адрес и тема кодируются как части URL; HTML-слой
    дополнительно обязан экранировать готовый URL перед вставкой в href.
    """
    clean_email = str(
        email or CONTACT_EMAIL or ""
    ).strip()
    safe_email = quote(
        clean_email,
        safe="@+._-",
    )
    query = urlencode(
        {
            "subject": (
                "Подбор решения для онлайн-кассы"
            )
        }
    )
    return f"mailto:{safe_email}?{query}"
