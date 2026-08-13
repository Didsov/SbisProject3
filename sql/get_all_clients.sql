SELECT
    c.name AS "Название организации",
    c.inn AS "ИНН",
    c.kpp AS "КПП",
    email.value AS "Почта",
    phone.value AS "Телефон"
FROM clients AS c

LEFT JOIN client_selections AS cs
    ON cs.client_id = c.id

LEFT JOIN client_contacts AS email
    ON email.client_id = c.id
    AND email.contact_type = 'email'

LEFT JOIN client_contacts AS phone
    ON phone.client_id = c.id
    AND phone.contact_type = 'phone'

WHERE cs.client_id IS NOT NULL

GROUP BY
    c.id,
    c.name,
    c.inn,
    c.kpp,
    email.value,
    phone.value

ORDER BY
    c.name;