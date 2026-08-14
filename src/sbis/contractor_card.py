"""
Получение подробной карточки организации через ContractorCard.Read.

Назначение файла:
- формировать JSON-RPC запрос ContractorCard.Read;
- выполнять запрос карточки по SppUuid организации;
- использовать ContractorUUID без обязательной привязки к spp_id;
- проверять HTTP-ответ и JSON-RPC ошибки;
- декодировать result из внутреннего формата СБИС d/s;
- возвращать готовую декодированную карточку для дальнейшего разбора.

Функции:
- build_contractor_card_payload() — формирует payload ContractorCard.Read;
- get_contractor_card() — выполняет запрос и возвращает декодированный result.

Источник идентификатора:
- SppUuid получается из CRMClients.ListClientsOnline;
- его значение передаётся в params.ДопПоля.ContractorUUID.

Модуль не занимается:
- сохранением данных в БД;
- извлечением директора;
- извлечением телефонов и email;
- массовой обработкой списка клиентов;
- пагинацией списка клиентов.
"""

from __future__ import annotations

from typing import Any

import aiohttp
import asyncio
from src.config import get_sbis_url, require_env
from src.sbis.records import record_to_dict

def build_contractor_card_payload(
    *,
    spp_uuid: str | None = None,
    contractor_id: int | None = None,
) -> dict[str, object]:
    """
    Сформировать JSON-RPC payload для ContractorCard.Read.

    Поддерживает два способа идентификации контрагента:

    1. Через spp_uuid:
       - поле ИдО остаётся None;
       - в ДопПоля передаётся ContractorUUID.

    2. Через contractor_id:
       - значение передаётся непосредственно в поле ИдО;
       - ContractorUUID в ДопПоля не добавляется.

    Приоритет имеет spp_uuid. Если переданы оба идентификатора,
    запрос будет сформирован через ContractorUUID.

    Аргументы:
        spp_uuid:
            UUID контрагента СБИС.

        contractor_id:
            Числовой идентификатор контрагента для поля ИдО.

    Возвращает:
        Готовый JSON-RPC payload ContractorCard.Read.

    Исключения:
        TypeError:
            Если contractor_id передан не как int.

        ValueError:
            Если отсутствуют оба идентификатора.
    """
    if spp_uuid is not None:
        spp_uuid = spp_uuid.strip()

        if not spp_uuid:
            spp_uuid = None

    if (
        contractor_id is not None
        and not isinstance(contractor_id, int)
    ):
        raise TypeError(
            "contractor_id должен быть int или None"
        )

    if spp_uuid is None and contractor_id is None:
        raise ValueError(
            "Для ContractorCard.Read нужен "
            "spp_uuid или contractor_id"
        )

    extra_fields: dict[str, object] = {
        "browser": True,
        "firstLoad": True,
        "page": "crm",
        "isRead": True,
        "anchor": "about",
        "CountryCode": "643",
        "accordion": True,
    }

    if spp_uuid is not None:
        extra_fields["ContractorUUID"] = spp_uuid

    return {
        "jsonrpc": "2.0",
        "protocol": 7,
        "method": "ContractorCard.Read",
        "params": {
            "ИдО": (
                contractor_id
                if spp_uuid is None
                else None
            ),
            "ИмяМетода": None,
            "ДопПоля": extra_fields,
        },
        "id": 1,
    }
async def get_contractor_card(
    *,
    spp_uuid: str | None = None,
    contractor_id: int | None = None,
) -> dict[str, Any]:
    """
    Получить подробную карточку организации по SppUuid.

    Что делает:
    - получает cookie браузерной сессии из конфигурации;
    - получает URL RPC-сервиса СБИС;
    - формирует payload через build_contractor_card_payload();
    - выполняет HTTP POST запрос;
    - при HTTP 429 ждёт окончания блокировки и повторяет запрос;
    - проверяет HTTP status;
    - читает JSON-ответ независимо от Content-Type;
    - проверяет JSON-RPC поле "error";
    - извлекает поле "result";
    - декодирует result через общий record_to_dict().

    Аргументы:
        spp_uuid:
            SppUuid организации из CRMClients.ListClientsOnline.

    Возвращает:
        Декодированную карточку ContractorCard.Read
        в виде обычного Python-словаря.

    Особенности:
        При HTTP 429 функция автоматически ждёт 65 секунд
        и повторяет запрос.

        Это позволяет пережить общий лимит ContractorCard.Read,
        в том числе если карточки параллельно открываются вручную
        через интерфейс СБИС.
    """
    cookie = require_env("SBIS_BROWSER_COOKIE")
    url = get_sbis_url()

    payload = build_contractor_card_payload(
        spp_uuid=spp_uuid,
        contractor_id=contractor_id,
    )

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Cookie": cookie,
    }

    timeout = aiohttp.ClientTimeout(total=60)

    while True:
        async with aiohttp.ClientSession(
            timeout=timeout,
        ) as session:
            async with session.post(
                url,
                headers=headers,
                json=payload,
            ) as response:
                response_text = await response.text()

                if response.status == 429:
                    print(
                        "Достигнут лимит ContractorCard.Read."
                    )
                    print(
                        "Ожидание 65 секунд перед повтором..."
                    )

                    await asyncio.sleep(65)
                    continue

                if response.status >= 400:
                    print("Ответ сервера:")
                    print(response_text)

                    raise RuntimeError(
                        "ContractorCard.Read вернул "
                        f"HTTP {response.status}"
                    )

                try:
                    response_payload = await response.json(
                        content_type=None
                    )
                except (
                    aiohttp.ContentTypeError,
                    ValueError,
                ) as error:
                    raise ValueError(
                        "ContractorCard.Read вернул "
                        "ответ не в формате JSON"
                    ) from error

        if not isinstance(response_payload, dict):
            raise ValueError(
                "ContractorCard.Read вернул "
                "JSON неизвестного формата"
            )

        if "error" in response_payload:
            error_data = response_payload["error"]

            if isinstance(error_data, dict):
                message = (
                    error_data.get("details")
                    or error_data.get("message")
                    or "неизвестная ошибка"
                )
            else:
                message = str(error_data)

            raise RuntimeError(
                "ContractorCard.Read вернул ошибку: "
                f"{message}"
            )

        result = response_payload.get("result")

        if not isinstance(result, dict):
            raise ValueError(
                "Ответ ContractorCard.Read "
                "не содержит корректный result"
            )

        return record_to_dict(result)