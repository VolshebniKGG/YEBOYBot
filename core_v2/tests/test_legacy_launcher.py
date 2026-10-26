"""Legacy launch isolation, failures and safe configuration checks; no Discord login."""
import builtins
import hashlib
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace
import venv

import pytest

from tools import legacy_launcher as legacy
from core_v2.runtime import InstanceLock


def write_config(root, token="unit-test-token", extra=""):
    directory = root / "config"
    directory.mkdir(exist_ok=True)
    path = directory / "options.ini"
    path.write_text(f"[Credentials]\nToken = {token}\n{extra}\n[Chat]\nCommandPrefix = !\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def isolated_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("Бот з пробілами")
    # Real venv/probe integration, with no downloads or global package changes.
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(root / ".venv-legacy")
    return root


def test_child_environment_ignores_package_and_python_injection():
    original = {
        "PATH": "ordinary-search-path", "HTTPS_PROXY": "http://proxy.invalid:8080",
        "REQUESTS_CA_BUNDLE": "/trusted/ca.pem", "PYTHONPATH": "/foreign/packages",
        "PYTHONHOME": "/foreign/python", "PYTHONUSERBASE": "/foreign/user",
        "PYTHONSTARTUP": "/foreign/startup.py", "VIRTUAL_ENV": "/core/.venv-v2",
        "PIP_TARGET": "/global/site-packages", "PIP_PREFIX": "/global",
        "PIP_USER": "true", "PIP_INDEX_URL": "https://hidden@invalid/simple",
        "PIP_CONFIG_FILE": "/untrusted/pip.ini",
    }
    result = legacy.clean_environment(original)
    assert original["PIP_TARGET"] == "/global/site-packages"
    assert result["PATH"] == original["PATH"]
    assert result["HTTPS_PROXY"] == original["HTTPS_PROXY"]
    assert result["REQUESTS_CA_BUNDLE"] == original["REQUESTS_CA_BUNDLE"]
    assert result["PYTHONUTF8"] == "1"
    assert result["PIP_CONFIG_FILE"] == os.devnull
    assert not {key for key in result if key.startswith("PYTHON")} - {"PYTHONUTF8"}
    assert not {key for key in result if key.startswith("PIP_")} - {"PIP_CONFIG_FILE"}
    assert "VIRTUAL_ENV" not in result


def test_real_venv_probe_handles_unicode_spaces_and_poisoned_environment(isolated_root, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/foreign/packages")
    monkeypatch.setenv("PYTHONHOME", "/foreign/python")
    # Successful probe proves the exact venv starts with -I despite inherited values.
    legacy.validate_environment(isolated_root, require_packages=False)
    assert legacy.legacy_python(isolated_root).is_file()


def test_real_venv_with_missing_packages_is_not_launchable(isolated_root):
    with pytest.raises(legacy.LegacyError):
        legacy.validate_environment(isolated_root, require_packages=True)


def test_pycord_distribution_in_legacy_environment_is_refused(tmp_path):
    environment = tmp_path / ".venv-legacy"
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(environment)
    site = environment / ("Lib/site-packages" if os.name == "nt" else "lib/python3.12/site-packages")
    metadata = site / "py_cord-2.6.1.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: py-cord\nVersion: 2.6.1\n", encoding="utf-8")
    with pytest.raises(legacy.LegacyError, match="py-cord"):
        legacy.validate_environment(tmp_path)


def test_system_site_packages_environment_is_refused(tmp_path):
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(tmp_path / ".venv-legacy")
    with pytest.raises(legacy.LegacyError):
        legacy.validate_environment(tmp_path)


def test_pth_cannot_add_arbitrary_external_package_directory(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    environment = root / ".venv-legacy"
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(environment)
    site = environment / ("Lib/site-packages" if os.name == "nt" else "lib/python3.12/site-packages")
    # This directory deliberately has no 'site-packages' component in its name.
    external = tmp_path / "foreign-modules"
    external.mkdir()
    (site / "foreign.pth").write_text(str(external) + "\n", encoding="utf-8")
    with pytest.raises(legacy.LegacyError, match="сторонній шлях"):
        legacy.validate_environment(root, require_packages=False)


def test_matching_stamp_does_not_trust_package_versions_that_drifted(tmp_path, monkeypatch):
    environment = tmp_path / ".venv-legacy"
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(environment)
    requirements = tmp_path / "requirements-legacy.txt"
    shutil.copy(legacy.ROOT / "requirements-legacy.txt", requirements)
    site = environment / ("Lib/site-packages" if os.name == "nt" else "lib/python3.12/site-packages")
    versions = {"discord.py": "2.6.0", "Pillow": "13.0.0", "spotipy": "2.26.0",
                "yt-dlp": "2026.10.21", "ConfigUpdater": "3.2"}
    for name, version in versions.items():
        metadata = site / f"{name.replace('-', '_')}-{version}.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8")
    stamp = environment / ".yeboy-legacy-requirements.sha256"
    stamp.write_text(hashlib.sha256(requirements.read_bytes()).hexdigest(), encoding="ascii")
    checked = legacy._checked
    imports = []

    def guarded_check(root, command, **kwargs):
        if legacy.IMPORTS in command:
            imports.append(command)
            return ""
        if "pip" in command:
            pytest.fail("A matching stamp must detect drift before package manager use")
        return checked(root, command, **kwargs)

    monkeypatch.setattr(legacy, "_checked", guarded_check)
    with pytest.raises(legacy.LegacyError, match="Версії"):
        legacy.prepare(tmp_path)
    # Distribution metadata is checked before trusting/importing a changed package.
    assert imports == []


def test_missing_venv_is_refused_without_installing(tmp_path):
    before = list(tmp_path.iterdir())
    with pytest.raises(legacy.LegacyError):
        legacy.validate_environment(tmp_path)
    assert list(tmp_path.iterdir()) == before


def test_legacy_directory_cannot_alias_core_environment(tmp_path):
    core = tmp_path / ".venv-v2"
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(core)
    try:
        (tmp_path / ".venv-legacy").symlink_to(core, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation unavailable for this Windows account")
    with pytest.raises(legacy.LegacyError):
        legacy.validate_environment(tmp_path)
    assert (core / "pyvenv.cfg").exists()


@pytest.mark.parametrize("token", ["", "bot_token"])
def test_placeholder_configuration_is_refused_without_modification(tmp_path, token, capsys):
    path = write_config(tmp_path, token)
    before = path.read_bytes()
    with pytest.raises(legacy.LegacyError):
        legacy.preflight(tmp_path)
    assert path.read_bytes() == before
    assert "Token =" not in capsys.readouterr().out


def test_no_legacy_configuration_is_generated_from_core_secrets(tmp_path):
    core_config = tmp_path / ".env.v2"
    core_config.write_text("DISCORD_TOKEN=core-secret-do-not-copy\n", encoding="utf-8")
    with pytest.raises(legacy.LegacyError) as error:
        legacy.preflight(tmp_path)
    assert "core-secret-do-not-copy" not in str(error.value)
    assert not (tmp_path / "config/options.ini").exists()
    assert core_config.read_text() == "DISCORD_TOKEN=core-secret-do-not-copy\n"


def test_valid_legacy_configuration_preserves_percent_and_unknown_values(tmp_path, capsys):
    path = write_config(tmp_path, "unit-test-percent%token", "CustomCredential = preserve%me")
    before = path.read_bytes()
    legacy.preflight(tmp_path, music=False)
    assert path.read_bytes() == before
    assert "unit-test-percent%token" not in capsys.readouterr().out


def test_configuration_parser_errors_do_not_expose_credentials(tmp_path, capsys):
    path = write_config(tmp_path, extra="Token = hidden-duplicate-test-credential")
    before = path.read_bytes()
    with pytest.raises(legacy.LegacyError) as error:
        legacy.preflight(tmp_path)
    assert "hidden-duplicate-test-credential" not in str(error.value)
    assert "hidden-duplicate-test-credential" not in capsys.readouterr().out
    assert path.read_bytes() == before


def test_music_remains_explicit_and_requires_its_prerequisites(tmp_path):
    write_config(tmp_path)
    legacy.preflight(tmp_path, music=False)
    with pytest.raises(legacy.LegacyError):
        legacy.preflight(tmp_path, music=True)


def test_failed_probe_discards_raw_subprocess_credentials(tmp_path, monkeypatch, capsys):
    environment = tmp_path / ".venv-legacy"
    environment.mkdir()
    interpreter = legacy.legacy_python(tmp_path)
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    interpreter.touch()
    (environment / "pyvenv.cfg").write_text("include-system-site-packages = false\n", encoding="utf-8")
    monkeypatch.setattr(legacy.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        [], 1, "https://user:hidden-credential-test@invalid/", "hidden-credential-test"))
    with pytest.raises(legacy.LegacyError) as error:
        legacy.validate_environment(tmp_path)
    captured = capsys.readouterr()
    assert "hidden-credential-test" not in str(error.value)
    assert "hidden-credential-test" not in captured.out + captured.err


def test_probe_timeout_is_bounded_and_does_not_print_subprocess_payload(isolated_root, monkeypatch, capsys):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] <= 120
        raise subprocess.TimeoutExpired("hidden-timeout-credential", kwargs["timeout"])

    monkeypatch.setattr(legacy.subprocess, "run", timeout)
    with pytest.raises(legacy.LegacyError) as error:
        legacy.validate_environment(isolated_root)
    assert "hidden-timeout-credential" not in str(error.value)
    assert "hidden-timeout-credential" not in capsys.readouterr().out


