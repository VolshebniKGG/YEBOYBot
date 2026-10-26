# Поточний стан Core V2 — 26.10.2026

Гілка feature/core-v2; main не змінено, PR #1 — draft. Нижче актуальний стан,
попередні звіти збережено в розгортуваній історії. Не переносіть старі CI totals
на нові коміти.

## Поточна ітерація: PostgreSQL backup → upgrade → recovery

- **[23]** у YEBOYBot.cmd: read-only plan, native Core-backup, явна міграція1→2,
  restore власної копії тільки в окрему попередньо створену порожню БД.
- SQLite workflow/схема2 і legacy запуск збережено. Login не застосовує міграції.
- До DDL: валідація стандартної public Core-схеми, OS/advisory/SHARE table locks,
  native custom archive, SHA-256/manifest/TOC. Дані двох guild, pending cases,
  timestamps, XP, jobs, panels та прості btree-індекси зберігаються.
- Кастомні залежності (types/functions/defaults/FK/CHECK/partition/inheritance/
  RLS/collations/opclasses/access methods) відхиляються до зміни даних.
  Це backup шести Core-таблиць, не всього PostgreSQL сервера/ролей/файлів.
- Credentials передаються native tools через очищене child environment;
  немає password/URL в argv, raw stderr або JSON-звіті. .env/token не копіюються.
- Recovery не використовує clean/drop, не створює БД й не перезаписує поточну.
  Config URL не перемикається автоматично. Після COMMIT connection loss —
  unknown_commit, а не вигаданий rollback. Ctrl+C дочікується bounded worker.
- Звіт data/v2/postgres-report.json містить phase/status/backup/error_type.
  PostgreSQL Git-updater, SQLite→PG import і складні custom schemas не реалізовані.
- Додано окремий PostgreSQL maintenance CI job; повні Windows/Linux/PG checks
  збережені. Нових Python application dependencies немає; потрібні штатні native tools.
- [Інструкція](POSTGRES_RECOVERY_UA.md), [матриця з ProBot](COMPETITIVE_SCENARIOS_UA.md),
  [наступний промт](NEXT_DEVELOPMENT_PROMPT_UA.md).

Перевірено локально Linux/Python3.12.14: **347 tests**, із них **76 нових**.
- Без PostgreSQL: **309 passed, 38 skipped** (5 Windows +33 PG).
- З PostgreSQL17.11: **342 passed, 5 Windows-only skipped**.
- Реальні pg_dump/pg_restore17.11 запускалися в disposable Docker через stdio
  wrappers; SQLAlchemy/asyncpg — у Linux host до loopback TCP контейнера.
  Test fixture створює/видаляє лише власні нові yeboy_test_* БД.
- Перевірено full payload/index preservation, v1/v2 native restore, rollback після
  DDL failure, stale plan/config switch, unknown marker, locks, custom dependencies,
  backup/file/credentials failures, commit/report ambiguity й cancellation drain.
- Ruff, pip check та diff check успішні. pip check не є CVE-аудитом;
  CVE-аудит і performance baseline у цій ітерації не виконувались.
- CI нового коду перевіряється після публікації. До змін faf3385 мав Ubuntu full
  261/10, legacy Windows45 і Linux43/2 success; повні Windows/PG jobs не завершилися
  на момент огляду, їхній успіх новому коду не приписується.

Native PG workflow на Windows, production TLS, live Discord, ручний Windows10/11
та 24/7 залишаються неперевіреними. Порівняльних вимірювань ProBot немає.
Наступний функціональний етап: модерація з permissions/hierarchy/guild isolation,
підтвердженням clear та діагностикою pending без сліпого повтору Discord API.

## Попередня ітерація: безпечний legacy entrypoint (faf3385)

- run.bat замінено thin UTF-8/CRLF входом до stdlib Python controller.
  Видалені registry edits, global uninstall/cache purge/break-system-packages.
- [22] Core V2 відкриває те саме legacy-меню. Встановлення й виконання тільки
  в .venv-legacy; .venv-v2 і системні пакети не змінюються. Невідомі дані не видаляються.
- Окремі pinned requirements: discord.py[voice]2.7.1 відповідає legacy async add_cog;
  історичний py-cord root requirements більше не встановлюється цим входом.
