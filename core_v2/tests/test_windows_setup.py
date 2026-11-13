"""Offline Windows PowerShell bootstrap tests; never install system software."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools/windows_setup.ps1"
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 only")


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _cmd_entry(script):
    # cmd /s /c removes its OUTER quote pair. list2cmdline uses CRT backslash
    # escaping, which cmd does not understand for this nested command string.
    # Windows filenames cannot contain double quotes; keep the inner path pair
    # so spaces, apostrophes and ampersands are literal, as with Explorer launch.
    return f'"{os.environ["COMSPEC"]}" /d /s /c ""{script}""'


def _run_ps(tmp_path, body, *, input_text="", source=SOURCE, timeout=45):
    """Exercise the real PS functions while replacing only OS side effects."""
    directory = tmp_path / "Бот з пробілами O'Connor"
    directory.mkdir(exist_ok=True)
    script = directory / "перевірка встановлення.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)\n"
        f". {_literal(source)} -LibraryOnly\n"
        "function Assert-True([bool] $Condition, [string] $Message) {\n"
        "    if (-not $Condition) { throw ('ASSERT: ' + $Message) }\n"
        "}\n"
        + body
        + "\nWrite-Output 'YEBOY_TEST_OK'\n",
        encoding="utf-8-sig",
    )
    interpreter = (Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell"
                   / "v1.0" / "powershell.exe")
    result = subprocess.run(
        [str(interpreter), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script)],
        cwd=tmp_path, input=input_text, text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=timeout,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "YEBOY_TEST_OK" in result.stdout, result.stdout + result.stderr
    return result


def test_setup_source_preserves_ukrainian_on_windows_powershell_51():
    # PS 5.1 otherwise interprets a UTF-8 script as the legacy ANSI code page.
    data = SOURCE.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    assert "YEBOYBot" in data.decode("utf-8-sig")


@WINDOWS
def test_powershell_parser_and_library_mode_do_not_start_installation(tmp_path):
    # This is the focused Windows job's first PowerShell process. It exceeded
    # 45s once on the hosted runner while the same test passed
    # in the full Windows job. Keep assertions and no retries; allow startup
    # headroom here without relaxing the other scripts' execution budgets.
    _run_ps(tmp_path, r"""
$tokens = $null
$parseErrors = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile(
    $SOURCE_FILE, [ref]$tokens, [ref]$parseErrors)
Assert-True ($parseErrors.Count -eq 0) 'PowerShell 5.1 syntax must parse'
foreach ($name in @('Find-YeboyPython', 'Find-YeboyWinget', 'Invoke-YeboyWinget',
                    'Invoke-YeboyBootstrap', 'Invoke-YeboyMedia', 'Test-YeboyEnvironment', 'Start-YeboyMenu',
                    'Install-YeboyCore', 'Show-YeboySetup', 'Invoke-YeboyEntry',
                    'Invoke-YeboyGitHubUpdate', 'Restore-YeboyInterruptedUpdate',
                    'Invoke-YeboyUpdateWorker')) {
    Assert-True ($null -ne (Get-Command $name -CommandType Function)) ('Missing ' + $name)
}
""".replace("$SOURCE_FILE", _literal(SOURCE)), timeout=90)


@WINDOWS
def test_main_cmd_without_python_displays_panel_and_can_exit_from_arbitrary_cwd(tmp_path):
    root = tmp_path / "Порожній ПК O'Connor & бот (!)"
    (root / "tools").mkdir(parents=True)
    shutil.copy2(ROOT / "YEBOYBot.cmd", root)
    shutil.copy2(SOURCE, root / "tools/windows_setup.ps1")
    environment = dict(os.environ)
    environment["PATH"] = str(tmp_path / "empty-path")
    environment["PSExecutionPolicyPreference"] = "RemoteSigned"
    result = subprocess.run(
        _cmd_entry(root / "YEBOYBot.cmd"),
        cwd=tmp_path, env=environment, input="0\n", text=True,
        encoding="utf-8", errors="replace", capture_output=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "YEBOYBot" in result.stdout
    assert "[1]" in result.stdout and "[0]" in result.stdout
    assert not (root / ".venv-v2").exists()
    assert not (root / ".env.v2").exists()


@WINDOWS
def test_main_cmd_enters_real_prepared_core_menu_from_arbitrary_cwd(tmp_path):
    # The full Windows CI job already performs first/repeat installation.
    # Focused jobs do not: never create a venv or download packages in this test.
    if not (ROOT / ".venv-v2/Scripts/python.exe").is_file():
        pytest.skip("CI Core installation not prepared")
    result = subprocess.run(
        _cmd_entry(ROOT / "YEBOYBot.cmd"),
        cwd=tmp_path, env={**os.environ, "PSExecutionPolicyPreference": "RemoteSigned"},
        input="0\n", text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Core V2" in result.stdout, result.stdout + result.stderr
    assert "[19]" in result.stdout, result.stdout + result.stderr


@WINDOWS
def test_no_python_without_install_consent_never_invokes_package_manager(tmp_path):
    _run_ps(tmp_path, r"""
function Find-YeboyPython { return $null }
function Find-YeboyWinget { throw 'Unexpected package manager discovery' }
function Invoke-YeboyWinget { throw 'Unexpected system install' }
function Invoke-YeboyBootstrap { throw 'Unexpected Python bootstrap' }
$code = Install-YeboyCore
Assert-True ($code -ne 0) 'Missing Python requires explicit consent'
""")


@WINDOWS
def test_missing_winget_is_actionable_without_unchecked_fallback(tmp_path):
    result = _run_ps(tmp_path, r"""
function Find-YeboyPython { return $null }
function Find-YeboyWinget { return $null }
function Invoke-YeboyWinget { throw 'Unexpected installer' }
function Invoke-YeboyBootstrap { throw 'Unexpected Python bootstrap' }
$code = Install-YeboyCore -AllowPythonInstall
Assert-True ($code -ne 0) 'No package manager cannot report completed install'
""")
    assert ("python.org" in result.stdout or "App Installer" in result.stdout
            or "winget" in result.stdout.lower())


@WINDOWS
@pytest.mark.parametrize("installer_code", [1, 1603, -1978335189],
                         ids=["failure", "installer-failure", "winget-failure"])
def test_python_install_failure_never_runs_bootstrap(tmp_path, installer_code):
    _run_ps(tmp_path, r"""
function Find-YeboyPython { return $null }
function Find-YeboyWinget { return 'fake-winget' }
function Confirm-YeboyPackageInstall { return $true }
function Invoke-YeboyWinget { return INSTALLER_CODE }
function Invoke-YeboyBootstrap { throw 'Bootstrap after failed Python install' }
$code = Install-YeboyCore -AllowPythonInstall
Assert-True ($code -ne 0) 'Installer failure cannot report success'
""".replace("INSTALLER_CODE", str(installer_code)))


@WINDOWS
def test_declining_python_package_never_runs_installer_or_bootstrap(tmp_path):
    _run_ps(tmp_path, r"""
function Find-YeboyPython { return $null }
function Find-YeboyWinget { return 'fake-winget' }
function Read-Host { return '0' }
function Invoke-YeboyWinget { throw 'Declined installer must never run' }
function Invoke-YeboyBootstrap { throw 'No verified Python' }
$code = Install-YeboyCore -AllowPythonInstall
Assert-True ($code -eq 0) 'Declining Python is a user cancellation'
""")


@WINDOWS
def test_installer_success_without_discoverable_python_never_runs_bootstrap(tmp_path):
    _run_ps(tmp_path, r"""
$script:FindCalls = 0
function Find-YeboyPython { $script:FindCalls++; return $null }
function Find-YeboyWinget { return 'fake-winget' }
function Confirm-YeboyPackageInstall { return $true }
function Invoke-YeboyWinget { return 0 }
function Invoke-YeboyBootstrap { throw 'No interpreter was verified' }
$code = Install-YeboyCore -AllowPythonInstall
Assert-True ($code -ne 0) 'A successful installer alone is not readiness'
Assert-True ($script:FindCalls -ge 2) 'Rediscover Python after installation'
""")


@WINDOWS
def test_successful_install_rediscovers_python_without_restarting_console(tmp_path):
    _run_ps(tmp_path, r"""
