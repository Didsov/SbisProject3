"""
Шаблон письма кампании для новых организаций.

Назначение модуля:
- сформировать тему письма;
- сформировать plain-text версию;
- сформировать HTML-версию;
- персонализировать письмо названием организации или ФИО ИП;
- использовать фирменный стиль компании «Атлантис»;
- формировать click-tracking ссылки и open-tracking pixel;
- брать контактные destinations из централизованной конфигурации.

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
from urllib.parse import quote

from src.config import (
    CONTACT_EMAIL,
    CONTACT_MAX_URL,
    CONTACT_PHONE_DISPLAY,
    CONTACT_PHONE_URL,
    CONTACT_TELEGRAM_URL,
    CONTACT_WHATSAPP_URL,
    build_contact_email_url,
)


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

TRACKING_BASE_URL = "https://mail.projectsbis.ru"


def build_click_url(
    *,
    tracking_token: str | None,
    click_key: str,
    direct_url: str,
) -> str:
    """
    Сформировать ссылку с click tracking.

    Если tracking_token передан, получатель сначала открывает
    наш endpoint вида:

        /t/c/<tracking_token>/<click_key>

    Tracking-сервис записывает событие перехода и затем делает
    HTTP redirect на реальный адрес.

    Если tracking_token отсутствует, возвращается исходная ссылка.
    Это удобно для локального preview шаблона и писем без трекинга.

    Аргументы:
        tracking_token:
            Токен конкретного mail_messages.

        click_key:
            Стабильный идентификатор ссылки:
            cta_email, phone, whatsapp, telegram, max.

        direct_url:
            Реальный адрес назначения.

    Возвращает:
        Tracking URL либо исходный direct_url.
    """
    if not tracking_token:
        return direct_url

    safe_token = quote(tracking_token, safe="")
    safe_key = quote(click_key, safe="")

    return (
        f"{TRACKING_BASE_URL}/t/c/"
        f"{safe_token}/{safe_key}"
    )


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

        Связаться с нами:
        Телефон: {CONTACT_PHONE_DISPLAY}
        WhatsApp: {CONTACT_WHATSAPP_URL}
        Telegram: {CONTACT_TELEGRAM_URL}
        MAX: {CONTACT_MAX_URL}

        С уважением,
        Атлантис
        Автоматизация бизнеса

        {CONTACT_EMAIL}
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
            f"{TRACKING_BASE_URL}/t/o/"
            f"{tracking_token}.gif"
        )

    # Имя click_key сохраняется для совместимости с накопленной
    # аналитикой. Реальный destination основной CTA теперь WhatsApp.
    cta_email_url = build_click_url(
        tracking_token=tracking_token,
        click_key="cta_email",
        direct_url=CONTACT_WHATSAPP_URL,
    )
    phone_url = build_click_url(
        tracking_token=tracking_token,
        click_key="phone",
        direct_url=CONTACT_PHONE_URL,
    )
    whatsapp_url = build_click_url(
        tracking_token=tracking_token,
        click_key="whatsapp",
        direct_url=CONTACT_WHATSAPP_URL,
    )
    telegram_url = build_click_url(
        tracking_token=tracking_token,
        click_key="telegram",
        direct_url=CONTACT_TELEGRAM_URL,
    )
    max_url = build_click_url(
        tracking_token=tracking_token,
        click_key="max",
        direct_url=CONTACT_MAX_URL,
    )
    email_url = build_contact_email_url(
        CONTACT_EMAIL
    )

    safe_cta_email_url = html.escape(cta_email_url, quote=True)
    safe_phone_url = html.escape(phone_url, quote=True)
    safe_whatsapp_url = html.escape(whatsapp_url, quote=True)
    safe_telegram_url = html.escape(telegram_url, quote=True)
    safe_max_url = html.escape(max_url, quote=True)
    safe_email_url = html.escape(email_url, quote=True)
    safe_contact_email = html.escape(
        str(CONTACT_EMAIL or "")
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
                                                    href="{safe_cta_email_url}"
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



                                    <!-- =================================================
                                         CONTACTS / TRACKED LINKS
                                         ================================================= -->
                                    <table
                                        role="presentation"
                                        width="100%"
                                        cellspacing="0"
                                        cellpadding="0"
                                        border="0"
                                        style="
                                            width: 100%;
                                            margin-top: 24px;
                                            background-color: {SOFT_BACKGROUND};
                                            border-radius: 10px;
                                        "
                                    >
                                        <tr>
                                            <td
                                                style="
                                                    padding: 20px 24px 22px 24px;
                                                "
                                            >
                                                <!-- Заголовок блока -->
                                                <div
                                                    style="
                                                        margin: 0;
                                                        font-size: 15px;
                                                        line-height: 1.4;
                                                        font-weight: 700;
                                                        color: {TEXT_PRIMARY};
                                                    "
                                                >
                                                    Связаться с нами
                                                </div>

                                                <!-- Телефон: отдельная усиленная строка -->
                                                <table
                                                    role="presentation"
                                                    cellspacing="0"
                                                    cellpadding="0"
                                                    border="0"
                                                    style="
                                                        margin-top: 12px;
                                                    "
                                                >
                                                    <tr>
                                                        <td
                                                            valign="middle"
                                                            style="
                                                                width: 28px;
                                                                height: 28px;
                                                                text-align: center;
                                                                vertical-align: middle;
                                                                background-color: #E8F8FA;
                                                                border-radius: 7px;
                                                                font-size: 16px;
                                                                line-height: 28px;
                                                                color: {BRAND_CYAN};
                                                            "
                                                        >
                                                            &#9742;
                                                        </td>

                                                        <td
                                                            valign="middle"
                                                            style="
                                                                padding-left: 9px;
                                                            "
                                                        >
                                                            <a
                                                                href="{safe_phone_url}"
                                                                style="
                                                                    display: inline-block;
                                                                    color: {TEXT_PRIMARY};
                                                                    text-decoration: none;
                                                                    font-size: 16px;
                                                                    line-height: 1.4;
                                                                    font-weight: 700;
                                                                "
                                                            >
                                                                {CONTACT_PHONE_DISPLAY}
                                                            </a>
                                                        </td>
                                                    </tr>
                                                </table>

                                                <!-- Подпись -->
                                                <div
                                                    style="
                                                        margin-top: 13px;
                                                        margin-bottom: 9px;
                                                        font-size: 12px;
                                                        line-height: 1.45;
                                                        color: {TEXT_SECONDARY};
                                                    "
                                                >
                                                    Выберите удобный мессенджер
                                                </div>

                                                <!--
                                                    Email-safe кнопки.

                                                    Используются отдельные inline-table,
                                                    поэтому на узком экране клиенты,
                                                    допускающие перенос inline-block,
                                                    смогут перенести кнопку целиком
                                                    на следующую строку без горизонтального скролла.
                                                -->
                                                <div
                                                    style="
                                                        font-size: 0;
                                                        line-height: 0;
                                                    "
                                                >
                                                    <!-- WhatsApp -->
                                                    <table
                                                        role="presentation"
                                                        cellspacing="0"
                                                        cellpadding="0"
                                                        border="0"
                                                        style="
                                                            display: inline-table;
                                                            margin: 0 8px 8px 0;
                                                            vertical-align: top;
                                                        "
                                                    >
                                                        <tr>
                                                            <td
                                                                style="
                                                                    height: 40px;
                                                                    background-color: #FFFFFF;
                                                                    border: 1px solid #E2E6E8;
                                                                    border-radius: 8px;
                                                                "
                                                            >
                                                                <a
                                                                    href="{safe_whatsapp_url}"
                                                                    style="
                                                                        display: block;
                                                                        padding: 10px 14px;
                                                                        color: {TEXT_PRIMARY};
                                                                        text-decoration: none;
                                                                        font-size: 14px;
                                                                        line-height: 18px;
                                                                        font-weight: 600;
                                                                        white-space: nowrap;
                                                                    "
                                                                >
                                                                    <span
                                                                        style="
                                                                            display: inline-block;
                                                                            width: 18px;
                                                                            height: 18px;
                                                                            margin-right: 7px;
                                                                            border-radius: 50%;
                                                                            background-color: #E8F8FA;
                                                                            color: {BRAND_CYAN};
                                                                            font-size: 13px;
                                                                            line-height: 18px;
                                                                            text-align: center;
                                                                            vertical-align: -1px;
                                                                        "
                                                                    >&#9742;</span>WhatsApp
                                                                </a>
                                                            </td>
                                                        </tr>
                                                    </table>

                                                    <!-- Telegram -->
                                                    <table
                                                        role="presentation"
                                                        cellspacing="0"
                                                        cellpadding="0"
                                                        border="0"
                                                        style="
                                                            display: inline-table;
                                                            margin: 0 8px 8px 0;
                                                            vertical-align: top;
                                                        "
                                                    >
                                                        <tr>
                                                            <td
                                                                style="
                                                                    height: 40px;
                                                                    background-color: #FFFFFF;
                                                                    border: 1px solid #E2E6E8;
                                                                    border-radius: 8px;
                                                                "
                                                            >
                                                                <a
                                                                    href="{safe_telegram_url}"
                                                                    style="
                                                                        display: block;
                                                                        padding: 10px 14px;
                                                                        color: {TEXT_PRIMARY};
                                                                        text-decoration: none;
                                                                        font-size: 14px;
                                                                        line-height: 18px;
                                                                        font-weight: 600;
                                                                        white-space: nowrap;
                                                                    "
                                                                >
                                                                    <span
                                                                        style="
                                                                            display: inline-block;
                                                                            width: 18px;
                                                                            margin-right: 7px;
                                                                            color: {BRAND_CYAN};
                                                                            font-size: 17px;
                                                                            line-height: 18px;
                                                                            text-align: center;
                                                                            vertical-align: -1px;
                                                                        "
                                                                    >&#10148;</span>Telegram
                                                                </a>
                                                            </td>
                                                        </tr>
                                                    </table>

                                                    <!-- MAX -->
                                                    <table
                                                        role="presentation"
                                                        cellspacing="0"
                                                        cellpadding="0"
                                                        border="0"
                                                        style="
                                                            display: inline-table;
                                                            margin: 0 0 8px 0;
                                                            vertical-align: top;
                                                        "
                                                    >
                                                        <tr>
                                                            <td
                                                                style="
                                                                    height: 40px;
                                                                    background-color: #FFFFFF;
                                                                    border: 1px solid #E2E6E8;
                                                                    border-radius: 8px;
                                                                "
                                                            >
                                                                <a
                                                                    href="{safe_max_url}"
                                                                    style="
                                                                        display: block;
                                                                        padding: 10px 14px;
                                                                        color: {TEXT_PRIMARY};
                                                                        text-decoration: none;
                                                                        font-size: 14px;
                                                                        line-height: 18px;
                                                                        font-weight: 600;
                                                                        white-space: nowrap;
                                                                    "
                                                                >
                                                                    <span
                                                                        style="
                                                                            display: inline-block;
                                                                            width: 18px;
                                                                            height: 18px;
                                                                            margin-right: 7px;
                                                                            border-radius: 50%;
                                                                            background-color: #E8F8FA;
                                                                            color: {BRAND_CYAN};
                                                                            font-size: 11px;
                                                                            line-height: 18px;
                                                                            font-weight: 700;
                                                                            text-align: center;
                                                                            vertical-align: -1px;
                                                                        "
                                                                    >M</span>MAX
                                                                </a>
                                                            </td>
                                                        </tr>
                                                    </table>
                                                </div>
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
                                            line-height: 1.8;
                                        "
                                    >
                                        <a
                                            href="{safe_phone_url}"
                                            style="
                                                color: {BRAND_CYAN};
                                                text-decoration: none;
                                            "
                                        >
                                            {CONTACT_PHONE_DISPLAY}
                                        </a>
                                        <br>
                                        <a
                                            href="{safe_email_url}"
                                            style="
                                                color: {BRAND_CYAN};
                                                text-decoration: none;
                                            "
                                        >
                                            {safe_contact_email}
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
