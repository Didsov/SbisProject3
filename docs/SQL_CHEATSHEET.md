# ProjectSbis — полезные SQL-запросы

Эта шпаргалка рассчитана на текущую структуру базы `data/project.db`.

Основные таблицы:

- `clients` — общая таблица клиентов;
- `client_selections` — связь клиентов с выборками СБИС;
- `client_contacts` — телефоны и e-mail клиентов.

---

## 1. Общее количество клиентов в базе

```sql
SELECT COUNT(*) AS total_clients
FROM clients;
```

---

## 2. Сколько клиентов находится в конкретной выборке

Для выборки `41307`:

```sql
SELECT COUNT(*) AS clients_in_selection
FROM client_selections
WHERE selection_id = 41307;
```

Для `42420`:

```sql
SELECT COUNT(*) AS clients_in_selection
FROM client_selections
WHERE selection_id = 42420;
```

---

## 3. Сколько клиентов выборки ещё ждут ContractorCard.Read

```sql
SELECT COUNT(*) AS unenriched_clients
FROM clients AS c
INNER JOIN client_selections AS cs
    ON cs.client_id = c.id
WHERE
    cs.selection_id = 41307
    AND c.enriched = 0;
```

Чтобы проверить `42420`, заменить:

```text
41307
```

на:

```text
42420
```

---

## 4. Сколько клиентов выборки уже обогащено

```sql
SELECT COUNT(*) AS enriched_clients
FROM clients AS c
INNER JOIN client_selections AS cs
    ON cs.client_id = c.id
WHERE
    cs.selection_id = 41307
    AND c.enriched = 1;
```

---

## 5. Полный список клиентов конкретной выборки

```sql
SELECT
    c.id,
    c.spp_uuid,
    c.inn,
    c.name,
    c.kpp,
    c.ogrn,
    c.enriched,
    c.updated_at
FROM clients AS c
INNER JOIN client_selections AS cs
    ON cs.client_id = c.id
WHERE cs.selection_id = 41307
ORDER BY c.id;
```

---

## 6. Только необогащённые клиенты выборки

Это примерно соответствует очереди, которую получает приложение перед `ContractorCard.Read`.

```sql
SELECT
    c.id,
    c.spp_uuid,
    c.inn,
    c.name
FROM clients AS c
INNER JOIN client_selections AS cs
    ON cs.client_id = c.id
WHERE
    cs.selection_id = 41307
    AND c.enriched = 0
ORDER BY c.id;
```

---

## 7. Первые 10 необогащённых клиентов выборки

Полезно для проверки того, кто попадёт в:

```text
--enrich-limit 10
```

```sql
SELECT
    c.id,
    c.spp_uuid,
    c.inn,
    c.name
FROM clients AS c
INNER JOIN client_selections AS cs
    ON cs.client_id = c.id
WHERE
    cs.selection_id = 41307
    AND c.enriched = 0
ORDER BY c.id
LIMIT 10;
```

---

## 8. Данные клиента вместе с руководителем

```sql
SELECT
    c.id,
    c.name,
    c.inn,
    c.kpp,
    c.ogrn,
    c.spp_uuid,
    c.director_last_name,
    c.director_first_name,
    c.director_middle_name,
    c.director_inn,
    c.director_position,
    c.enriched
FROM clients AS c
WHERE c.inn = '2543082240';
```

ИНН в примере заменить на нужный.

---

## 9. Найти клиента по части названия

```sql
SELECT
    id,
    name,
    inn,
    spp_uuid,
    enriched
FROM clients
WHERE name LIKE '%ВОРЛД%'
ORDER BY name;
```

---

## 10. Все контакты конкретного клиента

По ИНН:

```sql
SELECT
    c.name,
    c.inn,
    cc.contact_type,
    cc.value
FROM clients AS c
INNER JOIN client_contacts AS cc
    ON cc.client_id = c.id
WHERE c.inn = '2543082240'
ORDER BY cc.contact_type, cc.value;
```

---

## 11. Все телефоны клиентов конкретной выборки

```sql
SELECT
    c.name,
    c.inn,
    cc.value AS phone
FROM clients AS c
INNER JOIN client_selections AS cs
    ON cs.client_id = c.id
INNER JOIN client_contacts AS cc
    ON cc.client_id = c.id
WHERE
    cs.selection_id = 41307
    AND cc.contact_type = 'phone'
ORDER BY c.name;
```

