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

## Реалізований стан, який слід повторно звірити


Поточна функціональна ітерація (28.10.2026):
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

- Core V2 має18 основних slash-команд; opt-in /music додає6 команд у групі. Kick/ban/unban мають shared ModerationService,
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

Перенеси **доступ до `/warnings` і `/unwarn`** на актуальну авторизацію та
перевірений сервісний lifecycle, без повторної реалізації /warn.

1. Для читання й відкликання перевір свіжі guild/actor permissions, guild isolation
   та поточну роль модератора. Обґрунтуй policy вимог для бота/цілі: читання
   історичного локального запису не повинно удавати Discord mutation API.
2. Збережи compatibility старих UUID і нових deterministic IDs. Повторне відкликання
   не реактивує warning; same-request/different-payload не переписує audit.
   Забезпеч атомарний scope/status CAS та коректну діагностику unknown COMMIT.
3. Обґрунтуй аудит revocation і дані автора, не ховай метадані в reason.
   Міграцію додавай лише за необхідності з compatibility/backup/rollback/preservation.
4. Перевір SQLite/PG concurrency, role/owner changes, чужий guild, старі rows,
   duplicate/revoked/no-op, DB before/aftercommit failures та private bounded UX.
   Додай реальний SQL/SDK cross-layer і негативні тести, не лише mocks success.
5. Збережи всі Windows/Linux/PG/full/focused/installer/native recovery/CVE jobs
   та log/JUnit/JSON artifacts. Не прибирай assertions заради зеленого CI.
6. Онови українські інструкції/матрицю/карту коду, зроби коміт у feature/core-v2,
   перевір CI, онови draft PR без merge уmain й надай фактичний український звіт.

Потім окремо timeout/untimeout: durable immutable duration/absolute expiry,
причина, guild/interaction/actor/target, claim до PATCH, pending/unknown при
неоднозначній відповіді, API-success/DB-unknown окремо. Простого додавання action
у claim_case недостатньо; повтор не має подовжувати санкцію. Для schema change
спершу verified recovery і preservation tests, без руйнівних дій над user data.

Далі event logs з урахуванням intents/API, /setup/help, welcome/autorole,
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
