"""Interactive GitHub branch picker for the external transactional updater."""
from __future__ import annotations

from pathlib import Path
import json
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import process, storage
from tools.update_worker import (API, HEADERS, REQUIRED, UpdateFailure,
                                 check_git, read_json, safe_path, valid_branch,
                                 valid_sha, write_json)

ROOT = Path(__file__).resolve().parents[2]
EXIT_TO_EXTERNAL_UPDATER = 26
MAX_BRANCHES = 300
PAGE_SIZE = 12


def list_branches(*, open_url=urlopen):
    """Retrieve up to 300 real branches, paginating the official GitHub API."""
    branches = []
    seen = set()
    for page in range(1, 5):
        request = Request(API + f"/branches?per_page=100&page={page}", headers=HEADERS)
        try:
            with open_url(request, timeout=20) as response:
                payload = response.read(250_001)
            if len(payload) > 250_000:
                raise UpdateFailure("Список GitHub-гілок перевищив дозволений розмір.")
            items = json.loads(payload.decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, ValueError) as error:
            raise UpdateFailure("Не вдалося отримати гілки GitHub. Перевірте інтернет і ліміти API.") from error
        if not isinstance(items, list):
            raise UpdateFailure("GitHub повернув некоректний список гілок.")
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            commit = item.get("commit")
            sha = commit.get("sha") if isinstance(commit, dict) else None
            if not valid_branch(name) or not valid_sha(sha):
                continue
            if name not in seen:
                branches.append({"name": name, "sha": sha.lower()})
                seen.add(name)
        if len(items) < 100:
            break
        if page == 3:
            raise UpdateFailure("У репозиторії понад 300 гілок; потрібен звужений вибір.")
    if not branches or len(branches) > MAX_BRANCHES:
        raise UpdateFailure("Не знайдено сумісних GitHub-гілок.")
    return sorted(branches, key=lambda x: (x["name"] != "improvement/probot-readiness", x["name"]))


def inspect_release(sha, *, open_url=urlopen):
    """Fetch a pinned GitHub tree and fail before stopping a running bot."""
    if not valid_sha(sha):
        raise UpdateFailure("GitHub повернув некоректний коміт.")
    request = Request(API + "/git/trees/" + sha + "?recursive=1", headers=HEADERS)
    try:
        with open_url(request, timeout=20) as response:
            body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise UpdateFailure("Перелік файлів у GitHub-гілці занадто великий.")
        payload = json.loads(body.decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, ValueError) as error:
        raise UpdateFailure("Не вдалося перевірити склад вибраної гілки на GitHub.") from error
    if (not isinstance(payload, dict) or payload.get("truncated") is not False
            or not isinstance(payload.get("tree"), list)):
        raise UpdateFailure("GitHub повернув неповний опис файлів гілки.")
    files, windows_names = set(), set()
    for item in payload["tree"]:
        if not isinstance(item, dict):
            raise UpdateFailure("GitHub повернув некоректний опис файла.")
        kind = item.get("type")
        if kind == "tree":
            continue
        if kind != "blob" or item.get("mode") not in {"100644", "100755"}:
            raise UpdateFailure("Гілка містить непідтримувані посилання або субмодулі.")
        filename = safe_path(item.get("path"))
        folded = filename.casefold()
        if folded in windows_names:
            raise UpdateFailure("Гілка містить конфліктні Windows-імена файлів.")
        windows_names.add(folded)
        files.add(filename)
    if not REQUIRED <= files:
        raise UpdateFailure(
            "У вибраній гілці бракує файлів сучасного Windows/Core V2 лаунчера."
        )
    return len(files)


def choose_branch(branches, *, reader=input, printer=print):
    page = 0
    while True:
        count = (len(branches) + PAGE_SIZE - 1) // PAGE_SIZE
        lo = page * PAGE_SIZE
        printer("\n" + "═" * 68)
        printer("  YEBOYBot  /  ВИБІР ГІЛКИ GITHUB")
        printer("═" * 68)
        printer("  Введіть номер гілки або [0] для повернення.")
        printer(f"  Сторінка {page + 1} із {count}; усього гілок: {len(branches)}")
        printer("─" * 68)
        for index in range(lo, min(lo + PAGE_SIZE, len(branches))):
            item = branches[index]
            printer(f"  [{index + 1:>2}] {item['name']:<42} {item['sha'][:9]}")
        printer("─" * 68)
        if count > 1:
            printer("  [N] Наступна сторінка   [P] Попередня сторінка")
        raw = reader("  Оберіть гілку › ").strip().lower()
        if raw in {"0", "", "q"}:
            return None
        if raw == "n" and page + 1 < count:
            page += 1
            continue
        if raw == "p" and page > 0:
            page -= 1
            continue
        if raw.isascii() and raw.isdecimal() and 1 <= int(raw) <= len(branches):
            return branches[int(raw) - 1]
        printer("  Невірний номер гілки. Повторіть введення.")


