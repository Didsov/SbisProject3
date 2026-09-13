"""
Синхронизация статусов доставки SMTP-писем по журналу Postfix.

Назначение модуля:
- получить из SQLite боевые SMTP-сообщения, ожидающие результата доставки;
- найти их provider_message_id в журнале Postfix;
- определить внутренний Postfix queue ID;
- найти результат доставки по queue ID;
- преобразовать статусы Postfix в статусы проекта;
- сохранить историю MX, статусы и состояние ограничений в SQLite.

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
import hashlib
import json
import time
from contextlib import closing
from datetime import datetime
import re
from pathlib import Path

from src.database import (
    get_connection,
    initialize_database,
)


DEFAULT_MAIL_LOG = Path(
    "/var/log/mail.log"
)

MESSAGE_ID_RE = re.compile(
    r"""
    (?P<queue_id>[A-Za-z0-9]+):
    \s+
    message-id=
    (?P<message_id><[^>]+>)
    """,
    re.VERBOSE,
)

DELIVERY_RE = re.compile(
    r"""
    postfix/smtp\[\d+\]:
    \s+
    (?P<queue_id>[A-Za-z0-9]+):
    \s+
    to=<(?P<recipient>[^>]+)>
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

    parser.add_argument("--watch", action="store_true", help="Повторять проверку и heartbeat каждые 5 секунд")
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
    - SMTP status='sent';
    - delivery_status='unknown' или 'deferred'.

    delivered и bounced повторно анализировать не нужно.
    """
    query = """
        SELECT
            id,
            recipient_id,
            provider_message_id,
            status,
            delivery_status
        FROM mail_messages
        WHERE
            provider = 'smtp'
            AND is_test = 0
            AND provider_message_id IS NOT NULL
            AND TRIM(provider_message_id) <> ''
            AND status = 'sent'
            AND delivery_status IN (
                'unknown',
                'deferred'
            )
        ORDER BY
            CASE
                WHEN delivery_status = 'unknown' THEN 0
                ELSE 1
            END,
            id DESC
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


def classify_result(status: str, dsn: str, detail: str) -> str | None:
    if status == "delivered":
        return None
    if status == "deferred" or dsn.startswith("4."):
        return "temporary_failure"
    if re.search(r"spam message rejected|(?:reject\w*|block\w*|detected|classified).*\bspam\b|"
                 r"\bspam\b.*(?:reject\w*|block\w*|detected)|\bunsolicited (?:mail|message)", detail, re.I):
        return "spam_rejected"
    if dsn == "5.1.1" or re.search(
        r"user (?:not found|unknown)|unknown (?:user|recipient)|no such (?:user|mailbox)|"
        r"recipient (?:not found|does not exist)|mailbox (?:not found|does not exist)", detail, re.I
    ):
        return "invalid_recipient"
    return "other_failure"


def event_timestamp(line: str, reference: float) -> float:
    first = line.split(" ", 1)[0]
    try:
        value = datetime.fromisoformat(first.replace("Z", "+00:00"))
        return value.timestamp()
    except ValueError:
        pass
    # Traditional syslog timestamps are in the host's local timezone, no year.
    year = datetime.fromtimestamp(reference).year
    candidates = []
    for y in (year - 1, year, year + 1):
        try:
            candidates.append(datetime.strptime(f"{y} {line[:15]}", "%Y %b %d %H:%M:%S").timestamp())
        except ValueError:
            pass
    if not candidates:
        raise ValueError("Postfix event has no valid timestamp")
    return min(candidates, key=lambda value: abs(value-reference))


