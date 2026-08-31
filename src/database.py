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

from contextlib import closing
import json
import os
import sqlite3
from pathlib import Path
import secrets
from src.config import DATABASE_FILE



PROJECT_ROOT = Path(__file__).resolve().parent.parent

MAX_MAIL_SEND_ATTEMPTS = 3

MAIL_RUN_COUNTER_FIELDS = (
    "recipients_added",
    "sent_count",
    "delivered_count",
    "bounced_count",
    "deferred_count",
    "failed_count",
)

MAIL_RUN_FINAL_STATUSES = {
    "success",
    "partial",
    "failed",
}

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
    DATABASE_FILE.parent.mkdir(
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
    - добавляет отсутствующие колонки clients;
    - выполняет миграцию идентификаторов клиентов;
    - создаёт необходимые индексы clients;
    - создаёт таблицы контактов, выборок и рассылок.

    Все операции инициализации выполняются через одно
    соединение с базой данных.
    """
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                spp_uuid TEXT UNIQUE,
                contractor_id INTEGER UNIQUE,

                inn TEXT,
                name TEXT,

                enriched INTEGER NOT NULL DEFAULT 0,

                created_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP,

                updated_at TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        existing_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(clients)"
            ).fetchall()
        }
        

        columns_to_add = {
            "contractor_id": "INTEGER",
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

        migrate_clients_identifiers(
            connection
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_clients_inn
            ON clients (inn)
            """
        )

        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_clients_contractor_id
            ON clients (contractor_id)
            WHERE contractor_id IS NOT NULL
            """
        )

        initialize_client_contacts_table(
            connection
        )

        initialize_client_selections_table(
            connection
        )
        initialize_client_sources_table(
            connection
        )

        initialize_mailing_tables(
            connection
        )
    ensure_default_mail_campaign()
    




def save_enriched_client(
    client: dict[str, object],
    *,
    contractor_id: int | None = None,
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

    if not isinstance(spp_uuid, str) or not spp_uuid.strip():
        spp_uuid = None
    else:
        spp_uuid = spp_uuid.strip()

    if contractor_id is not None and not isinstance(
        contractor_id,
        int,
    ):
        raise TypeError(
            "contractor_id должен быть int или None"
        )
    if spp_uuid is None and contractor_id is None:
        raise ValueError(
            "Невозможно сохранить карточку: "
            "нет spp_uuid и contractor_id"
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
            SELECT id,   spp_uuid, contractor_id
            FROM clients
            WHERE 
                (? IS NOT NULL AND contractor_id = ?)
                OR
                (? IS NOT NULL AND spp_uuid = ?)
            LIMIT 1
            """,
            (
                contractor_id,
                contractor_id,
                spp_uuid,
                spp_uuid,
            ),
        ).fetchone()

        if row is None:
            raise ValueError(
                "Клиент отсутствует в базе. "
                f"spp_uuid={spp_uuid!r}, "
                f"contractor_id={contractor_id!r}"
            )
        
        client_id = row["id"]

        connection.execute(
            """
            UPDATE clients
            SET
                spp_uuid = COALESCE(?, spp_uuid),
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
                spp_uuid,
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
            "contractor_id": ...,
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
                c.contractor_id,
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
                c.contractor_id,
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

            if not isinstance(spp_uuid, str) or not spp_uuid.strip():
                spp_uuid = None
            else:
                spp_uuid = spp_uuid.strip()
            contractor_id = client.get("@Лицо")

            if not isinstance(contractor_id, int):
                contractor_id = client.get("ID")

            if not isinstance(contractor_id, int):
                contractor_id = None

            if spp_uuid is None and contractor_id is None:
                continue

            inn = client.get("ИНН")
            name = client.get("Название")

            connection.execute(
                """
                INSERT INTO clients (
                    spp_uuid,
                    contractor_id,
                    inn,
                    name
                )
                VALUES (?, ?, ?, ?)

                ON CONFLICT DO UPDATE SET
                    spp_uuid = COALESCE(
                        excluded.spp_uuid,
                        clients.spp_uuid
                    ),
                    contractor_id = COALESCE(
                        excluded.contractor_id,
                        clients.contractor_id
                    ),
                    inn = excluded.inn,
                    name = excluded.name,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    spp_uuid,
                    contractor_id,
                    inn if isinstance(inn, str) else None,
                    name if isinstance(name, str) else None,
                ),
            )

            row = connection.execute(
                """
                SELECT id
                FROM clients
                WHERE
                    (? IS NOT NULL AND spp_uuid = ?)
                    OR
                    (? IS NOT NULL AND contractor_id = ?)
                LIMIT 1
                """,
                (
                    spp_uuid,
                    spp_uuid,
                    contractor_id,
                    contractor_id,
                ),
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

def initialize_client_sources_table(
    connection: sqlite3.Connection,
) -> None:
    """
    Создать таблицу источников клиентов.

    Назначение:
    - хранить, откуда именно клиент был получен;
    - позволять одному клиенту иметь несколько источников;
    - не перезаписывать старый источник новым;
    - отдельно хранить тип источника и его значение.

    Примеры:
        source_type = "inn_file"
        source_value = "clients_august.txt"

        source_type = "manual_inn"
        source_value = "251118147906"

    Таблица client_sources:
        id:
            Внутренний идентификатор записи.

        client_id:
            Ссылка на clients.id.

        source_type:
            Тип источника.

        source_value:
            Значение источника.

        created_at:
            Время первого появления источника.

        updated_at:
            Время последнего подтверждения источника.

    Особенности:
        UNIQUE(client_id, source_type, source_value)
        не позволяет сохранять один и тот же источник повторно.
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS client_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            client_id INTEGER NOT NULL,

            source_type TEXT NOT NULL,
            source_value TEXT NOT NULL,

            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            updated_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (client_id)
                REFERENCES clients (id)
                ON DELETE CASCADE,

            UNIQUE (
                client_id,
                source_type,
                source_value
            )
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_client_sources_client_id
        ON client_sources (client_id)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_client_sources_type_value
        ON client_sources (
            source_type,
            source_value
        )
        """
    )

def add_client_source(
    *,
    client_id: int,
    source_type: str,
    source_value: str,
) -> None:
    """
    Сохранить источник, из которого был получен клиент.

    Что делает:
    - проверяет входные значения;
    - создаёт связь клиента с источником;
    - не создаёт дубль одинакового источника;
    - при повторном обнаружении обновляет updated_at.

    Аргументы:
        client_id:
            Внутренний ID клиента из таблицы clients.

        source_type:
            Тип источника, например:
            - inn_file;
            - manual_inn.

        source_value:
            Значение источника, например:
            - имя файла;
            - ИНН при ручном вводе.

    Возвращает:
        None.
    """
    if client_id < 1:
        raise ValueError(
            "client_id должен быть больше 0"
        )

    clean_source_type = source_type.strip()
    clean_source_value = source_value.strip()

    if not clean_source_type:
        raise ValueError(
            "source_type не может быть пустым"
        )

    if not clean_source_value:
        raise ValueError(
            "source_value не может быть пустым"
        )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO client_sources (
                client_id,
                source_type,
                source_value
            )
            VALUES (?, ?, ?)

            ON CONFLICT(
                client_id,
                source_type,
                source_value
            )
            DO UPDATE SET
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                client_id,
                clean_source_type,
                clean_source_value,
            ),
        )


