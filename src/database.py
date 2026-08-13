"""
Работа с локальной SQLite-базой проекта.

Назначение файла:
- создавать SQLite-базу при первом запуске;
- создавать таблицу клиентов;
- хранить базовые данные из CRMClients.ListClientsOnline;
- хранить статус получения подробной карточки ContractorCard.Read;
- подготовить основу для дальнейшего сохранения директора и контактов.

Функции:
- get_connection() — открывает соединение с SQLite;
- initialize_database() — создаёт необходимые таблицы и индексы.

На текущем этапе база хранит:
- SppUuid;
- ИНН;
- название;
- статус обогащения карточкой;
- дату создания записи;
- дату последнего обновления.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_DIR = PROJECT_ROOT / "data"
DATABASE_FILE = DATABASE_DIR / "project.db"


def get_connection() -> sqlite3.Connection:
    """
    Открыть соединение с основной SQLite-базой проекта.

    Что делает:
    - создаёт каталог data, если он отсутствует;
    - открывает или создаёт файл project.db;
    - включает доступ к строкам результата по имени колонки.

    Возвращает:
        Открытое соединение sqlite3.Connection.

    Важно:
        Закрывать соединение должен вызывающий код,
        обычно через конструкцию with.
    """
    DATABASE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        DATABASE_FILE
    )

    connection.row_factory = sqlite3.Row

    return connection

def initialize_client_contacts_table(
    connection: sqlite3.Connection,
) -> None:
    """
    Создать таблицу персонализированных контактов клиентов.

    Что делает:
    - создаёт таблицу client_contacts;
    - связывает контакт с записью из clients через client_id;
    - запрещает сохранение одинакового контакта одного типа
      для одного клиента;
    - создаёт индексы для поиска контактов.

    Аргументы:
        connection:
            Открытое соединение с основной SQLite-базой проекта.

    Таблица client_contacts:
        id:
            Внутренний идентификатор контакта.

        client_id:
            Ссылка на clients.id.

        contact_type:
            Тип контакта.

            Поддерживаемые на текущем этапе значения:
            - phone;
            - email.

        value:
            Телефон или email.

        created_at:
            Время первого сохранения контакта.

    Особенности:
        UNIQUE(client_id, contact_type, value) не позволяет
        сохранить один и тот же контакт одному клиенту повторно.
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS client_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            contact_type TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (client_id)
                REFERENCES clients (id)
                ON DELETE CASCADE,

            UNIQUE (
                client_id,
                contact_type,
                value
            )
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_client_contacts_client_id
        ON client_contacts (client_id)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_client_contacts_type
        ON client_contacts (contact_type)
        """
    )
def initialize_database() -> None:
    """
    Инициализировать структуру основной SQLite-базы проекта.

    Что делает:
    - создаёт таблицу clients;
    - создаёт необходимые индексы clients;
    - добавляет отсутствующие колонки clients;
    - создаёт таблицу client_contacts;
    - создаёт индексы client_contacts.

    Все операции инициализации выполняются через одно
    соединение с базой данных.
    """
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                spp_uuid TEXT NOT NULL UNIQUE,
                inn TEXT,
                name TEXT,
                enriched INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_clients_inn
            ON clients (inn)
            """
        )

        existing_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(clients)"
            ).fetchall()
        }

        columns_to_add = {
            "kpp": "TEXT",
            "ogrn": "TEXT",
            "director_last_name": "TEXT",
            "director_first_name": "TEXT",
            "director_middle_name": "TEXT",
            "director_inn": "TEXT",
            "director_position": "TEXT",
        }

        for column_name, column_type in columns_to_add.items():
            if column_name in existing_columns:
                continue

            connection.execute(
                f"""
                ALTER TABLE clients
                ADD COLUMN {column_name} {column_type}
                """
            )

        initialize_client_contacts_table(
            connection
        )
        initialize_client_selections_table(
            connection
        )


