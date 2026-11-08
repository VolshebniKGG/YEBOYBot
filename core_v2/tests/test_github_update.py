"""Offline regression tests for branch picker, ZIP safety, rollback and updates."""
import json
import shutil
import subprocess
import sqlite3
import stat
import uuid
import zipfile

import pytest

from tools.launcher import github_update
from tools import update_worker as worker


BASE = {
    "YEBOYBot.cmd": "OLD SCRIPT",
    "tools/windows_setup.ps1": "OLD SETUP",
    "tools/update_worker.py": "OLD WORKER",
    "tools/launcher/__main__.py": "OLD MENU",
    "core_v2/__main__.py": "OLD CORE",
    "tools/launcher/github_update.py": "OLD GITHUB MENU",
    "tools/launcher/verify_update.py": "OLD VERIFIER",
}
NEW = {key: "NEW " + key for key in BASE}
NEW["tools/windows_setup.ps1"] += (
    "\\nfunction Invoke-YeboyGitHubUpdate {}"
    "\\nfunction Restore-YeboyInterruptedUpdate {}"
)
NEW["tools/update_worker.py"] += "\\ndef rollback(root): pass\\ndef confirm(root): pass"
NEW["tools/launcher/__main__.py"] += "\\nUPDATE_EXIT_CODE = 26"


@pytest.fixture
def installation(tmp_path):
    root = tmp_path / "bot"
    for name, text in BASE.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (root / "data/v2").mkdir(parents=True)
    (root / "config").mkdir()
    (root / ".env.v2").write_text("DISCORD_TOKEN=topsecret", encoding="utf-8")
    (root / "config/options.ini").write_text("personal=1", encoding="utf-8")
    db = root / "data/v2/yeboy.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE things (number INTEGER NOT NULL)")
        connection.execute("INSERT INTO things VALUES (42)")
    return root


def prepare(installation, sha="a" * 40, branch="improvement/probot-readiness"):
    payload = {"id": uuid.uuid4().hex, "mode": "zip", "branch": branch,
               "sha": sha, "sqlite_path": str(installation / "data/v2/yeboy.db"),
               "was_running": False}
    worker.write_json(installation / "data/v2/update-plan.json", payload)
    return payload


def archive(path, content):
    with zipfile.ZipFile(path, "w") as stream:
        for name, value in content.items():
            stream.writestr("VolshebniKGG-YEBOYBot-000000/" + name, value)


def patch_archive(monkeypatch, path):
    monkeypatch.setattr(worker, "download_archive",
                        lambda sha, target: shutil.copyfile(path, target))


def test_branch_validation_and_safe_paths():
    for value in ("main", "feature/core-v2", "improvement/probot-readiness"):
        assert worker.valid_branch(value)
    for bad in ("", "../x", "/absolute", "x//y", "x..y", "x.lock", "x/", "not ok",
                "x\tfoo", "a" * 150):
        assert not worker.valid_branch(bad)
    for bad in ("../x", "/etc/passwd", "a/./b", "a/../b", "a\\b", "data/v2/y",
                "folder/CON.txt", "bad\x00name", "x//y", "file:thing", "x\ny"):
        if worker.protected(bad):
            assert worker.protected(bad)
        else:
            with pytest.raises(worker.UpdateFailure):
                worker.safe_path(bad)
    assert worker.safe_path("tools/launcher/update.py") == "tools/launcher/update.py"
    assert worker.protected(".env.v2")
    assert worker.protected("config/autoplaylist.txt")
    assert worker.protected("bin/ffmpeg/bin.exe")


def test_choose_branch_pages_and_cancellation():
    branches = [{"name": f"feature/test-{i}", "sha": f"{i:040x}"} for i in range(15)]
    answers = iter(["n", "15"])
    result = github_update.choose_branch(branches, reader=lambda _: next(answers),
                                         printer=lambda _: None)
    assert result == branches[14]
    assert github_update.choose_branch(branches, reader=lambda _: "0",
                                       printer=lambda _: None) is None


def test_list_branches_uses_official_api_and_deduplicates():
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self, limit):
            return json.dumps([
                {"name": "feature/core-v2", "commit": {"sha": "a" * 40}},
                {"name": "feature/core-v2", "commit": {"sha": "a" * 40}},
                {"name": "main", "commit": {"sha": "b" * 40}},
            ]).encode()
    def fake(request, timeout):
        assert request.full_url.startswith("https://api.github.com/repos/VolshebniKGG/YEBOYBot/branches?")
        assert timeout <= 20
        return Response()
    branches = github_update.list_branches(open_url=fake)
    assert [item["name"] for item in branches] == ["feature/core-v2", "main"]


