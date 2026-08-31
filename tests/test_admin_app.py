from __future__ import annotations

import unittest
from unittest.mock import call, patch

from aiohttp.test_utils import TestClient, TestServer

from src.admin import app as admin_app


RUN = {
    "run_id": 7,
    "campaign_id": 3,
    "campaign_name": "Campaign <unsafe>",
    "campaign_family": "new_companies",
    "selection_id": 5984,
    "trigger": "manual",
    "status": "partial",
    "started_at": "2026-08-20 09:00:00",
    "finished_at": "2026-08-20 09:12:00",
    "recipients_added": 2,
    "sent_count": 2,
    "delivered_count": 1,
    "bounced_count": 1,
    "deferred_count": 0,
    "failed_count": 0,
    "input_inns_count": 0,
    "clients_found_count": 0,
    "clients_without_email_count": 0,
    "email_found_after_enrichment_count": 0,
    "invalid_email_count": 0,
    "duplicate_count": 0,
    "bounced_before_send_count": 0,
    "prepared_email_count": 2,
    "skipped_count": 0,
    "pending_count": 0,
    "open_count": 2,
    "unique_open_count": 1,
    "click_count": 1,
    "unique_click_count": 1,
}

EMPTY_RUN = {
    **RUN,
    "run_id": 8,
    "status": "success",
    "started_at": "2026-08-21 09:00:00",
    "finished_at": "2026-08-21 09:01:00",
    "recipients_added": 0,
    "sent_count": 0,
    "delivered_count": 0,
    "bounced_count": 0,
    "deferred_count": 0,
    "failed_count": 0,
}

DETAILS = {
    **RUN,
    "error_text": None,
}

MESSAGES = [
    {
        "message_id": 21,
        "company_name": "Company <One>",
        "inn": "9900000001",
        "email": "one@example.invalid",
        "send_status": "sent",
        "delivery_status": "delivered",
        "sent_at": "2026-08-20 09:01:00",
        "delivered_at": "2026-08-20 09:02:00",
        "opened_count": 2,
        "clicked_count": 1,
        "last_event_at": "2026-08-20 09:05:00",
    },
]

MESSAGE_DETAILS = {
    "message_id": 21,
    "run_id": 7,
    "campaign_id": 3,
    "campaign_name": "Campaign <unsafe>",
    "campaign_family": "new_companies",
    "run_status": "partial",
    "run_started_at": "2026-08-20 09:00:00",
    "client_id": 11,
    "company_name": "Company <One>",
    "inn": "9900000001",
    "email": "one@example.invalid",
    "provider": "smtp",
    "provider_message_id": "provider-<unsafe>",
    "send_status": "sent",
    "delivery_status": "delivered",
    "sent_at": "2026-08-20 09:01:00",
    "delivered_at": "2026-08-20 09:02:00",
    "tracking_token": "token-<unsafe>",
    "opened_count": 2,
    "clicked_count": 1,
    "last_event_at": "2026-08-20 09:05:00",
}

MESSAGE_TIMELINE = [
    {
        "event_id": 40,
        "event_type": "sent",
        "event_at": "2026-08-20 09:01:00",
        "event_data": None,
    },
    {
        "event_id": 41,
        "event_type": "delivered",
        "event_at": "2026-08-20 09:02:00",
        "event_data": None,
    },
    {
        "event_id": 42,
        "event_type": "opened",
        "event_at": "2026-08-20 09:04:00",
        "event_data": None,
    },
    {
        "event_id": 43,
        "event_type": "clicked",
        "event_at": "2026-08-20 09:05:00",
        "event_data": (
            '{"click_key":"whatsapp",'
            '"unsafe":"<script>alert(1)</script>"}'
        ),
    },
]

EVENTS = [
    {
        "event_id": 31,
        "message_id": 21,
        "company_name": "Company <One>",
        "email": "one@example.invalid",
        "event_type": "clicked",
        "event_at": "2026-08-20 09:05:00",
        "event_data": '<script>alert("unsafe")</script>',
    },
]


class AdminAppHttpTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.recent_patcher = patch.object(
            admin_app,
            "get_recent_mail_runs",
            return_value=[EMPTY_RUN],
        )
        self.latest_mailing_patcher = patch.object(
            admin_app,
            "get_latest_mail_run_with_sent_messages",
            return_value=RUN,
        )
        self.details_patcher = patch.object(
            admin_app,
            "get_mail_run_details",
            return_value=DETAILS,
        )
        self.messages_patcher = patch.object(
            admin_app,
            "get_mail_run_messages",
            return_value=MESSAGES,
        )
        self.events_patcher = patch.object(
            admin_app,
            "get_mail_run_events",
            return_value=EVENTS,
        )
        self.message_details_patcher = patch.object(
            admin_app,
            "get_mail_message_details",
            return_value=MESSAGE_DETAILS,
        )
        self.message_timeline_patcher = patch.object(
            admin_app,
            "get_mail_message_timeline",
            return_value=MESSAGE_TIMELINE,
        )

        self.get_recent = self.recent_patcher.start()
        self.get_latest_mailing = self.latest_mailing_patcher.start()
        self.get_details = self.details_patcher.start()
        self.get_messages = self.messages_patcher.start()
        self.get_events = self.events_patcher.start()
        self.get_message_details = self.message_details_patcher.start()
        self.get_message_timeline = self.message_timeline_patcher.start()

        self.client = TestClient(
            TestServer(admin_app.create_app())
        )
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.message_timeline_patcher.stop()
        self.message_details_patcher.stop()
        self.events_patcher.stop()
        self.messages_patcher.stop()
        self.details_patcher.stop()
        self.latest_mailing_patcher.stop()
        self.recent_patcher.stop()

    async def test_routes_are_registered(self) -> None:
        routes = {
            (
                route.method,
                route.resource.canonical,
            )
            for route in self.client.app.router.routes()
            if route.method == "GET"
        }

        self.assertEqual(
            routes,
            {
                ("GET", "/admin"),
                ("GET", "/admin/runs"),
                ("GET", "/admin/runs/{run_id}"),
                ("GET", "/admin/messages/{message_id}"),
            },
        )

    async def test_dashboard_and_runs_list(self) -> None:
        dashboard_response = await self.client.get(
            "/admin"
        )
        dashboard_html = await dashboard_response.text()

        self.assertEqual(dashboard_response.status, 200)
        self.assertEqual(
            dashboard_response.content_type,
            "text/html",
        )
        self.assertIn("Последняя рассылка", dashboard_html)
        self.assertIn("Последний запуск", dashboard_html)
        self.assertIn("Последняя проверка:", dashboard_html)
        self.assertIn("#8 — новых получателей нет", dashboard_html)
        self.assertIn("○ Нет новых получателей", dashboard_html)
        for label in (
            "Подготовлено",
            "Pending",
            "Отправлено",
            "Принято сервером",
            "Bounce",
            "Открытия / уник.",
            "Клики / уник.",
            "Длительность",
            "Кампания",
            "Семейство",
            "Selection",
        ):
            self.assertIn(label, dashboard_html)
        self.assertIn("⚠ Частично", dashboard_html)
        self.assertIn("12 мин", dashboard_html)
        self.assertIn("Campaign &lt;unsafe&gt;", dashboard_html)
        self.assertNotIn("Campaign <unsafe>", dashboard_html)
        self.assertIn('/admin/runs/7', dashboard_html)
        self.assertIn('/admin/runs/8', dashboard_html)
        self.get_latest_mailing.assert_called_once_with()
        self.get_messages.assert_called_once_with(7)

        runs_response = await self.client.get(
            "/admin/runs"
        )
        runs_html = await runs_response.text()

        self.assertEqual(runs_response.status, 200)
        self.assertIn("История запусков", runs_html)
        for label in (
            "Запуск",
            "Кампания",
            "Начало",
            "ИНН",
            "Подготовлено",
            "Skipped",
            "Pending",
            "Отправлено",
            "Принято сервером",
            "Ошибки",
            "Открытия / уник.",
            "Клики / уник.",
            "Статус",
        ):
            self.assertIn(label, runs_html)
        self.assertIn("○ Нет новых получателей", runs_html)
        self.assertNotIn("✓ Успешно", runs_html)
        self.assertGreaterEqual(
            runs_html.count('href="/admin/runs/8"'),
            10,
        )
        self.assertIn('@media (max-width: 720px)', runs_html)
        self.assertIn('data-label="Кампания"', runs_html)
        self.assertIn("Campaign &lt;unsafe&gt;", runs_html)
        self.assertNotIn("Campaign <unsafe>", runs_html)
        self.assertEqual(
            self.get_recent.call_args_list,
            [
                call(limit=1),
                call(limit=admin_app.RECENT_RUNS_LIMIT),
            ],
        )

    async def test_run_details_contains_messages_and_events(self) -> None:
        response = await self.client.get(
            "/admin/runs/7"
        )
        page_html = await response.text()

        self.assertEqual(response.status, 200)
        self.assertIn("Запуск #7", page_html)
        self.assertIn("Company &lt;One&gt;", page_html)
        self.assertIn("Статус message", page_html)
        self.assertIn("Delivery status", page_html)
        self.assertIn("Открытия", page_html)
        self.assertIn("Клики", page_html)
        self.assertIn("✓ Принято", page_html)
        self.assertIn("Failed", page_html)
        self.assertIn("Последние события", page_html)
        self.assertIn("Клик", page_html)
        self.assertIn(
            'href="/admin/messages/21"',
            page_html,
        )
        self.assertIn('<details class="event-data">', page_html)
        self.assertIn("<summary>Данные</summary>", page_html)
        self.assertIn(
            '&lt;script&gt;alert(&quot;unsafe&quot;)&lt;/script&gt;',
            page_html,
        )
        self.assertNotIn("<script>alert", page_html)
        self.get_details.assert_called_once_with(7)
        self.get_messages.assert_called_once_with(7)
        self.get_events.assert_called_once_with(7)

    async def test_etrn_run_shows_preparation_breakdown(self) -> None:
        self.get_details.return_value = {
            **DETAILS,
            "campaign_name": "etrn_2026_08",
            "campaign_family": "etrn",
            "input_inns_count": 12,
            "clients_found_count": 10,
            "clients_without_email_count": 2,
            "email_found_after_enrichment_count": 3,
            "invalid_email_count": 1,
            "duplicate_count": 2,
            "bounced_before_send_count": 1,
            "prepared_email_count": 7,
            "skipped_count": 5,
            "pending_count": 4,
        }

        response = await self.client.get("/admin/runs/7")
        page_html = await response.text()

        self.assertEqual(response.status, 200)
        for label in (
            "ИНН во входном списке",
            "Клиентов найдено",
            "Клиентов без email",
            "Email после enrichment",
            "Invalid email",
            "Duplicate ETRN",
            "Bounced до отправки",
            "К отправке",
        ):
            self.assertIn(label, page_html)
        self.assertIn("etrn_2026_08", page_html)
        self.assertIn("Email подготовлено", page_html)
        self.assertIn("Уник. открытия", page_html)
        self.assertIn("Уник. клики", page_html)

    async def test_message_details_contains_timeline_and_channel(
        self,
    ) -> None:
        response = await self.client.get(
            "/admin/messages/21"
        )
        page_html = await response.text()

        self.assertEqual(response.status, 200)
        for label in (
            "Компания",
            "Email",
            "Кампания",
            "Run ID",
            "Message ID",
            "Статус отправки",
            "Статус доставки",
            "Отправлено",
            "Открытий",
            "Кликов",
            "Последнее событие",
            "История событий",
        ):
            self.assertIn(label, page_html)
        self.assertIn("✓ Отправлено", page_html)
        self.assertIn("✓ Принято", page_html)
        self.assertIn("Открыто", page_html)
        self.assertIn("Клик · WhatsApp", page_html)
        self.assertLess(
            page_html.index("Открыто"),
            page_html.index("Клик · WhatsApp"),
        )
        self.assertIn(
            'href="/admin/runs/7"',
            page_html,
        )
        self.assertIn("← К запуску #7", page_html)
        self.assertIn("Технические данные", page_html)
        self.assertIn("provider-&lt;unsafe&gt;", page_html)
        self.assertIn("token-&lt;unsafe&gt;", page_html)
        self.assertIn(
            "&lt;script&gt;alert(1)&lt;/script&gt;",
            page_html,
        )
        self.assertNotIn("<script>alert(1)</script>", page_html)
        self.assertIn('<details class="event-data">', page_html)
        self.get_message_details.assert_called_once_with(21)
        self.get_message_timeline.assert_called_once_with(21)

    async def test_message_without_run_links_to_runs_list(self) -> None:
        self.get_message_details.return_value = {
            **MESSAGE_DETAILS,
            "run_id": None,
        }

        response = await self.client.get(
            "/admin/messages/21"
        )
        page_html = await response.text()

        self.assertEqual(response.status, 200)
        self.assertIn('href="/admin/runs"', page_html)
        self.assertIn("← К списку запусков", page_html)

    async def test_missing_message_returns_404(self) -> None:
        self.get_message_details.return_value = None

        response = await self.client.get(
            "/admin/messages/999"
        )

        self.assertEqual(response.status, 404)
        self.assertEqual(
            await response.text(),
            "Сообщение не найдено.",
        )
        self.get_message_details.assert_called_once_with(999)
        self.get_message_timeline.assert_not_called()

    async def test_invalid_message_id_returns_400(self) -> None:
        response = await self.client.get(
            "/admin/messages/not-a-number"
        )

        self.assertEqual(response.status, 400)
        self.assertEqual(
            await response.text(),
            "Некорректный message_id.",
        )
        self.get_message_details.assert_not_called()
        self.get_message_timeline.assert_not_called()

    async def test_dashboard_without_prior_mailing_keeps_latest_run(
        self,
    ) -> None:
        self.get_latest_mailing.return_value = None

        response = await self.client.get("/admin")
        page_html = await response.text()

        self.assertEqual(response.status, 200)
        self.assertIn("Последняя рассылка", page_html)
        self.assertIn(
            "Рассылок с отправленными сообщениями пока нет.",
            page_html,
        )
        self.assertIn("#8 — новых получателей нет", page_html)
        self.assertIn("○ Нет новых получателей", page_html)
        self.get_messages.assert_not_called()

    async def test_invalid_run_id_returns_400(self) -> None:
        response = await self.client.get(
            "/admin/runs/not-a-number"
        )

        self.assertEqual(response.status, 400)
        self.assertEqual(
            await response.text(),
            "Некорректный run_id.",
        )
        self.get_details.assert_not_called()
        self.get_messages.assert_not_called()
        self.get_events.assert_not_called()

    async def test_missing_run_returns_404(self) -> None:
        self.get_details.side_effect = LookupError(
            "fixture missing"
        )

        response = await self.client.get(
            "/admin/runs/999"
        )

        self.assertEqual(response.status, 404)
        self.assertEqual(
            await response.text(),
            "Запуск не найден.",
        )
        self.get_details.assert_called_once_with(999)
        self.get_messages.assert_not_called()
        self.get_events.assert_not_called()


