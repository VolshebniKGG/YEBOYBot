# Промт наступної ітерації YEBOYBot

Продовжуй розробку VolshebniKGG/YEBOYBot у feature/core-v2 як відповідальний
головний розробник: Python/Discord, архітектура, SRE, БД, безпека, QA та український UX.
Основний Windows-вхід — YEBOYBot.cmd; майбутній production — Linux24/7.
ProBot — функціональний орієнтир, без копіювання чи недоведених переваг.

Спершу звір local/GitHub HEAD, локальні зміни, draft PR #1 та CI за SHA.
Прочитай AGENTS.md, якщо є, README, CORE_V2_STATUS_UA, PROJECT_AUDIT_UA, RANK_MUSIC_UA,
MODERATION_UA, MIGRATIONS_UA, WINDOWS_CMD_LAUNCHER_UA, CLEAN_PC_SETUP_UA,
POSTGRES_RECOVERY_UA та DEPENDENCY_AUDIT_UA. Код має пріоритет перед історичними
результатами. Не дублюй готові сценарії й не переписуй чужі зміни.

Перед новими функціями перевір цей користувацький сценарій на окремому Discord
сервері: PNGранг/AttachFiles+EmbedLinks → перенесеннястарихXPчерез[26] →
текст/голоснарахування → AP→usertrack→AP → voice disconnect/reconnect → stop.
Cloudprobe першого автотреку повернув DownloadError з категорієюnetwork, тож
успішний стримінг не заявлено. Не додавай cookies/TLSdisable/обхідправилджерела
для отримання зеленого smoke. Якщо liveсередовище недоступне — зафіксуй межу,
продовжуй незалежну стабілізацію музики, не повторюй rank/music з нуля.

## Реалізований стан, який слід повторно звірити

Поточна музична ітерація на базі`de38b71`:
- Session.closing/close_failed зберігають облік власного backend за
  непідтвердженого player/audio/voice cleanup. Новий connect та замовлення
  блокуються; `/music stop` повторює закриття тієї самої сесії без безмежного retry.
- `/music queue` і `/modules` не повідомляють успішне завершення за помилки.
  Власний VoiceClient обліковується під час створення, до await handshake;
  пізні з'єднання теж не втрачаються. Скасування команди
  не кидає teardown; різні guilds завершуються незалежно.
- Успіх закриття підтверджує лише локальний стан SDK: bounded call без
  expired deadline, strict False від is_connected та успішний audio cleanup.
  Не видавай це за незалежний remote voice-state ack чи доказ усіх native cleanup.
- Source cleanup exception не є успіхом. Незавершений native cleanup може
  потребувати штатного перезапуску; не обіцяй автоматичне лікування будь-якого зависання.
- Час для instant EOF починається перед backend.play після resolver, тому
  довгий пошук не обходить backoff. Callback cleanup входить до цього часу:
  це не вимір фактично почутого аудіо й не доказ його тривалості.
- Нових dependencies/schema/intents/configkeys/команд немає. Перевір
  фактичні результати тестів і CI цієї ітерації за новим SHA у STATUS і PR#1.
  Успішні push40681982798/PR40682163302 належать базі, не новим змінам.

Нова довідка на базі5ded468:
- /help topic/page: runtimeкаталог, guildoverrides, 10команд/сторінку, cap100,
  privateauthor/guildbound select+buttons120s, cap100views/staticfallback.
- /modules: freshSQL≤3s, readonly; strict unknown/disabled/unconnected, current
  ready/voicecollector/gateway/shard/scheduler/music. Не показує credentials/IDs.
- Automod/welcome/autorole/panels/rewards/eventlogs ще НЕактивні, навітьзаDBflags.
- Guide підключений доsetup_hook ДОsync і перевіряєтьсяupdate smoke; schema/deps/
  intents не змінено. Тепер20основних команд +optional musicgroup6. Потрібнаsync
  новихcommands у вибранійобласті; privateпанелі неpersist/restartrestore.
- Прочитай GUIDE_UA.md і перевірCI новогоSHA. ЖивийDiscordUI не доведений mocks.



Нова стабілізація музики на базі4be915c:
- error_code allowlist і фіксовані українські підказки у /music queue; raw provider
  output/URLs/credentials не показувати. Класифікація ознак не доводить rootcause.
- Worker stdin16KiB/stdout64KB streaming; kill/reap/drain тільки власного Pythonchild.
  Знищення Deno-нащадків на всіх ОС не підтверджене; не заявляй цю гарантію.
- Stop перевіряється до/після automaticconnect, неправильний query до connect,
  canonicalguild до/після handshake. Shutdown guard і ownedSDKcancellationcleanup.
- Скасована команда не кидає teardown: strong task/shield/registry до completion,
  повторний connect блокується; різні guilds закриваються паралельно.
- Нових команд/dependencies/schema/configkeys немає. Перевір актуальний CI за SHA.