def save_enriched_client(
    client: dict[str, object],
) -> None:
    """
    Сохранить результат ContractorCard.Read для одного клиента.

    Что делает:
    - находит клиента по spp_uuid;
    - обновляет реквизиты организации;
    - обновляет данные директора;
    - удаляет старые персонализированные контакты клиента;
    - сохраняет актуальные телефоны и email;
    - выставляет enriched = 1;
    - обновляет updated_at.

    Аргументы:
        client:
            Нормализованный словарь, возвращаемый
            parse_contractor_card().

    Исключения:
        ValueError:
            Если spp_uuid отсутствует или имеет неправильный тип;
            если клиент с таким spp_uuid отсутствует в базе;
            если director, phones или emails имеют неожиданный тип.

    Важно:
        Все изменения выполняются в одной транзакции.
        Если во время сохранения произойдёт ошибка,
        изменения не будут зафиксированы частично.
    """
    spp_uuid = client.get("spp_uuid")

    if not isinstance(spp_uuid, str) or not spp_uuid:
        raise ValueError(
            "Обогащённая карточка не содержит корректный spp_uuid"
        )

    director = client.get("director")

    if not isinstance(director, dict):
        raise ValueError(
            "Поле director должно быть словарём"
        )

    phones = client.get("phones")

    if not isinstance(phones, list):
        raise ValueError(
            "Поле phones должно быть списком"
        )

    emails = client.get("emails")

    if not isinstance(emails, list):
        raise ValueError(
            "Поле emails должно быть списком"
        )

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id
            FROM clients
            WHERE spp_uuid = ?
            """,
            (spp_uuid,),
        ).fetchone()

        if row is None:
            raise ValueError(
                "Клиент с SppUuid "
                f"{spp_uuid} отсутствует в базе"
            )

        client_id = row["id"]

        connection.execute(
            """
            UPDATE clients
            SET
                name = COALESCE(?, name),
                inn = COALESCE(?, inn),
                kpp = ?,
                ogrn = ?,
                director_last_name = ?,
                director_first_name = ?,
                director_middle_name = ?,
                director_inn = ?,
                director_position = ?,
                enriched = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                client.get("name"),
                client.get("inn"),
                client.get("kpp"),
                client.get("ogrn"),
                director.get("last_name"),
                director.get("first_name"),
                director.get("middle_name"),
                director.get("inn"),
                director.get("position"),
                client_id,
            ),
        )

        # Контакты из ContractorCard.Read считаем актуальным снимком,
        # поэтому старый набор полностью заменяем новым.
        connection.execute(
            """
            DELETE FROM client_contacts
            WHERE client_id = ?
            """,
            (client_id,),
        )

        for phone in phones:
            if not isinstance(phone, str) or not phone:
                continue

            connection.execute(
                """
                INSERT OR IGNORE INTO client_contacts (
                    client_id,
                    contact_type,
                    value
                )
                VALUES (?, 'phone', ?)
                """,
                (
                    client_id,
                    phone,
                ),
            )

        for email in emails:
            if not isinstance(email, str) or not email:
                continue

            connection.execute(
                """
                INSERT OR IGNORE INTO client_contacts (
                    client_id,
                    contact_type,
                    value
                )
                VALUES (?, 'email', ?)
                """,
                (
                    client_id,
                    email,
                ),
            )

