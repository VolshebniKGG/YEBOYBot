> Актуальне доповнення 30.10.2026: [панелі ролей і перевірки надійності](ROLE_PANELS_RELEASE_UA.md).
> Нижче збережено історію попередніх ітерацій; їхні кількості тестів не описують новий пакет.

# Музика: гучність поточної сесії — 29.10.2026

База `feature/core-v2@0698223` — окрема завершена ітерація рейтингу. Перед
початком цієї роботи дерево було чистим. Її
[push40773505381](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40773505381)
і [PR40773512437](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40773512437)
мають success, усі 9 jobs у кожному. Історичні full suites бази зібрали 2236
тестів: Windows 2069 passed/167 skipped, Linux 2027/209, PostgreSQL 2193/43;
аудит Linux Core — 31 distributions/0 advisories/0 skips. Це докази `0698223`,
а не нового коду гучності. CI поточного SHA звіряється окремо в
[draft PR#1](https://github.com/VolshebniKGG/YEBOYBot/pull/1). Main не змінено.

Додано одну функцію: `/music volume percent` для вже наявної власної сесії.
Необов'язковий `percent` — ціле число 0–100; без числа команда читає стан.
Для запиту й зміни потрібні «Керувати сервером», актуальне членство й той
самий поточний голосовий канал. Команда не створює підключення. Актуальний
сервер і стан сесії повторно звіряються після перевірки доступу. Завершення
сесії, непідтверджене очищення, завершена задача плеєра та відновлення
з'єднання блокують керування.

- Значення застосовується до власного PCM-перетворювача поточного треку
  й наступних треків цієї сесії: замовлень, автоплейлиста та після skip.
  Сервери ізольовані; нова Session або restart знову бере `DefaultVolume` з INI.
- 0% вимикає звук без додавання паузи; наявна пауза залишається. Під час
  відтворення час треку й обробка черги продовжуються. Ручна пауза й AutoPause
  незалежні; зміна гучності не змінює причини паузи.
- `/music volume` і `/music queue` показують спостережений коефіцієнт
  власного поточного PCM, налаштування майбутніх треків за відсутності джерела
  або невідомий стан. Дробове початкове значення з INI не округлюється до
  цілого командного відсотка.
- Помилка SDK дає фіксовану безпечну відповідь без підтвердження успіху.
  Часткова зміна поточного PCM не підтверджує налаштування наступних треків;
  за доступного спостереження й розбіжності UI показує обидва значення.
  Нові значення не записуються в INI чи БД; `DefaultVolume` лишається 0.01–1.0.

Основних slash-команд залишається 20, у групі `/music` тепер 9 підкоманд
(21 коренева команда разом із групою). Нових залежностей, intents, ключів
конфігурації, schema чи міграцій немає. Для нової підкоманди в `YEBOYBot.cmd`
потрібні **[11]** — sync обраної області, потім **[2] Перезапустити**.
`SYNC_COMMANDS=none` її не публікує; для тестового сервера global sync не потрібна.

Додано **191 тест**: 67 backend, 29 engine, 95 commands. Є справжній
`PCMVolumeTransformer` зі зміною амплітуди семплів, audio-thread читання,
межі завершення/заміни джерела під час getter/setter, часткова помилка SDK,
mute, FIFO/AP/skip, ізоляція сесій, незмінність manual/AutoPause/backoff,
права й зміна guild/каналу/closing під час REST, cancellation та safe readback.
Незалежний review: **405 passed** за 13,21 с, включно з усіма новими тестами.
Цільовий набір автора: 399 passed за 3,48 с; metadata/install/help: 145 passed
за 2,85 с. Виявлені review неточне формулювання mute та завершення джерела
під час доступу до гучності виправлено з негативними регресійними сценаріями.

Локально Linux/Python 3.12.14, повний exact verbose CI: **2218 passed,
209 skipped**, загалом **2427**, 0 failures/0 errors за 45,33 с. Ruff,
pip check і diff check пройдено. Fresh pip-audit 2.10.1/PyPI: **31 distributions,
0 advisories, 0 skips**, pip 26.2; Linux Core/runtime/dev/installer resolution,
без OS/native/legacy/окремого Windows resolution. CI нового SHA перевіряється
окремо у PR; історичні результати не приписуються цьому коду.
Початкові невдалі перевірки нових fixtures стосувалися ідемпотентного cleanup
та округлення негативних PCM-семплів; припущення тестів виправлено зі збереженням
перевірок. Старі exact metadata assertions розширено до 9 команд без вилучення.

Локально застосований коефіцієнт PCM не доводить почуту гучність. Аудіопотік
може завершити один кадр зі старим значенням. Живий Discord, native FFmpeg
і 24-годинний сценарій лишаються окремими непідтвердженими межами.
[Інструкція гучності](RANK_MUSIC_UA.md#гучність-музичної-сесії--29102026).

Нижчі датовані розділи зберігають історичні результати й кількість команд
відповідних ітерацій; вони не є перевіркою поточного коду.

---

# Ранг: окремі рейтинги й точність великих сум XP — 28.10.2026

База `feature/core-v2@4c160da` збережена в GitHub як окрема ітерація pause/resume.
Її локальні результати наведені нижче в історичному розділі й не є перевіркою
нового leaderboard-коду. CI кожного SHA звіряється окремо в
[draft PR#1](https://github.com/VolshebniKGG/YEBOYBot/pull/1). Main не змінено.

Ця історична ітерація узгодила текстовий і голосовий рейтинги з PNG `/rank`:

- `/leaderboard page xp_type` приймає необов'язковий вибір **Загальний**
  (`total`), **Чат** (`text`) або **Голос** (`voice`). Без параметра лишається
  попередня сума чату й голосу, сторінка за замовчуванням — 1.
- По 10 профілів поточного guild, сторінки 1–1000. Порядок окремих осей
  відповідає `rank_snapshot`; за однакового XP менший user_id вище.
- Сума сортується як carry/remainder без переповнення signed bigint чи
  округлення SQLite до float. Кожний лічильник лишається допустимим int64;
  загальна сума обчислюється точно Python integer і може перевищувати int64.
- Публічна відповідь містить лише перевірені ID й цілі XP, без сповіщення
  згадок. Порожня сторінка відрізняється від збою читання. БД має 10-секундний
  timeout; generic error теж публічний після public defer, без рядків даних,
  raw SQL, шляхів чи credentials.
- Guild перевіряється до й після SQL. Команда тільки читає дані; XP, рівні,
  налаштування та оригінальні `rank_background.png`/`gg.png` не змінюються.
  Рейтинги використовують збережені профілі XP цього guild, включно з
  історичними профілями учасників, які вийшли; це не скан поточного складу Discord.

Основних команд залишається 20, музичних підкоманд — 8. Нових залежностей,
schema чи intents немає. Зміна metadata `/leaderboard` потребує sync обраної
області через **[11]**, потім **[2] Перезапустити**; `none` не публікує параметр.
Додано **127 тестів**: 53 команди й 74 repository (по 37 SQLite/PostgreSQL).
Є реальні SQL-перевірки максимальних/сусідніх сум, порогів int64, ties,
126 профілів із відтворюваним Python reference, сторінок, rank_snapshot,
guild isolation та відсутності записів. PostgreSQL 17.11 у власному тимчасовому
контейнері: **74 passed**, 0 skipped за 5,80 с; контейнер і тимчасові credentials
прибрано після перевірки. Незалежний review: 117 passed/37 PG-skipped за 16,59 с.

Локально Linux/Python 3.12.14, повний exact verbose CI: **2027 passed,
209 skipped**, загалом **2236**, 0 failures/0 errors за 85,05 с. Ruff, pip check
і diff check пройдено. Залежності не змінювалися; свіжий Linux Core/runtime/dev/
installer аудит цієї сесії: 31 distributions/0 advisories/0 skips, без OS/native/
legacy та окремого Windows resolution. Нові CI докази додаються до PR за точним SHA.
Live Discord,
ручний Windows і порівняльні вимірювання з ProBot не підтверджені.
[Інструкція рейтингу](RANK_MUSIC_UA.md#окремі-рейтинги-чату-й-голосу).

---

# Музика: ручна пауза й відновлення — 28.10.2026

База `feature/core-v2@23eb600` звірена з локальним Git і draft PR#1.
Перед роботою дерево було чистим. Push40714907665 і PR40714922993 цієї бази
мають усі 9 jobs success у кожному; це історичні перевірки, не результати
нових pause/resume змін. Актуальний CI нового SHA — у
[PR#1](https://github.com/VolshebniKGG/YEBOYBot/pull/1). Main не змінено.

Додано `/music pause` і `/music resume` для наявної сесії. Обидві команди
потребують «Керувати сервером», актуального членства й того самого голосового
каналу; не створюють підключення й не працюють із сесією, що завершується.

- Ручний намір паузи відокремлено від AutoPause порожнього каналу. Прихід
  учасника не скасовує ручної паузи; resume не обходить активну AutoPause.
- Намір діє в пам'яті сесії через skip і зміну треку до resume або завершення
  сесії. Це не параметр БД/INI, після перезапуску він не відновлюється.
- Під ручною паузою не споживається наступне замовлення чи автотрек і не
  починається нове отримання джерела. Успішний результат уже розпочатого
  отримання чекає відновлення й повторного отримання свіжого посилання;
  помилка джерела обробляється звичайним шляхом невдалого треку.
- `/music queue` показує окремі причини паузи та доступний локальний стан SDK.
  Невідомий стан не видається за підтверджене відтворення. Втрата з'єднання
  блокує pause/resume до відновлення, зберігаючи встановлений намір паузи.

Основних slash-команд залишається 20; опціональна група music тепер має
8 підкоманд. Нових залежностей, schema/intents/configkeys немає. Для нових
підкоманд потрібна синхронізація обраної області через **[11]**, потім
**[2] Перезапустити**. Режим `SYNC_COMMANDS=none` нічого не публікує;
глобальну sync не потрібно вмикати для тестового сервера.

Додано **142 тести**: 89 commands, 23 engine, 30 backend. Є справжні
`VoiceClient`/`AudioPlayer` стани без запуску аудіопотоку чи Discord login,
незалежність manual/AutoPause, FIFO/AP/skip, пауза під час resolver, права,
зміна guild/каналу/closing під час REST, cancellation і безпечні SDK-помилки.
Незалежний review: 142 passed за 1,74 с. Цільовий набір: 348 passed за 3,62 с.
Повторний resume без зміни наміру не перериває backoff; час ручної паузи
виключається з перевірки миттєвого EOF, але це не вимір фактичного звучання.

Локально Linux/Python 3.12.14, повний exact verbose CI: **1937 passed,
172 skipped**, загалом **2109**, 0 failures/0 errors за 63,85 с. Перший повний
прогін знайшов дві застарілі перевірки числа команд; оновлено точні значення
та перелік усіх 8 музичних команд, жодного сценарію не вилучено.
Перевірки metadata/install: 56 passed за 1,63 с. Ruff, pip check і diff check
пройдено. Fresh pip-audit 2.10.1/PyPI: **31 distributions/0 advisories/0 skips**,
Linux Core/runtime/dev/installer resolution; не OS/native/legacy/Windows.
CI нового коду перевіряється окремо за SHA у PR; старі результати не запозичуються.
Після збереження pause/resume як `4c160da` [push40772258249](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40772258249)
і [PR40772268177](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40772268177)
завершилися success, усі 9 jobs у кожному. Обидва full suites зібрали 2109:
Windows1979/130, Linux1937/172, PostgreSQL2066/43; maintenance1007 passed.
Обидва audit JSON перевірено: 31 distributions/0 vulnerabilities.
Це докази `4c160da`, не наступного rank-коміту.
Live Discord, ручний Windows/WinGet, native recovery й тривалий 24-годинний
сценарій не підтверджені автоматичними тестами.
[Інструкція паузи](RANK_MUSIC_UA.md#ручна-пауза-й-відновлення).

---

# Музика: контроль завершення сесій і backoff — 28.10.2026

База `feature/core-v2@de38b71` звірена з локальним Git і PR#1; до роботи дерево
було чистим. Push40681982798 і PR40682163302 бази завершилися успішно,
усі 9 jobs. Це історичні перевірки; новому музичному коду вони не приписуються.
Актуальний результат CI нового HEAD наведений у
[PR#1](https://github.com/VolshebniKGG/YEBOYBot/pull/1). Main не змінено, PR — draft.

Виправлено два відтворені сценарії без входу в Discord:

- Помилка/таймаут від'єднання та незавершений audio cleanup більше не означають
  успішну зупинку. Зберігається та сама власна Session з ознаками closing і
  close_failed; нове підключення блокується до підтвердженого завершення.
- `/music stop` повідомляє непідтверджений результат і дозволяє обмежену
  повторну спробу закриття тієї самої сесії. `/music queue` і `/modules`
  показують її стан. Одночасні stop не створюють кілька teardown-операцій;
  скасування команди не кидає контрольовану операцію завершення.
- Власний VoiceClient обліковується під час створення, до очікування handshake.
  Пізно отримане з'єднання після stop/видалення сервера або збою attach
  також не втрачається. Різні guilds завершуються незалежно.
- Виняток очищення FFmpeg позначається як непідтверджене завершення;
  сигнал закінчення cleanup не видається за його успіх. Нескінченного retry немає.
- Пошук джерела більше не входить до часу для виявлення миттєвого EOF.
  Повільне отримання треку не скидає лічильник помилок і backoff після
  миттєвого завершення. Skip і пріоритет замовлення не вважаються помилкою.
  Час включає очікування callback/очищення й не є виміром почутого аудіо.

Підтвердження закриття означає локальний стан SDK: виклик не перевищив дедлайн,
`voice.is_connected()` повертає саме False та cleanup успішний. Це не окремий
доказ server voice-state acknowledgement або завершення всіх native-ресурсів:
discord.py може приховати частину транспортних помилок.

Додано **59 тестів**: 38 teardown, 10 backoff і 11 станів музичної сесії.
Є справжні шляхи SDK `Connectable.connect` і `MusicManager` без мережі:
скасування handshake, повтор закриття того самого клієнта/worker, помилки
до/після видалення з manager, поглинений timeout, source cleanup exception,
конкурентні stop, переміщення між каналами, guild isolation та втрата прав бота.
Попередні сценарії й assertions збережено; fixtures моделюють зміну connected
при connect/disconnect. Незалежний review: 202 passed за 3,51 с.

Локально Linux/Python 3.12.14 у точному verbose CI режимі: **1795 passed,
172 skipped**, загалом **1967**, 0 failures/0 errors за 56,53 с. Цільовий набір:
**364 passed**, 0 skipped за 10,40 с. Пропущено PostgreSQL/native Windows;
новий CI перевіряється окремо за SHA, старі результати не запозичуються.
Ruff, pip check і `git diff --check` пройдено. Fresh pip-audit 2.10.1/PyPI:
**31 distributions, 0 advisories, 0 skips**, pip 26.2; Linux Core/runtime/dev/
installer, без OS/native/legacy/Windows resolution.

Нових залежностей, схеми, intents, налаштувань чи slash-команд немає:
20 основних команд і 6 команд групи music збережені. Інструкція застосування:
оновлений `YEBOYBot.cmd` → **[2] Перезапустити**; додаткова sync не потрібна.
Черги лише в пам'яті. Live Discord, ручний чистий Windows, native cleanup після
зависання та тривалий 24-годинний тест не підтверджені й не підміняються mocks.
[Порядок повторної спроби](RANK_MUSIC_UA.md#непідтверджена-зупинка-та-повторна-спроба).

---

# Довідка й стан модулів — 28.10.2026

База `feature/core-v2@5ded468` звірена з локальним Git та PR#1; робоче дерево
перед змінами чисте. База мала успішні push40671293610 і PR40671305031,
усі9jobs; це попередні результати, новому коду вони не приписуються.
Актуальні CI-докази поточного HEAD — у [PR#1](https://github.com/VolshebniKGG/YEBOYBot/pull/1).
Main лишився без змін, PR залишається draft.

Реалізовано:

- `/help topic page`: приватний каталог фактично підключених локальних команд
  із guild override, прикладами й поясненням дозволів. До100 команд,10 на сторінку;
  Ukrainian select/buttons, author/guild scope, timeout120s, cap100views.
  За досягнення межі працює звичайна довідка зі slash-параметрами розділу/сторінки.
  Збій send/cancellation/unload не залишає активну панель без обліку.
- `/modules`: приватний read-only звіт. Fresh guild-scoped SQL≤3s; без записів,
  API-запитів чи міграцій. Готовність читається після SQL. Перевіряються
  intents, collector/gateway/shard, scheduler, музичні сесії/закриття.
  Unknown не підміняється enabled; unfinished automod/welcome/autorole/panels/
  rewards/eventlogs явно непідключені, навіть коли в БД установлені прапорці.
- Guide завантажується ДО синхронізації команд і включений до offline update
  smoke. Тепер20 основних slash-команд плюс optional music group6;
  metadata Core18 не змінено. Схема, залежності, intents і налаштування незмінні.

Нових тестів **117**:40catalog,53module_status,24guide. Є actual discord.py
metadata/UI callbacks, SQLite SELECT-only/freshness/guildisolation, limits,
Unicode/mentions, DB failure/timeout/cancellation, paused runtime, author/guild
controls, pagination, late defer/send/unload races та реальний update smoke.
Незалежний read-only review виявив гонку late-view після unload; її виправлено
і закріплено негативними тестами. Додаткові module-status regressions перевіряють
paused collector, готовність після DB await і невідомий lifecycle музики.

Локально Linux/Python3.12.14: повний exactverboseCI **1736passed/172skipped**,
**1908tests/0failures/0errors**,42.04s. Цільовий набір151passed за5.05s.
Пропущені PostgreSQL/nativeWindows; їх перевіряє CI поточного SHA окремо.
Ruff/pipcheck/diffcheck пройдено. Freshpip-audit2.10.1/PyPI:31distributions,
0advisories/0skips,pip26.2; LinuxCore/runtime/dev/installer, не OS/native/legacy.

Команди показують підключений код і доступні локальні спостереження, а не
доказ усіх Discord API сценаріїв або реального звуку. Живий DiscordUI/manual
Windows/24hsoak не перевірені. Щоб нові команди з'явилися, налаштуйте sync
обраної області через[11] і перезапустіть[2]. Панелі після restart не відновлюються.
[Докладна інструкція](GUIDE_UA.md).

---

# Музика: діагностика та зупинка — 28.10.2026

База `feature/core-v2@4be915c` звірена з GitHub; робоче дерево перед змінами було
чистим. PR#1 лишається draft, main не змінено. Попередні push40604109875 і
PR40604115901 успішні, але їхні результати не приписуються новому коду.
Актуальні CI-докази нового HEAD наведені у [PR#1](https://github.com/VolshebniKGG/YEBOYBot/pull/1).

Реалізовано завершену стабілізацію музичного плеєра:

- `/music queue` показує український стан і фіксовану підказку за дозволеним
  кодом помилки: мережа, TLS, rate limit, вхід, формат, JS runtime, таймаут,
  відтворення або загальна недоступність. Raw provider output/URLs/credentials
  не потрапляють у відповідь чи журнал. Класифікація ознак не доводить root cause.
- Worker читається потоково: stdin16KiB/stdout64KB. Oversize, timeout і
  cancellation завершують власний Pythonchild та дренують його pipes без
  необмеженого накопичення stdout. Інші процеси Python не завершуються.
- Навмисна зупинка повторно перевіряється до й після automaticconnect, включно
  з очікуванням REST/handshake. Пізнє власне з'єднання закривається без attach.
  Неправильний query відхиляється до connect; сервер звіряється з кешем до/після.
- Shutdown guard не дає відновити autojoin після unload. Власний SDKVoiceClient
  прибирає з'єднання при cancellation handshake. Незавершений teardown лишається
  під контролем після cancellation команди; повторний connect чекає його кінця.
  Від'єднання різних guilds не тримають спільний lock протягом native cleanup.

Підтверджені помилки відтворено через контрольовані REST/connect/close interleavings.
Додано **78 тестів:54 diagnostics +24 lifecycle**, загалом **1791**.
Цільовий набір command/lifecycle/diagnostics/engine: **212passed**,2.07s.
Незалежний review command/lifecycle: **90passed**,2.18s; файли reviewer не змінював.
Повний local Linux/Python3.12.14 у точному verboseCI режимі: **1619passed/172skipped**,
1791tests/0failures/0errors за37.14s. Пропущені PostgreSQL і nativeWindows;
Windows/PG перевіряються окремими CI jobs цього HEAD. Ruff/pipcheck/diffcheck
пройдено. Fresh pip-audit2.10.1/PyPI: **31distributions/0advisories/0skips**,pip26.2;
scope LinuxCore/runtime/dev/installer, не native/OS/legacy/Windowsresolution.
Залежності, schema2, конфігурація й кількість команд не змінені.

Окрема справжня cloudспроба metadata extraction першого автотреку повернула
код `network` із фіксованою українською підказкою; успіх отримання аудіо не
підтверджений. Реальні Discordvoice/Spotifycredentials/24hsoak не перевірялися.
Окреме завершення Deno-нащадків і підтверджений disconnect після native timeout
лишилися відомими межами; їх не видаємо за завершені цією ітерацією.
Для застосування: оновлення коду → **[2]** перезапуск, без нової sync чи міграції.
[Інструкція музики](RANK_MUSIC_UA.md).

---

# Поточна ітерація — ранг, голосовий XP і музика, 28.10.2026

База3ad0432, feature/core-v2, PR#1 draft, main без змін. Попередній CI40560868774
підтверджено success; його результати не приписуються новому коду.

Реалізовано:
- /rank повертає PNG500×188 з rank_background.png + gg.png masks/font.ttf,
  великим avatar і малою guildicon, двома XP/ranks/levels/progress. SQL snapshot
  одним statement, max2renderworkers, boundedCDN/UTF8/PNG/errors. Legacyфайли збережені.
- Голос5XP за60с спостереженої придатної присутності з≥2людьми; noAFK/mute/deaf,
  reset onshard/gateway/eligibilityloss, nocatchup. Text5/60s лишився. Levelsflag
  вимикає обидва; VoiceStates+voicecache безprivilegedMembers/Content. Speechнеаналізується.
- [26] explicit validated legacyJSON→SQLitev2, maxперcounter/idempotent,
  preserve timestamps, OS/maintenance/SQLlocks, verifiedbackup/rollback/sourcehash.
  Підтвердження перед записом; JSON не видаляється. PostgreSQLimportвідсутній.
- Opt-in /music join/play/queue/skip/stop/autoplay: FIFO50/20guilds/2extractors,
  ваш _autoplaylistдо10000rows, userpriority/preemptionAP, boundedprocesses,
  backoff/reconnect, ownednonblockingFFmpegcleanup. Черга лише в пам'яті.
- [25] налаштовує subsetoptions.ini таENABLE_MUSIC, optionalnativeFFmpeg/Deno
  лише після окремого consent/officialWinGet. CredentialsToken fallback при
  порожньому DISCORD_TOKEN; credentialsнепублікуються/непередаютьсявargv.
- YouTube/SoundCloud +textsearch черезpinnedyt-dlp/EJS+installedDeno/Node≥20;
  Spotifytrackofficialmetadata→YouTubematch, не originalSpotifyaudio.
- Voicecrypto depsPillow13/PyNaCl1.6.2/davey0.1.7/yt-dlp2026.10.26/EJS0.8.
  PyNaCl1.5 мав2advisories; виправлено, перевірено реальний SDKAEADroundtrip.
  Schema2/шістьтаблиць/міграції не змінено. Немає vendoredбінарників.

Локально Linux/Python3.12.14: **1541passed/172skipped**,1713tests/0failures/0errors,
29.79s. Це366новихтестів відносно1347на3ad0432; є actualPillow/SQLite/PG,
thread/cancel/cleanup, callback/permission negatives, replay/importbackup/rollback.
Повний набір з disposablePostgreSQL17.11 +nativepg_dump/pg_restore:
**1670passed/43Windows-skipped**,1713tests/0failures/0errors за92.68s.
Ruff/pipcheck/diffcheck пройдено. Freshpip-audit2.10.1/PyPI:31distributions,
0advisories/0skips,pip26.2; лише LinuxCore/runtime/dev/installer, не native/OS/legacy.

Початкові невдалі перевірки не приховані: завузький лімітAP2000 замінено10000
для реальногофайла2548rows; тест GroupCog виправлено доinstanceSDKAPI;
імітація завершення180s відокремлена від productionanti-restartloop;
тест cleanupчекаєконтрольований retryinterval, production1s збережено.
НовеtestAEADналаштовано черезSDKconnection.secret_key readonlyproperty.
Жоден сценарій/критичний assertion не прибрано для зеленого статусу.

Окрема мережна спроба metadata extraction першого запису вашого автоплейлиста
в cloud-середовищі не підтвердила успіх (MusicError). Unit/integration tests
не є доказом доступності конкретного YouTube відео чи відтворення в Discord.
Live Discordvoice, реальніSpotifycredentials, WinGetnativeinstall, ручнийWindows
та24hsoak ще не перевірені. Порядок перевірки — [RANK_MUSIC_UA.md](RANK_MUSIC_UA.md).
Код407d9d5 збережено в GitHub. Push40603362574/PR40603368622 затрималися
на повних suites: точний локальний -vv запуск підтвердив2,097,258символів в
одній nodeID-лінії, журнал5,398,189байт і1541pass/172skip за38.69s.
Причина — implicitpytestIDs із1–2MiBpayloads у rank_card/media_setup/music_options.
Додано короткі явні IDs; усі payloads, assertions,1713tests іCI-vv/JUnit збережені.
Повторний exact-CI local:1541pass/172skip за32.31s,222025байтlog замість5.4MB;
ще один16KBstreamURL testcase теж отримав короткийID,10negativecases пройшли.
Після fix новий CI перевіряється за новим SHA; функціональний код407d9d5 не змінено.
Докази завершення будуть у PR та фінальному звіті.

---

# Поточний стан Core V2 — 27.10.2026

Гілка feature/core-v2; main не змінено, PR #1 — draft. Нижче актуальний стан,
попередні звіти збережено в розгортуваній історії. Не переносіть старі CI totals
на нові коміти.

## Поточна ітерація: ідемпотентний /warn і безпечний список попереджень

База809db8a має [успішний CI40522292914](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40522292914),
усі9jobs. Це перевірка попереднього коду, не нових warning-змін. Відсоток
готовності встановлення/продукту не присвоюємо: ручний чистий Windows і live
Discord досі не перевірені. Незалежна функціональна робота продовжена.

- `/warn` тепер використовує shared ModerationService: свіжі REST guild/owner/roles,
  actor/bot/target, Moderate Members, захист себе/цього бота/власника та ієрархію.
  Власник обходить тільки власну ієрархію; bot rank перевіряється завжди.
  Це політика локального warning, не вимога mutation API Discord.
- `record_warning` атомарно додає warn/applied в існуючу v2_cases. Ключ — guild
  та interaction ID; immutable actor/target/action/reason перевіряються при replay.
  Повтор не створює другого запису, не змінює timestamps і не відновлює revoked.
  Changed payload/cross-action/cross-guild конфлікти не переписують дані.
- DB error/timeout може означати невідомий COMMIT: відповідь unconfirmed не
  стверджує відсутність запису. Cancellation поширюється; автоматичного retry немає.
  Warning не викликає Discord mutation/DM і не створює Discord Audit Log entry.
- Причина1–500 після trim без C0/C1/сурогатів. Старі UUID warning rows збережені;
  schema2/шість таблиць/dependencies/intents не змінені. Поточних цілей читаємо
  навіть при replay; після виходу цілі новий SQL не виконується, історія лишається.
  Нова ручна slash-команда має новий ID та створює окреме попередження.
- `/warnings`: десять активних записів за created/idDESC, приватно й без mentions.
  Escaped reasons≤300UTF16/title≤64 укладаються в aggregate6000. Повний текст
  зберігається в БД; /modhistory теж обмежує показ і не обіцяє повної причини.
  DB read має10s timeout та безпечне повідомлення. Read/revoke authorization
  `/warnings`/`/unwarn` поки cached; timeout/untimeout також мають старий lifecycle.
- Додано222 тести:94repository,87service,25commands,16integration.
  Є realSQLite/PG конкуренція, rollback/unknown ack після commit, revoked replay,
  legacy compatibility, cross-action collisions, SDK role/timeout/cache checks,
  lost followup без нового запису, bounded Unicode/Markdown outputs.

Локально Linux/Python3.12.14 повний набір: **1186passed/161skipped** за62.42s,
1347tests/0failures/0errors. Первинний full знайшов регістр української фрази
в історичному assertion; текст виправлено, assertions не прибиралися.
Окремий repository-набір на realSQLite/PostgreSQL:94passed/0skipped;
фінальний набір усіх нових тестів із PG — **222passed/0skipped** за14.68s,
JUnit222tests/0failures/0errors. Реальні Discord REST responses підмінено;
SDK-моделі, SQL-транзакції й конкурентні записи виконувались реально.
Ruff/pipcheck/diffcheck пройдено. Повторний Linux Core/runtime/dev/installer
pip-audit2.10.1:24distributions/0findings/0skips,pip26.2; не OS/interpreter/legacy/Windows/native.
Нові CI результати ef4b345 підтверджено окремо нижче.
Live Discord/ручний Windows/24×7 не виконувались.
Код, [MODERATION_UA.md](MODERATION_UA.md), README/Quickstart та «Код структура.txt» узгоджені.

### CI попереджень — ef4b345

[Push40560042495](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40560042495),154s,
і [PR40560052692](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40560052692),170s,
мають exactHEADef4b345f677da75bdbf4a638e6995c3e8b292037 та **усі9jobs success**.

| Job | Passed | Skipped |
|---|---:|---:|
| Повний Windows | 1229 | 118 PostgreSQL |
| Повний Ubuntu | 1186 | 161 PostgreSQL/Windows |
| Повний PostgreSQL | 1304 | 43 Windows |
| Focused Windows | 966 | 86 PostgreSQL/непідготовлений Core venv |
| Focused Ubuntu | 926 | 126 PostgreSQL/Windows |
| PostgreSQL maintenance | 1007 | 0 |
| Legacy Windows | 51 | 0 |
| Legacy Ubuntu | 49 | 2 Windows |
| Dependency audit | success | 0 distributions |

Windows підтвердив196newwarning passed/26PGskip, наявні39PS+12handoff tests,
перше й повторне install_v2.bat. NativePGclients17.11.
Artifacts push/PR: Windows12760055687/12761270008, PG12758063194/12760546256,
Ubuntu12759780222/12759990065, maintenance12760030577/12759516133.
AdvisoryJSON12757484452:24distributions/0findings/0skips/pip26.2;
SHA256ZIP збігається з API digest. Scope обмежений Linux Core/dev/installer.
Реальні Discord-запити/ручний clean-PC Python install/24×7 не виконувались.

## Попередня ітерація: встановлення на чистому Windows-ПК

База — a61ef0b, її [PR CI40511736421](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40511736421)
успішний, усі9jobs. Нові результати цієї ітерації перевіряються окремо.

- YEBOYBot.cmd тепер відкриває початкову панель через вбудований PowerShell5.1,
  навіть без Python. [1] дозволяє встановити відсутній Python3.12x64 для поточного
  користувача через офіційний WinGet та підготувати всі залежності Core V2.
- Пошук використовує py/python, Python registry та LocalAppData; після інсталяції
  повторюється без залежності від оновлення PATH у відкритій консолі.
  Автоматичне встановлення через сучасний py під час discovery вимкнено;
  початкові process-env flags відновлюються в finally. Немає прихованої згоди.
- WinGet має exactpackage/source/user/x64, штатну перевірку hash і явне погодження
  умов після вибору пункту. Немає довільного EXE-fallback, підвищення прав або
  постійної зміни ExecutionPolicy. За відсутності App Installer є офіційні посилання.
- Наявний Python bootstrap перевіряє venv/locks/pip26.2, dependencies/imports/pipcheck,
  зберігає config/data; schema2, Discord-команди й залежності не змінені.
  «Готово» означає локальне середовище, а не авторизований Discord-клієнт.
- [24] повертає повне встановлення; [5] оновлює бібліотеки. У Windows Python-меню
  спочатку завершується (службові коди24/25), щоб звільнити .pyd перед pip;
  після перевірки зовнішній launcher відкриває меню заново в тому самому вікні.
- install_v2.bat збережено для сумісності й CI; Ensure/Repair не встановлюють
  системний Python без вибору користувачем пункту в головному CMD.
- Додано 51 тест:12 Python-menu handoff та39 PowerShell/CMD сценаріїв.
  Native installer у тестах замінено boundary mocks; Windows-код виконує
  справжній PowerShell5.1, discovery перевіряє вже наявний CI Python.
  Є реальний CMD→готове.venv→меню smoke у fullWindows job та cancel на порожній
  PATH без Python, кирилиця/пробіли/апостроф/&, failure/retry/EOF/consent.
- Оновлено [CLEAN_PC_SETUP_UA.md](CLEAN_PC_SETUP_UA.md), README, quickstart,
  Windows-інструкцію та «Код структура.txt». Старі legacy-функції лишаються [22].

Локально Linux/Python3.12.14 після804d009: повний набір **990passed/135skipped** за83.38s,
1125tests/0failures/0errors. Пропущено PostgreSQL і Windows; WindowsPS5.1 тут
не виконувався. Ruff, pip check, diff check, UTF-8 BOM та відносні docs links пройдено.
Повторний pip-audit2.10.1/PyPI:24distributions/0findings/0skips,pip26.2;
лише Linux Core/runtime/dev/installer, не interpreter/OS/legacy/Windows/native.
Новий CI804d009 підтверджено окремо; докази a61ef0b на цей код не переносили.
Перший setup-кодac92c31: Windows full1failed/1032passed/92skipped,
focused1failed/769passed/60skipped. Єдина помилка — новий тест запускав cmd /c
через CRT list2cmdline quoting, яке не зберігало шлях з пробілами та&.
Виправлено саме cmd-specific /d /s /c outer/inner quote pairs; шлях із
кирилицею/апострофом/& та всі assertions збережено. Real prepared-Core menu smoke,
інші38PS та12menu tests у fullWindows пройшли. Fix-коміт804d009 має нові повні
успішні CI; початкові Windows runs не позначаємо зеленими.

### CI встановлення — 804d009

[Push40521511034](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40521511034)
завершився за168s, [PR40521522298](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40521522298)
за151s. Обидва exactHEAD804d00904767e1d0ce57df52904d412fea727937 мають
**усі9jobs success**.

| Job | Passed | Skipped |
|---|---:|---:|
| Повний Windows | 1033 | 92 PostgreSQL |
| Повний Ubuntu | 990 | 135 PostgreSQL/Windows |
| Повний PostgreSQL | 1082 | 43 Windows |
| Focused Windows | 770 | 60 PostgreSQL/непідготовлений Core venv |
| Focused Ubuntu | 730 | 100 PostgreSQL/Windows |
| PostgreSQL maintenance | 785 | 0 |
| Legacy Windows | 51 | 0 |
| Legacy Ubuntu | 49 | 2 Windows |
| Dependency audit | success | 0 distributions |

У повному Windows усі39PS/CMD та12menu tests пройшли, включно з порожнім PATH,
кириличним шляхом O'Connor& та справжнім меню з підготовленого Core venv.
Перше й повторне install_v2.bat підтверджено logs; nativePGclients17.11.
Повні Windows artifacts: push12741873227/PR12741414208; PG12742770119/12742212160.
AdvisoryJSON12742366786 розібрано:24distributions/0findings/0skips/pip26.2.
Ці результати не є actual WinGet download/install на порожньому Windows:
system-installer boundary у тестах підмінено; PS5.1/nativeCMD виконувались реально.

Реальне встановлення системного Python на порожній
Windows10/11, ручний double-click/logon і live Discord не виконані.
Потрібні інтернет та дозвіл Windows; токен власник вводить один раз через[12].
Відсутні Discord-модулі не стають реалізованими від встановлення пакетів.

## Попередня ітерація: підтверджене очищення повідомлень

База **4aa63d8** має обидва повні CI успішні (докази ban/unban нижче).
Додано `/clear amount reason` та `/clear-status request_id`: **18 slash-команд**.

- Preview читає одну сторінку до25 повідомлень перед початковим interaction ID.
  Відбираються лише звичайні/reply повідомлення молодші7діб; закріплені,
  системні й старі пропускаються з явними лічильниками. Пропущені не замінюються.
- Підтвердження приватне, прив'язане до автора/guild/channel, діє60секунд для
  прийняття claim. Кнопки confirm/cancel одноразові; серверний SQL CAS блокує
  повтор, гонку cancel/confirm і повтор після перезапуску, незалежно від UI.
- Незмінний version1 payload з ID/причиною зберігається в існуючому v2_jobs.body
  (≤1000символів), scope/expiry — у typed columns. Причина1–200символів без
  керівних символів. До5 живих підтверджень на guild/actor; cap має зрозумілу помилку.
  Нових таблиць/міграцій/dependencies/intents немає; schema2 та шість Core-таблиць
  збережені. Clear states clear_* ніколи не стають pending-нагадуваннями.
- Перед preview/claim і API заново перевіряються ролі/власник/actor/bot/channel
  overwrites: Manage Messages, View Channel, Read Message History. Підтримано
  лише regular TextChannel, без threads/news/forum. Перевіряються лише схвалені
  ID, повторна вибірка історії під час execute заборонена. Stale/pinned/old/404
  під час validate зупиняє весь запит до write. Атомарного pin/permission+delete
  Discord не дає; зміна після останньої перевірки все ще можлива.
- Для одного ID reason передається direct pinned HTTP delete_message: SDK2.7.1
  інакше губить reason у single-ID delete_messages. Для2–25 використано SDKbulk.
  Є audit prefix clear/actor/channel, encoded header≤512, повна причина в БД.
- SDK exception/timeout/cancel після claim лишає unknown/running/pending без
  автоматичного повтору. SDKsuccess+DBfailure/CAS0 явно розрізняються.
  Підтвердження стосується запиту дляNID, не незалежного підрахунку фактичних
  нових видалень. /clear-status читає тільки власний receipt у тому самому
  каналі й не потребує права на нове видалення; після restart кнопки не
  відновлюються, job не продовжується. Expired підтвердження не приймаються.
- Clear receipts — окремий manual-job audit; /modhistory лишається історією
  member moderation cases. Скасування не відкочує вже надіслану API-операцію.
- Оновлено «Код структура.txt», README, [CLEAR_UA.md](CLEAR_UA.md) та інструкції
  Core V2. Нові тести включено у focused Windows/Linux/PG jobs; старі перевірки збережено.

Під час незалежного перегляду виправлено expiry race: час оновлюється ПІСЛЯ
отримання SQL lock/row lock, тож підтвердження, яке чекало блокування, не
приймається після deadline. Додано6 регресій (claim/cancel/prepare × SQLite/PG)
із реальними locks та синхронізацією подіями без sleep; усі6 перевірено на обохБД.

Новий набір містить **1074 тести, 353 доданих**: 82 plan, 101 repository,
43 service, 58 adapter, 59 UI та 10 integration. Локально Linux/Python3.12.14
після expiry fix: SQLite/default **977 passed, 97 skipped** за100.00s,
JUnit:1074tests/0failures/0errors.
Окремо відтворено й виправлено негативний PostgreSQL-тест oversize-body:
SQLAlchemy/asyncpg повертає DBAPIError зі SQLSTATE22001, а не DataError.
Тест вимагає саме22001 і перевіряє незмінність початкового рядка після rollback;
цільовий повтор —1passed. Перший локальний повний PostgreSQL/native17.11 прогін
зі старим очікуванням класу винятку мав1068passed/1failed/5skipped за648.51s.
Його не позначаємо зеленим і не видаємо за остаточну перевірку виправленого тесту.
Повний повтор виправленого набору підтверджено в новому CI нижче.
Окремий фінальний локальний набір усіх шести clear test-файлів із реальними
SQLite/PostgreSQL після виправлення — **353passed,0skipped** за90.68s;
JUnit353tests/0failures/0errors. Native17.11 dump/restore сценарії початкового
повного прогону пройшли; його єдина помилка була очікуванням driver exception.
Ruff, pip check та diff check успішні. Посилання документації та UTF-8 карту
коду перевірено; відсутніх відносних посилань немає.
CVE-аудит27.10.2026 pip-audit2.10.1/PyPI:24distributions,0findings,0skipped,pip26.2;
Linux Core/runtime/dev/installer, не legacy/OS/native/Windows/tool resolution.
Реальне видалення в Discord, ручний Windows10/11 та24/7 не виконувались.

### Підтверджений CI коду 8bb380f

[Push40510725655](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40510725655)
завершився за102s, [PR40510734573](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40510734573)
за124s. Обидва exactHEAD8bb380fe788a2a27fe1623910c085c25506ca774 мають
**усі9jobs success**, зокрема виправлений oversize-body тест.

| Job | Passed | Skipped |
|---|---:|---:|
| Повний Windows | 982 | 92 PostgreSQL |
| Повний Ubuntu | 977 | 97 PostgreSQL/Windows |
| Повний PostgreSQL | 1069 | 5 Windows |
| Focused Windows | 720 | 59 PostgreSQL |
| Focused Ubuntu | 717 | 62 PostgreSQL/Windows |
| PostgreSQL maintenance | 785 | 0 |
| Legacy Windows | 51 | 0 |
| Legacy Ubuntu | 49 | 2 Windows |
| Dependency audit | success | 0 distributions |

Windows logs підтверджують перше й повторне install_v2.bat; native
pg_dump/pg_restore17.11. PR advisory JSON artifact12737156079 розібрано:
24distributions/0findings/0skips/pip26.2 у зазначеному вище Linux scope.
Log/JUnit artifacts (push/PR): Windows12737241236/12736768277,
Ubuntu12736219875/12737705352, PG12736274950/12736129898,
PGmaintenance12736124838/12737131720, audit12737415845/12737156079.
Це автоматичні Windows CI докази, не ручний Windows/logon або live Discord.

## Попередня ітерація: ban/unban та актуальна карта коду (4aa63d8)

Обидва повні CI **4aa63d8** успішні:
[push40375663900](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40375663900),127s;
[PR40375699446](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40375699446),125s.
У кожному всі9jobs success: Windows661/60, Ubuntu656/65, PostgreSQL716/5,
focused Windows399/27, Ubuntu396/30, PGmaintenance432, legacy Windows51,
Ubuntu49/2. Nativeclients17.11. Audit artifact12673599319:24distributions,
0findings/0skips/pip26.2. Ці докази стосуються саме ban/unban+карти, не clear.


База — **083d4bc**, чиста feature/core-v2; PR1 draft, main не змінено.
Перед роботою підтверджено [успішний CI083d4bc](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40371041490).
Це доказ базового стану, не результати нових змін.

- Core V2 реєструє **16 slash-команд**. Додано `/ban user_id reason` та
  `/unban user_id reason`; потрібен Ban Members у автора й бота.
- `ModerationService` спільний для kick/ban/unban/history; попередні Python
  імена KickService/KickResult збережені як aliases для сумісності.
- Свіжі REST owner/actor/bot/roles перевіряються до claim і повторно після commit.
  Ban поточного учасника перевіряє ієрархію автора/бота; non-member допускається
  лише при exact404/10007 для цілі. Помилки actor/bot/guild не маскуються.
- Ban явно передає `delete_message_seconds=0`: повідомлення зберігаються.
  Unban не потребує поточного членства, читає бан за ID; лише404/10026 означає
  відсутній запис ДО write, що блокує unban. Інші read failures теж не пишуть.
- Atomic claim звіряє guild/interaction/action/actor/target/reason. Конкурентні
  однойменні й cross-action запити мають одного власника; інші не переписують
  справу й не викликають повторну API-дію. Після API exception/timeout/cancel —
  pending; API success+DB error/CAS0 не видається за невдалу Discord-дію.
- Немає application retry або автоматичного replay pending. SDK retries і
  race між role/ban read та write лишаються межами Discord API. Applied означає
  підтверджений запит, не доказ першого ban чи exactly-once. Історія приватна.
- Нових application dependencies, intents, DDL/міграцій немає; schema2 збережено.
  Claim додатково відхиляє NUL і невідомі/non-string action до SQL.
- «Код структура.txt» замість старого E:\legacy дерева описує реальні Core V2,
  launcher, legacy, flows, SQL/schema, локальні приватні файли, тести й docs.
  Перевірено76 канонічних шляхів; дані/секрети/згенеровані pyc не включено в дерево.
- [Модерація](MODERATION_UA.md), [аудит](PROJECT_AUDIT_UA.md),
  [наступний промт](NEXT_DEVELOPMENT_PROMPT_UA.md); README має посилання на карту.

Локально Linux/Python3.12.14: **721 tests,224 нові** (83 service,38 adapter,
49 slash binding,15 cross-layer integration,39 repository regressions).
- PostgreSQL17.11/native: **716 passed,5 Windows skipped**,210.82s.
- Остаточний SQLite/default: **656 passed,65 skipped**
  (60 PostgreSQL/5 Windows),62.87s.
- Попередній default до15 cross-layer tests:641 passed,65 skipped,117.94s;
  усі нові15 та рештаmoderation tests268passed перевірені окремо. Це не підміна
  остаточного full run, який фіксується окремо.
- Ruff, pip check, diff check та посилання українських docs пройшли.
- Повторний pip-audit2.10.1/PyPI:24distributions Core/runtime/dev/installer,
  **0 findings,0 skipped,pip26.2**, exit0. Це не аудит legacy/OS/native/Windows.
- Нові test files включено в обидва focused Windows/Linux та PostgreSQL
  maintenance invocations, усі full jobs/checks/log artifacts збережено.
- Новий CI після публікації перевіряється за новим SHA; базові результати
  083d4bc не приписуються змінам ban/unban.
Live Discord, ручний Windows10/11/logon та24/7 не виконувались. Clear і перенесення
старих warn/timeout/untimeout lifecycle залишаються наступними етапами.

## Попередня ітерація: kick, приватна історія та захист встановлювача

**Повний CI коду c86c177 успішний:** [push run40370321990](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40370321990),
усі9 jobs,90s загалом. [PR run40370330021](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40370330021)
також усі9 jobs success,130s. Тести не видалено, дані/межі/timeouts збережені.

| Перевірка | Підтверджений результат |
|---|---|
| Windows full SQLite | **454 passed,43 PostgreSQL skipped**,40.41s |
| Ubuntu full SQLite | **449 passed,48 skipped** (43 PostgreSQL/5 Windows),10.56s |
| Ubuntu full PostgreSQL17 | **492 passed,5 Windows skipped**,29.78s |
| Focused Windows moderation/installer | **192 passed,10 PostgreSQL skipped** |
| Focused Ubuntu moderation/installer | **189 passed,13 skipped** |
| PostgreSQL maintenance + moderation | **208 passed** |
| Legacy Windows / Ubuntu | **51 passed /49 passed,2 skipped**, installs/offline cogs success |
| Dependency audit | **24 distributions,0 findings,0 skipped,pip26.2**, Linux resolution |

Ruff і artifact upload пройшли. Full jobs мають retained pytest logs/JUnit.
Push artifacts: SQLiteWindows12672152245, SQLiteUbuntu12672261743,
PostgreSQL12671563716, audit12672037335. PR: SQLiteWindows12671263997,
SQLiteUbuntu12672166959, PostgreSQL12672791734.
Windows runner — сервер GitHub Actions; ручний double-click/logon на Windows10/11,
справжній Discord API та 24/7 лишаються неперевіреними.

Після коротких parameter IDs max test-log line: Windows387, PostgreSQL385,
Ubuntu386 символів замість524410. У Ubuntu перехід malformed→oversize XML→
наступний тест має інтервали2.4/2.6ms замість сотень секунд навколо гігантського
рядка. За незмінних payloads/assertions workflow завершився за90s. Ці докази
підтверджують виправлення надмірного test-log output; не приписуємо йому всі
історичні Windows cancellations без їхніх журналів.

Коміти цієї ітерації: **1ef3ebd** moderation, **b446028** installer/CVE,
**f5685f0** Windows path assertion, **c86c177** bounded test IDs/CI evidence.
Наступний docs-only коміт фіксує ці результати, production-код не змінює.
Нижче збережено проміжні фактичні перевірки й знайдені/виправлені failures.

- Зареєстровано **14 slash-команд**, нові **/kick** і **/modhistory**.
- KickService відокремлює use-case від Discord adapter і guild-scoped SQL.
  Fresh detached Guild REST snapshot перевіряє owner, actor/bot permissions,
  timeout, ієрархію з tie за role ID та невідомі role IDs; спільний кеш не змінюється.
- Перед DELETE атомарно commit-иться case claim. Ключ із guild/interaction ID
  блокує повторний application kick, зокрема після restart. Параметри запиту
  мають збігатися; чужий guild або інший payload справу не перезаписує.
- Права/ціль повторно перевіряються після claim. API exception/timeout/cancel
  залишає результат непідтвердженим; DB failure/CAS0 після API success не видає
  дію за невдалу. Автоматичного replay pending немає. SDK transport retries
  збережені, exactly-once DELETE і атомарна role-check+API не обіцяються.
- /modhistory має current permission check, user ID колишнього учасника,
  state filter, стабільний порядок і сторінки по5; відповіді ephemeral, mentions
  вимкнені, причини екрановано й обмежено з урахуванням UTF-16/emoji.
  Старий failed показується як запис помилки, а не доказ скасованої дії.
- Схема2 й міграції не змінювались, нових залежностей/intents немає.
  Audit-header має case/actor prefix і bounded URL-encoded причину; повний текст у БД.
- Додано focused moderation/installer Windows/Linux CI, SQL integration в PG job
  та окремий Linux CVE audit з JSON artifact; усі full jobs/log/JUnit збережено.
- [Інструкція](MODERATION_UA.md), [аудит](PROJECT_AUDIT_UA.md),
  [наступний промт](NEXT_DEVELOPMENT_PROMPT_UA.md).

Локально Linux/Python3.12.14: **497 tests**, **150 нових**
(132 moderation +18 installer regressions).
- PostgreSQL17.11 + native tools: **492 passed, 5 Windows-only skipped**, 274.24s.
- SQLite/default без PostgreSQL: **449 passed, 48 skipped**
  (5 Windows +43 PostgreSQL), 72.89s; повтор без конкурентних heavy tasks.
- Є real SQLite/PostgreSQL atomic claim/guild/history checks і real discord.py
  models з mocked HTTP. Конкурентні duplicate claims мають одного власника;
  перевірені role/owner changes, permissions, ambiguous API404/5xx/network,
  cancellation, DB failures, CAS0, Unicode limits та private response binding.
- Ruff, pip check, diff check пройшли. Performance baseline не вимірювався.
- CVE-аудит `pip-audit2.10.1`/PyPI: 24 встановлені distributions Core/dev/installer,
  без пропусків. Для pip25.0.1 повернуто6 унікальних advisory IDs (12 raw записів);
  після pip26.2 — 0 findings, exit0. Інші23 не мали відомих advisories.
  Legacy/OS/native/Windows resolution цим аудитом не охоплені.
  [Точний набір, IDs та методика](DEPENDENCY_AUDIT_UA.md).
- Обидва bootstrap перевіряють власний ізольований venv до pip, прибирають
  package redirects, зберігають proxy/CA, оновлюють pip26.2 до app dependencies
  навіть при чинному marker. Failure блокує install і скидає marker; системний
  Python не змінюється. Старе Core-меню доступне для зупинки[3]/repair[5].
- Реальні fresh/repeat Core і legacy installs у різних venv у Linux-шляху з
  кирилицею/пробілами пройшли, обидва pip26.2. Discord login не виконувався.
- Один initial default run:448 passed/48 skipped/1 subprocess timeout15s
  у test_process_exits_on_control_failure_and_releases_lock під паралельним
  install+PG навантаженням. Три діагностичні запуски завершились за3.03–7.44s;
  faulthandler показав імпорти SQLAlchemy/pytest, close<3ms. Увесь managed suite
  без змін:10 passed/5.38s. Таймаут15s і всі тести збережено; це не доказ
  причини історичного Windows CI. Повний default повторено без heavy-конкуренції.
- На базовому fafa177 full Ubuntu309/38, PostgreSQL342/5, PG maintenance76 та
  legacy обох ОС підтверджені; Windows full скасовано під час pytest, logs404
  і відсутній artifact не дозволяють назвати конкретний тест/стек. Новий CI
  перевіряється окремо за SHA; старі результати новому коду не приписуються.


CI коду **b446028**, [PR run40368711425](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40368711425):
- Focused Ubuntu: **189 passed, 13 skipped**; PostgreSQL maintenance: **208 passed**.
- Legacy first/repeat/install/import/cogs smoke: Windows **51 passed**, Ubuntu
  **49 passed, 2 skipped**. Справжній Discord login не виконувався.
- Dependency-audit: artifact12671432239 містить **24 distributions,0 findings,
  0 skipped,pip26.2** для Linux Core/runtime/dev/installer resolution.
- Focused Windows: **191 passed,10 skipped,1 failed** у test_bootstrap.py:127.
  Єдиний failure — порівняння C:\Users\RUNNER~1 та C:\Users\runneradmin для
  тієї самої temp-папки. Production install й moderation перевірки пройшли.
  Тест виправлено через Path.resolve() зі збереженням цільового venv і pip flags;
  тести не видалено, Windows результат після виправлення перевіряється окремо.
- Повні jobs на момент цієї фіксації ще виконуються; ці focused results не є
  підтвердженням повного зеленого workflow.


Повні Linux jobs b446028 також завершились успішно за журналами обох подій:
SQLite **449 passed,48 skipped** (pytest11.04–11.43s), PostgreSQL **492 passed,
5 skipped** (pytest31.10–31.40s). PR jobs121733311160 та121733311057.
Вивід має рядок524410символів із oversized XML nodeid; timestamps навколо нього
мають розриви216–258s, тоді як сам pytest звітує секунди. Це виміряний кандидат
затримки console/log processing, а не підтверджений runtime deadlock.
Windows full на17:36UTC ще не завершений. Зміна коротких IDs перевіряється
новим CI; не називаємо причину всіх історичних скасувань доведеною.

Після Windows path fix, код **f5685f06**, [PR run40369126215](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40369126215)
і [push run40369117626](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40369117626):

| Перевірка | Підтверджено в обох запусках |
|---|---|
| Focused Windows moderation/installer | **192 passed,10 skipped** |
| Focused Ubuntu moderation/installer | **189 passed,13 skipped** |
| PostgreSQL maintenance + moderation SQL | **208 passed** |
| Legacy Windows | **51 passed**, first/repeat/import/offline cogs success |
| Legacy Ubuntu | **49 passed,2 skipped**, first/repeat/import/offline cogs success |
| Linux dependency audit | **24 distributions,0 findings,0 skipped,pip26.2** |

PR artifacts: moderation-windows12670663745, moderation-ubuntu12670679739,
postgres-maintenance12671751971, dependency-audit12672290753. Ruff успішний.
Це GitHub Windows runner, не ручний Windows10/11 або живий Discord.
Повні SQLite Windows/Linux і PG jobs на17:34UTC ще виконуються; їхній успіх
не приписується focused jobs. Старі runs не скасовувалися. Конкретного тесту/стека
їх затримки з доступних журналів поки не отримано; діагностика залишається P0.

Точний CI shell pipeline з -vv/tee/JUnit локально завершився: **449 passed,
48 skipped за43.82s**, без зависання завершення. Виявлено окрему виміряну
проблему журналу: oversize XML у параметричному ID створював рядок524381символ,
повний log600529bytes. Задані короткі IDs для XML і SID CSV payloads; тестові
512KiB/16KiB дані й усі assertions збережені. Targeted autostart: **47 passed,
1 Windows skipped**. Collect усіх497 tests після зміни не має nodeid>1024символів.
Це виправлення читабельності логів, не доведене усунення CI затримки.
Production-код після b446028 не змінювався; наступні зміни — test path/IDs
і фіксація фактичних доказів. CI останнього follow-up потрібно звіряти за SHA.

Live Discord kick/permissions/command visibility, ручний Windows10/11 та 24/7
не перевірено. Ban/unban/clear, автоматичне pending reconciliation та виправлення
старих warn/timeout/untimeout lifecycle залишаються наступними етапами.
Переваги над ProBot без порівняльних вимірювань не заявляються.

## Попередня ітерація: PostgreSQL backup → upgrade → recovery (fafa177)

- **[23]** у YEBOYBot.cmd: read-only plan, native Core-backup, явна міграція1→2,
  restore власної копії тільки в окрему попередньо створену порожню БД.
- SQLite workflow/схема2 і legacy запуск збережено. Login не застосовує міграції.
- До DDL: валідація стандартної public Core-схеми, OS/advisory/SHARE table locks,
  native custom archive, SHA-256/manifest/TOC. Дані двох guild, pending cases,
  timestamps, XP, jobs, panels та прості btree-індекси зберігаються.
- Кастомні залежності (types/functions/defaults/FK/CHECK/partition/inheritance/
  RLS/collations/opclasses/access methods) відхиляються до зміни даних.
  Це backup шести Core-таблиць, не всього PostgreSQL сервера/ролей/файлів.
- Credentials передаються native tools через очищене child environment;
  немає password/URL в argv, raw stderr або JSON-звіті. .env/token не копіюються.
- Recovery не використовує clean/drop, не створює БД й не перезаписує поточну.
  Config URL не перемикається автоматично. Після COMMIT connection loss —
  unknown_commit, а не вигаданий rollback. Ctrl+C дочікується bounded worker.
- Звіт data/v2/postgres-report.json містить phase/status/backup/error_type.
  PostgreSQL Git-updater, SQLite→PG import і складні custom schemas не реалізовані.
- Додано окремий PostgreSQL maintenance CI job; повні Windows/Linux/PG checks
  збережені. Нових Python application dependencies немає; потрібні штатні native tools.
- [Інструкція](POSTGRES_RECOVERY_UA.md), [матриця з ProBot](COMPETITIVE_SCENARIOS_UA.md),
  [наступний промт](NEXT_DEVELOPMENT_PROMPT_UA.md).

Перевірено локально Linux/Python3.12.14: **347 tests**, із них **76 нових**.
- Без PostgreSQL: **309 passed, 38 skipped** (5 Windows +33 PG).
- З PostgreSQL17.11: **342 passed, 5 Windows-only skipped**.
- Реальні pg_dump/pg_restore17.11 запускалися в disposable Docker через stdio
  wrappers; SQLAlchemy/asyncpg — у Linux host до loopback TCP контейнера.
  Test fixture створює/видаляє лише власні нові yeboy_test_* БД.
- Перевірено full payload/index preservation, v1/v2 native restore, rollback після
  DDL failure, stale plan/config switch, unknown marker, locks, custom dependencies,
  backup/file/credentials failures, commit/report ambiguity й cancellation drain.
- Ruff, pip check та diff check успішні. pip check не є CVE-аудитом;
  CVE-аудит і performance baseline у цій ітерації не виконувались.
- CI a5dd3ef зупинився до PG-тестів: Ubuntu apt не мав postgresql-client-17.
  CI-підготовку виправлено: tools/ci_pg_clients.py використовує реальні native17
  binaries із вже запущеного disposable PG service container через stdio, без
  установки системних пакетів runner. Тести й version checks не послаблено.
- CI виправленого коду перевіряється після публікації. До змін faf3385 мав Ubuntu full
  261/10, legacy Windows45 і Linux43/2 success; повні Windows/PG jobs не завершилися
  на момент огляду, їхній успіх новому коду не приписується.

CI коду **f14273a**, [run40273263180](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40273263180):
**postgres-maintenance — 76 passed**, native pg_dump/pg_restore17.11; JUnit/log
опубліковані в artifact postgres-maintenance. Legacy Windows45 і Ubuntu43/2
також success. Повні SQLite Windows/Linux і PG suites на момент фіксації ще
виконуються; успіх focused76 не видається за повний зелений workflow.

Native PG workflow на Windows, production TLS, live Discord, ручний Windows10/11
та 24/7 залишаються неперевіреними. Порівняльних вимірювань ProBot немає.
Наступний функціональний етап: модерація з permissions/hierarchy/guild isolation,
підтвердженням clear та діагностикою pending без сліпого повтору Discord API.

## Попередня ітерація: безпечний legacy entrypoint (faf3385)

- run.bat замінено thin UTF-8/CRLF входом до stdlib Python controller.
  Видалені registry edits, global uninstall/cache purge/break-system-packages.
- [22] Core V2 відкриває те саме legacy-меню. Встановлення й виконання тільки
  в .venv-legacy; .venv-v2 і системні пакети не змінюються. Невідомі дані не видаляються.
- Окремі pinned requirements: discord.py[voice]2.7.1 відповідає legacy async add_cog;
  історичний py-cord root requirements більше не встановлюється цим входом.
- Pre-import guard перевіряє Python/venv, system/user site, сторонні .pth,
  py-cord, точні версії та critical imports; requirements stamp не приховує drift.
- OS locks захищають від дубля legacy й пакетних змін під час його роботи.
  Foreground Ctrl+C, зрозумілі помилки/exit codes; сторонні процеси не завершуються.
- Import bot більше не читає token/config і не очищує logs. Cogs завантажуються
  ліниво; Music тільки opt-in. Логи append, Credentials приховані й у traceback.
- Додано Windows/Linux legacy-install CI: first/repeat install, probe й real cogs
  offline smoke. Наявні SQLite/PG/Windows checks не прибиралися.
- [Інструкція](LEGACY_LAUNCHER_UA.md), [аудит](PROJECT_AUDIT_UA.md),
  [наступний P0-промт](NEXT_DEVELOPMENT_PROMPT_UA.md).

Локально Linux/Python 3.12.14: **261 passed, 10 skipped** із 271 tests:
5 native Windows (включно з двома новими CMD), 5 PostgreSQL. Додано 45 tests;
це 43 passed + 2 Windows skipped у цій ітерації, а не повторення старого звіту.
Ruff, pip check в обох окремих venv та diff check пройшли. CVE-аудит не виконувався.
Реальні legacy dependencies installed; repeat install і critical imports пройшли.
Disposable source copy завантажила справжні Moderation/User/Help/RankCog,
перевірила commands, optional-music skip і client/task cleanup без Discord login.
Без DLC assets використано fallback; rendering/реальні команди не перевірялися.
PostgreSQL suite локально в цій ітерації не запускали — змін БД немає.

CI коду **a6c8e49**, [run40269481755](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40269481755):
- **Windows Server 2025 / Python 3.12.10:** legacy first/repeat install, probe,
  real cogs offline smoke та **45 passed**. Обидва native CMD тести (missing Python,
  кирилиця/пробіли/cwd/exit code) пройшли. Ручний Windows 10/11 double-click не перевірявся.
- **Ubuntu:** legacy install/smoke та **43 passed, 2 Windows-only skipped**.
- Test logs/JUnit опубліковані як legacy-windows-latest і legacy-ubuntu-latest
  artifacts. Повні SQLite/PG/Windows jobs на момент фіксації ще виконувались;
  цей результат підтверджує саме legacy-ітерацію, не повний зелений workflow.

Попередній CI на **8b1897a**, run40231450946: Ubuntu SQLite **258 passed/10 skipped**,
PostgreSQL **263 passed/5 skipped**, legacy first/repeat install/probe/offline cogs —
success на Ubuntu. Windows legacy install відмовив на штатному executable-directory
у sys.path; обробка помилки додатково виявила cp1252 UnicodeEncodeError, бо -I
ігнорує PYTHONUTF8. Обидві причини виправлено: exact base-prefix дозволений поряд
зі stdlib, сторонні .pth досі відхиляються; stdin/stdout/stderr явно UTF-8.
Три subprocess regressions примусово використовують ASCII для menu/check/bot errors.
Ці нові результати не приписуються старому SHA; новий CI перевіряється окремо.

Повний Windows job121283871503 на 8b1897a скасовано під час pytest; dependencies,
first/repeat Core install, pip check та Ruff успішні. Логи Windows pytest повертають
404 BlobNotFound; конкретний тест/стек і причина затримки не встановлені.
Додано збереження unbuffered pytest.log/JUnit як 7-денні CI artifacts (always),
та окремі legacy regression tests у legacy job обох ОС. Повний suite не скорочено.
Ручний Windows 10/11, live Discord/music та 24/7 не підтверджені. Legacy
all-intents/TLS/hardcoded paths, рольова безпека команд і неізольовані legacy JSON
залишаються окремими ризиками.
Наступна P0-ітерація: native PostgreSQL backup → upgrade → restore зі збереженням
даних; наявна PostgreSQL v1 досі заблокована до цього workflow.

## Попередня ітерація: версійовані міграції (2fc94e8)

- Схема **2**, реальна впорядкована SQLite ревізія **002_case_updated_at**.
- [21] читає план; після МІГРУВАТИ та зупинки [3] робить перевірену копію,
  застосовує DDL/data/marker в транзакції й записує migration-report.json.
- Налаштування, XP, jobs, cases, role-panel дані й індекси зберігаються.
  updated для старих справ = created, не реконструйований історичний час.
- Backup прив'язаний до одного джерела; stale plan/config/file відхиляються.
  Помилки до commit відкочуються; помилка звіту після commit не приховує applied DB.
- Restore v1 створює страховий snapshot v2; повторна v2 migration — no-op.
- Нові БД SQLite/PostgreSQL — v2. Наявна v1 не оновлюється при запуску;
  DB preflight виконується до Discord login, MigrationRequired не повторюється.
- [4]/doctor показують потребу [21]; manifest format1 підтримує фактичну schema1/2.
- PostgreSQL v1 upgrade через меню **не реалізовано**: потрібен native pg_dump/
  verification/upgrade/recovery workflow. Це обмеження rollout для наявної PG v1.

Документи: [Міграції](MIGRATIONS_UA.md), [повний аудит](PROJECT_AUDIT_UA.md),
[наступний промт](NEXT_DEVELOPMENT_PROMPT_UA.md).

Локальна перевірка попередньої ітерації (Linux/Python 3.12):
- SQLite: **218 passed, 8 skipped** (3 Windows-only + 5 PostgreSQL-only).
- PostgreSQL 17.11 у disposable container: **223 passed, 3 Windows-only skipped**.
- Разом 226 сценаріїв, **43 нових** відносно попередніх 183. Це не сума jobs.
- Ruff, pip check, compileall і diff check успішні. CVE audit не виконувався;
  pip check підтверджує сумісність установлених залежностей, не відсутність CVE.
- Є повні real SQLite payload/index checks, backup+SHA, failed DDL rollback,
  restore1 зі safetycopy2, stale-plan/config race, failed report aftercommit,
  no-Discord-login for MigrationRequired. Користувацьку БД не мігрували.
- Source-pinning race знайдено під час review: `_create` тепер бере перевірений
  source, у restore safetybackup також прив'язаний до target; окремий regression
  змінює DATABASE_URL під час backup і підтверджує відсутність DDL в обох БД.
- Нових application dependencies немає. CI нового коміту перевіряється окремо;
  старі затримані jobs не є доказом успіху нового коду.
Live Discord, ручний Windows 10/11/logon і production 24/7 не підтверджені.
Порівняльних вимірювань з ProBot немає.

<details>
<summary>Історія попередніх ітерацій (результати прив'язані до зазначених SHA)</summary>

# Стан Core v2

Оновлено: 2026-10-25. Робоча гілка: `feature/core-v2`.

> Актуальний результат: bootstrap перевірено в CI Windows/Linux, див. останній розділ.
> Повний Control Center ще не завершено; матриця — LAUNCHER_AUDIT_UA.md.

## Історичні факти попередніх ітерацій

Середовище ChatGPT залишається offline (409 environment_offline). Повторний вхід з
телефона не відновив його. GitHub читання й запис доступні, тому розробку продовжено
через GitHub. Нове ядро відновлено в окремому пакеті `core_v2/`.

Попередні 33 локальні тести стосувалися втраченої локальної версії, а НЕ цього коду.
Їх не слід цитувати як результати поточної гілки. Старий main.py та README збережені.

## Реалізовано в цій ітерації

- Окремий запуск python -m core_v2, discord.py, AutoShardedBot, мінімальні intents.
- SQLite/PostgreSQL, атомарний XP з cooldown, дані ізольовані через guild_id.
- 12 slash-команд: статус, levels, rank, leaderboard, warn/warnings/unwarn,
  timeout/untimeout, remind/reminders/cancel-reminder.
- Перевірки ролей і дозволів модераторів, захист від масових mentions.
- Збережені нагадування, lease/retry, перевірка доступу автора перед доставкою.
- Обмежені кеші, ротація логів, приховування токена та URL БД, OS instance lock.
- Windows install/start, offline doctor, перевірене SQLite backup API.
- Автоматичні тести та workflow Windows/Ubuntu/PostgreSQL.

## Перевірки

Перевірено код коміту `cbe31a22a234cd0bec915b8f6398e6d3a29b84b8`.
GitHub Actions: https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40141653388

| Середовище | Результат |
|---|---|
| Ubuntu, Python 3.12, SQLite | 20 passed; Ruff і pip check успішні |
| Windows runner, Python 3.12, SQLite | 20 passed; Ruff і pip check успішні |
| Ubuntu, Python 3.12, PostgreSQL 17 | 20 passed |

Це 20 однакових тестів у трьох конфігураціях, не 60 різних тестів.
Є попередження audioop deprecation від discord.py на Python 3.12.
Перший CI знайшов Windows PermissionError при читанні заблокованого байта;
виправлено перевіркою розміру через fstat і закриттям handle при помилці.
Окремий тест перевіряє втрату членства у приватній гілці перед доставкою нагадування.

Підключення до живого Discord і навантажувальних тестів цього коду ще не було.
Draft PR: https://github.com/VolshebniKGG/YEBOYBot/pull/1
У main зміни не злиті; новий код та інструкції доступні в feature/core-v2.
Наступний коміт із цим звітом змінює лише документацію, перевірений Python-код той самий.

Відстежуваний файл `.cache` видалено з гілки: попередній аудит виявив у ньому
непорожній access_token. Історія Git не очищена; якщо credentials ще чинні,
їх потрібно відкликати у провайдера. Значення токена не використовувалося.

## Далі за пріоритетом

1. Пройти новий CI та ручний сценарій із docs/CORE_V2_QUICKSTART_UA.md.
2. Додати модерацію kick/ban, аудит подій, автомодерацію, welcome, авторолі.
3. Додати голосовий XP і стійкі рольові панелі.
4. Ізолювати та інтегрувати музичний модуль: per-guild queue, таймаути, FFmpeg.
5. Безпечний імпорт старих JSON із явним вибором сервера.
6. Retention завершених jobs/cases, індекси за результатами профілювання,
   версійовані міграції схеми, health/metrics, PostgreSQL load tests.
7. Розподілені shard workers та координація, тільки після вимірювань.
8. API конфігурації й вебпанель з Discord OAuth2 та перевіркою прав на сервері.

Сайт, музика та готовність до тисяч серверів поки НЕ реалізовані/не підтверджені.
Налаштування automod/welcome/autorole і таблиця role panels — резерв схеми, не активні функції.

## Як відновити роботу ChatGPT

Помилка environment_offline означає відсутність підключеного виконавчого середовища.
Права GitHub цього не виправляють. Якщо інтерфейс пропонує повторно підключити/створити
середовище, скористайтеся цим; інакше відкрийте нове завдання з репозиторієм та гілкою
feature/core-v2. Якщо помилка повторюється, передайте підтримці точний текст 409
environment_offline. Не потрібно скидати Discord token через цю помилку.

## Керований CMD запуск — 25.10.2026
Середовище виконання доступне. Використовується актуальний tools.launcher.
Додано [1]/[2]/[3] із OS lock, nonce, heartbeat і cooperative shutdown.
Деталі та обмеження: WINDOWS_CMD_LAUNCHER_UA.md.
Модерація з окремого коміту 9ce34c4 ще не інтегрована; не змішуйте результати її тестів.
Локально 23 тести пройшли; Windows smoke виконується тільки Windows CI.

## Перевірений результат CMD-керування
Код caf182283902b401d75d03565a4eaaf051b49cd5: Windows 24 тести успішно; Ubuntu і PostgreSQL — 23 успішно, Windows-only тест пропущений. Ruff/pip check успішні.
CI: https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40162166628
Windows smoke перевіряє CMD меню з кириличного шляху з пробілами, але не live Discord.
Повний launcher ще не готовий: Git update, restore через меню, автоматичний crash supervisor, повна телеметрія та live Discord сценарії залишаються наступними етапами.

## SQLite maintenance — 25.10.2026
CMD [7] враховує DATABASE_URL, [8] відновлює підтверджену копію після зупинки,
[10] перевіряє цілісність і схему v1. Перед відновленням створюється копія поточних даних.
PostgreSQL не обслуговується SQLite-меню. Докладно: WINDOWS_CMD_LAUNCHER_UA.md.
Локально: 28 passed, 1 skipped (Windows-only). Новий CI перевіряється окремо.

Перевірено ab394926c7f9e1513c6fa13ec8c480bfe78aa282: Windows 29 passed; Ubuntu SQLite і PostgreSQL — 28 passed, 1 Windows-only skipped. Ruff/pip check успішні. CI: https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40162579315
Новий етап не тестував живе Discord-підключення та не додає PostgreSQL backup/restore.
Наступний пріоритет: безпечне оновлення коду з GitHub через меню з перевіркою гілки, локальних змін, backup і міграцій.

## Оновлення через CMD — 25.10.2026
Додано [6]: Git fast-forward дозволеної гілки, перевірка локальних змін,
підтвердження, SQLite backup, pip check, перевірка БД/команд новим процесом,
повторний запуск тільки після успіху. ZIP та PostgreSQL автоматично не оновлюються.
Інструкції й обмеження: WINDOWS_CMD_LAUNCHER_UA.md.
Локально 35 passed, 1 Windows-only skipped; новий CI перевіряється окремо.


## Захист локальних файлів під час оновлення — 25.10.2026
Попередній updater (09f71e7) перевірено CI: Windows 36 passed; Ubuntu SQLite
та PostgreSQL — 35 passed, 1 Windows-only skipped.
https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40162972836
Додатково виправлено читання Git-шляхів: NUL-розділювачі зберігають кирилицю
та пробіли без Git quoting. Тести перевіряють відмову перезаписувати наявні
ігноровані файли, які оновлення додає до Git.
Для нового коду локально: 37 passed, 1 Windows-only skipped; Ruff успішний.
Код 3487321 перевірено CI: Windows — 38 passed; Ubuntu SQLite та PostgreSQL —
37 passed, 1 Windows-only skipped. Ruff/pip check успішні у SQLite jobs.
CI: https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40168851702
Живий Discord та повний цикл
мережевого оновлення з перезапуском реального бота не перевірені.
Наступний пріоритет: контрольоване відновлення процесу після аварії.


## Обмежене відновлення застосунку — 25.10.2026
Managed runtime повторює невдалий запуск до трьох разів із паузами 2/5/15 секунд.
Кожний попередній Bot виходить із async context перед наступною спробою;
OS lock утримується весь час. Навмисне завершення не повторюється, запит [3]
перериває backoff максимум за одну секунду. Неправильний токен, privileged intents
та ValueError конфігурації завершують запуск одразу. Traceback проходить через
редактор секретів у журналі. Меню показує стан відновлення за heartbeat.
Локально 42 passed, 1 Windows-only skipped; Ruff успішний. CI перевіряється окремо.
Це відновлення після Python exceptions, не супервізор аварій ОС/kill/segfault.
Зависання не спричиняє примусового завершення. Живе Discord-підключення не перевірене.
Наступний пріоритет: автоматизація початкової конфігурації та діагностика запуску.


## Майстер конфігурації — 25.10.2026
[11] та [12] запускають майстер: приховане введення токена, ID тестового сервера,
перевірка введення, атомарний запис .env.v2 зі збереженням інших ключів і коментарів.
Enter зберігає наявний токен/ID. Без ID використовується SYNC_COMMANDS=none;
з ID — test. Global sync через майстер недоступний. Для розширених параметрів
редагуйте .env.v2; значення середовища мають пріоритет. Після змін потрібен [2].
Токен не перевіряється через Discord API. Не запускайте майстер з перенаправленим
stdin: якщо getpass не може приховати введення, операція відхиляється.
Локально 50 passed, 1 Windows-only skipped; Ruff успішний. CI перевіряється окремо.
Наступний пріоритет: bootstrap першого встановлення та діагностика відсутнього Python.


## Bootstrap першого встановлення — 25.10.2026

`YEBOYBot.cmd` шукає готове `.venv-v2`, перевіряє критичні імпорти, а за потреби
викликає `install_v2.bat`. Інсталятор шукає Python 3.12 через `py`, потім `python`.
Якщо Python відсутній, показує офіційну адресу й завершується з кодом 1;
системні компоненти автоматично не завантажує. Git не потрібен для ZIP-установки.
Bootstrap створює venv, встановлює requirements, виконує pip check та імпорти,
створює каталоги й `.env.v2`, зберігаючи наявну конфігурацію.
Повторний виклик не встановлює незмінені залежності; після збою можна повторити.
[5] відновлює залежності через той самий bootstrap. Працюючий бот або інший
інсталятор блокують зміну бібліотек через OS locks; спершу зупиніть бот через [3].
Наявний venv іншої версії Python автоматично не видаляється: після зупинки бота
перейменуйте `.venv-v2` і повторіть запуск. Дані та `.env.v2` розміщені поза venv.

Локально Linux/Python 3.12: 10 нових offline unittest пройшли; compileall успішний.
Повний pytest/Ruff локально не виконано: залежності недоступні через мережу
середовища. Windows first/repeat install додано до CI; результат слід перевіряти
для нового коміту. Реальний Windows-ПК і Discord API не перевірені.
Матриця та наступні кроки: [аудит](LAUNCHER_AUDIT_UA.md).

## Підтверджений CI bootstrap — 25.10.2026

Код `1b9b135014c4dab80083345d5e624a7d36e77cbf` перевірено:
[GitHub Actions](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40203553458).
Windows/Python 3.12 — 62 passed; Ubuntu SQLite і PostgreSQL — по 60 passed,
2 Windows-only skipped. Ruff і pip check успішні у Windows/Ubuntu SQLite jobs.
Windows first/repeat install, відсутній Python та CMD smoke у кириличному шляху
з пробілами пройшли. Перший CI виявив дві помилки тестових fixture
(короткі Windows-шляхи та quoting CMD); їх виправлено до успішного повтору.
Це runner Windows, не ручний тест Windows 10/11; live Discord не перевірено.
Цей запис змінює лише документацію, перевірений Python/Batch-код не змінено.

## Локальна діагностика та JSON-звіт — 25.10.2026

[4] перевіряє Python 3.12, установлені пакети та критичні імпорти, конфігурацію
і файлову SQLite. БД відкривається mode=ro: quick_check, версія v1 та потрібні
колонки; перевірка SQL має progress deadline 2 секунди й lock timeout 1 секунду.
Це не повний integrity_check, тест запису або міграція. Відсутня БД не створюється;
вона готується штатним запуском [1]. PostgreSQL отримує unknown, без підключення.
[18] додатково атомарно зберігає data/v2/diagnostics/latest.json, замінюючи попередній
звіт. Токен, URL БД, ID серверів, nonce, шляхи, журнали й тексти винятків
не включаються. Каталог data/ виключено з Git.
Runtime readiness вимагає свіжого heartbeat і стану «Готовий»; прострочений heartbeat
не підтверджує Discord ready. Чинність токена, мережа та Discord API не перевіряються.
Статуси JSON: ok/error/unknown; local_checks_passed=false також для unknown.
За невдалої локальної перевірки CLI повертає 1, але [18] зберігає доступний звіт.
Кожен виклик меню запускає новий Python-процес, тому перевірка читає актуальну .env.v2.
Локально пройшли 3 stdlib тести експорту/збереження попереднього звіту та відсутньої БД;
інші тести перевіряються в CI нового коміту через недоступні локальні залежності.

## Перевірений результат діагностики — 25.10.2026

Код `70ccbd757c318d4a71d5f80ddbb741239ba7a62a`: Windows — 75 passed;
Ubuntu SQLite та PostgreSQL — по 73 passed, 2 Windows-only skipped.
Ruff і pip check пройшли у Windows/Ubuntu SQLite jobs.
CI: https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40204847470
13 нових тестів покривають export, збереження попереднього звіту після збою,
відсутню/пошкоджену/несумісну SQLite, конфігурацію, залежності, heartbeat
та відсутність секретів у JSON. Локально виконано 3 stdlib тести, решта — у CI.
Реальний Discord API та ручний Windows 10/11 залишаються неперевіреними.
Наступний етап: checksum і метадані резервних копій та безпечна retention.
Цей коміт змінює лише звіт; перевірений код не змінено.

## Маніфести й кероване зберігання SQLite-копій — 25.10.2026

База ітерації: `ac5fbab`, `feature/core-v2`. Гілка GitHub звірена; draft PR #1
залишається відкритим. Старий yeboybot і його точки входу збережені.
Нові копії [7], перед відновленням [8] та оновленням [6] отримують `.db.json`
з SHA-256, розміром, UTC та версією схеми. Конфігурація/токен не додаються.
Знімки нормалізовані до journal_mode=DELETE, читаються як закриті immutable
копії; live DB так не відкривається. SQLite Backup API враховує committed WAL.
Restore перевіряє фактичну staged-копію до створення safety backup і заміни БД.
Старі копії без маніфесту потребують окремого підтвердження; неправильний маніфест
або checksum блокують restore. Файли-посилання й self-restore через hardlink відхиляються.
[20] показує метадані та пропонує keep-N із підтвердженим перенесенням старіших
перевірених пар в архів. Архів залишається доступним у [8], автоматичного
чи остаточного видалення немає. Місце на диску не звільняється. Legacy/invalid,
поточна БД й останні N перевірених копій не архівуються.

Реалізація: core_v2/backup.py, tools/launcher/backup_manifest.py, storage.py, меню.
32 нових тестових сценарії покривають manifests/restore/retention, збої запису,
повторну перевірку без WAL, symlink/hardlink, locks, скасування і live WAL writer.
Локально Linux/Python 3.12: **105 passed, 2 Windows-only skipped**; Ruff і pip check
успішні. Перший прогін знайшов дефект repeated-inspection WAL; його виправлено
до успішного повного повтору. CI нового коду перевіряється окремо.
SHA-256 не є підписом; довільний користувацький вміст БД не фільтрується.
PostgreSQL backup/restore, ручний Windows 10/11 і live Discord не перевірені/не реалізовані.
Наступний пріоритет лаунчера: opt-in Windows Task Scheduler і контрольоване
відновлення після аварій ОС; ядра — версійовані міграції й модерація.

### CI резервування підтверджено

Коміт `698457184c20567d44d7e33a7640de863a52ed6e`: Windows — **107 passed**;
Ubuntu SQLite і PostgreSQL — по **105 passed, 2 Windows-only skipped**.
Ruff/pip check успішні у Windows/Ubuntu SQLite jobs.
https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40205958270
Це 32 нові сценарії відносно попередньої ітерації, не сума тестів трьох jobs.
Повний Git-клон у робочій папці відновлено після відновлення мережевого доступу;
попередній текстовий snapshot збережено окремо. Ручний Windows/live Discord не перевірені.

## Добровільний автозапуск Windows — 25.10.2026

[15] керує завданням Планувальника для поточного користувача й папки інсталяції.
Створення/увімкнення потребує `УВІМКНУТИ АВТОЗАПУСК`; видалення —
`ВИМКНУТИ АВТОЗАПУСК`. Без підтвердження системні налаштування не змінюються.
Назва ізольована хешем root+SID. Перед увімкненням нові процеси перевіряють
конфігурацію та імпорти. Системні інструменти викликаються абсолютними шляхами,
без shell, stdin, з таймаутами та прихованим raw output.

XML задає logon поточного SID, InteractiveToken, LeastPrivilege, IgnoreNew,
WorkingDirectory та pythonw.exe -m core_v2.managed; паролі не зберігаються.
Створення не перезаписує наявні завдання. Перед зміною/видаленням перевіряються
description, principal, SID, єдиний trigger/action, шляхи й аргументи. Невдалий
Query залишається unknown, а не «вимкнено». Стан Планувальника не є Discord readiness.
Для pythonw файлове redacted логування працює без sys.stderr. Зупинка бота — [3],
видалення завдання саме по собі бот не зупиняє. Scheduler restart-on-failure не додано:
навмисна зупинка не спричиняє повторний запуск; попередній managed backoff збережено.

49 нових сценаріїв: consent/ownership/XML/SID/таймаути/помилки/інтерпретатори,
скасування, query-only Windows smoke та файлова редакція логів pythonw.
Локально Linux/Python 3.12: **153 passed, 3 Windows-only skipped**, Ruff/pip check
успішні. CI нового коміту перевіряється окремо. Тести не реєструють справжніх
завдань; створення, видалення і запуск після logon на реальному Windows 10/11
залишаються ручним критерієм. Живий Discord не перевірено.

Ручний сценарій: налаштувати [12] → перевірити [4] → [15]/[1] і підтвердження →
повторний вхід у Windows → відкрити меню, перевірити готовність і /status →
[3] (бот не повинен перезапуститися сам) → [15]/[2] і підтвердження →
наступний вхід не повинен запускати цю інсталяцію. Перед перенесенням папки
вимкніть її старий автозапуск. До виконання цього сценарію функція лишається Partial.
Наступне: супервізор аварій процесу й версійовані міграції перед розширенням модулів.

### Фактичний стан перевірок автозапуску

Код `60c9984207c58dd340d7752ef52557be9e31dc1b`: Ubuntu SQLite та PostgreSQL CI —
по **153 passed, 3 Windows-only skipped**. Windows first/repeat install, Ruff
і pip check пройшли; pytest залишився in_progress без доступного завершеного
журналу. Причину затримки не встановлено, Windows pytest не позначається успішним.
https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40206795971

Коміт `cc283467d94cc84c10ea1303d8a039819ba32e8b` додає в CI `-vv --durations=10`,
faulthandler_timeout=120 та exit-on-timeout, без зміни Python-коду бота.
Ідентична команда локально дала **153 passed, 3 skipped**; новий CI також ще
не повернув завершених результатів на момент звіту:
https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40207357017
Запити скасування попередніх затриманих запусків відправлені, але їх завершення
не підтверджено API. Журнали затриманих Windows jobs поки повертають BlobNotFound.

Наступні точні дії: перевірити зазначені runs і jobs 121209346037/121211039299;
за появи журналів визначити конкретний повільний тест/стек, виправити причину
та повторити тільки потрібні перевірки. Після Windows CI пройти ручний logon
сценарій із попереднього розділу. Автозапуск залишається Partial; успіх старого
Windows backup CI не переноситься на нього. У main нічого не зливалося.

## P0: безпечна відмова керування та несумісної БД — 25.10.2026

База: 8872ce9, feature/core-v2, draft PR #1. AGENTS.md відсутній; старий
yeboybot і користувацькі дані не змінювалися.

Реалізовано:
- managed.run очікує завершення client/control, перед виходом скасовує та
  приєднує обидві задачі. ControlFailure завершує клієнт без retry; LoginFailure
  зберігає тип і не повторюється. Штатний stop має пріоритет перед heartbeat.
- Atomic heartbeat зберігає попередній стан при невдалій публікації; cleanup
  не приховує первинну I/O помилку. Невдале завершення повертає 1.
- Schema guard виконується до WAL/DDL і повторно для pooled-з'єднання.
  Невідома/порожня/versionless/неповна v1 схема відхиляється без ремонту.
- SQLite bootstrap у BEGIN IMMEDIATE; помилка DDL відкочується. Конкурентний
  WAL setup має до 4 BUSY/LOCKED-only спроб з async backoff і повторним guard.
- PostgreSQL guard працює під advisory lock; інтеграційні тести створюють
  власну UUID-схему лише в одноразовій yeboy_test.

Тести: 27 нових сценаріїв (9 managed, 18 schema guard). Heartbeat regression
спершу впав із TimeoutError на попередньому коді; після виправлення проходить.
Є реальні filesystem/subprocess/OS-lock перевірки без входу в Discord,
збереження байтів несумісної SQLite, rollback DDL і конкурентна ініціалізація.
Реальний PostgreSQL-прогін знайшов різницю типу relkind bytes/text у тесті;
SQL-приведення виправлено до повторного повного успіху.

Локально Linux, Python 3.12:
- SQLite: 176 passed, 7 skipped (3 Windows-only + 4 PostgreSQL-only).
- PostgreSQL 17.11 у локальному контейнері: 180 passed, 3 Windows-only skipped.
- Ruff, pip check, compileall та diff check успішні. Нових залежностей немає.

CI на початку ітерації: 40207357017/40207361179 Windows jobs скасовані,
але logs досі BlobNotFound 404; поточний 40207735164 Windows job121212190702
показував in_progress понад 15 хв при timeout-minutes=15. Конкретного тесту
і стеку отримати неможливо; причину не встановлено. Перевірки не прибиралися.
Новий CI перевіряється окремо після публікації цього коду.

Обмеження: повного migration runner/переходів схем, зовнішнього OS crash
supervisor, ручного Windows 10/11 і live Discord перевірок ще немає.
Наступний завершуваний етап — versioned migration runner з backup/recovery
перед першою потрібною зміною схеми; паралельно отримати Windows CI logs.
Запуск Windows: YEBOYBot.cmd → [4] діагностика → [1]; помилка — [9] журнал;
[3] зупинка, [2] перезапуск. Номер версії БД вручну не редагувати.

</details>