$script:FindCalls = 0
$script:InstallCalls = 0
$script:BootstrapCalls = 0
$script:ExpectedPython = "D:\Бот з пробілами O'Connor\Python312\python.exe"
function Find-YeboyPython {
    $script:FindCalls++
    if ($script:FindCalls -eq 1) { return $null }
    return $script:ExpectedPython
}
function Find-YeboyWinget { return 'fake-winget' }
function Confirm-YeboyPackageInstall { return $true }
function Invoke-YeboyWinget([string] $Executable) {
    Assert-True ($Executable -eq 'fake-winget') 'Use discovered package manager'
    $script:InstallCalls++
    return 0
}
function Invoke-YeboyBootstrap([string] $Python, [switch] $Repair) {
    Assert-True ($Python -ceq $script:ExpectedPython) 'Preserve Unicode, spaces and apostrophe'
    Assert-True (-not $Repair) 'First install is not an automatic repair'
    $script:BootstrapCalls++
    return 0
}
function Invoke-YeboyMedia { return 0 }
function Test-YeboyEnvironment { return $true }
$code = Install-YeboyCore -AllowPythonInstall
Assert-True ($code -eq 0) 'Complete first installation'
Assert-True ($script:InstallCalls -eq 1) 'Install Python once'
Assert-True ($script:BootstrapCalls -eq 1) 'Create isolated environment once'
Assert-True ($script:FindCalls -ge 2) 'Use newly discovered Python in same console'
""")


@WINDOWS
@pytest.mark.parametrize("repair", [False, True], ids=["repeat", "repair"])
def test_existing_python_does_not_trigger_system_install_and_preserves_mode(tmp_path, repair):
    _run_ps(tmp_path, r"""
$script:BootstrapCalls = 0
function Find-YeboyPython { return 'existing-python' }
function Find-YeboyWinget { throw 'Existing Python does not need winget' }
function Invoke-YeboyWinget { throw 'Unexpected system installation' }
function Invoke-YeboyBootstrap([string] $Python, [switch] $Repair) {
    Assert-True ($Python -eq 'existing-python') 'Use existing compatible interpreter'
    Assert-True ([bool]$Repair -eq EXPECTED_REPAIR) 'Forward explicit repair mode'
    $script:BootstrapCalls++
    return 0
}
function Invoke-YeboyMedia { return 0 }
function Test-YeboyEnvironment { return $true }
$code = Install-YeboyCore -AllowPythonInstall REPAIR_ARGUMENT
Assert-True ($code -eq 0) 'Repeat installation succeeds'
Assert-True ($script:BootstrapCalls -eq 1) 'Only one environment operation'
""".replace("EXPECTED_REPAIR", "$true" if repair else "$false")
         .replace("REPAIR_ARGUMENT", "-Repair" if repair else ""))


@WINDOWS
def test_dependency_failure_does_not_report_ready_environment(tmp_path):
    _run_ps(tmp_path, r"""
function Find-YeboyPython { return 'existing-python' }
function Invoke-YeboyBootstrap { return 7 }
function Invoke-YeboyMedia { throw 'Failed bootstrap must not prepare music' }
function Test-YeboyEnvironment { throw 'Failed bootstrap cannot be treated as ready' }
$code = Install-YeboyCore
Assert-True ($code -ne 0) 'Dependency failure remains failure'
""")


@WINDOWS
def test_failed_imports_after_bootstrap_do_not_report_success(tmp_path):
    _run_ps(tmp_path, r"""
function Find-YeboyPython { return 'existing-python' }
function Invoke-YeboyBootstrap { return 0 }
function Invoke-YeboyMedia { return 0 }
function Test-YeboyEnvironment { return $false }
$code = Install-YeboyCore
Assert-True ($code -ne 0) 'Readiness requires actual environment probe'
""")


@WINDOWS
@pytest.mark.parametrize("menu_code", [0, 7], ids=["normal-exit", "menu-failure"])
def test_ready_launcher_opens_control_center_without_installing(tmp_path, menu_code):
    _run_ps(tmp_path, r"""
$script:MenuCalls = 0
function Test-YeboyEnvironment { return $true }
function Start-YeboyMenu { $script:MenuCalls++; return MENU_CODE }
function Show-YeboySetup { throw 'Healthy launcher must keep ordinary menu' }
function Install-YeboyCore { throw 'Healthy launcher must not reinstall packages' }
function Invoke-YeboyMedia { throw 'Healthy launcher must not download music tools' }
$code = Invoke-YeboyEntry -Mode Launcher
Assert-True ($code -eq MENU_CODE) 'Healthy menu returns its exit code'
Assert-True ($script:MenuCalls -eq 1) 'Open one control center'
""".replace("MENU_CODE", str(menu_code)))


@WINDOWS
def test_unconfigured_launcher_shows_setup_before_python_is_available(tmp_path):
    _run_ps(tmp_path, r"""
$script:SetupCalls = 0
function Test-YeboyEnvironment { return $false }
function Show-YeboySetup { $script:SetupCalls++; return 0 }
function Start-YeboyMenu { throw 'No configured environment exists' }
function Install-YeboyCore { throw 'Do not install before selection' }
$code = Invoke-YeboyEntry -Mode Launcher
Assert-True ($code -eq 0) 'User can exit initial panel'
Assert-True ($script:SetupCalls -eq 1) 'Display pre-Python installation panel'
""")


@WINDOWS
@pytest.mark.parametrize("mode, expected_repair", [("Ensure", False), ("Repair", True)])
def test_noninteractive_ensure_and_repair_never_opt_into_python_install(tmp_path, mode, expected_repair):
    _run_ps(tmp_path, r"""
$script:InstallCalls = 0
function Install-YeboyCore([switch] $AllowPythonInstall, [switch] $Repair) {
    Assert-True (-not $AllowPythonInstall) 'No implicit system-install consent'
    Assert-True ([bool]$Repair -eq EXPECTED_REPAIR) 'Preserve repair selection'
    $script:InstallCalls++
    return 9
}
function Show-YeboySetup { throw 'Ensure/Repair must not prompt unattended' }
$code = Invoke-YeboyEntry -Mode 'ENTRY_MODE'
Assert-True ($code -eq 9) 'Propagate unsuccessful bootstrap exit code'
Assert-True ($script:InstallCalls -eq 1) 'Run one environment bootstrap'
""".replace("EXPECTED_REPAIR", "$true" if expected_repair else "$false")
         .replace("ENTRY_MODE", mode))


@WINDOWS
def test_explicit_setup_mode_displays_install_panel_without_implicit_install(tmp_path):
    _run_ps(tmp_path, r"""
$script:SetupCalls = 0
function Show-YeboySetup { $script:SetupCalls++; return 0 }
function Install-YeboyCore { throw 'Selection must precede installation' }
function Start-YeboyMenu { throw 'Setup mode must display setup panel' }
$code = Invoke-YeboyEntry -Mode Setup
Assert-True ($code -eq 0) 'Setup panel preserves normal exit'
Assert-True ($script:SetupCalls -eq 1) 'Explicit installation panel is available'
""")


@WINDOWS
def test_winget_boundary_uses_exact_official_per_user_package_without_hash_bypass(tmp_path):
    _run_ps(tmp_path, r"""
$script:CapturedArguments = @()
function Invoke-FakeWinget {
    $script:CapturedArguments = @($args)
    $global:LASTEXITCODE = 0
}
$code = Invoke-YeboyWinget -Executable 'Invoke-FakeWinget'
Assert-True ($code -eq 0) 'Propagate successful trusted-catalog install'
$seen = $script:CapturedArguments
Assert-True ($seen[0] -eq 'install') 'Install rather than run arbitrary commands'
foreach ($pair in @(@('--id', 'Python.Python.3.12'), @('--source', 'winget'),
                    @('--scope', 'user'), @('--architecture', 'x64'))) {
    $index = [Array]::IndexOf($seen, $pair[0])
    Assert-True ($index -ge 0 -and $seen[$index + 1] -eq $pair[1]) ('Wrong package policy ' + $pair[0])
}
Assert-True ($seen -contains '--exact') 'No fuzzy package-name matching'
Assert-True ($seen -contains '--disable-interactivity') 'No hidden secondary prompts'
Assert-True (-not ($seen -contains '--ignore-security-hash')) 'Keep installer integrity checks'
Assert-True (-not ($seen -contains '--override')) 'Do not inject arbitrary installer switches'
""")


@WINDOWS
@pytest.mark.parametrize("exit_code", [0, 17], ids=["success", "failure"])
def test_real_bootstrap_boundary_preserves_literal_root_and_exit_code(tmp_path, exit_code):
    root = tmp_path / "Бот O'Connor & $(no-command)" / "YEBOYBot"
    entry = root / "tools/launcher/bootstrap_entry.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("# safe fixture entry\n", encoding="utf-8")
    _run_ps(tmp_path, r"""