def initialize_mailing_tables(
    connection: sqlite3.Connection,
) -> None:
    """
    Создать таблицы подсистемы почтовых рассылок.

    Таблицы:
    - mail_campaigns:
        Описывает кампанию рассылки и связывает её
        с выборкой клиентов СБИС.

    - mail_recipients:
        Хранит конкретных получателей кампании.
        Один клиент может иметь несколько email,
        поэтому получатель определяется сочетанием
        campaign_id + client_id + email.

    - mail_runs:
        Хранит отдельную историю каждого запуска daily-run,
        независимо от повторного использования кампании.

    - mail_messages:
        Хранит факт попытки отправки письма
        конкретному получателю и идентификатор
        сообщения почтового провайдера. Поле status
        описывает SMTP-отправку, а delivery_status —
        последующую доставку по данным Postfix.

    - mail_events:
        Хранит историю событий письма:
        delivered, opened, clicked, bounced и другие.

    Аргументы:
        connection:
            Открытое SQLite-соединение, в котором
            необходимо создать таблицы и индексы.

    Возвращает:
        None.
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS mail_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            name TEXT NOT NULL UNIQUE,
            template_name TEXT,

            selection_id INTEGER NOT NULL,

            campaign_family TEXT NOT NULL DEFAULT 'new_companies',
            next_send_at TEXT,
            batch_sent_count INTEGER NOT NULL DEFAULT 0,

            status TEXT NOT NULL DEFAULT 'draft',

            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            updated_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    campaign_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(mail_campaigns)"
        ).fetchall()
    }

    if "template_name" not in campaign_columns:
        connection.execute(
            """
            ALTER TABLE mail_campaigns
            ADD COLUMN template_name TEXT
            """
        )

    for column_name, definition in {
        "campaign_family": "TEXT NOT NULL DEFAULT 'new_companies'",
        "next_send_at": "TEXT",
        "batch_sent_count": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if column_name not in campaign_columns:
            connection.execute(
                f"ALTER TABLE mail_campaigns ADD COLUMN {column_name} {definition}"
            )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS mail_recipients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            campaign_id INTEGER NOT NULL,

            client_id INTEGER NOT NULL,

            email TEXT NOT NULL,

            normalized_email TEXT,
            campaign_family TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT,
            last_error TEXT,

            status TEXT NOT NULL DEFAULT 'pending',

            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            updated_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (campaign_id)
                REFERENCES mail_campaigns(id)
                ON DELETE CASCADE,

            FOREIGN KEY (client_id)
                REFERENCES clients(id)
                ON DELETE CASCADE,

            UNIQUE (
                campaign_id,
                client_id,
                email
            )
        )
        """
    )

    recipient_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(mail_recipients)"
        ).fetchall()
    }
    for column_name, definition in {
        "normalized_email": "TEXT",
        "campaign_family": "TEXT",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "next_attempt_at": "TEXT",
        "last_error": "TEXT",
    }.items():
        if column_name not in recipient_columns:
            connection.execute(
                f"ALTER TABLE mail_recipients ADD COLUMN {column_name} {definition}"
            )

    connection.execute(
        "UPDATE mail_recipients SET normalized_email = LOWER(TRIM(email)) "
        "WHERE normalized_email IS NULL"
    )
    connection.execute(
        """
        UPDATE mail_recipients
        SET campaign_family = COALESCE(
            (SELECT campaign_family FROM mail_campaigns
             WHERE id = mail_recipients.campaign_id),
            'new_companies'
        )
        WHERE campaign_family IS NULL
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS mail_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            recipient_id INTEGER NOT NULL,

            run_id INTEGER,

            provider TEXT,

            provider_message_id TEXT,

            tracking_token TEXT,

            is_test INTEGER NOT NULL DEFAULT 0,

            status TEXT NOT NULL DEFAULT 'pending',

            delivery_status TEXT NOT NULL DEFAULT 'unknown',

            sent_at TEXT,

            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            updated_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (recipient_id)
                REFERENCES mail_recipients(id)
                ON DELETE CASCADE,

            FOREIGN KEY (run_id)
                REFERENCES mail_runs(id)
                ON DELETE SET NULL
        )
        """
    )

    message_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(mail_messages)"
        ).fetchall()
    }

    if "tracking_token" not in message_columns:
        connection.execute(
            """
            ALTER TABLE mail_messages
            ADD COLUMN tracking_token TEXT
            """
        )
    if "is_test" not in message_columns:
            connection.execute(
                """
                ALTER TABLE mail_messages
                ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0
                """
            )
    if "delivery_status" not in message_columns:
        connection.execute(
            """
            ALTER TABLE mail_messages
            ADD COLUMN delivery_status TEXT NOT NULL DEFAULT 'unknown'
            """
        )
    if "run_id" not in message_columns:
        connection.execute(
            """
            ALTER TABLE mail_messages
            ADD COLUMN run_id INTEGER
                REFERENCES mail_runs(id)
                ON DELETE SET NULL
            """
        )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS mail_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            campaign_id INTEGER NOT NULL,
            selection_id INTEGER NOT NULL,

            trigger TEXT NOT NULL DEFAULT 'manual'
                CHECK (trigger IN ('manual')),

            status TEXT NOT NULL DEFAULT 'running'
                CHECK (
                    status IN (
                        'running',
                        'success',
                        'partial',
                        'failed'
                    )
                ),

            started_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            finished_at TEXT,

            recipients_added INTEGER NOT NULL DEFAULT 0,
            sent_count INTEGER NOT NULL DEFAULT 0,
            delivered_count INTEGER NOT NULL DEFAULT 0,
            bounced_count INTEGER NOT NULL DEFAULT 0,
            deferred_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,

            error_text TEXT,

            FOREIGN KEY (campaign_id)
                REFERENCES mail_campaigns(id)
                ON DELETE CASCADE
        )
        """
    )

    # До разделения статусов Postfix заменял SMTP-статус
    # значениями delivered/deferred/bounced. Переносим эти значения
    # в отдельную колонку без изменения остальных данных сообщения.
    connection.execute(
        """
        UPDATE mail_messages
        SET
            delivery_status = status,
            status = 'sent'
        WHERE status IN (
            'delivered',
            'deferred',
            'bounced'
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS mail_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            message_id INTEGER NOT NULL,

            event_type TEXT NOT NULL,

            event_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            event_data TEXT,

            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (message_id)
                REFERENCES mail_messages(id)
                ON DELETE CASCADE
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_mail_campaigns_selection_id
        ON mail_campaigns(selection_id)
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS mail_audience_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            client_id INTEGER,
            source_value TEXT,
            email TEXT,
            event_type TEXT NOT NULL,
            event_data TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (campaign_id)
                REFERENCES mail_campaigns(id) ON DELETE CASCADE,
            FOREIGN KEY (client_id)
                REFERENCES clients(id) ON DELETE SET NULL
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_mail_runs_campaign_id
        ON mail_runs(campaign_id)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_mail_runs_status
        ON mail_runs(status)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_mail_runs_started_at
        ON mail_runs(started_at)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_mail_recipients_campaign_id
        ON mail_recipients(campaign_id)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_mail_recipients_client_id
        ON mail_recipients(client_id)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_mail_recipients_email
        ON mail_recipients(email)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_mail_recipients_status
        ON mail_recipients(status)
        """
    )

    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_recipients_etrn_email
        ON mail_recipients(campaign_family, normalized_email)
        WHERE campaign_family = 'etrn' AND normalized_email IS NOT NULL
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mail_audience_events_campaign
        ON mail_audience_events(campaign_id, event_type)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_mail_messages_recipient_id
        ON mail_messages(recipient_id)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_mail_messages_run_id
        ON mail_messages(run_id)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_mail_messages_provider_message_id
        ON mail_messages(provider_message_id)
        """
    )

    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            idx_mail_messages_tracking_token
        ON mail_messages(tracking_token)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_mail_messages_status
        ON mail_messages(status)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_mail_messages_delivery_status
        ON mail_messages(delivery_status)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_mail_events_message_id
        ON mail_events(message_id)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_mail_events_event_type
        ON mail_events(event_type)
        """
    )


def get_or_create_mail_campaign(
    *,
    name: str,
    selection_id: int,
) -> int:
    """
    Получить существующую почтовую кампанию
    или создать новую.

    Что делает:
    - ищет кампанию по уникальному имени;
    - если кампания уже существует, возвращает её id;
    - если кампании нет, создаёт её;
    - сохраняет selection_id, к которой относится кампания.

    Аргументы:
        name:
            Уникальное имя кампании.

        selection_id:
            Номер выборки СБИС, которая является
            источником клиентов для кампании.

    Возвращает:
        ID кампании из таблицы mail_campaigns.

    Исключения:
        ValueError:
            Если name пустое;
            если selection_id меньше 1.

        RuntimeError:
            Если после создания не удалось получить id кампании.
    """
    campaign_name = name.strip()

    if not campaign_name:
        raise ValueError(
            "Имя кампании не может быть пустым"
        )

    if selection_id < 1:
        raise ValueError(
            "selection_id должен быть больше 0"
        )

    with get_connection() as connection:
        existing = connection.execute(
            """
            SELECT
                id,
                selection_id
            FROM mail_campaigns
            WHERE name = ?
            """,
            (campaign_name,),
        ).fetchone()

        if existing is not None:
            existing_selection_id = existing[
                "selection_id"
            ]

            if existing_selection_id != selection_id:
                raise ValueError(
                    f"Кампания {campaign_name!r} уже существует "
                    f"для выборки #{existing_selection_id}, "
                    f"а передана выборка #{selection_id}"
                )

            return existing["id"]

        connection.execute(
            """
            INSERT INTO mail_campaigns (
                name,
                selection_id
            )
            VALUES (?, ?)
            """,
            (
                campaign_name,
                selection_id,
            ),
        )

        row = connection.execute(
            """
            SELECT id
            FROM mail_campaigns
            WHERE name = ?
            """,
            (campaign_name,),
        ).fetchone()

        if row is None:
            raise RuntimeError(
                "Не удалось получить id созданной кампании"
            )

        return row["id"]


def populate_mail_recipients(
    campaign_id: int,
) -> int:
    """
    Наполнить получателей почтовой кампании из связанной выборки СБИС.

    Что делает:
    - находит кампанию по campaign_id;
    - получает selection_id этой кампании;
    - выбирает клиентов, входящих в эту выборку;
    - берёт только контакты типа email;
    - добавляет каждого получателя в mail_recipients;
    - не создаёт дубли благодаря UNIQUE(
        campaign_id,
        client_id,
        email
      );
    - возвращает количество новых добавленных получателей.

    Аргументы:
        campaign_id:
            ID кампании из таблицы mail_campaigns.

    Возвращает:
        Количество новых строк, добавленных
        в таблицу mail_recipients.

    Исключения:
        ValueError:
            Если campaign_id меньше 1.

        LookupError:
            Если кампания с таким ID не найдена.
    """
    if campaign_id < 1:
        raise ValueError(
            "campaign_id должен быть больше 0"
        )

    with get_connection() as connection:
        campaign = connection.execute(
            """
            SELECT
                id,
                selection_id
            FROM mail_campaigns
            WHERE id = ?
            """,
            (campaign_id,),
        ).fetchone()

        if campaign is None:
            raise LookupError(
                f"Кампания с id={campaign_id} не найдена"
            )

        selection_id = campaign["selection_id"]

        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO mail_recipients (
                campaign_id,
                client_id,
                email
            )
            SELECT
                ?,
                c.id,
                cc.value
            FROM clients AS c

            INNER JOIN client_selections AS cs
                ON cs.client_id = c.id

            INNER JOIN client_contacts AS cc
                ON cc.client_id = c.id

            WHERE
                cs.selection_id = ?
                AND cc.contact_type = 'email'
                AND cc.value IS NOT NULL
                AND TRIM(cc.value) <> ''

                AND NOT EXISTS (
                    SELECT 1
                    FROM mail_recipients AS old_mr

                    INNER JOIN mail_messages AS old_mm
                        ON old_mm.recipient_id = old_mr.id

                    WHERE
                        LOWER(TRIM(old_mr.email))
                            = LOWER(TRIM(cc.value))

                        AND old_mm.status = 'sent'
                        AND old_mm.is_test = 0
                )
            """,
            (
                campaign_id,
                selection_id,
            ),
        )

        return cursor.rowcount




