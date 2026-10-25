# Промт наступної ітерації YEBOYBot

Продовжуй реальну розробку YEBOYBot: архітектура, Python/Discord, SRE, БД,
безпека, QA та український UX. Репозиторій VolshebniKGG/YEBOYBot,
гілка feature/core-v2; основна ОС Windows 10/11, майбутній production Linux.
Основний користувацький вхід — YEBOYBot.cmd.

Спершу звір поточний GitHub/local HEAD, незбережені зміни, PR та CI. Прочитай
AGENTS.md, якщо є, README, CORE_V2_STATUS_UA, MIGRATIONS_UA,
PROJECT_AUDIT_UA, WINDOWS_CMD_LAUNCHER_UA та актуальний код. Не покладайся
на історичні «наступні кроки». Не реалізовуй повторно завершені функції.

Відомий стан після попередньої ітерації, який треба повторно перевірити:
- Schema2, впорядкована SQLite-ревізія 002_case_updated_at, read-only план,
  explicit [21] з backup/transaction/rollback/report та захистом stale config.
- v2_cases.updated backfill із created, а нові переходи записують актуальний час.
  Pending reconciliation це ще не реалізує.
- Нова SQLite/PostgreSQL БД — v2; наявна v1 не мігрує під час login.
  PostgreSQL v1 upgrade через меню заблокований до native backup workflow.
- Heartbeat failure закриває клієнт/задачі, без recovery loop; зовнішнього
  супервізора kill/segfault/аварій ОС ще немає.
- Є 12 slash-команд, guild-scoped XP, warnings/timeout, durable reminders,
  Windows bootstrap/process/config/backup/update/diagnostics/autostart code.
- Automod/welcome/autorole/voiceXP/panels — не активні готові модулі.
- Live Discord, ручний Windows logon, production 24/7 та останній Windows CI
  не вважай перевіреними без нового підтвердження.

Найближчий завершуваний P0-етап: убезпеч legacy entrypoint run.bat.
Аудит виявив REG ADD Explorer, global pip cache purge/uninstall і
--break-system-packages. Не запускай цей скрипт для перевірки на машині
користувача. Збережи старі модулі й доступ до них; спроєктуй безпечний запуск
у власному legacy venv із чітким deprecated-попередженням. Не змішуй root
py-cord та Core V2 discord.py: вони мають спільний namespace discord.
Без згоди не змінюй реєстр, системний Python, пакети або автозапуск.
Тестуй поведінку в ізольованому середовищі, з кирилицею/пробілами й exit codes.
Не перенось legacy nocheckcertificate=True або E:-hardcoded FFmpeg у Core V2.

Наступний P0 після цього: native PostgreSQL pg_dump → перевірка backup →
транзакційний upgrade1→2 → перевірене pg_restore recovery. Секрети не передавай
у commandline/logs, backup залишай приватним. Тести використовують тільки
одноразову yeboy_test/ізольовані схеми. Перевір backup failure, permission error,
rollback, повторне застосування, збереження settings/XP/jobs/cases і відмову
невідомої версії до DDL. Не видавай fresh PostgreSQL підтримку за upgrade readiness.

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