$script:YeboyRoot = ROOT_LITERAL
$script:CapturedArguments = @()
function Invoke-FakePython {
    $script:CapturedArguments = @($args)
    $global:LASTEXITCODE = EXPECTED_CODE
}
$code = Invoke-YeboyBootstrap -Python 'Invoke-FakePython' -Repair
Assert-True ($code -eq EXPECTED_CODE) 'Propagate actual Python process result'
$seen = $script:CapturedArguments
Assert-True ($seen[0] -eq '-I' -and $seen[1] -eq '-X' -and $seen[2] -eq 'utf8') 'Run isolated Python with explicit UTF-8'
Assert-True ($seen[3] -ceq (Join-Path $script:YeboyRoot 'tools\launcher\bootstrap_entry.py')) 'Launch exact Python source, never python.exe as a script'
Assert-True ($seen[4] -eq '--repair') 'Forward repair without splitting path'
Assert-True ($seen.Count -eq 5) 'No unintended extra executable or script argument'
""".replace("ROOT_LITERAL", _literal(root)).replace("EXPECTED_CODE", str(exit_code)))


@WINDOWS
@pytest.mark.parametrize("repair", [False, True], ids=["first-install", "repair"])
def test_bootstrap_entry_launches_real_verified_python_in_isolated_mode(tmp_path, repair):
    # Native Windows execution catches the Python-binary-as-script regression
    # that fake PowerShell functions would silently miss.
    root = tmp_path / "Бот O'Connor & $(no-command)"
    launcher = root / "tools/launcher"
    launcher.mkdir(parents=True)
    shutil.copy2(ROOT / "tools/launcher/bootstrap_entry.py", launcher)
    (root / "tools/__init__.py").write_text("", encoding="utf-8")
    (launcher / "__init__.py").write_text("", encoding="utf-8")
    (launcher / "bootstrap.py").write_text(
        "import os, re, sys\n"
        "assert re.fullmatch(r'[0-9a-f]{32}', os.environ['YEBOY_INSTALL_ATTEMPT'])\n"
        "from pathlib import Path\n"
        "assert sys.flags.isolated, 'Python isolation must stay active'\n"
        f"assert sys.argv[1:] == {['--repair'] if repair else []!r}, sys.argv\n"
        f"assert Path(__file__).resolve().parents[2] == Path({str(root)!r}).resolve()\n"
        "print('YEBOY_BOOTSTRAP_NATIVE_OK')\n",
        encoding="utf-8",
    )
    result = _run_ps(tmp_path, r"""
$script:YeboyRoot = ROOT_LITERAL
$code = Invoke-YeboyBootstrap -Python PYTHON_LITERAL REPAIR_ARGUMENT
Assert-True ($code -eq 0) 'Run real Python and invoke module successfully'
""".replace("ROOT_LITERAL", _literal(root))
         .replace("PYTHON_LITERAL", _literal(sys.executable))
         .replace("REPAIR_ARGUMENT", "-Repair" if repair else ""))
    assert "YEBOY_BOOTSTRAP_NATIVE_OK" in result.stdout
    assert "Non-UTF-8" not in result.stdout


@WINDOWS
def test_install_selection_releases_python_menu_before_repair_and_reopens_it(tmp_path):
    _run_ps(tmp_path, r"""
$script:Sequence = [System.Collections.Generic.List[string]]::new()
$script:MenuCalls = 0
function Test-YeboyEnvironment { return $true }
function Start-YeboyMenu {
    $script:MenuCalls++
    $script:Sequence.Add('menu-return')
    if ($script:MenuCalls -eq 1) { return 24 }
    return 0
}
function Show-YeboySetup { $script:Sequence.Add('setup'); return 0 }
$code = Invoke-YeboyEntry -Mode Launcher
Assert-True ($code -eq 0) 'Reopen control center after install panel'
Assert-True (($script:Sequence -join ',') -eq 'menu-return,setup,menu-return') 'Never repair inside loaded Python process'
""")


@WINDOWS
def test_failed_repair_after_menu_exit_does_not_reopen_invalid_environment(tmp_path):
    _run_ps(tmp_path, r"""
$script:Checks = 0
$script:MenuCalls = 0
function Test-YeboyEnvironment {
    $script:Checks++
    return ($script:Checks -eq 1)
}
function Start-YeboyMenu { $script:MenuCalls++; return 24 }
function Show-YeboySetup { return 0 }
$code = Invoke-YeboyEntry -Mode Launcher
Assert-True ($code -ne 0) 'Failed environment must not reopen bot menu'
Assert-True ($script:MenuCalls -eq 1) 'One exited menu, no failed restart loop'
""")


@WINDOWS
def test_complete_bundle_preflight_is_read_only_and_passes_for_repository(tmp_path):
    _run_ps(tmp_path, r"""
Assert-True (Test-YeboySourceBundle) 'All required source files present in full ZIP'
""")


@WINDOWS
def test_missing_source_files_stop_before_python_or_network_and_save_safe_report(tmp_path):
    root = tmp_path / "Неповний ZIP бота"
    root.mkdir()
    result = _run_ps(tmp_path, r"""
$script:YeboyRoot = FIXTURE_ROOT
$script:InstallCalls = 0
function Read-Host { return '1' }
function Install-YeboyCore { $script:InstallCalls++; throw 'Must reject partial ZIP before any downloads' }
$code = Show-YeboySetup
Assert-True ($code -eq 1) 'Partial ZIP is not an installable project'
Assert-True ($script:InstallCalls -eq 0) 'No Python, WinGet, venv or media started'
$report = Get-Content -LiteralPath (Join-Path $script:YeboyRoot 'data/v2/launcher-report.json') -Raw |
    ConvertFrom-Json
Assert-True ($report.stage -eq 'source-bundle') 'Missing source stage reported'
Assert-True ($report.exit_code -eq 1 -and $report.status -eq 'failed') 'Failure status persisted'
""".replace("FIXTURE_ROOT", _literal(root)))
    assert "bootstrap_entry.py" in result.stdout
    assert not (root / ".venv-v2").exists()
    assert not (root / ".env.v2").exists()


@WINDOWS
def test_report_contains_only_fixed_metadata_and_never_leaks_user_secrets(tmp_path):
    root = tmp_path / "Бот O'Connor & private-token"
    root.mkdir()
    report_path = root / "data/v2/launcher-report.json"
    _run_ps(tmp_path, r"""
$script:YeboyRoot = FIXTURE_ROOT
$script:YeboyAttemptId = [guid]::NewGuid().ToString('N')
Write-YeboyLauncherReport -Stage bootstrap -ExitCode 17
Assert-True (Test-Path -LiteralPath (Join-Path $script:YeboyRoot 'data/v2/launcher-report.json')) 'Report created without Python'
Write-YeboyLauncherReport -Stage media -ExitCode 31
$report = Get-Content -LiteralPath (Join-Path $script:YeboyRoot 'data/v2/launcher-report.json') -Raw | ConvertFrom-Json
Assert-True ($report.stage -eq 'media') 'Last failed stage is recorded'
Assert-True ($report.exit_code -eq 31) 'Last child code is recorded'
Assert-True ($report.schema -eq 2 -and $report.status -eq 'failed') 'Versioned diagnostic format'
""".replace("FIXTURE_ROOT", _literal(root)))
    report = report_path.read_text(encoding="utf-8-sig")
    import json
    payload = json.loads(report)
    assert set(payload) == {"schema", "timestamp_utc", "status", "stage", "exit_code",
                            "matched_security_events", "attempt_id"}
    assert len(payload["attempt_id"]) == 32
    assert set(payload["attempt_id"]) <= set("0123456789abcdef")
    assert payload["matched_security_events"] == []
    assert "private-token" not in report and "O'Connor" not in report
    assert "password" not in report and "DISCORD_TOKEN" not in report
    assert not list(report_path.parent.glob("launcher-report-*.tmp"))


@WINDOWS
def test_fresh_access_denial_report_is_required_before_querying_security_events(tmp_path):
    root = tmp_path / "Бот з пробілами"
    root.mkdir()
    _run_ps(tmp_path, r"""
