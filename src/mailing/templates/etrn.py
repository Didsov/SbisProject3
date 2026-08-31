"""Email-шаблон отдельного семейства кампаний ЭТрН."""

from __future__ import annotations

import html
import re
from textwrap import dedent

from src.config import CONTACT_EMAIL, CONTACT_PHONE_DISPLAY, CONTACT_WHATSAPP_URL
from src.mailing.templates.new_companies import TRACKING_BASE_URL, build_click_url


PREHEADER = (
    "Подключение ЭТрН, МЧД, ГосКлюч, интеграция с 1С/SAP/Saby "
    "и обучение сотрудников."
)
SUBJECT = "С 1 сентября 2026 года ЭТрН станет обязательной — подготовим подключение заранее"
_NAME_RE = re.compile(r"^[А-ЯЁ][а-яё-]{1,39}$")


def build_greeting(*, director_first_name: str, director_middle_name: str) -> str:
    first = director_first_name.strip()
    middle = director_middle_name.strip()
    if _NAME_RE.fullmatch(first) and _NAME_RE.fullmatch(middle):
        return f"Добрый день, {first} {middle}!"
    return "Добрый день!"


def build_subject(**_: object) -> str:
    return SUBJECT


def build_text_body(
    *, director_first_name: str, director_middle_name: str,
    tracking_token: str | None = None, **_: object,
) -> str:
    greeting = build_greeting(
        director_first_name=director_first_name,
        director_middle_name=director_middle_name,
    )
    cta = build_click_url(
        tracking_token=tracking_token,
        click_key="cta_email",
        direct_url=CONTACT_WHATSAPP_URL,
    )
    return dedent(f"""
        {greeting}

        С 1 сентября 2026 года участникам перевозок необходимо перейти на
        электронные транспортные накладные — ЭТрН. Предприниматель может быть
        перевозчиком, грузоотправителем или грузополучателем, поэтому подготовку
        важно начать заранее.

        КАК РАБОТАЕТ ЭТрН
        Грузоотправитель создаёт накладную → Перевозчик принимает груз →
        Грузополучатель принимает груз → Перевозчик подтверждает завершение.

        ЧТО МЫ БЕРЁМ НА СЕБЯ
        • помощь в получении КЭП и оформлении МЧД;
        • подключение к аккредитованному оператору ИС ЭПД;
        • подготовку сотрудников, транспорта и справочников;
        • интеграцию с 1С, SAP и Saby;
        • обучение логистов, бухгалтерии и водителей;
        • настройку мобильной работы водителей.

        Во вложении два документа: чек-лист подготовки бизнеса и коммерческое
        предложение по Saby TMS и ЭТрН.

        При подключении бесплатно поможем оформить ГосКлюч водителю.
        Получить консультацию по ЭТрН: {cta}

        Владивосток · Артём · Уссурийск · Находка
        {CONTACT_PHONE_DISPLAY}
        {CONTACT_EMAIL}
    """).strip()


def build_html_body(
    *, director_first_name: str, director_middle_name: str,
    tracking_token: str | None = None, **_: object,
) -> str:
    greeting = html.escape(build_greeting(
        director_first_name=director_first_name,
        director_middle_name=director_middle_name,
    ))
    cta = html.escape(build_click_url(
        tracking_token=tracking_token,
        click_key="cta_email",
        direct_url=CONTACT_WHATSAPP_URL,
    ), quote=True)
    pixel = (
        f'<img src="{TRACKING_BASE_URL}/t/o/{html.escape(tracking_token)}.gif" '
        'width="1" height="1" alt="" style="display:block;border:0">'
        if tracking_token else ""
    )
    steps = [
        ("Грузоотправитель", "создаёт транспортную накладную"),
        ("Перевозчик", "принимает груз и отправляется в рейс"),
        ("Грузополучатель", "принимает груз"),
        ("Перевозчик", "подтверждает завершение перевозки"),
    ]
    steps_html = "".join(
        f'<tr><td style="padding:10px 0;border-bottom:1px solid #e8eaed">'
        f'<b style="color:#13afc1">{html.escape(title)}</b><br>{html.escape(text)}'
        f'</td></tr>' for title, text in steps
    )
    benefits = "".join(
        f'<tr><td style="padding:5px 0">✓ {html.escape(item)}</td></tr>'
        for item in (
            "Помощь в получении КЭП и оформлении МЧД",
            "Подключение к аккредитованному оператору ИС ЭПД",
            "Подготовка сотрудников, транспорта и справочников",
            "Интеграция с 1С, SAP и Saby",
            "Обучение логистов, бухгалтерии и водителей",
            "Мобильная работа водителей с ЭТрН",
        )
    )
    return dedent(f"""
        <!doctype html><html lang="ru"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1"></head>
        <body style="margin:0;background:#f4f6f7;font-family:Arial,sans-serif;color:#202124">
        <div style="display:none;max-height:0;overflow:hidden">{html.escape(PREHEADER)}</div>
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:24px 10px">
        <table role="presentation" width="620" cellspacing="0" cellpadding="0" style="width:100%;max-width:620px;background:#fff;border-radius:14px">
        <tr><td style="padding:30px"><img src="cid:atlantis-logo" width="270" alt="Атлантис" style="max-width:100%;height:auto"></td></tr>
        <tr><td style="padding:0 30px 30px">
        <p style="font-size:20px;margin:0 0 18px">{greeting}</p>
        <h1 style="font-size:27px;line-height:1.25;margin:0 0 18px;color:#e76a2e">С 1 сентября 2026 года ЭТрН станет обязательной</h1>
        <p style="line-height:1.55">Участникам перевозок необходимо перейти на электронные транспортные накладные. Подготовиться важно заранее — компания может выступать перевозчиком, грузоотправителем или грузополучателем.</p>
        <h2 style="font-size:17px;margin-top:26px">КАК РАБОТАЕТ ЭТрН</h2><table role="presentation" width="100%">{steps_html}</table>
        <h2 style="font-size:17px;margin-top:26px">ЧТО МЫ БЕРЁМ НА СЕБЯ</h2><table role="presentation" width="100%">{benefits}</table>
        <div style="margin:24px 0;padding:18px;background:#f7f9fa;border-left:4px solid #13afc1"><b>Во вложении:</b><br>чек-лист подготовки бизнеса и коммерческое предложение по Saby TMS и ЭТрН.</div>
        <div style="margin:24px 0;padding:18px;background:#fff4ed;border-radius:10px"><b>Специальное предложение:</b> бесплатно поможем оформить ГосКлюч водителю.</div>
        <table role="presentation" cellspacing="0" cellpadding="0"><tr><td bgcolor="#e76a2e" style="border-radius:8px"><a href="{cta}" style="display:inline-block;padding:14px 22px;color:#fff;text-decoration:none;font-weight:bold">Получить консультацию по ЭТрН</a></td></tr></table>
        <p style="margin-top:28px;line-height:1.55">Не откладывайте подключение: выпуск подписей и доверенностей, обучение и интеграция требуют времени.</p>
        <p style="margin-top:28px;color:#5f6368">Владивосток · Артём · Уссурийск · Находка<br>{html.escape(str(CONTACT_PHONE_DISPLAY or ''))}<br>{html.escape(str(CONTACT_EMAIL or ''))}</p>
        </td></tr></table></td></tr></table>{pixel}</body></html>
    """).strip()
