"""
Синхронизация статусов доставки SMTP-писем по журналу Postfix.

Назначение модуля:
- получить из SQLite боевые SMTP-сообщения, ожидающие результата доставки;
- найти их provider_message_id в журнале Postfix;
- определить внутренний Postfix queue ID;
- найти результат доставки по queue ID;
- преобразовать статусы Postfix в статусы проекта;
- сохранить delivered / bounced / deferred через record_mail_delivery_status().

Поддерживаемые соответствия:
- Postfix status=sent     -> delivered
- Postfix status=bounced  -> bounced
- Postfix status=deferred -> deferred

Пример запуска на production VPS:

    python -m src.mailing.postfix_delivery

Или с явным путём:

    python -m src.mailing.postfix_delivery \
        --log /var/log/mail.log

Можно ограничить количество проверяемых сообщений:

    python -m src.mailing.postfix_delivery \
        --limit 100
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from src.database import (
    get_connection,
    record_mail_delivery_status,
)


DEFAULT_MAIL_LOG = Path(
    "/var/log/mail.log"
)

MESSAGE_ID_RE = re.compile(
    r"""
    (?P<queue_id>[A-F0-9]+):
    \s+
    message-id=
    (?P<message_id><[^>]+>)
    """,
    re.VERBOSE,
)

DELIVERY_RE = re.compile(
    r"""
    (?P<queue_id>[A-F0-9]+):
    .*?
    \bto=<(?P<recipient>[^>]+)>
    .*?
    \bdsn=(?P<dsn>[0-9.]+)
    .*?
    \bstatus=(?P<status>sent|bounced|deferred)
    \s*
    (?P<detail>\(.*\))?
    """,
    re.VERBOSE,
)


POSTFIX_STATUS_MAP = {
    "sent": "delivered",
    "bounced": "bounced",
    "deferred": "deferred",
}


def parse_arguments() -> argparse.Namespace:
    """
    Разобрать параметры командной строки.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Синхронизировать delivered/bounced/deferred "
            "по журналу Postfix."
        )
    )

    parser.add_argument(
        "--log",
        type=Path,
        default=DEFAULT_MAIL_LOG,
        help=(
            "Путь к mail.log. "
            "По умолчанию: /var/log/mail.log"
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Максимальное количество сообщений из БД "
            "для проверки."
        ),
    )

    arguments = parser.parse_args()

    if (
        arguments.limit is not None
        and arguments.limit < 1
    ):
        parser.error(
            "--limit должен быть больше 0"
        )

    return arguments


def load_log_lines(
    log_path: Path,
) -> list[str]:
    """
    Прочитать журнал Postfix.

    Ошибочные байты заменяются, чтобы единичная битая строка
    не останавливала весь анализ.
    """
    if not log_path.is_file():
        raise FileNotFoundError(
            f"Журнал Postfix не найден: {log_path}"
        )

    return log_path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()


def build_message_id_queue_map(
    lines: list[str],
) -> dict[str, str]:
    """
    Построить соответствие:

        provider_message_id -> queue_id
    """
    result: dict[str, str] = {}

    for line in lines:
        match = MESSAGE_ID_RE.search(
            line
        )

        if match is None:
            continue

        message_id = match.group(
            "message_id"
        ).strip()

        queue_id = match.group(
            "queue_id"
        ).strip()

        result[message_id] = queue_id

    return result


def build_delivery_map(
    lines: list[str],
) -> dict[str, dict[str, str]]:
    """
    Получить последний известный delivery-статус
    для каждого queue ID.

    Последняя строка побеждает, что важно для deferred:
    сначала может быть временная ошибка, а позже sent.
    """
    result: dict[str, dict[str, str]] = {}

    for line in lines:
        match = DELIVERY_RE.search(
            line
        )

        if match is None:
            continue

        queue_id = match.group(
            "queue_id"
        ).strip()

        postfix_status = match.group(
            "status"
        ).strip()

        delivery_status = POSTFIX_STATUS_MAP[
            postfix_status
        ]

        detail = match.group(
            "detail"
        )

        if detail is not None:
            detail = detail.strip()

            if (
                detail.startswith("(")
                and detail.endswith(")")
            ):
                detail = detail[1:-1]

        result[queue_id] = {
            "delivery_status": delivery_status,
            "dsn": match.group(
                "dsn"
            ).strip(),
            "recipient": match.group(
                "recipient"
            ).strip(),
            "detail": detail or "",
        }

    return result


