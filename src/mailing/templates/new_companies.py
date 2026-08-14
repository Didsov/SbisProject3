"""
Шаблон письма для кампании по новым организациям.

Назначение модуля:
- сформировать тему письма;
- сформировать текстовую версию;
- сформировать HTML-версию;
- не содержать логики отправки;
- не обращаться к базе данных.

Функции:
- build_subject() — сформировать тему;
- build_text_body() — сформировать текстовую версию;
- build_html_body() — сформировать HTML-версию.
"""

from __future__ import annotations

from html import escape
from textwrap import dedent

def build_subject(
    *,
    client_name: str,
    inn: str,
) -> str:
    """
    Сформировать тему письма.

    Аргументы:
        client_name:
            Название организации или ИП.

        inn:
            ИНН клиента.

    Возвращает:
        Тему письма.
    """
    return "Тестовое письмо ProjectSbis"


def build_text_body(
    *,
    client_name: str,
    inn: str,
) -> str:
    """
    Сформировать текстовую версию письма.

    Аргументы:
        client_name:
            Название организации или ИП.

        inn:
            ИНН клиента.

    Возвращает:
        Текст письма.
    """
    return (
        f"{client_name}\n\n"
        "Это тестовое письмо системы рассылок ProjectSbis.\n"
        "Реальная отправка пока не выполняется.\n\n"
        f"ИНН: {inn}"
    )


def build_html_body(
    *,
    client_name: str,
    inn: str,
) -> str:
    """
    Сформировать HTML-версию письма.

    Пользовательские значения экранируются перед вставкой
    в HTML.

    Аргументы:
        client_name:
            Название организации или ИП.

        inn:
            ИНН клиента.

    Возвращает:
        HTML письма.
    """
    safe_client_name = escape(
        client_name
    )

    safe_inn = escape(
        inn
    )

    return dedent(
        f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="utf-8">
        </head>
        <body>
            <p>
                <strong>{safe_client_name}</strong>
            </p>

            <p>
                Это тестовое письмо системы рассылок ProjectSbis.
            </p>

            <p>
                Реальная отправка пока не выполняется.
            </p>

            <p>
                ИНН: {safe_inn}
            </p>
        </body>
        </html>
        """
    ).strip()