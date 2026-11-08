"""Offline regression tests for branch picker, ZIP safety, rollback and updates."""
from io import BytesIO
import json
from pathlib import Path
import shutil
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
}
NEW = {key: "NEW " + key for key in BASE}


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
    "../outside.txt", "data/v2/token.txt", ".env.v2", "other/../../outside.txt",
    "config/options.ini", "bin/ffmpeg/ffmpeg.exe", "a\\b.txt",
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
