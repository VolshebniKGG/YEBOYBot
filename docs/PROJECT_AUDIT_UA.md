# Аудит YEBOYBot — 09.11.2026

Початкова перевірена база: `improvement/probot-readiness`, коміт `5e31ee87ca14dc5eb9c378bb5a678040a89b8620`.
[PR №2](https://github.com/VolshebniKGG/YEBOYBot/pull/2) відкритий як draft,
base — `feature/core-v2`. [CI базового коміту](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/42523717327)
завершився успішно. Це докази для зазначеної бази, а не для наступних правок.

Після відновлення роботи враховано нові коміти до `9f051006d4480653266f176d4af5b20329a634c3`: live-потоки з обмеженими перепідключеннями, пагінацію черги, next/clear/jump, гучність панелі та додаткову перевірку складу гілки. Їхні зміни збережено під час поєднання. Живе відтворення не підтверджувалося.

## Поточний код порівняно з передачею контексту

| Частина | Підтверджено у Core V2 | Межа готовності |
|---|---|---|
| Windows | Меню 1–21; [6] вибір гілки/коміту, Git і ZIP updater, резервування SQLite та коду | Ручний сценарій на ПК користувача не перевірено |
| Музика | play/join/queue/remove/move/shuffle/repeat/panel/skip/stop/pause/resume/volume/autoplay | Немає доказу живого звуку, довгих сесій чи доступності зовнішніх джерел |
| Префікс | `!help`, `!modules`, `!queue` мають прямі приватні DM; fallback — авторська кнопка | Змінювальні команди через кнопку; обмеження Discord Integrations автоматично не успадковуються |
| Спільнота | `Community`, `AutoMod`, `RolePanels`, `LevelRewards` реально підключені в `bot.py` | Наявність обробників не замінює ручну перевірку Discord |
| AutoMod | Метадані, понад 5 повідомлень/5 с або 8 різних згадок, обмеження частоти видалень | Немає налаштовуваних контентних правил і винятків |
| Ролі | Постійна панель видачі/зняття однієї безпечної ролі | Немає багаторольового конструктора |
| XP | Текстовий/голосовий XP, рейтинг, накопичувальні нагороди, schema 3 | Немає режиму заміни нагород і повного налаштування винятків |
| Журнали | Входи/виходи, створення/видалення каналів і ролей | Немає повного набору подій і категорій каналів журналів |
| Вебпанель | Не реалізована як повноцінна панель Core V2 | Потрібні OAuth2, серверні права, CSRF і спільна модель налаштувань |

## Перший завершуваний етап: взаємне блокування оновлення

Відтворено: `tools.launcher.process.start()` допускав створення процесу за наявності
`update-plan.json` або `update-transaction.json`. Блокування Python updater діяло
лише під час заміни коду, а не протягом встановлення залежностей і перевірки.
Другий CMD міг помилково почати recovery активного оновлення.
Виправлення закриває цикл apply → dependencies → verify → confirm/rollback,
блокувати паралельне відновлення й запуск із незавершеним планом. Дані й схема БД
не змінюються. Автоматичні перевірки цього сценарію не є перевіркою Discord.

## Наступні пріоритети

1. Надійність: завершити перевірки Windows updater, потім контрольовану живу музичну сесію.
2. Панель плеєра: обмеження на користувача/сервер. Зараз глобальні 64 слоти може
   зайняти один учасник; `View(timeout=120)` — тайм-аут бездіяльності, а не абсолютні 2 хвилини.
3. Налаштовувані AutoMod/винятки, багаторольові панелі, XP exclusions та режим заміни нагород.
4. Категорії журналів, єдина модель доступу slash/prefix, після цього вебпанель.

## Порівняння з офіційними можливостями ProBot

Перевірено 09.11.2026:

- [AutoMod](https://docs.probot.io/docs/modules/automod): фільтри слів/посилань/запрошень,
  дублікатів, згадок і винятки ролей/каналів; частина правил працює через Discord AutoMod.
- [Самостійні ролі](https://probot.io/uk/features/self-assignable-role): кнопки, реакції,
  select-меню з кількома пунктами.
- [Tickets](https://docs.probot.io/docs/modules/tickets) реально документовані;
  додавання до YEBOYBot залежить від потреби сервера, а не лише конкурентного списку.
- Музику згадано в маркетинговому описі ProBot, але поточний каталог команд не дає
  достатнього підтвердження музичних джерел або паритету. Твердження «краще за ProBot» не робиться.

## Історичний аудит 29.10.2026

**Нижче збережено попередній звіт. Його позначки Missing/Disconnected, номери меню,
кількість команд і CI не описують поточний стан; актуальна звірка наведена вище.**

# Аудит проєкту YEBOYBot — 29.10.2026

Поточна ітерація на базі `0698223` додає `/music volume percent`: ціле 0–100,
без числа — стан наявної сесії. Для читання й зміни потрібні «Керувати сервером»,
свіжа перевірка учасника й поточний голосовий канал бота. Гучність діє для
власного поточного PCM і наступних треків цієї сесії. 0 вимикає звук без
додавання паузи; під час відтворення час і черга продовжуються. Наявні паузи
залишаються незалежними. Нова сесія або перезапуск бере INI.
Команда не підключає voice, не змінює БД чи INI; закриття, завершена задача
й reconnect блокують керування. Часткова помилка SDK не підтверджує успіх;
за розбіжності поточного й майбутнього значень UI показує обидва.
[Гучність і межі спостереження](RANK_MUSIC_UA.md#гучність-музичної-сесії--29102026).

База `0698223` має успішні push40773505381 і PR40773512437, усі 9 jobs у
кожному. Це історичні докази рейтингу, не перевірка нового коду гучності.
Актуальний CI за SHA — у [draft PR#1](https://github.com/VolshebniKGG/YEBOYBot/pull/1).

Попередня ітерація на базі `4c160da` додала окремі `/leaderboard xp_type`
рейтинги чату й голосу та точне сортування великих сум XP. Без параметра
лишається загальний рейтинг; окремі осі узгоджені з місцями PNG `/rank`.
Дані XP, assets, schema й кількість команд не змінюються. Пауза вже збережена
окремим комітом; її результати не приписуються новому коду рейтингу.
[Окремі рейтинги](RANK_MUSIC_UA.md#окремі-рейтинги-чату-й-голосу).

Попередня ітерація на базі `23eb600` додала `/music pause` і `/music resume`.
Ручна пауза незалежна від AutoPause порожнього каналу, діє лише в наявній
сесії та показується у приватній черзі. Для нових підкоманд потрібні sync
обраної області через [11] і restart [2]. База має успішні push40714907665
і PR40714922993, усі 9 jobs у кожному; ці результати не приписуються новому коду.
[Пауза й відновлення](RANK_MUSIC_UA.md#ручна-пауза-й-відновлення).

Попередня музична ітерація на базі `de38b71` виправила втрату обліку голосової
сесії після невдалого від'єднання та обхід backoff через довге отримання треку.
Непідтверджена сесія блокує нові підключення; `/music stop` дозволяє обмежену
повторну спробу, `/music queue` і `/modules` показують стан завершення.
База має успішні push40681982798 і PR40682163302; ці історичні результати не
є перевіркою наступного коду. На тому етапі main, schema2, залежності й кількість
команд не змінювалися.
[Відновлення музики](RANK_MUSIC_UA.md#непідтверджена-зупинка-та-повторна-спроба) ·
[Фактичний статус](CORE_V2_STATUS_UA.md).

Статус нижче описує інтеграцію й автоматичні перевірки. Робота на живому Discord,
ручний Windows/logon і production 24/7 не підтверджені.

| Частина | Стан | Фактичний обсяг |
|---|---|---|
| Windows launcher | Partial | Панель до Python, opt-in WinGet Python3.12x64, усі Core dependencies, [24]/[5] parent-exit перед pip; token wizard/start/stop/restart/locks/heartbeat/logs збережені. На804d009 Windows1033/92 та39PS+12menu успішні; реальна інсталяція Python на чистому ПК не підтверджена |
| Process recovery | Partial | Python exception backoff і fail-closed control; зовнішнього crash supervisor немає |
| SQLite backup | Partial | Snapshot, manifest/SHA, staged restore, archive без видалення; ручний Windows не завершено |
| Міграції SQLite | Partial | Registry, план, явний 1→2, backup, rollback, idempotence; немає manual Windows доказу |
| PostgreSQL | Partial | [23] standard public Core snapshot/upgrade1→2/restore в окрему порожню БД; Linux17.11 локально й PG maintenance76 у CI перевірено; Windows native/TLS/custom schemas не підтверджені |
| Автозапуск | Partial | Opt-in Task Scheduler root/SID, pythonw; реальне створення/logon не перевірено |
| Slash-команди | Partial | 20 основних + opt-in група music із 9 підкомандами (21 коренева команда з групою); нові volume, pause/resume та leaderboard xp_type потребують sync обраної області [11] → restart [2]; none не публікує їх, global для тестового сервера не потрібна; live Discord ще не підтверджений |
| Модерація | Partial | Kick/ban/unban: shared service, fresh permissions/hierarchy, atomic cross-action claim, ambiguity-safe audit; modhistory з pending diagnostics. Ban не видаляє повідомлення; unban за ID колишнього учасника. Clear має окреме підтвердження/receipt; automatic reconcile відсутній; старі timeout lifecycle потребують виправлення |
| Попередження | Partial | /warn: fresh authority + atomic local warn/applied, deterministic guild/interaction key, immutable payload/revoked replay; /warnings bounded10 UTF16 outputs. Read/revoke authorization warnings/unwarn ще cached; liveDiscord не перевірений |
| Очищення повідомлень | Partial | До25/recent7days/TextChannel, immutableIDs, ephemeral60s confirm, atomicmanualjobclaim/expiry, ownstatus, no replay unknown; liveDiscord/manualWindows не перевірені |
| Музика Core | Partial | Per-guild FIFO/autoplay, пріоритет замовлень, bounded worker I/O, stop/autojoin guards, quarantine непідтвердженої сесії та backoff. Pause/resume з окремими manual/AutoPause; volume 0–100 для наявної сесії, поточного власного PCM і наступних треків. 0 вимикає звук без додавання паузи; наявні паузи залишаються, під час відтворення час/черга продовжуються. Queue розрізняє спостережений PCM, майбутнє налаштування та unknown, показує обидва значення за розбіжності. Options subset, FFmpeg/Deno setup; Spotify metadata→YouTube match. Черга/пауза/гучність у пам'яті, нова сесія бере INI 0.01–1.0; live Discord/native/24×7 не перевірені |
| XP | Partial | Текст5XP/60s, голос5XP/спостережену хвилину з ≥2 придатними людьми; PNG з власних assets, окремі text/voice ranks і leaderboard xp_type, exact total sort без bigint/float overflow, стабільний tie за user_id. [26] explicit JSON→SQLite max-merge з backup. Rewards, speech detection і live Discord не перевірені/відсутні |
| Нагадування | Partial | Durable lease/retry/access check; at-least-once, можливі повтори після crash |
| Automod/welcome/autorole/panels | Disconnected | Поля/таблиці/helpers є; активних сценаріїв Discord немає |
| Подієві Discord-журнали | Missing | Системні logs і moderation cases не є повним event audit |
| Onboarding/help/modules | Partial | Приватні /help із runtime-каталогом/сторінками та /modules зі свіжими read-only станами; /setup і запис конфігурації через Discord не реалізовані, live UI не перевірений |
| Метрики/аналітика | Missing | Немає p95/p99, CPU/RAM/loop lag, resource baseline і soak evidence |
| Tickets/polls/giveaways/webpanel | Missing | Наявність Flask/Redis у legacy dependencies не реалізує ці сервіси |
| Legacy cogs | Partial | Реальні moderation/user/help/rank завантажені offline в disposable copy; поведінка команд/live API та music не перевірені |
| run.bat/run.py | Partial | Ізольований .venv-legacy, guard, locks, pinned imports, append/redaction; Windows CI45/Linux43+2skip успішні на a6c8e49; ручний Windows/live API очікуються |
| start_v2.bat | Duplicate | Додаткова стара точка входу збережена |
| CI | Partial | Поточні докази гучності — у CORE_V2_STATUS та PR#1 за точним SHA. Історична база 0698223: push40773505381 і PR40773512437 мають усі 9 jobs success; full suites 2236, Windows 2069/167, Linux 2027/209, PostgreSQL 2193/43, CVE 31/0/0. Ці результати не приписуються новому коду; ручний Windows/WinGet install/live API не підтверджені |
| Dependency audit | Partial | Linux Core/dev/pip:31 distributions, PyNaCl1.6.2/pip26.2 zero known advisories; legacy/OS/Windows resolution не охоплені |

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
