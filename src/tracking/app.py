"""
HTTP-сервис отслеживания событий почтовой рассылки.

Назначение:
- принимать запросы tracking pixel из отправленных писем;
- находить письмо по tracking_token;
- сохранять событие `opened` в mail_events;
- возвращать прозрачный GIF 1x1;
- фиксировать `clicked` и перенаправлять только на известные
  контактные destinations из src.config.

Публичные endpoints:
    GET /t/o/<tracking_token>.gif
    GET /t/c/<tracking_token>/<click_key>

Tracking token является непрозрачным случайным идентификатором
и не содержит email, ИНН или client_id.
"""

from __future__ import annotations

from aiohttp import web

from src.config import (
    CONTACT_ETRN_WHATSAPP_URL,
    CONTACT_MAX_URL,
    CONTACT_PHONE_URL,
    CONTACT_TELEGRAM_URL,
    CONTACT_WHATSAPP_URL,
)
from src.database import record_mail_click, record_mail_open


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


CLICK_TARGETS = {
    # cta_email — исторический ключ аналитики основной кнопки.
    # Его нельзя переименовывать в старых и новых письмах, хотя
    # фактический destination теперь совпадает с WhatsApp.
    "cta_email": CONTACT_WHATSAPP_URL,
    "phone": CONTACT_PHONE_URL,
    "whatsapp": CONTACT_WHATSAPP_URL,
    "etrn_whatsapp": CONTACT_ETRN_WHATSAPP_URL,
    "telegram": CONTACT_TELEGRAM_URL,
    "max": CONTACT_MAX_URL,
}


def process_click_tracking(
    tracking_token: str,
    click_key: str,
) -> str | None:
    """
    Обработать переход по отслеживаемой ссылке.

    Что делает:
    - проверяет, что click_key относится к разрешённой ссылке;
    - сохраняет событие `clicked` для tracking_token;
    - возвращает реальный URL назначения.

    Аргументы:
        tracking_token:
            Token конкретного письма.

        click_key:
            Стабильный идентификатор ссылки:
            cta_email, phone, whatsapp, etrn_whatsapp, telegram или max.

    Возвращает:
        Реальный URL назначения либо None,
        если click_key неизвестен.

    Примечание:
        Реальный URL не принимается из пользовательского запроса.
        Он выбирается только из фиксированного словаря конфигурации,
        что защищает endpoint от превращения в открытый redirect.

        Результат record_mail_click намеренно не влияет на redirect:
        неизвестный старый token не приводит к HTTP 500 и всё равно
        открывает безопасный destination известного click_key.
    """
    target_url = CLICK_TARGETS.get(click_key)

    if target_url is None:
        return None

    record_mail_click(
        tracking_token=tracking_token,
        click_key=click_key,
    )

    return target_url


async def handle_click_tracking(
    request: web.Request,
) -> web.Response:
    """
    Обработать переход по tracked-ссылке.

    URL:
        GET /t/c/{tracking_token}/{click_key}

    Что делает:
    - извлекает tracking_token и click_key из URL;
    - сохраняет событие clicked;
    - перенаправляет пользователя на настоящий адрес.

    Неизвестный click_key возвращает 404.
    """
    tracking_token = request.match_info["tracking_token"]
    click_key = request.match_info["click_key"]

    target_url = process_click_tracking(
        tracking_token=tracking_token,
        click_key=click_key,
    )

    if target_url is None:
        raise web.HTTPNotFound()

    raise web.HTTPFound(location=target_url)


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
    - регистрирует endpoint открытия письма;
    - регистрирует endpoint переходов по ссылкам.

    Маршруты:
        GET /t/o/{tracking_token}.gif
        GET /t/c/{tracking_token}/{click_key}

    Возвращает:
        Настроенный web.Application.
    """
    app = web.Application()

    app.router.add_get(
        "/t/o/{tracking_token}.gif",
        handle_open_tracking,
    )

    app.router.add_get(
        "/t/c/{tracking_token}/{click_key}",
        handle_click_tracking,
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
