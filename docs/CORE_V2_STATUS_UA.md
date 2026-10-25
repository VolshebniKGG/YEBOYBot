# Стан Core v2

Оновлено: 2026-10-24. Робоча гілка: `feature/core-v2`.

## Факти

Середовище ChatGPT залишається offline (409 environment_offline). Повторний вхід з
телефона не відновив його. GitHub читання й запис доступні, тому розробку продовжено
через GitHub. Нове ядро відновлено в окремому пакеті `core_v2/`.

Попередні 33 локальні тести стосувалися втраченої локальної версії, а НЕ цього коду.
Їх не слід цитувати як результати поточної гілки. Старий main.py та README збережені.

## Реалізовано в цій ітерації

- Окремий запуск python -m core_v2, discord.py, AutoShardedBot, мінімальні intents.
- SQLite/PostgreSQL, атомарний XP з cooldown, дані ізольовані через guild_id.
- 12 slash-команд: статус, levels, rank, leaderboard, warn/warnings/unwarn,
  timeout/untimeout, remind/reminders/cancel-reminder.
- Перевірки ролей і дозволів модераторів, захист від масових mentions.
- Збережені нагадування, lease/retry, перевірка доступу автора перед доставкою.
- Обмежені кеші, ротація логів, приховування токена та URL БД, OS instance lock.
- Windows install/start, offline doctor, перевірене SQLite backup API.
- Автоматичні тести та workflow Windows/Ubuntu/PostgreSQL.

## Перевірки

Перевірено код коміту `cbe31a22a234cd0bec915b8f6398e6d3a29b84b8`.
GitHub Actions: https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40141653388

| Середовище | Результат |
|---|---|
| Ubuntu, Python 3.12, SQLite | 20 passed; Ruff і pip check успішні |
| Windows runner, Python 3.12, SQLite | 20 passed; Ruff і pip check успішні |
| Ubuntu, Python 3.12, PostgreSQL 17 | 20 passed |

Це 20 однакових тестів у трьох конфігураціях, не 60 різних тестів.
Є попередження audioop deprecation від discord.py на Python 3.12.
Перший CI знайшов Windows PermissionError при читанні заблокованого байта;
виправлено перевіркою розміру через fstat і закриттям handle при помилці.
Окремий тест перевіряє втрату членства у приватній гілці перед доставкою нагадування.

Підключення до живого Discord і навантажувальних тестів цього коду ще не було.
Draft PR: https://github.com/VolshebniKGG/YEBOYBot/pull/1
У main зміни не злиті; новий код та інструкції доступні в feature/core-v2.
Наступний коміт із цим звітом змінює лише документацію, перевірений Python-код той самий.

Відстежуваний файл `.cache` видалено з гілки: попередній аудит виявив у ньому
непорожній access_token. Історія Git не очищена; якщо credentials ще чинні,
їх потрібно відкликати у провайдера. Значення токена не використовувалося.

## Далі за пріоритетом

1. Пройти новий CI та ручний сценарій із docs/CORE_V2_QUICKSTART_UA.md.
2. Додати модерацію kick/ban, аудит подій, автомодерацію, welcome, авторолі.
3. Додати голосовий XP і стійкі рольові панелі.
4. Ізолювати та інтегрувати музичний модуль: per-guild queue, таймаути, FFmpeg.
5. Безпечний імпорт старих JSON із явним вибором сервера.
6. Retention завершених jobs/cases, індекси за результатами профілювання,
   версійовані міграції схеми, health/metrics, PostgreSQL load tests.
7. Розподілені shard workers та координація, тільки після вимірювань.
8. API конфігурації й вебпанель з Discord OAuth2 та перевіркою прав на сервері.

Сайт, музика та готовність до тисяч серверів поки НЕ реалізовані/не підтверджені.
Налаштування automod/welcome/autorole і таблиця role panels — резерв схеми, не активні функції.

## Як відновити роботу ChatGPT

Помилка environment_offline означає відсутність підключеного виконавчого середовища.
Права GitHub цього не виправляють. Якщо інтерфейс пропонує повторно підключити/створити
середовище, скористайтеся цим; інакше відкрийте нове завдання з репозиторієм та гілкою
feature/core-v2. Якщо помилка повторюється, передайте підтримці точний текст 409
environment_offline. Не потрібно скидати Discord token через цю помилку.

