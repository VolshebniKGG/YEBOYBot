# Промт наступної ітерації YEBOYBot

Продовжуй реальну розробку YEBOYBot: архітектура, Python/Discord, SRE, БД,
безпека, QA та український UX. Репозиторій VolshebniKGG/YEBOYBot,
гілка feature/core-v2; основна ОС Windows 10/11, майбутній production Linux.
Основний користувацький вхід — YEBOYBot.cmd.

Спершу звір поточний GitHub/local HEAD, незбережені зміни, PR та CI. Прочитай
AGENTS.md, якщо є, README, CORE_V2_STATUS_UA, MIGRATIONS_UA,
PROJECT_AUDIT_UA, WINDOWS_CMD_LAUNCHER_UA, LEGACY_LAUNCHER_UA,
POSTGRES_RECOVERY_UA, COMPETITIVE_SCENARIOS_UA та актуальний код. Не покладайся
на історичні «наступні кроки». Не реалізовуй повторно завершені функції.

Відомий стан після попередньої ітерації, який треба повторно перевірити:
- Schema2, впорядкована SQLite-ревізія 002_case_updated_at, read-only план,
  explicit [21] з backup/transaction/rollback/report та захистом stale config.
- v2_cases.updated backfill із created, а нові переходи записують актуальний час.
  Pending reconciliation це ще не реалізує.
- Нова SQLite/PostgreSQL БД — v2; наявна v1 не мігрує під час login.
  PostgreSQL v1 тепер має явний native workflow [23] для стандартної public схеми;
  під час login upgrade все ще блокується. Не повторюй реалізацію native maintenance.
- Heartbeat failure закриває клієнт/задачі, без recovery loop; зовнішнього
  супервізора kill/segfault/аварій ОС ще немає.
- Є 12 slash-команд, guild-scoped XP, warnings/timeout, durable reminders,
  Windows bootstrap/process/config/backup/update/diagnostics/autostart code.
- Automod/welcome/autorole/voiceXP/panels — не активні готові модулі.
- Live Discord, ручний Windows logon, production 24/7 та останній Windows CI
  не вважай перевіреними без нового підтвердження.

Legacy P0 виконано в коді: [22]/run.bat → stdlib controller → .venv-legacy;
без registry/global uninstall/cache purge/break-system-packages. run.py має
pre-import guard, OS locks, exact requirements/імпорти, .pth/py-cord rejection,
append/redacted logs. Music opt-in, старі cogs збережені. Не повторюй цю роботу.
Core V2 .venv-v2 не змішуй з legacy. Перший/repeat install і actual cogs offline
smoke пройшли локально Linux, live Discord/music/ручний Windows не підтверджені.
На 8b1897a Ubuntu SQLite/PG/legacy CI успішні; Windows legacy install виявив
штатний CPython executable sys.path і cp1252 output errors, для них є виправлення
й regressions. Повний Windows pytest скасовано; logs404 не дають конкретного тесту
або стека. Наступний CI зберігає pytest.log/JUnit artifacts без скорочення suite.
На a6c8e49 legacy Windows CI підтвердив45 passed, first/repeat install і real cogs
без login; Ubuntu43 passed/2 Windows skipped. Logs/JUnit artifacts існують.
Це automated Windows Server2025, не ручний Windows10/11 doubleclick/live Discord.
Повні SQLite/PG/Windows jobs run40269481755 перевір за актуальним статусом;
не приписуй targeted45tests повному Windows suite. Ручні критерії лишаються окремо.
Старі nocheckcertificate=True й E:-hardcoded FFmpeg лишилися відомим ризиком.
Не перенось їх у Core V2. Історичний root requirements не є launcher requirements.

PostgreSQL P0 реалізовано: [23] native Core dump/SHA/TOC → locks/transaction1→2
→ pg_restore тільки в окрему порожню БД. Джерело/поточна БД не перезаписується,
паролі/URL не передаються вargv/report. Unknown COMMIT/cancel/report-aftercommit
мають явну діагностику. Sourcecustom dependencies відхиляються; backup scope —
шість public Coreтаблиць, не весь сервер. PGupdater/SQLiteimport/nativeWindows/TLS
не вважай завершеними. Локально347tests: PG342/5skip, безPG309/38skip;
76нових. ПеревірCIактуальногоSHA, не приписуйстарийуспіхновомукоду.

Найближчий завершуваний етап: модерація й audit lifecycle.
Далі переходь до одного завершеного P1-модуля модерації: kick/ban/unban/clear/
modhistory. Виділяй спільну бізнес-логіку поступово; перевіряй actor/bot permissions,
role hierarchy, guild_id, причини, Discord API failures і lifecycle справ.
Масові дії потребують підтвердження. Для pending після crash спершу встановлюй
результат/неоднозначність, не повторюй небезпечні Discord-дії навмання.
Потім — /setup/help, event audit, automod із intents/винятками/false positives,
стійкі role panels, welcome/autorole, voice XP/rewards та scheduled announcements.
Legacy JSON без guild_id імпортуй лише через dry-run і явне зіставлення сервера.

Окремо підтримуй hygiene та експлуатацію: збереження локальних даних при
припиненні tracking pyc/logs/queues, реальний dependency CVE audit, доречні
format/type checks, systemd, metrics p50/p95/p99, resource baseline й soak tests.
Не додавай Redis/Celery/Kubernetes без виміряної потреби.

ProBot — функціональний орієнтир. Створи публічно підтверджену матрицю сценаріїв
з джерелами/датами, недоступні характеристики позначай «Невідомо».
Український UX, self-hosting, контроль даних і recovery оцінюй вимірюваннями,
а не маркетинговими заявами. Не копіюй код, дизайн або брендинг.

Працюй завершеними ітераціями: перевірка → реалізація → негативні/інтеграційні
тести → виправлення → документація → коміт/GitHub → фактичний звіт.
Звичайні зміни, обґрунтовані залежності, тести, CI, коміти й draft PR дозволені.
Не видаляй дані, не розкривай секрети, не перезаписуй чужі зміни й не зливай у main.
Руйнівні незворотні дії потребують перевіреного відновлення й підтвердження.
CI logs BlobNotFound не доводять дефект коду; отримуй конкретний тест/стек,
не прибирай перевірки для зеленого статусу й не вигадуй причину затримки.

Звіт українською: зроблене, файли, SHA/PR/CI, точні результати й обмеження,
запуск через CMD, наступний крок. Розділяй код/локальні тести/CI/Windows/
live Discord/manual criteria. Не обіцяй exactly-once Discord send або перевагу
над ProBot без доказів. Почни реалізацію найближчого P0-етапу, не зупиняйся
на плані, якщо інструменти дозволяють виконати роботу.
