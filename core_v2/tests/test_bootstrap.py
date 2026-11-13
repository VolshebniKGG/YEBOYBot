"""Offline installer tests: no pip downloads or Discord login."""
from pathlib import Path
import json
import os
import sys
import venv
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
        self.pip_version = b.PIP_VERSION
        self.version = patch.object(b.sys, "version_info", (3, 12, 0)).start()
        self.addCleanup(patch.stopall)
        patch.object(b, "run", side_effect=self.fake_run).start()
        patch.object(b.venv.EnvBuilder, "create", side_effect=self.create_venv).start()

    def environment_info(self):
        target = self.root / ".venv-v2"
        return {"prefix": str(target), "base": sys.base_prefix, "version": [3, 12],
                "isolated": True, "user_site": False,
                "paths": [str(target / "Lib/site-packages")], "stdlib": [sys.base_prefix]}

    def fake_run(self, args, root):
        self.commands.append(args)
        if b.ENVIRONMENT_PROBE in args:
            return json.dumps(self.environment_info())
        if b.PIP_VERSION_CHECK in args:
            return self.pip_version
        if "pip==" + b.PIP_VERSION in args:
            self.pip_version = b.PIP_VERSION
        return ""

    def create_venv(self, path):
        interpreter = b.python_path(self.root)
        interpreter.parent.mkdir(parents=True, exist_ok=True)
        interpreter.touch()
        (path / "pyvenv.cfg").write_text("include-system-site-packages = false\n", encoding="utf-8")

    def installs(self):
        return [args for args in self.commands if "install" in args]


    def test_owned_broken_interpreter_is_preserved_and_rebuilt_once(self):
        b.prepare(self.root)
        (self.root / '.venv-v2/keep.txt').write_text('preserve')
        original = b.validate_environment
        calls = []
        def validate(root):
            calls.append(root)
            if len(calls) == 1:
                raise b.BootstrapError('broken', stage='environment-probe', category='broken-interpreter')
            return original(root)
        with patch.object(b, 'validate_environment', side_effect=validate):
            b.prepare(self.root)
        saved = list(self.root.glob('.venv-v2.failed-*'))
        self.assertEqual(len(saved), 1)
        self.assertEqual((saved[0] / 'keep.txt').read_text(), 'preserve')
        self.assertEqual(len(calls), 2)
        self.assertTrue(b.python_path(self.root).is_file())

    def test_unowned_broken_interpreter_is_not_moved(self):
        b.prepare(self.root)
        (self.root / '.venv-v2/.yeboy-core-owner').unlink()
        with patch.object(b, 'validate_environment', side_effect=b.BootstrapError('broken', stage='environment-probe')):
            with self.assertRaises(b.BootstrapError):
                b.prepare(self.root, repair=True)
        self.assertEqual(list(self.root.glob('.venv-v2.failed-*')), [])

    def test_missing_dependency_repairs_once_in_same_attempt(self):
        b.prepare(self.root)
        failures = []
        def missing_once(args, root):
            if b.IMPORT_CHECK in args and not failures:
                failures.append(True)
                raise b.BootstrapError("missing", stage="library-import", category="missing-module")
            return self.fake_run(args, root)
        with patch.object(b, "run", side_effect=missing_once):
            b.prepare(self.root)
        self.assertEqual(len(self.installs()), 2)
        self.assertTrue((self.root / ".venv-v2/yeboy-requirements.sha256").is_file())

    def test_first_install_and_repeat_preserves_config(self):
        b.prepare(self.root)
        (self.root / ".env.v2").write_text("DISCORD_TOKEN=private-test-value\n")
        b.prepare(self.root)
        self.assertEqual(len(self.installs()), 1)
        self.assertEqual((self.root / ".env.v2").read_text(), "DISCORD_TOKEN=private-test-value\n")
        self.assertTrue((self.root / "backups").is_dir())
        self.assertIn(str(self.root.resolve() / "core_v2/requirements.txt"), self.installs()[0])

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
        def fail_install(args, root):
            if "-r" in args:
                raise b.BootstrapError("offline")
            return self.fake_run(args, root)
        with patch.object(b, "run", side_effect=fail_install):
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
            return self.fake_run(args, root)
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


    def test_old_pip_is_upgraded_before_first_application_install(self):
        self.pip_version = "25.0.1"
        b.prepare(self.root)
        installs = self.installs()
        self.assertEqual(len(installs), 2)
        self.assertIn("pip==" + b.PIP_VERSION, installs[0])
        self.assertIn("--only-binary=:all:", installs[0])
        self.assertIn("-r", installs[1])
        for command in installs:
            # Windows temp paths can use RUNNER~1 while prepare resolves their
            # long spelling. Compare the same target, retaining the venv guard.
            self.assertEqual(Path(command[0]).resolve(), b.python_path(self.root).resolve())
            self.assertEqual(command[1:4], ["-I", "-m", "pip"])
            self.assertTrue({"--isolated", "--require-virtualenv", "--no-cache-dir"} <= set(command))

    def test_valid_app_stamp_does_not_hide_installer_version_drift(self):
        b.prepare(self.root)
        self.commands.clear()
        self.pip_version = "25.0.1"
        b.prepare(self.root)
        self.assertEqual(len(self.installs()), 1)
        self.assertIn("pip==" + b.PIP_VERSION, self.installs()[0])
        self.assertFalse(any("-r" in args for args in self.commands))
        self.commands.clear()
        b.prepare(self.root)
        self.assertEqual(self.installs(), [])

    def test_failed_pip_upgrade_invalidates_stamp_and_blocks_app_installs(self):
        b.prepare(self.root)
        self.commands.clear()
        self.pip_version = "25.0.1"
        def fail_upgrade(args, root):
            self.commands.append(args)
            if b.ENVIRONMENT_PROBE in args:
                return json.dumps(self.environment_info())
            if b.PIP_VERSION_CHECK in args:
                return self.pip_version
            if "pip==" + b.PIP_VERSION in args:
                raise b.BootstrapError("package source unavailable")
            return ""
        with patch.object(b, "run", side_effect=fail_upgrade):
            with self.assertRaises(b.BootstrapError):
                b.prepare(self.root, repair=True)
        self.assertFalse(any("-r" in args for args in self.commands))
        self.assertFalse((self.root / ".venv-v2/yeboy-requirements.sha256").exists())

    def test_unconfirmed_pip_upgrade_never_installs_app_or_marks_success(self):
        self.pip_version = "25.0.1"
        def unchanged_version(args, root):
            if "pip==" + b.PIP_VERSION in args:
                self.commands.append(args)
                return ""
            return self.fake_run(args, root)
        with patch.object(b, "run", side_effect=unchanged_version):
            with self.assertRaises(b.BootstrapError):
                b.prepare(self.root)
        self.assertFalse(any("-r" in args for args in self.commands))
        self.assertFalse((self.root / ".venv-v2/yeboy-requirements.sha256").exists())

    def test_wrong_global_prefix_is_rejected_before_package_changes(self):
        self.pip_version = "25.0.1"
        info = self.environment_info()
        info["prefix"] = info["base"]
        with patch.object(self, "environment_info", return_value=info):
            with self.assertRaises(b.BootstrapError):
                b.prepare(self.root)
        self.assertEqual(self.installs(), [])
        self.assertFalse((self.root / ".env.v2").exists())

    def test_system_site_packages_are_rejected_without_running_interpreter(self):
        b.prepare(self.root)
        self.commands.clear()
        cfg = self.root / ".venv-v2/pyvenv.cfg"
        cfg.write_text("include-system-site-packages = true\n", encoding="utf-8")
        with self.assertRaises(b.BootstrapError):
            b.prepare(self.root, repair=True)
        self.assertEqual(self.commands, [])

    def test_unknown_incomplete_environment_is_preserved(self):
        target = self.root / ".venv-v2"
        target.mkdir()
        marker = target / "keep-this-file"
        marker.write_text("preserve user data", encoding="utf-8")
        with self.assertRaises(b.BootstrapError):
            b.prepare(self.root)
        self.assertEqual(marker.read_text(), "preserve user data")
        self.assertEqual(self.commands, [])

    def test_environment_directory_symlink_is_refused(self):
        foreign = self.root / "another-venv"
        foreign.mkdir()
        try:
            (self.root / ".venv-v2").symlink_to(foreign, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation not allowed for this Windows account")
        with self.assertRaises(b.BootstrapError):
            b.prepare(self.root)
        self.assertEqual(list(foreign.iterdir()), [])
        self.assertEqual(self.commands, [])


class CommandTests(unittest.TestCase):
    def test_failure_exit_code(self):
        with patch.object(b.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)):
            with self.assertRaises(b.BootstrapError):
                b.run(["python", "-m", "pip"], Path.cwd())

    def test_timeout(self):
        with patch.object(b.subprocess, "run", side_effect=subprocess.TimeoutExpired([], 600)):
            with self.assertRaises(b.BootstrapError):
                b.run(["python"], Path.cwd())


    def test_child_run_removes_global_package_redirection_and_keeps_verified_ca(self):
        environment = {"PIP_TARGET": "/global/packages", "PIP_PREFIX": "/global",
                       "PIP_CONFIG_FILE": "/untrusted/pip.ini", "PYTHONPATH": "/foreign",
                       "PYTHONHOME": "/foreign", "VIRTUAL_ENV": "/foreign/env",
                       "HTTPS_PROXY": "http://proxy.invalid:8080", "REQUESTS_CA_BUNDLE": "/trusted/ca.pem"}
        with patch.dict(os.environ, environment), patch.object(b.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "ok")) as runner:
            self.assertEqual(b.run(["python", "-I", "-c", "pass"], Path.cwd()), "ok")
        child = runner.call_args.kwargs["env"]
        self.assertNotIn("PIP_TARGET", child)
        self.assertNotIn("PIP_PREFIX", child)
        self.assertNotIn("PYTHONPATH", child)
        self.assertNotIn("PYTHONHOME", child)
        self.assertNotIn("VIRTUAL_ENV", child)
        self.assertEqual(child["PIP_CONFIG_FILE"], os.devnull)
        self.assertEqual(child["HTTPS_PROXY"], environment["HTTPS_PROXY"])
        self.assertEqual(child["REQUESTS_CA_BUNDLE"], environment["REQUESTS_CA_BUNDLE"])
        command = b.pip_command("target-python", "install", "pip==" + b.PIP_VERSION,
                                source={"PIP_CERT": "/trusted/pip-ca.pem", "PIP_TARGET": "/global"})
        self.assertIn("/trusted/pip-ca.pem", command)
        self.assertNotIn("/global", command)

    def test_network_cause_takes_precedence_over_pip_no_matching_distribution(self):
        output = ("WARNING: Retrying after NewConnectionError: [WinError 11001] getaddrinfo failed "
                  "https://account:secret@private.invalid/simple/pip/\\n"
                  "ERROR: Could not find a version that satisfies the requirement pip==26.2 "
                  "(from versions: none)\\n"
                  "ERROR: No matching distribution found for pip==26.2")
        result = subprocess.CompletedProcess([], 1, "", output)
        with patch.object(b.subprocess, "run", return_value=result):
            with self.assertRaises(b.BootstrapError) as failure:
                b.run(["python", "-I", "-m", "pip", "install", "pip==" + b.PIP_VERSION], Path.cwd())
        self.assertEqual(failure.exception.stage, "pip-upgrade")
        self.assertEqual(failure.exception.category, "network")
        self.assertIn("DNS", str(failure.exception))
        self.assertNotIn("secret", str(failure.exception))
        self.assertNotIn("private.invalid", str(failure.exception))

    def test_certificate_cause_takes_precedence_over_pip_no_matching_distribution(self):
        output = ("SSLCertVerificationError: CERTIFICATE_VERIFY_FAILED "
                  "https://token:password@private.invalid/\\n"
                  "ERROR: No matching distribution found for pip==26.2")
        with patch.object(b.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", output)):
            with self.assertRaises(b.BootstrapError) as failure:
                b.run(["python", "-m", "pip", "install", "pip==" + b.PIP_VERSION], Path.cwd())
        self.assertEqual(failure.exception.category, "certificate")
        self.assertNotIn("password", str(failure.exception))
        self.assertIn("TLS", str(failure.exception))

    def test_proxy_cause_overrides_no_matching_and_never_discloses_credentials(self):
        output = ("ProxyError: 407 Proxy Authentication Required; user=secret\\n"
                  "ERROR: No matching distribution found for pip==26.2")
        with patch.object(b.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", output)):
            with self.assertRaises(b.BootstrapError) as failure:
                b.run(["python", "-m", "pip", "install", "pip==" + b.PIP_VERSION], Path.cwd())
        self.assertEqual(failure.exception.category, "proxy")
        self.assertNotIn("secret", str(failure.exception))

    def test_known_pip_release_missing_from_index_is_not_called_python_incompatible(self):
        output = "ERROR: No matching distribution found for pip==" + b.PIP_VERSION
        with patch.object(b.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", output)):
            with self.assertRaises(b.BootstrapError) as failure:
                b.run(["python", "-m", "pip", "install", "pip==" + b.PIP_VERSION], Path.cwd())
        self.assertEqual(failure.exception.category, "package-unavailable")
        self.assertIn("існує на PyPI", str(failure.exception))
        self.assertNotIn("сумісного пакета", str(failure.exception))

    def test_network_failure_report_has_no_raw_proxy_or_pip_content(self):
        with tempfile.TemporaryDirectory() as directory:
            error = b.BootstrapError("private token and URL", stage="pip-upgrade",
                                     category="network", exit_code=1)
            b.write_failure_report(directory, error)
            content = (Path(directory) / "data/v2/install-report.json").read_text()
            report = json.loads(content)
        self.assertEqual(report["stage"], "pip-upgrade")
        self.assertEqual(report["category"], "network")
        self.assertNotIn("private", content)
        self.assertNotIn("URL", content)

    def test_raw_package_manager_credentials_are_not_exposed(self):
        result = subprocess.CompletedProcess([], 1, "https://secret:private@invalid/", "private")
        with patch.object(b.subprocess, "run", return_value=result):
            with self.assertRaises(b.BootstrapError) as error:
                b.run(["python", "-m", "pip"], Path.cwd())
        self.assertNotIn("private", str(error.exception))
        self.assertNotIn("secret", str(error.exception))


class RealEnvironmentTests(unittest.TestCase):
    def test_real_venv_probe_ignores_injected_global_python_and_pip_paths(self):
        with tempfile.TemporaryDirectory(prefix="Core Бот з пробілами ") as directory:
            root = Path(directory)
            venv.EnvBuilder(with_pip=False, system_site_packages=False).create(root / ".venv-v2")
            with patch.dict(os.environ, {"PYTHONPATH": "/foreign/packages", "PYTHONHOME": "/foreign/python",
                                         "PIP_TARGET": "/global/packages"}):
                b.validate_environment(root)

    def test_real_venv_with_external_pth_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="Core в іншій папці ") as directory:
            root = Path(directory)
            target = root / ".venv-v2"
            venv.EnvBuilder(with_pip=False, system_site_packages=False).create(target)
            external = root / "foreign-modules"
            external.mkdir()
            site = target / ("Lib/site-packages" if os.name == "nt" else "lib/python3.12/site-packages")
            (site / "foreign.pth").write_text(str(external) + "\n", encoding="utf-8")
            with self.assertRaises(b.BootstrapError):
                b.validate_environment(root)


@unittest.skipUnless(__import__("os").name == "nt", "Windows CMD only")
class WindowsBootstrapTests(unittest.TestCase):
    def test_incomplete_download_returns_actionable_failure(self):
        import os
        import shutil
        with tempfile.TemporaryDirectory(prefix="Бот з пробілами ") as directory:
            script = Path(directory) / "install_v2.bat"
            shutil.copy(b.ROOT / "install_v2.bat", script)
            env = os.environ.copy()
            env["PATH"] = str(Path(directory) / "empty-path")
            result = subprocess.run(
                [os.environ["COMSPEC"], "/d", "/c", "install_v2.bat"],
                cwd=directory, env=env, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=30)
            self.assertEqual(result.returncode, 1)
            self.assertIn("PowerShell", result.stdout, result.stderr)
            self.assertIn("https://www.python.org/downloads/", result.stdout, result.stderr)
            self.assertFalse((Path(directory) / ".venv-v2").exists())


class DiagnosticsTests(unittest.TestCase):
    def test_safe_failure_report(self):
        with tempfile.TemporaryDirectory() as directory:
            error = b.BootstrapError('secret-token https://private', stage='dependency-install',
                                     category='certificate', exit_code=7)
            b.write_failure_report(directory, error)
            content = (Path(directory) / 'data/v2/install-report.json').read_text()
            report = json.loads(content)
            self.assertEqual(report['stage'], 'dependency-install')
            self.assertEqual(report['exit_code'], 7)
            self.assertNotIn('secret-token', content)
            self.assertNotIn('https://private', content)

    def test_failed_process_exposes_stage_not_output(self):
        result = subprocess.CompletedProcess([], 7, '', 'CERTIFICATE_VERIFY_FAILED secret-token')
        with patch.object(b.subprocess, 'run', return_value=result):
            with self.assertRaises(b.BootstrapError) as caught:
                b.run(['python', '-m', 'pip', 'install'], Path('.'))
        self.assertEqual(caught.exception.category, 'certificate')
        self.assertEqual(caught.exception.stage, 'dependency-install')
        self.assertEqual(caught.exception.exit_code, 7)
        self.assertNotIn('secret-token', str(caught.exception))

    def test_venv_creation_failure_is_safe(self):
        failure = subprocess.CalledProcessError(1, ['private'], output='secret-token')
        with patch.object(b.venv.EnvBuilder, 'create', side_effect=failure):
            with self.assertRaises(b.BootstrapError) as caught:
                b.create_environment(Path('.venv-v2'))
        self.assertEqual(caught.exception.stage, 'venv-create')
        self.assertNotIn('secret-token', str(caught.exception))
