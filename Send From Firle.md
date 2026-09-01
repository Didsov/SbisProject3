Да. Для текущей схемы я бы оформил полный цикл так.

1. На локальном ПК готовишь файл `etrn.txt`: один ИНН на строку.

Пример:

```text
2502065647
2502040579
2536320038
2502070453
7725806898
```

Проверка количества строк в PowerShell:

```powershell
(Get-Content .\etrn.txt | Where-Object { $_.Trim() }).Count
```

2. Загружаешь файл на сервер:

```powershell
scp .\etrn.txt root@mail.projectsbis.ru:/tmp/etrn.txt
```

3. На сервере переходишь в проект:

```bash
cd /opt/projectsbis/repository
source .venv/bin/activate
```

Если файл мог быть без переноса строки в конце, можно сразу нормализовать:

```bash
sed -i -e '$a\' /tmp/etrn.txt
```

Проверить количество ИНН:

```bash
wc -l /tmp/etrn.txt
```

4. Перед боевой подготовкой убедись, что тестовый recipient override выключен.

Проверь:

```bash
python -m src.mailing.etrn config
```

Для боевой рассылки ожидаемо должно быть примерно:

```text
ETRN test recipient mode: DISABLED
Batch limit: 500
```

Если test mode ещё включён в `/etc/projectsbis/projectsbis.env`, его надо выключить:

```bash
sed -i 's/^ETRN_TEST_RECIPIENTS_ENABLED=.*/ETRN_TEST_RECIPIENTS_ENABLED=false/' /etc/projectsbis/projectsbis.env
```

И установить боевой batch:

```bash
sed -i 's/^ETRN_BATCH_LIMIT=.*/ETRN_BATCH_LIMIT=500/' /etc/projectsbis/projectsbis.env
```

Затем обновить текущую shell:

```bash
unset ETRN_TEST_RECIPIENTS_ENABLED
unset ETRN_BATCH_LIMIT

set -a
source /etc/projectsbis/projectsbis.env
set +a

python -m src.mailing.etrn config
```

5. Подготавливаешь аудиторию:

```bash
python -m src.mailing.etrn prepare --inn-file /tmp/etrn.txt
```

На этом этапе письмо никому не отправляется.

`prepare` делает:

```text
ИНН
↓
поиск клиента в локальной БД
↓
если клиента нет → поиск в СБИС по точному ИНН
↓
сохранение клиента
↓
поиск/обогащение контактов
↓
сбор email
↓
проверка дублей ETRN / bounce / invalid
↓
создание pending recipient
```

В конце смотри прежде всего:

```text
ИНН во входном файле
Найдено клиентов
Без email
Уникальных email
Уже получали/дубли ЭТрН
Invalid/bounced
К отправке
```

6. Перед отправкой можно проверить размер очереди:

```bash
sqlite3 /var/lib/projectsbis/project.db "
SELECT status, COUNT(*)
FROM mail_recipients
WHERE campaign_id = (
    SELECT id
    FROM mail_campaigns
    WHERE campaign_family = 'etrn'
    LIMIT 1
)
GROUP BY status;
"
```

Например:

```text
pending|1387
sent|420
```

7. Запускаешь один batch:

```bash
python -m src.mailing.etrn send --confirm-real-send
```

При `ETRN_BATCH_LIMIT=500` команда возьмёт максимум первые 500 `pending`.

Например:

```text
batch=7
run_id=12
size=500

[1/500] ...
...
[500/500] ...

ETRN_BATCH_COMPLETE
recipients=500
sent=...
failed=...
deferred=...
```

## Если в списке больше 500

Ничего делить вручную не надо.

Допустим после `prepare` получилось:

```text
К отправке: 1387
```

Первый запуск:

```bash
python -m src.mailing.etrn send --confirm-real-send
```

возьмёт:

```text
500
```

Останется:

```text
887 pending
```

После предусмотренного cooldown запускаешь команду ещё раз:

```bash
python -m src.mailing.etrn send --confirm-real-send
```

Второй batch:

```text
500
```

Останется:

```text
387
```

Третий запуск возьмёт оставшиеся:

```text
387
```

Итого для 1387 писем:

```text
Batch 1 → 500
Batch 2 → 500
Batch 3 → 387
```

Каждый batch будет отдельным `mail_run` и отдельно появится в `/admin/runs`.

Важно: у кампании есть `next_send_at` и cooldown примерно 40–50 минут. Поэтому после успешного batch не надо сразу запускать следующий. Worker должен отказать/не отправлять до истечения cooldown. Это специально сделано для ограничения темпа отправки.

Можно проверить состояние кампании:

```bash
sqlite3 /var/lib/projectsbis/project.db "
SELECT
    id,
    name,
    status,
    next_send_at
FROM mail_campaigns
WHERE campaign_family = 'etrn';
"
```

И количество оставшихся:

```bash
sqlite3 /var/lib/projectsbis/project.db "
SELECT COUNT(*)
FROM mail_recipients
WHERE campaign_id = (
    SELECT id FROM mail_campaigns
    WHERE campaign_family = 'etrn'
    LIMIT 1
)
AND status = 'pending';
"
```

## Если сервер/процесс упал между batch

Ничего заново `prepare` делать не нужно.

Запускаешь снова:

```bash
python -m src.mailing.etrn send --confirm-real-send
```

Уже отправленные `sent` не возьмутся. Следующий batch берёт только:

```sql
status = 'pending'
```

плюс mature `deferred`, если для них уже пришло время повтора.

## Если хочешь добавить ещё один файл позже

Можно подготовить новый `etrn2.txt`:

```bash
python -m src.mailing.etrn prepare --inn-file /tmp/etrn2.txt
```

Старым адресам повторное ETRN-письмо не должно ставиться в очередь из-за dedup `campaign_family=etrn`.

Новые адреса добавятся в ту же кампанию и будут отправляться следующими batch.

## Практический боевой цикл

Фактически каждый раз у тебя будет вот такой набор команд:

```bash
cd /opt/projectsbis/repository
source .venv/bin/activate

sed -i -e '$a\' /tmp/etrn.txt
wc -l /tmp/etrn.txt

python -m src.mailing.etrn config

python -m src.mailing.etrn prepare --inn-file /tmp/etrn.txt

python -m src.mailing.etrn send --confirm-real-send
```

Далее через 40–50 минут:

```bash
python -m src.mailing.etrn send --confirm-real-send
```

И повторять, пока:

```bash
sqlite3 /var/lib/projectsbis/project.db "
SELECT COUNT(*)
FROM mail_recipients
WHERE campaign_id = (
    SELECT id FROM mail_campaigns
    WHERE campaign_family='etrn'
    LIMIT 1
)
AND status='pending';
"
```

не покажет:

```text
0
```

Я бы перед первой уже настоящей массовой отправкой ещё один раз отдельно проверил, что `ETRN_TEST_RECIPIENTS_ENABLED=false`, потому что это сейчас самый критичный переключатель между тестом и реальными адресами.