@pytest.fixture
def install_root(tmp_path, monkeypatch):
    """Exercise install orchestration without any package manager/network changes."""
    root = tmp_path / "Legacy з пробілами"
    root.mkdir()
    (root / "requirements-legacy.txt").write_text("example==1\n", encoding="utf-8")
    (root / ".venv-v2").mkdir()
    (root / ".venv-v2/core-packages.txt").write_text("core-is-unchanged", encoding="utf-8")
    commands, builders = [], []

    def create_environment(target):
        interpreter = legacy.legacy_python(root)
        interpreter.parent.mkdir(parents=True, exist_ok=True)
        interpreter.touch()
        (target / "pyvenv.cfg").write_text("include-system-site-packages = false\n", encoding="utf-8")

    def builder(**options):
        builders.append(options)
        return SimpleNamespace(create=create_environment)

    monkeypatch.setattr(legacy.venv, "EnvBuilder", builder)
    monkeypatch.setattr(legacy, "validate_environment", lambda *a, **k: None)
    monkeypatch.setattr(legacy, "_checked", lambda root, args, **kwargs: commands.append(args) or "")
    return root, commands, builders


def installation_commands(commands):
    return [command for command in commands if "install" in command]


def test_first_and_repeat_setup_preserve_existing_core_and_configuration(install_root):
    root, commands, builders = install_root
    config = write_config(root, "private-unit-test-token")
    original = config.read_bytes()
    legacy.prepare(root)
    legacy.prepare(root)
    installs = installation_commands(commands)
    assert len(installs) == 1
    assert builders == [{"with_pip": True, "system_site_packages": False, "clear": False}]
    assert config.read_bytes() == original
    assert (root / ".venv-v2/core-packages.txt").read_text() == "core-is-unchanged"
    command = installs[0]
    assert command[:4] == [str(legacy.legacy_python(root)), "-I", "-m", "pip"]
    assert "--isolated" in command and "--require-virtualenv" in command
    assert "--no-cache-dir" in command and "--only-binary=:all:" in command
    assert command[command.index("-r") + 1] == str(root / "requirements-legacy.txt")
    assert not set(command) & {"uninstall", "purge", "--break-system-packages", "--user"}