def migrate_clients_identifiers(
    connection: sqlite3.Connection,
) -> None:
    """
    Мигрировать таблицу clients на поддержку двух типов идентификаторов.

    После миграции клиент может иметь:
    - spp_uuid;
    - contractor_id;
    - либо оба идентификатора одновременно.

    Что делает:
    - проверяет текущую схему clients;
    - если spp_uuid уже допускает NULL, ничего не меняет;
    - создаёт временную таблицу clients_new;
    - переносит все существующие данные с сохранением id;
    - удаляет старую таблицу clients;
    - переименовывает clients_new в clients.

    Аргументы:
        connection:
            Открытое SQLite-соединение.

    Возвращает:
        None.
    """
    columns = {
        row["name"]: row
        for row in connection.execute(
            "PRAGMA table_info(clients)"
        ).fetchall()
    }

    spp_uuid_column = columns.get(
        "spp_uuid"
    )

    if spp_uuid_column is None:
        raise RuntimeError(
            "В таблице clients отсутствует spp_uuid"
        )

    # notnull == 0 означает, что колонка уже допускает NULL.
    if (
        spp_uuid_column["notnull"] == 0
        and "contractor_id" in columns
    ):
        return

    if "contractor_id" not in columns:
        connection.execute(
            """
            ALTER TABLE clients
            ADD COLUMN contractor_id INTEGER
            """
        )

    connection.execute(
        """
        CREATE TABLE clients_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            spp_uuid TEXT UNIQUE,

            contractor_id INTEGER UNIQUE,

            inn TEXT,
            name TEXT,

            enriched INTEGER NOT NULL DEFAULT 0,

            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            updated_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            kpp TEXT,
            ogrn TEXT,

            director_last_name TEXT,
            director_first_name TEXT,
            director_middle_name TEXT,
            director_inn TEXT,
            director_position TEXT
        )
        """
    )

    connection.execute(
        """
        INSERT INTO clients_new (
            id,
            spp_uuid,
            contractor_id,
            inn,
            name,
            enriched,
            created_at,
            updated_at,
            kpp,
            ogrn,
            director_last_name,
            director_first_name,
            director_middle_name,
            director_inn,
            director_position
        )
        SELECT
            id,
            spp_uuid,
            contractor_id,
            inn,
            name,
            enriched,
            created_at,
            updated_at,
            kpp,
            ogrn,
            director_last_name,
            director_first_name,
            director_middle_name,
            director_inn,
            director_position
        FROM clients
        """
    )

    connection.execute(
        """
        DROP TABLE clients
        """
    )

    connection.execute(
        """
        ALTER TABLE clients_new
        RENAME TO clients
        """
    )



def get_pending_mail_recipients(
    campaign_id: int,
    limit: int | None = None,
) -> list[dict[str, object]]:
    """
    Получить получателей кампании, готовых к отправке письма.

    Что делает:
    - выбирает только строки mail_recipients со статусом `pending`;
    - добавляет основные данные клиента;
    - сортирует очередь по mail_recipients.id;
    - при необходимости ограничивает количество строк.

    Аргументы:
        campaign_id:
            ID почтовой кампании.

        limit:
            Максимальное количество получателей.
            Если None, возвращается вся очередь.

    Возвращает:
        Список словарей вида:

        {
            "recipient_id": ...,
            "client_id": ...,
            "name": ...,
            "inn": ...,
            "email": ...,
            "status": ...
        }

    Исключения:
        ValueError:
            Если campaign_id меньше 1;
            если limit указан и меньше 1.
    """
    if campaign_id < 1:
        raise ValueError(
            "campaign_id должен быть больше 0"
        )

    if limit is not None and limit < 1:
        raise ValueError(
            "limit должен быть больше 0"
        )

    query = """
        SELECT
            mr.id AS recipient_id,
            mr.client_id,
            c.name,
            c.inn,
            mr.email,
            mr.status,
            c.director_first_name,
            c.director_middle_name
        FROM mail_recipients AS mr

        INNER JOIN clients AS c
            ON c.id = mr.client_id

        WHERE
            mr.campaign_id = ?
            AND mr.status = 'pending'

        ORDER BY
            mr.id
    """

    params: list[object] = [
        campaign_id
    ]

    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    with get_connection() as connection:
        rows = connection.execute(
            query,
            tuple(params),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]

