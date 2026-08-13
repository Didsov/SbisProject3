"""
Загрузка и обогащение клиентов из выборки СБИС.

Назначение модуля:
- принять номер пользовательской выборки СБИС;
- получить всех клиентов выборки через CRMClients.ListClientsOnline;
- сохранить или обновить базовые сведения о клиентах в SQLite;
- связать клиентов с конкретной выборкой СБИС;
- определить клиентов, для которых ещё не выполнялся ContractorCard.Read;
- при необходимости обогатить ограниченное количество клиентов;
- при необходимости обогатить всех оставшихся клиентов выборки;
- сохранить реквизиты, руководителя и контакты в локальной базе.

Режимы запуска:

1. Только синхронизация состава выборки:

    python -m src.client_loader --selection 41307

2. Синхронизация и обработка ограниченного количества карточек:

    python -m src.client_loader --selection 41307 --enrich-limit 10

3. Синхронизация и обработка всех необогащённых карточек:

    python -m src.client_loader --selection 41307 --enrich-all

Особенности:
- уже обогащённые клиенты повторно через ContractorCard.Read
  не запрашиваются;
- принадлежность клиента выборке хранится отдельно через
  таблицу client_selections;
- один клиент может принадлежать нескольким выборкам;
- между успешными вызовами ContractorCard.Read выдерживается
  пауза 3 секунды;
- обработка HTTP 429 и ожидание снятия ограничения выполняются
  внутри get_contractor_card().

Функции:
- parse_arguments() — разбирает параметры командной строки;
- enrich_selection_clients() — обогащает выбранную очередь клиентов;
- run() — выполняет полный сценарий загрузки выборки;
- main() — точка входа CLI.
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
from src.sbis.client_list import get_all_clients
from src.sbis.contractor_card import get_contractor_card


CONTRACTOR_CARD_DELAY_SECONDS = 3


def parse_arguments() -> argparse.Namespace:
    """
    Разобрать параметры командной строки.

    Поддерживаемые параметры:
    - --selection — обязательный номер выборки СБИС;
    - --enrich-limit — количество клиентов для тестового
      или ограниченного обогащения;
    - --enrich-all — обработать всех оставшихся необогащённых
      клиентов выбранной выборки.

    Если не передан ни --enrich-limit, ни --enrich-all,
    выполняется только загрузка состава выборки и сохранение
    базовых сведений о клиентах.

    Если одновременно переданы --enrich-all и --enrich-limit,
    приоритет имеет --enrich-all.

    Возвращает:
        argparse.Namespace:
            Объект со значениями:
            - selection;
            - enrich_limit;
            - enrich_all.

    Примеры:
        python -m src.client_loader --selection 41307

        python -m src.client_loader \
            --selection 41307 \
            --enrich-limit 10

        python -m src.client_loader \
            --selection 41307 \
            --enrich-all
    """
    parser = argparse.ArgumentParser(
        description=(
            "Загрузить клиентов из выборки СБИС "
            "и при необходимости выполнить ContractorCard.Read."
        )
    )

    parser.add_argument(
        "--selection",
        type=int,
        required=True,
        help="Номер выборки клиентов СБИС.",
    )

    parser.add_argument(
        "--enrich-limit",
        type=int,
        default=0,
        help=(
            "Количество необогащённых клиентов для "
            "ContractorCard.Read. "
            "0 — не запускать ограниченное обогащение."
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

    arguments = parser.parse_args()

    if arguments.selection <= 0:
        parser.error(
            "--selection должен быть больше 0"
        )

    if arguments.enrich_limit < 0:
        parser.error(
            "--enrich-limit не может быть меньше 0"
        )

    return arguments


async def enrich_selection_clients(
    selection_id: int,
    enrich_limit: int,
    enrich_all: bool,
) -> None:
    """
    Обогатить необработанных клиентов выбранной выборки СБИС.

    Функция определяет очередь клиентов в зависимости от режима
    запуска и последовательно вызывает ContractorCard.Read.

    Что делает:
    - если enrich_all=True, получает всю очередь необогащённых
      клиентов указанной выборки;
    - если enrich_all=False и enrich_limit > 0, получает только
      указанное количество клиентов;
    - если обогащение не запрошено, завершается без запросов;
    - для каждого клиента получает карточку ContractorCard.Read;
    - декодирует и нормализует карточку;
    - сохраняет расширенные сведения в SQLite;
    - после успешного запроса выдерживает паузу между обращениями;
    - после завершения повторно считает оставшуюся очередь.

    Аргументы:
        selection_id:
            Номер выборки СБИС.

        enrich_limit:
            Максимальное количество необогащённых клиентов
            для текущего запуска.

            Значение 0 означает, что ограниченное обогащение
            запускать не нужно.

        enrich_all:
            True — обработать всю оставшуюся очередь выборки.

            При True значение enrich_limit игнорируется.

    Возвращает:
        None.

    Примечания:
        Обработка HTTP 429 выполняется непосредственно внутри
        get_contractor_card(). Поэтому при временном достижении
        лимита СБИС текущий клиент не пропускается.

        Клиент помечается как обогащённый только после успешного
        получения, разбора и сохранения карточки.
    """
    if enrich_all:
        clients_to_enrich = get_unenriched_clients(
            selection_id=selection_id
        )

    elif enrich_limit > 0:
        clients_to_enrich = get_unenriched_clients(
            selection_id=selection_id,
            limit=enrich_limit,
        )

    else:
        return

    if not clients_to_enrich:
        print()
        print(
            "Необогащённых клиентов в этой выборке нет."
        )
        return

    total_clients = len(clients_to_enrich)

    print()
    print(
        "Запускаю ContractorCard.Read для "
        f"{total_clients} клиентов..."
    )

    for index, client in enumerate(
        clients_to_enrich,
        start=1,
    ):
        spp_uuid = client.get("spp_uuid")
        client_name = client.get("name")

        if not isinstance(spp_uuid, str) or not spp_uuid:
            print()
            print(
                f"[{index}/{total_clients}] "
                "SppUuid отсутствует, клиент пропущен."
            )
            continue

        if isinstance(client_name, str) and client_name:
            display_name = client_name
        else:
            display_name = spp_uuid

        print()
        print(
            f"[{index}/{total_clients}] "
            f"{display_name}"
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

        print(
            "Карточка сохранена."
        )

        # Не создаём лишнюю паузу после последнего клиента.
        if index < total_clients:
            print(
                "Ожидание "
                f"{CONTRACTOR_CARD_DELAY_SECONDS} "
                "секунды перед следующим запросом..."
            )

            await asyncio.sleep(
                CONTRACTOR_CARD_DELAY_SECONDS
            )

    remaining_clients = get_unenriched_clients(
        selection_id=selection_id
    )

    print()
    print(
        "Обогащение завершено."
    )
    print(
        "Осталось необогащённых клиентов "
        f"в выборке #{selection_id}: "
        f"{len(remaining_clients)}"
    )


async def run(
    selection_id: int,
    enrich_limit: int,
    enrich_all: bool,
) -> None:
    """
    Выполнить загрузку и при необходимости обогащение выборки.

    Основной сценарий:
    1. Загружает конфигурацию из .env.
    2. Инициализирует структуру SQLite.
    3. Получает всех клиентов указанной выборки СБИС.
    4. Сохраняет или обновляет базовые сведения клиентов.
    5. Обновляет связи клиентов с selection_id.
    6. Определяет размер очереди ContractorCard.Read.
    7. При необходимости запускает обогащение.

    Аргументы:
        selection_id:
            Номер пользовательской выборки СБИС.

        enrich_limit:
            Максимальное количество клиентов для ограниченного
            запуска ContractorCard.Read.

            0 — не запускать ограниченное обогащение.

        enrich_all:
            True — обработать всю оставшуюся очередь клиентов
            текущей выборки.

    Возвращает:
        None.
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

    await enrich_selection_clients(
        selection_id=selection_id,
        enrich_limit=enrich_limit,
        enrich_all=enrich_all,
    )


def main() -> None:
    """
    Запустить CLI-сценарий загрузки клиентов из СБИС.

    Функция:
    - получает аргументы командной строки;
    - запускает асинхронный сценарий через asyncio.run();
    - передаёт номер выборки и параметры режима обогащения.

    Возвращает:
        None.
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