$script:YeboyRoot = ROOT_LITERAL
$path = Join-Path $script:YeboyRoot 'data/v2/install-report.json'
[void][IO.Directory]::CreateDirectory((Split-Path -Parent $path))
$script:YeboyAttemptId = [guid]::NewGuid().ToString('N')
$script:YeboyBootstrapStarted = [DateTimeOffset]::UtcNow.AddSeconds(-1)
$sample = @{schema = 2; status = 'failed'; operation = 'install'; attempt_id = $script:YeboyAttemptId; time_utc = [DateTimeOffset]::UtcNow.ToString('o');
    stage = 'environment-probe'; category = 'access-denied'; winerror = 5}
$sample | ConvertTo-Json -Compress | Set-Content -LiteralPath $path -Encoding UTF8
Assert-True (Test-YeboyFreshVenvDeniedReport) 'Fresh matching WinError 5'
$sample.attempt_id = [guid]::NewGuid().ToString('N')
$sample | ConvertTo-Json -Compress | Set-Content -LiteralPath $path -Encoding UTF8
Assert-True (-not (Test-YeboyFreshVenvDeniedReport)) 'Fresh report from another launcher'
$sample.attempt_id = $script:YeboyAttemptId
$sample.time_utc = [DateTimeOffset]::UtcNow.AddDays(-1).ToString('o')
$sample | ConvertTo-Json -Compress | Set-Content -LiteralPath $path -Encoding UTF8
Assert-True (-not (Test-YeboyFreshVenvDeniedReport)) 'No stale event scan'
$sample.time_utc = [DateTimeOffset]::UtcNow.ToString('o')
foreach ($wrongCode in @(32, 577, '5')) {
    $sample.winerror = $wrongCode
    $sample | ConvertTo-Json -Compress | Set-Content -LiteralPath $path -Encoding UTF8
    Assert-True (-not (Test-YeboyFreshVenvDeniedReport)) 'Only numeric WinError 5 belongs to this diagnostic'
}
Set-Content -LiteralPath $path -Value '{"broken":' -Encoding UTF8
Assert-True (-not (Test-YeboyFreshVenvDeniedReport)) 'Ignore invalid JSON'
""".replace("ROOT_LITERAL", _literal(root)))


@WINDOWS
def test_execution_log_probe_filters_target_and_never_displays_raw_messages(tmp_path):
    result = _run_ps(tmp_path, r"""
$script:YeboyBootstrapStarted = [DateTimeOffset]::UtcNow.AddSeconds(-5)
$script:ExpectedTarget = Join-Path $script:YeboyRoot '.venv-v2\Scripts\python.exe'
function New-TestEvent($Filter, [string]$Payload, [DateTime]$Time = (Get-Date).AddSeconds(-1)) {
    $event = [pscustomobject]@{
        Id = $Filter.Id; LogName = $Filter.LogName; TimeCreated = $Time
        Payload = "<Event>$Payload</Event>"; Message = 'private-token'
    }
    $event | Add-Member -MemberType ScriptMethod -Name ToXml -Value { $this.Payload }
    return $event
}
function Get-WinEvent {
    param($FilterHashtable, [int]$MaxEvents, [string]$ErrorAction)
    $target = [Security.SecurityElement]::Escape($script:ExpectedTarget)
    if ($FilterHashtable.Id -eq 3077) {
        New-TestEvent $FilterHashtable "<EventData><Data Name='File Name'>$target</Data></EventData>" ((Get-Date).AddDays(-1))
        New-TestEvent $FilterHashtable "<EventData><Data Name='Process Name'>$target</Data></EventData>"
        New-TestEvent $FilterHashtable "<EventData><Data Name='File Name'>$target</Data></EventData>"
    }
    if ($FilterHashtable.Id -eq 8004) {
        New-TestEvent $FilterHashtable '<UserData><RuleAndFileData><FilePath>C:\Other\.venv-v2\Scripts\python.exe</FilePath></RuleAndFileData></UserData>'
        New-TestEvent $FilterHashtable "<UserData><RuleAndFileData><FilePath>$target</FilePath></RuleAndFileData></UserData>"
    }
    if ($FilterHashtable.Id -eq 1121) { throw 'Not accessible' }
}
$codes = @(Get-YeboyVenvBlockEvents)
Assert-True ($codes.Count -eq 2) 'Only matching executable and a known event ID'
Assert-True (($codes -join ',') -eq 'CodeIntegrity:3077,AppLocker:8004') 'Handle EventData and UserData'
""")
    assert "private-token" not in result.stdout
    assert ".venv-v2" not in result.stdout


@WINDOWS
def test_setup_records_only_allowlisted_security_event_codes_after_denied_venv(tmp_path):
    root = tmp_path / "Бот O'Connor"
    root.mkdir()
    result = _run_ps(tmp_path, r"""
$script:YeboyRoot = ROOT_LITERAL
$path = Join-Path $script:YeboyRoot 'data/v2/install-report.json'
[void][IO.Directory]::CreateDirectory((Split-Path -Parent $path))
function Test-YeboySourceBundle { return $true }
function Read-Host { return '1' }
function Install-YeboyCore {
    $script:YeboyInstallStage = 'bootstrap'
    $script:YeboyBootstrapStarted = [DateTimeOffset]::UtcNow
    @{schema = 2; status = 'failed'; operation = 'install'; attempt_id = $script:YeboyAttemptId;
      time_utc = [DateTimeOffset]::UtcNow.ToString('o'); stage = 'environment-probe';
      category = 'access-denied'; winerror = 5} |
        ConvertTo-Json -Compress | Set-Content -LiteralPath $path -Encoding UTF8
    return 1
}
function Get-YeboyVenvBlockEvents { return @('CodeIntegrity:3077','private-token') }
$code = Show-YeboySetup
Assert-True ($code -eq 1) 'Setup error is retained'
$report = Get-Content -LiteralPath (Join-Path $script:YeboyRoot 'data/v2/launcher-report.json') -Raw |
    ConvertFrom-Json
Assert-True ($report.stage -eq 'bootstrap') 'Stage preserved'
Assert-True ($report.attempt_id -ceq $script:YeboyAttemptId) 'Attempt ID preserved'
Assert-True ($report.matched_security_events.Count -eq 1) 'Untrusted event IDs discarded'
Assert-True ($report.matched_security_events[0] -eq 'CodeIntegrity:3077') 'Only known event evidence recorded'
""".replace("ROOT_LITERAL", _literal(root)))
    report = (root / "data/v2/launcher-report.json").read_text(encoding="utf-8")
    assert "private-token" not in report
    assert "private-token" not in result.stdout
    assert "O'Connor" not in report


@WINDOWS
def test_setup_failure_after_bootstrap_saves_stage_and_failure_code(tmp_path):
    root = tmp_path / "Бот O'Connor"
    root.mkdir()
    _run_ps(tmp_path, r"""
$script:YeboyRoot = FIXTURE_ROOT
function Test-YeboySourceBundle { return $true }
function Read-Host { return '1' }
function Install-YeboyCore {
    $script:YeboyInstallStage = 'bootstrap'
    return 9
}
$code = Show-YeboySetup
Assert-True ($code -eq 9) 'Preserve child-process failure code'
$report = Get-Content -LiteralPath (Join-Path $script:YeboyRoot 'data/v2/launcher-report.json') -Raw | ConvertFrom-Json
Assert-True ($report.stage -eq 'bootstrap' -and $report.exit_code -eq 9) 'Correct stage saved'
""".replace("FIXTURE_ROOT", _literal(root)))


@WINDOWS
def test_failed_report_write_does_not_hide_install_error(tmp_path):
    root = tmp_path / "Бот з заблокованим звітом"
    root.mkdir()
    _run_ps(tmp_path, r"""
