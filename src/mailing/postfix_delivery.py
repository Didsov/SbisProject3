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
import gzip
import hashlib
import json
import time
import sqlite3
from contextlib import closing
from datetime import datetime
import re
from pathlib import Path

from src.database import (
    get_connection,
    initialize_database,
)
from src import database as db
from src.daily_lock import daily_run_lock, DailyRunAlreadyRunningError


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
    parser.add_argument('--import-only', action='store_true', help='Импорт архива без здорового heartbeat')
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

    if log_path.suffix == '.gz':
        with gzip.open(log_path, 'rt', encoding='utf-8', errors='replace') as source:
            text = source.read()
    else:
        text = log_path.read_text(encoding='utf-8', errors='replace')
    if text and not text.endswith('\n'):
        raise ValueError('Incomplete final log line; feedback remains unhealthy')
    return text.splitlines()


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


def log_sources(log_path):
    rotated = [p for p in log_path.parent.glob(log_path.name + '*')
               if p.is_file() and re.fullmatch(
                   re.escape(log_path.name) + r'(?:\.\d+|-\d{8})(?:\.gz)?', p.name)]
    return sorted(rotated) + [log_path]


def source_signature(sources):
    return [(str(p), s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
            for p in sources for s in [p.stat()]]


def synchronize_delivery_statuses(*, log_path: Path, limit: int | None = None,
                                  import_only: bool = False) -> dict[str, int]:
    # One importer per physical database, including daily_run and archive imports.
    with daily_run_lock(db.DATABASE_FILE.resolve().with_suffix('.mail-log.lock')):
        return _synchronize_locked(log_path=log_path.resolve(), limit=limit, import_only=import_only)


def _synchronize_locked(*, log_path, limit, import_only):
    from src.mailing.control import evaluate, provider_for
    # Invalidate first. A partial scan/error must never advertise a healthy tail.
    with closing(get_connection()) as c, c:
        c.execute("UPDATE mail_control SET heartbeat=NULL WHERE id=1")
        previous = c.execute('SELECT log_path,log_checkpoint FROM mail_control WHERE id=1').fetchone()
    if not import_only and previous['log_path'] not in (None, str(log_path)):
        raise ValueError('Different live log path; use --import-only for archives')
    sources = [log_path] if import_only else log_sources(log_path)
    before = source_signature(sources)
    lines = []
    tail = None
    for source in sources:
        reference = source.stat().st_mtime
        source_lines = load_log_lines(source)
        lines.extend((line, reference) for line in source_lines)
        if source == log_path and source_lines:
            tail = hashlib.sha256(source_lines[-1].encode('utf-8')).hexdigest()
    available_hashes = {hashlib.sha256(line.encode('utf-8')).hexdigest() for line, _ in lines}
    if not import_only and previous['log_checkpoint'] and previous['log_checkpoint'] not in available_hashes:
        raise ValueError('Log rotation gap: previous tail missing; restore retained archives before continuing')
    stats = dict(checked=0, matched_queue=0, delivered=0, bounced=0, deferred=0, not_found=0)
    with closing(get_connection()) as c, c:
        for line, reference in lines:
            match = MESSAGE_ID_RE.search(line)
            if match:
                c.execute("INSERT OR IGNORE INTO mail_queue_links VALUES(?,?,?)", (
                    match['queue_id'], match['message_id'], event_timestamp(line, reference)))
    processed = 0
    complete = True
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
            mappings = c.execute("""SELECT provider_message_id FROM mail_queue_links
                WHERE queue_id=? AND event_time=(SELECT MAX(event_time) FROM mail_queue_links
                  WHERE queue_id=? AND event_time<=?)""",
                (match['queue_id'], match['queue_id'], when)).fetchall()
            if len(mappings) != 1:
                complete = False
                stats['not_found'] += 1
                continue
            message = c.execute("""SELECT m.*, COALESCE(m.smtp_recipient_email,r.email) AS actual_email
                FROM mail_messages m JOIN mail_recipients r ON r.id=m.recipient_id
                WHERE m.provider_message_id=? AND m.is_test=0 AND m.provider='smtp'
                ORDER BY m.id DESC LIMIT 1""", (mappings[0][0],)).fetchone()
        if message is None:
            continue
        if message['actual_email'].lower() != match['recipient'].lower():
            complete = False
            stats['not_found'] += 1
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
            # A legacy terminal result may only exist in the old mail_events.
            # Never roll it back when importing an incomplete retained history.
            c.execute("""UPDATE mail_messages SET delivery_status=?,result_reason=? WHERE id=?
                AND (delivery_status NOT IN ('delivered','bounced') OR delivery_status=?)""",
                      (final['status'], final['reason'], message['id'], final['status']))
            c.execute("INSERT INTO mail_events(message_id,event_type,event_data) VALUES(?,?,?)",
                      (message['id'], status, json.dumps(dict(queue_id=match['queue_id'],
                       dsn=match['dsn'], detail=detail, relay=relay[1] if relay else '',
                       event_time=when, reason=reason), ensure_ascii=False)))
            evaluate(c, provider, time.time())
        stats['checked'] += 1
        stats['matched_queue'] += 1
        stats[status] += 1
    after = source_signature([log_path] if import_only else log_sources(log_path))
    if not import_only and complete and before == after:
        with closing(get_connection()) as c, c:
            c.execute("UPDATE mail_control SET heartbeat=?,log_path=?,log_checkpoint=? WHERE id=1",
                      (time.time(), str(log_path), tail or previous['log_checkpoint']))
    return stats


def main() -> None:
    """
    CLI-точка входа.
    """
    arguments = parse_arguments()
    initialize_database()

    while True:
        try:
            stats = synchronize_delivery_statuses(log_path=arguments.log, limit=arguments.limit,
                                                  import_only=arguments.import_only)
            print("Postfix delivery sync:", stats, flush=True)
        except (OSError, ValueError, sqlite3.Error, DailyRunAlreadyRunningError) as error:
            if not arguments.watch:
                raise
            print(f'Postfix feedback unhealthy: {error}', flush=True)
        if not arguments.watch:
            break
        time.sleep(5)


if __name__ == "__main__":
    main()
