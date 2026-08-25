from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer
from dotenv import dotenv_values

from src import daily_run
from src.mailing.smtp_provider import SMTPSendResult
from src.sbis import auth
from src.sbis import auth_alert
from src.sbis import cookie_manager


def make_auth_result(
    status: auth.SbisAuthStatus,
    *,
    http_status: int | None = None,
    error_type: str | None = None,
) -> auth.SbisAuthCheckResult:
    return auth.SbisAuthCheckResult(
        status=status,
        checked_at=datetime.now().astimezone(),
        http_status=http_status,
        error_type=error_type,
    )


class SbisAuthHttpTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.response_status = 200
        self.response_payload: object = {
            "jsonrpc": "2.0",
            "result": {
                "d": [],
                "s": [],
            },
            "id": 1,
        }
        self.response_delay = 0.0

        async def handler(request: web.Request) -> web.StreamResponse:
            if self.response_delay:
                import asyncio

                await asyncio.sleep(self.response_delay)

            if self.response_status in {401, 403, 500, 503}:
                return web.Response(
                    status=self.response_status,
                    text="response body intentionally ignored",
                )
            return web.json_response(
                self.response_payload,
                status=self.response_status,
            )

        application = web.Application()
        application.router.add_post("/service/", handler)
        self.server = TestServer(application)
        await self.server.start_server()
        self.url_patch = patch.object(
            auth,
            "get_sbis_url",
            return_value=str(self.server.make_url("/service/")),
        )
        self.url_patch.start()

    async def asyncTearDown(self) -> None:
        self.url_patch.stop()
        await self.server.close()

    async def test_http_200_is_valid(self) -> None:
        result = await auth.check_sbis_browser_cookie(
            "safe-test-cookie",
        )
        self.assertEqual(result.status, "valid")
        self.assertEqual(result.http_status, 200)

    async def test_http_401_and_403_are_invalid_auth(self) -> None:
        for status_code in (401, 403):
            with self.subTest(status_code=status_code):
                self.response_status = status_code
                result = await auth.check_sbis_browser_cookie(
                    "safe-test-cookie",
                )
                self.assertEqual(result.status, "invalid_auth")
                self.assertEqual(result.http_status, status_code)

    async def test_5xx_is_server_error_not_invalid_auth(self) -> None:
        for status_code in (500, 503):
            with self.subTest(status_code=status_code):
                self.response_status = status_code
                result = await auth.check_sbis_browser_cookie(
                    "safe-test-cookie",
                )
                self.assertEqual(
                    result.status,
                    "network_server_error",
                )
                self.assertEqual(result.http_status, status_code)

    async def test_timeout_is_network_error_not_invalid_auth(self) -> None:
        self.response_delay = 0.1
        with patch.object(
            auth,
            "AUTH_CHECK_TIMEOUT_SECONDS",
            0.01,
        ):
            result = await auth.check_sbis_browser_cookie(
                "safe-test-cookie",
            )

        self.assertEqual(result.status, "network_server_error")
        self.assertIsNone(result.http_status)
        self.assertEqual(result.error_type, "TimeoutError")

    async def test_json_rpc_auth_error_is_invalid(self) -> None:
        self.response_payload = {
            "jsonrpc": "2.0",
            "error": {
                "code": 401,
                "message": "Unauthorized",
            },
            "id": 1,
        }
        result = await auth.check_sbis_browser_cookie(
            "safe-test-cookie",
        )
        self.assertEqual(result.status, "invalid_auth")
        self.assertEqual(result.error_type, "json_rpc_auth_error")


class CookieManagerTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_cookie_value_round_trips_through_python_dotenv(self) -> None:
        cookie = "sid=a=b; flag=x y; quote='; slash=\\"
        content = cookie_manager._replace_cookie_line(
            "OTHER=value\n",
            cookie,
        )
        parsed = dotenv_values(stream=StringIO(content))
        self.assertEqual(parsed["OTHER"], "value")
        self.assertEqual(parsed["SBIS_BROWSER_COOKIE"], cookie)

    async def test_target_path_prefers_production_then_local_env(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            production_path = directory / "production.env"
            local_path = directory / ".env"

            with (
                patch.object(
                    cookie_manager,
                    "PRODUCTION_ENV_FILE",
                    production_path,
                ),
                patch.object(
                    cookie_manager,
                    "LOCAL_ENV_FILE",
                    local_path,
                ),
            ):
                self.assertEqual(
                    cookie_manager.get_cookie_env_path(),
                    local_path,
                )
                production_path.write_text("", encoding="utf-8")
                self.assertEqual(
                    cookie_manager.get_cookie_env_path(),
                    production_path,
                )

    async def test_invalid_cookie_does_not_change_env(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_path = Path(temporary_directory) / ".env"
            original = (
                "# keep this comment\r\n"
                "OTHER=value\r\n"
                "SBIS_BROWSER_COOKIE='old-cookie'\r\n"
                "MAIL_FROM_NAME=Атлантис\r\n"
            ).encode("utf-8")
            env_path.write_bytes(original)

            with patch.object(
                cookie_manager,
                "check_sbis_browser_cookie",
                new=AsyncMock(
                    return_value=make_auth_result(
                        "invalid_auth",
                        http_status=401,
                    )
                ),
            ):
                result = await cookie_manager.validate_and_replace_cookie(
                    "rejected-secret-cookie",
                    target_path=env_path,
                )

            self.assertFalse(result.updated)
            self.assertEqual(env_path.read_bytes(), original)
            self.assertFalse(Path(f"{env_path}.bak").exists())

    async def test_valid_cookie_replaces_only_target_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_path = Path(temporary_directory) / ".env"
            original = (
                "# keep this comment\n"
                "OTHER=value\n"
                "SBIS_BROWSER_COOKIE='old-cookie'\n"
                "MAIL_FROM_NAME=Атлантис\n"
            )
            env_path.write_text(original, encoding="utf-8")
            os.chmod(env_path, 0o640)
            original_owner = (
                env_path.stat().st_uid,
                env_path.stat().st_gid,
            )

            with patch.object(
                cookie_manager,
                "check_sbis_browser_cookie",
                new=AsyncMock(
                    return_value=make_auth_result(
                        "valid",
                        http_status=200,
                    )
                ),
            ):
                result = await cookie_manager.validate_and_replace_cookie(
                    "new-secret-cookie",
                    target_path=env_path,
                )

            self.assertTrue(result.updated)
            updated = env_path.read_text(encoding="utf-8")
            self.assertEqual(
                updated,
                original.replace(
                    "SBIS_BROWSER_COOKIE='old-cookie'",
                    "SBIS_BROWSER_COOKIE='new-secret-cookie'",
                ),
            )
            self.assertEqual(
                Path(f"{env_path}.bak").read_text(encoding="utf-8"),
                original,
            )
            if os.name != "nt":
                self.assertEqual(
                    env_path.stat().st_mode & 0o777,
                    0o640,
                )
                self.assertEqual(
                    (
                        env_path.stat().st_uid,
                        env_path.stat().st_gid,
                    ),
                    original_owner,
                )

    async def test_interactive_output_never_contains_cookie(self) -> None:
        secret = "SECRET-MUST-NOT-APPEAR"
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_path = Path(temporary_directory) / ".env"
            env_path.write_text(
                "SBIS_BROWSER_COOKIE='old'\n",
                encoding="utf-8",
            )
            output = io.StringIO()

            with (
                patch.object(
                    cookie_manager,
                    "get_cookie_env_path",
                    return_value=env_path,
                ),
                patch.object(
                    cookie_manager.getpass,
                    "getpass",
                    return_value=secret,
                ),
                patch.object(
                    cookie_manager,
                    "check_sbis_browser_cookie",
                    new=AsyncMock(
                        return_value=make_auth_result(
                            "invalid_auth",
                            http_status=403,
                        )
                    ),
                ),
                contextlib.redirect_stdout(output),
            ):
                exit_code = await cookie_manager.run_interactive_update()

            self.assertEqual(exit_code, 2)
            self.assertNotIn(secret, output.getvalue())
            self.assertNotIn(secret, env_path.read_text(encoding="utf-8"))

    async def test_check_exit_codes_and_output(self) -> None:
        cases = (
            ("valid", 200, 0, "SBIS_BROWSER_COOKIE: OK"),
            (
                "invalid_auth",
                401,
                2,
                "SBIS_BROWSER_COOKIE: INVALID (HTTP 401)",
            ),
            (
                "network_server_error",
                503,
                1,
                "SBIS_BROWSER_COOKIE: ERROR (HTTP 503)",
            ),
        )

        for status, http_status, expected_code, expected_output in cases:
            with self.subTest(status=status):
                output = io.StringIO()
                with (
                    patch.object(
                        cookie_manager,
                        "check_sbis_browser_cookie",
                        new=AsyncMock(
                            return_value=make_auth_result(
                                status,
                                http_status=http_status,
                            )
                        ),
                    ),
                    contextlib.redirect_stdout(output),
                ):
                    exit_code = await cookie_manager.run_check()

                self.assertEqual(exit_code, expected_code)
                self.assertIn(expected_output, output.getvalue())


class DailyRunAuthPreflightTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_auth_stops_before_database_load_and_mailing(self) -> None:
        invalid_result = make_auth_result(
            "invalid_auth",
            http_status=401,
        )
        alert = AsyncMock(
            side_effect=RuntimeError("simulated alert failure"),
        )
        create_run = Mock()
        load_selection = AsyncMock()
        sender = AsyncMock()
        locked_workflow = AsyncMock()
        output = io.StringIO()

        with (
            patch.object(
                daily_run,
                "daily_run_lock",
                return_value=contextlib.nullcontext(),
            ),
            patch.object(
                daily_run,
                "check_sbis_browser_cookie",
                new=AsyncMock(return_value=invalid_result),
            ),
            patch.object(
                daily_run,
                "send_sbis_auth_alert",
                new=alert,
            ),
            patch.object(daily_run, "create_mail_run", create_run),
            patch.object(daily_run, "load_daily_selection", load_selection),
            patch.object(daily_run, "run_sender", sender),
            patch.object(daily_run, "_run_daily_locked", locked_workflow),
            contextlib.redirect_stdout(output),
        ):
            with self.assertRaises(
                daily_run.SbisAuthPreflightError,
            ) as captured:
                await daily_run.run_daily(
                    selection_id=5984,
                    skip_load=False,
                    real_send=True,
                    limit=1000,
                    delivery_wait=60,
                    mail_log=Path("mail.log"),
                )

        self.assertIs(captured.exception.result, invalid_result)
        alert.assert_awaited_once_with(invalid_result)
        create_run.assert_not_called()
        load_selection.assert_not_awaited()
        sender.assert_not_awaited()
        locked_workflow.assert_not_awaited()
        self.assertIn("Рассылка не выполнялась", output.getvalue())
        self.assertIn(
            "исходная ошибка авторизации сохранена",
            output.getvalue(),
        )

    async def test_server_error_stops_without_auth_alert(self) -> None:
        error_result = make_auth_result(
            "network_server_error",
            http_status=503,
        )
        alert = AsyncMock()
        locked_workflow = AsyncMock()

        with (
            patch.object(
                daily_run,
                "daily_run_lock",
                return_value=contextlib.nullcontext(),
            ),
            patch.object(
                daily_run,
                "check_sbis_browser_cookie",
                new=AsyncMock(return_value=error_result),
            ),
            patch.object(
                daily_run,
                "send_sbis_auth_alert",
                new=alert,
            ),
            patch.object(daily_run, "_run_daily_locked", locked_workflow),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(daily_run.SbisAuthPreflightError):
                await daily_run.run_daily(
                    selection_id=5984,
                    skip_load=False,
                    real_send=True,
                    limit=1000,
                    delivery_wait=60,
                    mail_log=Path("mail.log"),
                )

        alert.assert_not_awaited()
        locked_workflow.assert_not_awaited()


class AuthAlertTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_alert_uses_only_dedicated_recipients(self) -> None:
        provider = Mock()
        provider.send = AsyncMock(
            return_value=SMTPSendResult(
                success=True,
                provider_message_id="test-message-id",
            )
        )
        result = make_auth_result(
            "invalid_auth",
            http_status=403,
        )

        with (
            patch.object(
                auth_alert,
                "SBIS_AUTH_ALERT_EMAILS",
                "first@example.test, second@example.test",
            ),
            patch.object(
                auth_alert.SMTPMailProvider,
                "from_env",
                return_value=provider,
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            await auth_alert.send_sbis_auth_alert(result)

        self.assertEqual(provider.send.await_count, 2)
        recipients = [
            call.args[0].to_email
            for call in provider.send.await_args_list
        ]
        self.assertEqual(
            recipients,
            ["first@example.test", "second@example.test"],
        )
        message = provider.send.await_args_list[0].args[0]
        self.assertEqual(message.subject, auth_alert.AUTH_ALERT_SUBJECT)
        self.assertIn("HTTP status: 403", message.text_body)
        self.assertIn("Рассылка не выполнялась", message.text_body)
        self.assertIn(
            auth_alert.COOKIE_RECOVERY_COMMAND,
            message.text_body,
        )

    async def test_missing_alert_recipients_does_not_create_provider(self) -> None:
        provider_factory = Mock()
        output = io.StringIO()
        with (
            patch.object(auth_alert, "SBIS_AUTH_ALERT_EMAILS", ""),
            patch.object(
                auth_alert.SMTPMailProvider,
                "from_env",
                provider_factory,
            ),
            contextlib.redirect_stdout(output),
        ):
            await auth_alert.send_sbis_auth_alert(
                make_auth_result(
                    "invalid_auth",
                    http_status=401,
                )
            )

        provider_factory.assert_not_called()
        self.assertIn("не настроен", output.getvalue())


if __name__ == "__main__":
    unittest.main()
