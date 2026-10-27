# Промт наступної ітерації YEBOYBot

Продовжуй реальну розробку VolshebniKGG/YEBOYBot у feature/core-v2 як
відповідальний головний розробник: архітектура, Python/Discord, SRE, БД, безпека,
QA та український UX. Основна ОС користувача — Windows 10/11, майбутній production
— Linux. Основний вхід — YEBOYBot.cmd. ProBot — функціональний орієнтир;
не копіюй код, дизайн або брендинг і не оголошуй переваг без вимірювань.

Спершу звір GitHub/local HEAD, локальні зміни, PR та CI. Прочитай AGENTS.md,
якщо існує, README, CORE_V2_STATUS_UA, PROJECT_AUDIT_UA, MIGRATIONS_UA,
WINDOWS_CMD_LAUNCHER_UA, LEGACY_LAUNCHER_UA, POSTGRES_RECOVERY_UA, MODERATION_UA,
COMPETITIVE_SCENARIOS_UA та DEPENDENCY_AUDIT_UA. Перевір код; історичні результати не
приписуй новому SHA. Онови матрицю Working/Partial/Disconnected/Broken/
Duplicate/Missing/Untested. Не реалізовуй повторно завершені можливості.

Відомий стан, який потрібно повторно перевірити:

- Core V2 має 18 slash-команд: попередні16 та clear/clear-status.
  Kick/ban/unban мають спільний ModerationService, fresh REST authorization,
  role/owner recheck, committed deterministic
  case claim, duplicate blocking і ambiguity-safe outcome. History читає тільки
  поточний guild, уміє pending/state/user ID/page й надсилає приватні відповіді.
- Є guild-scoped XP, cases, durable reminders з lease/retry; доставка нагадувань
  at-least-once. Exactly-once для Discord send не обіцяється.
- Schema2 і SQLite 1→2 через [21] мають verified backup, транзакцію, rollback,
  stale-source guard і report. Cases.updated заповнюється created для старих
  записів; нові переходи пишуть фактичний час. Є pending diagnostics через
  /modhistory; автоматичного reconciliation/replay немає.
- [23] додає PostgreSQL Core-backup/SHA/TOC, явний upgrade 1→2 та pg_restore
  лише в окрему попередньо створену порожню БД. Підтримано стандартні шість
  public Core-таблиць; custom dependencies відхиляються. Це не backup сервера.
  Поточна БД не перезаписується, credentials не передаються в argv/report.
  Unknown COMMIT, cancellation і report failure мають явну діагностику.
- Login не мігрує наявну v1 автоматично. PostgreSQL Git-updater, SQLite→PG import,
  native PG на Windows, production TLS і довільні custom schemas не завершені.
- [22]/run.bat використовують окрему .venv-legacy без registry/global pip змін.
  Є pre-import guard, OS locks, pinned imports, .pth/py-cord rejection та
  append/redacted logs. Music opt-in; старі TLS/FFmpeg обмеження не перенось у Core.
- Windows bootstrap/process/config/backup/update/diagnostics/autostart code існує.
  Heartbeat failure закриває клієнт і задачі; зовнішнього OS-crash supervisor немає.
- Automod/welcome/autorole/voice XP/role panels не є завершеними активними модулями.

Базові результати попередньої ітерації083d4bc (історія, не новий HEAD):

- 497 tests,150 нових (132 moderation/18 installer). Локально Linux: SQLite
  449 passed/48 skipped, PostgreSQL17.11/native492 passed/5 Windows skipped.
- Повний CI коду **c86c177**: push40370321990 та PR40370330021, обидва усі9jobs
  success за90/130s. Windows full454 passed/43 PostgreSQL skipped, Ubuntu full
  449/48, PostgreSQL full492/5, focused Windows192/10, focused Ubuntu189/13,
  PGmaintenance208, legacy Windows51, legacy Ubuntu49/2.
- Виправлено Windows8.3alias assertion через resolved target, без послаблення
  перевірки власного venv. Короткі parameter IDs XML/SID CSV зберегли oversized
  payloads/assertions/timeouts та зменшили max test-log line524410→387символів.
  Після цього обидва full CI успішні; історичні cancelled runs не є поточним станом.
- Linux Core/runtime/dev/installer CVE-аудит:24 distributions,0 skipped.
  Pip25.0.1 мав6 unique advisory IDs; після pip26.2 zero findings. Core/legacy
  installers перевіряють власний ізольований venv до pip та enforce26.2 перед
  dependencies, навіть при valid marker; global Python не змінюють. Не дублюй fix.
  Наявне Core menu не auto-upgrade-ить pip: зупинка[3]→dependencies[5].
  Audit-tool/legacy/Windows/native/OS resolution цим аудитом не охоплені;
  потрібні routine advisory refresh і окреме розширення охоплення.
- Реальні SQLite/PG перевіряють atomic claims/history, discord.py models — mocked
  HTTP. Немає нових application dependencies/intents/DDL. Ручний Windows10/11
  double-click/logon, live Discord, production24/7/performance baseline не перевірені.

Follow-up docs коміт може мати інший HEAD, але не додає production функцій.
Звір actual HEAD, локальні зміни, PR і нові CI runs перед редагуванням; не перенось
старі totals на нові зміни без перевірки. За відсутності регресії переходь до
функціональної ітерації, не повторюй уже завершені P0 tasks.