def test_zip_menu_writes_selection_without_running_update(installation, monkeypatch):
    monkeypatch.setattr(github_update.storage, "database_path",
                        lambda root: root / "data/v2/yeboy.db")
    monkeypatch.setattr(github_update.process, "locked", lambda root: False)
    selected = [{"name": "main", "sha": "b" * 40},
                {"name": "improvement/probot-readiness", "sha": "c" * 40}]
    answers = iter(["2", "ОНОВИТИ"])
    printed = []
    result = github_update.menu(installation, reader=lambda _: next(answers),
                                printer=printed.append, branches=selected)
    assert result == github_update.EXIT_TO_EXTERNAL_UPDATER == 26
    plan = worker.read_json(installation / "data/v2/update-plan.json")
    assert plan["branch"] == "improvement/probot-readiness"
    assert plan["mode"] == "zip" and plan["sha"] == "c" * 40
    assert (installation / ".env.v2").read_text() == "DISCORD_TOKEN=topsecret"


def test_zip_install_confirm_preserves_secrets_and_snapshots_sqlite(installation, monkeypatch, tmp_path):
    package = tmp_path / "source.zip"
    archive(package, {**NEW, "docs/new-feature.md": "new docs"})
    patch_archive(monkeypatch, package)
    plan = prepare(installation)
    assert worker.apply(installation) == 0
    assert (installation / "YEBOYBot.cmd").read_text() == NEW["YEBOYBot.cmd"]
    pending = worker.read_json(installation / "data/v2/update-transaction.json")
    assert pending["branch"] == plan["branch"]
    assert "YEBOYBot.cmd" in pending["replaced"]
    assert "docs/new-feature.md" in pending["created"]
    with sqlite3.connect(installation / "backups" / ("update-" + plan["id"] + ".db")) as db:
        assert db.execute("SELECT number FROM things").fetchone() == (42,)
    assert worker.confirm(installation) == 0
    manifest = worker.read_json(installation / "data/v2/install-manifest.json")
    assert manifest["mode"] == "zip" and manifest["commit"] == "a" * 40
    assert set(manifest["files"]) == set(NEW) | {"docs/new-feature.md"}
    assert not (installation / "data/v2/update-transaction.json").exists()
    assert (installation / ".env.v2").read_text() == "DISCORD_TOKEN=topsecret"
    assert (installation / "config/options.ini").read_text() == "personal=1"


def test_zip_rollback_restores_old_code_and_removes_added_files(installation, monkeypatch, tmp_path):
    package = tmp_path / "source.zip"
    archive(package, {**NEW, "notes.md": "added"})
    patch_archive(monkeypatch, package)
    prepare(installation)
    assert worker.apply(installation) == 0
    assert worker.rollback(installation) == 0
    for name, contents in BASE.items():
        assert (installation / name).read_text() == contents
    assert not (installation / "notes.md").exists()
    assert not (installation / "data/v2/update-plan.json").exists()
    assert (installation / ".env.v2").read_text() == "DISCORD_TOKEN=topsecret"


def test_zip_second_update_refuses_changed_tracked_source(installation, monkeypatch, tmp_path):
    package = tmp_path / "first.zip"
    archive(package, {**NEW, "obsolete.md": "old docs"})
    patch_archive(monkeypatch, package)
    prepare(installation)
    assert worker.apply(installation) == 0
    assert worker.confirm(installation) == 0
    (installation / "YEBOYBot.cmd").write_text("LOCAL EDIT")
    next_package = tmp_path / "next.zip"
    archive(next_package, {**NEW, "notes.md": "new"})
    patch_archive(monkeypatch, next_package)
    prepare(installation, sha="b" * 40)
    with pytest.raises(worker.UpdateFailure, match="Локально змінено"):
        worker.apply(installation)
    assert (installation / "YEBOYBot.cmd").read_text() == "LOCAL EDIT"
    assert (installation / "obsolete.md").exists()
    assert worker.rollback(installation) == 0


@pytest.mark.parametrize("unsafe", [
    "../outside.txt", "other/../../outside.txt",
])
def test_zip_rejects_unsafe_members_before_applying(installation, monkeypatch, tmp_path, unsafe):
    package = tmp_path / "unsafe.zip"
    archive(package, {**NEW, unsafe: "payload"})
    patch_archive(monkeypatch, package)
    prepare(installation)
    with pytest.raises(worker.UpdateFailure):
        worker.apply(installation)
    assert (installation / "YEBOYBot.cmd").read_text() == BASE["YEBOYBot.cmd"]
    assert (installation / ".env.v2").read_text() == "DISCORD_TOKEN=topsecret"
    assert worker.rollback(installation) == 0