- Pre-import guard перевіряє Python/venv, system/user site, сторонні .pth,
  py-cord, точні версії та critical imports; requirements stamp не приховує drift.
- OS locks захищають від дубля legacy й пакетних змін під час його роботи.
  Foreground Ctrl+C, зрозумілі помилки/exit codes; сторонні процеси не завершуються.
- Import bot більше не читає token/config і не очищує logs. Cogs завантажуються
  ліниво; Music тільки opt-in. Логи append, Credentials приховані й у traceback.
- Додано Windows/Linux legacy-install CI: first/repeat install, probe й real cogs
  offline smoke. Наявні SQLite/PG/Windows checks не прибиралися.
- [Інструкція](LEGACY_LAUNCHER_UA.md), [аудит](PROJECT_AUDIT_UA.md),
  [наступний P0-промт](NEXT_DEVELOPMENT_PROMPT_UA.md).

Локально Linux/Python 3.12.14: **261 passed, 10 skipped** із 271 tests:
5 native Windows (включно з двома новими CMD), 5 PostgreSQL. Додано 45 tests;
це 43 passed + 2 Windows skipped у цій ітерації, а не повторення старого звіту.
Ruff, pip check в обох окремих venv та diff check пройшли. CVE-аудит не виконувався.
Реальні legacy dependencies installed; repeat install і critical imports пройшли.
Disposable source copy завантажила справжні Moderation/User/Help/RankCog,
перевірила commands, optional-music skip і client/task cleanup без Discord login.
Без DLC assets використано fallback; rendering/реальні команди не перевірялися.
PostgreSQL suite локально в цій ітерації не запускали — змін БД немає.

CI коду **a6c8e49**, [run40269481755](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40269481755):
- **Windows Server 2025 / Python 3.12.10:** legacy first/repeat install, probe,
  real cogs offline smoke та **45 passed**. Обидва native CMD тести (missing Python,
  кирилиця/пробіли/cwd/exit code) пройшли. Ручний Windows 10/11 double-click не перевірявся.
- **Ubuntu:** legacy install/smoke та **43 passed, 2 Windows-only skipped**.
- Test logs/JUnit опубліковані як legacy-windows-latest і legacy-ubuntu-latest
  artifacts. Повні SQLite/PG/Windows jobs на момент фіксації ще виконувались;
  цей результат підтверджує саме legacy-ітерацію, не повний зелений workflow.

Попередній CI на **8b1897a**, run40231450946: Ubuntu SQLite **258 passed/10 skipped**,
PostgreSQL **263 passed/5 skipped**, legacy first/repeat install/probe/offline cogs —
success на Ubuntu. Windows legacy install відмовив на штатному executable-directory
у sys.path; обробка помилки додатково виявила cp1252 UnicodeEncodeError, бо -I
ігнорує PYTHONUTF8. Обидві причини виправлено: exact base-prefix дозволений поряд
зі stdlib, сторонні .pth досі відхиляються; stdin/stdout/stderr явно UTF-8.
Три subprocess regressions примусово використовують ASCII для menu/check/bot errors.
Ці нові результати не приписуються старому SHA; новий CI перевіряється окремо.

Повний Windows job121283871503 на 8b1897a скасовано під час pytest; dependencies,
first/repeat Core install, pip check та Ruff успішні. Логи Windows pytest повертають
404 BlobNotFound; конкретний тест/стек і причина затримки не встановлені.
Додано збереження unbuffered pytest.log/JUnit як 7-денні CI artifacts (always),
та окремі legacy regression tests у legacy job обох ОС. Повний suite не скорочено.
Ручний Windows 10/11, live Discord/music та 24/7 не підтверджені. Legacy
all-intents/TLS/hardcoded paths, рольова безпека команд і неізольовані legacy JSON
залишаються окремими ризиками.
Наступна P0-ітерація: native PostgreSQL backup → upgrade → restore зі збереженням
даних; наявна PostgreSQL v1 досі заблокована до цього workflow.

## Попередня ітерація: версійовані міграції (2fc94e8)

- Схема **2**, реальна впорядкована SQLite ревізія **002_case_updated_at**.
- [21] читає план; після МІГРУВАТИ та зупинки [3] робить перевірену копію,
  застосовує DDL/data/marker в транзакції й записує migration-report.json.