Нова ban/unban ітерація розширила shared ModerationService без DDL/dependencies:
ban зберігає повідомлення (delete_message_seconds=0), приймає numeric ID
поточного/відсутнього учасника; unban не вимагає членства й перевіряє ban entry.
Тільки exact404code10007 дозволяє відсутність цілі, exact404code10026 означає
відсутній ban ДО надсилання unban. Інші preflight failures не надсилають дію;
після write exception/timeout/cancel pending зберігається без сліпого retry.
Atomic claim перевіряє action/guild/target/actor/reason; cross-action replay
не перезаписує справу. Невідомий DB result після API success не є failed API.
Оновлено «Код структура.txt»: реальна карта Core/launcher/legacy, flows/data/docs.
Нова ітерація:721 tests,224 доданих. Локально Linux default656 passed/65 skipped,
PostgreSQL17.11/native716 passed/5 Windows skipped; Ruff/pipcheck/diffcheck pass.
Повторний Linux Core/dev/pip CVE-аудит24distributions:zero findings/0skips.
Фактичні CI результати цієї ітерації звір у CORE_V2_STATUS_UA.md за SHA;
старі497-test totals вище не є підтвердженням нового HEAD.

Clear вже реалізовано: regularTextChannel, одна сторінка до25 повідомлень перед
interaction, default/reply/recent7days; pinned/system/old пропускаються без
переобрання. Private60s author/guild/channel confirmation; immutable IDs і reason
1..200 у version1 payload existing v2_jobs.body≤1000, typed clear_* states,
понад5liveconfirmations відхиляються. Reminder scheduler не бере clear jobs.
Serverclaim повторно перевіряє expiry ПІСЛЯ очікування SQL lock, свіжі channel
permissions/overwrites та схвалені messages перевіряються до API. Discord не
дає atomic pin/permissions+delete. Receipt status тільки власнику в тому самому
каналі; /modhistory — member cases, clear-status — manual-job audit. Старіviews
після restart не відновлюються, unknown не повторюється. APIackNID не є
незалежним підрахунком нових видалень. Docs: CLEAR_UA.md і «Код структура.txt».
Фактичні нові totals/CI звір у CORE_V2_STATUS_UA.md; не перенось721-test baseline
на наступні зміни. Цю реалізацію не дублюй, доки немає підтвердженої регресії.
Clear додає353 тести; загалом1074. Фінальний SQLite/default977passed/97skipped
після expiry-fix перевірено локально; PostgreSQL повний результат і новіCI
звір у поточному статусі за SHA. Oversize-body негативний PG-тест перевіряє
DBAPIError із точним SQLSTATE22001 та збереження рядка, не загальний Exception.

Наступний пріоритет — перенести старі /warn, /timeout, /untimeout на спільні
свіжі перевірки та безпечний lifecycle. Kick/ban/unban/clear/status не переписуй
повторно. Тримай ітерацію обмеженою одним завершеним сценарієм.

1. Встанови фактичні permission/hierarchy/owner/admin/timeout правила Discord.
   Не використовуй cached member/guild role data як остаточну авторизацію.
2. Для timeout зберігай явний початковий payload із duration/expiry, причиною,
   actor/target/guild/interaction. Changed duration при duplicate не має
   повторно або інакше карати людину. Обґрунтуй зберігання метаданих; міграцію
   додавай лише з compatibility guard, verified backup/rollback і preservation tests.
3. Claim має commit-итись перед API. SDKerrors/timeouts/cancellation можуть бути
   неоднозначні; pending/unknown не видавай за failed. DBfailure/CAS0 після API
   success не є failed API. Не додавай blind replay чи exactly-once обіцянок.
4. Перевір SQL/guild isolation, permissions/role changes, API403/404/5xx, timeout,
   cancellation, duplicate/differentpayload, DBbefore/afterfailures, restart.
   Реальний Discord у unit-тестах не запускай; ручний/API доказ — окремо.
5. Підтримуй Windows/Linux/PostgreSQL full/focused CI, retained logs/JUnit і CVE
   audit. Parameter IDs не містять великих payloads; перевірки не прибирай.
6. Онови українські інструкції, карту коду та статус, зроби логічний коміт у
   feature/core-v2, перевір CI, збережи draft PR без merge уmain й надай звіт.

Далі: доступні Discord event logs з урахуванням intents/API, /setup і /help,
welcome/autorole, persistent role panels, automod з винятками/false positives,
voice XP/rewards та scheduled announcements. Без Message Content Intent власні
content-фільтри не показуй активними. Legacy JSON без guild_id потребують dry-run
і явного зіставлення серверів, а не автоматичного імпорту.

Підтримуй окремі експлуатаційні задачі: повторення CVE-аудиту й охоплення
legacy/Cross-OS/native залежностей (pip check ним не є), безпечне припинення tracking pyc/logs/queues зі збереженням локальних даних,
systemd, resource baseline p50/p95/p99, event-loop lag, RAM/CPU та soak/failure tests.
Не додавай Redis/Celery/Kubernetes або розподілений шардинг без виміряної потреби.
Порівнюй UX/setup/recovery з ProBot лише за однаковими відтворюваними сценаріями;
внутрішні недоступні метрики ProBot позначай «Невідомо».

Працюй завершеною ітерацією: перевірка → реалізація → негативні/інтеграційні
тести → виправлення → документація → коміт/GitHub → український фактичний звіт.
Звичайні зміни, обґрунтовані залежності, тести, CI, коміти й draft PR дозволені.
Не видаляй дані, не розкривай секрети, не перезаписуй чужі зміни й не зливай у main.
Руйнівні незворотні дії потребують перевіреного відновлення й підтвердження.
У звіті наведи зроблене, файли, SHA/PR/CI, точні тести, запуск через CMD,
обмеження й наступний крок. Розрізняй локальні, CI, Windows, live та ручні перевірки.