---

## 12. Все e-mail клиентов конкретной выборки

```sql
SELECT
    c.name,
    c.inn,
    cc.value AS email
FROM clients AS c
INNER JOIN client_selections AS cs
    ON cs.client_id = c.id
INNER JOIN client_contacts AS cc
    ON cc.client_id = c.id
WHERE
    cs.selection_id = 41307
    AND cc.contact_type = 'email'
ORDER BY c.name;
```

---

## 13. Клиенты выборки, у которых найден хотя бы один телефон

```sql
SELECT DISTINCT
    c.id,
    c.name,
    c.inn,
    c.spp_uuid
FROM clients AS c
INNER JOIN client_selections AS cs
    ON cs.client_id = c.id
INNER JOIN client_contacts AS cc
    ON cc.client_id = c.id
WHERE
    cs.selection_id = 41307
    AND cc.contact_type = 'phone'
ORDER BY c.name;
```

---

## 14. Клиенты выборки без телефонов

```sql
SELECT
    c.id,
    c.name,
    c.inn,
    c.spp_uuid
FROM clients AS c
INNER JOIN client_selections AS cs
    ON cs.client_id = c.id
WHERE
    cs.selection_id = 41307
    AND NOT EXISTS (
        SELECT 1
        FROM client_contacts AS cc
        WHERE
            cc.client_id = c.id
            AND cc.contact_type = 'phone'
    )
ORDER BY c.name;
```

---

## 15. Клиенты выборки без e-mail

```sql
SELECT
    c.id,
    c.name,
    c.inn,
    c.spp_uuid
FROM clients AS c
INNER JOIN client_selections AS cs
    ON cs.client_id = c.id
WHERE
    cs.selection_id = 41307
    AND NOT EXISTS (
        SELECT 1
        FROM client_contacts AS cc
        WHERE
            cc.client_id = c.id
            AND cc.contact_type = 'email'
    )
ORDER BY c.name;
```

---

## 16. Сколько телефонов и e-mail найдено по выборке

```sql
SELECT
    cc.contact_type,
    COUNT(*) AS contacts_count
FROM clients AS c
INNER JOIN client_selections AS cs
    ON cs.client_id = c.id
INNER JOIN client_contacts AS cc
    ON cc.client_id = c.id
WHERE cs.selection_id = 41307
GROUP BY cc.contact_type
ORDER BY cc.contact_type;
```

---

## 17. Сводка по всем выборкам

Показывает количество клиентов, сколько уже обогащено и сколько осталось.

```sql
SELECT
    cs.selection_id,
    COUNT(*) AS total_clients,
    SUM(
        CASE
            WHEN c.enriched = 1 THEN 1
            ELSE 0
        END
    ) AS enriched_clients,
    SUM(
        CASE
            WHEN c.enriched = 0 THEN 1
            ELSE 0
        END
    ) AS unenriched_clients
FROM client_selections AS cs
INNER JOIN clients AS c
    ON c.id = cs.client_id
GROUP BY cs.selection_id
ORDER BY cs.selection_id;
```

---

## 18. Найти клиентов, которые входят сразу в несколько выборок

```sql
SELECT
    c.id,
    c.name,
    c.inn,
    c.spp_uuid,
    COUNT(*) AS selections_count
FROM clients AS c
INNER JOIN client_selections AS cs
    ON cs.client_id = c.id
GROUP BY
    c.id,
    c.name,
    c.inn,
    c.spp_uuid
HAVING COUNT(*) > 1
ORDER BY selections_count DESC, c.name;
```

---

## 19. Посмотреть, в каких выборках находится конкретный клиент

По ИНН:

```sql
SELECT
    c.name,
    c.inn,
    cs.selection_id
FROM clients AS c
INNER JOIN client_selections AS cs
    ON cs.client_id = c.id
WHERE c.inn = '2543082240'
ORDER BY cs.selection_id;
```

---

## 20. Сколько клиентов одновременно находятся в 41307 и 42420