def get_mail_campaign_stats(
    campaign_id: int,
) -> dict[str, int | float]:
    """
    Получить статистику почтовой кампании.

    Считает:
    - статусы получателей;
    - реально отправленные письма;
    - отдельные статусы доставки Postfix;
    - открытия;
    - клики;
    - уникальные открытия и клики;
    - процент открытия и CTR;
    - переходы по отдельным каналам.

    Тестовые письма с is_test = 1
    в боевую статистику не входят.
    """
    if campaign_id < 1:
        raise ValueError(
            "campaign_id должен быть больше 0"
        )

    recipient_statuses = (
        "pending",
        "sent",
        "opened",
        "clicked",
        "failed",
        "unsubscribed",
    )

    delivery_statuses = (
        "unknown",
        "deferred",
        "delivered",
        "bounced",
    )

    stats: dict[str, int | float] = {
        "total": 0,
        **{
            status: 0
            for status in (
                *recipient_statuses,
                *delivery_statuses,
            )
        },
        "messages_sent": 0,
        "opens_total": 0,
        "opened_unique": 0,
        "open_rate": 0.0,
        "clicks_total": 0,
        "clicked_unique": 0,
        "click_rate": 0.0,
        "click_to_open_rate": 0.0,
        "click_phone": 0,
        "click_whatsapp": 0,
        "click_telegram": 0,
        "click_max": 0,
        "click_cta_email": 0,
    }

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                status,
                COUNT(*) AS count
            FROM mail_recipients
            WHERE campaign_id = ?
            GROUP BY status
            """,
            (campaign_id,),
        ).fetchall()

        for row in rows:
            status = row["status"]
            count = int(row["count"])

            stats["total"] = int(
                stats["total"]
            ) + count

            if status in stats:
                stats[status] = count

        sent_row = connection.execute(
            """
            SELECT
                COUNT(*) AS sent
            FROM mail_messages AS mm

            INNER JOIN mail_recipients AS mr
                ON mr.id = mm.recipient_id

            WHERE
                mr.campaign_id = ?
                AND mm.is_test = 0
                AND mm.status = 'sent'
            """,
            (campaign_id,),
        ).fetchone()

        messages_sent = int(
            sent_row["sent"]
        )

        stats["messages_sent"] = messages_sent

        delivery_rows = connection.execute(
            """
            SELECT
                mm.delivery_status,
                COUNT(*) AS count
            FROM mail_messages AS mm

            INNER JOIN mail_recipients AS mr
                ON mr.id = mm.recipient_id

            WHERE
                mr.campaign_id = ?
                AND mm.is_test = 0
                AND mm.status = 'sent'

            GROUP BY mm.delivery_status
            """,
            (campaign_id,),
        ).fetchall()

        for row in delivery_rows:
            delivery_status = row["delivery_status"]

            if delivery_status in delivery_statuses:
                stats[delivery_status] = int(
                    row["count"]
                )

        event_row = connection.execute(
            """
            SELECT
                SUM(
                    CASE
                        WHEN me.event_type = 'opened'
                        THEN 1
                        ELSE 0
                    END
                ) AS opens_total,

                COUNT(
                    DISTINCT CASE
                        WHEN me.event_type = 'opened'
                        THEN mm.id
                    END
                ) AS opened_unique,

                SUM(
                    CASE
                        WHEN me.event_type = 'clicked'
                        THEN 1
                        ELSE 0
                    END
                ) AS clicks_total,

                COUNT(
                    DISTINCT CASE
                        WHEN me.event_type = 'clicked'
                        THEN mm.id
                    END
                ) AS clicked_unique

            FROM mail_messages AS mm

            INNER JOIN mail_recipients AS mr
                ON mr.id = mm.recipient_id

            LEFT JOIN mail_events AS me
                ON me.message_id = mm.id

            WHERE
                mr.campaign_id = ?
                AND mm.is_test = 0
                AND mm.status = 'sent'
            """,
            (campaign_id,),
        ).fetchone()

        opens_total = int(
            event_row["opens_total"] or 0
        )

        opened_unique = int(
            event_row["opened_unique"] or 0
        )

        clicks_total = int(
            event_row["clicks_total"] or 0
        )

        clicked_unique = int(
            event_row["clicked_unique"] or 0
        )

        stats["opens_total"] = opens_total
        stats["opened_unique"] = opened_unique
        stats["clicks_total"] = clicks_total
        stats["clicked_unique"] = clicked_unique

        channel_rows = connection.execute(
            """
            SELECT
                me.event_data,
                COUNT(DISTINCT mm.id) AS unique_messages

            FROM mail_messages AS mm

            INNER JOIN mail_recipients AS mr
                ON mr.id = mm.recipient_id

            INNER JOIN mail_events AS me
                ON me.message_id = mm.id

            WHERE
                mr.campaign_id = ?
                AND mm.is_test = 0
                AND mm.status = 'sent'
                AND me.event_type = 'clicked'

            GROUP BY me.event_data
            """,
            (campaign_id,),
        ).fetchall()

        event_data_to_metric = {
            '{"click_key":"phone"}': "click_phone",
            '{"click_key":"whatsapp"}': "click_whatsapp",
            '{"click_key":"telegram"}': "click_telegram",
            '{"click_key":"max"}': "click_max",
            '{"click_key":"cta_email"}': "click_cta_email",
        }

        for row in channel_rows:
            metric_name = event_data_to_metric.get(
                row["event_data"]
            )

            if metric_name is None:
                continue

            stats[metric_name] = int(
                row["unique_messages"]
            )

    if messages_sent > 0:
        stats["open_rate"] = round(
            opened_unique / messages_sent * 100,
            2,
        )

        stats["click_rate"] = round(
            clicked_unique / messages_sent * 100,
            2,
        )

    if opened_unique > 0:
        stats["click_to_open_rate"] = round(
            clicked_unique / opened_unique * 100,
            2,
        )

    return stats
def get_mail_campaign(
    campaign_id: int,
) -> dict[str, object]:
    """
    Получить данные почтовой кампании по её ID.

    Аргументы:
        campaign_id:
            ID кампании из таблицы mail_campaigns.

    Возвращает:
        Словарь с данными кампании:

        {
            "id": ...,
            "name": ...,
            "selection_id": ...,
            "template_name": ...,
            "status": ...
        }

    Исключения:
        ValueError:
            Если campaign_id меньше 1.

        LookupError:
            Если кампания не найдена.
    """
    if campaign_id < 1:
        raise ValueError(
            "campaign_id должен быть больше 0"
        )

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                id,
                name,
                selection_id,
                template_name,
                status,
                campaign_family,
                next_send_at,
                batch_sent_count
            FROM mail_campaigns
            WHERE id = ?
            """,
            (campaign_id,),
        ).fetchone()

    if row is None:
        raise LookupError(
            f"Кампания с id={campaign_id} не найдена"
        )

    return dict(row)


def create_mail_run(
    *,
    campaign_id: int,
    selection_id: int,
    trigger: str = "manual",
) -> int:
    """Создать отдельную запись запуска почтовой кампании."""
    if campaign_id < 1:
        raise ValueError(
            "campaign_id должен быть больше 0"
        )

    if selection_id < 0:
        raise ValueError(
            "selection_id не может быть меньше 0"
        )

    if trigger != "manual":
        raise ValueError(
            f"Неизвестный trigger mail run: {trigger!r}"
        )

    with get_connection() as connection:
        campaign = connection.execute(
            """
            SELECT selection_id
            FROM mail_campaigns
            WHERE id = ?
            """,
            (campaign_id,),
        ).fetchone()

        if campaign is None:
            raise LookupError(
                f"Кампания с id={campaign_id} не найдена"
            )

        if campaign["selection_id"] != selection_id:
            raise ValueError(
                f"Кампания #{campaign_id} относится к выборке "
                f"#{campaign['selection_id']}, а не #{selection_id}"
            )

        cursor = connection.execute(
            """
            INSERT INTO mail_runs (
                campaign_id,
                selection_id,
                trigger,
                status
            )
            VALUES (?, ?, ?, 'running')
            """,
            (
                campaign_id,
                selection_id,
                trigger,
            ),
        )

        run_id = cursor.lastrowid

        if run_id is None:
            raise RuntimeError(
                "Не удалось получить id созданного mail_runs"
            )

        return int(run_id)


def _validate_mail_run_counts(
    counts: dict[str, int],
) -> None:
    """Проверить неотрицательные счётчики запуска."""
    for field_name in MAIL_RUN_COUNTER_FIELDS:
        value = counts[field_name]

        if value < 0:
            raise ValueError(
                f"{field_name} не может быть меньше 0"
            )


