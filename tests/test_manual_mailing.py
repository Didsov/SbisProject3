from __future__ import annotations

import io
import gc
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src import database as db
from src.mailing import manual
from src.mailing.smtp_provider import SMTPSendResult


class ManualMailingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database_patch = patch.object(db, "DATABASE_FILE", self.root / "project.db")
        self.database_patch.start()
        db.initialize_database()

    def tearDown(self) -> None:
        self.database_patch.stop()
        gc.collect()
        self.temporary.cleanup()

    def csv(self, text: str) -> Path:
        path = self.root / "recipients.csv"
        path.write_text(text, encoding="utf-8-sig")
        return path

    def test_prepare_preserves_order_names_and_deduplicates(self) -> None:
        path = self.csv(
            "email;contact_name\n"
            "FIRST@Example.RU;Иван Петрович\n"
            "second@example.ru;\n"
            "first@example.ru;Дубликат\n"
        )
        with redirect_stdout(io.StringIO()):
            campaign_id = manual.prepare_queue(
                csv_path=path,
                campaign_name="manual-october",
                template_name="new_companies",
            )
        recipients = db.get_pending_mail_recipients(campaign_id)
        self.assertEqual(
            [(row["email"], row["contact_name"]) for row in recipients],
            [("first@example.ru", "Иван Петрович"), ("second@example.ru", None)],
        )
        self.assertEqual(recipients[0]["director_first_name"], "Иван Петрович")
        self.assertEqual(recipients[0]["director_middle_name"], "")

    def test_invalid_csv_is_atomic(self) -> None:
        path = self.csv(
            "email;contact_name\n"
            "valid@example.ru;Иван\n"
            "not-an-email;Пётр\n"
        )
        with self.assertRaisesRegex(ValueError, "строке 3"):
            manual.prepare_queue(
                csv_path=path,
                campaign_name="must-not-exist",
                template_name="new_companies",
            )
        with closing(db.get_connection()) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM mail_campaigns WHERE name='must-not-exist'"
            ).fetchone())

    def test_reset_backups_and_clears_only_mailing_data(self) -> None:
        path = self.csv("email;contact_name\nfirst@example.ru;Иван\n")
        with redirect_stdout(io.StringIO()):
            manual.prepare_queue(
                csv_path=path,
                campaign_name="old-mailing",
                template_name="new_companies",
            )
        with closing(db.get_connection()) as connection:
            clients_before = connection.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        backup = self.root / "before-reset.db"
        with redirect_stdout(io.StringIO()):
            manual.reset_mailing_data(backup_path=backup)
        self.assertTrue(backup.is_file())
        with closing(db.get_connection()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM clients").fetchone()[0], clients_before)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM mail_campaigns").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM mail_recipients").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT paused FROM mail_control WHERE id=1").fetchone()[0], 1)

    async def test_test_send_targets_exactly_three_configured_addresses(self) -> None:
        provider = AsyncMock()
        provider.send.return_value = SMTPSendResult(True, "accepted")
        environment = {
            "ETRN_TEST_RECIPIENTS_ENABLED": "true",
            "ETRN_TEST_RECIPIENTS": "one@example.ru,two@example.ru,three@example.ru",
            "MANUAL_TEST_INTERVAL_SECONDS": "0",
        }
        with (
            patch.dict("os.environ", environment, clear=False),
            patch.object(manual.SMTPMailProvider, "from_env", return_value=provider),
            redirect_stdout(io.StringIO()),
        ):
            await manual.send_test(
                template_name="new_companies",
                contact_name="Иван Петрович",
            )
        messages = [call.args[0] for call in provider.send.await_args_list]
        self.assertEqual(
            [message.to_email for message in messages],
            ["one@example.ru", "two@example.ru", "three@example.ru"],
        )
        self.assertEqual(
            [message.subject.split("]", 1)[0] + "]" for message in messages],
            ["[TEST 1/3]", "[TEST 2/3]", "[TEST 3/3]"],
        )
        with closing(db.get_connection()) as connection:
            rows = connection.execute(
                """SELECT is_test,is_test_recipient,smtp_recipient_email,status
                   FROM mail_messages ORDER BY id"""
            ).fetchall()
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(row["is_test"] == 1 for row in rows))
            self.assertTrue(all(row["is_test_recipient"] == 1 for row in rows))
            self.assertTrue(all(row["status"] == "sent" for row in rows))
            self.assertEqual(
                [row["smtp_recipient_email"] for row in rows],
                ["one@example.ru", "two@example.ru", "three@example.ru"],
            )
            self.assertEqual(
                connection.execute(
                    """SELECT status FROM mail_recipients
                       WHERE campaign_id=(SELECT id FROM mail_campaigns
                                          WHERE name='_manual_test')"""
                ).fetchone()[0],
                "pending",
            )


if __name__ == "__main__":
    unittest.main()
