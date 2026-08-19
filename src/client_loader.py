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
from pathlib import Path
from src.database import (
    get_unenriched_clients,
    initialize_database,
    save_enriched_client,
    upsert_clients,
)
from src.sbis.card_parser import parse_contractor_card
from src.sbis.client_list import get_all_clients
from src.sbis.contractor_card import get_contractor_card

from src.database import upsert_client_by_inn_lookup
from src.sbis.company_search import search_company_uuid_by_inn

from src.database import (
    add_client_source,
    upsert_client_by_inn_lookup,
)


CONTRACTOR_CARD_DELAY_SECONDS = 3
def load_inns(
    *,
    inn: str | None,
    inn_file: str | None,
) -> list[str]:
    """
    Получить нормализованный список ИНН для режима загрузки по ИНН.

    Источники:
    - одиночный ИНН из --inn;
    - текстовый файл из --inn-file.

    Формат файла:
    - один ИНН на строку;
    - пустые строки игнорируются;
    - повторяющиеся ИНН удаляются
      с сохранением исходного порядка.

    Аргументы:
        inn:
            Одиночный ИНН из CLI.

        inn_file:
            Путь к текстовому файлу со списком ИНН.

    Возвращает:
        Список уникальных ИНН.

    Исключения:
        ValueError:
            Если ИНН содержит не 10 и не 12 цифр.

        FileNotFoundError:
            Если указанный файл не существует.
    """
    if inn is not None:
        raw_values = [
            inn
        ]

    elif inn_file is not None:
        file_path = Path(
            inn_file
        )

        raw_values = file_path.read_text(
            encoding="utf-8-sig"
        ).splitlines()

    else:
        return []

    result: list[str] = []
    seen: set[str] = set()

    for raw_value in raw_values:
        clean_inn = raw_value.strip()

        if not clean_inn:
            continue

        if (
            not clean_inn.isdigit()
            or len(clean_inn) not in (10, 12)
        ):
            raise ValueError(
                f"Некорректный ИНН: {clean_inn!r}. "
                "Ожидается 10 или 12 цифр."
            )

        if clean_inn in seen:
            continue

        seen.add(
            clean_inn
        )

        result.append(
            clean_inn
        )

    return result