def update_mail_run_counts(
    run_id: int,
    *,
    recipients_added: int,
    sent_count: int,
    delivered_count: int,
    bounced_count: int,
    deferred_count: int,
    failed_count: int,
) -> None:
    """Обновить текущие итоговые счётчики running-запуска."""
    if run_id < 1:
        raise ValueError(
            "run_id должен быть больше 0"
        )

    counts = {
        "recipients_added": recipients_added,
        "sent_count": sent_count,
        "delivered_count": delivered_count,
        "bounced_count": bounced_count,
        "deferred_count": deferred_count,
        "failed_count": failed_count,
    }
    _validate_mail_run_counts(counts)

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE mail_runs
            SET
                recipients_added = ?,
                sent_count = ?,
                delivered_count = ?,
                bounced_count = ?,
                deferred_count = ?,
                failed_count = ?
            WHERE
                id = ?
                AND status = 'running'
            """,
            (
                recipients_added,
                sent_count,
                delivered_count,
                bounced_count,
                deferred_count,
                failed_count,
                run_id,
            ),
        )

        if cursor.rowcount != 1:
            raise ValueError(
                f"mail_runs #{run_id} не найден или уже завершён"
            )


def finish_mail_run(
    run_id: int,
    *,
    status: str,
    recipients_added: int,
    sent_count: int,
    delivered_count: int,
    bounced_count: int,
    deferred_count: int,
    failed_count: int,
    error_text: str | None = None,
) -> None:
    """Завершить running-запуск и атомарно сохранить его итог."""
    if run_id < 1:
        raise ValueError(
            "run_id должен быть больше 0"
        )

    if status not in MAIL_RUN_FINAL_STATUSES:
        raise ValueError(
            f"Недопустимый финальный статус mail run: {status!r}"
        )

    counts = {
        "recipients_added": recipients_added,
        "sent_count": sent_count,
        "delivered_count": delivered_count,
        "bounced_count": bounced_count,
        "deferred_count": deferred_count,
        "failed_count": failed_count,
    }
    _validate_mail_run_counts(counts)

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE mail_runs
            SET
                status = ?,
                finished_at = CURRENT_TIMESTAMP,
                recipients_added = ?,
                sent_count = ?,
                delivered_count = ?,
                bounced_count = ?,
                deferred_count = ?,
                failed_count = ?,
                error_text = ?
            WHERE
                id = ?
                AND status = 'running'
            """,
            (
                status,
                recipients_added,
                sent_count,
                delivered_count,
                bounced_count,
                deferred_count,
                failed_count,
                error_text,
                run_id,
            ),
        )

        if cursor.rowcount != 1:
            raise ValueError(
                f"mail_runs #{run_id} не найден или уже завершён"
            )


def get_mail_run_message_counts(
    *,
    run_id: int,
    campaign_id: int,
) -> dict[str, int]:
    """Посчитать боевые сообщения, однозначно связанные с запуском."""
    if run_id < 1:
        raise ValueError(
            "run_id должен быть больше 0"
        )

    if campaign_id < 1:
        raise ValueError(
            "campaign_id должен быть больше 0"
        )

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                SUM(
                    CASE
                        WHEN mm.status = 'sent'
                        THEN 1
                        ELSE 0
                    END
                ) AS sent_count,

                SUM(
                    CASE
                        WHEN
                            mm.status = 'sent'
                            AND mm.delivery_status = 'delivered'
                        THEN 1
                        ELSE 0
                    END
                ) AS delivered_count,

                SUM(
                    CASE
                        WHEN
                            mm.status = 'sent'
                            AND mm.delivery_status = 'bounced'
                        THEN 1
                        ELSE 0
                    END
                ) AS bounced_count,

                SUM(
                    CASE
                        WHEN
                            mm.status = 'sent'
                            AND mm.delivery_status = 'deferred'
                        THEN 1
                        ELSE 0
                    END
                ) AS deferred_count,

                SUM(
                    CASE
                        WHEN mm.status = 'failed'
                        THEN 1
                        ELSE 0
                    END
                ) AS failed_count

            FROM mail_messages AS mm

            INNER JOIN mail_recipients AS mr
                ON mr.id = mm.recipient_id

            WHERE
                mm.run_id = ?
                AND mr.campaign_id = ?
                AND mm.is_test = 0
            """,
            (
                run_id,
                campaign_id,
            ),
        ).fetchone()

    return {
        field_name: int(row[field_name] or 0)
        for field_name in MAIL_RUN_COUNTER_FIELDS
        if field_name != "recipients_added"
    }


def get_recent_mail_runs(
    limit: int = 50,
) -> list[dict[str, object]]:
    """Получить последние запуски рассылки для списка будущей админки."""
    if limit < 1:
        raise ValueError(
            "limit должен быть больше 0"
        )

    with closing(get_connection()) as connection:
        rows = connection.execute(
            """
            SELECT
                mr.id AS run_id,
                mr.campaign_id,
                mc.name AS campaign_name,
                mr.selection_id,
                mr.trigger,
                mr.status,
                mr.started_at,
                mr.finished_at,
                mr.recipients_added,
                mr.sent_count,
                mr.delivered_count,
                mr.bounced_count,
                mr.deferred_count,
                mr.failed_count
            FROM mail_runs AS mr

            INNER JOIN mail_campaigns AS mc
                ON mc.id = mr.campaign_id

            ORDER BY
                mr.started_at DESC,
                mr.id DESC

            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def get_latest_mail_run_with_sent_messages(
) -> dict[str, object] | None:
    """Получить последний запуск, в котором были отправлены письма."""
    with closing(get_connection()) as connection:
        row = connection.execute(
            """
            SELECT
                mr.id AS run_id,
                mr.campaign_id,
                mc.name AS campaign_name,
                mr.selection_id,
                mr.trigger,
                mr.status,
                mr.started_at,
                mr.finished_at,
                mr.recipients_added,
                mr.sent_count,
                mr.delivered_count,
                mr.bounced_count,
                mr.deferred_count,
                mr.failed_count
            FROM mail_runs AS mr

            INNER JOIN mail_campaigns AS mc
                ON mc.id = mr.campaign_id

            WHERE mr.sent_count > 0

            ORDER BY
                mr.started_at DESC,
                mr.id DESC

            LIMIT 1
            """
        ).fetchone()

    if row is None:
        return None

    return dict(row)


def get_mail_run_details(
    run_id: int,
) -> dict[str, object]:
    """Получить сводные данные одного запуска рассылки."""
    if run_id < 1:
        raise ValueError(
            "run_id должен быть больше 0"
        )

    with closing(get_connection()) as connection:
        row = connection.execute(
            """
            SELECT
                mr.id AS run_id,
                mr.campaign_id,
                mc.name AS campaign_name,
                mr.selection_id,
                mr.trigger,
                mr.status,
                mr.started_at,
                mr.finished_at,
                mr.recipients_added,
                mr.sent_count,
                mr.delivered_count,
                mr.bounced_count,
                mr.deferred_count,
                mr.failed_count,
                mr.error_text
            FROM mail_runs AS mr

            INNER JOIN mail_campaigns AS mc
                ON mc.id = mr.campaign_id

            WHERE mr.id = ?
            """,
            (run_id,),
        ).fetchone()

    if row is None:
        raise LookupError(
            f"mail_runs #{run_id} не найден"
        )

    return dict(row)


def get_mail_run_messages(
    run_id: int,
    *,
    include_test: bool = False,
) -> list[dict[str, object]]:
    """Получить сообщения запуска с агрегатами открытий и кликов."""
    if run_id < 1:
        raise ValueError(
            "run_id должен быть больше 0"
        )

    with closing(get_connection()) as connection:
        rows = connection.execute(
            """
            SELECT
                mm.id AS message_id,
                c.name AS company_name,
                mr.email,
                mm.status AS send_status,
                mm.delivery_status,
                mm.sent_at,
                SUM(
                    CASE
                        WHEN me.event_type = 'opened'
                        THEN 1
                        ELSE 0
                    END
                ) AS opened_count,
                SUM(
                    CASE
                        WHEN me.event_type = 'clicked'
                        THEN 1
                        ELSE 0
                    END
                ) AS clicked_count,
                MAX(me.event_at) AS last_event_at
            FROM mail_messages AS mm

            INNER JOIN mail_recipients AS mr
                ON mr.id = mm.recipient_id

            INNER JOIN clients AS c
                ON c.id = mr.client_id

            LEFT JOIN mail_events AS me
                ON me.message_id = mm.id

            WHERE
                mm.run_id = ?
                AND (? = 1 OR mm.is_test = 0)

            GROUP BY
                mm.id,
                c.name,
                mr.email,
                mm.status,
                mm.delivery_status,
                mm.sent_at

            ORDER BY mm.id
            """,
            (
                run_id,
                int(include_test),
            ),
        ).fetchall()

    messages: list[dict[str, object]] = []

    for row in rows:
        message = dict(row)
        message["opened_count"] = int(
            message["opened_count"] or 0
        )
        message["clicked_count"] = int(
            message["clicked_count"] or 0
        )
        messages.append(message)

    return messages