$script:YeboyRoot = FIXTURE_ROOT
function Test-YeboySourceBundle { return $true }
function Read-Host { return '1' }
function Install-YeboyCore {
    $script:YeboyInstallStage = 'media'
    return 31
}
function Write-YeboyLauncherReport {
    param([string]$Stage, [int]$ExitCode)
    Write-Host 'Звіт запуску не вдалося записати.'
}
$code = Show-YeboySetup
Assert-True ($code -eq 31) 'Failure reporting cannot mask original exit status'
""".replace("FIXTURE_ROOT", _literal(root)))


@WINDOWS
def test_setup_cancel_never_installs_system_components(tmp_path):
    _run_ps(tmp_path, r"""
function Read-Host { return '0' }
function Install-YeboyCore { throw 'Exit is not installation consent' }
$code = Show-YeboySetup
Assert-True ($code -eq 0) 'User can cancel before Python exists'
""")


@WINDOWS
def test_setup_install_button_is_explicit_consent_and_reuses_verified_packages(tmp_path):
    _run_ps(tmp_path, r"""
$script:InstallCalls = 0
function Read-Host { return '1' }
function Install-YeboyCore([switch] $AllowPythonInstall, [switch] $Repair) {
    Assert-True ([bool]$AllowPythonInstall) 'Install selection explicitly allows Python install'
    Assert-True (-not $Repair) 'Initial setup reuses verified packages instead of forcing reinstall'
    $script:InstallCalls++
    return 0
}
$code = Show-YeboySetup
Assert-True ($code -eq 0) 'Successful install returns to launcher'
Assert-True ($script:InstallCalls -eq 1) 'One button press invokes one operation'
""")


@WINDOWS
def test_invalid_choice_then_failed_install_stops_without_repeating_initial_panel(tmp_path):
    _run_ps(tmp_path, r"""
$script:Choices = [System.Collections.Generic.Queue[string]]::new()
foreach ($choice in @('invalid', '1', '0')) { $script:Choices.Enqueue($choice) }
$script:InstallCalls = 0
function Read-Host {
    Assert-True ($script:Choices.Count -gt 0) 'Panel must not spin past available input'
    return $script:Choices.Dequeue()
}
function Install-YeboyCore { $script:InstallCalls++; return 1 }
$code = Show-YeboySetup
Assert-True ($code -eq 1) 'Preserve installation failure for CMD pause'
Assert-True ($script:InstallCalls -eq 1) 'Invalid choice does not install or silently retry'
Assert-True ($script:Choices.Count -eq 1) 'Stop after failure instead of reopening initial panel'
""")



@WINDOWS
def test_interactive_media_probe_reuses_ffmpeg_but_installs_deno_locally(tmp_path):
    result = _run_ps(tmp_path, r"""
$script:YeboyInteractiveInstall = $true
$script:Choices = [System.Collections.Generic.Queue[string]]::new()
$script:Choices.Enqueue('1')
$script:Choices.Enqueue('2')
function Read-Host { return $script:Choices.Dequeue() }
function Find-YeboyPython { return 'verified-python' }
function Invoke-YeboySystemMediaProbe {
    param([string]$Python)
    Assert-True ($Python -eq 'verified-python') 'Probe verified interpreter before pip'
    return [pscustomobject]@{
        ffmpeg = [pscustomobject]@{ found = $true; compatible = $true; path = 'C:\Program Files\FFmpeg\bin\ffmpeg.exe'; version = '9.0.2' }
        deno = [pscustomobject]@{ found = $true; compatible = $true; path = 'C:\Deno Test\deno.exe'; version = '2.9.8' }
    }
}
function Invoke-YeboyBootstrap { return 0 }
function Invoke-YeboyMedia {
    param([switch]$SelectMedia, [string]$SystemFfmpeg, [string]$SystemDeno)
    Assert-True ([bool]$SelectMedia) 'Interactive selection forwarded to media'
    Assert-True ($SystemFfmpeg -eq 'C:\Program Files\FFmpeg\bin\ffmpeg.exe') 'Reuse chosen global FFmpeg'
    Assert-True ($SystemDeno -eq '') 'Deno explicitly requested local'
    return 0
}
function Test-YeboyEnvironment { return $true }
$code = Install-YeboyCore
Assert-True ($code -eq 0) 'Selected hybrid media setup should succeed'
Assert-True ($script:Choices.Count -eq 0) 'Only two separate media questions'
""")
    assert "C:\\Program Files\\FFmpeg\\bin\\ffmpeg.exe" in result.stdout
    assert "C:\\Deno Test\\deno.exe" in result.stdout
    assert "[OK]" in result.stdout


@WINDOWS
@pytest.mark.parametrize(("name", "package"), [
    ("ffmpeg", "Gyan.FFmpeg"), ("deno", "DenoLand.Deno"),
])
def test_media_winget_fallback_uses_pinned_official_package_and_user_scope(tmp_path, name, package):
    _run_ps(tmp_path, r"""
$script:Captured = @()
function Find-YeboyWinget { return 'Invoke-TestWinget' }
function Confirm-YeboyPackageInstall { return $true }
function Invoke-TestWinget {
    $script:Captured = @($args)
    $global:LASTEXITCODE = 0
}
$code = Invoke-YeboyWingetMedia -Name NAME_PLACEHOLDER
Assert-True ($code -eq 0) 'WinGet fallback success'
$argsSeen = $script:Captured
Assert-True ($argsSeen[0] -eq 'install') 'Official install only'
foreach ($pair in @(@('--id', PACKAGE_PLACEHOLDER), @('--source', 'winget'),
                    @('--scope', 'user'), @('--architecture', 'x64'))) {
    $index = [Array]::IndexOf($argsSeen, $pair[0])
    Assert-True ($index -ge 0 -and $argsSeen[$index + 1] -eq $pair[1]) ('Wrong policy: ' + $pair[0])
}
foreach ($flag in @('--exact', '--silent', '--accept-package-agreements',
                    '--accept-source-agreements', '--disable-interactivity')) {
    Assert-True ($argsSeen -contains $flag) ('Missing trusted-catalog flag ' + $flag)
}
foreach ($bad in @('--force', '--ignore-security-hash', '--override')) {
    Assert-True (-not ($argsSeen -contains $bad)) ('No security bypass ' + $bad)
}
""".replace("NAME_PLACEHOLDER", _literal(name)).replace("PACKAGE_PLACEHOLDER", _literal(package)))


@WINDOWS
def test_media_winget_decline_never_executes_installer(tmp_path):
    _run_ps(tmp_path, r"""
function Find-YeboyWinget { return 'Invoke-TestWinget' }
function Invoke-TestWinget { throw 'Declined installer must never run' }
function Read-Host { return '' }
$code = Invoke-YeboyWingetMedia -Name ffmpeg
Assert-True ($code -eq 2) 'Declining WinGet ends the fallback'
""")


@WINDOWS
def test_denied_portable_ffmpeg_falls_back_to_winget_inside_same_setup(tmp_path):
    result = _run_ps(tmp_path, r"""