async def load_clients_by_inn(
    inns: list[str],
    *,
    source_type: str,
    source_value: str,
) -> list[dict[str, str]]:
    """
    Найти организации СБИС по списку ИНН и сохранить их в clients.

    Что делает:
    - проходит по списку ИНН;
    - для каждого ИНН вызывает Contractor.SearchSuggest;
    - получает точный SppUuid;
    - создаёт или обновляет клиента в таблице clients;
    - не создаёт связь client_selections;
    - не выполняет ContractorCard.Read;
    - возвращает список успешно найденных организаций.

    Аргументы:
        inns:
            Нормализованный список ИНН.

    Возвращает:
        Список словарей:
            {
                "inn": "...",
                "spp_uuid": "..."
            }

        Если организация по ИНН не найдена,
        она не попадает в возвращаемый список.
    """
    found_clients: list[dict[str, str]] = []

    total = len(inns)

    for index, inn in enumerate(
        inns,
        start=1,
    ):
        print()
        print(
            f"[{index}/{total}] "
            f"Поиск ИНН {inn}"
        )

        spp_uuid = await search_company_uuid_by_inn(
            inn
        )

        if spp_uuid is None:
            print(
                "Организация не найдена."
            )
            continue

        client_id = upsert_client_by_inn_lookup(
            inn=inn,
            spp_uuid=spp_uuid,
        )
        add_client_source(
            client_id=client_id,
            source_type=source_type,
            source_value=source_value,
        )

        print(
            f"SppUuid: {spp_uuid}"
        )
        print(
            f"clients.id: {client_id}"
        )

        found_clients.append(
            {
                "inn": inn,
                "spp_uuid": spp_uuid,
            }
        )

    return found_clients

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
        default=None,
        help="Номер выборки клиентов СБИС.",
    )
    parser.add_argument(
        "--inn",
        type=str,
        default=None,
        help=(
            "Загрузить и обогатить одну организацию "
            "по точному ИНН."
        ),
    )

    parser.add_argument(
        "--inn-file",
        type=str,
        default=None,
        help=(
            "Путь к текстовому файлу со списком ИНН. "
            "Один ИНН на строку."
        ),
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

    if (
        arguments.selection is not None
        and arguments.selection <= 0
    ):
        parser.error(
            "--selection должен быть больше 0"
        )
    if arguments.enrich_limit < 0:
        parser.error(
            "--enrich-limit не может быть меньше 0"
        )


    source_count = sum(
        [
            arguments.selection is not None,
            arguments.inn is not None,
            arguments.inn_file is not None,
        ]
    )

    if source_count != 1:
        parser.error(
            "Нужно указать ровно один источник: "
            "--selection, --inn или --inn-file"
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

        if not isinstance(spp_uuid, str) or not spp_uuid.strip():
            spp_uuid = None
        else:
            spp_uuid = spp_uuid.strip()

        contractor_id = client.get("contractor_id")
        if not isinstance(contractor_id, int):
            contractor_id = None

        if spp_uuid is None and contractor_id is None:
            print(
                f"[{index}/{total_clients}] "
                "Нет spp_uuid и contractor_id, клиент пропущен."
            )
            continue

        client_name = client.get("name")
        if isinstance(client_name, str) and client_name:
            display_name = client_name
        elif spp_uuid is not None:
            display_name = spp_uuid
        else:
            display_name = str(contractor_id)

        print()
        print(
            f"[{index}/{total_clients}] "
            f"{display_name}"
        )

        card = await get_contractor_card(
            spp_uuid=spp_uuid,
            contractor_id=contractor_id,
        )

        parsed_card = parse_contractor_card(
            card
        )

        save_enriched_client(
            parsed_card,
            contractor_id=contractor_id,
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
    *,
    selection_id: int | None,
    inn: str | None,
    inn_file: str | None,
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
    initialize_database()
    if inn is not None or inn_file is not None:
        inns = load_inns(
            inn=inn,
            inn_file=inn_file,
        )

        if not inns:
            print(
                "Список ИНН пуст."
            )
            return

        print(
            f"ИНН для обработки: {len(inns)}"
        )

        if inn_file is not None:
            source_type = "inn_file"
            source_value = Path(
                inn_file
            ).name
        else:
            source_type = "manual_inn"
            source_value = inns[0]

        found_clients = await load_clients_by_inn(
            inns,
            source_type=source_type,
            source_value=source_value,
        )

        print()
        print(
            "Организаций найдено и сохранено: "
            f"{len(found_clients)}"
        )

        if enrich_all and found_clients:
            await enrich_inn_clients(
                found_clients
            )

        return

    print(
        f"Получаю выборку СБИС #{selection_id}..."
    )
    if selection_id is None:
        raise RuntimeError(
            "selection_id отсутствует "
            "для режима загрузки выборки"
        )

    all_clients = await get_all_clients(
        selection_id
    )
    # if all_clients:
    #     print()
    #     print("DEBUG: первая строка выборки:")
    #     print(all_clients[0])

    #     print()
    #     print("DEBUG: поля первой строки:")
    #     print(list(all_clients[0].keys()))

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


async def enrich_inn_clients(
    clients: list[dict[str, str]],
) -> None:
    """
    Обогатить организации, найденные по ИНН.

    Что делает:
    - принимает список организаций с inn и spp_uuid;
    - для каждой организации вызывает ContractorCard.Read;
    - разбирает карточку через parse_contractor_card();
    - сохраняет реквизиты, директора и контакты;
    - выдерживает паузу между запросами.

    Аргументы:
        clients:
            Список словарей вида:
            {
                "inn": "...",
                "spp_uuid": "..."
            }

    Возвращает:
        None.
    """
    total = len(clients)

    for index, client in enumerate(
        clients,
        start=1,
    ):
        inn = client["inn"]
        spp_uuid = client["spp_uuid"]

        print()
        print(
            f"[{index}/{total}] "
            f"ContractorCard.Read: ИНН {inn}"
        )

        card = await get_contractor_card(
            spp_uuid=spp_uuid,
            contractor_id=None,
        )

        parsed_card = parse_contractor_card(
            card
        )

        save_enriched_client(
            parsed_card,
            contractor_id=None,
        )

        print(
            "Карточка сохранена."
        )

        if index < total:
            print(
                "Ожидание "
                f"{CONTRACTOR_CARD_DELAY_SECONDS} "
                "секунды перед следующим запросом..."
            )

            await asyncio.sleep(
                CONTRACTOR_CARD_DELAY_SECONDS
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
            inn=arguments.inn,
            inn_file=arguments.inn_file,
            enrich_limit=arguments.enrich_limit,
            enrich_all=arguments.enrich_all,
        )
    )


if __name__ == "__main__":
    main()