def get_mail_run_events(
    run_id: int,
    *,
    include_test: bool = False,
) -> list[dict[str, object]]:
    """Получить хронологию событий сообщений одного запуска."""
    if run_id < 1:
        raise ValueError(
            "run_id должен быть больше 0"
        )

    with closing(get_connection()) as connection:
        rows = connection.execute(
            """
            SELECT
                me.id AS event_id,
                me.message_id,
                c.name AS company_name,
                mr.email,
                me.event_type,
                me.event_at,
                me.event_data
            FROM mail_events AS me

            INNER JOIN mail_messages AS mm
                ON mm.id = me.message_id

            INNER JOIN mail_recipients AS mr
                ON mr.id = mm.recipient_id

            INNER JOIN clients AS c
                ON c.id = mr.client_id

            WHERE
                mm.run_id = ?
                AND (? = 1 OR mm.is_test = 0)

            ORDER BY
                me.event_at,
                me.id
            """,
            (
                run_id,
                int(include_test),
            ),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def get_mail_message_details(
    message_id: int,
) -> dict[str, object] | None:
    """Получить одно сообщение с адресатом и engagement-счётчиками."""
    if message_id < 1:
        raise ValueError(
            "message_id должен быть больше 0"
        )

    with closing(get_connection()) as connection:
        row = connection.execute(
            """
            SELECT
                mm.id AS message_id,
                mm.run_id,
                mr.campaign_id,
                mc.name AS campaign_name,
                mr.client_id,
                c.name AS company_name,
                mr.email,
                mm.provider,
                mm.provider_message_id,
                mm.status AS send_status,
                mm.delivery_status,
                mm.sent_at,
                mm.tracking_token,
                SUM(
                    CASE
                        WHEN me.event_type = 'opened'
                        THEN 1
                        ELSE 0
                    END
                ) AS opened_count,
                SUM(
                    CASE
                        WHEN me.event_type = 'clicked'
                        THEN 1
                        ELSE 0
                    END
                ) AS clicked_count,
                MAX(me.event_at) AS last_event_at
            FROM mail_messages AS mm

            INNER JOIN mail_recipients AS mr
                ON mr.id = mm.recipient_id

            INNER JOIN mail_campaigns AS mc
                ON mc.id = mr.campaign_id

            INNER JOIN clients AS c
                ON c.id = mr.client_id

            LEFT JOIN mail_events AS me
                ON me.message_id = mm.id

            WHERE mm.id = ?

            GROUP BY
                mm.id,
                mm.run_id,
                mr.campaign_id,
                mc.name,
                mr.client_id,
                c.name,
                mr.email,
                mm.provider,
                mm.provider_message_id,
                mm.status,
                mm.delivery_status,
                mm.sent_at,
                mm.tracking_token
            """,
            (message_id,),
        ).fetchone()

    if row is None:
        return None

    details = dict(row)
    details["opened_count"] = int(
        details["opened_count"] or 0
    )
    details["clicked_count"] = int(
        details["clicked_count"] or 0
    )
    return details


def get_mail_message_timeline(
    message_id: int,
) -> list[dict[str, object]]:
    """Получить хронологию событий одного сообщения, включая тестовое."""
    if message_id < 1:
        raise ValueError(
            "message_id должен быть больше 0"
        )

    with closing(get_connection()) as connection:
        rows = connection.execute(
            """
            SELECT
                id AS event_id,
                event_type,
                event_at,
                event_data
            FROM mail_events
            WHERE message_id = ?
            ORDER BY
                event_at,
                id
            """,
            (message_id,),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def create_mail_message(
    *,
    recipient_id: int,
    provider: str,
    is_test: bool = False,
    run_id: int | None = None,
) -> dict:
    """
    Создать попытку отправки письма до обращения к SMTP.

    Что делает:
    - проверяет существование получателя;
    - разрешает создание попытки только для pending-получателя;
    - генерирует криптографически случайный tracking_token;
    - создаёт запись mail_messages со статусом pending;
    - возвращает ID сообщения и tracking_token.

    Аргументы:
        recipient_id:
            ID получателя из mail_recipients.

        provider:
            Имя почтового провайдера, например "smtp" или "mock".

        is_test:
            Признак тестовой учётной попытки.
            True сохраняется как 1, False как 0.

        run_id:
            ID запуска mail_runs. Для прямых запусков sender вне daily-run
            остаётся None.

    Возвращает:
        Словарь:
        {
            "message_id": int,
            "tracking_token": str,
        }

    Исключения:
        ValueError:
            Если recipient_id некорректен,
            provider пустой
            или получатель уже не находится в pending.

        RuntimeError:
            Если запись mail_messages создать не удалось.
    """
    if recipient_id < 1:
        raise ValueError(
            "recipient_id должен быть больше 0"
        )

    if run_id is not None and run_id < 1:
        raise ValueError(
            "run_id должен быть больше 0 или равен None"
        )

    clean_provider = provider.strip()

    if not clean_provider:
        raise ValueError(
            "provider не может быть пустым"
        )

    tracking_token = secrets.token_urlsafe(24)

    with get_connection() as connection:
        recipient = connection.execute(
            """
            SELECT
                id,
                status
            FROM mail_recipients
            WHERE id = ?
            """,
            (recipient_id,),
        ).fetchone()

        if recipient is None:
            raise ValueError(
                f"Получатель #{recipient_id} не найден"
            )

        recipient_status = recipient["status"]

        if recipient_status != "pending":
            raise ValueError(
                f"Получатель #{recipient_id} имеет статус "
                f"{recipient_status!r}, ожидался 'pending'"
            )

        cursor = connection.execute(
            """
            INSERT INTO mail_messages (
                recipient_id,
                run_id,
                provider,
                status,
                tracking_token,
                is_test
            )
            VALUES (?, ?, ?, 'pending', ?, ?)
            """,
            (
                recipient_id,
                run_id,
                clean_provider,
                tracking_token,
                int(is_test),
            ),
        )

        message_id = cursor.lastrowid

        if message_id is None:
            raise RuntimeError(
                "Не удалось получить id созданного mail_messages"
            )

    return {
        "message_id": int(message_id),
        "tracking_token": tracking_token,
    }


def complete_mail_message(
    *,
    message_id: int,
    provider_message_id: str | None,
    success: bool,
    error: str | None = None,
) -> None:
    """
Что делает:
- находит существующую запись mail_messages;
- меняет её статус на sent или failed;
- сохраняет provider_message_id;
- при успехе устанавливает sent_at;
- для обычной отправки обновляет статус mail_recipients;
- для is_test=1 статус реального mail_recipients не изменяет;
- создаёт событие sent или failed в mail_events.

    Аргументы:
        message_id:
            ID попытки из mail_messages.

        provider_message_id:
            Идентификатор письма у SMTP/provider.
            Может быть None при ошибке отправки.

        success:
            True, если SMTP/provider принял письмо.

        error:
            Описание ошибки отправки.
            Используется для события failed.

    Возвращает:
        None.

    Исключения:
        ValueError:
            Если message_id некорректен,
            запись не существует
            или попытка уже завершена.
    """
    if message_id < 1:
        raise ValueError(
            "message_id должен быть больше 0"
        )

    message_status = (
        "sent"
        if success
        else "failed"
    )

    event_type = message_status

    with get_connection() as connection:
        message = connection.execute(
            """
            SELECT
                id,
                recipient_id,
                status,
                is_test
            FROM mail_messages
            WHERE id = ?
            """,
            (message_id,),
        ).fetchone()

        if message is None:
            raise ValueError(
                f"mail_messages #{message_id} не найден"
            )

        if message["status"] != "pending":
            raise ValueError(
                f"mail_messages #{message_id} уже имеет статус "
                f"{message['status']!r}"
            )

        recipient_id = int(
            message["recipient_id"]
        )
        is_test = bool(
            message["is_test"]
        )

        if success:
            connection.execute(
                """
                UPDATE mail_messages
                SET
                    provider_message_id = ?,
                    status = 'sent',
                    sent_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    provider_message_id,
                    message_id,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE mail_messages
                SET
                    provider_message_id = ?,
                    status = 'failed',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    provider_message_id,
                    message_id,
                ),
            )

        if not is_test:
            connection.execute(
                """
                UPDATE mail_recipients
                SET
                    status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    message_status,
                    recipient_id,
                ),
            )

        connection.execute(
            """
            INSERT INTO mail_events (
                message_id,
                event_type,
                event_data
            )
            VALUES (?, ?, ?)
            """,
            (
                message_id,
                event_type,
                error,
            ),
        )

def confirm_mail_send(
    *,
    recipient_id: int,
    provider: str,
    provider_message_id: str | None,
    success: bool,
) -> int:
    """
    Зафиксировать результат отправки письма.

    Что делает:
    - проверяет существование получателя;
    - создаёт запись в mail_messages;
    - меняет статус mail_recipients;
    - выполняет обе операции в одной транзакции.

    При успешной отправке:
    - mail_messages.status = 'sent';
    - mail_recipients.status = 'sent';
    - sent_at заполняется текущим временем.

    При ошибке:
    - mail_messages.status = 'failed';
    - mail_recipients.status = 'failed';
    - sent_at остаётся NULL.

    Аргументы:
        recipient_id:
            ID получателя из mail_recipients.

        provider:
            Имя почтового провайдера.

        provider_message_id:
            Идентификатор сообщения у провайдера.
            Может быть None при ошибке.

        success:
            True при успешной отправке,
            False при ошибке.

    Возвращает:
        ID созданной записи mail_messages.

    Исключения:
        ValueError:
            Если recipient_id меньше 1;
            если provider пустой.

        LookupError:
            Если получатель не найден.
    """
    if recipient_id < 1:
        raise ValueError(
            "recipient_id должен быть больше 0"
        )

    provider_name = provider.strip()

    if not provider_name:
        raise ValueError(
            "provider не может быть пустым"
        )

    message_status = (
        "sent"
        if success
        else "failed"
    )

    with get_connection() as connection:
        recipient = connection.execute(
            """
            SELECT
                id,
                status
            FROM mail_recipients
            WHERE id = ?
            """,
            (recipient_id,),
        ).fetchone()

        if recipient is None:
            raise LookupError(
                f"Получатель с id={recipient_id} не найден"
            )
        if recipient["status"] != "pending":
            raise ValueError(
                f"Получатель id={recipient_id} уже обработан: "
                f"status={recipient['status']!r}"
            )

        cursor = connection.execute(
            """
            INSERT INTO mail_messages (
                recipient_id,
                provider,
                provider_message_id,
                status,
                sent_at
            )
            VALUES (
                ?,
                ?,
                ?,
                ?,
                CASE
                    WHEN ? = 'sent'
                    THEN CURRENT_TIMESTAMP
                    ELSE NULL
                END
            )
            """,
            (
                recipient_id,
                provider_name,
                provider_message_id,
                message_status,
                message_status,
            ),
        )

        connection.execute(
            """
            UPDATE mail_recipients
            SET
                status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                message_status,
                recipient_id,
            ),
        )

        return cursor.lastrowid



def get_failed_mail_recipients(
    campaign_id: int,
    limit: int | None = None,
) -> list[dict[str, object]]:
    """
    Получить получателей кампании с неудачной отправкой.

    Что делает:
    - выбирает только mail_recipients со статусом `failed`;
    - добавляет основные данные клиента;
    - сортирует записи по mail_recipients.id;
    - при необходимости ограничивает количество строк.

    Аргументы:
        campaign_id:
            ID почтовой кампании.

        limit:
            Максимальное количество получателей.
            Если None, возвращаются все failed-записи.

    Возвращает:
        Список словарей с данными получателей.

    Исключения:
        ValueError:
            Если campaign_id меньше 1;
            если limit указан и меньше 1.
    """
    if campaign_id < 1:
        raise ValueError(
            "campaign_id должен быть больше 0"
        )

    if limit is not None and limit < 1:
        raise ValueError(
            "limit должен быть больше 0"
        )

    query = """
        SELECT
            mr.id AS recipient_id,
            mr.client_id,
            c.name,
            c.inn,
            mr.email,
            mr.status
        FROM mail_recipients AS mr

        INNER JOIN clients AS c
            ON c.id = mr.client_id

        WHERE
            mr.campaign_id = ?
            AND mr.status = 'failed'

        ORDER BY
            mr.id
    """

    params: list[object] = [
        campaign_id
    ]

    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    with get_connection() as connection:
        rows = connection.execute(
            query,
            tuple(params),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def retry_failed_mail_recipient(
    recipient_id: int,
) -> None:
    """
    Вернуть failed-получателя в очередь на повторную отправку.

    Что делает:
    - находит mail_recipients по recipient_id;
    - разрешает retry только для статуса `failed`;
    - меняет статус обратно на `pending`;
    - обновляет updated_at.

    Аргументы:
        recipient_id:
            ID получателя из mail_recipients.

    Исключения:
        ValueError:
            Если recipient_id меньше 1;
            если текущий статус получателя не `failed`.

        LookupError:
            Если получатель не найден.
    """
    if recipient_id < 1:
        raise ValueError(
            "recipient_id должен быть больше 0"
        )

    with get_connection() as connection:
        recipient = connection.execute(
            """
            SELECT
                id,
                status
            FROM mail_recipients
            WHERE id = ?
            """,
            (recipient_id,),
        ).fetchone()

        if recipient is None:
            raise LookupError(
                f"Получатель с id={recipient_id} не найден"
            )

        if recipient["status"] != "failed":
            raise ValueError(
                f"Получатель id={recipient_id} нельзя "
                f"вернуть в retry: "
                f"status={recipient['status']!r}"
            )

        attempt_count = get_mail_recipient_attempt_count(
            recipient_id
        )

        if attempt_count >= MAX_MAIL_SEND_ATTEMPTS:
            raise ValueError(
                f"Получатель id={recipient_id} достиг "
                f"максимального количества попыток: {attempt_count}"
            )

        connection.execute(
            """
            UPDATE mail_recipients
            SET
                status = 'pending',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (recipient_id,),
        )


def get_mail_recipient_attempt_count(
    recipient_id: int,
) -> int:
    """
    Получить количество попыток отправки для получателя.

    Что делает:
    - считает все записи mail_messages,
      связанные с recipient_id;
    - учитывает как успешные, так и неуспешные попытки.

    Аргументы:
        recipient_id:
            ID получателя из mail_recipients.

    Возвращает:
        Количество попыток отправки.

    Исключения:
        ValueError:
            Если recipient_id меньше 1.
    """
    if recipient_id < 1:
        raise ValueError(
            "recipient_id должен быть больше 0"
        )

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS count
            FROM mail_messages
            WHERE recipient_id = ?
            """,
            (recipient_id,),
        ).fetchone()

    return int(
        row["count"]
    )



def record_mail_open(
    tracking_token: str,
) -> bool:
    """
    Зафиксировать открытие письма по tracking_token.

    Что делает:
    - принимает непрозрачный tracking_token из URL пикселя;
    - ищет соответствующую запись в mail_messages;
    - если письмо найдено, добавляет событие `opened`
      в таблицу mail_events;
    - если token неизвестен или пустой, ничего не меняет.

    Аргументы:
        tracking_token:
            Уникальный tracking token из mail_messages.

    Возвращает:
        True:
            Письмо найдено и событие `opened` записано.

        False:
            Token пустой или соответствующее письмо не найдено.

    Примечание:
        Повторные загрузки пикселя сейчас сохраняются как отдельные
        события. Для аналитики уникальное открытие позже можно считать
        как наличие хотя бы одного `opened` для конкретного message_id.
    """
    token = tracking_token.strip()

    if not token:
        return False

    with get_connection() as connection:
        message = connection.execute(
            """
            SELECT id
            FROM mail_messages
            WHERE tracking_token = ?
            """,
            (token,),
        ).fetchone()

        if message is None:
            return False

        connection.execute(
            """
            INSERT INTO mail_events (
                message_id,
                event_type
            )
            VALUES (?, ?)
            """,
            (
                message["id"],
                "opened",
            ),
        )

        return True


def record_mail_click(
    *,
    tracking_token: str,
    click_key: str,
) -> bool:
    """
    Зафиксировать переход по ссылке из письма.

    Что делает:
    - принимает tracking_token конкретного mail_messages;
    - принимает стабильный идентификатор ссылки click_key;
    - ищет письмо по tracking_token;
    - сохраняет событие `clicked` в mail_events;
    - помещает click_key в event_data как JSON;
    - неизвестный или пустой token не приводит к ошибке.

    Аргументы:
        tracking_token:
            Уникальный tracking token из mail_messages.

        click_key:
            Стабильный идентификатор ссылки, например:
            - cta_email;
            - phone;
            - whatsapp;
            - telegram;
            - max.

    Возвращает:
        True:
            Письмо найдено и событие `clicked` записано.

        False:
            tracking_token или click_key пустой,
            либо письмо по token не найдено.

    Формат event_data:
        {"click_key": "whatsapp"}

    Примечание:
        Каждый фактический HTTP-переход сохраняется отдельным событием.
        Уникальный клик позже считается аналитическим запросом
        как наличие хотя бы одного события clicked для message_id.
    """
    token = tracking_token.strip()
    clean_click_key = click_key.strip()

    if not token or not clean_click_key:
        return False

    event_data = json.dumps(
        {
            "click_key": clean_click_key,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    with get_connection() as connection:
        message = connection.execute(
            """
            SELECT id
            FROM mail_messages
            WHERE tracking_token = ?
            """,
            (token,),
        ).fetchone()

        if message is None:
            return False

        connection.execute(
            """
            INSERT INTO mail_events (
                message_id,
                event_type,
                event_data
            )
            VALUES (?, ?, ?)
            """,
            (
                message["id"],
                "clicked",
                event_data,
            ),
        )

        return True

def record_mail_delivery_status(
    *,
    provider_message_id: str,
    delivery_status: str,
    queue_id: str | None = None,
    dsn: str | None = None,
    detail: str | None = None,
) -> bool:
    """
    Зафиксировать результат доставки письма по данным Postfix.

    Поддерживаемые статусы:
    - delivered — удалённый SMTP-сервер принял письмо;
    - deferred — временная ошибка доставки;
    - bounced — окончательный отказ в доставке.

    Функция:
    - ищет mail_messages по provider_message_id;
    - не обрабатывает тестовые сообщения;
    - обновляет только mail_messages.delivery_status;
    - mail_recipients.status не изменяет;
    - сохраняет событие в mail_events;
    - повторная запись того же статуса не создаёт дубль события.

    Возвращает:
        True — письмо найдено;
        False — письмо не найдено, является тестовым
        или не имеет SMTP-статус sent.
    """
    message_id_value = provider_message_id.strip()

    if not message_id_value:
        return False

    allowed_statuses = {
        "delivered",
        "deferred",
        "bounced",
    }

    if delivery_status not in allowed_statuses:
        raise ValueError(
            f"Неизвестный delivery_status: {delivery_status!r}"
        )

    event_data = json.dumps(
        {
            "queue_id": queue_id,
            "dsn": dsn,
            "detail": detail,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    with get_connection() as connection:
        message = connection.execute(
            """
            SELECT
                id,
                status,
                delivery_status,
                is_test
            FROM mail_messages
            WHERE provider_message_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (message_id_value,),
        ).fetchone()

        if message is None:
            return False

        if message["is_test"]:
            return False

        # Postfix может сообщить результат доставки только для письма,
        # которое Python успешно передал нашему SMTP.
        if message["status"] != "sent":
            return False

        # delivered и bounced являются финальными состояниями.
        # Повторный проход по mail.log не должен откатывать их назад.
        if message["delivery_status"] in {
            "delivered",
            "bounced",
        }:
            return True

        # Если этот статус уже был записан, повторное сканирование
        # логов не должно создавать дополнительное событие.
        if message["delivery_status"] == delivery_status:
            return True

        connection.execute(
            """
            UPDATE mail_messages
            SET
                delivery_status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                delivery_status,
                message["id"],
            ),
        )

        connection.execute(
            """
            INSERT INTO mail_events (
                message_id,
                event_type,
                event_data
            )
            VALUES (?, ?, ?)
            """,
            (
                message["id"],
                delivery_status,
                event_data,
            ),
        )

        return True



def ensure_default_mail_campaign() -> int:
    """
    Гарантировать наличие основной почтовой кампании проекта.

    Что делает:
    - проверяет наличие кампании `new_companies_daily`;
    - если кампании нет, создаёт её для выборки СБИС #5984;
    - гарантирует использование шаблона `new_companies`;
    - не создаёт дубликаты при повторных запусках;
    - возвращает ID основной кампании.

    Возвращает:
        ID основной почтовой кампании.

    Исключения:
        ValueError:
            Если кампания с именем `new_companies_daily`
            уже существует, но привязана к другой выборке.
    """
    campaign_id = get_or_create_mail_campaign(
        name="new_companies_daily",
        selection_id=5984,
    )

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE mail_campaigns
            SET
                template_name = ?,
                campaign_family = 'new_companies'
            WHERE id = ?
              AND (
                    template_name IS NULL
                    OR TRIM(template_name) = ''
                  )
            """,
            (
                "new_companies",
                campaign_id,
            ),
        )

    return campaign_id


