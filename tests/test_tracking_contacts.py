from __future__ import annotations

from contextlib import closing
import gc
import html as html_lib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import warnings

from aiohttp.test_utils import TestClient, TestServer

import src.database as database
from src import config
from src.mailing.templates import new_companies
from src.tracking import app as tracking_app


class TrackingContactsHttpTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory(
            prefix="projectsbis_tracking_contacts_"
        )
        self.database_path = (
            Path(self.temp_directory.name) / "project.db"
        )
        self.database_patch = patch.object(
            database,
            "DATABASE_FILE",
            self.database_path,
        )
        self.database_patch.start()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            database.initialize_database()
            gc.collect()

        self.tracking_token = "tracking-contact-fixture"
        self.message_id = self._create_message_fixture()
        self.client = TestClient(
            TestServer(tracking_app.create_app())
        )
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.database_patch.stop()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            gc.collect()
        self.temp_directory.cleanup()

    def assert_whatsapp_location(self, location: str) -> None:
        """Сравнить destination без зависимости от URL-нормализации aiohttp."""
        actual = urlsplit(location)
        expected = urlsplit(
            str(config.CONTACT_WHATSAPP_URL)
        )
        self.assertEqual(actual.scheme, "https")
        self.assertEqual(actual.netloc, expected.netloc)
        self.assertEqual(actual.path, expected.path)
        self.assertEqual(
            parse_qs(actual.query),
            parse_qs(expected.query),
        )

    def _create_message_fixture(self) -> int:
        with closing(database.get_connection()) as connection:
            campaign = connection.execute(
                """
                SELECT id
                FROM mail_campaigns
                WHERE name = 'new_companies_daily'
                """
            ).fetchone()
            campaign_id = int(campaign["id"])
            client_id = int(
                connection.execute(
                    """
                    INSERT INTO clients (
                        spp_uuid,
                        inn,
                        name
                    )
                    VALUES (
                        'tracking-contact-fixture',
                        '9900000088',
                        'Tracking Contact Fixture'
                    )
                    """
                ).lastrowid
            )
            recipient_id = int(
                connection.execute(
                    """
                    INSERT INTO mail_recipients (
                        campaign_id,
                        client_id,
                        email
                    )
                    VALUES (?, ?, 'tracking@example.invalid')
                    """,
                    (campaign_id, client_id),
                ).lastrowid
            )
            message_id = int(
                connection.execute(
                    """
                    INSERT INTO mail_messages (
                        recipient_id,
                        provider,
                        tracking_token,
                        status,
                        delivery_status,
                        is_test
                    )
                    VALUES (?, 'smtp', ?, 'sent', 'delivered', 0)
                    """,
                    (recipient_id, self.tracking_token),
                ).lastrowid
            )
            connection.commit()

        return message_id

    async def test_cta_email_redirects_to_whatsapp_and_records_click(
        self,
    ) -> None:
        response = await self.client.get(
            f"/t/c/{self.tracking_token}/cta_email",
            allow_redirects=False,
        )

        self.assertEqual(response.status, 302)
        self.assert_whatsapp_location(
            response.headers["Location"]
        )
        self.assertTrue(
            response.headers["Location"].startswith(
                "https://wa.me/"
            )
        )
        self.assertFalse(
            response.headers["Location"].startswith("mailto:")
        )

        with closing(database.get_connection()) as connection:
            events = connection.execute(
                """
                SELECT event_type, event_data
                FROM mail_events
                WHERE message_id = ?
                ORDER BY id
                """,
                (self.message_id,),
            ).fetchall()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "clicked")
        self.assertEqual(
            events[0]["event_data"],
            '{"click_key":"cta_email"}',
        )

    async def test_unknown_click_key_keeps_safe_404_behavior(self) -> None:
        response = await self.client.get(
            f"/t/c/{self.tracking_token}/unknown_target",
            allow_redirects=False,
        )

        self.assertEqual(response.status, 404)

        with closing(database.get_connection()) as connection:
            count = connection.execute(
                """
                SELECT COUNT(*)
                FROM mail_events
                WHERE message_id = ?
                """,
                (self.message_id,),
            ).fetchone()[0]

        self.assertEqual(count, 0)

    async def test_etrn_whatsapp_redirect_keeps_tracking_and_custom_text(
        self,
    ) -> None:
        response = await self.client.get(
            f"/t/c/{self.tracking_token}/etrn_whatsapp",
            allow_redirects=False,
        )

        self.assertEqual(response.status, 302)
        actual = urlsplit(response.headers["Location"])
        expected = urlsplit(str(config.CONTACT_ETRN_WHATSAPP_URL))
        self.assertEqual(
            (actual.netloc, actual.path),
            (expected.netloc, expected.path),
        )
        self.assertEqual(parse_qs(actual.query), parse_qs(expected.query))
        self.assertEqual(
            parse_qs(actual.query)["text"],
            [config.ETRN_WHATSAPP_TEXT],
        )

        with closing(database.get_connection()) as connection:
            event_data = connection.execute(
                "SELECT event_data FROM mail_events WHERE message_id = ? ORDER BY id DESC",
                (self.message_id,),
            ).fetchone()["event_data"]
        self.assertEqual(event_data, '{"click_key":"etrn_whatsapp"}')

    async def test_unknown_old_token_does_not_return_500(self) -> None:
        response = await self.client.get(
            "/t/c/old-or-missing-token/cta_email",
            allow_redirects=False,
        )

        self.assertEqual(response.status, 302)
        self.assert_whatsapp_location(
            response.headers["Location"]
        )