```sql
SELECT COUNT(*) AS common_clients
FROM client_selections AS a
INNER JOIN client_selections AS b
    ON b.client_id = a.client_id
WHERE
    a.selection_id = 41307
    AND b.selection_id = 42420;
```

---

## 21. Список общих клиентов двух выборок

```sql
SELECT
    c.id,
    c.name,
    c.inn,
    c.spp_uuid,
    c.enriched
FROM clients AS c
INNER JOIN client_selections AS a
    ON a.client_id = c.id
INNER JOIN client_selections AS b
    ON b.client_id = c.id
WHERE
    a.selection_id = 41307
    AND b.selection_id = 42420
ORDER BY c.name;
```

---

## 22. Клиенты, которые есть в 41307, но отсутствуют в 42420

```sql
SELECT
    c.id,
    c.name,
    c.inn,
    c.spp_uuid
FROM clients AS c
INNER JOIN client_selections AS cs
    ON cs.client_id = c.id
WHERE
    cs.selection_id = 41307
    AND NOT EXISTS (
        SELECT 1
        FROM client_selections AS other_selection
        WHERE
            other_selection.client_id = c.id
            AND other_selection.selection_id = 42420
    )
ORDER BY c.name;
```

---

## 23. Клиенты без ИНН

```sql
SELECT
    id,
    name,
    spp_uuid,
    enriched
FROM clients
WHERE
    inn IS NULL
    OR TRIM(inn) = ''
ORDER BY name;
```

---

## 24. Обогащённые клиенты без руководителя

```sql
SELECT
    id,
    name,
    inn,
    spp_uuid
FROM clients
WHERE
    enriched = 1
    AND (
        director_last_name IS NULL
        OR TRIM(director_last_name) = ''
    )
ORDER BY name;
```

---

## 25. Обогащённые клиенты без каких-либо контактов

```sql
SELECT
    c.id,
    c.name,
    c.inn,
    c.spp_uuid
FROM clients AS c
WHERE
    c.enriched = 1
    AND NOT EXISTS (
        SELECT 1
        FROM client_contacts AS cc
        WHERE cc.client_id = c.id
    )
ORDER BY c.name;
```

---

## 26. Последние обновлённые клиенты

```sql
SELECT
    id,
    name,
    inn,
    enriched,
    updated_at
FROM clients
ORDER BY updated_at DESC
LIMIT 50;
```

---

## 27. Полная выгрузка выборки: клиент + руководитель + контакты

Один клиент может иметь несколько контактов, поэтому для него будет несколько строк.

```sql
SELECT
    c.id,
    c.name,
    c.inn,
    c.kpp,
    c.ogrn,
    c.spp_uuid,
    c.director_last_name,
    c.director_first_name,
    c.director_middle_name,
    c.director_inn,
    c.director_position,
    cc.contact_type,
    cc.value AS contact,
    c.enriched
FROM clients AS c
INNER JOIN client_selections AS cs
    ON cs.client_id = c.id
LEFT JOIN client_contacts AS cc
    ON cc.client_id = c.id
WHERE cs.selection_id = 41307
ORDER BY c.name, cc.contact_type, cc.value;
```

---

## 28. Быстрая контрольная сводка по выборке

Один из самых полезных запросов после запуска сборщика.

```sql
SELECT
    COUNT(*) AS total,
    SUM(CASE WHEN c.enriched = 1 THEN 1 ELSE 0 END) AS enriched,
    SUM(CASE WHEN c.enriched = 0 THEN 1 ELSE 0 END) AS waiting,
    SUM(
        CASE
            WHEN EXISTS (
                SELECT 1
                FROM client_contacts AS cc
                WHERE
                    cc.client_id = c.id
                    AND cc.contact_type = 'phone'
            )
            THEN 1
            ELSE 0
        END
    ) AS clients_with_phone,
    SUM(
        CASE
            WHEN EXISTS (
                SELECT 1
                FROM client_contacts AS cc
                WHERE
                    cc.client_id = c.id
                    AND cc.contact_type = 'email'
            )
            THEN 1
            ELSE 0
        END
    ) AS clients_with_email
FROM clients AS c
INNER JOIN client_selections AS cs
    ON cs.client_id = c.id
WHERE cs.selection_id = 41307;
```

Для другой выборки достаточно заменить `41307` на нужный `selection_id`.