def upsert_client_by_inn_lookup(
    *,
    inn: str,
    spp_uuid: str,
) -> int:
    """
    Создать или обновить клиента, найденного через поиск по ИНН.

    Используется для сценария, когда организация получена
    не из пользовательской выборки СБИС, а через
    Contractor.SearchSuggest.

    Что делает:
    - нормализует ИНН и SppUuid;
    - сначала ищет клиента по SppUuid;
    - если по SppUuid клиент не найден, ищет по точному ИНН;
    - если клиент существует, обновляет его SppUuid и ИНН;
    - если клиента нет, создаёт новую запись в clients;
    - не создаёт связь в client_selections;
    - не меняет enriched;
    - возвращает внутренний clients.id.

    Аргументы:
        inn:
            ИНН организации, состоящий из 10 или 12 цифр.

        spp_uuid:
            SppUuid организации, найденный через
            Contractor.SearchSuggest.

    Возвращает:
        ID записи clients.

    Исключения:
        ValueError:
            Если ИНН или SppUuid пустые либо некорректные.
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

    clean_spp_uuid = spp_uuid.strip()

    if not clean_spp_uuid:
        raise ValueError(
            "spp_uuid не может быть пустым"
        )

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id
            FROM clients
            WHERE spp_uuid = ?
            LIMIT 1
            """,
            (clean_spp_uuid,),
        ).fetchone()

        if row is None:
            row = connection.execute(
                """
                SELECT id
                FROM clients
                WHERE inn = ?
                ORDER BY id
                LIMIT 1
                """,
                (clean_inn,),
            ).fetchone()

        if row is not None:
            client_id = int(row["id"])

            connection.execute(
                """
                UPDATE clients
                SET
                    spp_uuid = ?,
                    inn = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    clean_spp_uuid,
                    clean_inn,
                    client_id,
                ),
            )

            return client_id

        cursor = connection.execute(
            """
            INSERT INTO clients (
                spp_uuid,
                inn
            )
            VALUES (?, ?)
            """,
            (
                clean_spp_uuid,
                clean_inn,
            ),
        )

        client_id = cursor.lastrowid

        if client_id is None:
            raise RuntimeError(
                "Не удалось создать запись клиента"
            )

        return int(client_id)