def synchronize_delivery_statuses(*, log_path: Path, limit: int | None = None) -> dict[str, int]:
    from src.mailing.control import evaluate, provider_for
    # Invalidate first. A partial scan/error must never advertise a healthy tail.
    with closing(get_connection()) as c, c:
        c.execute("UPDATE mail_control SET heartbeat=NULL WHERE id=1")
    before = log_path.stat()
    # Include the immediate rotated file to bridge rotation between polls.
    rotated = log_path.with_name(log_path.name + ".1")
    sources = ([rotated] if rotated.is_file() else []) + [log_path]
    lines = []
    for source in sources:
        reference = source.stat().st_mtime
        lines.extend((line, reference) for line in load_log_lines(source))
    stats = dict(checked=0, matched_queue=0, delivered=0, bounced=0, deferred=0, not_found=0)
    with closing(get_connection()) as c, c:
        for line, reference in lines:
            match = MESSAGE_ID_RE.search(line)
            if match:
                c.execute("INSERT OR IGNORE INTO mail_queue_ids VALUES(?,?,?)", (
                    match['queue_id'], match['message_id'], event_timestamp(line, reference)))
    processed = 0
    deliveries = []
    for line, reference in lines:
        match = DELIVERY_RE.search(line)
        if match is not None:
            deliveries.append((event_timestamp(line, reference),
                               hashlib.sha256(line.encode('utf-8')).hexdigest(), line, match))
    for when, key, line, match in sorted(deliveries, key=lambda item: (item[0], item[1])):
        with closing(get_connection()) as c:
            if c.execute("SELECT 1 FROM mail_delivery_events WHERE event_key=?", (key,)).fetchone():
                continue
            mapping = c.execute("""SELECT provider_message_id FROM mail_queue_ids
                WHERE queue_id=? AND event_time<=? ORDER BY event_time DESC LIMIT 1""",
                (match['queue_id'], when)).fetchone()
            if not mapping:
                continue
            message = c.execute("""SELECT m.*, COALESCE(m.smtp_recipient_email,r.email) AS actual_email
                FROM mail_messages m JOIN mail_recipients r ON r.id=m.recipient_id
                WHERE m.provider_message_id=? AND m.is_test=0 AND m.provider='smtp'
                ORDER BY m.id DESC LIMIT 1""", (mapping[0],)).fetchone()
        if message is None or message['actual_email'].lower() != match['recipient'].lower():
            continue
        if limit is not None and processed >= limit:
            # A deliberately limited scan cannot provide a healthy heartbeat.
            return stats
        processed += 1
        provider = message['mx_provider'] or provider_for(message['actual_email'])
        if not provider:
            raise RuntimeError('Cannot resolve provider for delivery event')
        status = POSTFIX_STATUS_MAP[match['status']]
        detail = (match['detail'] or '').removeprefix('(').removesuffix(')')
        reason = classify_result(status, match['dsn'], detail)
        relay = re.search(r"\brelay=([^,\s]+)", line)
        with closing(get_connection()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            inserted = c.execute("""INSERT OR IGNORE INTO mail_delivery_events
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (key, message['id'], match['recipient'],
                match['queue_id'], when, status, reason, match['dsn'],
                relay[1] if relay else '', detail, line)).rowcount
            if not inserted:
                continue
            # Legacy messages are retained, but cannot enter a new observation period.
            c.execute("UPDATE mail_messages SET mx_provider=? WHERE id=?", (provider, message['id']))
            final = c.execute("""SELECT * FROM mail_delivery_events WHERE message_id=?
                ORDER BY CASE WHEN status IN ('delivered','bounced') THEN 0 ELSE 1 END,
                event_time,event_key LIMIT 1""", (message['id'],)).fetchone()
            c.execute("UPDATE mail_messages SET delivery_status=?,result_reason=? WHERE id=?",
                      (final['status'], final['reason'], message['id']))
            c.execute("INSERT INTO mail_events(message_id,event_type,event_data) VALUES(?,?,?)",
                      (message['id'], status, json.dumps(dict(queue_id=match['queue_id'],
                       dsn=match['dsn'], detail=detail, relay=relay[1] if relay else '',
                       event_time=when, reason=reason), ensure_ascii=False)))
            evaluate(c, provider, time.time())
        stats['checked'] += 1
        stats['matched_queue'] += 1
        stats[status] += 1
    after = log_path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns):
        with closing(get_connection()) as c, c:
            c.execute("UPDATE mail_control SET heartbeat=? WHERE id=1", (time.time(),))
    return stats


def main() -> None:
    """
    CLI-точка входа.
    """
    arguments = parse_arguments()
    initialize_database()

    while True:
        stats = synchronize_delivery_statuses(log_path=arguments.log, limit=arguments.limit)
        print("Postfix delivery sync:", stats, flush=True)
        if not arguments.watch:
            break
        time.sleep(5)


if __name__ == "__main__":
    main()
