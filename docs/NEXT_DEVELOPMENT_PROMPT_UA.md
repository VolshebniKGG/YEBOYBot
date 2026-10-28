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

- Core V2 має 20 основних slash-команд та опціональну групу music із 8 командами:
  play, join, queue, skip, stop, autoplay, pause, resume. Група додається до setup
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

База паузи — 23eb600: push40714907665 і PR40714922993 завершилися success,
усі9 jobs у кожному. Історичні full suites1967: Windows1837/130,
Linux1795/172, PostgreSQL1924/43; maintenance1007. Linux CVE31/0/0 skips.
Ці результати не є доказами наступного SHA. Pause checkpoint4c160da має
локальні1937passed/172skipped, total2109; його CI перевіряється окремо.
Ітерація рейтингу базується на4c160da: нові totals/CI rank-коду звір за
актуальним HEAD у STATUS і PR#1, не запозичуй успіх старих тестів.

## Найближчий завершуваний результат

Пріоритет користувача — ранг і музика з власними assets та українським UX.
Не реалізовуй pause/resume або окремі рейтинги повторно. Спершу звір CI,
узгодженість text/voice rank і exact total, включно з граничними лічильниками.
Після завершення рейтингу додай **одну функцію керування гучністю сесії**.

1. Визнач зрозумілу slash-команду, обґрунтований діапазон і policy доступу.
   Потрібні поточний голосовий канал, актуальна власна сесія, guild isolation
   та блокування closing/failed. Команда не повинна створювати новий connect.
2. Підтверди застосування до фактичного PCM transformer поточного й наступних
   треків. Помилка SDK не має повідомляти успіх чи показувати raw URLs/credentials.
   Сесійне значення не називай збереженим у БД/INI без реалізованого запису.
3. Збережи незалежність manual/AutoPause, FIFO, user priority, caps/backoff,
   cleanup ownership і safe diagnostics. Перевір зміни під час pause, resolve,
   skip, stop, reconnect, стару сесію й одночасні запити.
4. Додай змістовні негативні та SDK-boundary/інтеграційні тести без справжнього
   Discord login чи системних установників. Збережи всі full/focused Windows/
   Linux/PostgreSQL/native recovery/CVE jobs та log/JUnit/JSON artifacts.
5. Онови help, metadata, українські інструкції, матрицю й карту коду. Зроби
   логічний коміт у feature/core-v2, перевір CI за новим SHA, онови draft PR
   без merge в main й надай фактичний звіт. pip check не вважай CVE-аудитом.

Після цього розглянь керування власними замовленнями в черзі: стабільна
ідентичність запиту, автор або Manage Guild, без видалення іншого треку
через конкурентний skip/початок відтворення. Це окрема завершена ітерація.

Паралельний борг модерації не втрачено: warnings/unwarn потребують fresh actor
permissions, аудиту revoker без метаданих у reason, atomic guild/status CAS,
compatibility UUID/deterministic IDs та unknown COMMIT без хибного failed.
Далі timeout/untimeout з immutable duration/expiry і claim до PATCH: простого
додавання action у claim_case недостатньо, replay не має подовжувати санкцію.
Міграції — лише за необхідності з verified recovery й preservation tests.

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
