"""
Главная точка запуска проекта.

Назначение файла:
- разобрать параметры командной строки;
- загрузить настройки окружения;
- инициализировать SQLite-базу;
- получить клиентов указанной выборки СБИС;
- сохранить или обновить клиентов в базе данных;
- показать состояние очереди на ContractorCard.Read.

Функции:
- parse_arguments() — разбирает параметры командной строки;
- run() — выполняет основной асинхронный сценарий;
- main() — запускает приложение.
"""

from __future__ import annotations

import argparse
import asyncio

from src.config import load_environment
from src.database import (
    get_unenriched_clients,
    initialize_database,
    save_enriched_client,
    upsert_clients,
)

from src.sbis.card_parser import parse_contractor_card
from src.sbis.contractor_card import get_contractor_card
import asyncio
from src.sbis.client_list import get_all_clients


def parse_arguments() -> argparse.Namespace:
    """
    Разобрать параметры запуска приложения.

    Что делает:
    - принимает номер выборки СБИС через --selection;
    - проверяет, что значение является целым числом.

    Возвращает:
        argparse.Namespace с параметрами запуска.

    Пример:
        python -m src.main --selection 42420
    """
    parser = argparse.ArgumentParser(
        description=(
            "Получить клиентов из указанной выборки СБИС "
            "и сохранить их в локальную базу."
        )
    )

    parser.add_argument(
        "--selection",
        type=int,
        required=True,
        help="Номер выборки клиентов СБИС",
    )
    parser.add_argument(
        "--enrich-limit",
        type=int,
        default=0,
        help=(
            "Сколько необогащённых клиентов обработать через "
            "ContractorCard.Read. 0 — не запускать обогащение."
        ),
    )
    parser.add_argument(
        "--enrich-all",
        action="store_true",
        help=(
            "Обработать через ContractorCard.Read всех "
            "необогащённых клиентов выбранной выборки."
        ),
    )

    return parser.parse_args()

async def run(
    selection_id: int,
    enrich_limit: int,
    enrich_all: bool,
) -> None:
    """
    Выполнить основной сценарий загрузки и обогащения клиентов.

    Что делает:
    - загружает настройки из .env;
    - инициализирует SQLite;
    - получает все страницы указанной выборки;
    - сохраняет или обновляет клиентов;
    - показывает количество клиентов без ContractorCard.Read;
    - при enrich_limit > 0 получает указанное количество карточек;
    - сохраняет реквизиты, директора и контакты в БД.

        Аргументы:
        selection_id:
            Номер выборки клиентов СБИС.

        enrich_limit:
            Максимальное количество необогащённых клиентов,
            которые нужно обработать через ContractorCard.Read.

            0 — не запускать обогащение по лимиту.

        enrich_all:
            True — обработать всех необогащённых клиентов
            выбранной выборки.
    """
    load_environment()
    initialize_database()

    print(
        f"Получаю выборку СБИС #{selection_id}..."
    )

    all_clients = await get_all_clients(
        selection_id
    )

    saved_count = upsert_clients(
        all_clients,
        selection_id,
    )

    print()
    print(
        "Клиентов сохранено или обновлено в БД: "
        f"{saved_count}"
    )

    unenriched_clients = get_unenriched_clients(
        selection_id=selection_id
    )

    print(
        "Клиентов ожидают ContractorCard.Read: "
        f"{len(unenriched_clients)}"
    )
    if enrich_limit > 0:
        clients_to_enrich = get_unenriched_clients(
            selection_id=selection_id,
            limit=enrich_limit,
        )

        print()
        print(
            "Запускаю ContractorCard.Read для "
            f"{len(clients_to_enrich)} клиентов..."
        )

        for index, client in enumerate(
            clients_to_enrich,
            start=1,
        ):
            spp_uuid = client.get("spp_uuid")

            if not isinstance(spp_uuid, str) or not spp_uuid:
                print(
                    f"[{index}/{len(clients_to_enrich)}] "
                    "SppUuid отсутствует, пропуск."
                )
                continue

            print()
            print(
                f"[{index}/{len(clients_to_enrich)}] "
                f"{client.get('name')}"
            )

            card = await get_contractor_card(
                spp_uuid
            )

            parsed_card = parse_contractor_card(
                card
            )

            save_enriched_client(
                parsed_card
            )

            print("Карточка сохранена.")
            print("Ожидание 3 секунды перед следующим запросом...")
            await asyncio.sleep(3)


def main() -> None:
    """
    Запустить приложение с параметрами командной строки.

    Разбирает CLI-аргументы и передаёт номер выборки
    в основной асинхронный сценарий.
    """
    arguments = parse_arguments()

    asyncio.run(
        run(
            selection_id=arguments.selection,
            enrich_limit=arguments.enrich_limit,
            enrich_all=arguments.enrich_all,
        )
    )


if __name__ == "__main__":
    main()