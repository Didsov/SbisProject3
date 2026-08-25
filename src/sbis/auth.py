"""
Безопасная проверка браузерной авторизации СБИС.

Модуль выполняет только лёгкий Contractor.SearchSuggest с фиктивным ИНН.
Он не читает клиентов из БД, не изменяет БД и никогда не включает cookie
в результат, сообщение ошибки или вывод.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import aiohttp

from src.config import (
    SBIS_BROWSER_COOKIE,
    get_sbis_url,
)
from src.sbis.company_search import (
    _build_company_search_payload,
    _company_search_headers,
)


SbisAuthStatus = Literal[
    "valid",
    "invalid_auth",
    "network_server_error",
]

AUTH_CHECK_INN = "000000000000"
AUTH_CHECK_TIMEOUT_SECONDS = 15.0
AUTH_HTTP_STATUSES = {401, 403}


@dataclass(frozen=True, slots=True)
class SbisAuthCheckResult:
    """Результат проверки SBIS_BROWSER_COOKIE без секретных данных."""

    status: SbisAuthStatus
    checked_at: datetime
    http_status: int | None = None
    error_type: str | None = None

    @property
    def is_valid(self) -> bool:
        """Вернуть True только для подтверждённой рабочей cookie."""
        return self.status == "valid"

    @property
    def is_invalid_auth(self) -> bool:
        """Вернуть True для истёкшей или отсутствующей авторизации."""
        return self.status == "invalid_auth"


def _result(
    status: SbisAuthStatus,
    *,
    http_status: int | None = None,
    error_type: str | None = None,
) -> SbisAuthCheckResult:
    """Создать результат с единым timezone-aware временем проверки."""
    return SbisAuthCheckResult(
        status=status,
        checked_at=datetime.now().astimezone(),
        http_status=http_status,
        error_type=error_type,
    )


def _payload_contains_auth_error(payload: object) -> bool:
    """Распознать JSON-RPC auth-ошибку без сохранения текста ответа."""
    if not isinstance(payload, dict) or "error" not in payload:
        return False

    error = payload.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        fragments = (
            error.get("message"),
            error.get("details"),
        )
    else:
        code = None
        fragments = (error,)

    if code in AUTH_HTTP_STATUSES or str(code) in {"401", "403"}:
        return True

    normalized = " ".join(
        str(fragment or "").casefold()
        for fragment in fragments
    )
    markers = (
        "unauthorized",
        "forbidden",
        "authentication",
        "authorization",
        "access denied",
        "авторизац",
        "аутентификац",
        "сессия истек",
        "сеанс истек",
        "доступ запрещ",
    )
    return any(marker in normalized for marker in markers)


async def check_sbis_browser_cookie(
    cookie: str | None = None,
) -> SbisAuthCheckResult:
    """
    Проверить браузерную cookie СБИС лёгким безопасным запросом.

    Статусы результата:
    - ``valid`` — СБИС принял запрос и вернул JSON-RPC result;
    - ``invalid_auth`` — cookie отсутствует, получен HTTP 401/403,
      redirect на авторизацию либо эквивалентная JSON-RPC auth-ошибка;
    - ``network_server_error`` — timeout, транспортная ошибка, HTTP 5xx,
      неожиданный HTTP/JSON-ответ или не-auth JSON-RPC ошибка.

    Значение cookie не журналируется и не включается в исключения.
    """
    browser_cookie = (
        cookie
        if cookie is not None
        else SBIS_BROWSER_COOKIE
    )
    browser_cookie = str(browser_cookie or "").strip()

    if not browser_cookie:
        return _result(
            "invalid_auth",
            error_type="missing_cookie",
        )

    timeout = aiohttp.ClientTimeout(
        total=AUTH_CHECK_TIMEOUT_SECONDS,
    )
    payload = _build_company_search_payload(
        AUTH_CHECK_INN,
    )

    try:
        async with aiohttp.ClientSession(
            timeout=timeout,
        ) as session:
            async with session.post(
                get_sbis_url(),
                headers=_company_search_headers(
                    browser_cookie,
                ),
                json=payload,
                allow_redirects=False,
            ) as response:
                http_status = response.status

                if http_status in AUTH_HTTP_STATUSES:
                    return _result(
                        "invalid_auth",
                        http_status=http_status,
                    )

                # API не должен перенаправлять JSON-RPC запрос. На практике
                # redirect означает переход к браузерной авторизации.
                if 300 <= http_status < 400:
                    return _result(
                        "invalid_auth",
                        http_status=http_status,
                        error_type="auth_redirect",
                    )

                if http_status >= 400:
                    return _result(
                        "network_server_error",
                        http_status=http_status,
                        error_type="http_error",
                    )

                try:
                    response_payload = await response.json(
                        content_type=None,
                    )
                except (
                    aiohttp.ContentTypeError,
                    UnicodeError,
                    ValueError,
                ):
                    return _result(
                        "network_server_error",
                        http_status=http_status,
                        error_type="invalid_json",
                    )
    except (
        asyncio.TimeoutError,
        aiohttp.ClientError,
        OSError,
    ) as error:
        # Сохраняется только имя класса, поскольку str(error) потенциально
        # может содержать детали запроса. Cookie сюда не попадёт.
        return _result(
            "network_server_error",
            error_type=type(error).__name__,
        )
    except Exception as error:
        # Контракт preflight требует результата и для неожиданных локальных
        # ошибок. Текст исключения намеренно не сохраняется и не печатается.
        return _result(
            "network_server_error",
            error_type=type(error).__name__,
        )

    if _payload_contains_auth_error(response_payload):
        return _result(
            "invalid_auth",
            http_status=http_status,
            error_type="json_rpc_auth_error",
        )

    if not isinstance(response_payload, dict):
        return _result(
            "network_server_error",
            http_status=http_status,
            error_type="unexpected_json",
        )

    if "error" in response_payload:
        return _result(
            "network_server_error",
            http_status=http_status,
            error_type="json_rpc_error",
        )

    if "result" not in response_payload:
        return _result(
            "network_server_error",
            http_status=http_status,
            error_type="missing_result",
        )

    return _result(
        "valid",
        http_status=http_status,
    )
