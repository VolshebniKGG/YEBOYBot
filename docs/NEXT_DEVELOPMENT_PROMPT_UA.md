# Промт наступної ітерації YEBOYBot

Продовжуй розробку VolshebniKGG/YEBOYBot у feature/core-v2 як відповідальний
головний розробник: Python/Discord, архітектура, SRE, БД, безпека, QA та український UX.
Основний Windows-вхід — YEBOYBot.cmd; майбутнє виробниче середовище — Linux 24/7.
ProBot — функціональний орієнтир, без копіювання й непідтверджених переваг.

Спершу звір local/GitHub HEAD, локальні зміни, draft PR #1 та CI за точним SHA.
Прочитай AGENTS.md, якщо є, README, CORE_V2_STATUS_UA, PROJECT_AUDIT_UA,
RANK_MUSIC_UA, MODERATION_UA, MIGRATIONS_UA, WINDOWS_CMD_LAUNCHER_UA,
CLEAN_PC_SETUP_UA, POSTGRES_RECOVERY_UA та DEPENDENCY_AUDIT_UA.
Код має пріоритет перед історичними результатами. Не дублюй готові функції,
не перезаписуй чужу роботу й не прибирай перевірки заради зеленого CI.

## Реалізований стан, який треба повторно звірити

- Core V2 має 20 основних slash-команд та опціональну групу music із 9 підкомандами:
  play, join, queue, skip, stop, autoplay, pause, resume, volume. На корені дерева
  21 команда разом із групою. Група додається до setup
  перед sync. Нові підкоманди публікуються в обраній області через [11] → [2];
  SYNC_COMMANDS=none їх не публікує. Не вмикай global sync заради тестового сервера.
- Pause/resume керують тільки наявною сесією: Manage Guild, актуальний actor,
  поточний голосовий канал, canonical guild, closing/failed guards. Ручний намір
  незалежний від AutoPause порожнього каналу; прихід людини не знімає його,
  resume не обходить AutoPause. Намір сесійний, не збережена конфігурація БД/INI.
- Ручна пауза діє через skip і зміну треку. До resume не брати наступний запит
  чи автотрек і не починати новий resolver. Успішний результат уже розпочатого
  resolver чекає resume й отримання свіжого посилання; звичайна помилка джерела
  не гарантує збереження невдалого запиту. Queue показує
  причини паузи й доступний локальний SDK-стан, а не доказ почутого аудіо.
  Втрата з'єднання блокує команди до reconnect, зберігаючи ручний намір.
- /music volume percent — необов'язкове ціле число 0–100, без числа читає стан.
  Читання й зміна потребують «Керувати сервером», свіжої перевірки учасника,
  того самого поточного голосового каналу й актуального сервера. Завершення
  сесії, непідтверджене очищення, завершена задача й відновлення з'єднання
  блокують дію. Команда працює лише з наявною власною сесією й не створює
  підключення. Значення діє для поточного власного PCM і наступних замовлень
  та автотреків, зокрема після skip та під паузою. 0 вимикає звук без додавання
  паузи; наявні паузи залишаються. Під час відтворення час треку й черга
  продовжуються; причини паузи незалежні.
  Нова Session або перезапуск бере DefaultVolume з INI 0.01–1.0, можливі дробові
  відсотки. Сесійне значення, зокрема 0, не записується в INI/БД.
  Черга й запит гучності розрізняють спостережений поточний PCM, майбутнє
  налаштування за відсутності джерела та невідомий стан. За часткової помилки
  SDK поточний PCM може змінитися, майбутнє налаштування не підтверджується;
  фіксована помилка не повідомляє успіх, UI показує обидва значення за
  розбіжності й доступного спостереження. Локальний коефіцієнт не доводить
  почуту гучність, один старий кадр аудіопотоку можливий.
- Музика: окремі FIFO до50 замовлень, до20 плеєрів/2 extractors, AP до10000
  записів, пріоритет користувача, backoff/reconnect. Черги в пам'яті; restart
  їх не відновлює. Підтримується лише описаний subset options.ini.