$script:YeboyInteractiveInstall = $true
$script:Probes = 0
$script:MediaCalls = 0
$script:WingetCalls = 0
function Find-YeboyPython { return 'verified-python' }
function Invoke-YeboySystemMediaProbe {
    $script:Probes++
    $ready = $script:Probes -ge 2
    return [pscustomobject]@{
        ffmpeg = [pscustomobject]@{ found = $ready; compatible = $ready; path = 'C:\WinGet\ffmpeg.exe'; version = '9.0.2' }
        deno = [pscustomobject]@{ found = $false; compatible = $false; path = ''; version = '' }
    }
}
function Invoke-YeboyBootstrap { return 0 }
function Invoke-YeboyMedia {
    param([switch]$SelectMedia, [string]$SystemFfmpeg, [string]$SystemDeno)
    Assert-True ([bool]$SelectMedia) 'Use selected external or local paths'
    $script:MediaCalls++
    if ($script:MediaCalls -eq 1) {
        Assert-True (-not $SystemFfmpeg) 'First try a pinned local FFmpeg'
        return 31
    }
    Assert-True ($SystemFfmpeg -eq 'C:\WinGet\ffmpeg.exe') 'Reuse WinGet binary by verified path, not refreshed PATH'
    Assert-True (-not $SystemDeno) 'Keep local Deno selection'
    return 0
}
function Invoke-YeboyWingetMedia {
    param([string]$Name)
    Assert-True ($Name -eq 'ffmpeg') 'Fallback is only for denied FFmpeg, not Deno'
    $script:WingetCalls++
    return 0
}
function Test-YeboyEnvironment { return $true }
$code = Install-YeboyCore
Assert-True ($code -eq 0) 'User can finish via only YEBOYBot setup'
Assert-True ($script:WingetCalls -eq 1) 'Only one exact WinGet fallback'
Assert-True ($script:MediaCalls -eq 2) 'Continue the same setup, no second CMD run'
Assert-True ($script:Probes -eq 2) 'Refresh installed-program discovery in current session'
""")
    assert "[4/4]" in result.stdout


@WINDOWS
def test_winget_fallback_never_runs_in_noninteractive_repairs(tmp_path):
    _run_ps(tmp_path, r"""
$script:YeboyInteractiveInstall = $false
function Find-YeboyPython { return 'verified-python' }
function Invoke-YeboyBootstrap { return 0 }
function Invoke-YeboyMedia { return 31 }
function Invoke-YeboyWingetMedia { throw 'Never install global software during automated repair' }
$code = Install-YeboyCore -Repair
Assert-True ($code -eq 31) 'No silent system package installation'
""")


@WINDOWS
def test_winget_fallback_fails_closed_when_executable_is_still_unavailable(tmp_path):
    _run_ps(tmp_path, r"""
$script:YeboyInteractiveInstall = $true
$script:MediaCalls = 0
$script:WingetCalls = 0
function Find-YeboyPython { return 'verified-python' }
function Invoke-YeboySystemMediaProbe {
    return [pscustomobject]@{
        ffmpeg = [pscustomobject]@{ found = $false; compatible = $false; path = ''; version = '' }
        deno = [pscustomobject]@{ found = $false; compatible = $false; path = ''; version = '' }
    }
}
function Invoke-YeboyBootstrap { return 0 }
function Invoke-YeboyMedia { $script:MediaCalls++; return 31 }
function Invoke-YeboyWingetMedia { $script:WingetCalls++; return 5 }
function Test-YeboyEnvironment { throw 'Never report ready without playable FFmpeg' }
$code = Install-YeboyCore
Assert-True ($code -eq 31) 'Keep original denied-executable failure code'
Assert-True ($script:WingetCalls -eq 1) 'No repeated install attempts'
Assert-True ($script:MediaCalls -eq 1) 'Do not retry media if system FFmpeg not executable'
""")


@WINDOWS
def test_user_cancels_media_choice_before_any_application_install(tmp_path):
    _run_ps(tmp_path, r"""
$script:YeboyInteractiveInstall = $true
function Read-Host { return '0' }
function Find-YeboyPython { return 'verified-python' }
function Invoke-YeboySystemMediaProbe {
    return [pscustomobject]@{
        ffmpeg = [pscustomobject]@{ found = $true; compatible = $true; path = 'C:\ffmpeg.exe'; version = '9.0.2' }
        deno = [pscustomobject]@{ found = $false; compatible = $false; path = ''; version = '' }
    }
}
function Invoke-YeboyBootstrap { throw 'Cancelled before pip/venv creation' }
function Invoke-YeboyMedia { throw 'Cancelled before media installation' }
$code = Install-YeboyCore
Assert-True ($code -eq 0) 'Cancel means no installation; no error loop'
""")


@WINDOWS
def test_broken_system_probe_fails_closed_without_downloading(tmp_path):
    _run_ps(tmp_path, r"""
$script:YeboyInteractiveInstall = $true
function Find-YeboyPython { return 'verified-python' }
function Invoke-YeboySystemMediaProbe { return $null }
function Invoke-YeboyBootstrap { throw 'No bootstrap without confirmed probe' }
function Invoke-YeboyMedia { throw 'No media install after probe failure' }
$code = Install-YeboyCore
Assert-True ($code -ne 0) 'Preflight failure stops initial installation'
""")


@WINDOWS
def test_initial_menu_enables_and_restores_interactive_preflight_flag(tmp_path):
    _run_ps(tmp_path, r"""
function Read-Host { return '1' }
function Install-YeboyCore([switch]$AllowPythonInstall) {
    Assert-True ([bool]$AllowPythonInstall) 'Only explicit setup selection allows system Python'
    Assert-True ([bool]$script:YeboyInteractiveInstall) 'Enable media decisions for human'
    return 0
}
$code = Show-YeboySetup
Assert-True ($code -eq 0) 'Setup choice accepted'
Assert-True (-not $script:YeboyInteractiveInstall) 'No interactive prompts in automated repairs'
""")


@WINDOWS
def test_python_discovery_uses_registry_when_running_console_has_no_python_in_path(tmp_path):
    _run_ps(tmp_path, r"""
$script:ExpectedPython = EXPECTED_PYTHON
$env:LOCALAPPDATA = ''
$env:PATH = ''
function Get-Command { return $null }
function Get-Item([string] $LiteralPath, [string] $ErrorAction) {
    if ($LiteralPath -ne 'HKCU:\SOFTWARE\Python\PythonCore\3.12\InstallPath') { return $null }
    $registry = New-Object PSObject
    $registry | Add-Member -MemberType ScriptMethod -Name GetValue -Value {
        param($Name)
        if ($Name -eq 'ExecutablePath') { return $script:ExpectedPython }
        return $null
    }
    return $registry
}
$found = Find-YeboyPython
Assert-True ($found -eq $script:ExpectedPython) 'Probe real Python from registry without refreshing PATH'
""".replace("EXPECTED_PYTHON", _literal(sys.executable)))


@WINDOWS
@pytest.mark.parametrize("repair_code", [0, 7], ids=["repair-success", "repair-failure-valid-env"])
def test_dependency_update_handoff_exits_python_menu_before_installing(tmp_path, repair_code):
    _run_ps(tmp_path, r"""
$script:Sequence = [System.Collections.Generic.List[string]]::new()
$script:MenuCalls = 0
function Test-YeboyEnvironment { return $true }
function Start-YeboyMenu {
    $script:MenuCalls++
    $script:Sequence.Add('menu-return')
    if ($script:MenuCalls -eq 1) { return 25 }
    return 0
}
function Install-YeboyCore([switch] $AllowPythonInstall, [switch] $Repair) {
    Assert-True (-not $AllowPythonInstall) 'Dependency update is not consent to install system Python'
    Assert-True ([bool]$Repair) 'Dependency update requests explicit repair'
    $script:Sequence.Add('repair')
    return REPAIR_CODE
}
function Show-YeboySetup { throw 'Dependency update does not need full setup panel' }
$code = Invoke-YeboyEntry -Mode Launcher
Assert-True ($code -eq 0) 'Valid environment can reopen management after update attempt'
Assert-True (($script:Sequence -join ',') -eq 'menu-return,repair,menu-return') 'Repair must run after old Python menu exits'
""".replace("REPAIR_CODE", str(repair_code)))


@WINDOWS
def test_dependency_update_failure_with_invalid_environment_returns_failure(tmp_path):
    _run_ps(tmp_path, r"""
$script:Checks = 0
$script:MenuCalls = 0
function Test-YeboyEnvironment { $script:Checks++; return ($script:Checks -eq 1) }
function Start-YeboyMenu { $script:MenuCalls++; return 25 }
function Install-YeboyCore { return 1 }
$code = Invoke-YeboyEntry -Mode Launcher
Assert-True ($code -ne 0) 'Never reopen a broken environment'
Assert-True ($script:MenuCalls -eq 1) 'No menu/reinstall loop'
""")


@WINDOWS
@pytest.mark.parametrize("choice", [None, "", "   "], ids=["eof", "empty", "whitespace"])
def test_initial_panel_can_exit_without_selection_and_does_not_spin_on_eof(tmp_path, choice):
    _run_ps(tmp_path, r"""
