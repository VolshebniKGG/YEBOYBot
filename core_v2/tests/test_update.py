import subprocess

import pytest

from tools.launcher import update


def command(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


@pytest.fixture
def repo(tmp_path):
    command(tmp_path, "init", "-b", update.BRANCH)
    command(tmp_path, "config", "user.name", "Test")
    command(tmp_path, "config", "user.email", "test@example.invalid")
    command(tmp_path, "remote", "add", "origin", update.REMOTE)
    (tmp_path / ".gitignore").write_text("data/\nbackups/\n.env.v2\n")
    (tmp_path / "code.txt").write_text("old")
    command(tmp_path, "add", ".")
    command(tmp_path, "commit", "-m", "base")
    old = command(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "code.txt").write_text("new")
    command(tmp_path, "commit", "-am", "update")
    target = command(tmp_path, "rev-parse", "HEAD")
    command(tmp_path, "update-ref", f"refs/remotes/origin/{update.BRANCH}", target)
    # Disposable test repository only; production updater never performs reset.
    command(tmp_path, "checkout", "-b", "temporary", old)
    command(tmp_path, "branch", "-f", update.BRANCH, old)
    command(tmp_path, "checkout", update.BRANCH)
    return tmp_path, old, target


def test_clean_fast_forward_preserves_config(repo, monkeypatch):
    root, old, target = repo
    (root / ".env.v2").write_text("DISCORD_TOKEN=secret\n")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(update, "python", lambda *args: None)
    selected = update.plan(root, fetch=False)
    assert selected["old"] == old
    assert update.apply(selected, root)
    assert command(root, "rev-parse", "HEAD") == target
    assert (root / ".env.v2").read_text() == "DISCORD_TOKEN=secret\n"
    assert "secret" not in (root / "data/v2/update-report.json").read_text()


def test_dirty_worktree_is_preserved(repo):
    root, _, _ = repo
    (root / "code.txt").write_text("my changes")
    with pytest.raises(update.UpdateError, match="локальні"):
        update.plan(root, fetch=False)
    assert (root / "code.txt").read_text() == "my changes"


def test_wrong_branch_rejected(repo):
    root, _, _ = repo
    command(root, "checkout", "-b", "main")
    with pytest.raises(update.UpdateError, match="feature/core-v2"):
        update.plan(root, fetch=False)


def test_divergence_rejected(repo):
    root, _, _ = repo
    (root / "local.txt").write_text("local")
    command(root, "add", ".")
    command(root, "commit", "-m", "local")
    with pytest.raises(update.UpdateError, match="розійшлися"):
        update.plan(root, fetch=False)


def test_failed_verification_records_phase_without_reset(repo, monkeypatch):
    root, _, target = repo
    monkeypatch.delenv("DATABASE_URL", raising=False)
    def fail(*args):
        raise update.UpdateError("test")
    monkeypatch.setattr(update, "python", fail)
    with pytest.raises(update.UpdateError):
        update.apply(update.plan(root, fetch=False), root)
    assert command(root, "rev-parse", "HEAD") == target
    assert '"error_type": "UpdateError"' in (root / "data/v2/update-report.json").read_text()


def test_postgresql_refused_before_stop(repo, monkeypatch):
    root, old, _ = repo
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:secret@localhost/db")
    monkeypatch.setattr(update.process, "stop", lambda *_: pytest.fail("must not stop"))
    with pytest.raises(RuntimeError):
        update.apply(update.plan(root, fetch=False), root)
    assert command(root, "rev-parse", "HEAD") == old


def test_existing_ignored_file_cannot_be_overwritten(repo):
    root, _, target = repo
    command(root, "checkout", "-b", "addition", target)
    (root / "data").mkdir()
    (root / "data/settings.json").write_text("remote")
    command(root, "add", "-f", "data/settings.json")
    command(root, "commit", "-m", "bad data update")
    command(root, "update-ref", f"refs/remotes/origin/{update.BRANCH}", command(root, "rev-parse", "HEAD"))
    command(root, "checkout", update.BRANCH)
    (root / "data").mkdir(exist_ok=True)
    (root / "data/settings.json").write_text("local")
    with pytest.raises(update.UpdateError):
        update.plan(root, fetch=False)
    assert (root / "data/settings.json").read_text() == "local"


@pytest.mark.parametrize("name", ["налаштування.txt", "local settings.txt"])
def test_ignored_unicode_and_space_names_preserved(repo, name):
    root, _, target = repo
    command(root, "checkout", "-b", "unicode-addition", target)
    (root / name).write_text("remote", encoding="utf-8")
    command(root, "add", name)
    command(root, "commit", "-m", "new file")
    command(root, "update-ref", f"refs/remotes/origin/{update.BRANCH}", command(root, "rev-parse", "HEAD"))
    command(root, "checkout", update.BRANCH)
    with (root / ".git/info/exclude").open("a", encoding="utf-8") as stream:
        stream.write("\n" + name + "\n")
    (root / name).write_text("local data", encoding="utf-8")
    with pytest.raises(update.UpdateError, match="вже існує"):
        update.plan(root, fetch=False)
    assert (root / name).read_text(encoding="utf-8") == "local data"
