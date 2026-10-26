# Промт наступної ітерації YEBOYBot

Продовжуй реальну розробку VolshebniKGG/YEBOYBot у feature/core-v2 як
відповідальний головний розробник: архітектура, Python/Discord, SRE, БД, безпека,
QA та український UX. Основна ОС користувача — Windows 10/11, майбутній production
— Linux. Основний вхід — YEBOYBot.cmd. ProBot — функціональний орієнтир;
не копіюй код, дизайн або брендинг і не оголошуй переваг без вимірювань.

Спершу звір GitHub/local HEAD, локальні зміни, PR та CI. Прочитай AGENTS.md,
якщо існує, README, CORE_V2_STATUS_UA, PROJECT_AUDIT_UA, MIGRATIONS_UA,
WINDOWS_CMD_LAUNCHER_UA, LEGACY_LAUNCHER_UA, POSTGRES_RECOVERY_UA та
COMPETITIVE_SCENARIOS_UA. Перевір код; історичні результати документації не
приписуй новому SHA. Онови матрицю Working/Partial/Disconnected/Broken/
Duplicate/Missing/Untested. Не реалізовуй повторно завершені можливості.

Відомий стан, який потрібно повторно перевірити:

- Core V2 має 12 slash-команд: status, levels, rank, leaderboard, warn, warnings,
  unwarn, timeout, untimeout, remind, reminders, cancel-reminder.
- Є guild-scoped XP, cases, durable reminders з lease/retry; доставка нагадувань
  at-least-once. Exactly-once для Discord send не обіцяється.
- Schema2 і SQLite 1→2 через [21] мають verified backup, транзакцію, rollback,
  stale-source guard і report. Cases.updated заповнюється created для старих
  записів; нові переходи пишуть фактичний час. Pending reconciliation ще немає.
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

Перевірки application коду a5dd3ef та CI-підготовки f14273a: локально 347 tests,
76 нових; з PostgreSQL 17.11 — 342 passed/5 Windows skipped, без PG —
309 passed/38 skipped. CI f14273a, run40273263180: postgres-maintenance —
76 passed, legacy Windows — 45 passed, legacy Ubuntu — 43 passed/2 skipped.
Повні Windows/Linux/PG jobs на момент звіту ще виконувалися; перевір їхній
актуальний статус. Не видавай focused 76 за успіх усього workflow.
APT client17 був недоступний на Ubuntu runner; виправлений CI використовує
справжні native17 tools із того самого service container через stdio.
Логи/JUnit зберігаються як artifacts. BlobNotFound не доводить дефект коду;
для зависання потрібні конкретний тест і стек. Не прибирай перевірки.

Найближчий функціональний етап — повна модерація та audit lifecycle. Рухайся
невеликими завершеними сценаріями: kick, ban, unban, clear, modhistory.

1. Повторно перевір існуючі security/cases/commands і поступово відділи спільну
   service/repository логіку для майбутньої вебпанелі.
2. Для кожної дії перевір actor/bot permissions, role hierarchy, guild_id,
   причину, owner/self/bot targets та фактичний результат Discord API.
3. Записуй pending/applied/failed коректно; для справ після crash спершу
   визначай результат або неоднозначність, не повторюй небезпечний API навмання.
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

Підтримуй окремі експлуатаційні задачі: CVE-аудит залежностей (pip check ним
не є), безпечне припинення tracking pyc/logs/queues зі збереженням локальних даних,
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
