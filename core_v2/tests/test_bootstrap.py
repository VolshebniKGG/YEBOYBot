"""Offline installer tests: no pip downloads or Discord login."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from core_v2.runtime import InstanceLock
from tools.launcher import bootstrap as b


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Бот з пробілами ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "core_v2").mkdir()
        (self.root / "core_v2/requirements.txt").write_text("example==1\n")
        (self.root / ".env.v2.example").write_text("DISCORD_TOKEN=\n")
        self.commands = []
        self.version = patch.object(b.sys, "version_info", (3, 12, 0)).start()
        self.addCleanup(patch.stopall)
        patch.object(b, "run", side_effect=lambda args, root: self.commands.append(args)).start()
        patch.object(b.venv.EnvBuilder, "create", side_effect=self.create_venv).start()

    def create_venv(self, path):
        interpreter = b.python_path(self.root)
        interpreter.parent.mkdir(parents=True, exist_ok=True)
        interpreter.touch()

    def installs(self):
        return [args for args in self.commands if "install" in args]

    def test_first_install_and_repeat_preserves_config(self):
        b.prepare(self.root)
        (self.root / ".env.v2").write_text("DISCORD_TOKEN=private-test-value\n")
        b.prepare(self.root)
        self.assertEqual(len(self.installs()), 1)
        self.assertEqual((self.root / ".env.v2").read_text(), "DISCORD_TOKEN=private-test-value\n")
        self.assertTrue((self.root / "backups").is_dir())
        self.assertIn(str(self.root / "core_v2/requirements.txt"), self.installs()[0])

    def test_requirements_change_reinstalls(self):
        b.prepare(self.root)
        (self.root / "core_v2/requirements.txt").write_text("example==2\n")
        b.prepare(self.root)
        self.assertEqual(len(self.installs()), 2)

    def test_explicit_repair(self):
        b.prepare(self.root)
        b.prepare(self.root, repair=True)
        self.assertEqual(len(self.installs()), 2)

    def test_pip_failure_leaves_no_success_marker(self):
        with patch.object(b, "run", side_effect=b.BootstrapError("offline")):
            with self.assertRaises(b.BootstrapError):
                b.prepare(self.root)
        self.assertFalse((self.root / ".venv-v2/yeboy-requirements.sha256").exists())
        b.prepare(self.root)
        self.assertEqual(len(self.installs()), 1)

    def test_import_failure_invalidates_existing_marker(self):
        b.prepare(self.root)
        def fail_import(args, root):
            if b.IMPORT_CHECK in args:
                raise b.BootstrapError("import failed")
        with patch.object(b, "run", side_effect=fail_import):
            with self.assertRaises(b.BootstrapError):
                b.prepare(self.root)
        self.assertFalse((self.root / ".venv-v2/yeboy-requirements.sha256").exists())

    def test_running_bot_blocks_installation(self):
        with InstanceLock(self.root / "data/v2/bot.lock"):
            with self.assertRaises(RuntimeError):
                b.prepare(self.root)
        self.assertEqual(self.commands, [])

    def test_parallel_installation_blocked(self):
        with InstanceLock(self.root / "data/v2/install.lock"):
            with self.assertRaises(RuntimeError):
                b.prepare(self.root)
        self.assertEqual(self.commands, [])

    def test_unsupported_python_does_not_create_files(self):
        with patch.object(b.sys, "version_info", (3, 13, 0)):
            with self.assertRaises(b.BootstrapError):
                b.prepare(self.root)
        self.assertFalse((self.root / ".venv-v2").exists())


class CommandTests(unittest.TestCase):
    def test_failure_exit_code(self):
        with patch.object(b.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)):
            with self.assertRaises(b.BootstrapError):
                b.run(["python", "-m", "pip"], Path.cwd())

    def test_timeout(self):
        with patch.object(b.subprocess, "run", side_effect=subprocess.TimeoutExpired([], 600)):
            with self.assertRaises(b.BootstrapError):
                b.run(["python"], Path.cwd())


@unittest.skipUnless(__import__("os").name == "nt", "Windows CMD only")
class WindowsBootstrapTests(unittest.TestCase):
    def test_missing_python_returns_actionable_failure(self):
        import os
        import shutil
        with tempfile.TemporaryDirectory(prefix="Бот з пробілами ") as directory:
            script = Path(directory) / "install_v2.bat"
            shutil.copy(b.ROOT / "install_v2.bat", script)
            env = os.environ.copy()
            env["PATH"] = str(Path(directory) / "empty-path")
            result = subprocess.run(
                [os.environ["COMSPEC"], "/d", "/c", f'call "{script}"'],
                cwd=Path(directory).parent, env=env, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(result.returncode, 1)
            self.assertIn("https://www.python.org/downloads/", result.stdout)
            self.assertFalse((Path(directory) / ".venv-v2").exists())
