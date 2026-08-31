"""
Реестр шаблонов почтовых кампаний.

Назначение модуля:
- хранить соответствие между template_name из базы данных
  и конкретным модулем шаблона;
- позволять sender выбирать шаблон динамически;
- исключить жёсткую привязку sender.py к одной кампании.

Функции:
- get_mail_template() — получить шаблон по имени.
"""

from __future__ import annotations

from src.mailing.templates import etrn, new_companies


MAIL_TEMPLATES = {
    "new_companies": new_companies,
    "etrn": etrn,
}


def get_mail_template(
    template_name: str,
):
    """
    Получить модуль почтового шаблона по его имени.

    Аргументы:
        template_name:
            Имя шаблона из mail_campaigns.template_name.

    Возвращает:
        Модуль шаблона, содержащий функции:
        - build_subject();
        - build_text_body();
        - build_html_body().

    Исключения:
        ValueError:
            Если template_name пустое.

        LookupError:
            Если шаблон с таким именем не зарегистрирован.
    """
    normalized_name = template_name.strip()

    if not normalized_name:
        raise ValueError(
            "template_name не может быть пустым"
        )

    template = MAIL_TEMPLATES.get(
        normalized_name
    )

    if template is None:
        raise LookupError(
            f"Неизвестный почтовый шаблон: {normalized_name!r}"
        )

    return template
