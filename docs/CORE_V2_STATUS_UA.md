# Поточний стан Core V2 — 27.10.2026

Гілка feature/core-v2; main не змінено, PR #1 — draft. Нижче актуальний стан,
попередні звіти збережено в розгортуваній історії. Не переносіть старі CI totals
на нові коміти.

## Поточна ітерація: підтверджене очищення повідомлень

База **4aa63d8** має обидва повні CI успішні (докази ban/unban нижче).
Додано `/clear amount reason` та `/clear-status request_id`: **18 slash-команд**.

- Preview читає одну сторінку до25 повідомлень перед початковим interaction ID.
  Відбираються лише звичайні/reply повідомлення молодші7діб; закріплені,
  системні й старі пропускаються з явними лічильниками. Пропущені не замінюються.
- Підтвердження приватне, прив'язане до автора/guild/channel, діє60секунд для
  прийняття claim. Кнопки confirm/cancel одноразові; серверний SQL CAS блокує
  повтор, гонку cancel/confirm і повтор після перезапуску, незалежно від UI.
- Незмінний version1 payload з ID/причиною зберігається в існуючому v2_jobs.body
  (≤1000символів), scope/expiry — у typed columns. Причина1–200символів без
  керівних символів. До5 живих підтверджень на guild/actor; cap має зрозумілу помилку.
  Нових таблиць/міграцій/dependencies/intents немає; schema2 та шість Core-таблиць
  збережені. Clear states clear_* ніколи не стають pending-нагадуваннями.
- Перед preview/claim і API заново перевіряються ролі/власник/actor/bot/channel
  overwrites: Manage Messages, View Channel, Read Message History. Підтримано
  лише regular TextChannel, без threads/news/forum. Перевіряються лише схвалені
  ID, повторна вибірка історії під час execute заборонена. Stale/pinned/old/404
  під час validate зупиняє весь запит до write. Атомарного pin/permission+delete
  Discord не дає; зміна після останньої перевірки все ще можлива.
- Для одного ID reason передається direct pinned HTTP delete_message: SDK2.7.1
  інакше губить reason у single-ID delete_messages. Для2–25 використано SDKbulk.
  Є audit prefix clear/actor/channel, encoded header≤512, повна причина в БД.
- SDK exception/timeout/cancel після claim лишає unknown/running/pending без
  автоматичного повтору. SDKsuccess+DBfailure/CAS0 явно розрізняються.
  Підтвердження стосується запиту дляNID, не незалежного підрахунку фактичних
  нових видалень. /clear-status читає тільки власний receipt у тому самому
  каналі й не потребує права на нове видалення; після restart кнопки не
  відновлюються, job не продовжується. Expired підтвердження не приймаються.
- Clear receipts — окремий manual-job audit; /modhistory лишається історією
  member moderation cases. Скасування не відкочує вже надіслану API-операцію.
- Оновлено «Код структура.txt», README, [CLEAR_UA.md](CLEAR_UA.md) та інструкції
  Core V2. Нові тести включено у focused Windows/Linux/PG jobs; старі перевірки збережено.

Під час незалежного перегляду виправлено expiry race: час оновлюється ПІСЛЯ
отримання SQL lock/row lock, тож підтвердження, яке чекало блокування, не
приймається після deadline. Додано6 регресій (claim/cancel/prepare × SQLite/PG)
із реальними locks та синхронізацією подіями без sleep; усі6 перевірено на обохБД.

Новий набір містить **1074 тести, 353 доданих**: 82 plan, 101 repository,
43 service, 58 adapter, 59 UI та 10 integration. Локально Linux/Python3.12.14
після expiry fix: SQLite/default **977 passed, 97 skipped** за100.00s,
JUnit:1074tests/0failures/0errors.
Окремо відтворено й виправлено негативний PostgreSQL-тест oversize-body:
SQLAlchemy/asyncpg повертає DBAPIError зі SQLSTATE22001, а не DataError.
Тест вимагає саме22001 і перевіряє незмінність початкового рядка після rollback;
цільовий повтор —1passed. Перший локальний повний PostgreSQL/native17.11 прогін
зі старим очікуванням класу винятку мав1068passed/1failed/5skipped за648.51s.
Його не позначаємо зеленим і не видаємо за остаточну перевірку виправленого тесту.
Повний повтор виправленого набору підтверджено в новому CI нижче.
Окремий фінальний локальний набір усіх шести clear test-файлів із реальними
SQLite/PostgreSQL після виправлення — **353passed,0skipped** за90.68s;
JUnit353tests/0failures/0errors. Native17.11 dump/restore сценарії початкового
повного прогону пройшли; його єдина помилка була очікуванням driver exception.
Ruff, pip check та diff check успішні. Посилання документації та UTF-8 карту
коду перевірено; відсутніх відносних посилань немає.
CVE-аудит27.10.2026 pip-audit2.10.1/PyPI:24distributions,0findings,0skipped,pip26.2;
Linux Core/runtime/dev/installer, не legacy/OS/native/Windows/tool resolution.
Реальне видалення в Discord, ручний Windows10/11 та24/7 не виконувались.