def installed_version(root):
    installed = root / "data/v2/install-manifest.json"
    info = read_json(installed) if installed.exists() else {}
    if (root / ".git").is_dir():
        # A local Git checkout may have changed outside this updater; HEAD wins.
        from tools.update_worker import git
        current = git(root, "rev-parse", "HEAD")
        if info.get("commit") == current:
            return current, info.get("branch", "поточний Git HEAD")
        return current, git(root, "branch", "--show-current") or "Git: detached HEAD"
    if isinstance(info, dict) and valid_sha(info.get("commit")):
        return info["commit"], info.get("branch", "невідома")
    return None, "встановлення із ZIP (SHA невідомий)"


def menu(root=ROOT, *, reader=input, printer=print, branches=None,
         preflight=inspect_release):
    root = Path(root).resolve()
    plan_path = root / "data/v2/update-plan.json"
    pending_path = root / "data/v2/update-transaction.json"
    if pending_path.exists():
        raise UpdateFailure("Попереднє оновлення не завершено. Використайте recovery, не запускайте нове.")
    if plan_path.exists():
        raise UpdateFailure("Є незавершений план оновлення. Спочатку перевірте звіт попередньої спроби.")
    mode = "git" if (root / ".git").exists() else "zip"
    if mode == "git":
        check_git(root)
    try:
        current, current_branch = installed_version(root)
    except Exception as error:
        raise UpdateFailure("Не вдалося перевірити поточну версію бота.") from error
    printer("\n" + "═" * 68)
    printer("  GITHUB UPDATER  /  YEBOYBot")
    printer("═" * 68)
    printer("  Поточна гілка: " + current_branch)
    printer("  Поточний коміт: " + (current[:12] if current else "не визначено"))
    printer("  Режим: " + ("Git (клон репозиторію)" if mode == "git"
                           else "ZIP (Git не потрібен)"))
    printer("  Отримання актуального переліку гілок із GitHub...")
    selected = choose_branch(branches if branches is not None else list_branches(),
                             reader=reader, printer=printer)
    if selected is None:
        printer("  Оновлення скасовано.")
        return False
    if current == selected["sha"]:
        printer("  Уже встановлено той самий коміт. Оновлення коду не потрібне.")
        return False
    printer("  Перевірка сумісності гілки без зупинки Discord-бота...")
    total_files = preflight(selected["sha"])
    printer(f"  Склад гілки: {total_files} програмних файлів; базовий Core V2 — сумісний.")
    try:
        sqlite_path = storage.database_path(root)
    except (storage.StorageError, ValueError) as error:
        raise UpdateFailure(
            "Автооновлення наразі підтримує файлову SQLite. Для PostgreSQL "
            "виконайте окреме резервування через меню PostgreSQL та оновлюйте контрольовано."
        ) from error
    printer("\n" + "─" * 68)
    printer("  Гілка: " + selected["name"])
    printer("  Коміт: " + selected["sha"])
    printer("  Спосіб: " + mode.upper())
    printer("  Локальні секрети, дані, бінарники й конфігурація не замінюються.")
    printer("  Код перевірятиметься перед повторним запуском бота.")
    printer("  Під час операції бот буде зупинено, а код — зарезервовано.")
    printer("  При збої виконується спроба відновити попередню версію.")
    if reader("  Для підтвердження введіть ОНОВИТИ › ").strip() != "ОНОВИТИ":
        printer("  Скасовано без змін.")
        return False
    payload = {"id": uuid.uuid4().hex, "mode": mode,
               "branch": selected["name"], "sha": selected["sha"],
               "sqlite_path": str(sqlite_path), "was_running": bool(process.locked(root)),
               "created": int(time.time())}
    write_json(plan_path, payload)
    try:
        if payload["was_running"]:
            printer("  Коректне завершення керованого процесу Discord...")
            if not process.stop(root):
                raise UpdateFailure("Зупинку бота не підтверджено.")
    except Exception:
        plan_path.unlink(missing_ok=True)
        raise
    printer("  План підготовлено; зовнішній updater продовжить роботу в цьому вікні.")
    return EXIT_TO_EXTERNAL_UPDATER
