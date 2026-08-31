"""Подготовка и возобновляемая отправка отдельной кампании ЭТрН."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.client_loader import load_clients_by_inn, request_with_network_retries
from src.config import PROJECT_ROOT
from src.database import (
    complete_mail_message,
    create_mail_batch_run,
    create_mail_message,
    finish_mail_run,
    get_connection,
    get_mail_campaign,
    initialize_database,
    save_enriched_client,
    set_mail_run_preparation_stats,
    set_mail_run_pending_count,
    update_mail_run_counts,
)
from src.mailing.sender import MailMessage, build_mail_message
from src.mailing.smtp_provider import MailAttachment, SMTPMailProvider
from src.mailing.templates.registry import get_mail_template
from src.sbis.card_parser import parse_contractor_card
from src.sbis.contractor_card import get_contractor_card


CAMPAIGN_NAME = "etrn_2026"
CAMPAIGN_FAMILY = "etrn"
TEMPLATE_NAME = "etrn"
ATTACHMENTS_DIR = PROJECT_ROOT / "assets" / "mailing" / "etrn"
ATTACHMENT_FILENAMES = (
    "Чек-лист подключения бизнеса к электронным транспортным накладным (ЭТрН).pdf",
    "КП Saby TMS Электронные перевозочные документы ЭТрН (основное).pdf",
)
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
TRANSIENT_ERROR_RE = re.compile(
    r"(^|\D)4\d\d(\D|$)|timeout|timed out|connection|temporar|try again|deferred",
    re.IGNORECASE,
)


def _env_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 0:
        raise ValueError(f"{name} не может быть отрицательным")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    value = raw_value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{name} должен быть true или false, получено {raw_value!r}"
    )


@dataclass(frozen=True, slots=True)
class TestRecipientConfig:
    enabled: bool
    recipients: tuple[str, ...]


def get_batch_limit() -> int:
    """Получить размер ETRN batch с пределом 500 и legacy fallback."""
    raw_limit = os.getenv("ETRN_BATCH_LIMIT")
    if raw_limit is None:
        limit = _env_int("ETRN_BATCH_SIZE", 500)
    else:
        limit = _env_int("ETRN_BATCH_LIMIT", 500)
    if limit < 1:
        raise ValueError("ETRN_BATCH_LIMIT должен быть больше 0")
    return min(limit, 500)


def get_test_recipient_config() -> TestRecipientConfig:
    """Прочитать fail-closed конфигурацию SMTP-подмены получателей."""
    enabled = _env_bool("ETRN_TEST_RECIPIENTS_ENABLED", False)
    if not enabled:
        return TestRecipientConfig(enabled=False, recipients=())

    raw_recipients = os.getenv("ETRN_TEST_RECIPIENTS", "")
    values = [value.strip() for value in raw_recipients.split(",")]
    if not raw_recipients.strip() or any(not value for value in values):
        raise ValueError(
            "ETRN_TEST_RECIPIENTS_ENABLED=true, но список "
            "ETRN_TEST_RECIPIENTS пуст или содержит пустой адрес"
        )

    recipients = tuple(normalize_email(value) for value in values)
    invalid = [value for value in recipients if not is_valid_email(value)]
    if invalid:
        raise ValueError(
            "ETRN_TEST_RECIPIENTS содержит некорректные email; "
            f"количество ошибок: {len(invalid)}"
        )
    return TestRecipientConfig(enabled=True, recipients=recipients)


@dataclass(slots=True)
class PrepareStats:
    input_inns: int = 0
    clients_found: int = 0
    clients_with_email: int = 0
    enrichment_needed: int = 0
    email_found_after_enrichment: int = 0
    without_email: int = 0
    unique_emails: int = 0
    duplicate_etrn: int = 0
    invalid_email: int = 0
    bounced_before_send: int = 0
    invalid_or_bounced: int = 0
    queued: int = 0


def normalize_email(value: str) -> str:
    return value.strip().lower()


def is_valid_email(value: str) -> bool:
    return len(value) <= 254 and EMAIL_RE.fullmatch(value) is not None


def read_inns(path: Path) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        inn = line.strip()
        if not inn or inn in seen:
            continue
        if not inn.isdigit() or len(inn) not in (10, 12):
            raise ValueError(f"Некорректный ИНН во входном файле: {inn!r}")
        seen.add(inn)
        values.append(inn)
    return values


def ensure_etrn_campaign() -> int:
    with closing(get_connection()) as connection, connection:
        row = connection.execute(
            "SELECT id, campaign_family FROM mail_campaigns WHERE name = ?",
            (CAMPAIGN_NAME,),
        ).fetchone()
        if row is not None:
            if row["campaign_family"] != CAMPAIGN_FAMILY:
                raise ValueError("Имя ETRN-кампании занято другим семейством")
            return int(row["id"])
        cursor = connection.execute(
            """
            INSERT INTO mail_campaigns (
                name, template_name, selection_id, campaign_family, status
            ) VALUES (?, ?, 0, ?, 'active')
            """,
            (CAMPAIGN_NAME, TEMPLATE_NAME, CAMPAIGN_FAMILY),
        )
        return int(cursor.lastrowid)


def _audit(
    campaign_id: int, event_type: str, *, source_value: str,
    client_id: int | None = None, email: str | None = None,
    event_data: str | None = None,
) -> None:
    with closing(get_connection()) as connection, connection:
        connection.execute(
            """
            INSERT INTO mail_audience_events (
                campaign_id, client_id, source_value, email, event_type, event_data
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (campaign_id, client_id, source_value, email, event_type, event_data),
        )


