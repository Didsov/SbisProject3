"""
Шаблон письма для кампании по новым организациям.

Назначение модуля:
- сформировать тему письма;
- сформировать текстовую версию письма;
- сформировать HTML-версию письма;
- персонализировать письмо названием организации и ИНН;
- подготовить HTML-структуру для будущего подключения
  tracking-ссылок, unsubscribe и других метрик.

Шаблон намеренно не содержит логики отправки писем,
работы с SMTP или базой данных.

Текущая версия использует:
- простой HTML, совместимый с основными почтовыми клиентами;
- inline CSS;
- адаптивную ширину;
- отдельную plain-text версию;
- один основной CTA.

В дальнейшем сюда будут передаваться:
- tracking URL;
- unsubscribe URL;
- идентификатор письма;
- дополнительные персональные данные.
"""

from __future__ import annotations

import html
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
            Название организации или ФИО ИП.

        inn:
            ИНН клиента.

    Возвращает:
        Готовую тему письма.
    """
    return "Онлайн-касса для вашего бизнеса — поможем с подключением"


def build_text_body(
    *,
    client_name: str,
    inn: str,
) -> str:
    """
    Сформировать текстовую версию письма.

    Text-версия нужна для:
    - почтовых клиентов без HTML;
    - корректного multipart/alternative;
    - улучшения совместимости письма.

    Аргументы:
        client_name:
            Название организации или ФИО ИП.

        inn:
            ИНН клиента.

    Возвращает:
        Готовое тело письма в plain text.
    """
    return dedent(
        f"""
        {client_name}, добрый день!

        Помогаем бизнесу с подбором, регистрацией и настройкой онлайн-касс,
        фискальных накопителей и сопутствующего оборудования.

        Если вам требуется новая касса, подключение ОФД, регистрация ККТ
        или помощь с настройкой — можем подобрать подходящий вариант
        и помочь с запуском.

        Что можем сделать:
        • подобрать онлайн-кассу под задачи бизнеса;
        • установить и заменить фискальный накопитель;
        • зарегистрировать ККТ;
        • подключить ОФД;
        • настроить кассовое ПО и оборудование;
        • помочь с маркировкой и интеграциями.

        ИНН организации: {inn}

        Если предложение актуально, просто ответьте на это письмо —
        уточним задачу и предложим подходящий вариант.

        С уважением,
        ProjectSbis

        Email: info@projectsbis.ru
        """
    ).strip()


def build_html_body(
    *,
    client_name: str,
    inn: str,
) -> str:
    """
    Сформировать HTML-версию письма.

    HTML специально сделан достаточно простым для email:
    - табличная основа;
    - inline CSS;
    - без JavaScript;
    - без внешних шрифтов;
    - без сложной CSS-вёрстки;
    - максимальная ширина 620 px.

    Такая структура лучше отображается в Outlook,
    Яндекс Почте, Gmail и мобильных почтовых клиентах.

    Аргументы:
        client_name:
            Название организации или ФИО ИП.

        inn:
            ИНН клиента.

    Возвращает:
        Готовое HTML-тело письма.
    """
    safe_client_name = html.escape(
        client_name
    )

    safe_inn = html.escape(
        inn
    )

    return dedent(
        f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>ProjectSbis</title>
        </head>

        <body
            style="
                margin: 0;
                padding: 0;
                background-color: #f5f6f8;
                font-family: Arial, Helvetica, sans-serif;
                color: #202124;
            "
        >

            <!--
                PREHEADER

                Короткий скрытый текст, который некоторые
                почтовые клиенты показывают рядом с темой письма.
            -->
            <div
                style="
                    display: none;
                    max-height: 0;
                    overflow: hidden;
                    opacity: 0;
                    color: transparent;
                "
            >
                Подбор и настройка онлайн-кассы для вашего бизнеса.
            </div>

            <table
                role="presentation"
                width="100%"
                cellspacing="0"
                cellpadding="0"
                border="0"
                style="
                    width: 100%;
                    background-color: #f5f6f8;
                "
            >
                <tr>
                    <td
                        align="center"
                        style="
                            padding: 32px 12px;
                        "
                    >

                        <!-- Основная карточка письма -->
                        <table
                            role="presentation"
                            width="100%"
                            cellspacing="0"
                            cellpadding="0"
                            border="0"
                            style="
                                max-width: 620px;
                                width: 100%;
                                background-color: #ffffff;
                                border-radius: 14px;
                                overflow: hidden;
                            "
                        >

                            <!-- Header -->
                            <tr>
                                <td
                                    style="
                                        padding: 26px 32px;
                                        border-bottom: 1px solid #eeeeee;
                                    "
                                >
                                    <div
                                        style="
                                            font-size: 22px;
                                            font-weight: 700;
                                            letter-spacing: -0.3px;
                                        "
                                    >
                                        ProjectSbis
                                    </div>

                                    <div
                                        style="
                                            margin-top: 5px;
                                            font-size: 13px;
                                            color: #777777;
                                        "
                                    >
                                        Кассы · ФН · ОФД · Настройка
                                    </div>
                                </td>
                            </tr>

                            <!-- Основной текст -->
                            <tr>
                                <td
                                    style="
                                        padding: 32px;
                                    "
                                >
                                    <div
                                        style="
                                            font-size: 21px;
                                            line-height: 1.35;
                                            font-weight: 700;
                                            margin-bottom: 18px;
                                        "
                                    >
                                        {safe_client_name}, добрый день!
                                    </div>

                                    <div
                                        style="
                                            font-size: 16px;
                                            line-height: 1.6;
                                            color: #3c4043;
                                        "
                                    >
                                        Помогаем бизнесу с подбором,
                                        регистрацией и настройкой
                                        <strong>онлайн-касс</strong>,
                                        фискальных накопителей и
                                        сопутствующего оборудования.
                                    </div>

                                    <div
                                        style="
                                            font-size: 16px;
                                            line-height: 1.6;
                                            color: #3c4043;
                                            margin-top: 16px;
                                        "
                                    >
                                        Если вам требуется новая касса,
                                        подключение ОФД, регистрация ККТ
                                        или помощь с настройкой — можем
                                        подобрать подходящий вариант
                                        и помочь с запуском.
                                    </div>

                                    <!-- Возможности -->
                                    <table
                                        role="presentation"
                                        width="100%"
                                        cellspacing="0"
                                        cellpadding="0"
                                        border="0"
                                        style="
                                            margin-top: 26px;
                                            background-color: #f7f8fa;
                                            border-radius: 10px;
                                        "
                                    >
                                        <tr>
                                            <td
                                                style="
                                                    padding: 22px 24px;
                                                "
                                            >
                                                <div
                                                    style="
                                                        font-size: 15px;
                                                        font-weight: 700;
                                                        margin-bottom: 13px;
                                                    "
                                                >
                                                    Можем помочь:
                                                </div>

                                                <div
                                                    style="
                                                        font-size: 15px;
                                                        line-height: 1.75;
                                                        color: #3c4043;
                                                    "
                                                >
                                                    ✓ Подобрать онлайн-кассу<br>
                                                    ✓ Установить или заменить ФН<br>
                                                    ✓ Зарегистрировать ККТ<br>
                                                    ✓ Подключить ОФД<br>
                                                    ✓ Настроить кассовое ПО<br>
                                                    ✓ Помочь с маркировкой и интеграциями
                                                </div>
                                            </td>
                                        </tr>
                                    </table>

                                    <!-- CTA -->
                                    <table
                                        role="presentation"
                                        cellspacing="0"
                                        cellpadding="0"
                                        border="0"
                                        style="
                                            margin-top: 28px;
                                        "
                                    >
                                        <tr>
                                            <td>
                                                <a
                                                    href="mailto:info@projectsbis.ru?subject=Консультация%20по%20онлайн-кассе"
                                                    style="
                                                        display: inline-block;
                                                        padding: 13px 22px;
                                                        background-color: #202124;
                                                        color: #ffffff;
                                                        text-decoration: none;
                                                        font-size: 15px;
                                                        font-weight: 700;
                                                        border-radius: 8px;
                                                    "
                                                >
                                                    Получить консультацию
                                                </a>
                                            </td>
                                        </tr>
                                    </table>

                                    <div
                                        style="
                                            margin-top: 20px;
                                            font-size: 14px;
                                            line-height: 1.55;
                                            color: #777777;
                                        "
                                    >
                                        Или просто ответьте на это письмо —
                                        уточним задачу и предложим подходящий вариант.
                                    </div>
                                </td>
                            </tr>

                            <!-- Информация о получателе -->
                            <tr>
                                <td
                                    style="
                                        padding: 0 32px 28px 32px;
                                    "
                                >
                                    <div
                                        style="
                                            border-top: 1px solid #eeeeee;
                                            padding-top: 20px;
                                            font-size: 12px;
                                            line-height: 1.5;
                                            color: #999999;
                                        "
                                    >
                                        Письмо сформировано для:<br>
                                        {safe_client_name}<br>
                                        ИНН: {safe_inn}
                                    </div>
                                </td>
                            </tr>

                            <!-- Footer -->
                            <tr>
                                <td
                                    style="
                                        padding: 22px 32px;
                                        background-color: #fafafa;
                                        font-size: 12px;
                                        line-height: 1.6;
                                        color: #888888;
                                    "
                                >
                                    <strong
                                        style="
                                            color: #555555;
                                        "
                                    >
                                        ProjectSbis
                                    </strong>
                                    <br>

                                    Онлайн-кассы и кассовая инфраструктура
                                    <br>

                                    <a
                                        href="mailto:info@projectsbis.ru"
                                        style="
                                            color: #555555;
                                            text-decoration: none;
                                        "
                                    >
                                        info@projectsbis.ru
                                    </a>

                                    <!--
                                        Здесь позднее появится:
                                        - ссылка отписки;
                                        - tracking pixel;
                                        - служебные ссылки кампании.
                                    -->
                                </td>
                            </tr>

                        </table>

                    </td>
                </tr>
            </table>

        </body>
        </html>
        """
    ).strip()