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