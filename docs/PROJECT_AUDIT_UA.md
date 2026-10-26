# Аудит проєкту YEBOYBot — 26.10.2026

Оглянуто відстежуване дерево, core_v2, tools/launcher, legacy yeboybot,
точки входу, залежності, тести, CI та документацію. База ітерації —
feature/core-v2@083d4bc; нова ітерація додає /ban і /unban до спільного
ModerationService та оновлює «Код структура.txt». Kick/history, pip26.2,
PG maintenance й legacy entrypoint збережені. Базовий CI083d4bc має всі9jobs
success; нові результати фіксуються окремо за новим SHA.
SQLite schema2/migrations та fail-closed heartbeat збережені без повторної реалізації.
main@14bd68a містить стару реалізацію; PR #1 залишається draft. GitHub HEAD
і CI потрібно повторно звіряти перед наступною роботою.

Статус нижче описує інтеграцію й автоматичні перевірки. Робота на живому Discord,
ручний Windows/logon і production 24/7 не підтверджені.

| Частина | Стан | Фактичний обсяг |
|---|---|---|
| Windows launcher | Partial | Bootstrap, token wizard, start/stop/restart, locks, heartbeat, logs, diagnostics |
| Process recovery | Partial | Python exception backoff і fail-closed control; зовнішнього crash supervisor немає |
| SQLite backup | Partial | Snapshot, manifest/SHA, staged restore, archive без видалення; ручний Windows не завершено |
| Міграції SQLite | Partial | Registry, план, явний 1→2, backup, rollback, idempotence; немає manual Windows доказу |
| PostgreSQL | Partial | [23] standard public Core snapshot/upgrade1→2/restore в окрему порожню БД; Linux17.11 локально й PG maintenance76 у CI перевірено; Windows native/TLS/custom schemas не підтверджені |
| Автозапуск | Partial | Opt-in Task Scheduler root/SID, pythonw; реальне створення/logon не перевірено |
| Slash-команди | Partial | 16 commands: попередні14 плюс ban/unban; live Discord ще не підтверджений |
| Модерація | Partial | Kick/ban/unban: shared service, fresh permissions/hierarchy, atomic cross-action claim, ambiguity-safe audit; modhistory з pending diagnostics. Ban не видаляє повідомлення; unban за ID колишнього учасника. Clear/reconcile відсутні; старі timeout lifecycle потребують виправлення |
| XP | Partial | Текстовий XP/cooldown/guild rankings; голосовий XP/rewards не підключені |
| Нагадування | Partial | Durable lease/retry/access check; at-least-once, можливі повтори після crash |
| Automod/welcome/autorole/panels | Disconnected | Поля/таблиці/helpers є; активних сценаріїв Discord немає |
| Подієві Discord-журнали | Missing | Системні logs і moderation cases не є повним event audit |
| Onboarding/help/modules | Missing | Windows wizard існує; Discord /setup та module UX не завершені |
| Метрики/аналітика | Missing | Немає p95/p99, CPU/RAM/loop lag, resource baseline і soak evidence |
| Tickets/polls/giveaways/webpanel | Missing | Наявність Flask/Redis у legacy dependencies не реалізує ці сервіси |
| Legacy cogs | Partial | Реальні moderation/user/help/rank завантажені offline в disposable copy; поведінка команд/live API та music не перевірені |
| run.bat/run.py | Partial | Ізольований .venv-legacy, guard, locks, pinned imports, append/redaction; Windows CI45/Linux43+2skip успішні на a6c8e49; ручний Windows/live API очікуються |
| start_v2.bat | Duplicate | Додаткова стара точка входу збережена |
| CI | Partial | На базовому fafa177 Linux309/38, PG342/5, maintenance76 і legacy обох ОС success; Windows full cancelled під час pytest без доступного stack/artifact. На f5685f06 focused Windows192/10, Ubuntu189/13, maintenance208, legacy51/49+2skip, CVE24/zero підтверджені. Повний c86c177 push40370321990: усі9jobs success, Windows454/43, Ubuntu449/48, PG492/5. Ручний Windows/live API не підтверджені; звіряти SHA |
| Dependency audit | Partial | Linux Core/dev/pip:24 distributions, після pip26.2 zero known advisories; legacy/OS/Windows resolution не охоплені |