def test_changed_requirements_and_explicit_repair_are_local(install_root):
    root, commands, _ = install_root
    legacy.prepare(root)
    (root / "requirements-legacy.txt").write_text("example==2\n", encoding="utf-8")
    legacy.prepare(root)
    legacy.prepare(root, repair=True)
    assert len(installation_commands(commands)) == 3
    assert {command[0] for command in commands} == {str(legacy.legacy_python(root))}
    assert (root / ".venv-v2/core-packages.txt").read_text() == "core-is-unchanged"


def test_failed_pip_install_leaves_retryable_environment_without_success_stamp(install_root, monkeypatch):
    root, commands, _ = install_root
    successful_runner = legacy._checked

    def fail_install(root, command, **kwargs):
        if "install" in command:
            raise legacy.LegacyError("Test package source unavailable")
        return successful_runner(root, command, **kwargs)

    monkeypatch.setattr(legacy, "_checked", fail_install)
    with pytest.raises(legacy.LegacyError):
        legacy.prepare(root)
    stamp = root / ".venv-legacy/.yeboy-legacy-requirements.sha256"
    assert not stamp.exists()
    monkeypatch.setattr(legacy, "_checked", successful_runner)
    legacy.prepare(root)
    assert stamp.is_file()
    assert len(installation_commands(commands)) == 1


