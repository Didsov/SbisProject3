"""Manual CSV mailing queues with explicit preview, test and send steps."""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path

from src import database as db
from src.daily_lock import daily_run_lock
from src.database import (
    complete_mail_message,
    create_mail_message,
    get_mail_campaign,
    get_pending_mail_recipients,
)
from src.mailing.etrn import (
    TEMPLATE_NAME as ETRN_TEMPLATE_NAME,
    get_test_recipient_config,
    is_valid_email,
    load_attachments,
    normalize_email,
)
from src.mailing.maintenance import backup_database
from src.mailing.sender import build_mail_message
from src.mailing.smtp_provider import SMTPMailProvider
from src.mailing.templates.registry import MAIL_TEMPLATES, get_mail_template


MANUAL_CLIENT_UUID = "projectsbis-manual-mailing-recipient"
MANUAL_FAMILY = "manual"


@dataclass(frozen=True, slots=True)
class ManualRecipient:
    email: str
    contact_name: str


def read_recipients(path: Path) -> tuple[list[ManualRecipient], int]:
    """Read the complete UTF-8 semicolon CSV before changing the database."""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=";")
        if reader.fieldnames is None:
            raise ValueError("CSV не содержит строку заголовков")
        headers = [name.strip() for name in reader.fieldnames]
        if "email" not in headers:
            raise ValueError("CSV должен содержать обязательную колонку email")
        unknown = sorted(set(headers) - {"email", "contact_name"})
        if unknown:
            raise ValueError("Неизвестные колонки CSV: " + ", ".join(unknown))
        if len(headers) != len(set(headers)):
            raise ValueError("CSV содержит повторяющиеся колонки")

        result: list[ManualRecipient] = []
        seen: set[str] = set()
        duplicates = 0
        for line_number, raw in enumerate(reader, start=2):
            row = {
                str(key).strip(): (value or "").strip()
                for key, value in raw.items()
                if key is not None
            }
            if not any(row.values()):
                continue
            email = normalize_email(row.get("email", ""))
            if not is_valid_email(email):
                raise ValueError(f"Некорректный email в строке {line_number}")
            if email in seen:
                duplicates += 1
                continue
            seen.add(email)
            result.append(ManualRecipient(email, row.get("contact_name", "")))
    if not result:
        raise ValueError("CSV не содержит ни одного корректного получателя")
    return result, duplicates


def _manual_client_id(connection) -> int:
    row = connection.execute(
        "SELECT id FROM clients WHERE spp_uuid=?", (MANUAL_CLIENT_UUID,)
    ).fetchone()
    if row is not None:
        return int(row[0])
    cursor = connection.execute(
        "INSERT INTO clients(spp_uuid,name) VALUES(?,?)",
        (MANUAL_CLIENT_UUID, "Ручная почтовая очередь"),
    )
    return int(cursor.lastrowid)