## Ризики, що потребують окремих завершених ітерацій

1. **Legacy entrypoint виправлено в коді.** run.bat більше не змінює реєстр,
   не виконує global pip uninstall/cache purge/break-system-packages. [22] або
   run.bat керує .venv-legacy; run.py перевіряє його до імпорту cogs. Є негативні
   тести, реальне встановлення й offline cogs smoke на Linux; native Windows
   smoke й45 legacy tests успішні у Windows CI на a6c8e49. Ручний Windows10/11
   не перевірений. Початковий скрипт не виконували.
2. **Ізоляція залежностей.** Історичний root requirements з py-cord2.6.1 залишено,
   але новий запуск його не встановлює. Legacy async add_cog потребує discord.py;
   новий requirements-legacy використовує discord.py2.7.1 у власній .venv-legacy,
   Core V2 лишається в .venv-v2. Змішане/зовнішнє середовище відхиляється.
3. **P0/P1 legacy переносність/безпека.** Music жорстко очікує E:\Discord Bot\Bot\bin\ffmpeg.exe
   і ffprobe; legacy downloader/youtube/music мають nocheckcertificate=True.
   Перенесення має включати OS-neutral paths та перевірку TLS.
4. **PostgreSQL workflow додано.** [23] робить native dump/перевірку/явний upgrade
   й recovery в окрему порожню БД; підтверджено збереження даних на PostgreSQL17.11.
   Підтримується лише стандартна public Core-схема, без custom dependencies;
   ці копії не замінюють backup всього сервера. Native Windows/TLS і PG code-updater
   потребують окремих перевірок. Деталі — POSTGRES_RECOVERY_UA.md.
5. **P1 імпорт даних.** Legacy warnings/points не мають належної guild ізоляції.
   Не імпортувати глобальні JSON без явного зіставлення сервера й dry-run.
   Старі timed sanctions/create_task+sleep та memory voice XP не стійкі до restart.
6. **P1 moderation lifecycle.** Kick/ban/unban використовують спільний service;
   приватна history діагностує pending без повторної API-дії. Unban вимагає
   підтвердженого запису ban, ban приймає ID поточного/колишнього учасника й
   явно зберігає повідомлення. Перевірка ролей не атомарна з Discord API. Старі warn/timeout/untimeout користуються
   попередніми checks/lifecycle й потребують окремого перенесення; повна модерація
   та автоматичне reconciliation ще не завершені.
7. **P2 hygiene.** Відстежуються pyc, logs та queue JSON попри .gitignore.
   Потрібна окрема перевірка й безпечне припинення tracking зі збереженням локальних
   даних. Значення приватних журналів/черг у цьому аудиті не публікувалися.
8. **P2 перевірки.** Ruff E4/E7/E9/F та pip check не замінюють formatter/type audit.
   CVE-аудит конкретного Linux Core resolution виконано:24 distributions,6 unique
   pip advisories до виправлення й0 після pip26.2. Bootstrap guard стосується
   обох project venv; legacy/Cross-OS/OS/native аудит залишається окремим.
   Немає гарантії відсутності нових advisories. Деталі — DEPENDENCY_AUDIT_UA.md.
9. **P3 розгортання.** Не підготовлено production systemd/artifacts, ресурсних бюджетів
   та підтверджених навантажувальних характеристик. AutoShardedBot не доводить
   multi-process масштабування або місткість тисяч серверів.
10. **Поширення.** Видимого LICENSE немає; ліцензії font/assets потребують перевірки
    перед вибором умов розповсюдження. Ліцензію власника не вгадувати.

## Орієнтир ProBot

Публічні джерела й13 сценаріїв зібрано в [COMPETITIVE_SCENARIOS_UA.md](COMPETITIVE_SCENARIOS_UA.md).
Підтверджені функції не доводять вищу швидкість/надійність чи кращий UX.
Український onboarding, контроль власних даних і recovery — вимірювані гіпотези,
а не оголошені переваги. Внутрішні метрики ProBot недоступні, позначені «Невідомо».

Точні результати перевірок цієї ітерації — у CORE_V2_STATUS_UA.md;
наступне завдання — NEXT_DEVELOPMENT_PROMPT_UA.md.
