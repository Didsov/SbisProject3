"""
Шаблон письма кампании для новых организаций.

Назначение модуля:
- сформировать тему письма;
- сформировать plain-text версию;
- сформировать HTML-версию;
- персонализировать письмо названием организации или ФИО ИП;
- использовать фирменный стиль компании «Атлантис»;
- подготовить HTML-структуру для дальнейшего подключения
  click tracking, open tracking и unsubscribe.

Особенности HTML:
- табличная верстка для совместимости с email-клиентами;
- inline CSS;
- без JavaScript;
- без внешних шрифтов;
- ширина письма ограничена 620 px;
- логотип предполагается подключать как CID-вложение.

Ожидаемый CID логотипа:
    atlantis-logo

То есть HTML содержит:
    src="cid:atlantis-logo"

Сам PNG будет прикрепляться к MIME-письму
на уровне SMTPMailProvider.
"""

from __future__ import annotations

import html
from textwrap import dedent


# ---------------------------------------------------------------------------
# Фирменные параметры
# ---------------------------------------------------------------------------

BRAND_NAME = "Атлантис"

BRAND_ORANGE = "#E76A2E"
BRAND_CYAN = "#13AFC1"

TEXT_PRIMARY = "#202124"
TEXT_SECONDARY = "#5F6368"
TEXT_MUTED = "#8A8D91"

BACKGROUND = "#F4F6F7"
CARD_BACKGROUND = "#FFFFFF"
SOFT_BACKGROUND = "#F7F9FA"
BORDER = "#E8EAED"

LOGO_CID = "atlantis-logo"


def build_subject(
    *,
    client_name: str,
    inn: str,
    director_first_name: str,
    director_middle_name: str,
    tracking_token: str | None = None,
) -> str:
    """
    Сформировать тему письма.

    Аргументы:
        client_name:
            Название организации или ФИО ИП.

        inn:
            ИНН клиента.

            Сейчас не используется в теме, но аргумент
            сохраняется для совместимости с sender.

    Возвращает:
        Готовую тему письма.
    """
    return "Онлайн-касса и кассовое оборудование для вашего бизнеса"


def build_text_body(
    *,
    client_name: str,
    inn: str,
    director_first_name: str,
    director_middle_name: str,
    tracking_token: str | None = None,
) -> str:

    """
    Сформировать plain-text версию письма.

    Она используется почтовыми клиентами, которые не отображают HTML,
    а также является альтернативной MIME-частью письма.

    Аргументы:
        client_name:
            Название организации или ФИО ИП.

        inn:
            ИНН клиента.

            В текущем тексте клиенту не показывается, но параметр
            сохраняется для совместимости с общей архитектурой.

    Возвращает:
        Текстовую версию письма.
    """
    greeting = build_greeting(
        director_first_name=director_first_name,
        director_middle_name=director_middle_name,
    )
    return dedent(
        f"""
        {greeting}

        Помогаем бизнесу с подбором, регистрацией и настройкой
        онлайн-касс, фискальных накопителей и кассового оборудования.

        Если вам требуется новая касса, замена ФН, подключение ОФД,
        регистрация ККТ или помощь с настройкой — можем подобрать
        подходящее решение и помочь с запуском.

        Можем помочь:

        • подобрать онлайн-кассу;
        • установить или заменить фискальный накопитель;
        • зарегистрировать ККТ;
        • подключить ОФД;
        • настроить кассовое ПО;
        • помочь с маркировкой и интеграциями.

        Если предложение актуально, просто ответьте на это письмо —
        уточним задачу и предложим подходящий вариант.

        С уважением,
        Атлантис
        Автоматизация бизнеса

        info@projectsbis.ru
        """
    ).strip()


