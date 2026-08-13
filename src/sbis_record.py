def record_to_dict(record: dict[str, Any]) -> dict[str, Any]:
    """
    Преобразовать внутренний record-формат СБИС из d/s в обычный словарь.

    Пример:
        d = [False, 50]
        s = [
            {"n": "isExpired", "t": "Логическое"},
            {"n": "ReadRows", "t": "Число целое"},
        ]

    Результат:
        {
            "isExpired": False,
            "ReadRows": 50,
        }
    """