# Аудит нових media-залежностей — 28.10.2026

Під час rank/voice/music ітерації додано Pillow13.0.0, PyNaCl1.6.2, davey0.1.7,
yt-dlp2026.10.26 і yt-dlp-ejs0.8.0. Pillow малює зображення; PyNaCl і davey —
voice AEAD/DAVE; yt-dlp/EJS отримують підтримувані публічні потоки.
Пакети встановлюються одним Core bootstrap лише в .venv-v2; FFmpeg та JS runtime
є окремими native-компонентами й встановлюються за окремою згодою через[25].

Початковий PyNaCl1.5.0 повернув PYSEC-2026-1448 і PYSEC-2026-3002; обидва
мають виправлення1.6.2. Пакет замінено без вимкнення audit. Використовується
базовий discord.py2.7.1 із явно pinnedPyNaCl1.6.2, а не його voiceextra, що ще
має старий upperbound<1.6. Власний тест виконує справжнє SDKAEAD encryption
і PyNaCl decryption трьох пакетів з різними nonce. Це сумісність цього шляху,
а не доказ усіх livevoice/DAVE сценаріїв.

Повторний Linux Python3.12.14 pip-audit2.10.1/PyPI: **31дистрибутив,0advisories,
0пропусків**, pip26.2. pipcheck пройдено окремо. Scope: LinuxCore/runtime/dev/
installer; не OS/interpreter/FFmpeg/Deno/Node/legacy/Windows resolution.
Окремий CI виконує аудит заново для нового коміту; старий audit24 не є новим.

# Аудит Python-залежностей — 26.10.2026

`pip-audit 2.10.1` перевірив установлені дистрибутиви окремого Linux-середовища
Core V2 через сервіс PyPI. Це аудит конкретного набору версій на зазначену дату,
а не гарантія відсутності вразливостей у боті чи його залежностях.

| Перевірка | До виправлення | Після виправлення |
|---|---|---|
| Python | 3.12.14 / Linux | 3.12.14 / Linux |
| Перевірено дистрибутивів | 24 | 24 |
| Пропущено дистрибутивів | 0 | 0 |
| pip | 25.0.1 | 26.2 |
| Відомі advisory IDs | 6 у pip; 12 записів із дублями | 0 |
| Інші 23 дистрибутиви | Відомих advisories не повернуто | Відомих advisories не повернуто |
| Підтверджений exit code повторного аудиту | — | 0 |

Початкові JSON містили повтори тих самих PYSEC IDs; їх не рахували як окремі
вразливості. Знайдені записи та мінімальні виправлені версії за сервісом:

