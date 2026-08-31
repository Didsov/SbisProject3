from __future__ import annotations

import gc
import io
import tempfile
import unittest
import warnings
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import src.database as database
from src.mailing import etrn
from src.mailing.sender import MailMessage
from src.mailing.smtp_provider import MailAttachment, SMTPMailProvider, SMTPSendResult
from src.mailing.templates import etrn as etrn_template


class EtrnMailingTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.warning_context = warnings.catch_warnings()
        self.warning_context.__enter__()
        warnings.simplefilter("ignore", ResourceWarning)
        self.temp = tempfile.TemporaryDirectory(prefix="projectsbis_etrn_")
        self.root = Path(self.temp.name)
        self.db_patch = patch.object(database, "DATABASE_FILE", self.root / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self) -> None:
        self.db_patch.stop()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            gc.collect()
        self.temp.cleanup()
        self.warning_context.__exit__(None, None, None)

    def add_client(self, inn: str, emails: list[str]) -> int:
        with closing(database.get_connection()) as connection, connection:
            client_id = int(connection.execute(
                "INSERT INTO clients (spp_uuid, inn, name) VALUES (?, ?, ?)",
                (f"uuid-{inn}", inn, f"Client {inn}"),
            ).lastrowid)
            for email in emails:
                connection.execute(
                    "INSERT INTO client_contacts (client_id, contact_type, value) VALUES (?, 'email', ?)",
                    (client_id, email),
                )
        return client_id

    async def test_prepare_normalizes_and_deduplicates_across_clients(self) -> None:
        self.add_client("2500000001", [" A@Example.RU ", "b@example.ru", "bad-address"])
        self.add_client("2500000002", ["a@example.ru"])
        self.add_client("2500000003", [])
        inn_file = self.root / "inn.txt"
        inn_file.write_text(
            "2500000001\n2500000002\n2500000003\n2500000004\n",
            encoding="utf-8",
        )

        with (
            patch.object(etrn, "_enrich_client", new=AsyncMock()),
            redirect_stdout(io.StringIO()),
        ):
            campaign_id, stats = await etrn.prepare_audience(inn_file)

        self.assertEqual(stats.input_inns, 4)
        self.assertEqual(stats.clients_found, 3)
        self.assertEqual(stats.queued, 2)
        self.assertEqual(stats.duplicate_etrn, 1)
        self.assertEqual(stats.invalid_or_bounced, 1)
        self.assertEqual(stats.without_email, 1)
        with closing(database.get_connection()) as connection:
            rows = connection.execute(
                "SELECT normalized_email FROM mail_recipients WHERE campaign_id = ? ORDER BY normalized_email",
                (campaign_id,),
            ).fetchall()
            reasons = {
                row["event_type"] for row in connection.execute(
                    "SELECT event_type FROM mail_audience_events WHERE campaign_id = ?",
                    (campaign_id,),
                ).fetchall()
            }
        self.assertEqual([row["normalized_email"] for row in rows], ["a@example.ru", "b@example.ru"])
        self.assertTrue({"client_not_found", "no_email", "invalid_email", "duplicate_etrn"} <= reasons)

    def test_family_dedup_spans_separate_etrn_campaigns(self) -> None:
        first_client = self.add_client("2500000030", ["same@example.ru"])
        second_client = self.add_client("2500000031", ["same@example.ru"])
        first_campaign = etrn.ensure_etrn_campaign()
        with closing(database.get_connection()) as connection, connection:
            second_campaign = int(connection.execute(
                """
                INSERT INTO mail_campaigns (
                    name, template_name, selection_id, campaign_family, status
                ) VALUES ('etrn_second', 'etrn', 0, 'etrn', 'active')
                """
            ).lastrowid)

        self.assertTrue(etrn._queue_email(first_campaign, first_client, "same@example.ru"))
        self.assertFalse(etrn._queue_email(second_campaign, second_client, "same@example.ru"))

    def test_mailing_migration_is_idempotent_and_database_is_valid(self) -> None:
        database.initialize_database()
        database.initialize_database()
        with closing(database.get_connection()) as connection:
            campaign_columns = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(mail_campaigns)"
                ).fetchall()
            }
            recipient_columns = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(mail_recipients)"
                ).fetchall()
            }
            quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        self.assertTrue({"campaign_family", "next_send_at", "batch_sent_count"} <= campaign_columns)
        self.assertTrue({"normalized_email", "attempt_count", "next_attempt_at"} <= recipient_columns)
        self.assertEqual(quick_check, "ok")

    async def test_dry_run_does_not_change_queue_or_create_messages(self) -> None:
        client_id = self.add_client("2500000010", ["mail@example.ru"])
        campaign_id = etrn.ensure_etrn_campaign()
        self.assertTrue(etrn._queue_email(campaign_id, client_id, "mail@example.ru"))
        attachment_dir = self.root / "attachments"
        attachment_dir.mkdir()
        for filename in etrn.ATTACHMENT_FILENAMES:
            (attachment_dir / filename).write_bytes(b"%PDF-test")

        with (
            patch.object(etrn, "ATTACHMENTS_DIR", attachment_dir),
            redirect_stdout(io.StringIO()),
        ):
            await etrn.send_campaign(dry_run=True, confirm_real_send=False)

        with closing(database.get_connection()) as connection:
            status = connection.execute(
                "SELECT status FROM mail_recipients WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()["status"]
            messages = connection.execute("SELECT COUNT(*) FROM mail_messages").fetchone()[0]
        self.assertEqual(status, "pending")
        self.assertEqual(messages, 0)

    async def test_send_fails_closed_when_pdf_is_missing(self) -> None:
        empty_dir = self.root / "empty"
        empty_dir.mkdir()
        with (
            patch.object(etrn, "ATTACHMENTS_DIR", empty_dir),
            self.assertRaises(FileNotFoundError),
        ):
            await etrn.send_campaign(dry_run=True, confirm_real_send=False)

    async def test_real_worker_uses_persistent_batch_cooldown(self) -> None:
        for number in range(3):
            client_id = self.add_client(
                f"25000001{number:02d}",
                [f"mail{number}@example.ru"],
            )
            campaign_id = etrn.ensure_etrn_campaign()
            etrn._queue_email(campaign_id, client_id, f"mail{number}@example.ru")
        attachment_dir = self.root / "attachments"
        attachment_dir.mkdir()
        for filename in etrn.ATTACHMENT_FILENAMES:
            (attachment_dir / filename).write_bytes(b"%PDF-test")
        provider = AsyncMock()
        provider.send.return_value = SMTPSendResult(True, "message-id")

        with (
            patch.object(etrn, "ATTACHMENTS_DIR", attachment_dir),
            patch.object(etrn.SMTPMailProvider, "from_env", return_value=provider),
            patch.object(etrn.asyncio, "sleep", new=AsyncMock()),
            patch.dict(
                "os.environ",
                {
                    "ETRN_BATCH_SIZE": "2",
                    "ETRN_MESSAGE_DELAY_MIN_SECONDS": "0",
                    "ETRN_MESSAGE_DELAY_MAX_SECONDS": "0",
                    "ETRN_COOLDOWN_MIN_SECONDS": "2400",
                    "ETRN_COOLDOWN_MAX_SECONDS": "2400",
                },
            ),
            redirect_stdout(io.StringIO()),
        ):
            await etrn.send_campaign(dry_run=False, confirm_real_send=True)

        self.assertEqual(provider.send.await_count, 2)
        with closing(database.get_connection()) as connection:
            campaign = connection.execute(
                "SELECT next_send_at, batch_sent_count FROM mail_campaigns WHERE id = ?",
                (campaign_id,),
            ).fetchone()
            statuses = [row["status"] for row in connection.execute(
                "SELECT status FROM mail_recipients WHERE campaign_id = ? ORDER BY id",
                (campaign_id,),
            ).fetchall()]
        self.assertIsNotNone(campaign["next_send_at"])
        self.assertEqual(campaign["batch_sent_count"], 0)
        self.assertEqual(statuses, ["sent", "sent", "pending"])

    async def test_temporary_smtp_error_is_deferred_for_retry(self) -> None:
        client_id = self.add_client("2500000020", ["retry@example.ru"])
        campaign_id = etrn.ensure_etrn_campaign()
        etrn._queue_email(campaign_id, client_id, "retry@example.ru")
        attachment_dir = self.root / "attachments"
        attachment_dir.mkdir()
        for filename in etrn.ATTACHMENT_FILENAMES:
            (attachment_dir / filename).write_bytes(b"%PDF-test")
        provider = AsyncMock()
        provider.send.return_value = SMTPSendResult(False, None, "connection timeout")

        with (
            patch.object(etrn, "ATTACHMENTS_DIR", attachment_dir),
            patch.object(etrn.SMTPMailProvider, "from_env", return_value=provider),
            patch.dict(
                "os.environ",
                {
                    "ETRN_MESSAGE_DELAY_MIN_SECONDS": "0",
                    "ETRN_MESSAGE_DELAY_MAX_SECONDS": "0",
                    "ETRN_RETRY_FIRST_SECONDS": "60",
                },
            ),
            redirect_stdout(io.StringIO()),
        ):
            await etrn.send_campaign(dry_run=False, confirm_real_send=True)

        with closing(database.get_connection()) as connection:
            recipient = connection.execute(
                "SELECT status, attempt_count, next_attempt_at FROM mail_recipients WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            message_status = connection.execute(
                "SELECT status FROM mail_messages ORDER BY id DESC LIMIT 1"
            ).fetchone()["status"]
        self.assertEqual(recipient["status"], "deferred")
        self.assertEqual(recipient["attempt_count"], 1)
        self.assertIsNotNone(recipient["next_attempt_at"])
        self.assertEqual(message_status, "failed")

    def test_template_uses_safe_personalization_and_tracking(self) -> None:
        generic = etrn_template.build_text_body(
            director_first_name="UNKNOWN", director_middle_name="",
            tracking_token=None,
        )
        personalized = etrn_template.build_html_body(
            director_first_name="Валерий", director_middle_name="Дмитриевич",
            tracking_token="token-1",
        )
        self.assertTrue(generic.startswith("Добрый день!"))
        self.assertIn("Добрый день, Валерий Дмитриевич!", personalized)
        self.assertIn("/t/c/token-1/cta_email", personalized)
        self.assertIn("/t/o/token-1.gif", personalized)

    def test_smtp_mime_preserves_utf8_pdf_filename(self) -> None:
        filename = etrn.ATTACHMENT_FILENAMES[0]
        message = MailMessage(
            recipient_id=1,
            to_email="test@example.ru",
            subject="ЭТрН",
            text_body="text",
            html_body="<p>html</p>",
            attachments=[MailAttachment(filename, b"%PDF", "application", "pdf")],
            include_logo=False,
        )
        provider = SMTPMailProvider(
            host="smtp.example", port=587, username="user", password="secret",
            from_email="sender@example.ru", from_name="Sender",
        )
        smtp = MagicMock()
        smtp_context = MagicMock()
        smtp_context.__enter__.return_value = smtp
        with patch("src.mailing.smtp_provider.smtplib.SMTP", return_value=smtp_context):
            result = provider._send_sync(message)

        self.assertTrue(result.success)
        mime_message = smtp.send_message.call_args.args[0]
        attachment = next(mime_message.iter_attachments())
        self.assertEqual(attachment.get_content_type(), "application/pdf")
        self.assertEqual(attachment.get_filename(), filename)


if __name__ == "__main__":
    unittest.main()
