"""
Поиск организации СБИС по ИНН.

Назначение модуля:
- выполнить Contractor.SearchSuggest;
- искать организацию по точному ИНН;
- получить SppUuid найденной организации;
- не выполнять ContractorCard.Read;
- не изменять локальную базу данных.

Основной сценарий:

    ИНН
    → Contractor.SearchSuggest
    → точное совпадение по ИНН
    → SppUuid

Функции:
- search_company_uuid_by_inn() — выполнить поиск организации;
- _build_company_search_payload() — сформировать JSON-RPC запрос;
- _company_search_headers() — сформировать HTTP-заголовки;
- _extract_company_uuid_by_inn() — извлечь SppUuid из RecordSet.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import aiohttp

from src.config import get_sbis_url, require_env
from src.sbis.records import rows_to_dicts


def _company_search_headers(
    browser_cookie: str,
) -> dict[str, str]:
    """
    Сформировать заголовки браузерного вызова
    Contractor.SearchSuggest.

    Аргументы:
        browser_cookie:
            Значение Cookie браузерной сессии СБИС.

    Возвращает:
        Словарь HTTP-заголовков.
    """
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Cookie": browser_cookie,
        "Origin": "https://online.sbis.ru",
        "Referer": "https://online.sbis.ru/",
        "X-CalledMethod": "Contractor.SearchSuggest",
        "X-OriginalMethodName": (
            "Q29udHJhY3Rvci5TZWFyY2hTdWdnZXN0"
        ),
    }


def _build_company_search_payload(
    inn: str,
) -> dict[str, Any]:
    """
    Сформировать точный поиск организации по ИНН.

    Аргументы:
        inn:
            ИНН организации.

    Возвращает:
        JSON-RPC payload для Contractor.SearchSuggest.

    Исключения:
        ValueError:
            Если ИНН содержит не 10 и не 12 цифр.
    """
    clean_inn = "".join(
        character
        for character in inn
        if character.isdigit()
    )

    if clean_inn != inn.strip() or len(clean_inn) not in (10, 12):
        raise ValueError(
            "ИНН должен содержать 10 или 12 цифр"
        )

    return {
        "jsonrpc": "2.0",
        "protocol": 7,
        "method": "Contractor.SearchSuggest",
        "params": {
            "Фильтр": {
                "d": [
                    [],
                    "643",
                    {
                        "search": clean_inn,
                        "exact": True,
                        "history": False,
                        "params": ["ПоВсему"],
                    },
                    True,
                    True,
                    None,
                    [],
                    "company",
                    [],
                    True,
                    clean_inn,
                ],
                "s": [
                    {
                        "t": {
                            "n": "Массив",
                            "t": "Строка",
                        },
                        "n": "Category",
                    },
                    {
                        "t": "Строка",
                        "n": "CountryCode",
                    },
                    {
                        "t": "JSON-объект",
                        "n": "DetailsParams",
                    },
                    {
                        "t": "Логическое",
                        "n": "MatchedFields",
                    },
                    {
                        "t": "Логическое",
                        "n": "Misspelling",
                    },
                    {
                        "t": "Строка",
                        "n": "Parent",
                    },
                    {
                        "t": {
                            "n": "Массив",
                            "t": "Строка",
                        },
                        "n": "RegionCodes",
                    },
                    {
                        "t": "Строка",
                        "n": "currentTab",
                    },
                    {
                        "t": {
                            "n": "Массив",
                            "t": "Строка",
                        },
                        "n": "historyKeys",
                    },
                    {
                        "t": "Логическое",
                        "n": "useRegionCode",
                    },
                    {
                        "t": "Строка",
                        "n": "Реквизиты",
                    },
                ],
                "_type": "record",
                "f": 0,
            },
            "Сортировка": None,
            "Навигация": {
                "d": [
                    True,
                    7,
                    0,
                ],
                "s": [
                    {
                        "t": "Логическое",
                        "n": "ЕстьЕще",
                    },
                    {
                        "t": "Число целое",
                        "n": "РазмерСтраницы",
                    },
                    {
                        "t": "Число целое",
                        "n": "Страница",
                    },
                ],
                "_type": "record",
                "f": 0,
            },
            "ДопПоля": [],
        },
        "id": 1,
    }


def _extract_company_uuid_by_inn(
    result: dict[str, Any],
    inn: str,
) -> str | None:
    """
    Извлечь SppUuid организации с точным ИНН.

    Аргументы:
        result:
            Значение result из ответа
            Contractor.SearchSuggest.

        inn:
            ИНН, по которому выполнялся поиск.

    Возвращает:
        Нормализованный UUID в виде строки.

        None, если точного совпадения не найдено.
    """
    clean_inn = inn.strip()

    rows = result.get("d")
    schema = result.get("s")

    if not isinstance(rows, list):
        raise RuntimeError(
            'Ответ Contractor.SearchSuggest не содержит корректный result["d"]'
        )

    if not isinstance(schema, list):
        raise RuntimeError(
            'Ответ Contractor.SearchSuggest не содержит корректный result["s"]'
        )

    records = rows_to_dicts(
        rows,
        schema,
    )

    for record in records:
        record_inn = next(
            (
                record.get(field)
                for field in (
                    "ИНН",
                    "inn",
                    "Inn",
                )
                if record.get(field) is not None
            ),
            None,
        )

        digits = "".join(
            character
            for character in str(record_inn or "")
            if character.isdigit()
        )

        if digits != clean_inn:
            continue

        value = record.get("SppUuid")

        if not isinstance(value, str) or not value.strip():
            continue

        try:
            normalized = str(
                UUID(
                    value.strip()
                )
            )
        except ValueError:
            continue

        return normalized

    return None


async def search_company_uuid_by_inn(
    inn: str,
) -> str | None:
    """
    Найти SppUuid организации по точному ИНН.

    Что делает:
    - формирует Contractor.SearchSuggest;
    - использует браузерную Cookie СБИС;
    - выполняет HTTP POST;
    - проверяет HTTP и JSON-RPC ошибки;
    - ищет строку с точным совпадением ИНН;
    - возвращает валидный SppUuid.

    Аргументы:
        inn:
            ИНН организации.

    Возвращает:
        SppUuid организации.

        None, если организация с таким ИНН
        не найдена в результате поиска.

    Исключения:
        RuntimeError:
            Если СБИС вернул HTTP-ошибку,
            некорректный JSON
            или JSON-RPC ошибку.
    """
    payload = _build_company_search_payload(
        inn
    )

    clean_inn = inn.strip()

    browser_cookie = require_env(
        "SBIS_BROWSER_COOKIE"
    )

    url = get_sbis_url()

    timeout = aiohttp.ClientTimeout(
        total=60
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:
        async with session.post(
            url,
            headers=_company_search_headers(
                browser_cookie
            ),
            json=payload,
        ) as response:
            if response.status >= 400:
                raise RuntimeError(
                    "СБИС вернул HTTP "
                    f"{response.status}"
                )

            try:
                response_payload = (
                    await response.json(
                        content_type=None
                    )
                )
            except (
                aiohttp.ContentTypeError,
                ValueError,
            ) as error:
                raise RuntimeError(
                    "СБИС вернул ответ "
                    "не в формате JSON"
                ) from error

    if not isinstance(
        response_payload,
        dict,
    ):
        raise RuntimeError(
            "СБИС вернул JSON "
            "неизвестного формата"
        )

    if "error" in response_payload:
        error = response_payload[
            "error"
        ]

        if isinstance(error, dict):
            details = error.get(
                "details"
            )
            message = error.get(
                "message"
            )
        else:
            details = None
            message = None

        raise RuntimeError(
            details
            or message
            or (
                "Contractor.SearchSuggest "
                "вернул ошибку"
            )
        )

    result = response_payload.get(
        "result"
    )

    if not isinstance(
        result,
        dict,
    ):
        raise RuntimeError(
            "Ответ Contractor.SearchSuggest "
            "не содержит result"
        )

    return _extract_company_uuid_by_inn(
        result,
        clean_inn,
    )