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


def _run_ps(tmp_path, body, *, input_text="", source=SOURCE):
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
        capture_output=True, timeout=45,
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
    _run_ps(tmp_path, r"""
$tokens = $null
$parseErrors = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile(
    $SOURCE_FILE, [ref]$tokens, [ref]$parseErrors)
Assert-True ($parseErrors.Count -eq 0) 'PowerShell 5.1 syntax must parse'
foreach ($name in @('Find-YeboyPython', 'Find-YeboyWinget', 'Invoke-YeboyWinget',
                    'Invoke-YeboyBootstrap', 'Test-YeboyEnvironment', 'Start-YeboyMenu',
                    'Install-YeboyCore', 'Show-YeboySetup', 'Invoke-YeboyEntry')) {
    Assert-True ($null -ne (Get-Command $name -CommandType Function)) ('Missing ' + $name)
}
""".replace("$SOURCE_FILE", _literal(SOURCE)))


@WINDOWS
def test_main_cmd_without_python_displays_panel_and_can_exit_from_arbitrary_cwd(tmp_path):
    root = tmp_path / "Порожній ПК O'Connor & бот"
    (root / "tools").mkdir(parents=True)
    shutil.copy2(ROOT / "YEBOYBot.cmd", root)
    shutil.copy2(SOURCE, root / "tools/windows_setup.ps1")
    environment = dict(os.environ)
    environment["PATH"] = str(tmp_path / "empty-path")
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
        cwd=tmp_path, input="0\n", text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Core V2" in result.stdout, result.stdout + result.stderr
    assert "[24]" in result.stdout, result.stdout + result.stderr


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
function Invoke-YeboyWinget { return INSTALLER_CODE }
function Invoke-YeboyBootstrap { throw 'Bootstrap after failed Python install' }
$code = Install-YeboyCore -AllowPythonInstall
Assert-True ($code -ne 0) 'Installer failure cannot report success'
""".replace("INSTALLER_CODE", str(installer_code)))


@WINDOWS
def test_installer_success_without_discoverable_python_never_runs_bootstrap(tmp_path):
    _run_ps(tmp_path, r"""
$script:FindCalls = 0
function Find-YeboyPython { $script:FindCalls++; return $null }
function Find-YeboyWinget { return 'fake-winget' }
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
function Test-YeboyEnvironment { throw 'Failed bootstrap cannot be treated as ready' }
$code = Install-YeboyCore
Assert-True ($code -ne 0) 'Dependency failure remains failure'
""")


@WINDOWS
def test_failed_imports_after_bootstrap_do_not_report_success(tmp_path):
    _run_ps(tmp_path, r"""
function Find-YeboyPython { return 'existing-python' }
function Invoke-YeboyBootstrap { return 0 }
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
    _run_ps(tmp_path, r"""
$script:YeboyRoot = 'D:\Бот з пробілами O''Connor & $(no-command)\YEBOYBot'
$script:CapturedArguments = @()
function Invoke-FakePython {
    $script:CapturedArguments = @($args)
    $global:LASTEXITCODE = EXPECTED_CODE
}
$code = Invoke-YeboyBootstrap -Python 'Invoke-FakePython' -Repair
Assert-True ($code -eq EXPECTED_CODE) 'Propagate actual Python process result'
$seen = $script:CapturedArguments
Assert-True ($seen[0] -eq '-I' -and $seen[1] -eq '-X' -and $seen[2] -eq 'utf8' -and $seen[3] -eq '-c') 'Run isolated Python with explicit UTF-8'
Assert-True ($seen[5] -ceq $script:YeboyRoot) 'Root must stay one literal argument'
Assert-True ($seen[6] -eq 'tools.launcher.bootstrap') 'Use existing owned-venv installer'
Assert-True ($seen[7] -eq '--repair') 'Forward repair without splitting root'
""".replace("EXPECTED_CODE", str(exit_code)))


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
def test_setup_cancel_never_installs_system_components(tmp_path):
    _run_ps(tmp_path, r"""
function Read-Host { return '0' }
function Install-YeboyCore { throw 'Exit is not installation consent' }
$code = Show-YeboySetup
Assert-True ($code -eq 0) 'User can cancel before Python exists'
""")


@WINDOWS
def test_setup_install_button_is_explicit_consent_and_uses_repair(tmp_path):
    _run_ps(tmp_path, r"""
$script:InstallCalls = 0
function Read-Host { return '1' }
function Install-YeboyCore([switch] $AllowPythonInstall, [switch] $Repair) {
    Assert-True ([bool]$AllowPythonInstall) 'Install selection explicitly allows Python install'
    Assert-True ([bool]$Repair) 'Button repairs missing runtime packages as well'
    $script:InstallCalls++
    return 0
}
$code = Show-YeboySetup
Assert-True ($code -eq 0) 'Successful install returns to launcher'
Assert-True ($script:InstallCalls -eq 1) 'One button press invokes one operation'
""")


@WINDOWS
def test_invalid_choice_and_failed_install_can_return_to_panel_and_exit(tmp_path):
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
Assert-True ($code -eq 0) 'Exit remains available after failure'
Assert-True ($script:InstallCalls -eq 1) 'Invalid choice does not install or silently retry'
Assert-True ($script:Choices.Count -eq 0) 'Consume only explicit selections'
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