### Підтверджений CI коду 8bb380f

[Push40510725655](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40510725655)
завершився за102s, [PR40510734573](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40510734573)
за124s. Обидва exactHEAD8bb380fe788a2a27fe1623910c085c25506ca774 мають
**усі9jobs success**, зокрема виправлений oversize-body тест.

| Job | Passed | Skipped |
|---|---:|---:|
| Повний Windows | 982 | 92 PostgreSQL |
| Повний Ubuntu | 977 | 97 PostgreSQL/Windows |
| Повний PostgreSQL | 1069 | 5 Windows |
| Focused Windows | 720 | 59 PostgreSQL |
| Focused Ubuntu | 717 | 62 PostgreSQL/Windows |
| PostgreSQL maintenance | 785 | 0 |
| Legacy Windows | 51 | 0 |
| Legacy Ubuntu | 49 | 2 Windows |
| Dependency audit | success | 0 distributions |

Windows logs підтверджують перше й повторне install_v2.bat; native
pg_dump/pg_restore17.11. PR advisory JSON artifact12737156079 розібрано:
24distributions/0findings/0skips/pip26.2 у зазначеному вище Linux scope.
Log/JUnit artifacts (push/PR): Windows12737241236/12736768277,
Ubuntu12736219875/12737705352, PG12736274950/12736129898,
PGmaintenance12736124838/12737131720, audit12737415845/12737156079.
Це автоматичні Windows CI докази, не ручний Windows/logon або live Discord.

## Попередня ітерація: ban/unban та актуальна карта коду (4aa63d8)

Обидва повні CI **4aa63d8** успішні:
[push40375663900](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40375663900),127s;
[PR40375699446](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40375699446),125s.
У кожному всі9jobs success: Windows661/60, Ubuntu656/65, PostgreSQL716/5,
focused Windows399/27, Ubuntu396/30, PGmaintenance432, legacy Windows51,
Ubuntu49/2. Nativeclients17.11. Audit artifact12673599319:24distributions,
0findings/0skips/pip26.2. Ці докази стосуються саме ban/unban+карти, не clear.


База — **083d4bc**, чиста feature/core-v2; PR1 draft, main не змінено.
Перед роботою підтверджено [успішний CI083d4bc](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40371041490).
Це доказ базового стану, не результати нових змін.

- Core V2 реєструє **16 slash-команд**. Додано `/ban user_id reason` та
  `/unban user_id reason`; потрібен Ban Members у автора й бота.
- `ModerationService` спільний для kick/ban/unban/history; попередні Python
  імена KickService/KickResult збережені як aliases для сумісності.
- Свіжі REST owner/actor/bot/roles перевіряються до claim і повторно після commit.
  Ban поточного учасника перевіряє ієрархію автора/бота; non-member допускається
  лише при exact404/10007 для цілі. Помилки actor/bot/guild не маскуються.
- Ban явно передає `delete_message_seconds=0`: повідомлення зберігаються.
  Unban не потребує поточного членства, читає бан за ID; лише404/10026 означає
  відсутній запис ДО write, що блокує unban. Інші read failures теж не пишуть.
- Atomic claim звіряє guild/interaction/action/actor/target/reason. Конкурентні
  однойменні й cross-action запити мають одного власника; інші не переписують
  справу й не викликають повторну API-дію. Після API exception/timeout/cancel —
  pending; API success+DB error/CAS0 не видається за невдалу Discord-дію.
- Немає application retry або автоматичного replay pending. SDK retries і
  race між role/ban read та write лишаються межами Discord API. Applied означає
  підтверджений запит, не доказ першого ban чи exactly-once. Історія приватна.
- Нових application dependencies, intents, DDL/міграцій немає; schema2 збережено.
  Claim додатково відхиляє NUL і невідомі/non-string action до SQL.
