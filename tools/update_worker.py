"""Offline-capable transactional YEBOYBot code updater (Python stdlib only).

Run this file from a preserved copy under data/v2/. The menu process must
have exited and the managed Discord bot must already be stopped.
Never import installed bot modules here: code can change while we execute.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import uuid
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

OWNER_REPO = "VolshebniKGG/YEBOYBot"
API = "https://api.github.com/repos/" + OWNER_REPO
REMOTE = "https://github.com/" + OWNER_REPO + ".git"
SHA_RE = re.compile(r"[a-fA-F0-9]{40}\Z")
BRANCH_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._/-]{0,100}\Z")
MAX_ARCHIVE = 100 * 1024 * 1024
MAX_CONTENT = 300 * 1024 * 1024
MAX_ONE_FILE = 40 * 1024 * 1024
MAX_FILES = 5000
REQUIRED = {"YEBOYBot.cmd", "tools/windows_setup.ps1", "tools/update_worker.py",
            "tools/launcher/__main__.py", "tools/launcher/github_update.py",
            "tools/launcher/verify_update.py", "core_v2/__main__.py"}
# Prevent switching to an older branch that cannot recover an interrupted
# self-update on the next boot. These minimal entry points must be present
# inside the *selected* immutable commit, before any replacement happens.
COMPATIBILITY_MARKERS = {
    "tools/windows_setup.ps1": ("function Invoke-YeboyGitHubUpdate",
                                "function Restore-YeboyInterruptedUpdate"),
    "tools/update_worker.py": ("def rollback(", "def confirm("),
    "tools/launcher/__main__.py": ("UPDATE_EXIT_CODE = 26",),
}
PROTECTED_TOP = {".git", ".venv-v2", ".venv-legacy", "bin", "data", "backups",
                 "logs", "media", "project", "audio_cache", "dectalk", "__pycache__"}
PROTECTED_NAMES = {".env", ".env.v2", "docker-compose.yml", "discord.log"}
PROTECTED_CONFIG = {"options.ini", "permissions.ini", "aliases.json",
                    "autoplaylist.txt", "autoplaylist_removed.txt",
                    "autoplaylist.cachemap.json", "whitelist.txt", "blacklist.txt",
                    "blocklist_users.txt", "blocklist_songs.txt"}
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}
HEADERS = {"User-Agent": "YEBOYBot-updater/1", "Accept": "application/vnd.github+json"}


class UpdateFailure(RuntimeError):
    pass


def valid_branch(name):
    return (isinstance(name, str) and bool(BRANCH_RE.fullmatch(name)) and
            ".." not in name and "//" not in name and not name.endswith(("/", ".", ".lock")))


def valid_sha(value):
    return isinstance(value, str) and bool(SHA_RE.fullmatch(value))


def safe_path(name):
    if not isinstance(name, str) or "\\" in name or "\x00" in name or ":" in name:
        raise UpdateFailure("Некоректний шлях у пакеті оновлення.")
    if len(name) > 240 or any(ord(char) < 32 for char in name):
        raise UpdateFailure("Недозволене ім'я файла в архіві.")
    raw_parts = name.split("/")
    if any(not part or part in {".", ".."} or len(part) > 120 for part in raw_parts):
        raise UpdateFailure("Некоректні сегменти шляху в архіві.")
    path = PurePosixPath(name)
    parts = path.parts
    if (not parts or name.startswith("/") or any(
            p in ("", ".", "..") or p.endswith((" ", ".")) or
            p.split(".")[0].upper() in WINDOWS_RESERVED or
            any(ch in p for ch in '<>"|?*') for p in parts)):
        raise UpdateFailure("Недозволений шлях у пакеті оновлення.")
    return path.as_posix()


def protected(name):
    parts = PurePosixPath(name).parts
    return (parts[0].lower() in PROTECTED_TOP or name.lower() in PROTECTED_NAMES
            or (len(parts) >= 2 and parts[0].lower() == "config" and
                (parts[1].lower() in PROTECTED_CONFIG or parts[1].lower() == "playlists"))
            or any(part.lower().startswith(".venv-") for part in parts))


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        if parts.scheme != "https" or parts.hostname not in {
                "api.github.com", "github.com", "codeload.github.com"} or parts.username:
            raise UpdateFailure("GitHub повернув недозволене перенаправлення.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_archive(sha, destination, *, opener=None):
    if not valid_sha(sha):
        raise UpdateFailure("SHA коміту недійсний.")
    opener = opener or build_opener(SafeRedirect)
    request = Request(API + "/zipball/" + sha, headers=HEADERS)
    destination = Path(destination)
    try:
        with opener.open(request, timeout=45) as response, destination.open("xb") as stream:
            if urlsplit(response.geturl()).hostname not in {"api.github.com", "codeload.github.com",
                                                              "github.com"}:
                raise UpdateFailure("Джерело архіву не підтверджено.")
            total = 0
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > MAX_ARCHIVE:
                    raise UpdateFailure("Архів GitHub перевищує обмеження розміру.")
                stream.write(block)
            if total < 100:
                raise UpdateFailure("Архів GitHub неповний.")
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def check_compatibility(reader):
    """Validate target release can still launch and recover its own updater.

    The reader loads a required file's contents from an extracted ZIP or a
    specific fetched Git object; never inspect the currently running code.
    """
    for name, markers in COMPATIBILITY_MARKERS.items():
        content = reader(name)
        if isinstance(content, bytes):
            if len(content) > 1_000_000:
                raise UpdateFailure("Файл лаунчера в гілці занадто великий.")
            content = content.decode("utf-8-sig", errors="replace")
        if not isinstance(content, str) or len(content) > 1_000_000:
            raise UpdateFailure("Некоректний формат лаунчера в обраній гілці.")
        if not all(marker in content for marker in markers):
            raise UpdateFailure(
                "Обрана гілка містить застарілий механізм оновлення або recovery. "
                "Оберіть сумісну Core V2 гілку."
            )


def extract_archive(archive, destination):
    destination = Path(destination)
    directory = {}
    windows_names = set()
    total = 0
    with zipfile.ZipFile(archive) as source:
        files = [entry for entry in source.infolist() if not entry.is_dir()]
        if not 0 < len(files) <= MAX_FILES:
            raise UpdateFailure("Неочікувана кількість файлів у GitHub-архіві.")
        prefixes = {entry.filename.split("/", 1)[0] for entry in files}
        if len(prefixes) != 1:
            raise UpdateFailure("Архів містить сторонні кореневі каталоги.")
        for entry in files:
            raw = entry.filename
            if "/" not in raw or raw.startswith("/") or "\\" in raw:
                raise UpdateFailure("Некоректна структура архіву GitHub.")
            rel = safe_path(raw.split("/", 1)[1])
            if protected(rel):
                # Older YEBOYBot commits track a few data/ and logs/ entries.
                # Never update those files or include them in the manifest.
                continue
            mode = (entry.external_attr >> 16) & 0o170000
            if mode not in (0, stat.S_IFREG):
                raise UpdateFailure("Архів містить посилання або спеціальні файли.")
            total += entry.file_size
            if (entry.file_size > MAX_ONE_FILE or total > MAX_CONTENT or
                    entry.compress_size == 0 and entry.file_size > 0):
                raise UpdateFailure("Архів перевищує дозволені обсяги.")
            folded = rel.casefold()
            if rel in directory or folded in windows_names:
                raise UpdateFailure("Дублікати або несумісні Windows-імена файлів у GitHub-архіві.")
            windows_names.add(folded)
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with source.open(entry) as src, target.open("xb") as out:
                counted = 0
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    counted += len(chunk)
                    if counted > MAX_ONE_FILE:
                        raise UpdateFailure("Файл пакета перевищує ліміт.")
                    digest.update(chunk)
                    out.write(chunk)
            if counted != entry.file_size:
                raise UpdateFailure("Файл архіву неповний.")
            directory[rel] = digest.hexdigest()
    if not REQUIRED <= directory.keys():
        raise UpdateFailure("Гілка не містить повного комплекту Core V2 для Windows.")
    check_compatibility(lambda name: (destination / name).read_bytes())
    return directory


def read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        raise UpdateFailure("Файл плану або маніфесту недоступний.")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise UpdateFailure("Заборонено використовувати посилання для службових файлів.")
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def os_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise UpdateFailure("Бот або інша операція ще працює; оновлення відкладено.") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def ensure_target(root, name):
    name = safe_path(name)
    if protected(name):
        raise UpdateFailure("Захищений локальний шлях не може оновлюватися.")
    cursor = root
    for segment in PurePosixPath(name).parts:
        cursor = cursor / segment
        if cursor.is_symlink() or (hasattr(cursor, "is_junction") and cursor.is_junction()):
            raise UpdateFailure("Посилання або junction у шляху оновлення заборонені.")
    if (root / name).exists() and not (root / name).is_file():
        raise UpdateFailure("Файлу оновлення заважає локальний каталог.")
    return root / name


def digest_file(path):
    sha = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def git(root, *args):
    try:
        result = subprocess.run(["git", *args], cwd=root, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="replace", timeout=120,
                                env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateFailure("Git недоступний або перевищено час очікування.") from error
    if result.returncode:
        raise UpdateFailure("Git не зміг підтвердити репозиторій або виконати операцію.")
    return result.stdout.strip()


def check_git(root):
    if not (root / ".git").is_dir() or (root / ".git").is_symlink():
        raise UpdateFailure("Не знайдено безпечний локальний Git-клон.")
    origin = git(root, "remote", "get-url", "origin").rstrip("/")
    if origin not in {REMOTE, REMOTE.removesuffix(".git")}:
        raise UpdateFailure("Git origin не є офіційним HTTPS-репозиторієм YEBOYBot.")
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise UpdateFailure("У Git-клоні є локальні зміни. Збережіть їх перед оновленням.")


def git_tree_files(root, commit):
    """Reject links, submodules, special paths and Windows filename collisions."""
    raw = git(root, "-c", "core.quotePath=false", "ls-tree", "-r",
              "--full-tree", commit)
    names = set()
    windows_names = set()
    for line in raw.splitlines():
        metadata, separator, filename = line.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 3 or len(fields[2]) != 40:
            raise UpdateFailure("Неможливо безпечно прочитати дерево Git-коміту.")
        mode, kind, _ = fields
        if mode not in {"100644", "100755"} or kind != "blob":
            raise UpdateFailure("Git-гілка містить символічні посилання або субмодулі.")
        name = safe_path(filename)
        folded = name.casefold()
        if folded in windows_names:
            raise UpdateFailure("Git-гілка містить конфліктні Windows-імена файлів.")
        names.add(name)
        windows_names.add(folded)
    return names


def git_apply(root, plan, transaction_file):
    check_git(root)
    previous = git(root, "rev-parse", "HEAD")
    original_branch = git(root, "branch", "--show-current")
    git(root, "fetch", "--no-tags", REMOTE, "refs/heads/" + plan["branch"])
    remote_commit = git(root, "rev-parse", "FETCH_HEAD")
    if remote_commit.lower() != plan["sha"].lower():
        raise UpdateFailure("Гілка змінилася з часу вибору. Перегляньте список знову.")
    names = git_tree_files(root, remote_commit)
    if not REQUIRED <= names:
        raise UpdateFailure("Гілка не підтримує потрібний Core V2 лаунчер.")
    check_compatibility(lambda name: git(root, "show", f"{remote_commit}:{name}"))
    changed = set(filter(None, git(root, "diff", "--name-only", previous, remote_commit).splitlines()))
    if any(protected(name) for name in changed):
        raise UpdateFailure("Гілка намагається замінити секрети або локальні дані.")
    added = set(filter(None, git(root, "diff", "--diff-filter=A", "--name-only",
                                 previous, remote_commit).splitlines()))
    if any((root / name).exists() and name not in
           set(git(root, "ls-tree", "-r", "--name-only", previous).splitlines()) for name in added):
        raise UpdateFailure("Нові Git-файли конфліктують з локальними.")
    transaction = {"mode": "git", "previous": previous, "previous_branch": original_branch,
                   "new_commit": remote_commit, "changed": sorted(changed),
                   "branch": plan["branch"], "was_running": bool(plan.get("was_running"))}
    # Record recovery information before Git modifies the working tree.
    write_json(transaction_file, transaction)
    if previous != remote_commit:
        # Detached HEAD preserves the previous named branch and history.
        git(root, "switch", "--detach", remote_commit)
    return transaction


def restore_git(root, transaction):
    previous = transaction["previous"]
    original_branch = transaction.get("previous_branch")
    if original_branch:
        git(root, "switch", original_branch)
        if git(root, "rev-parse", "HEAD") != previous:
            raise UpdateFailure("Попередня гілка змінилася. Потрібне ручне відновлення.")
    else:
        git(root, "switch", "--detach", previous)


def backup_sqlite(root, plan):
    source_name = plan.get("sqlite_path")
    if not isinstance(source_name, str) or not source_name:
        raise UpdateFailure("Не визначено безпечний шлях SQLite.")
    source = Path(source_name)
    if not source.is_absolute() or source.is_symlink():
        raise UpdateFailure("Некоректний файл SQLite.")
    if not source.exists():
        return None
    if not source.is_file():
        raise UpdateFailure("SQLite не є звичайним файлом.")
    folder = root / "backups"
    if folder.is_symlink():
        raise UpdateFailure("Каталог резервних копій є посиланням.")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ("update-" + plan["id"] + ".db")
    if target.exists():
        raise UpdateFailure("Резервна копія з цим ID уже існує.")
    try:
        with sqlite3.connect(source) as original, sqlite3.connect(target) as output:
            original.backup(output)
            if output.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise UpdateFailure("SQLite backup не пройшов перевірку цілісності.")
        return str(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise


def zip_apply(root, plan, work, old_manifest):
    archive = work / "download.zip"
    extracted = work / "extracted"
    extracted.mkdir()
    download_archive(plan["sha"], archive)
    fresh = extract_archive(archive, extracted)
    previously = old_manifest.get("files", {}) if old_manifest else {}
    if not isinstance(previously, dict):
        raise UpdateFailure("Маніфест попереднього встановлення некоректний.")
    obsolete = set(previously) - set(fresh)
    for name in set(fresh) | obsolete:
        current = ensure_target(root, name)
        # Following first adoption, new repository paths must not replace
        # a user's formerly unmanaged file, even with a backup.
        if old_manifest is not None and name in fresh and name not in previously and current.exists():
            raise UpdateFailure("Новий файл релізу конфліктує з локальним файлом.")
        if name in previously and current.is_file():
            if digest_file(current) != previously[name]:
                raise UpdateFailure("Локально змінено програмний файл; оновлення не перезапише його.")
    backup = root / "backups" / ("code-update-" + plan["id"])
    if backup.exists() or backup.is_symlink():
        raise UpdateFailure("Резервна копія з таким ID вже існує.")
    backup.mkdir(parents=True)
    replaced = []
    created = []
    for name in sorted(set(fresh) | obsolete):
        target = root / name
        if target.is_file():
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
            replaced.append(name)
        elif name in fresh:
            created.append(name)
    transaction = {"mode": "zip", "new_commit": plan["sha"], "new_files": fresh,
                   "replaced": replaced, "created": created, "backup_dir": str(backup),
                   "previous_manifest": old_manifest}
    write_json(root / "data/v2/update-transaction.json", transaction)
    try:
        for name in sorted(fresh):
            target = ensure_target(root, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(extracted / name, target)
        for name in sorted(obsolete):
            (root / name).unlink(missing_ok=True)
    except Exception:
        restore_zip(root, transaction)
        raise
    return transaction


def restore_zip(root, transaction):
    backup = Path(transaction["backup_dir"])
    for name in transaction["created"]:
        current = ensure_target(root, name)
        current.unlink(missing_ok=True)
    for name in transaction["replaced"]:
        target = ensure_target(root, name)
        old = backup / name
        if not old.is_file() or old.is_symlink():
            raise UpdateFailure("Не знайдено файлів попередньої версії для відновлення.")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old, target)


def state_paths(root):
    data = root / "data/v2"
    return (data / "update-plan.json", data / "update-transaction.json",
            data / "install-manifest.json", data / "update-report.json")


def report(root, phase, *, error=None):
    value = {"phase": phase, "utc": datetime.now(timezone.utc).isoformat()}
    if error is not None:
        value["error_type"] = type(error).__name__
        # Only our own fixed Ukrainian updater errors are safe for local logs;
        # unexpected system exceptions may include paths, DSNs or secrets.
        if isinstance(error, UpdateFailure):
            value["reason"] = str(error)[:240]
    write_json(state_paths(root)[3], value)


def apply(root):
    root = Path(root).resolve()
    plan_path, txn_path, installed_path, _ = state_paths(root)
    plan = read_json(plan_path)
    if (not isinstance(plan, dict) or not valid_sha(plan.get("sha")) or
            not valid_branch(plan.get("branch")) or
            plan.get("mode") not in {"zip", "git"} or
            not isinstance(plan.get("id"), str) or
            not re.fullmatch(r"[a-f0-9]{32}", plan["id"])):
        raise UpdateFailure("План оновлення некоректний.")
    if txn_path.exists():
        raise UpdateFailure("Попередню транзакцію ще не завершено; відновіть її.")
    old_manifest = read_json(installed_path) if installed_path.exists() else None
    if plan["mode"] == "git" and not (root / ".git").is_dir():
        raise UpdateFailure("Обраний режим Git несумісний із цією папкою.")
    if plan["mode"] == "zip" and (root / ".git").exists():
        raise UpdateFailure("ZIP-режим не може змінювати наявний Git-клон.")
    with os_lock(root / "data/v2/launch.lock"), os_lock(root / "data/v2/bot.lock"):
        work = root / "data/v2" / (".update-work-" + plan["id"])
        work.mkdir(parents=True, exist_ok=False)
        try:
            # The Discord process has stopped. Snapshot SQLite before any code change.
            backup_sqlite(root, plan)
            if plan["mode"] == "git":
                transaction = git_apply(root, plan, txn_path)
            else:
                # Download/extract and validate BEFORE changing application files.
                transaction = zip_apply(root, plan, work, old_manifest)
            transaction["branch"] = plan["branch"]
            transaction["was_running"] = bool(plan.get("was_running"))
            write_json(txn_path, transaction)
            report(root, "code_updated")
            return 0
        except Exception as error:
            report(root, "failed", error=error)
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)


def rollback(root):
    root = Path(root).resolve()
    plan_path, txn_path, installed_path, _ = state_paths(root)
    if not txn_path.exists():
        # An unsuccessful download or preflight did not modify installed code.
        plan_path.unlink(missing_ok=True)
        report(root, "cancelled_before_apply")
        return 0
    transaction = read_json(txn_path)
    with os_lock(root / "data/v2/launch.lock"), os_lock(root / "data/v2/bot.lock"):
        if transaction["mode"] == "zip":
            restore_zip(root, transaction)
            old = transaction.get("previous_manifest")
            if old is None:
                installed_path.unlink(missing_ok=True)
            else:
                write_json(installed_path, old)
        elif transaction["mode"] == "git":
            restore_git(root, transaction)
        else:
            raise UpdateFailure("Невідомий режим rollback.")
        txn_path.unlink(missing_ok=True)
        plan_path.unlink(missing_ok=True)
        report(root, "rolled_back")
    return 0


def confirm(root):
    root = Path(root).resolve()
    plan_path, txn_path, installed_path, _ = state_paths(root)
    plan, transaction = read_json(plan_path), read_json(txn_path)
    if (plan.get("sha") != transaction.get("new_commit") or
            plan.get("branch") != transaction.get("branch")):
        raise UpdateFailure("Коміт перевірки не відповідає встановленій версії.")
    if transaction["mode"] == "zip":
        files = transaction["new_files"]
        if any(digest_file(ensure_target(root, name)) != sha for name, sha in files.items()):
            raise UpdateFailure("Під час перевірки змінено програмні файли.")
    else:
        if git(root, "rev-parse", "HEAD") != plan["sha"]:
            raise UpdateFailure("Git HEAD відрізняється від обраної версії.")
        files = {}
    write_json(installed_path, {"branch": plan["branch"], "commit": plan["sha"],
                               "mode": transaction["mode"], "files": files,
                               "installed_utc": datetime.now(timezone.utc).isoformat()})
    report(root, "verified")
    txn_path.unlink(missing_ok=True)
    plan_path.unlink(missing_ok=True)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] not in {"apply", "rollback", "confirm"}:
        print("Використання: update_worker.py apply|rollback|confirm ROOT")
        return 2
    command, root = argv
    try:
        if command == "apply":
            return apply(root)
        if command == "rollback":
            return rollback(root)
        return confirm(root)
    except Exception as error:
        print("Оновлення не завершено. Етап:", command, "Тип помилки:", type(error).__name__)
        if isinstance(error, UpdateFailure):
            print("Причина:", str(error))
        print("Перегляньте data/v2/update-report.json. Не видаляйте резервні копії.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