Попередня функціональна ітерація (28.10.2026):
- /rank знову PNG500×188 із DLC/rank_background.png + gg.png masks, великим
  display_avatar і малою guildicon, окремими XP/ranks/levels, bounded renderthreads.
- Голос5XP/повну спостережену хвилину з ≥2 придатними людьми; text5/60s;
  Voice States/voice-onlycache без privilegedMembers/Content; reset приgatewayloss,
  sharddisconnect, AFK/mute/deaf/одиночному перебуванні. Аудіо не аналізується.
- [26] explicit legacy JSON→наявнаSQLitev2: повна валідація, locks, verifiedbackup,
  transaction maxперcounter, idempotence, preserve timestamps; PostgreSQL importнемає.
- [25] optional Music/FFmpeg/Deno configure. options.ini копіюється виключно;
  Credentials/Token fallback лише за порожнього DISCORD_TOKEN; не весь legacyINI.
- Music FIFO50/20guildplayers/2extractors, AP до10000rows вашого _autoplaylist,
  user interruptsAP заAutoPlaylistAutoSkip=yes, backoff/reconnect, ownedasyncFFmpegcleanup.
  In-memoryqueueнепереживаєrestart. /music join/play/queue/skip/stop/autoplay.
- YouTube/SoundCloud/YouTube search: pinnedyt-dlp+ejs, installedDeno/Node≥20;
  noDRM/cookies/TLSdisable/unknownexecutabledownload. Spotifytrackofficialmetadata
  clientcredentials→YouTubematch, maydifferentversion, nooriginalSpotifyaudio.
- PyNaCl1.5.0 мав2advisories; directpinned1.6.2 виправлена, SDKAEADroundtriptested.
  Не повертай старий voiceextra upperbound шляхомdowngrade криптобібліотеки.
- РеальнийDiscordvoice, приватніSpotifycredentials і24hsoak не підтверджені.
  Спершу прочитай RANK_MUSIC_UA; не позначай відсутній liveproof зеленимиmocks.

- Core V2 має20 основних slash-команд; opt-in /music додає6 команд у групі. Kick/ban/unban мають shared ModerationService,
  fresh REST authorization/hierarchy, committed claim до API та ambiguity-safe
  outcomes. Ban зберігає повідомлення; unban перевіряє ban за ID. Modhistory
  приватна, guild-scoped, показує pending/state/user/page. Blind replay відсутній.
- `/warn` уже перенесено: fresh full guild/actor/bot/target, policy Moderate Members,
  hierarchy/protected targets; один atomic warn/applied запис через record_warning.
  ID=guild+interaction; duplicate незмінний, зокрема revoked, changed payload/action
  відхиляється. DB error/timeout може означати unknown COMMIT. Не називай його failed
  чи відсутнім записом. Warn — локальна БД, без mutation API/DM/Discord Audit Log.
  Після виходу цілі replay теж проходить current member check й не пише нових даних.
  Нова ручна команда має новий ID та означає нове попередження.
- `/warnings` display виправлено:10active rows, created/idDESC, escaped reasons
  ≤300UTF16, title≤64, mentionsnone/ephemeral, bounded DB read. Повна причина в БД;
  modhistory також скорочує довгий показ. Старі UUID warnings не видалено.
- **Read/revoke authorization `/warnings` і `/unwarn` ще cached.** Timeout/untimeout
  також мають стару авторизацію, випадкові UUID й неоднозначний PATCH lifecycle.
  Новий warn не означає завершення всіх цих команд.
- Clear/clear-status уже є: regular text channel, одна сторінка до25 recent7day
  default/reply, pinned/system/old skipped, immutable IDs, private60s confirmation,
  author/guild/channel scope. SQL expiry перевіряється після lock. Unknown не
  повторюється; receipt збережений, old views після restart не відновлюються.
- Text XP/cooldown/ranking та durable reminders є. Reminder delivery at-least-once;
  exactly-once для Discord send не обіцяється. Automod/welcome/autorole/panels/
  rewards ще не завершені активні модулі. Голосовий XP уже підключений.
- Schema2, шість Core-таблиць. SQLite[21]/PostgreSQL[23] мають явний1→2 із verified
  backup/rollback/recovery. PostgreSQL restore тільки в окрему порожню БД, standard
  public Core-schema; custom dependencies відхиляються, credentials приховані.
  NativePGWindows/TLS, Git-updater дляPG та SQLite→PG import окремі задачі.
- YEBOYBot.cmd має pre-Python PowerShell5.1 панель. [1] — opt-in official WinGet
  Python3.12x64 per-user + Core dependencies; без WinGet офіційні links, без
  unchecked EXE. Discovery не встановлює runtime самостійно. [24]/[5] Windows
  закривають Python menu до pip, потім відновлюють; config/data збережені.
- Legacy[22]/run.bat ізольовано у .venv-legacy, без globalpip/registry changes;
  music opt-in. Не став історичний root requirements.txt у Core.
- Heartbeat failure fail-closed; зовнішнього OS-crash supervisor немає.