class NewCompaniesContactTemplateTestCase(unittest.TestCase):
    @staticmethod
    def _build_html(*, tracking_token: str | None) -> str:
        return new_companies.build_html_body(
            client_name='ООО <script>alert("unsafe")</script>',
            inn="2500000000",
            director_first_name="Иван",
            director_middle_name="Иванович",
            tracking_token=tracking_token,
        )

    @staticmethod
    def _cta_href(html_body: str) -> str:
        cta_position = html_body.index("Подобрать решение")
        href_start = html_body.rfind(
            'href="',
            0,
            cta_position,
        ) + len('href="')
        href_end = html_body.index('"', href_start)
        return html_body[href_start:href_end]

    def test_cta_keeps_tracking_key_and_direct_fallback_is_whatsapp(
        self,
    ) -> None:
        tracked_html = self._build_html(
            tracking_token="token-1"
        )
        direct_html = self._build_html(
            tracking_token=None
        )

        self.assertEqual(
            self._cta_href(tracked_html),
            "https://mail.projectsbis.ru/t/c/token-1/cta_email",
        )
        self.assertEqual(
            self._cta_href(direct_html),
            html_lib.escape(
                str(config.CONTACT_WHATSAPP_URL),
                quote=True,
            ),
        )
        self.assertFalse(
            self._cta_href(direct_html).startswith("mailto:")
        )

    def test_contact_email_uses_configuration_and_html_is_escaped(
        self,
    ) -> None:
        configured_email = "sales+<unsafe>@atlantis.ooo"

        with patch.object(
            new_companies,
            "CONTACT_EMAIL",
            configured_email,
        ):
            html_body = self._build_html(
                tracking_token="token-2"
            )

        expected_url = config.build_contact_email_url(
            configured_email
        )
        self.assertIn(
            f'href="{html_lib.escape(expected_url, quote=True)}"',
            html_body,
        )
        self.assertIn(
            "sales+&lt;unsafe&gt;@atlantis.ooo",
            html_body,
        )
        self.assertNotIn(configured_email, html_body)
        self.assertNotIn(
            '<script>alert("unsafe")</script>',
            html_body,
        )

    def test_contact_email_url_uses_current_configured_address(self) -> None:
        self.assertEqual(
            config.build_contact_email_url(
                "info@atlantis.ooo"
            ).split("?", 1)[0],
            "mailto:info@atlantis.ooo",
        )


if __name__ == "__main__":
    unittest.main()
