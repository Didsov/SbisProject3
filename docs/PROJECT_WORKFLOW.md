# ProjectSbis — инструкция по текущей версии

## 1. Назначение

Текущая версия проекта умеет:

- получать клиентов из пользовательской выборки СБИС через `CRMClients.ListClientsOnline`;
- проходить пагинацию выборки;
- сохранять клиентов в SQLite;
- связывать одного клиента с одной или несколькими выборками через `client_selections`;
- определять клиентов, которым ещё не выполнялся `ContractorCard.Read`;
- получать подробную карточку клиента;
- извлекать реквизиты организации, руководителя, телефоны и e-mail;
- сохранять обогащённые данные в БД;
- выдерживать паузу между запросами `ContractorCard.Read`;
- при HTTP 429 ждать снятия блокировки и повторять тот же запрос;
- запускаться из PowerShell или через конфигурации VS Code.

Основной модуль текущего сценария:

```text
src/client_loader.py
```

Старый `src/main.py` больше не является основной точкой запуска этого функционала.

## 2. Подготовка окружения

Открыть PowerShell в корне проекта:

```powershell
cd D:\GIT\ProjectSbis
```

Активировать виртуальное окружение:

```powershell
.\.venv\Scripts\Activate.ps1
```

Если зависимости ещё не установлены:

```powershell
pip install -r requirements.txt
```

## 3. Настройки `.env`

Секретные параметры хранятся только локально в `.env`.

Например:

```env
SBIS_BROWSER_COOKIE=...
```

Файл `.env` не должен попадать в Git.

Номер выборки СБИС в `.env` не хранится. Он передаётся при запуске через `--selection`.

## 4. Основные режимы запуска

### Только получить состав выборки и сохранить клиентов в БД

```powershell
python -m src.client_loader --selection 41307
```

### Проверочный запуск на 10 карточках

```powershell
python -m src.client_loader --selection 41307 --enrich-limit 10
```

### Полностью собрать выборку

```powershell
python -m src.client_loader --selection 41307 --enrich-all
```

## 5. Последовательный сбор выборок 41307 и 42420

```powershell
python -m src.client_loader --selection 41307 --enrich-all

if ($LASTEXITCODE -eq 0) {
    python -m src.client_loader --selection 42420 --enrich-all
}
```

В одну строку:

```powershell
python -m src.client_loader --selection 41307 --enrich-all; if ($LASTEXITCODE -eq 0) { python -m src.client_loader --selection 42420 --enrich-all }
```

Вторая выборка запускается только если первая завершилась без аварийного завершения Python-процесса.

## 6. Лимит ContractorCard.Read

У СБИС есть ограничение примерно 20 вызовов `ContractorCard.Read` в минуту.

Проект использует паузу:

```text
3 секунды
```

между успешными запросами.

Если сервер возвращает `HTTP 429`, `get_contractor_card()` ждёт примерно 65 секунд и повторяет запрос того же клиента.

## 7. Структура данных

### `clients`

Общая таблица клиентов. `SppUuid` уникален.

Флаг `enriched` показывает, была ли подробная карточка клиента успешно получена и сохранена.

### `client_selections`

Связь клиентов с пользовательскими выборками СБИС. Один клиент может находиться сразу в нескольких выборках.

### `client_contacts`

Контакты клиента: телефоны и e-mail.

## 8. Что извлекается из ContractorCard.Read

Основные данные:

- название;
- ИНН;
- КПП;
- ОГРН;
- `SppUuid`.

Руководитель:

- фамилия;
- имя;
- отчество;
- ИНН;
- должность.

Контакты:

- телефоны;
- e-mail.

Если карточка является подразделением и присутствует `head_data`, данные руководителя и персонализированные контакты берутся из головной организации, а реквизиты самой организации остаются реквизитами текущей карточки.

## 9. Запуск через VS Code

Конфигурация находится в:

```text
.vscode/launch.json
```

Режимы:

```text
SBIS: загрузить выборку
SBIS: выборка + 10 карточек
SBIS: выборка + все карточки
```

Запуск через `Ctrl+Shift+D` или `F5`. VS Code запросит номер выборки.

## 10. Быстрая проверка синтаксиса

```powershell
python -m py_compile src\client_loader.py
python -m py_compile src\database.py
python -m py_compile src\sbis\contractor_card.py
python -m py_compile src\sbis\card_parser.py
```

Если команды ничего не вывели — синтаксис корректен.

## 11. Git

Посмотреть изменения:

```powershell
git status
```

Добавить изменения:

```powershell
git add .
```

Проверить состав коммита:

```powershell
git status
```

Создать коммит:

```powershell
git commit -m "Add selection-aware client loading and ContractorCard enrichment"
```

Отправить в GitHub:

```powershell
git push origin main
```

Полная последовательность:

```powershell
git status
git add .
git status
git commit -m "Add selection-aware client loading and ContractorCard enrichment"
git push origin main
```

## 12. Рекомендуемый порядок после изменения кода

Сначала короткая проверка:

```powershell
python -m src.client_loader --selection 41307 --enrich-limit 10
```

Если всё работает:

```powershell
python -m src.client_loader --selection 41307 --enrich-all
```

После окончания:

```powershell
python -m src.client_loader --selection 42420 --enrich-all
```

После стабильного запуска:

```powershell
git status
git add .
git commit -m "Update SBIS client collection workflow"
git push origin main
```

## 13. Текущие важные особенности

- `enriched` хранится глобально на клиенте, а не отдельно для каждой выборки.
- Если клиент входит в несколько выборок и уже был обогащён, повторный `ContractorCard.Read` не нужен.
- `client_selections` используется для фильтрации очереди по конкретной выборке.
- `ContractorCard.Read` выполняется последовательно, а не параллельно.
- Пагинация `CRMClients.ListClientsOnline` использует непрозрачный `nextPosition`.
- `nextPosition` нельзя самостоятельно разбирать или модифицировать.
- `Position` в запросе передаётся как вложенная SBIS-запись с полем `Cursor`.