def test_zip_rejects_symlinks_and_missing_core_launcher(tmp_path):
    archive_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as out:
        info = zipfile.ZipInfo("root/link.py")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        out.writestr(info, "target")
    with pytest.raises(worker.UpdateFailure, match="посилання"):
        worker.extract_archive(archive_path, tmp_path / "extract")
    archive(archive_path, {"YEBOYBot.cmd": "broken-only"})
    with pytest.raises(worker.UpdateFailure, match="повного комплекту"):
        worker.extract_archive(archive_path, tmp_path / "extract2")


def test_report_never_includes_token(installation, monkeypatch, tmp_path):
    package = tmp_path / "source.zip"
    archive(package, NEW)
    patch_archive(monkeypatch, package)
    prepare(installation)
    assert worker.apply(installation) == 0
    assert worker.confirm(installation) == 0
    report = (installation / "data/v2/update-report.json").read_text()
    assert "topsecret" not in report
    assert json.loads(report)["phase"] == "verified"


@pytest.mark.parametrize("protected_name", [
    "data/music/cache.json", "logs/bot.log", ".env.v2",
    "config/options.ini", "bin/ffmpeg/ffmpeg.exe",
])
def test_zip_skips_private_files_tracked_in_old_commits(
        installation, monkeypatch, tmp_path, protected_name):
    package = tmp_path / "mixed.zip"
    archive(package, {**NEW, protected_name: "MUST NOT INSTALL"})
    patch_archive(monkeypatch, package)
    prepare(installation)
    assert worker.apply(installation) == 0
    assert worker.confirm(installation) == 0
    manifest = worker.read_json(installation / "data/v2/install-manifest.json")
    assert protected_name not in manifest["files"]
    if protected_name == ".env.v2":
        assert (installation / protected_name).read_text() == "DISCORD_TOKEN=topsecret"
    elif protected_name == "config/options.ini":
        assert (installation / protected_name).read_text() == "personal=1"
    else:
        assert not (installation / protected_name).exists()


def test_zip_new_path_wont_clobber_unmanaged_file_after_adoption(
        installation, monkeypatch, tmp_path):
    first = tmp_path / "first.zip"
    archive(first, NEW)
    patch_archive(monkeypatch, first)
    prepare(installation)
    assert worker.apply(installation) == 0
    assert worker.confirm(installation) == 0
    local = installation / "docs/local-user-notes.md"
    local.parent.mkdir(parents=True)
    local.write_text("PERSONAL DOCUMENT", encoding="utf-8")
    second = tmp_path / "second.zip"
    archive(second, {**NEW, "docs/local-user-notes.md": "INCOMING DOC"})
    patch_archive(monkeypatch, second)
    prepare(installation, sha="b" * 40)
    with pytest.raises(worker.UpdateFailure, match="конфліктує"):
        worker.apply(installation)
    assert local.read_text(encoding="utf-8") == "PERSONAL DOCUMENT"
    assert worker.rollback(installation) == 0


def test_git_installed_version_uses_actual_head_not_stale_manifest(installation, monkeypatch):
    (installation / ".git").mkdir()
    worker.write_json(installation / "data/v2/install-manifest.json",
                      {"commit": "a" * 40, "branch": "old-branch", "files": {}})
    monkeypatch.setattr(worker, "git", lambda root, *args: "b" * 40
                        if args == ("rev-parse", "HEAD") else "new-branch")
    assert github_update.installed_version(installation) == ("b" * 40, "new-branch")


@pytest.mark.skipif(shutil.which("git") is None, reason="Git CLI is not installed")
def test_git_branch_switch_is_pinned_and_rollback_restores_original_branch(
        tmp_path, monkeypatch):
    root = tmp_path / "git-install"
    root.mkdir()
    def command(*args):
        completed = subprocess.run(["git", *args], cwd=root, check=True,
                                   text=True, capture_output=True, timeout=25)
        return completed.stdout.strip()
    command("init", "-b", "feature/core-v2")
    command("config", "user.name", "Offline Updater Tests")
    command("config", "user.email", "tests@example.invalid")
    command("remote", "add", "origin", worker.REMOTE)
    for name, value in BASE.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    (root / ".gitignore").write_text("data/\nbackups/\n", encoding="utf-8")
    command("add", ".")
    command("commit", "-m", "initial")
    previous = command("rev-parse", "HEAD")
    command("checkout", "-b", "improvement/probot-readiness")
    for name, value in NEW.items():
        (root / name).write_text(value, encoding="utf-8")
    command("commit", "-am", "update")
    target = command("rev-parse", "HEAD")
    command("checkout", "feature/core-v2")
    (root / "data/v2").mkdir(parents=True)
    plan = {"id": uuid.uuid4().hex, "mode": "git",
            "branch": "improvement/probot-readiness", "sha": target,
            "sqlite_path": str(root / "data/v2/missing.db"), "was_running": False}
    worker.write_json(root / "data/v2/update-plan.json", plan)
    actual_git = worker.git
    def offline_git(directory, *args):
        if args[:1] == ("fetch",):
            return ""
        if args == ("rev-parse", "FETCH_HEAD"):
            return target
        return actual_git(directory, *args)
    monkeypatch.setattr(worker, "git", offline_git)
    assert worker.apply(root) == 0
    assert command("rev-parse", "HEAD") == target
    assert command("branch", "--show-current") == ""
    assert (root / "YEBOYBot.cmd").read_text(encoding="utf-8") == NEW["YEBOYBot.cmd"]
    assert worker.rollback(root) == 0
    assert command("branch", "--show-current") == "feature/core-v2"
    assert command("rev-parse", "HEAD") == previous
    assert (root / "YEBOYBot.cmd").read_text(encoding="utf-8") == BASE["YEBOYBot.cmd"]