def get_unenriched_clients(
    selection_id: int | None = None,
    limit: int | None = None,
) -> list[dict[str, object]]:
    """
    Получить клиентов, для которых ещё не обработана подробная карточка.

    Что делает:
    - выбирает только клиентов с enriched = 0;
    - при наличии selection_id ограничивает результат конкретной
      выборкой СБИС через таблицу client_selections;
    - сортирует клиентов по внутреннему clients.id;
    - при необходимости ограничивает количество записей;
    - возвращает строки SQLite как обычные словари.

    Аргументы:
        selection_id:
            Номер выборки СБИС.

            Если указан, возвращаются только необогащённые клиенты,
            связанные с этой выборкой.

            Если None, возвращаются необогащённые клиенты
            из всей базы.

        limit:
            Максимальное количество клиентов.

            Если None, ограничение не применяется.

    Возвращает:
        Список словарей вида:

        {
            "spp_uuid": ...,
            "inn": ...,
            "name": ...
        }

    Исключения:
        ValueError:
            Если selection_id указан и меньше 1;
            если limit указан и меньше 1.
    """
    if selection_id is not None and selection_id < 1:
        raise ValueError(
            "selection_id должен быть больше 0"
        )

    if limit is not None and limit < 1:
        raise ValueError(
            "limit должен быть больше 0"
        )

    params: list[object] = []

    if selection_id is None:
        query = """
            SELECT
                c.spp_uuid,
                c.inn,
                c.name
            FROM clients AS c
            WHERE c.enriched = 0
            ORDER BY c.id
        """
    else:
        query = """
            SELECT
                c.spp_uuid,
                c.inn,
                c.name
            FROM clients AS c
            INNER JOIN client_selections AS cs
                ON cs.client_id = c.id
            WHERE
                c.enriched = 0
                AND cs.selection_id = ?
            ORDER BY c.id
        """

        params.append(
            selection_id
        )

    if limit is not None:
        query += " LIMIT ?"

        params.append(
            limit
        )

    with get_connection() as connection:
        rows = connection.execute(
            query,
            tuple(params),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def upsert_clients(
    clients: list[dict[str, object]],
    selection_id: int,
) -> int:
    """
    Сохранить или обновить клиентов и связать их с выборкой СБИС.

    Что делает:
    - проходит по декодированному списку клиентов;
    - берёт SppUuid, ИНН и название;
    - пропускает записи без корректного SppUuid;
    - добавляет новых клиентов;
    - обновляет ИНН и название уже существующих;
    - не сбрасывает статус enriched;
    - создаёт или обновляет связь client ↔ selection_id;
    - обновляет updated_at;
    - возвращает количество обработанных клиентов.

    Аргументы:
        clients:
            Декодированные строки CRMClients.ListClientsOnline.

        selection_id:
            Номер выборки СБИС, из которой получены клиенты.

    Возвращает:
        Количество клиентов, сохранённых или обновлённых в базе.
    """
    processed = 0

    with get_connection() as connection:
        for client in clients:
            spp_uuid = client.get("SppUuid")

            if not isinstance(spp_uuid, str) or not spp_uuid:
                continue

            inn = client.get("ИНН")
            name = client.get("Название")

            connection.execute(
                """
                INSERT INTO clients (
                    spp_uuid,
                    inn,
                    name
                )
                VALUES (?, ?, ?)

                ON CONFLICT(spp_uuid) DO UPDATE SET
                    inn = excluded.inn,
                    name = excluded.name,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    spp_uuid,
                    inn if isinstance(inn, str) else None,
                    name if isinstance(name, str) else None,
                ),
            )

            row = connection.execute(
                """
                SELECT id
                FROM clients
                WHERE spp_uuid = ?
                """,
                (spp_uuid,),
            ).fetchone()

            if row is None:
                raise RuntimeError(
                    f"Не удалось получить id клиента {spp_uuid}"
                )

            client_id = row["id"]

            connection.execute(
                """
                INSERT INTO client_selections (
                    client_id,
                    selection_id
                )
                VALUES (?, ?)

                ON CONFLICT(client_id, selection_id) DO UPDATE SET
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    client_id,
                    selection_id,
                ),
            )

            processed += 1

    return processed
def initialize_client_selections_table(
    connection: sqlite3.Connection,
) -> None:
    """
    Создать таблицу связей клиентов с выборками СБИС.

    Что делает:
    - создаёт таблицу client_selections;
    - связывает запись clients.id с номером выборки СБИС;
    - позволяет одному клиенту состоять сразу в нескольких выборках;
    - запрещает дублирование одной и той же связи;
    - создаёт индексы для поиска клиентов по выборке.

    Аргументы:
        connection:
            Открытое соединение с основной SQLite-базой проекта.

    Таблица client_selections:
        id:
            Внутренний идентификатор связи.

        client_id:
            Ссылка на clients.id.

        selection_id:
            Числовой идентификатор выборки СБИС.

        created_at:
            Время первого появления связи в базе.

        updated_at:
            Время последнего подтверждения того,
            что клиент присутствует в выборке.

    Особенности:
        UNIQUE(client_id, selection_id) не позволяет создать
        одинаковую связь повторно.
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS client_selections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            selection_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (client_id)
                REFERENCES clients (id)
                ON DELETE CASCADE,

            UNIQUE (
                client_id,
                selection_id
            )
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_client_selections_selection_id
        ON client_selections (selection_id)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_client_selections_client_id
        ON client_selections (client_id)
        """
    )