- «Код структура.txt» замість старого E:\legacy дерева описує реальні Core V2,
  launcher, legacy, flows, SQL/schema, локальні приватні файли, тести й docs.
  Перевірено76 канонічних шляхів; дані/секрети/згенеровані pyc не включено в дерево.
- [Модерація](MODERATION_UA.md), [аудит](PROJECT_AUDIT_UA.md),
  [наступний промт](NEXT_DEVELOPMENT_PROMPT_UA.md); README має посилання на карту.

Локально Linux/Python3.12.14: **721 tests,224 нові** (83 service,38 adapter,
49 slash binding,15 cross-layer integration,39 repository regressions).
- PostgreSQL17.11/native: **716 passed,5 Windows skipped**,210.82s.
- Остаточний SQLite/default: **656 passed,65 skipped**
  (60 PostgreSQL/5 Windows),62.87s.
- Попередній default до15 cross-layer tests:641 passed,65 skipped,117.94s;
  усі нові15 та рештаmoderation tests268passed перевірені окремо. Це не підміна
  остаточного full run, який фіксується окремо.
- Ruff, pip check, diff check та посилання українських docs пройшли.
- Повторний pip-audit2.10.1/PyPI:24distributions Core/runtime/dev/installer,
  **0 findings,0 skipped,pip26.2**, exit0. Це не аудит legacy/OS/native/Windows.
- Нові test files включено в обидва focused Windows/Linux та PostgreSQL
  maintenance invocations, усі full jobs/checks/log artifacts збережено.
- Новий CI після публікації перевіряється за новим SHA; базові результати
  083d4bc не приписуються змінам ban/unban.
Live Discord, ручний Windows10/11/logon та24/7 не виконувались. Clear і перенесення
старих warn/timeout/untimeout lifecycle залишаються наступними етапами.

## Попередня ітерація: kick, приватна історія та захист встановлювача

**Повний CI коду c86c177 успішний:** [push run40370321990](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40370321990),
усі9 jobs,90s загалом. [PR run40370330021](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40370330021)
також усі9 jobs success,130s. Тести не видалено, дані/межі/timeouts збережені.

| Перевірка | Підтверджений результат |
|---|---|
| Windows full SQLite | **454 passed,43 PostgreSQL skipped**,40.41s |
| Ubuntu full SQLite | **449 passed,48 skipped** (43 PostgreSQL/5 Windows),10.56s |
| Ubuntu full PostgreSQL17 | **492 passed,5 Windows skipped**,29.78s |
| Focused Windows moderation/installer | **192 passed,10 PostgreSQL skipped** |
| Focused Ubuntu moderation/installer | **189 passed,13 skipped** |
| PostgreSQL maintenance + moderation | **208 passed** |
| Legacy Windows / Ubuntu | **51 passed /49 passed,2 skipped**, installs/offline cogs success |
| Dependency audit | **24 distributions,0 findings,0 skipped,pip26.2**, Linux resolution |

Ruff і artifact upload пройшли. Full jobs мають retained pytest logs/JUnit.
Push artifacts: SQLiteWindows12672152245, SQLiteUbuntu12672261743,
PostgreSQL12671563716, audit12672037335. PR: SQLiteWindows12671263997,
SQLiteUbuntu12672166959, PostgreSQL12672791734.
Windows runner — сервер GitHub Actions; ручний double-click/logon на Windows10/11,
справжній Discord API та 24/7 лишаються неперевіреними.

Після коротких parameter IDs max test-log line: Windows387, PostgreSQL385,
Ubuntu386 символів замість524410. У Ubuntu перехід malformed→oversize XML→
наступний тест має інтервали2.4/2.6ms замість сотень секунд навколо гігантського
рядка. За незмінних payloads/assertions workflow завершився за90s. Ці докази
підтверджують виправлення надмірного test-log output; не приписуємо йому всі
історичні Windows cancellations без їхніх журналів.

Коміти цієї ітерації: **1ef3ebd** moderation, **b446028** installer/CVE,
**f5685f0** Windows path assertion, **c86c177** bounded test IDs/CI evidence.
Наступний docs-only коміт фіксує ці результати, production-код не змінює.
Нижче збережено проміжні фактичні перевірки й знайдені/виправлені failures.

- Зареєстровано **14 slash-команд**, нові **/kick** і **/modhistory**.
- KickService відокремлює use-case від Discord adapter і guild-scoped SQL.
  Fresh detached Guild REST snapshot перевіряє owner, actor/bot permissions,
  timeout, ієрархію з tie за role ID та невідомі role IDs; спільний кеш не змінюється.
