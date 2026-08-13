"""
Разбор декодированной карточки ContractorCard.Read.

Назначение файла:
- извлекать из карточки основные реквизиты организации;
- извлекать сведения о директоре;
- извлекать телефоны и email из персонализированных контактов;
- учитывать данные головной организации для подразделений;
- приводить данные к стабильной структуре для дальнейшей работы;
- отделять разбор данных СБИС от HTTP-запросов и хранения в БД.

Функции:
- parse_contractor_card() — преобразует декодированную карточку
  ContractorCard.Read в нормализованный словарь с данными организации.

Основные источники данных:
- верхний уровень карточки:
  ShortName, Название, ИНН, КПП, ОГРН, SppUuid;

- spp_data:
  реквизиты текущей организации или подразделения;

- extra_data:
  персонализированные контакты текущей организации;

- head_data:
  данные головной организации, которые используются для получения
  директора и контактов, если карточка относится к подразделению.

Результат функции предназначен для последующего:
- сохранения в БД;
- фильтрации клиентов;
- подготовки рассылок;
- формирования отчётов.
"""
from __future__ import annotations

from typing import Any
def parse_contractor_card(
    card: dict[str, Any],
) -> dict[str, Any]:
    """
    Извлечь нужные данные из декодированной карточки организации.

    Что делает:
    - получает основные реквизиты текущей организации;
    - использует spp_data как дополнительный источник реквизитов;
    - определяет источник данных директора;
    - определяет источник персонализированных контактов;
    - для обычной организации использует spp_data и extra_data;
    - для подразделения может использовать head_data;
    - извлекает фамилию, имя, отчество, ИНН и должность директора;
    - собирает телефоны;
    - собирает email;
    - удаляет дубли контактов с сохранением исходного порядка;
    - возвращает данные в едином внутреннем формате проекта.

    Аргументы:
        card:
            Полностью декодированный result метода
            ContractorCard.Read.

    Возвращает:
        Словарь следующей структуры:

        {
            "name": str | None,
            "inn": str | None,
            "kpp": str | None,
            "ogrn": str | None,
            "spp_uuid": str | None,

            "director": {
                "last_name": str | None,
                "first_name": str | None,
                "middle_name": str | None,
                "inn": str | None,
                "position": str | None
            },

            "phones": list[str],
            "emails": list[str]
        }

    Приоритет источников реквизитов:
        name:
            ShortName → Название → spp_data["Название"]

        inn:
            card["ИНН"] → spp_data["ИНН"]

        kpp:
            card["КПП"] → spp_data["КПП"]

        ogrn:
            card["ОГРН"] → spp_data["ОГРН"]

        spp_uuid:
            card["SppUuid"] → spp_data["SppUuid"]

    Источник директора:
        - по умолчанию spp_data;
        - если присутствует head_data["spp_data"], используется
          spp_data головной организации.

    Источник контактов:
        - по умолчанию extra_data;
        - если присутствует head_data["extra_data"], используется
          extra_data головной организации.

    Важно:
        Для подразделения реквизиты самой карточки не подменяются
        реквизитами головной организации.

        head_data используется только как источник директора
        и персонализированных контактов.

        Отсутствие телефонов или email не считается ошибкой.
        В таком случае возвращается пустой список.
    """
    spp_data = card.get("spp_data")
    extra_data = card.get("extra_data")

    if not isinstance(spp_data, dict):
        spp_data = {}

    if not isinstance(extra_data, dict):
        extra_data = {}

    # Для подразделений сведения о директоре и персонализированные контакты
    # могут находиться в карточке головной организации.
    director_data = spp_data
    contacts_data = extra_data

    head_data = card.get("head_data")

    if isinstance(head_data, dict):
        head_spp_data = head_data.get("spp_data")

        if isinstance(head_spp_data, dict):
            director_data = head_spp_data

        head_extra_data = head_data.get("extra_data")

        if isinstance(head_extra_data, dict):
            contacts_data = head_extra_data

    contacts = contacts_data.get(
        "Контрагент.GetPersonalisedContacts",
        [],
    )

    phones: list[str] = []
    emails: list[str] = []

    if isinstance(contacts, list):
        for contact in contacts:
            if not isinstance(contact, dict):
                continue

            contact_phones = contact.get("Phones")

            if isinstance(contact_phones, list):
                for phone in contact_phones:
                    if (
                        isinstance(phone, str)
                        and phone
                        and phone not in phones
                    ):
                        phones.append(phone)

            contact_emails = contact.get("Emails")

            if isinstance(contact_emails, list):
                for email in contact_emails:
                    if (
                        isinstance(email, str)
                        and email
                        and email not in emails
                    ):
                        emails.append(email)

    return {
        "name": (
            card.get("ShortName")
            or card.get("Название")
            or spp_data.get("Название")
        ),
        "inn": (
            card.get("ИНН")
            or spp_data.get("ИНН")
        ),
        "kpp": (
            card.get("КПП")
            or spp_data.get("КПП")
        ),
        "ogrn": (
            card.get("ОГРН")
            or spp_data.get("ОГРН")
        ),
        "spp_uuid": (
            card.get("SppUuid")
            or spp_data.get("SppUuid")
        ),
        "director": {
            "last_name": director_data.get(
                "Директор.Фамилия"
            ),
            "first_name": director_data.get(
                "Директор.Имя"
            ),
            "middle_name": director_data.get(
                "Директор.Отчество"
            ),
            "inn": director_data.get(
                "Директор.ИНН"
            ),
            "position": director_data.get(
                "Директор.Примечание"
            ),
        },
        "phones": phones,
        "emails": emails,
    }