def _client_by_inn(inn: str) -> dict[str, object] | None:
    with closing(get_connection()) as connection, connection:
        row = connection.execute(
            """
            SELECT id, inn, name, spp_uuid, contractor_id,
                   director_first_name, director_middle_name
            FROM clients WHERE inn = ? ORDER BY id LIMIT 1
            """,
            (inn,),
        ).fetchone()
    return dict(row) if row else None


async def _load_missing_client_from_sbis(
    inn: str,
    *,
    source_value: str,
) -> dict[str, object] | None:
    found_clients = await load_clients_by_inn(
        [inn],
        source_type="inn_file",
        source_value=source_value,
    )
    if not found_clients:
        return None

    client = _client_by_inn(inn)
    if client is None:
        raise RuntimeError("SBIS client was found but was not saved in clients")
    return client


def _emails(client_id: int) -> list[str]:
    with closing(get_connection()) as connection, connection:
        rows = connection.execute(
            """
            SELECT value FROM client_contacts
            WHERE client_id = ? AND contact_type = 'email'
            ORDER BY id
            """,
            (client_id,),
        ).fetchall()
    return [str(row["value"]) for row in rows]


async def _enrich_client(client: dict[str, object]) -> None:
    card = await request_with_network_retries(
        get_contractor_card,
        spp_uuid=client.get("spp_uuid"),
        contractor_id=client.get("contractor_id"),
    )
    save_enriched_client(
        parse_contractor_card(card),
        contractor_id=(
            int(client["contractor_id"])
            if isinstance(client.get("contractor_id"), int) else None
        ),
    )


def _was_bounced(normalized: str) -> bool:
    with closing(get_connection()) as connection, connection:
        return connection.execute(
            """
            SELECT 1 FROM mail_recipients AS mr
            INNER JOIN mail_messages AS mm ON mm.recipient_id = mr.id
            WHERE LOWER(TRIM(mr.email)) = ? AND mm.delivery_status = 'bounced'
            LIMIT 1
            """,
            (normalized,),
        ).fetchone() is not None


