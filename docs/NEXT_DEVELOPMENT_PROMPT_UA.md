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

Базовий fafa177 підтвердив Ubuntu309/38, PostgreSQL342/5, PG maintenance76
і legacy обох ОС; повний Windows job скасовано під час pytest, logs404 і відсутній
artifact не дають конкретного тесту чи стека. Тести не скорочувалися.
Поточна kick/history+installer ітерація має497 tests,150 нових: PostgreSQL17.11 —
492 passed/5 Windows skipped; default SQLite —449 passed/48 skipped.
Реальні SQLite/PG перевіряють atomic claims/history, справжні discord.py моделі —
mocked HTTP. Live Discord, ручний Windows і 24/7 не підтверджені. Перевір новий
CI за SHA; focused moderation і PG jobs не видавай за весь зелений workflow.
Немає нових application dependencies/intents/DDL. Linux Core/dev/installer CVE-аудит
24 distributions знайшов6 unique pip25.0.1 advisories; після pip26.2 zero/0skips.
Core/legacy installers примусово перевіряють pip26.2 у власному venv до dependencies,
навіть при valid marker, без глобальних pip змін. Не дублюй виправлення.
Наявний Core menu не auto-upgrade-ить pip: зупинка[3]→dependencies[5].
Legacy/Cross-OS/native/OS CVE-аудит і routine refresh advisory data ще потрібні.

Код f5685f06 має нові CI докази в push40369117626/PR40369126215:
focused Windows192/10, Ubuntu189/13, PGmaintenance208, legacy Windows51 й
Ubuntu49/2, dependency audit24distributions/zero/0skips/pip26.2. Full jobs ще
виконувались на17:34UTC, їхній успіх не підтверджений. Windows8.3alias assertion
виправлено; короткі IDs XML/SID CSV прибрали надмірні payloads із -vv logs,
не зменшуючи вхідних даних чи assertions. Це не доведена причина full CI delay.
Насамперед отримай завершені full job logs/artifacts і встанови конкретний
тест/стек затримки; не видаляй перевірки й не видавай локальний repeat за CI.
Точний локальний CI pipeline завершився449/48 за43.82s. Перевір actual HEAD,
оскільки follow-up test/docs commits можуть мати нові runs.

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