class AdminAppCliTestCase(unittest.TestCase):
    def test_main_binds_only_loopback(self) -> None:
        with (
            patch.object(admin_app.web, "run_app") as run_app,
            patch("builtins.print") as print_mock,
        ):
            admin_app.main()

        print_mock.assert_called_once_with(
            "ProjectSbis admin listening on "
            "http://127.0.0.1:8081/admin",
            flush=True,
        )
        self.assertEqual(
            run_app.call_args.kwargs["host"],
            "127.0.0.1",
        )
        self.assertEqual(
            run_app.call_args.kwargs["port"],
            8081,
        )


class AdminAppPresentationTestCase(unittest.TestCase):
    def test_all_required_statuses_are_localized(self) -> None:
        cases = (
            (admin_app._run_status, "success", "✓ Успешно"),
            (admin_app._run_status, "partial", "⚠ Частично"),
            (admin_app._run_status, "failed", "✕ Ошибка"),
            (admin_app._run_status, "running", "… Выполняется"),
            (admin_app._send_status, "sent", "✓"),
            (admin_app._send_status, "failed", "✕"),
            (
                admin_app._message_send_status,
                "sent",
                "✓ Отправлено",
            ),
            (
                admin_app._message_send_status,
                "failed",
                "✕ Ошибка SMTP",
            ),
            (admin_app._delivery_status, "delivered", "✓ Принято"),
            (admin_app._delivery_status, "bounced", "✕ Bounce"),
            (admin_app._delivery_status, "deferred", "… Ожидание"),
            (admin_app._delivery_status, "unknown", "… Нет результата"),
            (admin_app._event_status, "sent", "Отправлено"),
            (admin_app._event_status, "delivered", "Принято сервером"),
            (admin_app._event_status, "bounced", "Bounce"),
            (admin_app._event_status, "deferred", "Временная ошибка"),
            (admin_app._event_status, "opened", "Открыто"),
            (admin_app._event_status, "clicked", "Клик"),
        )

        for renderer, status, label in cases:
            with self.subTest(status=status, label=label):
                self.assertIn(label, renderer(status))

    def test_unknown_status_is_escaped(self) -> None:
        rendered = admin_app._run_status("<script>alert(1)</script>")

        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>", rendered)

    def test_empty_successful_run_has_neutral_status(self) -> None:
        rendered = admin_app._display_run_status(EMPTY_RUN)

        self.assertIn("○ Нет новых получателей", rendered)
        self.assertNotIn("✓ Успешно", rendered)
        self.assertEqual(EMPTY_RUN["status"], "success")

    def test_known_click_channels_are_human_readable(self) -> None:
        channels = {
            "phone": "Phone",
            "whatsapp": "WhatsApp",
            "telegram": "Telegram",
            "max": "MAX",
            "cta_email": "Email",
        }

        for click_key, label in channels.items():
            with self.subTest(click_key=click_key):
                rendered = admin_app._message_event_status(
                    "clicked",
                    f'{{"click_key":"{click_key}"}}',
                )
                self.assertIn(f"Клик · {label}", rendered)

        unknown = admin_app._message_event_status(
            "clicked",
            '{"click_key":"unknown"}',
        )
        self.assertIn(">Клик</span>", unknown)


if __name__ == "__main__":
    unittest.main()