@pytest.mark.parametrize("name", ["setup", "bot"])
def test_existing_operation_or_live_bot_blocks_package_changes(install_root, name):
    root, commands, builders = install_root
    with InstanceLock(root / "data/legacy" / (name + ".lock")):
        with pytest.raises(legacy.LegacyError):
            legacy.prepare(root)
    assert commands == []
    assert builders == []
    assert not (root / ".venv-legacy").exists()


def test_unknown_incomplete_environment_is_preserved(install_root):
    root, commands, builders = install_root
    environment = root / ".venv-legacy"
    environment.mkdir()
    sentinel = environment / "keep-this-user-file"
    sentinel.write_text("preserve-me", encoding="utf-8")
    with pytest.raises(legacy.LegacyError):
        legacy.prepare(root)
    assert sentinel.read_text() == "preserve-me"
    assert commands == []
    assert builders == []


def test_parallel_launch_is_refused_without_starting_or_installing(install_root, monkeypatch):
    root, commands, _ = install_root
    write_config(root)
    child_starts = []
    monkeypatch.setattr(legacy.subprocess, "call", lambda *a, **k: child_starts.append(a) or 0)
    with InstanceLock(root / "data/legacy/launcher.lock"):
        with pytest.raises(legacy.LegacyError):
            legacy.start(root)
    assert commands == []
    assert child_starts == []


def test_start_uses_guarded_foreground_child_and_preserves_its_exit_code(install_root, monkeypatch):
    root, _, _ = install_root
    write_config(root)
    calls = []
    monkeypatch.setenv("PYTHONPATH", "/foreign/packages")
    monkeypatch.setenv("PIP_TARGET", "/foreign/packages")

    def child(command, **kwargs):
        calls.append((command, kwargs))
        return 7

    monkeypatch.setattr(legacy.subprocess, "call", child)
    assert legacy.start(root) == 7
    command, options = calls[0]
    assert command == [str(legacy.legacy_python(root)), "-I", str(root / "run.py")]
    assert options["cwd"] == root
    assert "PYTHONPATH" not in options["env"] and "PIP_TARGET" not in options["env"]
    assert not options.get("shell", False)


def test_wrong_interpreter_is_refused_before_importing_legacy_bot(tmp_path, monkeypatch):
    write_config(tmp_path)
    imported_bot = []
    original_import = builtins.__import__

    def checked_import(name, *args, **kwargs):
        if name == "yeboybot.bot":
            imported_bot.append(name)
            raise AssertionError("Untrusted interpreter reached legacy import")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", checked_import)
    with pytest.raises(legacy.LegacyError):
        legacy.run_bot(tmp_path)
    assert imported_bot == []
    assert not (tmp_path / "logs").exists()