- Налаштування, XP, jobs, cases, role-panel дані й індекси зберігаються.
  updated для старих справ = created, не реконструйований історичний час.
- Backup прив'язаний до одного джерела; stale plan/config/file відхиляються.
  Помилки до commit відкочуються; помилка звіту після commit не приховує applied DB.
- Restore v1 створює страховий snapshot v2; повторна v2 migration — no-op.
- Нові БД SQLite/PostgreSQL — v2. Наявна v1 не оновлюється при запуску;
  DB preflight виконується до Discord login, MigrationRequired не повторюється.
- [4]/doctor показують потребу [21]; manifest format1 підтримує фактичну schema1/2.
- PostgreSQL v1 upgrade через меню **не реалізовано**: потрібен native pg_dump/
  verification/upgrade/recovery workflow. Це обмеження rollout для наявної PG v1.

Документи: [Міграції](MIGRATIONS_UA.md), [повний аудит](PROJECT_AUDIT_UA.md),
[наступний промт](NEXT_DEVELOPMENT_PROMPT_UA.md).

Локальна перевірка попередньої ітерації (Linux/Python 3.12):
- SQLite: **218 passed, 8 skipped** (3 Windows-only + 5 PostgreSQL-only).
- PostgreSQL 17.11 у disposable container: **223 passed, 3 Windows-only skipped**.
- Разом 226 сценаріїв, **43 нових** відносно попередніх 183. Це не сума jobs.
- Ruff, pip check, compileall і diff check успішні. CVE audit не виконувався;
  pip check підтверджує сумісність установлених залежностей, не відсутність CVE.
- Є повні real SQLite payload/index checks, backup+SHA, failed DDL rollback,
  restore1 зі safetycopy2, stale-plan/config race, failed report aftercommit,
  no-Discord-login for MigrationRequired. Користувацьку БД не мігрували.
- Source-pinning race знайдено під час review: `_create` тепер бере перевірений
  source, у restore safetybackup також прив'язаний до target; окремий regression
  змінює DATABASE_URL під час backup і підтверджує відсутність DDL в обох БД.
- Нових application dependencies немає. CI нового коміту перевіряється окремо;
  старі затримані jobs не є доказом успіху нового коду.
Live Discord, ручний Windows 10/11/logon і production 24/7 не підтверджені.
Порівняльних вимірювань з ProBot немає.