- [25] налаштовує optional Music/FFmpeg/Deno; системні компоненти встановлюються
  тільки після згоди з точних офіційних WinGet пакетів. YouTube/SoundCloud/пошук
  через pinned yt-dlp/EJS та Deno/Node≥20. Spotify дає офіційні метадані треку
  для YouTube match, не оригінальний Spotify audio. Не додавай cookies, DRM bypass
  чи вимкнення TLS для отримання зеленого smoke.
- Непідтверджений player/audio/voice cleanup зберігає ту саму Session/backend/worker
  із closing/close_failed й блокує новий connect. Stop повторює обмежене закриття,
  queue/modules показують стан; нескінченного автоматичного retry немає.
  VoiceClient обліковується зі створення перед handshake. Cancellation не кидає
  teardown; незалежні guilds завершуються паралельно. Після зміни voice-каналу
  перевіряється поточний канал, збережений ID — лише fallback при channel=None.
- Перевірка close означає локальний SDK-стан: bounded call без expired deadline,
  strict False від is_connected та успішний audio cleanup. Це не незалежний
  remote voice-state ack і не гарантія всіх native cleanup. Sticky source failure
  може потребувати огляду власником і штатного restart. Deno descendants окремо
  на всіх ОС не перевірені. Instant EOF вимірюється після resolver до callback
  з урахуванням cleanup; це не тривалість фактично почутого аудіо.
- /rank — PNG500×188 з власними DLC assets, окремими text/voice XP/ranks/levels.
  Text5XP/60s; voice5XP за спостережену хвилину з ≥2 придатними людьми, без
  аналізу аудіо й catchup після втрати gateway. [26] — явний JSON→SQLite max-merge
  із перевіреним backup; старі файли зберігаються, PostgreSQL import відсутній.
- /leaderboard має optional xp_type: Загальний(total), Чат(text), Голос(voice),
  default total й сумісний page1–1000 по10 профілів. Text/voice порядок відповідає
  rank_snapshot; tie — менший user_id вище. Total сортується carry/remainder,
  без SQLite float чи PostgreSQL bigint overflow; відображення — точний Python int.
  БД read≤10s, canonical guild до/після SQL; public defer означає й публічну
  fixed safe error без рядків/SQL/credentials, а не private followup. Публічний звіт
  лише з перевірених ID/XP й AllowedMentions.none. Немає XP writes/schema changes.
  Порядок враховує збережені guild XP-профілі, зокрема колишніх учасників;
  це не повний scan поточного Discord membership і не привід видаляти історію.
  Для нового optional параметра потрібна sync [11]→restart[2]. Не переробляй
  assets картки або формули XP без окремої обґрунтованої задачі.
- /help і /modules приватні, read-only; довідка використовує фактичний каталог,
  стан налаштувань читається fresh SQL. Automod/welcome/autorole/role panels/
  rewards/event logs ще не завершені активні модулі. /setup не реалізований.
- Kick/ban/unban мають shared ModerationService, fresh REST authority/hierarchy,
  claim до API та аудит неоднозначного результату. Clear має підтвердження й
  receipt; /warn атомарний та ідемпотентний. Доступ warnings/unwarn ще cached;
  timeout/untimeout мають стару авторизацію й неоднозначний lifecycle.
- Schema2, шість таблиць. SQLite[21]/PostgreSQL[23] мають явні 1→2,
  verified backup/rollback/recovery. PG restore лише в окрему порожню БД,
  standard public Core schema; native Windows/TLS та Git-updater PG окремі задачі.
- Windows launcher має pre-Python панель, добровільний WinGet Python3.12x64,
  .venv-v2, start/stop/restart й [24]/[5] parent-exit перед pip. Legacy[22]/run.bat
  ізольовано в .venv-legacy без global pip/registry changes. Heartbeat fail-closed;
  зовнішнього OS crash supervisor немає. Не встановлюй root legacy requirements
  у Core й не понижуй виправлений PyNaCl1.6.2 до вразливої старої версії.