## Керований CMD запуск — 25.10.2026
Середовище виконання доступне. Використовується актуальний tools.launcher.
Додано [1]/[2]/[3] із OS lock, nonce, heartbeat і cooperative shutdown.
Деталі та обмеження: WINDOWS_CMD_LAUNCHER_UA.md.
Модерація з окремого коміту 9ce34c4 ще не інтегрована; не змішуйте результати її тестів.
Локально 23 тести пройшли; Windows smoke виконується тільки Windows CI.

## Перевірений результат CMD-керування
Код caf182283902b401d75d03565a4eaaf051b49cd5: Windows 24 тести успішно; Ubuntu і PostgreSQL — 23 успішно, Windows-only тест пропущений. Ruff/pip check успішні.
CI: https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40162166628
Windows smoke перевіряє CMD меню з кириличного шляху з пробілами, але не live Discord.
Повний launcher ще не готовий: Git update, restore через меню, автоматичний crash supervisor, повна телеметрія та live Discord сценарії залишаються наступними етапами.

## SQLite maintenance — 25.10.2026
CMD [7] враховує DATABASE_URL, [8] відновлює підтверджену копію після зупинки,
[10] перевіряє цілісність і схему v1. Перед відновленням створюється копія поточних даних.
PostgreSQL не обслуговується SQLite-меню. Докладно: WINDOWS_CMD_LAUNCHER_UA.md.
Локально: 28 passed, 1 skipped (Windows-only). Новий CI перевіряється окремо.

Перевірено ab394926c7f9e1513c6fa13ec8c480bfe78aa282: Windows 29 passed; Ubuntu SQLite і PostgreSQL — 28 passed, 1 Windows-only skipped. Ruff/pip check успішні. CI: https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40162579315
Новий етап не тестував живе Discord-підключення та не додає PostgreSQL backup/restore.
Наступний пріоритет: безпечне оновлення коду з GitHub через меню з перевіркою гілки, локальних змін, backup і міграцій.

## Оновлення через CMD — 25.10.2026
Додано [6]: Git fast-forward дозволеної гілки, перевірка локальних змін,
підтвердження, SQLite backup, pip check, перевірка БД/команд новим процесом,
повторний запуск тільки після успіху. ZIP та PostgreSQL автоматично не оновлюються.
Інструкції й обмеження: WINDOWS_CMD_LAUNCHER_UA.md.
Локально 35 passed, 1 Windows-only skipped; новий CI перевіряється окремо.


## Захист локальних файлів під час оновлення — 25.10.2026
Попередній updater (09f71e7) перевірено CI: Windows 36 passed; Ubuntu SQLite
та PostgreSQL — 35 passed, 1 Windows-only skipped.
https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40162972836
Додатково виправлено читання Git-шляхів: NUL-розділювачі зберігають кирилицю
та пробіли без Git quoting. Тести перевіряють відмову перезаписувати наявні
ігноровані файли, які оновлення додає до Git.
Для нового коду локально: 37 passed, 1 Windows-only skipped; Ruff успішний.
Код 3487321 перевірено CI: Windows — 38 passed; Ubuntu SQLite та PostgreSQL —
37 passed, 1 Windows-only skipped. Ruff/pip check успішні у SQLite jobs.
CI: https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40168851702
Живий Discord та повний цикл
мережевого оновлення з перезапуском реального бота не перевірені.
Наступний пріоритет: контрольоване відновлення процесу після аварії.


## Обмежене відновлення застосунку — 25.10.2026
Managed runtime повторює невдалий запуск до трьох разів із паузами 2/5/15 секунд.
Кожний попередній Bot виходить із async context перед наступною спробою;
OS lock утримується весь час. Навмисне завершення не повторюється, запит [3]
перериває backoff максимум за одну секунду. Неправильний токен, privileged intents
та ValueError конфігурації завершують запуск одразу. Traceback проходить через
редактор секретів у журналі. Меню показує стан відновлення за heartbeat.
Локально 42 passed, 1 Windows-only skipped; Ruff успішний. CI перевіряється окремо.
Це відновлення після Python exceptions, не супервізор аварій ОС/kill/segfault.
Зависання не спричиняє примусового завершення. Живе Discord-підключення не перевірене.
Наступний пріоритет: автоматизація початкової конфігурації та діагностика запуску.
