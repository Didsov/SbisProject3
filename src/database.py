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
import secrets
import os

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_DIR = PROJECT_ROOT / "data"
DATABASE_FILE = Path(
    os.getenv(
        "PROJECT_DB_PATH",
        DATABASE_DIR / "project.db",
    )
)
MAX_MAIL_SEND_ATTEMPTS = 3

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

    - mail_messages:
        Хранит факт попытки отправки письма
        конкретному получателю и идентификатор
        сообщения почтового провайдера.

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

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS mail_recipients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            campaign_id INTEGER NOT NULL,

            client_id INTEGER NOT NULL,

            email TEXT NOT NULL,

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

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS mail_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            recipient_id INTEGER NOT NULL,

            provider TEXT,

            provider_message_id TEXT,

            tracking_token TEXT,

            status TEXT NOT NULL DEFAULT 'pending',

            sent_at TEXT,

            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            updated_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (recipient_id)
                REFERENCES mail_recipients(id)
                ON DELETE CASCADE
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
        CREATE INDEX IF NOT EXISTS
            idx_mail_messages_recipient_id
        ON mail_messages(recipient_id)
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
) -> dict[str, int]:
    """
    Получить краткую статистику получателей кампании.

    Аргументы:
        campaign_id:
            ID почтовой кампании.

    Возвращает:
        Словарь со счётчиками:

        {
            "total": ...,
            "pending": ...,
            "sent": ...,
            "delivered": ...,
            "opened": ...,
            "clicked": ...,
            "bounced": ...,
            "failed": ...,
            "unsubscribed": ...
        }

    Исключения:
        ValueError:
            Если campaign_id меньше 1.
    """
    if campaign_id < 1:
        raise ValueError(
            "campaign_id должен быть больше 0"
        )

    statuses = (
        "pending",
        "sent",
        "delivered",
        "opened",
        "clicked",
        "bounced",
        "failed",
        "unsubscribed",
    )

    stats = {
        "total": 0,
        **{
            status: 0
            for status in statuses
        },
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
        count = row["count"]

        stats["total"] += count

        if status in stats:
            stats[status] = count

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
                status
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



def create_mail_message(
    *,
    recipient_id: int,
    provider: str,
    provider_message_id: str | None,
    status: str,
) -> int:
    """
    Создать запись о попытке отправки письма.

    Аргументы:
        recipient_id:
            ID получателя из mail_recipients.

        provider:
            Имя почтового провайдера, например:
            - mock;
            - mailgun;
            - resend.

        provider_message_id:
            Идентификатор сообщения у провайдера.
            Может быть None при ошибке отправки.

        status:
            Статус сообщения, например:
            - sent;
            - failed.

    Возвращает:
        ID созданной записи mail_messages.

    Исключения:
        ValueError:
            Если recipient_id меньше 1;
            если provider или status пустые.
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

    message_status = status.strip()

    if not message_status:
        raise ValueError(
            "status не может быть пустым"
        )

    with get_connection() as connection:
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

        return cursor.lastrowid

def create_mail_message(
    *,
    recipient_id: int,
    provider: str,
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
                provider,
                status,
                tracking_token
            )
            VALUES (?, ?, 'pending', ?)
            """,
            (
                recipient_id,
                clean_provider,
                tracking_token,
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
    Завершить ранее созданную попытку отправки.

    Что делает:
    - находит существующую запись mail_messages;
    - меняет её статус на sent или failed;
    - сохраняет provider_message_id;
    - при успехе устанавливает sent_at;
    - обновляет статус mail_recipients;
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
                status
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
            SET template_name = ?
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