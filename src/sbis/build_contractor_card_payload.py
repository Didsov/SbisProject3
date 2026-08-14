def build_contractor_card_payload(
    *,
    spp_uuid: str | None = None,
    contractor_id: int | None = None,
) -> dict[str, object]:
    """
    Сформировать payload для ContractorCard.Read.

    Поддерживаются два режима идентификации клиента:

    1. Через ContractorUUID:
        Используется, если передан spp_uuid.

    2. Через ИдО:
        Используется, если spp_uuid отсутствует,
        но передан contractor_id.

    Аргументы:
        spp_uuid:
            UUID контрагента СБИС.

        contractor_id:
            Числовой идентификатор контрагента,
            который передаётся в поле ИдО.

    Возвращает:
        Готовый JSON-RPC payload для ContractorCard.Read.

    Исключения:
        ValueError:
            Если не передан ни spp_uuid,
            ни contractor_id.
    """
    if spp_uuid is not None:
        spp_uuid = spp_uuid.strip()

        if not spp_uuid:
            spp_uuid = None
    if contractor_id is not None and not isinstance(contractor_id, int):
        raise TypeError(
            "contractor_id должен быть int или None"
        )

    if spp_uuid is None and contractor_id is None:
        raise ValueError(
            "Для ContractorCard.Read нужен "
            "spp_uuid или contractor_id"
        )

    extra_fields: dict[str, object] = {
        "browser": True,
        "firstLoad": True,
        "page": "crm",
        "isRead": True,
        "anchor": "about",
        "CountryCode": "643",
        "accordion": True,
    }

    if spp_uuid is not None:
        extra_fields["ContractorUUID"] = spp_uuid

    return {
        "jsonrpc": "2.0",
        "protocol": 7,
        "method": "ContractorCard.Read",
        "params": {
            "ИдО": (
                contractor_id
                if spp_uuid is None
                else None
            ),
            "ИмяМетода": None,
            "ДопПоля": extra_fields,
        },
        "id": 1,
    }