from __future__ import annotations

import asyncio
import gc
import io
import tempfile
import unittest
import warnings
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

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
            patch.object(etrn, "load_clients_by_inn", new=AsyncMock(return_value=[])),
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
            run_count = connection.execute(
                "SELECT COUNT(*) FROM mail_runs WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()[0]
        self.assertEqual([row["normalized_email"] for row in rows], ["a@example.ru", "b@example.ru"])
        self.assertTrue({"client_not_found", "no_email", "invalid_email", "duplicate_etrn"} <= reasons)
        self.assertEqual(run_count, 0)
        snapshot = etrn._preparation_snapshot(campaign_id)
        self.assertEqual(snapshot["input_inns"], 4)
        self.assertEqual(snapshot["clients_found"], 3)
        self.assertEqual(snapshot["without_email"], 1)
        self.assertEqual(snapshot["invalid_email"], 1)
        self.assertEqual(snapshot["duplicate_etrn"], 1)
        self.assertEqual(snapshot["queued"], 2)
        self.assertEqual(snapshot["skipped_count"], 4)

    async def test_prepare_loads_missing_client_then_uses_regular_enrichment(self) -> None:
        inn = "7725806898"
        inn_file = self.root / "fallback.txt"
        inn_file.write_text(f"{inn}\n", encoding="utf-8")

        async def load_client(
            inns: list[str], *, source_type: str, source_value: str,
        ) -> list[dict[str, str]]:
            self.assertEqual(inns, [inn])
            client_id = database.upsert_client_by_inn_lookup(
                inn=inn,
                spp_uuid="4e450d9b-81b7-4bc4-b71a-a5dfb83116a8",
            )
            database.add_client_source(
                client_id=client_id,
                source_type=source_type,
                source_value=source_value,
            )
            return [{"inn": inn, "spp_uuid": "4e450d9b-81b7-4bc4-b71a-a5dfb83116a8"}]

        async def enrich(client: dict[str, object]) -> None:
            with closing(database.get_connection()) as connection, connection:
                connection.execute(
                    "INSERT INTO client_contacts (client_id, contact_type, value) VALUES (?, 'email', ?)",
                    (int(client["id"]), "fallback@example.ru"),
                )

        lookup = AsyncMock(side_effect=load_client)
        with (
            patch.object(etrn, "load_clients_by_inn", new=lookup),
            patch.object(etrn, "_enrich_client", new=AsyncMock(side_effect=enrich)) as enrichment,
            redirect_stdout(io.StringIO()),
        ):
            campaign_id, first_stats = await etrn.prepare_audience(inn_file)
            _, second_stats = await etrn.prepare_audience(inn_file)

        self.assertEqual(lookup.await_count, 1)
        lookup.assert_awaited_once_with(
            [inn], source_type="inn_file", source_value=inn_file.name,
        )
        enrichment.assert_awaited_once()
        self.assertEqual(first_stats.clients_found, 1)
        self.assertEqual(first_stats.email_found_after_enrichment, 1)
        self.assertEqual(first_stats.queued, 1)
        self.assertEqual(second_stats.clients_found, 1)
        self.assertEqual(second_stats.duplicate_etrn, 1)
        with closing(database.get_connection()) as connection:
            client = connection.execute(
                "SELECT id, spp_uuid FROM clients WHERE inn = ?", (inn,),
            ).fetchone()
            source = connection.execute(
                "SELECT source_type, source_value FROM client_sources WHERE client_id = ?",
                (client["id"],),
            ).fetchone()
            recipient = connection.execute(
                "SELECT normalized_email FROM mail_recipients WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
        self.assertEqual(client["spp_uuid"], "4e450d9b-81b7-4bc4-b71a-a5dfb83116a8")
        self.assertEqual(dict(source), {"source_type": "inn_file", "source_value": inn_file.name})
        self.assertEqual(recipient["normalized_email"], "fallback@example.ru")

    async def test_prepare_distinguishes_sbis_not_found_from_lookup_failure(self) -> None:
        inn = "7725806898"
        inn_file = self.root / "fallback.txt"
        inn_file.write_text(f"{inn}\n", encoding="utf-8")

        with (
            patch.object(etrn, "load_clients_by_inn", new=AsyncMock(return_value=[])),
            redirect_stdout(io.StringIO()),
        ):
            campaign_id, _ = await etrn.prepare_audience(inn_file)

        with closing(database.get_connection()) as connection:
            not_found_reasons = [row["event_type"] for row in connection.execute(
                "SELECT event_type FROM mail_audience_events WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchall()]
            connection.execute(
                "DELETE FROM mail_audience_events WHERE campaign_id = ?", (campaign_id,),
            )
            connection.commit()
        self.assertIn("client_not_found", not_found_reasons)
        self.assertNotIn("sbis_lookup_failed", not_found_reasons)

        with (
            patch.object(
                etrn,
                "load_clients_by_inn",
                new=AsyncMock(side_effect=RuntimeError("auth expired")),
            ),
            redirect_stdout(io.StringIO()),
        ):
            await etrn.prepare_audience(inn_file)

        with closing(database.get_connection()) as connection:
            failed = connection.execute(
                "SELECT event_type, event_data FROM mail_audience_events "
                "WHERE campaign_id = ? AND event_type != 'prepare_summary'",
                (campaign_id,),
            ).fetchall()
        self.assertEqual(
            [(row["event_type"], row["event_data"]) for row in failed],
            [("sbis_lookup_failed", "RuntimeError")],
        )

    async def test_prepare_keeps_enrichment_failure_separate_after_fallback(self) -> None:
        inn = "7725806898"
        inn_file = self.root / "fallback.txt"
        inn_file.write_text(f"{inn}\n", encoding="utf-8")

        async def load_client(*args: object, **kwargs: object) -> list[dict[str, str]]:
            database.upsert_client_by_inn_lookup(
                inn=inn,
                spp_uuid="4e450d9b-81b7-4bc4-b71a-a5dfb83116a8",
            )
            return [{"inn": inn, "spp_uuid": "4e450d9b-81b7-4bc4-b71a-a5dfb83116a8"}]

        with (
            patch.object(etrn, "load_clients_by_inn", new=AsyncMock(side_effect=load_client)),
            patch.object(
                etrn, "_enrich_client", new=AsyncMock(side_effect=RuntimeError("card failed")),
            ),
            redirect_stdout(io.StringIO()),
        ):
            campaign_id, stats = await etrn.prepare_audience(inn_file)

        with closing(database.get_connection()) as connection:
            reasons = {row["event_type"] for row in connection.execute(
                "SELECT event_type FROM mail_audience_events WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchall()}
        self.assertEqual(stats.clients_found, 1)
        self.assertEqual(stats.without_email, 1)
        self.assertIn("enrichment_failed", reasons)
        self.assertNotIn("client_not_found", reasons)
        self.assertNotIn("sbis_lookup_failed", reasons)

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
            message_columns = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(mail_messages)"
                ).fetchall()
            }
            run_columns = {
                row["name"] for row in connection.execute(
                    "PRAGMA table_info(mail_runs)"
                ).fetchall()
            }
            quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        self.assertTrue({"campaign_family", "next_send_at", "batch_sent_count"} <= campaign_columns)
        self.assertTrue({"normalized_email", "attempt_count", "next_attempt_at"} <= recipient_columns)
        self.assertTrue({
            "smtp_recipient_email",
            "is_test_recipient",
        } <= message_columns)
        self.assertTrue({
            "input_inns_count",
            "clients_found_count",
            "clients_without_email_count",
            "email_found_after_enrichment_count",
            "invalid_email_count",
            "duplicate_count",
            "bounced_before_send_count",
            "prepared_email_count",
            "skipped_count",
            "pending_count",
            "batch_number",
        } <= run_columns)
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
            patch.dict(
                "os.environ",
                {
                    "ETRN_TEST_RECIPIENTS_ENABLED": "false",
                    "ETRN_TEST_DISABLE_ATTACHMENTS": "true",
                },
            ),
            self.assertRaises(FileNotFoundError),
        ):
            await etrn.send_campaign(dry_run=True, confirm_real_send=False)

    def test_config_command_reports_mode_count_and_batch_without_send(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "ETRN_BATCH_LIMIT": "4",
                    "ETRN_TEST_RECIPIENTS_ENABLED": "true",
                    "ETRN_TEST_RECIPIENTS": "first@example.ru,second@example.ru",
                    "ETRN_TEST_DISABLE_ATTACHMENTS": "false",
                },
            ),
            redirect_stdout(output := io.StringIO()),
        ):
            etrn.print_configuration()

        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "ETRN test recipient mode: ENABLED",
                "Configured test recipients: 2",
                "Attachments disabled in test mode: False",
                "Batch limit: 4",
            ],
        )

    def test_attachment_flag_cannot_disable_production_attachments(self) -> None:
        with patch.dict(
            "os.environ",
            {"ETRN_TEST_DISABLE_ATTACHMENTS": "true"},
        ):
            self.assertFalse(etrn.should_disable_test_attachments(
                etrn.TestRecipientConfig(enabled=False, recipients=()),
            ))
            self.assertTrue(etrn.should_disable_test_attachments(
                etrn.TestRecipientConfig(
                    enabled=True,
                    recipients=("test@example.ru",),
                ),
            ))

    async def test_enabled_test_mode_with_invalid_list_fails_before_database(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "ETRN_TEST_RECIPIENTS_ENABLED": "true",
                    "ETRN_TEST_RECIPIENTS": "invalid-address",
                },
            ),
            patch.object(etrn, "initialize_database") as initialize,
            self.assertRaisesRegex(ValueError, "некорректные email"),
        ):
            await etrn.send_campaign(
                dry_run=False,
                confirm_real_send=True,
            )

        initialize.assert_not_called()

    def test_batch_limit_prefers_new_setting_and_caps_at_500(self) -> None:
        with patch.dict(
            "os.environ",
            {"ETRN_BATCH_LIMIT": "900", "ETRN_BATCH_SIZE": "2"},
        ):
            self.assertEqual(etrn.get_batch_limit(), 500)

    async def test_test_recipients_cycle_without_changing_real_audience(self) -> None:
        first_client = self.add_client(
            "2500000040",
            ["real-one@example.ru", "real-two@example.ru"],
        )
        second_client = self.add_client(
            "2500000041",
            ["real-three@example.ru"],
        )
        campaign_id = etrn.ensure_etrn_campaign()
        self.assertTrue(
            etrn._queue_email(campaign_id, first_client, "real-one@example.ru")
        )
        self.assertTrue(
            etrn._queue_email(campaign_id, first_client, "real-two@example.ru")
        )
        self.assertTrue(
            etrn._queue_email(campaign_id, second_client, "real-three@example.ru")
        )
        attachment_dir = self.root / "attachments-test-recipient"
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
                    "ETRN_BATCH_LIMIT": "3",
                    "ETRN_TEST_RECIPIENTS_ENABLED": "true",
                    "ETRN_TEST_RECIPIENTS": (
                        "test-a@example.ru,test-b@example.ru"
                    ),
                    "ETRN_TEST_DISABLE_ATTACHMENTS": "true",
                    "ETRN_MESSAGE_DELAY_MIN_SECONDS": "0",
                    "ETRN_MESSAGE_DELAY_MAX_SECONDS": "0",
                },
            ),
            redirect_stdout(output := io.StringIO()),
        ):
            await etrn.send_campaign(
                dry_run=False,
                confirm_real_send=True,
            )

        smtp_addresses = [
            call.args[0].to_email
            for call in provider.send.await_args_list
        ]
        self.assertEqual(
            smtp_addresses,
            ["test-a@example.ru", "test-b@example.ru", "test-a@example.ru"],
        )
        outbound_messages = [
            call.args[0]
            for call in provider.send.await_args_list
        ]
        self.assertTrue(all(
            not message.attachments
            for message in outbound_messages
        ))
        self.assertTrue(all(
            message.subject == etrn_template.SUBJECT
            for message in outbound_messages
        ))
        self.assertTrue(all(
            "/etrn_whatsapp" in message.html_body
            for message in outbound_messages
        ))
        with closing(database.get_connection()) as connection:
            recipients = connection.execute(
                """
                SELECT email, status, client_id FROM mail_recipients
                WHERE campaign_id = ? ORDER BY id
                """,
                (campaign_id,),
            ).fetchall()
            messages = connection.execute(
                """
                SELECT smtp_recipient_email, is_test_recipient, is_test,
                       recipient_id, tracking_token
                FROM mail_messages ORDER BY id
                """
            ).fetchall()
        self.assertEqual(
            [row["email"] for row in recipients],
            [
                "real-one@example.ru",
                "real-two@example.ru",
                "real-three@example.ru",
            ],
        )
        self.assertEqual([row["status"] for row in recipients], ["sent"] * 3)
        self.assertEqual(
            [row["smtp_recipient_email"] for row in messages],
            smtp_addresses,
        )
        self.assertEqual([row["is_test_recipient"] for row in messages], [1] * 3)
        self.assertEqual([row["is_test"] for row in messages], [0] * 3)
        self.assertEqual(len({row["recipient_id"] for row in messages}), 3)
        self.assertTrue(all(row["tracking_token"] for row in messages))
        first_token = str(messages[0]["tracking_token"])
        self.assertTrue(database.record_mail_open(first_token))
        self.assertTrue(database.record_mail_click(
            tracking_token=first_token,
            click_key="whatsapp",
        ))
        tracked_message = database.get_mail_message_details(1)
        assert tracked_message is not None
        self.assertEqual(tracked_message["opened_count"], 1)
        self.assertEqual(tracked_message["clicked_count"], 1)
        self.assertEqual(
            tracked_message["smtp_recipient_email"],
            "test-a@example.ru",
        )
        self.assertEqual(tracked_message["is_test_recipient"], 1)
        self.assertIn("ETRN TEST RECIPIENT MODE ENABLED", output.getvalue())
        for email in smtp_addresses + [row["email"] for row in recipients]:
            self.assertNotIn(email, output.getvalue())

    async def test_worker_exits_during_cooldown_and_next_run_continues(self) -> None:
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
                    "ETRN_BATCH_LIMIT": "2",
                    "ETRN_TEST_RECIPIENTS_ENABLED": "false",
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
                "SELECT next_send_at FROM mail_campaigns WHERE id = ?",
                (campaign_id,),
            ).fetchone()
            statuses = [row["status"] for row in connection.execute(
                "SELECT status FROM mail_recipients WHERE campaign_id = ? ORDER BY id",
                (campaign_id,),
            ).fetchall()]
            run_count = connection.execute(
                "SELECT COUNT(*) FROM mail_runs WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()[0]
        self.assertIsNotNone(campaign["next_send_at"])
        self.assertEqual(statuses, ["sent", "sent", "pending"])
        self.assertEqual(run_count, 1)

        with (
            patch.object(etrn, "load_attachments") as load_attachments,
            patch.object(etrn.SMTPMailProvider, "from_env") as provider_factory,
            patch.object(etrn.asyncio, "sleep", new=AsyncMock()) as cooldown_sleep,
            patch.dict(
                "os.environ",
                {
                    "ETRN_BATCH_LIMIT": "2",
                    "ETRN_TEST_RECIPIENTS_ENABLED": "false",
                    "ETRN_MESSAGE_DELAY_MIN_SECONDS": "0",
                    "ETRN_MESSAGE_DELAY_MAX_SECONDS": "0",
                    "ETRN_COOLDOWN_MIN_SECONDS": "2400",
                    "ETRN_COOLDOWN_MAX_SECONDS": "2400",
                },
            ),
            redirect_stdout(cooldown_output := io.StringIO()),
        ):
            await etrn.send_campaign(dry_run=False, confirm_real_send=True)

        load_attachments.assert_not_called()
        provider_factory.assert_not_called()
        cooldown_sleep.assert_not_awaited()
        self.assertIn("ETRN_COOLDOWN_ACTIVE", cooldown_output.getvalue())
        self.assertIn("next_send_at=", cooldown_output.getvalue())
        self.assertIn("remaining_seconds=", cooldown_output.getvalue())
        with closing(database.get_connection()) as connection:
            statuses_during_cooldown = [row["status"] for row in connection.execute(
                "SELECT status FROM mail_recipients WHERE campaign_id = ? ORDER BY id",
                (campaign_id,),
            ).fetchall()]
            run_count_during_cooldown = connection.execute(
                "SELECT COUNT(*) FROM mail_runs WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()[0]
            connection.execute(
                "UPDATE mail_campaigns SET next_send_at = '2000-01-01 00:00:00' "
                "WHERE id = ?",
                (campaign_id,),
            )
            connection.commit()
        self.assertEqual(statuses_during_cooldown, statuses)
        self.assertEqual(run_count_during_cooldown, run_count)
        self.assertEqual(provider.send.await_count, 2)

        with (
            patch.object(etrn, "ATTACHMENTS_DIR", attachment_dir),
            patch.object(etrn.SMTPMailProvider, "from_env", return_value=provider),
            patch.object(etrn.asyncio, "sleep", new=AsyncMock()),
            patch.dict(
                "os.environ",
                {
                    "ETRN_BATCH_LIMIT": "2",
                    "ETRN_TEST_RECIPIENTS_ENABLED": "false",
                    "ETRN_MESSAGE_DELAY_MIN_SECONDS": "0",
                    "ETRN_MESSAGE_DELAY_MAX_SECONDS": "0",
                    "ETRN_COOLDOWN_MIN_SECONDS": "2400",
                    "ETRN_COOLDOWN_MAX_SECONDS": "2400",
                },
            ),
            redirect_stdout(io.StringIO()),
        ):
            await etrn.send_campaign(dry_run=False, confirm_real_send=True)

        self.assertEqual(provider.send.await_count, 3)
        with closing(database.get_connection()) as connection:
            runs = connection.execute(
                """
                SELECT batch_number, recipients_added, status
                FROM mail_runs WHERE campaign_id = ? ORDER BY batch_number
                """,
                (campaign_id,),
            ).fetchall()
            statuses = [row["status"] for row in connection.execute(
                "SELECT status FROM mail_recipients WHERE campaign_id = ? ORDER BY id",
                (campaign_id,),
            ).fetchall()]
            next_send_at = connection.execute(
                "SELECT next_send_at FROM mail_campaigns WHERE id = ?",
                (campaign_id,),
            ).fetchone()[0]
        self.assertEqual(
            [(row["batch_number"], row["recipients_added"]) for row in runs],
            [(1, 2), (2, 1)],
        )
        self.assertEqual([row["status"] for row in runs], ["success", "success"])
        self.assertEqual(statuses, ["sent", "sent", "sent"])
        self.assertIsNone(next_send_at)

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
            patch.object(etrn.asyncio, "sleep", new=AsyncMock()),
            patch.dict(
                "os.environ",
                {
                    "ETRN_MESSAGE_DELAY_MIN_SECONDS": "0",
                    "ETRN_MESSAGE_DELAY_MAX_SECONDS": "0",
                    "ETRN_RETRY_FIRST_SECONDS": "60",
                    "ETRN_TEST_RECIPIENTS_ENABLED": "false",
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

    def test_restart_closes_interrupted_batch_before_next_number(self) -> None:
        campaign_id = etrn.ensure_etrn_campaign()
        first_run_id, first_batch = database.create_mail_batch_run(
            campaign_id=campaign_id,
            selection_id=0,
        )

        self.assertEqual(etrn._recover_interrupted_runs(campaign_id), 1)
        second_run_id, second_batch = database.create_mail_batch_run(
            campaign_id=campaign_id,
            selection_id=0,
        )

        with closing(database.get_connection()) as connection:
            first_run = connection.execute(
                "SELECT status, error_text FROM mail_runs WHERE id = ?",
                (first_run_id,),
            ).fetchone()
        self.assertEqual(first_batch, 1)
        self.assertEqual(second_batch, 2)
        self.assertNotEqual(first_run_id, second_run_id)
        self.assertEqual(first_run["status"], "failed")
        self.assertEqual(first_run["error_text"], "worker_restarted")

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
        self.assertIn("/t/c/token-1/etrn_whatsapp", personalized)
        self.assertIn("/t/c/token-1/phone", personalized)
        self.assertIn("/t/c/token-1/telegram", personalized)
        self.assertIn("/t/c/token-1/max", personalized)
        self.assertIn("/t/o/token-1.gif", personalized)
        self.assertIn("WhatsApp:", generic)
        self.assertIn("Telegram:", generic)
        self.assertIn("MAX:", generic)

        whatsapp_text = parse_qs(
            urlsplit(etrn_template.CONTACT_ETRN_WHATSAPP_URL).query
        )["text"]
        self.assertEqual(whatsapp_text, [
            "Обращение по ЭТрН из письма\n"
            "Здравствуйте! Меня заинтересовало ваше предложение по подключению ЭТрН."
        ])

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