def test_direct_entry_from_other_cwd_refuses_system_python_without_importing_bot(tmp_path):
    root = tmp_path / "Бот з пробілами"
    (root / "tools").mkdir(parents=True)
    (root / "core_v2").mkdir()
    (root / "yeboybot").mkdir()
    for name in ("run.py", "tools/legacy_launcher.py", "tools/__init__.py", "core_v2/runtime.py", "core_v2/__init__.py"):
        shutil.copy(legacy.ROOT / name, root / name)
    marker = root / "untrusted-import.txt"
    (root / "yeboybot/__init__.py").write_text(
        "from pathlib import Path\nPath(__file__).resolve().parents[1].joinpath('untrusted-import.txt').touch()\n",
        encoding="utf-8")
    result = subprocess.run([sys.executable, "-I", str(root / "run.py")], cwd=tmp_path,
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode != 0
    assert not marker.exists()
    assert not (root / ".venv-legacy").exists()
    assert not (root / "logs").exists()
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("mode,expected,exit_code", [
    ("check", "Українська помилка", 1),
    ("menu", "застарілий режим", 0),
    ("bot", "Українська помилка", 1),
])
def test_ukrainian_output_survives_ascii_redirected_streams(tmp_path, mode, expected, exit_code):
    # -I ignores PYTHONUTF8, as on Windows with a legacy redirected code page.
    # Force ASCII on every platform so this regression is reproducible on Linux too.
    code = (
        "import builtins, io, sys\n"
        f"sys.path.insert(0, {str(legacy.ROOT)!r})\n"
        "from tools import legacy_launcher as launcher\n"
        "sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='ascii', errors='strict')\n"
        "sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='ascii', errors='strict')\n"
        "assert sys.stdout.encoding == 'ascii'\n"
        "def fail(*args, **kwargs):\n"
        "    raise launcher.LegacyError('Українська помилка')\n"
        "mode = sys.argv[1]\n"
        "if mode == 'bot':\n"
        "    launcher.run_bot = fail\n"
        "    result = launcher.bot_entry()\n"
        "elif mode == 'menu':\n"
        "    builtins.input = lambda prompt: '0'\n"
        "    result = launcher.main([])\n"
        "else:\n"
        "    launcher.validate_environment = fail\n"
        "    result = launcher.main(['--check'])\n"
        "raise SystemExit(result)\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code, mode], cwd=tmp_path,
                            capture_output=True, timeout=30, env=legacy.clean_environment())
    assert result.returncode == exit_code, result.stderr.decode("utf-8", errors="replace")
    assert expected in result.stdout.decode("utf-8")
    assert b"Traceback" not in result.stderr
    assert b"UnicodeEncodeError" not in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Native Windows CMD required")
def test_windows_missing_python_returns_failure_without_installing(tmp_path):
    script = tmp_path / "run.bat"
    shutil.copy(legacy.ROOT / "run.bat", script)
    environment = os.environ.copy()
    environment["PATH"] = str(tmp_path / "empty-path")
    # Execute only the replacement batch; no registry/global package operations exist there.
    result = subprocess.run([os.environ["COMSPEC"], "/d", "/c", "run.bat"], cwd=tmp_path,
                            env=environment, input="\n", capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
    assert result.returncode != 0
    assert "https://www.python.org/downloads/" in result.stdout
    assert not (tmp_path / ".venv-legacy").exists()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows CMD required")
def test_windows_replacement_batch_uses_checkout_path_and_preserves_exit_code(tmp_path):
    root = tmp_path / "Бот з пробілами"
    (root / "tools").mkdir(parents=True)
    script = root / "run.bat"
    shutil.copy(legacy.ROOT / "run.bat", script)
    # A harmless stand-in isolates CMD quoting/cwd/exit behavior from package installs.
    (root / "tools/legacy_launcher.py").write_text(
        "from pathlib import Path\n"
        "Path('launcher-cwd.txt').write_text(str(Path.cwd()), encoding='utf-8')\n"
        "raise SystemExit(7)\n", encoding="utf-8")
    result = subprocess.run([os.environ["COMSPEC"], "/d", "/c", "call", str(script)],
                            cwd=tmp_path, input="\n", capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
    assert result.returncode == 7, result.stdout + result.stderr
    assert (root / "launcher-cwd.txt").read_text(encoding="utf-8") == str(root)
    assert not (tmp_path / "launcher-cwd.txt").exists()