<details>
<summary>Історія попередніх ітерацій (результати прив'язані до зазначених SHA)</summary>

# Стан Core v2

Оновлено: 2026-10-25. Робоча гілка: `feature/core-v2`.

> Актуальний результат: bootstrap перевірено в CI Windows/Linux, див. останній розділ.
> Повний Control Center ще не завершено; матриця — LAUNCHER_AUDIT_UA.md.

## Історичні факти попередніх ітерацій

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


## Майстер конфігурації — 25.10.2026
[11] та [12] запускають майстер: приховане введення токена, ID тестового сервера,
перевірка введення, атомарний запис .env.v2 зі збереженням інших ключів і коментарів.
Enter зберігає наявний токен/ID. Без ID використовується SYNC_COMMANDS=none;
з ID — test. Global sync через майстер недоступний. Для розширених параметрів
редагуйте .env.v2; значення середовища мають пріоритет. Після змін потрібен [2].
Токен не перевіряється через Discord API. Не запускайте майстер з перенаправленим
stdin: якщо getpass не може приховати введення, операція відхиляється.
Локально 50 passed, 1 Windows-only skipped; Ruff успішний. CI перевіряється окремо.
Наступний пріоритет: bootstrap першого встановлення та діагностика відсутнього Python.


## Bootstrap першого встановлення — 25.10.2026

`YEBOYBot.cmd` шукає готове `.venv-v2`, перевіряє критичні імпорти, а за потреби
викликає `install_v2.bat`. Інсталятор шукає Python 3.12 через `py`, потім `python`.
Якщо Python відсутній, показує офіційну адресу й завершується з кодом 1;
системні компоненти автоматично не завантажує. Git не потрібен для ZIP-установки.
Bootstrap створює venv, встановлює requirements, виконує pip check та імпорти,
створює каталоги й `.env.v2`, зберігаючи наявну конфігурацію.
Повторний виклик не встановлює незмінені залежності; після збою можна повторити.
[5] відновлює залежності через той самий bootstrap. Працюючий бот або інший
інсталятор блокують зміну бібліотек через OS locks; спершу зупиніть бот через [3].
Наявний venv іншої версії Python автоматично не видаляється: після зупинки бота
перейменуйте `.venv-v2` і повторіть запуск. Дані та `.env.v2` розміщені поза venv.

Локально Linux/Python 3.12: 10 нових offline unittest пройшли; compileall успішний.
Повний pytest/Ruff локально не виконано: залежності недоступні через мережу
середовища. Windows first/repeat install додано до CI; результат слід перевіряти
для нового коміту. Реальний Windows-ПК і Discord API не перевірені.
Матриця та наступні кроки: [аудит](LAUNCHER_AUDIT_UA.md).

## Підтверджений CI bootstrap — 25.10.2026

Код `1b9b135014c4dab80083345d5e624a7d36e77cbf` перевірено:
[GitHub Actions](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40203553458).
Windows/Python 3.12 — 62 passed; Ubuntu SQLite і PostgreSQL — по 60 passed,
2 Windows-only skipped. Ruff і pip check успішні у Windows/Ubuntu SQLite jobs.
Windows first/repeat install, відсутній Python та CMD smoke у кириличному шляху
з пробілами пройшли. Перший CI виявив дві помилки тестових fixture
(короткі Windows-шляхи та quoting CMD); їх виправлено до успішного повтору.
Це runner Windows, не ручний тест Windows 10/11; live Discord не перевірено.
Цей запис змінює лише документацію, перевірений Python/Batch-код не змінено.

## Локальна діагностика та JSON-звіт — 25.10.2026

[4] перевіряє Python 3.12, установлені пакети та критичні імпорти, конфігурацію
і файлову SQLite. БД відкривається mode=ro: quick_check, версія v1 та потрібні
колонки; перевірка SQL має progress deadline 2 секунди й lock timeout 1 секунду.
Це не повний integrity_check, тест запису або міграція. Відсутня БД не створюється;
вона готується штатним запуском [1]. PostgreSQL отримує unknown, без підключення.
[18] додатково атомарно зберігає data/v2/diagnostics/latest.json, замінюючи попередній
звіт. Токен, URL БД, ID серверів, nonce, шляхи, журнали й тексти винятків
не включаються. Каталог data/ виключено з Git.
Runtime readiness вимагає свіжого heartbeat і стану «Готовий»; прострочений heartbeat
не підтверджує Discord ready. Чинність токена, мережа та Discord API не перевіряються.
Статуси JSON: ok/error/unknown; local_checks_passed=false також для unknown.
За невдалої локальної перевірки CLI повертає 1, але [18] зберігає доступний звіт.
Кожен виклик меню запускає новий Python-процес, тому перевірка читає актуальну .env.v2.
Локально пройшли 3 stdlib тести експорту/збереження попереднього звіту та відсутньої БД;
інші тести перевіряються в CI нового коміту через недоступні локальні залежності.

## Перевірений результат діагностики — 25.10.2026

Код `70ccbd757c318d4a71d5f80ddbb741239ba7a62a`: Windows — 75 passed;
Ubuntu SQLite та PostgreSQL — по 73 passed, 2 Windows-only skipped.
Ruff і pip check пройшли у Windows/Ubuntu SQLite jobs.
CI: https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40204847470
13 нових тестів покривають export, збереження попереднього звіту після збою,
відсутню/пошкоджену/несумісну SQLite, конфігурацію, залежності, heartbeat
та відсутність секретів у JSON. Локально виконано 3 stdlib тести, решта — у CI.
Реальний Discord API та ручний Windows 10/11 залишаються неперевіреними.
Наступний етап: checksum і метадані резервних копій та безпечна retention.
Цей коміт змінює лише звіт; перевірений код не змінено.

## Маніфести й кероване зберігання SQLite-копій — 25.10.2026

База ітерації: `ac5fbab`, `feature/core-v2`. Гілка GitHub звірена; draft PR #1
залишається відкритим. Старий yeboybot і його точки входу збережені.
Нові копії [7], перед відновленням [8] та оновленням [6] отримують `.db.json`
з SHA-256, розміром, UTC та версією схеми. Конфігурація/токен не додаються.
Знімки нормалізовані до journal_mode=DELETE, читаються як закриті immutable
копії; live DB так не відкривається. SQLite Backup API враховує committed WAL.
Restore перевіряє фактичну staged-копію до створення safety backup і заміни БД.
Старі копії без маніфесту потребують окремого підтвердження; неправильний маніфест
або checksum блокують restore. Файли-посилання й self-restore через hardlink відхиляються.
[20] показує метадані та пропонує keep-N із підтвердженим перенесенням старіших
перевірених пар в архів. Архів залишається доступним у [8], автоматичного
чи остаточного видалення немає. Місце на диску не звільняється. Legacy/invalid,
поточна БД й останні N перевірених копій не архівуються.

Реалізація: core_v2/backup.py, tools/launcher/backup_manifest.py, storage.py, меню.
32 нових тестових сценарії покривають manifests/restore/retention, збої запису,
повторну перевірку без WAL, symlink/hardlink, locks, скасування і live WAL writer.
Локально Linux/Python 3.12: **105 passed, 2 Windows-only skipped**; Ruff і pip check
успішні. Перший прогін знайшов дефект repeated-inspection WAL; його виправлено
до успішного повного повтору. CI нового коду перевіряється окремо.
SHA-256 не є підписом; довільний користувацький вміст БД не фільтрується.
PostgreSQL backup/restore, ручний Windows 10/11 і live Discord не перевірені/не реалізовані.
Наступний пріоритет лаунчера: opt-in Windows Task Scheduler і контрольоване
відновлення після аварій ОС; ядра — версійовані міграції й модерація.

### CI резервування підтверджено

Коміт `698457184c20567d44d7e33a7640de863a52ed6e`: Windows — **107 passed**;
Ubuntu SQLite і PostgreSQL — по **105 passed, 2 Windows-only skipped**.
Ruff/pip check успішні у Windows/Ubuntu SQLite jobs.
https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40205958270
Це 32 нові сценарії відносно попередньої ітерації, не сума тестів трьох jobs.
Повний Git-клон у робочій папці відновлено після відновлення мережевого доступу;
попередній текстовий snapshot збережено окремо. Ручний Windows/live Discord не перевірені.

## Добровільний автозапуск Windows — 25.10.2026

[15] керує завданням Планувальника для поточного користувача й папки інсталяції.
Створення/увімкнення потребує `УВІМКНУТИ АВТОЗАПУСК`; видалення —
`ВИМКНУТИ АВТОЗАПУСК`. Без підтвердження системні налаштування не змінюються.
Назва ізольована хешем root+SID. Перед увімкненням нові процеси перевіряють
конфігурацію та імпорти. Системні інструменти викликаються абсолютними шляхами,
без shell, stdin, з таймаутами та прихованим raw output.

XML задає logon поточного SID, InteractiveToken, LeastPrivilege, IgnoreNew,
WorkingDirectory та pythonw.exe -m core_v2.managed; паролі не зберігаються.
Створення не перезаписує наявні завдання. Перед зміною/видаленням перевіряються
description, principal, SID, єдиний trigger/action, шляхи й аргументи. Невдалий
Query залишається unknown, а не «вимкнено». Стан Планувальника не є Discord readiness.
Для pythonw файлове redacted логування працює без sys.stderr. Зупинка бота — [3],
видалення завдання саме по собі бот не зупиняє. Scheduler restart-on-failure не додано:
навмисна зупинка не спричиняє повторний запуск; попередній managed backoff збережено.

49 нових сценаріїв: consent/ownership/XML/SID/таймаути/помилки/інтерпретатори,
скасування, query-only Windows smoke та файлова редакція логів pythonw.
Локально Linux/Python 3.12: **153 passed, 3 Windows-only skipped**, Ruff/pip check
успішні. CI нового коміту перевіряється окремо. Тести не реєструють справжніх
завдань; створення, видалення і запуск після logon на реальному Windows 10/11
залишаються ручним критерієм. Живий Discord не перевірено.

Ручний сценарій: налаштувати [12] → перевірити [4] → [15]/[1] і підтвердження →
повторний вхід у Windows → відкрити меню, перевірити готовність і /status →
[3] (бот не повинен перезапуститися сам) → [15]/[2] і підтвердження →
наступний вхід не повинен запускати цю інсталяцію. Перед перенесенням папки
вимкніть її старий автозапуск. До виконання цього сценарію функція лишається Partial.
Наступне: супервізор аварій процесу й версійовані міграції перед розширенням модулів.

### Фактичний стан перевірок автозапуску

Код `60c9984207c58dd340d7752ef52557be9e31dc1b`: Ubuntu SQLite та PostgreSQL CI —
по **153 passed, 3 Windows-only skipped**. Windows first/repeat install, Ruff
і pip check пройшли; pytest залишився in_progress без доступного завершеного
журналу. Причину затримки не встановлено, Windows pytest не позначається успішним.
https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40206795971

Коміт `cc283467d94cc84c10ea1303d8a039819ba32e8b` додає в CI `-vv --durations=10`,
faulthandler_timeout=120 та exit-on-timeout, без зміни Python-коду бота.
Ідентична команда локально дала **153 passed, 3 skipped**; новий CI також ще
не повернув завершених результатів на момент звіту:
https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40207357017
Запити скасування попередніх затриманих запусків відправлені, але їх завершення
не підтверджено API. Журнали затриманих Windows jobs поки повертають BlobNotFound.

Наступні точні дії: перевірити зазначені runs і jobs 121209346037/121211039299;
за появи журналів визначити конкретний повільний тест/стек, виправити причину
та повторити тільки потрібні перевірки. Після Windows CI пройти ручний logon
сценарій із попереднього розділу. Автозапуск залишається Partial; успіх старого
Windows backup CI не переноситься на нього. У main нічого не зливалося.

## P0: безпечна відмова керування та несумісної БД — 25.10.2026

База: 8872ce9, feature/core-v2, draft PR #1. AGENTS.md відсутній; старий
yeboybot і користувацькі дані не змінювалися.

Реалізовано:
- managed.run очікує завершення client/control, перед виходом скасовує та
  приєднує обидві задачі. ControlFailure завершує клієнт без retry; LoginFailure
  зберігає тип і не повторюється. Штатний stop має пріоритет перед heartbeat.
- Atomic heartbeat зберігає попередній стан при невдалій публікації; cleanup
  не приховує первинну I/O помилку. Невдале завершення повертає 1.
- Schema guard виконується до WAL/DDL і повторно для pooled-з'єднання.
  Невідома/порожня/versionless/неповна v1 схема відхиляється без ремонту.
- SQLite bootstrap у BEGIN IMMEDIATE; помилка DDL відкочується. Конкурентний
  WAL setup має до 4 BUSY/LOCKED-only спроб з async backoff і повторним guard.
- PostgreSQL guard працює під advisory lock; інтеграційні тести створюють
  власну UUID-схему лише в одноразовій yeboy_test.

Тести: 27 нових сценаріїв (9 managed, 18 schema guard). Heartbeat regression
спершу впав із TimeoutError на попередньому коді; після виправлення проходить.
Є реальні filesystem/subprocess/OS-lock перевірки без входу в Discord,
збереження байтів несумісної SQLite, rollback DDL і конкурентна ініціалізація.
Реальний PostgreSQL-прогін знайшов різницю типу relkind bytes/text у тесті;
SQL-приведення виправлено до повторного повного успіху.

Локально Linux, Python 3.12:
- SQLite: 176 passed, 7 skipped (3 Windows-only + 4 PostgreSQL-only).
- PostgreSQL 17.11 у локальному контейнері: 180 passed, 3 Windows-only skipped.
- Ruff, pip check, compileall та diff check успішні. Нових залежностей немає.

CI на початку ітерації: 40207357017/40207361179 Windows jobs скасовані,
але logs досі BlobNotFound 404; поточний 40207735164 Windows job121212190702
показував in_progress понад 15 хв при timeout-minutes=15. Конкретного тесту
і стеку отримати неможливо; причину не встановлено. Перевірки не прибиралися.
Новий CI перевіряється окремо після публікації цього коду.

Обмеження: повного migration runner/переходів схем, зовнішнього OS crash
supervisor, ручного Windows 10/11 і live Discord перевірок ще немає.
Наступний завершуваний етап — versioned migration runner з backup/recovery
перед першою потрібною зміною схеми; паралельно отримати Windows CI logs.
Запуск Windows: YEBOYBot.cmd → [4] діагностика → [1]; помилка — [9] журнал;
[3] зупинка, [2] перезапуск. Номер версії БД вручну не редагувати.

</details>
