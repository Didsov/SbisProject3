"""
Формирование и отправка дневного отчёта по почтовой кампании.

Отчёт содержит:
- компанию;
- email;
- итоговый статус доставки.

Статусы отображаются:
- delivered -> ✓
- bounced   -> ✕
- failed    -> ✕
- sent      -> …
- deferred  -> …

Отчёт отправляется:
- HTML-таблицей в теле письма;
- XLSX-файлом во вложении.

Открытия и клики в этот отчёт не входят.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from io import BytesIO
from html import escape

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment

from src.config import DAILY_REPORT_EMAILS
from src.database import get_connection
from src.mailing.smtp_provider import (
    MailAttachment,
    SMTPMailProvider,
)


STATUS_SYMBOLS = {
    "delivered": "✓",
    "bounced": "✕",
    "failed": "✕",
    "sent": "…",
    "deferred": "…",
    "pending": "…",
}


@dataclass(slots=True)
class DailyReportMessage:
    recipient_id: int
    to_email: str
    subject: str
    text_body: str
    html_body: str
    attachments: list[MailAttachment] | None = None


def get_campaign_delivery_rows(
    campaign_id: int,
) -> list[dict[str, object]]:
    """
    Получить строки дневного отчёта по кампании.

    Для каждого получателя выбирается последнее
    боевое mail_messages.
    """
    if campaign_id < 1:
        raise ValueError(
            "campaign_id должен быть больше 0"
        )

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                mr.id AS recipient_id,
                c.name AS company_name,
                mr.email,
                COALESCE(
                    (
                        SELECT mm.status
                        FROM mail_messages AS mm
                        WHERE
                            mm.recipient_id = mr.id
                            AND mm.is_test = 0
                        ORDER BY mm.id DESC
                        LIMIT 1
                    ),
                    mr.status
                ) AS delivery_status
            FROM mail_recipients AS mr

            INNER JOIN clients AS c
                ON c.id = mr.client_id

            WHERE
                mr.campaign_id = ?

            ORDER BY
                mr.id
            """,
            (campaign_id,),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def build_html_report(
    rows: list[dict[str, object]],
) -> str:
    """
    Сформировать HTML-таблицу отчёта.
    """
    delivered = sum(
        1
        for row in rows
        if row["delivery_status"] == "delivered"
    )

    failed = sum(
        1
        for row in rows
        if row["delivery_status"] in {
            "bounced",
            "failed",
        }
    )

    waiting = len(rows) - delivered - failed

    table_rows: list[str] = []

    for row in rows:
        company_name = escape(
            str(row["company_name"] or "")
        )

        email = escape(
            str(row["email"] or "")
        )

        status = str(
            row["delivery_status"] or "pending"
        )

        symbol = STATUS_SYMBOLS.get(
            status,
            "…",
        )

        table_rows.append(
            f"""
            <tr>
                <td style="
                    padding:8px 10px;
                    border-bottom:1px solid #e5e7eb;
                ">
                    {company_name}
                </td>

                <td style="
                    padding:8px 10px;
                    border-bottom:1px solid #e5e7eb;
                ">
                    {email}
                </td>

                <td style="
                    padding:8px 10px;
                    text-align:center;
                    border-bottom:1px solid #e5e7eb;
                    font-size:18px;
                ">
                    {symbol}
                </td>
            </tr>
            """
        )

    return f"""
    <!doctype html>
    <html>
    <body style="
        margin:0;
        padding:24px;
        font-family:Arial,Helvetica,sans-serif;
        color:#1f2937;
    ">

        <h2 style="margin:0 0 16px;">
            Отчёт по рассылке
        </h2>

        <p>
            Всего: <strong>{len(rows)}</strong><br>
            Доставлено: <strong>{delivered}</strong><br>
            Ошибок: <strong>{failed}</strong><br>
            Ожидают результата: <strong>{waiting}</strong>
        </p>

        <table
            cellpadding="0"
            cellspacing="0"
            style="
                border-collapse:collapse;
                width:100%;
                max-width:900px;
                border:1px solid #e5e7eb;
            "
        >
            <thead>
                <tr style="background:#f3f4f6;">
                    <th style="
                        padding:9px 10px;
                        text-align:left;
                    ">
                        Компания
                    </th>

                    <th style="
                        padding:9px 10px;
                        text-align:left;
                    ">
                        Email
                    </th>

                    <th style="
                        padding:9px 10px;
                        text-align:center;
                    ">
                        Статус
                    </th>
                </tr>
            </thead>

            <tbody>
                {''.join(table_rows)}
            </tbody>
        </table>

        <p style="
            margin-top:16px;
            font-size:12px;
            color:#6b7280;
        ">
            ✓ — почтовый сервер получателя принял письмо.<br>
            ✕ — письмо не доставлено.<br>
            … — результат доставки ещё не определён.
        </p>

    </body>
    </html>
    """