$script:ReadCalls = 0
function Read-Host {
    $script:ReadCalls++
    Assert-True ($script:ReadCalls -eq 1) 'Do not loop forever on empty redirected input'
    return USER_INPUT
}
function Install-YeboyCore { throw 'Empty input is not installation consent' }
$code = Show-YeboySetup
Assert-True ($code -eq 0) 'Cancel without installing'
""".replace("USER_INPUT", "$null" if choice is None else _literal(choice)))


@WINDOWS
@pytest.mark.parametrize("preset, failed_probe", [(False, False), (True, False), (True, True)],
                         ids=["unset-success", "preset-success", "preset-failed-probe"])
def test_python_discovery_disables_automatic_install_and_restores_caller_flags(tmp_path, preset, failed_probe):
    _run_ps(tmp_path, r"""
$script:ExpectedPython = EXPECTED_PYTHON
$script:OriginalFlags = @{
    PYTHON_MANAGER_AUTOMATIC_INSTALL = INITIAL_AUTOMATIC
    PYLAUNCHER_ALLOW_INSTALL = INITIAL_ALLOW
    PYLAUNCHER_ALWAYS_INSTALL = INITIAL_ALWAYS
}
foreach ($name in $script:OriginalFlags.Keys) {
    [Environment]::SetEnvironmentVariable($name, $script:OriginalFlags[$name], 'Process')
}
$env:LOCALAPPDATA = ''
$script:ProbeFlags = $null
$script:ProbeCalls = 0
function Get-Command([string] $Name, [string] $CommandType, [string] $ErrorAction) {
    if ($Name -eq 'py.exe') { return [pscustomobject]@{Source = 'Invoke-YeboyProbeFixture'} }
    return $null
}
function Get-Item { return $null }
function Test-Path([string] $LiteralPath, [string] $PathType) {
    if ($LiteralPath -eq 'Invoke-YeboyProbeFixture') { return $true }
    return [IO.File]::Exists($LiteralPath)
}
function Invoke-YeboyProbeFixture {
    # Capture the policy at the subprocess boundary instead of asserting inside
    # it: discovery intentionally catches exceptions from failed probes.
    $script:ProbeCalls++
    $script:ProbeFlags = @{
        Automatic = [Environment]::GetEnvironmentVariable('PYTHON_MANAGER_AUTOMATIC_INSTALL', 'Process')
        Allow = [Environment]::GetEnvironmentVariable('PYLAUNCHER_ALLOW_INSTALL', 'Process')
        Always = [Environment]::GetEnvironmentVariable('PYLAUNCHER_ALWAYS_INSTALL', 'Process')
    }
    if (FAILED_PROBE) { throw 'Synthetic unavailable runtime' }
    $global:LASTEXITCODE = 0
    return $script:ExpectedPython
}
$found = Find-YeboyPython
Assert-True ($script:ProbeCalls -eq 1) 'Exercise one launcher probe without installing Python'
Assert-True ($script:ProbeFlags.Automatic -ceq 'false') 'Disable modern manager automatic installation during detection'
Assert-True ($null -eq $script:ProbeFlags.Allow) 'Remove legacy install permission during detection'
Assert-True ($null -eq $script:ProbeFlags.Always) 'Remove forced legacy install during detection'
if (FAILED_PROBE) {
    Assert-True ($null -eq $found) 'A failed runtime probe is not a compatible interpreter'
} else {
    Assert-True ($found -eq $script:ExpectedPython) 'Preserve successful discovery'
}
foreach ($name in $script:OriginalFlags.Keys) {
    $actual = [Environment]::GetEnvironmentVariable($name, 'Process')
    Assert-True ($actual -ceq $script:OriginalFlags[$name]) ('Restore caller variable ' + $name)
}
""".replace("EXPECTED_PYTHON", _literal(sys.executable))
         .replace("INITIAL_AUTOMATIC", "'true'" if preset else "$null")
         .replace("INITIAL_ALLOW", "'1'" if preset else "$null")
         .replace("INITIAL_ALWAYS", "'1'" if preset else "$null")
         .replace("FAILED_PROBE", "$true" if failed_probe else "$false"))


@WINDOWS
@pytest.mark.parametrize("missing_dependency", [False, True], ids=["ready", "missing-music"])
def test_environment_readiness_runs_shared_runtime_import_check(tmp_path, missing_dependency):
    # Real native Python invocation catches PowerShell argument-quoting failures;
    # the fixture needs no Discord token, external packages or network access.
    root = tmp_path / "Бот з пробілами O'Connor & music"
    package = root / "tools/launcher"
    package.mkdir(parents=True)
    (root / "tools/__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    check = "import nonexistent_yeboy_music_dependency" if missing_dependency else "import json"
    (package / "bootstrap.py").write_text(
        "def validate_environment(root):\n    pass\n" + f"IMPORT_CHECK = {check!r}\n",
        encoding="utf-8",
    )
    (package / "media.py").write_text("def check_enabled_music(root):\n    pass\n", encoding="utf-8")
    subprocess.run(
        [sys.executable, "-I", "-m", "venv", "--without-pip", str(root / ".venv-v2")],
        check=True, capture_output=True, timeout=45,
    )
    _run_ps(tmp_path, r"""
$script:YeboyRoot = FIXTURE_ROOT
$ready = Test-YeboyEnvironment
Assert-True ($ready -eq EXPECTED_READY) 'Shared import check determines readiness, including music dependencies'
""".replace("FIXTURE_ROOT", _literal(root))
         .replace("EXPECTED_READY", "$false" if missing_dependency else "$true"))


@WINDOWS
def test_installation_prepares_media_before_readiness_in_one_ordered_attempt(tmp_path):
    result = _run_ps(tmp_path, r"""
$script:Sequence = [System.Collections.Generic.List[string]]::new()
function Find-YeboyPython { $script:Sequence.Add('python'); return 'existing-python' }
function Invoke-YeboyBootstrap { $script:Sequence.Add('bootstrap'); return 0 }
function Invoke-YeboyMedia { $script:Sequence.Add('media'); return 0 }
function Test-YeboyEnvironment { $script:Sequence.Add('ready'); return $true }
$code = Install-YeboyCore
Assert-True ($code -eq 0) 'Complete all four stages before reporting ready'
Assert-True (($script:Sequence -join ',') -eq 'python,bootstrap,media,ready') 'Music follows dependencies and precedes final readiness'
""")
    stages = [result.stdout.index(f"[{number}/4]") for number in range(1, 5)]
    assert stages == sorted(stages)


@WINDOWS
def test_media_failure_stops_installation_before_ready_or_menu(tmp_path):
    result = _run_ps(tmp_path, r"""
$script:MediaCalls = 0
function Find-YeboyPython { return 'existing-python' }
function Invoke-YeboyBootstrap { return 0 }
function Invoke-YeboyMedia { $script:MediaCalls++; return 19 }
function Test-YeboyEnvironment { throw 'Failed music cannot be marked ready' }
function Start-YeboyMenu { throw 'Failed music must not launch the menu' }
$code = Install-YeboyCore
Assert-True ($code -eq 19) 'Keep actual media failure code'
Assert-True ($script:MediaCalls -eq 1) 'Do not retry media forever'
""")
    assert "ГОТОВО" not in result.stdout
    assert "[4/4]" not in result.stdout


@WINDOWS
@pytest.mark.parametrize("exit_code", [0, 23], ids=["success", "failure"])
def test_media_boundary_uses_native_python_with_literal_root_and_exit_code(tmp_path, exit_code):
    # A synthetic Python module captures the real argument boundary without
    # downloading or running media executables.
    root = tmp_path / "Бот з пробілами O'Connor & music"
    package = root / "tools/launcher"
    package.mkdir(parents=True)
    (root / "tools/__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "media.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "assert sys.flags.isolated\n"
        "assert sys.argv[1:] == ['--prepare']\n"
        f"assert Path(sys.path[0]).resolve() == Path({str(root)!r}).resolve()\n"
        "print('YEBOY_MEDIA_BOUNDARY_OK')\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, "-I", "-m", "venv", "--without-pip", str(root / ".venv-v2")],
        check=True, capture_output=True, timeout=45,
    )
    result = _run_ps(tmp_path, r"""
