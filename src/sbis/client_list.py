"""
Получение полного списка клиентов из CRMClients.ListClientsOnline.

Назначение файла:
- выполнять постраничную загрузку клиентов из СБИС;
- декодировать строки result.d по общей схеме result.s;
- читать metadata пагинации из result.m;
- передавать nextPosition в следующий запрос;
- собирать клиентов со всех страниц в единый список.

Функции:
- get_all_clients() — получает все страницы CRMClients.ListClientsOnline
  и возвращает единый список декодированных клиентов.

Особенности:
- первая страница запрашивается без Position;
- следующая страница запрашивается по nextPosition;
- курсор считается непрозрачным значением и не разбирается;
- пагинация завершается, когда nextPosition отсутствует,
  пуст или содержит только None.
"""

from __future__ import annotations

from typing import Any

from src.sbis.client import send_raw_request
from src.sbis.pagination import (
    get_next_position,
    has_next_position,
)
from src.sbis.records import (
    record_to_dict,
    rows_to_dicts,
)


async def get_all_clients(
    selection_id: int,
) -> list[dict[str, Any]]:
    """
    Получить всех клиентов из CRMClients.ListClientsOnline.
        Аргументы:
        selection_id:
            Числовой идентификатор выборки клиентов СБИС.

    Что делает:
    - отправляет запрос первой страницы;
    - декодирует клиентов из result.d/result.s;
    - добавляет клиентов текущей страницы в общий список;
    - декодирует metadata result.m;
    - извлекает nextPosition;
    - при наличии следующей позиции выполняет следующий запрос;
    - повторяет процесс до окончания пагинации.

    Возвращает:
        Список всех декодированных клиентов из выбранной выборки СБИС.

    Исключения:
        ValueError:
            Если ответ СБИС не содержит корректный result;
            если metadata имеют неожиданный формат.

        Ошибки HTTP и JSON-RPC из send_raw_request()
        передаются вызывающему коду.

    Примечание:
        Функция не получает подробные карточки ContractorCard.Read.
        Она отвечает только за загрузку списка клиентов.
    """
    all_clients: list[dict[str, Any]] = []

    position: str | None = None
    page_number = 1

    while True:
        print()
        print(f"Загрузка страницы клиентов #{page_number}")

        response = await send_raw_request(
            selection_id=selection_id,
            position=position,
        )

        result = response.get("result")

        if not isinstance(result, dict):
            raise ValueError(
                "Ответ CRMClients.ListClientsOnline "
                "не содержит корректный result"
            )

        rows = result.get("d")
        schema = result.get("s")

        if not isinstance(rows, list):
            raise ValueError(
                'result["d"] должен быть массивом'
            )

        if not isinstance(schema, list):
            raise ValueError(
                'result["s"] должен быть массивом'
            )

        clients = rows_to_dicts(
            rows,
            schema,
        )

        all_clients.extend(
            clients
        )

        print(
            f"Получено на странице: {len(clients)}"
        )
        print(
            f"Всего собрано: {len(all_clients)}"
        )

        metadata_raw = result.get("m")

        if not isinstance(metadata_raw, dict):
            print(
                "Metadata пагинации отсутствуют. "
                "Загрузка завершена."
            )
            break

        metadata = record_to_dict(
            metadata_raw
        )

        next_position = get_next_position(
            metadata
        )

        if not has_next_position(
            next_position
        ):
            print(
                "Следующей страницы нет. "
                "Загрузка завершена."
            )
            break

        if not isinstance(next_position, list):
            raise ValueError(
                "nextPosition имеет неожиданный формат"
            )

        cursor = next_position[0]

        if not isinstance(cursor, str) or not cursor:
            print(
                "Курсор следующей страницы отсутствует. "
                "Загрузка завершена."
            )
            break

        position = cursor
        page_number += 1

    return all_clients