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

- Core V2 має 14 slash-команд: попередні 12 та kick/modhistory.
  Kick має fresh REST authorization, role/owner recheck, committed deterministic
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

Перевірені результати завершеної ітерації, які не треба реалізовувати повторно:

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

Найближчий функціональний етап — ban/unban зі спільним service/repository
та тим самим audit lifecycle. Kick/modhistory не реалізовуй повторно. Потім
окремою ітерацією додай clear із підтвердженням небезпечного видалення.

1. Перевір moderation.py, discord_moderation.py, case reservation/history у db.py
   та існуючі тести; розширюй ці межі поступово для майбутньої вебпанелі.
   Старі warn/timeout/untimeout ще мають cached checks і старий HTTP-failed
   lifecycle — це окремий борг, новий kick не робить їх перевіреними.
2. Для кожної дії перевір actor/bot permissions, role hierarchy, guild_id,
   причину, owner/self/bot targets та фактичний результат Discord API. Для ban
   не видаляй історію повідомлень за замовчуванням; для unban не вимагай
   поточного членства користувача й явно обробляй відсутній ban.
3. Committed claim перед API й зіставлення payload блокують повтор interaction.
   API exception/timeout/cancel можуть означати невідомий результат навіть за404;
   SDK має власні transport retries. Не обіцяй exactly-once і не додавай сліпий retry.
   DB failure/CAS0 після API success не видавай за невдалу дію. Відсутність
   учасника не доводить kick: потрібна явна звірка Audit Log за case prefix.
4. Для масового clear потрібне обмежене в часі підтвердження саме автора дії,
   прив'язане до guild/channel; повторний click не виконує дію вдруге.
5. Перевір інтеграційні й негативні сценарії: API 403/404/timeout, зміна ролей,
   видалений учасник/канал, guild isolation, DB failure і повторний interaction.
   Discord API в unit-тестах не запускай; live перевірку звітуй окремо.
6. Заверши один сценарій з тестами, конфігурацією та українською документацією,
   перш ніж додавати наступний. Не видаляй старі функції без перевіреної заміни.

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