$script:YeboyRoot = FIXTURE_ROOT
$code = Invoke-YeboyMedia
Assert-True ($code -eq EXPECTED_CODE) 'Preserve successful and failed native media exits'
""".replace("FIXTURE_ROOT", _literal(root)).replace("EXPECTED_CODE", str(exit_code)))
    assert "YEBOY_MEDIA_BOUNDARY_OK" in result.stdout


@WINDOWS
@pytest.mark.parametrize("scenario, expected_code, expected_order", [
    ("success", 0, "apply,repair,verify,confirm"),
    ("rollback", 2, "apply,rollback,repair,verify"),
    ("unconfirmed", 1, "apply,rollback"),
])
def test_github_updater_external_worker_handoff_is_atomic(
        tmp_path, scenario, expected_code, expected_order):
    # Run real PowerShell 5.1 orchestration without network or dependency changes.
    import json
    root = tmp_path / "YEBOYBot O'Connor"
    (root / "tools").mkdir(parents=True)
    (root / "data/v2").mkdir(parents=True)
    (root / "tools/update_worker.py").write_text("# mocked worker", encoding="utf-8")
    (root / "data/v2/update-plan.json").write_text(
        json.dumps({"was_running": False}), encoding="utf-8")
    body = r"""
$script:YeboyRoot = ROOT_PLACEHOLDER
$script:Steps = [System.Collections.Generic.List[string]]::new()
function Invoke-YeboyUpdateWorker {
    param($Python, $Snapshot, $Action)
    $script:Steps.Add([string]$Action)
    if (SCENARIO_PLACEHOLDER -eq 'success') { return 0 }
    if ($Action -eq 'apply') { return 1 }
    if (SCENARIO_PLACEHOLDER -eq 'rollback') { return 0 }
    return 1
}
function Install-YeboyCore {
    param([switch]$Repair)
    $script:Steps.Add('repair')
    return 0
}
function Test-YeboyAfterUpdate {
    $script:Steps.Add('verify')
    return 0
}
function Start-YeboyAfterUpdate { throw 'Must not restart when was_running=False' }
$result = Invoke-YeboyGitHubUpdate
Assert-True ($result -eq EXPECTED_PLACEHOLDER) 'Updater return code must reflect recovery status'
Assert-True (($script:Steps -join ',') -eq ORDER_PLACEHOLDER) 'Correct worker/repair/verify order'
if (SCENARIO_PLACEHOLDER -eq 'unconfirmed') {
    $remains = @(Get-ChildItem -LiteralPath (Join-Path $script:YeboyRoot 'data\\v2') -Filter 'updater-worker-*.py' -File)
    Assert-True ($remains.Count -eq 1) 'Keep updater snapshot after failed recovery'
}
""".replace("ROOT_PLACEHOLDER", _literal(root)).replace(
        "SCENARIO_PLACEHOLDER", _literal(scenario)).replace(
        "EXPECTED_PLACEHOLDER", str(expected_code)).replace(
        "ORDER_PLACEHOLDER", _literal(expected_order))
    _run_ps(tmp_path, body, timeout=90)


@WINDOWS
def test_update_session_blocks_recovery_and_python_until_restart(tmp_path):
    # Real FileShare.None handle versus a child Python process: no JSON marker
    # exists here, modeling confirm/rollback followed by dependency verification.
    root = tmp_path / "session bot"
    root.mkdir()
    probe = (
        "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[2]); "
        "from core_v2.runtime import require_update_complete\n"
        "try: require_update_complete(Path(sys.argv[1]))\n"
        "except RuntimeError: sys.exit(3)\n"
    )
    body = r"""
$script:YeboyRoot = ROOT_PLACEHOLDER
$script:Restarted = $false
function Test-PythonProbe {
    param([int]$Expected)
    & PYTHON_PLACEHOLDER -c PROBE_PLACEHOLDER $script:YeboyRoot REPO_PLACEHOLDER 2>$null
    if ($Expected -eq 0) { Assert-True ($LASTEXITCODE -eq 0) 'Python may launch after session release' }
    else { Assert-True ($LASTEXITCODE -ne 0) 'Python must refuse active PowerShell session' }
}
function Invoke-YeboyGitHubUpdateCore {
    Test-PythonProbe 1
    Assert-True ((Restore-YeboyInterruptedUpdate) -eq 1) 'Recovery must refuse a live update'
    Assert-True ((Invoke-YeboyGitHubUpdate) -eq 1) 'Another updater must refuse a live update'
    Test-PythonProbe 1
    $script:YeboyRestartAfterUpdate = $true
    return 0
}
function Start-YeboyAfterUpdate {
    Test-PythonProbe 0
    $script:Restarted = $true
    return 0
}
Assert-True ((Invoke-YeboyGitHubUpdate) -eq 0) 'Update succeeds'
Assert-True $script:Restarted 'Restart after session release'
function Restore-YeboyInterruptedUpdateCore {
    Test-PythonProbe 1
    Assert-True ((Invoke-YeboyGitHubUpdate) -eq 1) 'Update cannot interrupt recovery'
    throw 'Injected recovery failure'
}
$thrown = $false
try { $null = Restore-YeboyInterruptedUpdate } catch { $thrown = $true }
Assert-True $thrown 'Injected failure surfaced'
Test-PythonProbe 0
""".replace("ROOT_PLACEHOLDER", _literal(root)).replace(
        "PYTHON_PLACEHOLDER", _literal(sys.executable)).replace(
        "PROBE_PLACEHOLDER", _literal(probe)).replace("REPO_PLACEHOLDER", _literal(ROOT))
    _run_ps(tmp_path, body, timeout=90)


@WINDOWS
@pytest.mark.parametrize("failure", ["repair", "verify"])
def test_recovery_retries_persistent_repair_gate_without_code_transaction(tmp_path, failure):
    root = tmp_path / "recovery bot"
    directory = root / "data/v2"
    directory.mkdir(parents=True)
    marker = directory / "update-repair-required.json"
    marker.write_text('{"phase":"repair_required"}', encoding="utf-8")
    body = r"""
$script:YeboyRoot = ROOT_PLACEHOLDER
$script:Fail = FAILURE_PLACEHOLDER
$script:Repairs = 0
function Read-Host { return 'ВІДНОВИТИ' }
function Install-YeboyCore {
    param([switch]$Repair)
    $script:Repairs++
    if ($script:Fail -eq 'repair') { return 1 }
    return 0
}
function Test-YeboyAfterUpdate {
    if ($script:Fail -eq 'verify') { return 1 }
    return 0
}
function Invoke-YeboyUpdateWorker { throw 'Code already restored; no rollback to repeat' }
Assert-True ((Restore-YeboyInterruptedUpdate) -eq 1) 'Failed dependency or smoke check blocks recovery'
Assert-True (Test-Path -LiteralPath MARKER_PLACEHOLDER) 'Repair gate survives failure'
$script:Fail = ''
Assert-True ((Restore-YeboyInterruptedUpdate) -eq 0) 'Next launch retries repair without transaction'
Assert-True ($script:Repairs -eq 2) 'Both attempts verify dependencies'
Assert-True (-not (Test-Path -LiteralPath MARKER_PLACEHOLDER)) 'Gate cleared only after successful verification'
""".replace("ROOT_PLACEHOLDER", _literal(root)).replace(
        "FAILURE_PLACEHOLDER", _literal(failure)).replace("MARKER_PLACEHOLDER", _literal(marker))
    _run_ps(tmp_path, body, timeout=90)



@WINDOWS
def test_main_cmd_respects_restricted_powershell_policy(tmp_path):
    root = tmp_path / "Політика Windows"
    (root / "tools").mkdir(parents=True)
    shutil.copy2(ROOT / "YEBOYBot.cmd", root)
    (root / "tools/windows_setup.ps1").write_text("exit 0", encoding="utf-8-sig")
    result = subprocess.run(
        _cmd_entry(root / "YEBOYBot.cmd"), cwd=tmp_path,
        env={**os.environ, "PSExecutionPolicyPreference": "Restricted"},
        input="\n", text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=45,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "PowerShell" in result.stdout
