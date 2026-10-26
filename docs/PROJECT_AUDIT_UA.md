# Аудит проєкту YEBOYBot — 26.10.2026

Оглянуто відстежуване дерево, core_v2, tools/launcher, legacy yeboybot,
точки входу, залежності, тести, CI та документацію. База ітерації —
feature/core-v2@2fc94e8; legacy entrypoint ізольовано у 8b1897a,
наступне виправлення враховує Windows sys.path і redirected UTF-8.
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
| PostgreSQL | Partial | Fresh schema2 й запити; v1 upgrade/backup/restore workflow відсутній |
| Автозапуск | Partial | Opt-in Task Scheduler root/SID, pythonw; реальне створення/logon не перевірено |
| Slash-команди | Partial | 12 commands: status/levels/rank/leaderboard/warn/warnings/unwarn/timeout/untimeout/remind/reminders/cancel-reminder |
| Модерація | Partial | Permissions/hierarchy/cases; kick/ban/unban/clear/modhistory та pending reconciliation відсутні |
| XP | Partial | Текстовий XP/cooldown/guild rankings; голосовий XP/rewards не підключені |
| Нагадування | Partial | Durable lease/retry/access check; at-least-once, можливі повтори після crash |
| Automod/welcome/autorole/panels | Disconnected | Поля/таблиці/helpers є; активних сценаріїв Discord немає |
| Подієві Discord-журнали | Missing | Системні logs і moderation cases не є повним event audit |
| Onboarding/help/modules | Missing | Windows wizard існує; Discord /setup та module UX не завершені |
| Метрики/аналітика | Missing | Немає p95/p99, CPU/RAM/loop lag, resource baseline і soak evidence |
| Tickets/polls/giveaways/webpanel | Missing | Наявність Flask/Redis у legacy dependencies не реалізує ці сервіси |
| Legacy cogs | Partial | Реальні moderation/user/help/rank завантажені offline в disposable copy; поведінка команд/live API та music не перевірені |
| run.bat/run.py | Partial | Ізольований .venv-legacy, guard, locks, pinned imports, append/redaction; Windows CI/manual очікуються |
| start_v2.bat | Duplicate | Додаткова стара точка входу збережена |
| CI | Partial | На 8b1897a Ubuntu SQLite/PG/legacy success; Windows legacy failure виправлено в коді, повний Windows pytest cancelled/BlobNotFound, новий прогін потрібний |

## Ризики, що потребують окремих завершених ітерацій

1. **Legacy entrypoint виправлено в коді.** run.bat більше не змінює реєстр,
   не виконує global pip uninstall/cache purge/break-system-packages. [22] або
   run.bat керує .venv-legacy; run.py перевіряє його до імпорту cogs. Є негативні
   тести, реальне встановлення й offline cogs smoke на Linux; native Windows
   smoke додано до CI, успіх ще потребує підтвердження. Початковий скрипт не виконували.
2. **Ізоляція залежностей.** Історичний root requirements з py-cord2.6.1 залишено,
   але новий запуск його не встановлює. Legacy async add_cog потребує discord.py;
   новий requirements-legacy використовує discord.py2.7.1 у власній .venv-legacy,
   Core V2 лишається в .venv-v2. Змішане/зовнішнє середовище відхиляється.
3. **P0/P1 legacy переносність/безпека.** Music жорстко очікує E:\Discord Bot\Bot\bin\ffmpeg.exe
   і ffprobe; legacy downloader/youtube/music мають nocheckcertificate=True.
   Перенесення має включати OS-neutral paths та перевірку TLS.
4. **P0 PostgreSQL upgrade.** Guard v1 безпечний, але стару PG інсталяцію не оновити
   через меню. Native pg_dump/verification/upgrade/recovery — окремий блокувальний
   сценарій перед rollout для таких користувачів.
5. **P1 імпорт даних.** Legacy warnings/points не мають належної guild ізоляції.
   Не імпортувати глобальні JSON без явного зіставлення сервера й dry-run.
   Старі timed sanctions/create_task+sleep та memory voice XP не стійкі до restart.
6. **P1 moderation lifecycle.** updated є основою, але pending reconciliation
   й повний permission/API-error callback coverage ще потрібні.
7. **P2 hygiene.** Відстежуються pyc, logs та queue JSON попри .gitignore.
   Потрібна окрема перевірка й безпечне припинення tracking зі збереженням локальних
   даних. Значення приватних журналів/черг у цьому аудиті не публікувалися.
8. **P2 перевірки.** Ruff E4/E7/E9/F та pip check не замінюють formatter/type/CVE audit.
   Потрібні окремі доречні перевірки, без вигаданих звітів про вразливості.
9. **P3 розгортання.** Не підготовлено production systemd/artifacts, ресурсних бюджетів
   та підтверджених навантажувальних характеристик. AutoShardedBot не доводить
   multi-process масштабування або місткість тисяч серверів.
10. **Поширення.** Видимого LICENSE немає; ліцензії font/assets потребують перевірки
    перед вибором умов розповсюдження. Ліцензію власника не вгадувати.

## Орієнтир ProBot

Публічні актуальні характеристики ProBot у цій ітерації не досліджувалися.
Порівняльних вимірювань немає. Український onboarding, контроль власних даних,
прозорий audit/recovery та self-hosting — цільові сценарії, а не доведені переваги.
Наступні порівняння повинні фіксувати джерело/дату, однаковий сценарій, обладнання
та методику. Недоступні внутрішні метрики ProBot позначати «Невідомо».

Точні результати перевірок цієї ітерації — у CORE_V2_STATUS_UA.md;
наступне завдання — NEXT_DEVELOPMENT_PROMPT_UA.md.