| Advisory | CVE | Виправлений pip |
|---|---|---|
| [PYSEC-2026-1795](https://osv.dev/vulnerability/PYSEC-2026-1795) | CVE-2025-8869 | 25.3 |
| [PYSEC-2026-1796](https://osv.dev/vulnerability/PYSEC-2026-1796) | CVE-2026-1703 | 26.0 |
| [PYSEC-2026-2875](https://osv.dev/vulnerability/PYSEC-2026-2875) | CVE-2026-3219 | 26.1 |
| [PYSEC-2026-2876](https://osv.dev/vulnerability/PYSEC-2026-2876) | CVE-2026-6357 | 26.1 |
| [PYSEC-2026-196](https://osv.dev/vulnerability/PYSEC-2026-196) | CVE-2026-8643 | 26.1.2 |
| [PYSEC-2026-3721](https://osv.dev/vulnerability/PYSEC-2026-3721) | CVE-2026-13346 | 26.2 / 26.2.0 |

Для першого advisory опис містить умову щодо відсутності PEP 706 у Python;
Python 3.12 має цю підтримку. Виявлення за версією пакета не доводить
можливість експлуатації в цьому середовищі. Інші записи також мають власні
умови. Встановлено pip 26.2, щоб усунути всі шість відомих збігів за версією.
Позначення 26.2 та 26.2.0 еквівалентні за PEP 440.

## Що саме перевірялося

Аудит охопив фактично встановлені runtime/transitive пакети, dev/test tools
і pip у тестовому Core venv. Інструмент аудиту встановлений в іншому venv;
його власні залежності не включено до цих 24 дистрибутивів.

| Набір | Зафіксовані версії після виправлення |
|---|---|
| Runtime і transitives, 16 | aiohappyeyeballs 2.7.1; aiohttp 3.14.5; aiosignal 1.4.0; aiosqlite 0.22.1; asyncpg 0.32.0; attrs 26.1.0; discord-py 2.7.1; frozenlist 1.8.0; greenlet 3.5.6; idna 3.20; multidict 7.2.1; propcache 0.5.4; python-dotenv 1.2.4; sqlalchemy 2.0.55; typing-extensions 4.16.0; yarl 1.25.1 |
| Dev/test/lint, 7 | iniconfig 2.3.1; packaging 26.3; pluggy 1.7.0; pygments 2.21.0; pytest 9.1.1; pytest-asyncio 1.4.0; ruff 0.17.2 |
| Installer, 1 | pip 26.2 |

Не перевірено цим аудитом: окрема `.venv-legacy`, залежності audit tool,
системні пакети ОС, Python interpreter, native PostgreSQL/FFmpeg, assets,
Windows-specific resolution або вихідний код бота. Результат не замінює
аналіз permissions, SQL/command injection, TLS і безпечної роботи з даними.
`pip check` перевіряє сумісність установлених пакетів, а не базу CVE.
Майбутні версії advisory database й інше розв'язання transitives можуть
дати інший результат; перевірку потрібно повторювати.

## Виправлення встановлювача

`tools/installer_policy.py` задає pip 26.2 для Core та legacy bootstrap.
Перед встановленням пакетів контролер підтверджує Python 3.12, власний venv
і відсутність зовнішніх package paths. Після оновлення pip версія перевіряється
повторно. Чинний marker вимог не дозволяє пропустити застарілий pip.
Помилка оновлення блокує встановлення залежностей і прибирає success marker.

Зміни стосуються тільки `.venv-v2` або `.venv-legacy`. Системний Python,
глобальні пакети й конфігурація користувача не оновлюються. App requirements
не змінено. Збережені proxy та перевірка CA; редиректи Python/PIP очищуються,
pip config відключено. Використовуються `-I`, `--isolated`,
`--require-virtualenv`, `--no-cache-dir` і binary wheels, без вимкнення TLS.
Невідомі неповні середовища не видаляються автоматично.

На Windows для вже встановленого Core відкрийте **YEBOYBot.cmd → [3] Зупинити
→ [5] Залежності**. Старе меню залишається доступним: його відкриття саме по собі
не запускає оновлення pip і не змінює бібліотеки працюючого бота.
Для legacy: **[22] → [2]**, після зупинки старого процесу через Ctrl+C.
Нове встановлення застосовує політику до встановлення app dependencies.
Не оновлюйте системний pip вручну для цього виправлення.

## Повторення аудиту розробником

Це необов'язкові команди розробника, не крок першого запуску користувача.
Вони не входять у Discord і не встановлюють audit tool у середовище бота.
Виконуйте з кореня checkout після штатного встановлення Core; для audit tool
виберіть окрему нову тимчасову папку. Не вводьте credentials у команду чи
URL; потрібні звичайний доступ до PyPI та перевірені CA.

Linux / Python 3.12:

```bash
audit_dir=$(mktemp -d -t yeboy-audit-XXXXXXXX)
python3.12 -m venv "$audit_dir"
"$audit_dir/bin/python" -I -m pip --isolated --require-virtualenv --disable-pip-version-check install --no-cache-dir --only-binary=:all: pip==26.2
"$audit_dir/bin/python" -I -m pip --isolated --require-virtualenv --disable-pip-version-check install --no-cache-dir --only-binary=:all: pip-audit==2.10.1
core_site=$(.venv-v2/bin/python -I -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
"$audit_dir/bin/python" -I -m pip_audit --path "$core_site" --vulnerability-service pypi --format=json --output "$audit_dir/core-audit.json"
```

Windows PowerShell / Python Launcher 3.12:

```powershell
$auditDir = Join-Path $env:TEMP ("yeboy-audit-" + [guid]::NewGuid().ToString("N"))
py -3.12 -m venv "$auditDir"
& "$auditDir\Scripts\python.exe" -I -m pip --isolated --require-virtualenv --disable-pip-version-check install --no-cache-dir --only-binary=:all: pip==26.2
& "$auditDir\Scripts\python.exe" -I -m pip --isolated --require-virtualenv --disable-pip-version-check install --no-cache-dir --only-binary=:all: pip-audit==2.10.1
$coreSite = & ".\.venv-v2\Scripts\python.exe" -I -c 'import sysconfig; print(sysconfig.get_path("purelib"))'
& "$auditDir\Scripts\python.exe" -I -m pip_audit --path "$coreSite" --vulnerability-service pypi --format=json --output "$auditDir\core-audit.json"
```

Перевіряйте exit code кожного кроку перед наступним. Audit exit 0 означає,
що сервіс не повернув відомих advisories для перевірених пакетів; 1 може означати
знахідки або помилку. Перегляньте JSON і повідомлення інструмента, зокрема
пропущені дистрибутиви. Не прирівнюйте недоступний сервіс до чистого результату.
Збережіть дату, версію інструмента й повний набір пакетів разом зі звітом.
Ці Windows-команди наведені як спосіб повторення; наведений вище результат
отримано на Linux, не на Windows.


## Підтвердження в GitHub Actions

Код **f5685f06**, [PR run40369126215](https://github.com/VolshebniKGG/YEBOYBot/actions/runs/40369126215):
job dependency-audit успішний; JSON artifact **12672290753** містить24
дистрибутиви,0 findings,0 skipped,pip26.2. Push40369117626 також успішний.
Job використовує окремі application/audit venv і не змінює системний Python.
Це Linux Core/runtime/dev/installer resolution; окремий audit-tool venv,
legacy, native/OS та Windows-specific resolution до цього звіту не входять.
Стан усього workflow цим результатом не підтверджується.