- Перед DELETE атомарно commit-иться case claim. Ключ із guild/interaction ID
  блокує повторний application kick, зокрема після restart. Параметри запиту
  мають збігатися; чужий guild або інший payload справу не перезаписує.
- Права/ціль повторно перевіряються після claim. API exception/timeout/cancel
  залишає результат непідтвердженим; DB failure/CAS0 після API success не видає
  дію за невдалу. Автоматичного replay pending немає. SDK transport retries
  збережені, exactly-once DELETE і атомарна role-check+API не обіцяються.
- /modhistory має current permission check, user ID колишнього учасника,
  state filter, стабільний порядок і сторінки по5; відповіді ephemeral, mentions
  вимкнені, причини екрановано й обмежено з урахуванням UTF-16/emoji.
  Старий failed показується як запис помилки, а не доказ скасованої дії.
- Схема2 й міграції не змінювались, нових залежностей/intents немає.
  Audit-header має case/actor prefix і bounded URL-encoded причину; повний текст у БД.
- Додано focused moderation/installer Windows/Linux CI, SQL integration в PG job
  та окремий Linux CVE audit з JSON artifact; усі full jobs/log/JUnit збережено.
- [Інструкція](MODERATION_UA.md), [аудит](PROJECT_AUDIT_UA.md),
  [наступний промт](NEXT_DEVELOPMENT_PROMPT_UA.md).

Локально Linux/Python3.12.14: **497 tests**, **150 нових**
(132 moderation +18 installer regressions).
- PostgreSQL17.11 + native tools: **492 passed, 5 Windows-only skipped**, 274.24s.
- SQLite/default без PostgreSQL: **449 passed, 48 skipped**
  (5 Windows +43 PostgreSQL), 72.89s; повтор без конкурентних heavy tasks.
- Є real SQLite/PostgreSQL atomic claim/guild/history checks і real discord.py
  models з mocked HTTP. Конкурентні duplicate claims мають одного власника;
  перевірені role/owner changes, permissions, ambiguous API404/5xx/network,
  cancellation, DB failures, CAS0, Unicode limits та private response binding.
- Ruff, pip check, diff check пройшли. Performance baseline не вимірювався.
- CVE-аудит `pip-audit2.10.1`/PyPI: 24 встановлені distributions Core/dev/installer,
  без пропусків. Для pip25.0.1 повернуто6 унікальних advisory IDs (12 raw записів);
  після pip26.2 — 0 findings, exit0. Інші23 не мали відомих advisories.
  Legacy/OS/native/Windows resolution цим аудитом не охоплені.
  [Точний набір, IDs та методика](DEPENDENCY_AUDIT_UA.md).
- Обидва bootstrap перевіряють власний ізольований venv до pip, прибирають
  package redirects, зберігають proxy/CA, оновлюють pip26.2 до app dependencies
  навіть при чинному marker. Failure блокує install і скидає marker; системний
  Python не змінюється. Старе Core-меню доступне для зупинки[3]/repair[5].
- Реальні fresh/repeat Core і legacy installs у різних venv у Linux-шляху з
  кирилицею/пробілами пройшли, обидва pip26.2. Discord login не виконувався.
- Один initial default run:448 passed/48 skipped/1 subprocess timeout15s
  у test_process_exits_on_control_failure_and_releases_lock під паралельним
  install+PG навантаженням. Три діагностичні запуски завершились за3.03–7.44s;
  faulthandler показав імпорти SQLAlchemy/pytest, close<3ms. Увесь managed suite
  без змін:10 passed/5.38s. Таймаут15s і всі тести збережено; це не доказ
  причини історичного Windows CI. Повний default повторено без heavy-конкуренції.
- На базовому fafa177 full Ubuntu309/38, PostgreSQL342/5, PG maintenance76 та
  legacy обох ОС підтверджені; Windows full скасовано під час pytest, logs404
  і відсутній artifact не дозволяють назвати конкретний тест/стек. Новий CI
  перевіряється окремо за SHA; старі результати новому коду не приписуються.


CI коду **b446028**, [PR run40368711425](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40368711425):
- Focused Ubuntu: **189 passed, 13 skipped**; PostgreSQL maintenance: **208 passed**.
- Legacy first/repeat/install/import/cogs smoke: Windows **51 passed**, Ubuntu
  **49 passed, 2 skipped**. Справжній Discord login не виконувався.
- Dependency-audit: artifact12671432239 містить **24 distributions,0 findings,
  0 skipped,pip26.2** для Linux Core/runtime/dev/installer resolution.