def _queue_email(campaign_id: int, client_id: int, email: str) -> bool:
    with closing(get_connection()) as connection, connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO mail_recipients (
                campaign_id, client_id, email, normalized_email,
                campaign_family, status
            ) VALUES (?, ?, ?, ?, 'etrn', 'pending')
            """,
            (campaign_id, client_id, email, email),
        )
        return cursor.rowcount == 1


async def prepare_audience(inn_file: Path) -> tuple[int, PrepareStats]:
    initialize_database()
    campaign_id = ensure_etrn_campaign()
    inns = read_inns(inn_file)
    stats = PrepareStats(input_inns=len(inns))
    prepared_emails: set[str] = set()
    print(f"ETRN_PREPARE_START\ninns={len(inns)}")

    for inn in inns:
        client = _client_by_inn(inn)
        if client is None:
            try:
                client = await _load_missing_client_from_sbis(
                    inn,
                    source_value=inn_file.name,
                )
            except Exception as error:
                _audit(
                    campaign_id,
                    "sbis_lookup_failed",
                    source_value=inn,
                    event_data=type(error).__name__,
                )
                continue
            if client is None:
                _audit(campaign_id, "client_not_found", source_value=inn)
                continue
        stats.clients_found += 1
        client_id = int(client["id"])
        emails = _emails(client_id)
        if not emails:
            stats.enrichment_needed += 1
            try:
                await _enrich_client(client)
            except Exception as error:
                stats.without_email += 1
                _audit(
                    campaign_id, "enrichment_failed", source_value=inn,
                    client_id=client_id, event_data=type(error).__name__,
                )
                continue
            emails = _emails(client_id)
            if emails:
                stats.email_found_after_enrichment += 1
        if not emails:
            stats.without_email += 1
            _audit(campaign_id, "no_email", source_value=inn, client_id=client_id)
            continue
        stats.clients_with_email += 1
        for raw_email in emails:
            email = normalize_email(raw_email)
            if not is_valid_email(email):
                stats.invalid_email += 1
                stats.invalid_or_bounced += 1
                _audit(campaign_id, "invalid_email", source_value=inn, client_id=client_id, email=email)
                continue
            if _was_bounced(email):
                stats.bounced_before_send += 1
                stats.invalid_or_bounced += 1
                _audit(campaign_id, "bounced", source_value=inn, client_id=client_id, email=email)
                continue
            if email in prepared_emails or not _queue_email(campaign_id, client_id, email):
                stats.duplicate_etrn += 1
                _audit(campaign_id, "duplicate_etrn", source_value=inn, client_id=client_id, email=email)
                continue
            prepared_emails.add(email)
            stats.unique_emails += 1
            stats.queued += 1

    skipped_count = (
        stats.input_inns
        - stats.clients_found
        + stats.without_email
        + stats.invalid_email
        + stats.duplicate_etrn
        + stats.bounced_before_send
    )
    _audit(
        campaign_id,
        "prepare_summary",
        source_value=str(inn_file),
        event_data=json.dumps(
            {**asdict(stats), "skipped_count": skipped_count},
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    print(f"ETRN_CONTACTS_READY\nclients={stats.clients_found}\nemails={stats.queued}")
    print_prepare_stats(stats)
    return campaign_id, stats


def print_prepare_stats(stats: PrepareStats) -> None:
    labels = (
        ("ИНН во входном файле", stats.input_inns),
        ("Найдено клиентов в БД", stats.clients_found),
        ("Клиентов с готовыми email", stats.clients_with_email),
        ("Потребовался поиск", stats.enrichment_needed),
        ("Email найден после поиска", stats.email_found_after_enrichment),
        ("Без email", stats.without_email),
        ("Уникальных email", stats.unique_emails),
        ("Уже получали/дубли ЭТрН", stats.duplicate_etrn),
        ("Invalid/bounced", stats.invalid_or_bounced),
        ("К отправке", stats.queued),
    )
    for label, value in labels:
        print(f"{label + ':':32} {value}")


def load_attachments(directory: Path | None = None) -> list[MailAttachment]:
    directory = directory or ATTACHMENTS_DIR
    missing = [name for name in ATTACHMENT_FILENAMES if not (directory / name).is_file()]
    if missing:
        raise FileNotFoundError(
            "Отправка ЭТрН запрещена: отсутствуют обязательные PDF: "
            + ", ".join(missing)
        )
    return [
        MailAttachment(name, (directory / name).read_bytes(), "application", "pdf")
        for name in ATTACHMENT_FILENAMES
    ]


def _eligible_recipients(
    campaign_id: int,
    limit: int,
    *,
    promote_deferred: bool = True,
) -> list[dict[str, object]]:
    with closing(get_connection()) as connection, connection:
        if promote_deferred:
            connection.execute(
                """
                UPDATE mail_recipients
                SET status = 'pending', updated_at = CURRENT_TIMESTAMP
                WHERE campaign_id = ? AND status = 'deferred'
                  AND next_attempt_at <= CURRENT_TIMESTAMP
                """,
                (campaign_id,),
            )
            status_condition = "mr.status = 'pending'"
        else:
            status_condition = """
                (mr.status = 'pending' OR (
                    mr.status = 'deferred'
                    AND mr.next_attempt_at <= CURRENT_TIMESTAMP
                ))
            """
        rows = connection.execute(
            f"""
            SELECT mr.id AS recipient_id, mr.client_id, mr.email, mr.status,
                   mr.attempt_count, c.name, c.inn,
                   c.director_first_name, c.director_middle_name
            FROM mail_recipients AS mr
            INNER JOIN clients AS c ON c.id = mr.client_id
            WHERE mr.campaign_id = ? AND {status_condition}
            ORDER BY mr.id LIMIT ?
            """,
            (campaign_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def _campaign_cooldown(campaign_id: int) -> datetime | None:
    campaign = get_mail_campaign(campaign_id)
    raw = campaign.get("next_send_at")
    if not raw:
        return None
    value = datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return value if value > datetime.now(timezone.utc) else None


def _pending_queue_count(campaign_id: int) -> int:
    with closing(get_connection()) as connection, connection:
        return int(connection.execute(
            """
            SELECT COUNT(*) FROM mail_recipients
            WHERE campaign_id = ? AND status IN ('pending', 'deferred')
            """,
            (campaign_id,),
        ).fetchone()[0])


def _preparation_snapshot(campaign_id: int) -> dict[str, int]:
    with closing(get_connection()) as connection:
        row = connection.execute(
            """
            SELECT event_data FROM mail_audience_events
            WHERE campaign_id = ? AND event_type = 'prepare_summary'
            ORDER BY id DESC LIMIT 1
            """,
            (campaign_id,),
        ).fetchone()
    if row is None or not row["event_data"]:
        return {}
    try:
        value = json.loads(str(row["event_data"]))
    except (TypeError, ValueError):
        return {}
    return {
        str(key): int(count)
        for key, count in value.items()
        if isinstance(count, int) and count >= 0
    }


def _recover_interrupted_runs(campaign_id: int) -> int:
    """Закрыть runs прерванного worker перед созданием новой партии."""
    with closing(get_connection()) as connection, connection:
        cursor = connection.execute(
            """
            UPDATE mail_runs
            SET status = 'failed', finished_at = CURRENT_TIMESTAMP,
                error_text = COALESCE(error_text, 'worker_restarted')
            WHERE campaign_id = ? AND status = 'running'
            """,
            (campaign_id,),
        )
        return cursor.rowcount


def _defer_recipient(recipient_id: int, attempts: int, error: str, delay: int) -> None:
    next_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
    with closing(get_connection()) as connection, connection:
        connection.execute(
            """
            UPDATE mail_recipients
            SET status = 'deferred', attempt_count = ?, next_attempt_at = ?,
                last_error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (attempts, next_at.strftime("%Y-%m-%d %H:%M:%S"), error[:1000], recipient_id),
        )