База809db8a мала CI40522292914 усі9jobs success: Windows1033/92, Ubuntu990/135,
PG1082/43. Це історична база, не докази наступного SHA. Warn-ітерація додає222
тести (94repository/87service/25commands/16integration), загалом1347.
Історичний warning-кодef4b345: локально Linux full1186passed/161skipped; repository realSQLite/PG94passed,
фінальні всі нові тести ізPG222passed/0skipped. CI warning-коду звір у STATUS за SHA.
Кодef4b345 має push40560042495 і PR40560052692 усі9jobs success: Windows1229/118,
Ubuntu1186/161, PostgreSQL1304/43, focused966/86 і926/126, maintenance1007,
legacy51 і49/2, audit24/zero/0skips. Наявні39PS+12handoff Windows тести пройшли.
Нові документаційні або функціональні HEAD перевіряй окремо.
Ruff/pipcheck/diffcheck та Linux Core CVE24/zero/0skips пройдено. CVE scope не
охоплює interpreter/OS/legacy/Windows/native. Не перенось результати старих тестів
новому коду. Ручний clean-PC Python install, Windows/logon, liveDiscord,24/7
і performance baseline не підтверджені; відсоток готовності не вигадуй.

## Найближчий завершуваний результат

Пріоритет користувача зараз — **музичний функціонал**. Спершу звір перевірки
завершення сесій і backoff; не називай виправлення перевіреним лише за старим CI.
Потім реалізуй одну завершену музичну можливість: pause/resume, керування
гучністю або видалення замовлення з черги. Обери за актуальним кодом і тестами,
не додавай усе одночасно.

1. Для pause/resume відокрем навмисну паузу від AutoPause порожнього каналу:
   прихід учасника не повинен скасувати ручну паузу. Показуй правдивий стан.
2. Для volume обмеж значення, перевір фактичний PCM transformer і визнач,
   чи настройка діє тільки до завершення сесії. Не називай її збереженою без БД/конфігурації.
3. Для видалення замовлення перевір автора або «Керувати сервером», актуальну
   сесію, guild і голосовий канал. Зміна черги не повинна видалити інший трек
   через конкурентний skip чи початок відтворення.
4. Closing/close_failed сесії повинні блокувати нові дії відтворення.
   Збережи FIFO, пріоритет користувача над AP, caps/backoff, ізоляцію guilds
   і приховування credentials/підписаних URL. Не запускай справжній Discord
   чи системні інсталятори в unit-тестах.
5. Додай негативні й конкурентні сценарії та реальні SDK-boundary перевірки,
   синхронізуй metadata/docs/help. Не обіцяй 24/7 або перевагу над ProBot
   без тривалого сценарію, обладнання, методики та порівняльних результатів.
6. Збережи Windows/Linux/PG/full/focused/installer/native recovery/CVE jobs
   та log/JUnit/JSON artifacts. Онови документацію/матрицю/карту коду,
   створи коміт у feature/core-v2, перевір CI й онови draft PR без merge у main.

Після музичної ітерації повернися до доступу `/warnings` і `/unwarn`: fresh
guild/actor permissions, guild isolation, аудит revoker без метаданих у reason,
atomic scope/status CAS, compatibility UUID/deterministic IDs, replay без
реактивації та unknown COMMIT без хибного failed. Обґрунтуй policy для історичних
локальних записів; нову міграцію додавай лише за потреби з verified recovery.
Перевір SQLite/PG concurrency, role/owner changes, cross-guild, старі рядки,
duplicate/revoked/no-op та DB failures до/після commit.

Далі окремо timeout/untimeout: durable immutable duration/absolute expiry,
причина, guild/interaction/actor/target, claim до PATCH, pending/unknown при
неоднозначній відповіді, API-success/DB-unknown окремо. Простого додавання action
у claim_case недостатньо; повтор не має подовжувати санкцію. Для schema change
спершу verified recovery і preservation tests, без руйнівних дій над user data.

Далі event logs з урахуванням intents/API, /setup, welcome/autorole,
persistent role panels, automod/exceptions/false positives, XP rewards та
scheduled announcements. Content filters без Message Content Intent не позначай
активними. Legacy JSON без guild_id не імпортуй без dry-run і явного зіставлення.

Окремо: native clean-PC/manual acceptance, systemd, PostgreSQL TLS/Windows,
CVE scope expansion, pyc/log/queue tracking hygiene зі збереженням локальних даних,
resource baseline p50/p95/p99/DB latency/loop lag/RAM/CPU та soak/failure tests.
Не додавай Redis/Celery/Kubernetes/sharding без виміряної потреби. Порівняння
з ProBot проводь за однаковими відтворюваними сценаріями; невідомі дані позначай.

Працюй завершеною ітерацією: перевірка → реалізація → тести → виправлення →
документація → GitHub → звіт. Звичайні зміни/коміти/draft PR дозволені;
не видаляй user data, не розкривай секрети, не перезаписуй чужу роботу, не зливай
уmain. Ризикові незворотні дії потребують перевіреного відновлення й підтвердження.