def build_text_report(
    rows: list[dict[str, object]],
) -> str:
    """
    Сформировать текстовую версию отчёта.
    """
    lines = [
        "Отчёт по рассылке",
        "",
    ]

    for row in rows:
        status = str(
            row["delivery_status"] or "pending"
        )

        symbol = STATUS_SYMBOLS.get(
            status,
            "…",
        )

        lines.append(
            f"{symbol} "
            f"{row['company_name']} — "
            f"{row['email']}"
        )

    return "\n".join(lines)


def build_xlsx_report(
    rows: list[dict[str, object]],
) -> bytes:
    """
    Сформировать XLSX-отчёт и вернуть его содержимое в байтах.
    """
    workbook = Workbook()

    sheet = workbook.active
    sheet.title = "Рассылка"

    headers = [
        "Компания",
        "Email",
        "Статус",
    ]

    sheet.append(
        headers
    )

    for cell in sheet[1]:
        cell.font = Font(
            bold=True
        )

        cell.alignment = Alignment(
            horizontal="center",
        )

    for row in rows:
        status = str(
            row["delivery_status"] or "pending"
        )

        symbol = STATUS_SYMBOLS.get(
            status,
            "…",
        )

        sheet.append(
            [
                str(
                    row["company_name"] or ""
                ),
                str(
                    row["email"] or ""
                ),
                symbol,
            ]
        )

    sheet.column_dimensions["A"].width = 45
    sheet.column_dimensions["B"].width = 35
    sheet.column_dimensions["C"].width = 12

    for cell in sheet["C"]:
        cell.alignment = Alignment(
            horizontal="center",
        )

    output = BytesIO()

    workbook.save(
        output
    )

    return output.getvalue()


def get_report_recipients() -> list[str]:
    """
    Получить список email для отправки дневного отчёта.
    """
    raw_value = (
        DAILY_REPORT_EMAILS or ""
    ).strip()

    if not raw_value:
        raise RuntimeError(
            "Не задан DAILY_REPORT_EMAILS"
        )

    recipients = [
        value.strip()
        for value in raw_value.split(",")
        if value.strip()
    ]

    if not recipients:
        raise RuntimeError(
            "DAILY_REPORT_EMAILS не содержит адресов"
        )

    return recipients


async def send_daily_report(
    campaign_id: int,
) -> None:
    """
    Сформировать и отправить дневной отчёт.
    """
    rows = get_campaign_delivery_rows(
        campaign_id
    )

    html_body = build_html_report(
        rows
    )

    text_body = build_text_report(
        rows
    )

    xlsx_bytes = build_xlsx_report(
        rows
    )

    attachment = MailAttachment(
        filename=f"mail_report_campaign_{campaign_id}.xlsx",
        content=xlsx_bytes,
        maintype="application",
        subtype=(
            "vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )

    provider = SMTPMailProvider.from_env()

    recipients = get_report_recipients()

    for email in recipients:
        message = DailyReportMessage(
            recipient_id=0,
            to_email=email,
            subject=(
                f"Отчёт по рассылке — "
                f"кампания #{campaign_id}"
            ),
            text_body=text_body,
            html_body=html_body,
            attachments=[
                attachment
            ],
        )

        result = await provider.send(
            message
        )

        print(
            f"{email}: "
            f"{'OK' if result.success else 'ERROR'}"
        )

        if result.error:
            print(
                f"  {result.error}"
            )


def main() -> None:
    """
    CLI-точка входа.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Сформировать и отправить дневной "
            "отчёт по почтовой кампании."
        )
    )

    parser.add_argument(
        "--campaign-id",
        type=int,
        required=True,
        help="ID почтовой кампании.",
    )

    arguments = parser.parse_args()

    asyncio.run(
        send_daily_report(
            arguments.campaign_id
        )
    )


if __name__ == "__main__":
    main()