def get_messages_for_delivery_check(
    *,
    limit: int | None = None,
) -> list[dict[str, object]]:
    """
    Получить боевые SMTP-сообщения, для которых
    ещё имеет смысл проверять результат доставки.

    Проверяем:
    - provider='smtp';
    - is_test=0;
    - provider_message_id заполнен;
    - status='sent' или 'deferred'.

    delivered и bounced повторно анализировать не нужно.
    """
    query = """
        SELECT
            id,
            recipient_id,
            provider_message_id,
            status
        FROM mail_messages
        WHERE
            provider = 'smtp'
            AND is_test = 0
            AND provider_message_id IS NOT NULL
            AND TRIM(provider_message_id) <> ''
            AND status IN (
                'sent',
                'deferred'
            )
        ORDER BY id
    """

    params: list[object] = []

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


def synchronize_delivery_statuses(
    *,
    log_path: Path,
    limit: int | None = None,
) -> dict[str, int]:
    """
    Синхронизировать статусы доставки с SQLite.

    Возвращает краткую статистику текущего запуска.
    """
    lines = load_log_lines(
        log_path
    )

    message_id_queue_map = (
        build_message_id_queue_map(
            lines
        )
    )

    delivery_map = build_delivery_map(
        lines
    )

    messages = get_messages_for_delivery_check(
        limit=limit
    )

    stats = {
        "checked": 0,
        "matched_queue": 0,
        "delivered": 0,
        "bounced": 0,
        "deferred": 0,
        "not_found": 0,
    }

    for message in messages:
        stats["checked"] += 1

        provider_message_id = message[
            "provider_message_id"
        ]

        if not isinstance(
            provider_message_id,
            str,
        ):
            stats["not_found"] += 1
            continue

        provider_message_id = (
            provider_message_id.strip()
        )

        queue_id = (
            message_id_queue_map.get(
                provider_message_id
            )
        )

        if queue_id is None:
            stats["not_found"] += 1

            print(
                f"mail_messages.id={message['id']}: "
                "queue ID не найден"
            )

            continue

        stats["matched_queue"] += 1

        delivery = delivery_map.get(
            queue_id
        )

        if delivery is None:
            stats["not_found"] += 1

            print(
                f"mail_messages.id={message['id']}: "
                f"queue_id={queue_id}, "
                "delivery status пока не найден"
            )

            continue

        delivery_status = str(
            delivery[
                "delivery_status"
            ]
        )

        saved = record_mail_delivery_status(
            provider_message_id=provider_message_id,
            delivery_status=delivery_status,
            queue_id=queue_id,
            dsn=str(
                delivery["dsn"]
            ),
            detail=str(
                delivery["detail"]
            ),
        )

        if not saved:
            print(
                f"mail_messages.id={message['id']}: "
                "статус найден, но запись в БД "
                "не выполнена"
            )

            continue

        stats[delivery_status] += 1

        print(
            f"mail_messages.id={message['id']}: "
            f"{delivery_status} "
            f"(queue={queue_id}, "
            f"dsn={delivery['dsn']}, "
            f"to={delivery['recipient']})"
        )

    return stats


def main() -> None:
    """
    CLI-точка входа.
    """
    arguments = parse_arguments()

    stats = synchronize_delivery_statuses(
        log_path=arguments.log,
        limit=arguments.limit,
    )

    print()
    print("Postfix delivery sync завершён.")
    print(
        f"Проверено: {stats['checked']}"
    )
    print(
        f"Queue ID найден: "
        f"{stats['matched_queue']}"
    )
    print(
        f"Delivered: {stats['delivered']}"
    )
    print(
        f"Bounced: {stats['bounced']}"
    )
    print(
        f"Deferred: {stats['deferred']}"
    )
    print(
        f"Без результата: "
        f"{stats['not_found']}"
    )


if __name__ == "__main__":
    main()