def prepare_queue(*, csv_path: Path, campaign_name: str, template_name: str) -> int:
    recipients, duplicates = read_recipients(csv_path)
    get_mail_template(template_name)
    clean_name = campaign_name.strip()
    if not clean_name:
        raise ValueError("Название кампании не может быть пустым")
    db.initialize_database()
    with closing(db.get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute(
            "SELECT 1 FROM mail_campaigns WHERE name=?", (clean_name,)
        ).fetchone():
            raise ValueError(f"Кампания {clean_name!r} уже существует")
        client_id = _manual_client_id(connection)
        campaign_id = int(connection.execute(
            """INSERT INTO mail_campaigns(
                   name,template_name,selection_id,campaign_family,status
               ) VALUES(?,?,0,?,'active')""",
            (clean_name, template_name, MANUAL_FAMILY),
        ).lastrowid)
        connection.executemany(
            """INSERT INTO mail_recipients(
                   campaign_id,client_id,email,normalized_email,campaign_family,
                   contact_name,status
               ) VALUES(?,?,?,?,?,?,'pending')""",
            [
                (
                    campaign_id,
                    client_id,
                    recipient.email,
                    recipient.email,
                    MANUAL_FAMILY,
                    recipient.contact_name or None,
                )
                for recipient in recipients
            ],
        )
    print(f"campaign_id={campaign_id}")
    print(f"template={template_name}")
    print(f"queued={len(recipients)}")
    print(f"duplicates_skipped={duplicates}")
    return campaign_id


def _attachments(template_name: str):
    return load_attachments() if template_name == ETRN_TEMPLATE_NAME else []


def preview_queue(*, campaign_id: int, limit: int) -> None:
    db.initialize_database()
    campaign = get_mail_campaign(campaign_id)
    template_name = str(campaign.get("template_name") or "")
    template = get_mail_template(template_name)
    attachments = _attachments(template_name)
    recipients = get_pending_mail_recipients(campaign_id, limit)
    print(f"campaign_id={campaign_id}")
    print(f"template={template_name}")
    print(f"preview_count={len(recipients)}")
    print(f"attachments={len(attachments)}")
    for index, recipient in enumerate(recipients, 1):
        message = build_mail_message(recipient, template)
        print(
            f"[{index}/{len(recipients)}] recipient_id={message.recipient_id} "
            f"email={message.to_email} subject={message.subject!r}"
        )


async def send_test(*, template_name: str, contact_name: str) -> None:
    config = get_test_recipient_config()
    if not config.enabled:
        raise RuntimeError("ETRN test recipient mode выключен")
    if len(config.recipients) != 3:
        raise RuntimeError(
            "Для контрольного запуска ожидаются ровно 3 test recipient адреса"
        )
    template = get_mail_template(template_name)
    attachments = _attachments(template_name)
    provider = SMTPMailProvider.from_env()
    db.initialize_database()
    display_name = contact_name or "Тестовый получатель"
    with closing(db.get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        client_id = _manual_client_id(connection)
        campaign = connection.execute(
            "SELECT id FROM mail_campaigns WHERE name='_manual_test'"
        ).fetchone()
        if campaign is None:
            campaign_id = int(connection.execute(
                """INSERT INTO mail_campaigns(
                       name,template_name,selection_id,campaign_family,status
                   ) VALUES('_manual_test',?,0,?,'active')""",
                (template_name, MANUAL_FAMILY),
            ).lastrowid)
        else:
            campaign_id = int(campaign[0])
            connection.execute(
                "UPDATE mail_campaigns SET template_name=? WHERE id=?",
                (template_name, campaign_id),
            )
        recipient = connection.execute(
            "SELECT id FROM mail_recipients WHERE campaign_id=? ORDER BY id LIMIT 1",
            (campaign_id,),
        ).fetchone()
        if recipient is None:
            recipient_id = int(connection.execute(
                """INSERT INTO mail_recipients(
                       campaign_id,client_id,email,normalized_email,campaign_family,
                       contact_name,status
                   ) VALUES(?,?,'preview@projectsbis.invalid',
                            'preview@projectsbis.invalid',?,?,'pending')""",
                (campaign_id, client_id, MANUAL_FAMILY, display_name),
            ).lastrowid)
        else:
            recipient_id = int(recipient[0])
            connection.execute(
                "UPDATE mail_recipients SET contact_name=? WHERE id=?",
                (display_name, recipient_id),
            )
    source = get_pending_mail_recipients(campaign_id, 1)[0]
    interval = max(0, int(os.getenv("MANUAL_TEST_INTERVAL_SECONDS", "5")))
    for index, email in enumerate(config.recipients, 1):
        record = create_mail_message(
            recipient_id=recipient_id,
            provider="smtp",
            is_test=True,
            smtp_recipient_email=email,
            is_test_recipient=True,
        )
        message = build_mail_message(
            source,
            template,
            tracking_token=str(record["tracking_token"]),
        )
        message = replace(
            message,
            to_email=email,
            subject=f"[TEST {index}/3] {message.subject}",
            attachments=attachments,
        )
        result = await provider.send(message)
        complete_mail_message(
            message_id=int(record["message_id"]),
            provider_message_id=result.provider_message_id,
            success=result.success,
            error=result.error,
        )
        print(
            f"test={index}/3 success={result.success} "
            f"provider_message_id={result.provider_message_id}"
        )
        if not result.success:
            raise RuntimeError(f"Тестовая SMTP-отправка #{index} не принята: {result.error}")
        if index < len(config.recipients) and interval:
            await asyncio.sleep(interval)


async def send_queue(*, campaign_id: int, limit: int) -> None:
    db.initialize_database()
    campaign = get_mail_campaign(campaign_id)
    template_name = str(campaign.get("template_name") or "")
    template = get_mail_template(template_name)
    attachments = _attachments(template_name)
    recipients = get_pending_mail_recipients(campaign_id, limit)
    provider = SMTPMailProvider.from_env()
    from src.mailing.control import dispatch

    sent = 0
    for recipient in recipients:
        def build(token, current=recipient):
            message = build_mail_message(current, template, tracking_token=token)
            message.attachments = attachments
            return message

        result, reason = await dispatch(recipient, build, provider)
        print(f"recipient_id={recipient['recipient_id']} -> {reason}")
        if result is not None and result.success:
            sent += 1
        if reason in {
            "log_heartbeat_stale", "uncertain_handoff", "manual_pause",
            "daily_limit", "sender_busy", "legacy_feedback_pending",
        }:
            break
    print(f"sent={sent}")
    print(f"selected={len(recipients)}")


def reset_mailing_data(*, backup_path: Path) -> None:
    counts = backup_database(db.DATABASE_FILE, backup_path)
    db.initialize_database()
    lock_path = db.DATABASE_FILE.resolve().with_suffix(".mail-send.lock")
    with daily_run_lock(lock_path), closing(db.get_connection()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        active = connection.execute(
            "SELECT active_message FROM mail_control WHERE id=1"
        ).fetchone()
        if active is not None and active[0] is not None:
            raise RuntimeError("Нельзя очистить данные при незавершённой SMTP-передаче")
        for table in (
            "mail_delivery_events", "mail_queue_links", "mail_queue_ids",
            "mail_events", "mail_messages", "mail_runs", "mail_audience_events",
            "mail_recipients", "mail_campaigns", "mail_providers",
            "mail_provider_periods", "mail_mx_cache",
        ):
            connection.execute(f'DELETE FROM "{table}"')
        connection.execute(
            """UPDATE mail_control SET paused=1,last_handoff=NULL,
                   active_message=NULL,heartbeat=NULL,log_path=NULL,
                   log_checkpoint=NULL"""
        )
    print(f"backup={backup_path.resolve()}")
    print(f"backup_mail_recipients={counts.get('mail_recipients', 0)}")
    print("mailing_data_reset=true")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    reset = commands.add_parser("reset", help="Backup and clear only mailing data")
    reset.add_argument("--backup", type=Path, required=True)
    reset.add_argument("--confirm-reset-mailing-data", action="store_true")

    prepare = commands.add_parser("prepare", help="Create a manual queue from CSV")
    prepare.add_argument("--csv", type=Path, required=True)
    prepare.add_argument("--campaign-name", required=True)
    prepare.add_argument("--template", required=True, choices=sorted(MAIL_TEMPLATES))

    preview = commands.add_parser("preview", help="Render queued messages without SMTP")
    preview.add_argument("--campaign-id", type=int, required=True)
    preview.add_argument("--limit", type=int, default=10)

    test = commands.add_parser("test", help="Send to the 3 configured test recipients")
    test.add_argument("--template", required=True, choices=sorted(MAIL_TEMPLATES))
    test.add_argument("--contact-name", default="Тестовый получатель")
    test.add_argument("--confirm-test-send", action="store_true")

    send = commands.add_parser("send", help="Send pending manual queue messages")
    send.add_argument("--campaign-id", type=int, required=True)
    send.add_argument("--limit", type=int, default=1000)
    send.add_argument("--confirm-real-send", action="store_true")
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    if arguments.command == "reset":
        if not arguments.confirm_reset_mailing_data:
            raise SystemExit("Добавьте --confirm-reset-mailing-data")
        reset_mailing_data(backup_path=arguments.backup)
    elif arguments.command == "prepare":
        prepare_queue(
            csv_path=arguments.csv,
            campaign_name=arguments.campaign_name,
            template_name=arguments.template,
        )
    elif arguments.command == "preview":
        preview_queue(campaign_id=arguments.campaign_id, limit=arguments.limit)
    elif arguments.command == "test":
        if not arguments.confirm_test_send:
            raise SystemExit("Добавьте --confirm-test-send")
        asyncio.run(send_test(
            template_name=arguments.template,
            contact_name=arguments.contact_name.strip(),
        ))
    elif arguments.command == "send":
        if not arguments.confirm_real_send:
            raise SystemExit("Добавьте --confirm-real-send")
        asyncio.run(send_queue(campaign_id=arguments.campaign_id, limit=arguments.limit))


if __name__ == "__main__":
    main()
