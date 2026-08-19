"""
HTTP-сервис отслеживания событий почтовой рассылки.

Назначение:
- принимать запросы tracking pixel из отправленных писем;
- находить письмо по tracking_token;
- сохранять событие `opened` в mail_events;
- возвращать прозрачный GIF 1x1.

Публичный endpoint:
    GET /t/o/<tracking_token>.gif

Tracking token является непрозрачным случайным идентификатором
и не содержит email, ИНН или client_id.
"""

from __future__ import annotations
from aiohttp import web
from src.database import record_mail_open


TRANSPARENT_GIF = (
    b"GIF89a"
    b"\x01\x00\x01\x00"
    b"\x80\x00\x00"
    b"\x00\x00\x00"
    b"\xff\xff\xff"
    b"!\xf9\x04\x01\x00\x00\x00\x00"
    b",\x00\x00\x00\x00\x01\x00\x01\x00"
    b"\x00\x02\x02D\x01\x00;"
)


def process_open_tracking(
    tracking_token: str,
) -> bytes:
    """
    Обработать загрузку tracking pixel.

    Что делает:
    - передаёт tracking_token в слой БД;
    - сохраняет событие `opened`, если token существует;
    - независимо от результата возвращает прозрачный GIF 1x1.

    Аргументы:
        tracking_token:
            Token из URL tracking pixel.

    Возвращает:
        Байты прозрачного GIF 1x1.

    Примечание:
        Даже если token неизвестен, наружу возвращается тот же GIF.
        Это не позволяет по HTTP-ответу определить,
        существует такой tracking_token или нет.
    """
    record_mail_open(tracking_token)

    return TRANSPARENT_GIF


async def handle_open_tracking(
    request: web.Request,
) -> web.Response:
    """
    Обработать загрузку tracking-пикселя.

    Что делает:
    - получает tracking_token из URL;
    - фиксирует событие opened через process_open_tracking();
    - всегда возвращает прозрачный GIF;
    - запрещает кэширование ответа.

    Аргументы:
        request:
            HTTP-запрос aiohttp.

    Возвращает:
        HTTP-ответ с прозрачным GIF.
    """
    tracking_token = request.match_info[
        "tracking_token"
    ]

    gif_data = process_open_tracking(
        tracking_token
    )

    return web.Response(
        body=gif_data,
        content_type="image/gif",
        headers={
            "Cache-Control": (
                "no-store, no-cache, must-revalidate, max-age=0"
            ),
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def create_app() -> web.Application:
    """
    Создать aiohttp-приложение tracking-сервиса.

    Что делает:
    - создаёт HTTP-приложение;
    - регистрирует endpoint открытия письма.

    Маршруты:
        GET /t/o/{tracking_token}.gif

    Возвращает:
        Настроенный web.Application.
    """
    app = web.Application()

    app.router.add_get(
        "/t/o/{tracking_token}.gif",
        handle_open_tracking,
    )

    return app


def main() -> None:
    """
    Запустить tracking HTTP-сервис локально.

    Сервис слушает только localhost:
        127.0.0.1:8080

    Публичный доступ позже будет идти через Nginx.
    """
    web.run_app(
        create_app(),
        host="127.0.0.1",
        port=8080,
    )


if __name__ == "__main__":
    main()