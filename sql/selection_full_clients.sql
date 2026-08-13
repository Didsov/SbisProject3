SELECT
    c.name AS "Название организации",
    c.inn AS "ИНН",
    c.kpp AS "КПП",

    TRIM(
        COALESCE(c.director_last_name, '') || ' ' ||
        COALESCE(c.director_first_name, '') || ' ' ||
        COALESCE(c.director_middle_name, '')
    ) AS "ФИО директора",

    c.director_inn AS "ИНН директора",
    c.director_position AS "Должность директора",

    (
        SELECT GROUP_CONCAT(cc.value, ', ')
        FROM client_contacts AS cc
        WHERE
            cc.client_id = c.id
            AND cc.contact_type = 'email'
    ) AS "Почта",

    (
        SELECT GROUP_CONCAT(cc.value, ', ')
        FROM client_contacts AS cc
        WHERE
            cc.client_id = c.id
            AND cc.contact_type = 'phone'
    ) AS "Телефон"

FROM clients AS c

INNER JOIN client_selections AS cs
    ON cs.client_id = c.id

WHERE cs.selection_id = :selection_id

ORDER BY
    c.name;