База ітерації гучності — 0698223: push40773505381 і PR40773512437 завершилися
success, усі 9 jobs у кожному. Історичні full suites бази зібрали 2236:
Windows 2069 passed/167 skipped, Linux 2027/209, PostgreSQL 2193/43;
Linux Core CVE 31 distributions/0 advisories/0 skips. Це докази бази рейтингу,
а не нового коду гучності чи наступної зміни черги. Поточні totals та CI звір
за точним HEAD у CORE_V2_STATUS і draft PR#1; старі успіхи не запозичуй.

## Найближчий завершуваний результат

Пріоритет користувача — ранг і музика з власними assets та українським UX.
Гучність, pause/resume й окремі рейтинги вже реалізовані; звір їхній CI за
точним SHA й збережи поведінку. Наступний результат — **одна функція видалення
замовлення, що ще чекає в черзі, за стабільним ID**.

1. Додай стабільну ідентичність замовлення й показуй її в приватній черзі.
   ID має належати конкретному запиту цієї сесії й сервера; позиція в списку
   не є ID. Визнач одну зрозумілу slash-команду видалення за цим ID.
2. Автор може видаляти власне очікуване замовлення, «Керувати сервером» дає
   право видаляти чуже. Перевіряй актуального учасника, поточний голосовий канал,
   сервер і наявну власну сесію. Завершення сесії, непідтверджене очищення,
   завершена задача й відновлення з'єднання блокують дію. Нове підключення не потрібне.
3. Видалення має бути атомарним відносно взяття замовлення, skip, початку треку й інших
   запитів. Якщо трек уже взято плеєром, повідом про це без видалення іншого.
   Застарілий ID попередньої сесії або іншого сервера не змінює поточну чергу.
   Повторне видалення не зачіпає наступний трек. Збережи FIFO, пріоритет
   замовлень, caps/backoff, manual/AutoPause, гучність і cleanup ownership.
4. Перевір негативні права/канали/сервери, застарілі ID, конкурентні видалення,
   взяття замовлення/skip/stop, автотрек проти замовлення й скасування interaction. Використай
   змістовні engine/SDK-boundary тести без Discord login чи системних установників.
   Збережи всі full/focused Windows/Linux/PostgreSQL/native recovery/CVE jobs
   та log/JUnit/JSON artifacts. Нова черга лишається сесійною без БД-міграції.
5. Онови help, metadata, українські інструкції, матрицю й карту коду; поясни
   sync обраної області [11] → restart [2]. Зроби логічний коміт у feature/core-v2,
   перевір CI за новим SHA, онови draft PR без merge в main й надай фактичний
   звіт. pip check не вважай CVE-аудитом.

Паралельний борг модерації не втрачено: warnings/unwarn потребують fresh actor
permissions, аудиту revoker без метаданих у reason, atomic guild/status CAS,
compatibility UUID/deterministic IDs та unknown COMMIT без хибного failed.
Далі timeout/untimeout з immutable duration/expiry і claim до PATCH: простого
додавання action у claim_case недостатньо, replay не має подовжувати санкцію.
Міграції — лише за необхідності з verified recovery й preservation tests.

Наявний P0 борг даних і legacy зберігається: не видаляй журнали/черги/JSON,
не імпортуй старі глобальні дані без явного зіставлення сервера й backup; старі
жорстко задані шляхи FFmpeg та nocheckcertificate потребують окремої ітерації.
Завдання черги не є підставою для небезпечного очищення чи TLS bypass.

Реальні Discord voice/UI, Spotify credentials, чистий Windows/WinGet/logon,
native FFmpeg recovery, 24h soak, systemd і ресурсний baseline p50/p95/p99/
DB latency/loop lag/RAM/CPU не підтверджені. Якщо live-середовище недоступне,
познач межу й продовжуй незалежні задачі. Не заявляй SLA24/7 або переваги над
ProBot без однакових відтворюваних сценаріїв і вимірювань. Не додавай Redis,
Celery, Kubernetes чи розподілений sharding без підтвердженої потреби.

Працюй завершеною ітерацією: стан → реалізація → тести → виправлення →
документація → GitHub → звіт. Звичайні зміни/коміти/draft PR дозволені;
не видаляй user data, не розкривай секрети й не перезаписуй чужу роботу.
Ризикові незворотні операції потребують перевіреного відновлення й підтвердження.