def _record_attempt(recipient_id: int, attempts: int, error: str | None = None) -> None:
    with closing(get_connection()) as connection, connection:
        connection.execute(
            """
            UPDATE mail_recipients SET attempt_count = ?, last_error = ?,
                next_attempt_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (attempts, error[:1000] if error else None, recipient_id),
        )


def _set_cooldown(campaign_id: int, duration: int) -> datetime:
    until = datetime.now(timezone.utc) + timedelta(seconds=duration)
    with closing(get_connection()) as connection, connection:
        connection.execute(
            """
            UPDATE mail_campaigns SET next_send_at = ?,
                updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (until.strftime("%Y-%m-%d %H:%M:%S"), campaign_id),
        )
    return until


def _clear_cooldown(campaign_id: int) -> None:
    with closing(get_connection()) as connection, connection:
        connection.execute(
            """
            UPDATE mail_campaigns SET next_send_at = NULL,
                updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (campaign_id,),
        )


def _cooldown_duration() -> int:
    minimum = _env_int("ETRN_COOLDOWN_MIN_SECONDS", 2400)
    maximum = _env_int("ETRN_COOLDOWN_MAX_SECONDS", 3000)
    if maximum < minimum:
        raise ValueError("Максимальный ETRN cooldown меньше минимального")
    return random.randint(minimum, maximum)


async def send_campaign(*, dry_run: bool, confirm_real_send: bool) -> None:
    if not dry_run and not confirm_real_send:
        raise ValueError("Для реальной отправки требуется --confirm-real-send")
    batch_size = get_batch_limit()
    test_config = get_test_recipient_config()
    if test_config.enabled:
        print(
            "ETRN TEST RECIPIENT MODE ENABLED\n"
            f"configured_test_recipients={len(test_config.recipients)}"
        )
    initialize_database()
    campaign_id = ensure_etrn_campaign()
    attachments = load_attachments()
    template = get_mail_template(TEMPLATE_NAME)
    if dry_run:
        recipients = _eligible_recipients(
            campaign_id,
            batch_size,
            promote_deferred=False,
        )
        if not recipients:
            print("Pending/deferred-получателей ЭТрН, готовых к отправке, нет.")
            return
        print(f"ETRN_BATCH_PREVIEW\nsize={len(recipients)}")
        for index, recipient in enumerate(recipients, 1):
            message = build_mail_message(recipient, template)
            message.attachments = attachments
            print(
                f"[{index}/{len(recipients)}] "
                f"recipient_id={message.recipient_id} -> DRY_RUN"
            )
        print("Dry-run завершён: SMTP и БД очереди не изменялись.")
        return

    provider = SMTPMailProvider.from_env()
    retry_delays = (
        _env_int("ETRN_RETRY_FIRST_SECONDS", 60),
        _env_int("ETRN_RETRY_SECOND_SECONDS", 300),
    )
    jitter_min = _env_int("ETRN_MESSAGE_DELAY_MIN_SECONDS", 6)
    jitter_max = _env_int("ETRN_MESSAGE_DELAY_MAX_SECONDS", 8)
    if jitter_max < jitter_min:
        raise ValueError("Максимальный ETRN jitter меньше минимального")
    recovered_runs = _recover_interrupted_runs(campaign_id)
    if recovered_runs and _pending_queue_count(campaign_id) and not _campaign_cooldown(campaign_id):
        _set_cooldown(campaign_id, _cooldown_duration())

    while True:
        cooldown = _campaign_cooldown(campaign_id)
        if cooldown is not None:
            delay = max(
                1,
                int((cooldown - datetime.now(timezone.utc)).total_seconds()) + 1,
            )
            print(f"ETRN_COOLDOWN\nduration={delay}s\nuntil={cooldown.isoformat()}")
            await asyncio.sleep(delay)

        recipients = _eligible_recipients(campaign_id, batch_size)
        if not recipients:
            if _pending_queue_count(campaign_id) == 0:
                _clear_cooldown(campaign_id)
            print("Pending/deferred-получателей ЭТрН, готовых к отправке, нет.")
            return

        run_id, batch_number = create_mail_batch_run(
            campaign_id=campaign_id,
            selection_id=0,
        )
        snapshot = _preparation_snapshot(campaign_id)
        set_mail_run_preparation_stats(
            run_id,
            input_inns_count=snapshot.get("input_inns", 0),
            clients_found_count=snapshot.get("clients_found", 0),
            clients_without_email_count=snapshot.get("without_email", 0),
            email_found_after_enrichment_count=snapshot.get(
                "email_found_after_enrichment", 0
            ),
            invalid_email_count=snapshot.get("invalid_email", 0),
            duplicate_count=snapshot.get("duplicate_etrn", 0),
            bounced_before_send_count=snapshot.get("bounced_before_send", 0),
            prepared_email_count=len(recipients),
            skipped_count=snapshot.get("skipped_count", 0),
            pending_count=_pending_queue_count(campaign_id),
        )
        update_mail_run_counts(
            run_id,
            recipients_added=len(recipients),
            sent_count=0,
            delivered_count=0,
            bounced_count=0,
            deferred_count=0,
            failed_count=0,
        )
        print(
            f"ETRN_BATCH_START\nbatch={batch_number}\n"
            f"run_id={run_id}\nsize={len(recipients)}"
        )
        sent = failed = deferred = 0
        try:
            for index, recipient in enumerate(recipients, 1):
                real_email = normalize_email(str(recipient["email"]))
                smtp_email = (
                    test_config.recipients[(index - 1) % len(test_config.recipients)]
                    if test_config.enabled
                    else real_email
                )
                record = create_mail_message(
                    recipient_id=int(recipient["recipient_id"]),
                    provider="smtp",
                    run_id=run_id,
                    smtp_recipient_email=smtp_email,
                    is_test_recipient=test_config.enabled,
                )
                message: MailMessage = build_mail_message(
                    recipient,
                    template,
                    tracking_token=str(record["tracking_token"]),
                )
                message.attachments = attachments
                outbound_message = replace(message, to_email=smtp_email)
                result = await provider.send(outbound_message)
                complete_mail_message(
                    message_id=int(record["message_id"]),
                    provider_message_id=result.provider_message_id,
                    success=result.success,
                    error=result.error,
                )
                attempts = int(recipient.get("attempt_count") or 0) + 1
                if result.success:
                    sent += 1
                    _record_attempt(message.recipient_id, attempts)
                    outcome = "sent"
                else:
                    error = result.error or "SMTP error"
                    if attempts < 3 and TRANSIENT_ERROR_RE.search(error):
                        deferred += 1
                        _defer_recipient(
                            message.recipient_id,
                            attempts,
                            error,
                            retry_delays[attempts - 1],
                        )
                        outcome = "deferred"
                    else:
                        failed += 1
                        _record_attempt(message.recipient_id, attempts, error)
                        outcome = "failed"
                print(
                    f"[{index}/{len(recipients)}] "
                    f"recipient_id={message.recipient_id} -> {outcome}"
                )
                if index < len(recipients):
                    await asyncio.sleep(random.randint(jitter_min, jitter_max))
        except BaseException as error:
            finish_mail_run(
                run_id,
                status="failed",
                recipients_added=len(recipients),
                sent_count=sent,
                delivered_count=0,
                bounced_count=0,
                deferred_count=deferred,
                failed_count=failed,
                error_text=type(error).__name__,
            )
            pending_count = _pending_queue_count(campaign_id)
            set_mail_run_pending_count(run_id, pending_count)
            if pending_count:
                _set_cooldown(campaign_id, _cooldown_duration())
            raise

        finish_mail_run(
            run_id,
            status="success" if not failed and not deferred else "partial",
            recipients_added=len(recipients),
            sent_count=sent,
            delivered_count=0,
            bounced_count=0,
            deferred_count=deferred,
            failed_count=failed,
        )
        pending_count = _pending_queue_count(campaign_id)
        set_mail_run_pending_count(run_id, pending_count)
        print(
            f"ETRN_BATCH_COMPLETE\nbatch={batch_number}\nrun_id={run_id}\n"
            f"recipients={len(recipients)}\nsent={sent}\n"
            f"failed={failed}\ndeferred={deferred}"
        )
        if pending_count == 0:
            _clear_cooldown(campaign_id)
            return
        duration = _cooldown_duration()
        until = _set_cooldown(campaign_id, duration)
        print(f"ETRN_COOLDOWN_SET\nduration={duration}s\nuntil={until.isoformat()}")


def campaign_stats() -> dict[str, int]:
    initialize_database()
    campaign_id = ensure_etrn_campaign()
    with closing(get_connection()) as connection, connection:
        rows = connection.execute(
            "SELECT status, COUNT(*) AS n FROM mail_recipients WHERE campaign_id = ? GROUP BY status",
            (campaign_id,),
        ).fetchall()
        statuses = {str(row["status"]): int(row["n"]) for row in rows}
        result = {
            "prepared": sum(statuses.values()),
            "pending": statuses.get("pending", 0) + statuses.get("deferred", 0),
            "sent": statuses.get("sent", 0),
            "failed": statuses.get("failed", 0),
        }
        delivery = connection.execute(
            """
            SELECT
                COUNT(DISTINCT CASE WHEN mm.delivery_status = 'delivered' THEN mm.id END) AS delivered,
                COUNT(DISTINCT CASE WHEN mm.delivery_status = 'bounced' THEN mm.id END) AS bounced,
                SUM(CASE WHEN me.event_type = 'opened' THEN 1 ELSE 0 END) AS opens,
                SUM(CASE WHEN me.event_type = 'clicked' THEN 1 ELSE 0 END) AS clicks
            FROM mail_recipients AS mr
            LEFT JOIN mail_messages AS mm ON mm.recipient_id = mr.id AND mm.is_test = 0
            LEFT JOIN mail_events AS me ON me.message_id = mm.id
            WHERE mr.campaign_id = ?
            """,
            (campaign_id,),
        ).fetchone()
        for field in ("delivered", "bounced", "opens", "clicks"):
            result[field] = int(delivery[field] or 0)
        for event_type in (
            "client_not_found", "sbis_lookup_failed", "no_email",
            "enrichment_failed", "invalid_email", "duplicate_etrn", "bounced",
        ):
            result[f"skipped_{event_type}"] = int(connection.execute(
                "SELECT COUNT(*) FROM mail_audience_events WHERE campaign_id = ? AND event_type = ?",
                (campaign_id, event_type),
            ).fetchone()[0])
    return result


def print_configuration() -> None:
    """Показать безопасную ETRN-конфигурацию без БД и SMTP."""
    test_config = get_test_recipient_config()
    mode = "ENABLED" if test_config.enabled else "DISABLED"
    print(f"ETRN test recipient mode: {mode}")
    print(f"Configured test recipients: {len(test_config.recipients)}")
    print(f"Batch limit: {get_batch_limit()}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Кампания ЭТрН")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Подготовить аудиторию")
    prepare.add_argument("--inn-file", type=Path, required=True)
    send = commands.add_parser("send", help="Обработать очередь")
    mode = send.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--confirm-real-send", action="store_true")
    commands.add_parser("stats", help="Показать состояние очереди")
    commands.add_parser("config", help="Проверить конфигурацию без отправки")
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    if arguments.command == "prepare":
        asyncio.run(prepare_audience(arguments.inn_file))
    elif arguments.command == "send":
        asyncio.run(send_campaign(
            dry_run=arguments.dry_run,
            confirm_real_send=arguments.confirm_real_send,
        ))
    elif arguments.command == "stats":
        for key, value in sorted(campaign_stats().items()):
            print(f"{key}: {value}")
    else:
        print_configuration()


if __name__ == "__main__":
    main()
