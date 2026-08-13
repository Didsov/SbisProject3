SELECT
    cs.selection_id AS "Выборка",

    COUNT(DISTINCT c.id) AS "Количество клиентов",

    COUNT(
        DISTINCT CASE
            WHEN c.enriched = 1
            THEN c.id
        END
    ) AS "Обогащенных",

    COUNT(
        DISTINCT CASE
            WHEN c.enriched = 0
            THEN c.id
        END
    ) AS "Необогащенных"

FROM client_selections AS cs

INNER JOIN clients AS c
    ON c.id = cs.client_id

GROUP BY
    cs.selection_id

ORDER BY
    cs.selection_id;