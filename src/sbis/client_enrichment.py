"""
Обогащение клиентов подробными данными через ContractorCard.Read.

Назначение файла:
- принимать декодированных клиентов из CRMClients.ListClientsOnline;
- получать SppUuid каждого клиента;
- запрашивать подробную карточку ContractorCard.Read;
- передавать карточку в единый парсер;
- собирать готовые нормализованные данные для дальнейшей работы.

Функции:
- enrich_clients() — последовательно обогащает список клиентов.

Модуль отвечает только за orchestration:
- взять клиента;
- получить его SppUuid;
- запросить карточку;
- распарсить карточку;
- добавить результат в итоговый список.

Модуль не занимается:
- декодированием d/s;
- разбором полей карточки;
- сохранением в БД;
- пагинацией списка клиентов;
- отправкой писем;
- формированием отчётов.
"""

from __future__ import annotations

from typing import Any

from src.sbis.card_parser import parse_contractor_card
from src.sbis.contractor_card import get_contractor_card


async def enrich_clients(
    clients: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Получить подробные данные для списка клиентов.

    Что делает:
    - последовательно проходит по переданным клиентам;
    - получает поле SppUuid;
    - пропускает клиента, если SppUuid отсутствует или некорректен;
    - вызывает ContractorCard.Read;
    - передаёт полученную карточку в parse_contractor_card();
    - добавляет разобранную карточку в итоговый список;
    - выводит прогресс обработки в консоль.

    Аргументы:
        clients:
            Список клиентов, уже декодированный из
            CRMClients.ListClientsOnline.

    Возвращает:
        Список нормализованных карточек организаций.

    Формат одного элемента соответствует результату
    parse_contractor_card():

        {
            "name": ...,
            "inn": ...,
            "kpp": ...,
            "ogrn": ...,
            "spp_uuid": ...,
            "director": {...},
            "phones": [...],
            "emails": [...]
        }

    Особенности:
        Клиент без SppUuid не считается критической ошибкой
        и просто пропускается.

        На текущем этапе запросы выполняются последовательно,
        чтобы не создавать лишнюю нагрузку на СБИС и упростить
        обработку возможных ограничений API.

        Ошибки ContractorCard.Read пока не перехватываются
        внутри этой функции и передаются вызывающему коду.
    """
    enriched_clients: list[dict[str, Any]] = []

    total = len(clients)

    for index, client in enumerate(
        clients,
        start=1,
    ):
        spp_uuid = client.get("SppUuid")

        print()
        print(f"[{index}/{total}]")
        print(f"Название: {client.get('Название')}")
        print(f"SppUuid: {spp_uuid}")

        if not isinstance(spp_uuid, str) or not spp_uuid:
            print("SppUuid отсутствует, пропуск.")
            continue

        card = await get_contractor_card(
            spp_uuid
        )

        parsed_card = parse_contractor_card(
            card
        )

        enriched_clients.append(
            parsed_card
        )

        print("Карточка обработана.")

    return enriched_clients