def test_incompatible_zip_branch_rejected_before_replacing_code(
        installation, monkeypatch, tmp_path):
    package = tmp_path / "outdated.zip"
    legacy_files = dict(NEW)
    legacy_files["tools/windows_setup.ps1"] = "OUTDATED WINDOWS LAUNCHER"
    archive(package, legacy_files)
    patch_archive(monkeypatch, package)
    prepare(installation)
    with pytest.raises(worker.UpdateFailure, match="застарілий"):
        worker.apply(installation)
    assert (installation / "YEBOYBot.cmd").read_text() == BASE["YEBOYBot.cmd"]
    assert not (installation / "data/v2/update-transaction.json").exists()
    assert worker.rollback(installation) == 0


def test_release_requires_updater_and_verifier_not_only_discord_core(
        installation, monkeypatch, tmp_path):
    package = tmp_path / "partial.zip"
    partial = dict(NEW)
    del partial["tools/launcher/github_update.py"]
    archive(package, partial)
    patch_archive(monkeypatch, package)
    prepare(installation)
    with pytest.raises(worker.UpdateFailure, match="повного комплекту"):
        worker.apply(installation)
    assert (installation / "YEBOYBot.cmd").read_text() == BASE["YEBOYBot.cmd"]
    assert worker.rollback(installation) == 0


def test_worker_reports_own_errors_but_never_unexpected_secret_details(
        installation):
    worker.report(installation, "failed", error=worker.UpdateFailure(
        "Ця гілка не підтримується."))
    data = worker.read_json(installation / "data/v2/update-report.json")
    assert data["reason"] == "Ця гілка не підтримується."
    worker.report(installation, "failed",
                  error=OSError("private database token=never-report-this"))
    data = worker.read_json(installation / "data/v2/update-report.json")
    assert "reason" not in data
    assert "never-report-this" not in json.dumps(data)


def test_zip_rejects_case_collisions_before_touching_existing_code(
        installation, monkeypatch, tmp_path):
    package = tmp_path / "collision.zip"
    archive(package, {**NEW, "tools/new_settings.py": "safe",
                      "tools/NEW_SETTINGS.py": "different"})
    patch_archive(monkeypatch, package)
    prepare(installation)
    with pytest.raises(worker.UpdateFailure, match="Windows-імена"):
        worker.apply(installation)
    assert (installation / "YEBOYBot.cmd").read_text() == BASE["YEBOYBot.cmd"]
    assert worker.rollback(installation) == 0


@pytest.mark.parametrize("mode,kind", [
    ("120000", "blob"), ("160000", "commit"), ("100600", "blob"),
])
def test_git_preflight_rejects_links_submodules_and_special_modes(
        installation, monkeypatch, mode, kind):
    git_oid = "a" * 40
    raw = f"{mode} {kind} {git_oid}\\ttools/new.py"
    monkeypatch.setattr(worker, "git", lambda root, *args: raw)
    with pytest.raises(worker.UpdateFailure, match="символічні|субмодулі"):
        worker.git_tree_files(installation, git_oid)


def test_git_preflight_rejects_case_collisions_and_bad_paths(
        installation, monkeypatch):
    oid = "a" * 40
    raw = ("100644 blob " + oid + "\\ttools/Service.py\\n"
           "100644 blob " + oid + "\\ttools/service.py")
    monkeypatch.setattr(worker, "git", lambda root, *args: raw)
    with pytest.raises(worker.UpdateFailure, match="Windows-імена"):
        worker.git_tree_files(installation, oid)
    monkeypatch.setattr(worker, "git", lambda root, *args:
                        "100644 blob " + oid + "\\tcore_v2/../outside.py")
    with pytest.raises(worker.UpdateFailure, match="шлях"):
        worker.git_tree_files(installation, oid)