- Focused Windows: **191 passed,10 skipped,1 failed** у test_bootstrap.py:127.
  Єдиний failure — порівняння C:\Users\RUNNER~1 та C:\Users\runneradmin для
  тієї самої temp-папки. Production install й moderation перевірки пройшли.
  Тест виправлено через Path.resolve() зі збереженням цільового venv і pip flags;
  тести не видалено, Windows результат після виправлення перевіряється окремо.
- Повні jobs на момент цієї фіксації ще виконуються; ці focused results не є
  підтвердженням повного зеленого workflow.


Повні Linux jobs b446028 також завершились успішно за журналами обох подій:
SQLite **449 passed,48 skipped** (pytest11.04–11.43s), PostgreSQL **492 passed,
5 skipped** (pytest31.10–31.40s). PR jobs121733311160 та121733311057.
Вивід має рядок524410символів із oversized XML nodeid; timestamps навколо нього
мають розриви216–258s, тоді як сам pytest звітує секунди. Це виміряний кандидат
затримки console/log processing, а не підтверджений runtime deadlock.
Windows full на17:36UTC ще не завершений. Зміна коротких IDs перевіряється
новим CI; не називаємо причину всіх історичних скасувань доведеною.

Після Windows path fix, код **f5685f06**, [PR run40369126215](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40369126215)
і [push run40369117626](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40369117626):

| Перевірка | Підтверджено в обох запусках |
|---|---|
| Focused Windows moderation/installer | **192 passed,10 skipped** |
| Focused Ubuntu moderation/installer | **189 passed,13 skipped** |
| PostgreSQL maintenance + moderation SQL | **208 passed** |
| Legacy Windows | **51 passed**, first/repeat/import/offline cogs success |
| Legacy Ubuntu | **49 passed,2 skipped**, first/repeat/import/offline cogs success |
| Linux dependency audit | **24 distributions,0 findings,0 skipped,pip26.2** |

PR artifacts: moderation-windows12670663745, moderation-ubuntu12670679739,
postgres-maintenance12671751971, dependency-audit12672290753. Ruff успішний.
Це GitHub Windows runner, не ручний Windows10/11 або живий Discord.
Повні SQLite Windows/Linux і PG jobs на17:34UTC ще виконуються; їхній успіх
не приписується focused jobs. Старі runs не скасовувалися. Конкретного тесту/стека
їх затримки з доступних журналів поки не отримано; діагностика залишається P0.

Точний CI shell pipeline з -vv/tee/JUnit локально завершився: **449 passed,
48 skipped за43.82s**, без зависання завершення. Виявлено окрему виміряну
проблему журналу: oversize XML у параметричному ID створював рядок524381символ,
повний log600529bytes. Задані короткі IDs для XML і SID CSV payloads; тестові
512KiB/16KiB дані й усі assertions збережені. Targeted autostart: **47 passed,
1 Windows skipped**. Collect усіх497 tests після зміни не має nodeid>1024символів.
Це виправлення читабельності логів, не доведене усунення CI затримки.
Production-код після b446028 не змінювався; наступні зміни — test path/IDs
і фіксація фактичних доказів. CI останнього follow-up потрібно звіряти за SHA.

Live Discord kick/permissions/command visibility, ручний Windows10/11 та 24/7
не перевірено. Ban/unban/clear, автоматичне pending reconciliation та виправлення
старих warn/timeout/untimeout lifecycle залишаються наступними етапами.
Переваги над ProBot без порівняльних вимірювань не заявляються.

## Попередня ітерація: PostgreSQL backup → upgrade → recovery (fafa177)

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
- CI a5dd3ef зупинився до PG-тестів: Ubuntu apt не мав postgresql-client-17.
  CI-підготовку виправлено: tools/ci_pg_clients.py використовує реальні native17
  binaries із вже запущеного disposable PG service container через stdio, без
  установки системних пакетів runner. Тести й version checks не послаблено.
- CI виправленого коду перевіряється після публікації. До змін faf3385 мав Ubuntu full
  261/10, legacy Windows45 і Linux43/2 success; повні Windows/PG jobs не завершилися
  на момент огляду, їхній успіх новому коду не приписується.

CI коду **f14273a**, [run40273263180](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40273263180):
**postgres-maintenance — 76 passed**, native pg_dump/pg_restore17.11; JUnit/log
опубліковані в artifact postgres-maintenance. Legacy Windows45 і Ubuntu43/2
також success. Повні SQLite Windows/Linux і PG suites на момент фіксації ще
виконуються; успіх focused76 не видається за повний зелений workflow.

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