def build_html_body(
    *,
    client_name: str,
    inn: str,
    director_first_name: str,
    director_middle_name: str,
    tracking_token: str | None = None,
) -> str:
    """
    Сформировать HTML-версию письма.

    Дизайн:
    - фирменный логотип «Атлантис»;
    - белая карточка на светлом фоне;
    - оранжевый основной CTA;
    - бирюзовый дополнительный фирменный акцент;
    - компактный блок услуг;
    - минимальный footer.

    Логотип:
        Ожидается MIME CID:
            atlantis-logo

        В SMTPMailProvider позднее должен быть добавлен PNG-файл
        с Content-ID:
            <atlantis-logo>

    Аргументы:
        client_name:
            Название организации или ФИО ИП.

        inn:
            ИНН клиента.

            В видимой части текущего шаблона не используется.

    Возвращает:
        HTML письма.
    """
    safe_client_name = html.escape(
        client_name
    )
    greeting = build_greeting(
        director_first_name=director_first_name,
        director_middle_name=director_middle_name,
    )
    safe_greeting = html.escape(
        greeting
    )
    tracking_pixel_url = ""

    if tracking_token:
        tracking_pixel_url = (
            "https://mail.projectsbis.ru/t/o/"
            f"{tracking_token}.gif"
        )

    return dedent(
        f"""
        <!DOCTYPE html>
        <html lang="ru">

        <head>
            <meta charset="utf-8">

            <meta
                name="viewport"
                content="width=device-width, initial-scale=1.0"
            >

            <title>{BRAND_NAME}</title>
        </head>

        <body
            style="
                margin: 0;
                padding: 0;
                background-color: {BACKGROUND};
                font-family: Arial, Helvetica, sans-serif;
                color: {TEXT_PRIMARY};
                -webkit-text-size-adjust: 100%;
            "
        >

            <!--
                PREHEADER

                Этот текст некоторые почтовые клиенты показывают
                после темы письма в списке входящих.
            -->
            <div
                style="
                    display: none;
                    max-height: 0;
                    overflow: hidden;
                    opacity: 0;
                    color: transparent;
                    font-size: 1px;
                    line-height: 1px;
                "
            >
                Онлайн-кассы, ФН, ОФД и настройка кассовой инфраструктуры.
            </div>


            <table
                role="presentation"
                width="100%"
                cellspacing="0"
                cellpadding="0"
                border="0"
                style="
                    width: 100%;
                    background-color: {BACKGROUND};
                "
            >
                <tr>
                    <td
                        align="center"
                        style="
                            padding: 32px 12px;
                        "
                    >

                        <!-- Главная карточка -->
                        <table
                            role="presentation"
                            width="100%"
                            cellspacing="0"
                            cellpadding="0"
                            border="0"
                            style="
                                width: 100%;
                                max-width: 620px;
                                background-color: {CARD_BACKGROUND};
                                border-radius: 14px;
                                overflow: hidden;
                            "
                        >


                            <!-- =================================================
                                 HEADER / BRAND
                                 ================================================= -->
                            <tr>
                                <td
                                    style="
                                        padding:
                                            26px
                                            32px
                                            22px
                                            32px;
                                    "
                                >

                                    <img
                                        src="cid:{LOGO_CID}"
                                        alt="Атлантис — автоматизация бизнеса"
                                        width="270"
                                        style="
                                            display: block;
                                            width: 270px;
                                            max-width: 100%;
                                            height: auto;
                                            border: 0;
                                        "
                                    >

                                </td>
                            </tr>


                            <!-- Фирменная тонкая линия -->
                            <tr>
                                <td
                                    style="
                                        padding: 0;
                                        height: 3px;
                                        background-color: {BRAND_CYAN};
                                        font-size: 1px;
                                        line-height: 1px;
                                    "
                                >
                                    &nbsp;
                                </td>
                            </tr>


                            <!-- =================================================
                                 MAIN CONTENT
                                 ================================================= -->
                            <tr>
                                <td
                                    style="
                                        padding:
                                            34px
                                            32px
                                            32px
                                            32px;
                                    "
                                >

                                    <!-- Приветствие -->
                                    <div
                                        style="
                                            margin: 0 0 19px 0;
                                            font-size: 20px;
                                            line-height: 1.35;
                                            font-weight: 700;
                                            letter-spacing: -0.2px;
                                            color: {TEXT_PRIMARY};
                                        "
                                    >
                                        {safe_greeting}
                                    </div>


                                    <!-- Первый абзац -->
                                    <div
                                        style="
                                            margin: 0;
                                            font-size: 16px;
                                            line-height: 1.65;
                                            color: {TEXT_PRIMARY};
                                        "
                                    >
                                        Помогаем бизнесу с подбором,
                                        регистрацией и настройкой
                                        <strong>онлайн-касс</strong>,
                                        фискальных накопителей и
                                        кассового оборудования.
                                    </div>


                                    <!-- Второй абзац -->
                                    <div
                                        style="
                                            margin-top: 17px;
                                            font-size: 16px;
                                            line-height: 1.65;
                                            color: {TEXT_PRIMARY};
                                        "
                                    >
                                        Если вам требуется новая касса,
                                        замена ФН, подключение ОФД,
                                        регистрация ККТ или помощь
                                        с настройкой — можем подобрать
                                        подходящее решение и помочь
                                        с запуском.
                                    </div>


                                    <!-- =================================================
                                         SERVICE CARD
                                         ================================================= -->
                                    <table
                                        role="presentation"
                                        width="100%"
                                        cellspacing="0"
                                        cellpadding="0"
                                        border="0"
                                        style="
                                            width: 100%;
                                            margin-top: 27px;
                                            background-color: {SOFT_BACKGROUND};
                                            border-radius: 10px;
                                        "
                                    >
                                        <tr>
                                            <td
                                                style="
                                                    padding:
                                                        22px
                                                        24px;
                                                "
                                            >

                                                <div
                                                    style="
                                                        margin-bottom: 14px;
                                                        font-size: 15px;
                                                        line-height: 1.4;
                                                        font-weight: 700;
                                                        color: {TEXT_PRIMARY};
                                                    "
                                                >
                                                    Можем помочь:
                                                </div>


                                                <table
                                                    role="presentation"
                                                    width="100%"
                                                    cellspacing="0"
                                                    cellpadding="0"
                                                    border="0"
                                                >

                                                    <tr>
                                                        <td
                                                            width="24"
                                                            valign="top"
                                                            style="
                                                                padding: 4px 0;
                                                                color: {BRAND_CYAN};
                                                                font-size: 16px;
                                                                font-weight: 700;
                                                            "
                                                        >
                                                            ✓
                                                        </td>

                                                        <td
                                                            style="
                                                                padding: 4px 0;
                                                                font-size: 15px;
                                                                line-height: 1.5;
                                                                color: {TEXT_PRIMARY};
                                                            "
                                                        >
                                                            Подобрать онлайн-кассу
                                                        </td>
                                                    </tr>


                                                    <tr>
                                                        <td
                                                            width="24"
                                                            valign="top"
                                                            style="
                                                                padding: 4px 0;
                                                                color: {BRAND_CYAN};
                                                                font-size: 16px;
                                                                font-weight: 700;
                                                            "
                                                        >
                                                            ✓
                                                        </td>

                                                        <td
                                                            style="
                                                                padding: 4px 0;
                                                                font-size: 15px;
                                                                line-height: 1.5;
                                                                color: {TEXT_PRIMARY};
                                                            "
                                                        >
                                                            Установить или заменить ФН
                                                        </td>
                                                    </tr>


                                                    <tr>
                                                        <td
                                                            width="24"
                                                            valign="top"
                                                            style="
                                                                padding: 4px 0;
                                                                color: {BRAND_CYAN};
                                                                font-size: 16px;
                                                                font-weight: 700;
                                                            "
                                                        >
                                                            ✓
                                                        </td>

                                                        <td
                                                            style="
                                                                padding: 4px 0;
                                                                font-size: 15px;
                                                                line-height: 1.5;
                                                                color: {TEXT_PRIMARY};
                                                            "
                                                        >
                                                            Зарегистрировать ККТ
                                                            и подключить ОФД
                                                        </td>
                                                    </tr>


                                                    <tr>
                                                        <td
                                                            width="24"
                                                            valign="top"
                                                            style="
                                                                padding: 4px 0;
                                                                color: {BRAND_CYAN};
                                                                font-size: 16px;
                                                                font-weight: 700;
                                                            "
                                                        >
                                                            ✓
                                                        </td>

                                                        <td
                                                            style="
                                                                padding: 4px 0;
                                                                font-size: 15px;
                                                                line-height: 1.5;
                                                                color: {TEXT_PRIMARY};
                                                            "
                                                        >
                                                            Настроить кассовое ПО,
                                                            маркировку и интеграции
                                                        </td>
                                                    </tr>

                                                </table>

                                            </td>
                                        </tr>
                                    </table>


                                    <!-- =================================================
                                         CTA
                                         ================================================= -->
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
                                            <td
                                                bgcolor="{BRAND_ORANGE}"
                                                style="
                                                    border-radius: 8px;
                                                "
                                            >

                                                <a
                                                    href="mailto:info@projectsbis.ru?subject=Подбор%20решения%20для%20онлайн-кассы"
                                                    style="
                                                        display: inline-block;
                                                        padding:
                                                            14px
                                                            24px;
                                                        font-size: 15px;
                                                        line-height: 1;
                                                        font-weight: 700;
                                                        color: #FFFFFF;
                                                        text-decoration: none;
                                                        background-color: {BRAND_ORANGE};
                                                        border-radius: 8px;
                                                    "
                                                >
                                                    Подобрать решение
                                                </a>

                                            </td>
                                        </tr>
                                    </table>


                                    <!-- Низкий барьер -->
                                    <div
                                        style="
                                            margin-top: 16px;
                                            font-size: 14px;
                                            line-height: 1.6;
                                            color: {TEXT_SECONDARY};
                                        "
                                    >
                                        Можно просто ответить на это письмо —
                                        уточним задачу и предложим
                                        подходящий вариант.
                                    </div>

                                </td>
                            </tr>


                            <!-- =================================================
                                 CLIENT CONTEXT
                                 ================================================= -->
                            <tr>
                                <td
                                    style="
                                        padding:
                                            0
                                            32px
                                            27px
                                            32px;
                                    "
                                >

                                    <div
                                        style="
                                            padding-top: 18px;
                                            border-top: 1px solid {BORDER};
                                            font-size: 12px;
                                            line-height: 1.55;
                                            color: {TEXT_MUTED};
                                        "
                                    >
                                        Для {safe_client_name}
                                    </div>

                                </td>
                            </tr>


                            <!-- =================================================
                                 FOOTER
                                 ================================================= -->
                            <tr>
                                <td
                                    style="
                                        padding:
                                            22px
                                            32px
                                            24px
                                            32px;
                                        background-color: #FAFAFA;
                                    "
                                >

                                    <div
                                        style="
                                            font-size: 14px;
                                            line-height: 1.5;
                                            font-weight: 700;
                                            color: {TEXT_PRIMARY};
                                        "
                                    >
                                        Атлантис
                                    </div>

                                    <div
                                        style="
                                            margin-top: 2px;
                                            font-size: 12px;
                                            line-height: 1.5;
                                            color: {TEXT_MUTED};
                                        "
                                    >
                                        Автоматизация бизнеса
                                    </div>

                                    <div
                                        style="
                                            margin-top: 10px;
                                            font-size: 12px;
                                            line-height: 1.5;
                                        "
                                    >
                                        <a
                                            href="mailto:info@projectsbis.ru"
                                            style="
                                                color: {BRAND_CYAN};
                                                text-decoration: none;
                                            "
                                        >
                                            info@projectsbis.ru
                                        </a>
                                    </div>




                                </td>
                            </tr>

                        </table>

                    </td>
                </tr>
            </table>

        {(
            f'''
            <img
                src="{tracking_pixel_url}"
                width="1"
                height="1"
                alt=""
                style="
                    display: block;
                    width: 1px;
                    height: 1px;
                    border: 0;
                "
            >
            '''
            if tracking_pixel_url
            else ""
        )}
        </body>
        </html>
        """
    ).strip()



def build_greeting(
    *,
    director_first_name: str,
    director_middle_name: str,
) -> str:
    """
    Сформировать персональное приветствие.

    Правила:
    - если есть имя и отчество:
      "Михаил Петрович, добрый день!";
    - если есть только имя:
      "Михаил, добрый день!";
    - если имя отсутствует:
      "Добрый день!".

    Аргументы:
        director_first_name:
            Имя директора.

        director_middle_name:
            Отчество директора.

    Возвращает:
        Готовое приветствие.
    """
    parts = [
        value.strip()
        for value in (
            director_first_name,
            director_middle_name,
        )
        if value and value.strip()
    ]

    if not parts:
        return "Добрый день!"

    return f"{' '.join(parts)}, добрый день!"
