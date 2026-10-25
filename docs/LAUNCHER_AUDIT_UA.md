# Аудит запуску — 25.10.2026

База аудиту: `feature/core-v2`, `61c5293`. AGENTS.md у дереві відсутній.
Читання виконано через GitHub API: shell Git clone недоступний через proxy.
Локальний каталог — текстовий snapshot для перевірок, не повний клон із binary assets.
Є відкритий draft PR #1; main не змінювався.

Статуси описують підтверджену інтеграцію, а не готовність продукту до production.

| Компонент | Стан | Підстава / обмеження |
|---|---|---|
| YEBOYBot.cmd → tools.launcher | Partial | Меню, фоновий запуск, stop/restart є; ручний подвійний клік не перевірено |
| Початкове встановлення | Partial | Новий bootstrap, Python 3.12, fallback py/python, повторюваний pip; Windows CI перевіряється окремо |
| Керування процесом | Partial | OS lock, nonce, heartbeat; реальний Discord-сценарій не перевірено |
| Discord readiness | Untested | Готовність записує on_ready; live API не перевірено |
| Відновлення після exceptions | Partial | Обмежений retry/backoff; kill/segfault не відновлює |
| Git-оновлення | Partial | Fast-forward, backup, smoke; ZIP та PostgreSQL відхиляються |
| SQLite backup/restore | Partial | Backup API, schema validation, підтвердження; checksum/retention ще відсутні |
| PostgreSQL | Partial | asyncpg та CI є; backup/restore меню відсутні |
| Автозапуск Windows | Missing | Планувальник завдань не інтегровано |
| Телеметрія / експорт діагностики | Missing | Doctor і heartbeat є, повних метрик та експорту немає |
| Версійовані міграції | Partial | Маркер схеми v1 і початкова ініціалізація; ланцюжка міграцій немає |
| Core V2 moderation | Partial | warn/warnings/unwarn, timeout/untimeout; kick/ban/unban/clear/modhistory відсутні |
| XP | Partial | Текстовий XP/cooldown/rank; голосовий XP та rewards відсутні |
| Нагадування | Partial | Збереження, lease/retry; можлива повторна доставка після crash |
| Automod/welcome/autorole/role panels | Disconnected | Резерв конфігурації/схеми, активних модулів немає |
| Старий yeboybot | Untested | Окремий run.py, discord.Intents.all(), cogs moderation/music/rank; сумісність із v2 не підтверджена |
| Старі run.bat/start_v2.bat | Duplicate | Інші точки входу збережено до перевірки сумісності |

## Найближчі завершувані етапи

1. Перевірити bootstrap у Windows CI та вручну на Windows 10/11 без Python Launcher.
2. Додати діагностичний звіт без секретів і перевірку фактичної конфігурації/БД.
3. Додати checksum/метадані резервних копій та керовану retention без автоматичної втрати копій.
4. Реалізувати opt-in Task Scheduler і окремий supervisor аварій процесу.
5. Після стабілізації лаунчера — міграції та модерація з перевіркою hierarchy.
