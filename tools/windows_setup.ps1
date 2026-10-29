#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Launcher', 'Setup', 'Ensure', 'Repair')]
    [string]$Mode = 'Launcher',
    [switch]$LibraryOnly
)

# This small pre-Python layer delegates package and venv operations to Python.
$script:YeboyRoot = Split-Path -Parent $PSScriptRoot
$script:YeboyModuleCode = "import runpy,sys;root=sys.argv.pop(1);module=sys.argv.pop(1);sys.path.insert(0,root);runpy.run_module(module,run_name='__main__',alter_sys=True)"

function Find-YeboyPython {
    # Modern py.exe can provision a runtime merely while probing it. Discovery
    # must never install software before the user selects the installation item.
    $automatic = [Environment]::GetEnvironmentVariable('PYTHON_MANAGER_AUTOMATIC_INSTALL', 'Process')
    $allow = [Environment]::GetEnvironmentVariable('PYLAUNCHER_ALLOW_INSTALL', 'Process')
    $always = [Environment]::GetEnvironmentVariable('PYLAUNCHER_ALWAYS_INSTALL', 'Process')
    try {
        [Environment]::SetEnvironmentVariable('PYTHON_MANAGER_AUTOMATIC_INSTALL', 'false', 'Process')
        [Environment]::SetEnvironmentVariable('PYLAUNCHER_ALLOW_INSTALL', $null, 'Process')
        [Environment]::SetEnvironmentVariable('PYLAUNCHER_ALWAYS_INSTALL', $null, 'Process')
        $candidates = [System.Collections.Generic.List[object]]::new()
        foreach ($name in @('py.exe', 'python.exe')) {
            $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($command -and $command.Source -notlike '*\Microsoft\WindowsApps\python.exe') {
                $prefix = @()
                if ($name -eq 'py.exe') { $prefix = @('-3.12') }
                $candidates.Add(@{Path = $command.Source; Prefix = $prefix})
            }
        }
        foreach ($key in @('HKCU:\SOFTWARE\Python\PythonCore\3.12\InstallPath',
                            'HKLM:\SOFTWARE\Python\PythonCore\3.12\InstallPath',
                            'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore\3.12\InstallPath')) {
            $registry = Get-Item -LiteralPath $key -ErrorAction SilentlyContinue
            if ($registry) {
                $executable = $registry.GetValue('ExecutablePath')
                if (-not $executable) {
                    $directory = $registry.GetValue('')
                    if ($directory) { $executable = Join-Path $directory 'python.exe' }
                }
                if ($executable) { $candidates.Add(@{Path = $executable; Prefix = @()}) }
            }
        }
        if ($env:LOCALAPPDATA) {
            $candidates.Add(@{Path = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'); Prefix = @()})
        }
        foreach ($candidate in $candidates) {
            if (-not (Test-Path -LiteralPath $candidate.Path -PathType Leaf)) { continue }
            try {
                $prefix = $candidate.Prefix
                $global:LASTEXITCODE = 1
                $found = @(& $candidate.Path @prefix -I -X utf8 -c "import sys,struct,venv;print(sys.executable);sys.exit(sys.version_info[:2]!=(3,12) or struct.calcsize('P')!=8)" 2>$null)
                if ($LASTEXITCODE -eq 0 -and $found.Count -eq 1 -and
                    (Test-Path -LiteralPath ([string]$found[0]) -PathType Leaf)) {
                    return [string]$found[0]
                }
            } catch { }
        }
        return $null
    } finally {
        [Environment]::SetEnvironmentVariable('PYTHON_MANAGER_AUTOMATIC_INSTALL', $automatic, 'Process')
        [Environment]::SetEnvironmentVariable('PYLAUNCHER_ALLOW_INSTALL', $allow, 'Process')
        [Environment]::SetEnvironmentVariable('PYLAUNCHER_ALWAYS_INSTALL', $always, 'Process')
    }
}

function Find-YeboyWinget {
    $command = Get-Command 'winget.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
    return $null
}

function Invoke-YeboyWinget {
    param([Parameter(Mandatory)][string]$Executable)
    # Official catalog; WinGet validates its installer hash. Never bypass it.
    $arguments = @('install', '--id', 'Python.Python.3.12', '--exact', '--source', 'winget',
                   '--scope', 'user', '--architecture', 'x64', '--silent',
                   '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity')
    try {
        $global:LASTEXITCODE = 1
        & $Executable @arguments *> $null
        return [int]$LASTEXITCODE
    } catch { return 1 }
}

function Invoke-YeboyBootstrap {
    param([Parameter(Mandatory)][string]$Python, [switch]$Repair)
    $arguments = @('-I', '-X', 'utf8', '-c', $script:YeboyModuleCode, $script:YeboyRoot, 'tools.launcher.bootstrap')
    if ($Repair) { $arguments += '--repair' }
    try {
        $global:LASTEXITCODE = 1
        & $Python @arguments | Out-Host
        return [int]$LASTEXITCODE
    } catch {
        Write-Host 'Не вдалося запустити встановлення бібліотек. Перевірте доступ до папки проєкту.'
        return 1
    }
}

function Test-YeboyEnvironment {
    $directory = Join-Path $script:YeboyRoot '.venv-v2'
    $python = Join-Path $directory 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { return $false }
    if ((Get-Item -LiteralPath $directory).Attributes -band [IO.FileAttributes]::ReparsePoint) { return $false }
    # Reuse the existing isolation/path checks before importing runtime packages.
    $check = "import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from tools.launcher.bootstrap import validate_environment;validate_environment(Path(sys.argv[1]));import discord,sqlalchemy,aiosqlite,asyncpg,dotenv"
    try {
        $global:LASTEXITCODE = 1
        & $python -I -X utf8 -c $check $script:YeboyRoot *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Start-YeboyMenu {
    $python = Join-Path $script:YeboyRoot '.venv-v2\Scripts\python.exe'
    try {
        $global:LASTEXITCODE = 1
        & $python -I -X utf8 -c $script:YeboyModuleCode $script:YeboyRoot 'tools.launcher' | Out-Host
        return [int]$LASTEXITCODE
    } catch {
        Write-Host 'Панель керування не запустилася. Повторіть встановлення через головний CMD.'
        return 1
    }
}

function Install-YeboyCore {
    param([switch]$AllowPythonInstall, [switch]$Repair)
    Write-Host '[1/3] Перевірка Python 3.12 x64...'
    $python = Find-YeboyPython
    if (-not $python) {
        if (-not $AllowPythonInstall) {
            Write-Host 'Потрібен Python 3.12 x64. Відкрийте YEBOYBot.cmd і виберіть [1] встановлення.'
            Write-Host 'Офіційний сайт: https://www.python.org/downloads/'
            return 1
        }
        $winget = Find-YeboyWinget
        if (-not $winget) {
            Write-Host 'Windows App Installer (winget) не знайдено.'
            Write-Host 'Встановіть App Installer від Microsoft: https://apps.microsoft.com/detail/9NBLGGH4NNS1'
            Write-Host 'Або Python 3.12 x64: https://www.python.org/downloads/windows/'
            Write-Host 'Після встановлення повторно відкрийте YEBOYBot.cmd. Дані бота збережено.'
            return 1
        }
        Write-Host 'Встановлення Python для поточного користувача через офіційний каталог WinGet...'
        $installed = Invoke-YeboyWinget -Executable $winget
        if ($installed -ne 0) {
            Write-Host ('Встановлення Python не підтверджено (код {0}). Перевірте інтернет, App Installer і правила Windows; повторіть пізніше.' -f $installed)
            return 1
        }
        # A running console retains its old PATH. Probe registry/default paths too.
        $python = Find-YeboyPython
        if (-not $python) {
            Write-Host 'Python 3.12 x64 після встановлення не підтверджено. Повторно відкрийте YEBOYBot.cmd.'
            return 1
        }
    }
    Write-Host '[2/3] Окреме середовище й усі бібліотеки Core V2...'
    $prepared = Invoke-YeboyBootstrap -Python $python -Repair:$Repair
    if ($prepared -ne 0) { return $prepared }
    Write-Host '[3/3] Перевірка середовища та критичних бібліотек...'
    if (-not (Test-YeboyEnvironment)) {
        Write-Host 'Перевірка бібліотек не пройдена. Встановлення не позначено завершеним; повторіть [1].'
        return 1
    }
    Write-Host 'ГОТОВО: Python і бібліотеки Core V2 перевірені.'
    Write-Host 'Далі у панелі: [12] налаштування Discord, потім [1] запуск бота.'
    return 0
}

function Show-YeboySetup {
    while ($true) {
        Write-Host ''
        Write-Host '========================================================='
        Write-Host '         YEBOYBot — ПОЧАТКОВЕ ВСТАНОВЛЕННЯ'
        Write-Host '========================================================='
        Write-Host '[1] Встановити все для Core V2 / відновити бібліотеки'
        Write-Host '[0] Вихід'
        Write-Host ''
        Write-Host '[1] дозволяє встановити Python 3.12 x64 для цього користувача,'
        Write-Host 'якщо його немає, через WinGet з погодженням умов Python і каталогу.'
        Write-Host 'Потім буде підготовлено .venv-v2 з бібліотеками бота.'
        Write-Host 'Потрібні інтернет і дозвіл Windows на встановлення програм.'
        $choice = Read-Host 'Виберіть дію (Enter — вихід)'
        if ([string]::IsNullOrWhiteSpace($choice) -or $choice.Trim() -eq '0') { return 0 }
        if ($choice.Trim() -ne '1') {
            Write-Host 'Оберіть [1] для встановлення або [0] для виходу.'
            continue
        }
        $result = Install-YeboyCore -AllowPythonInstall -Repair
        if ($result -eq 0) { return 0 }
        Write-Host 'Встановлення не завершено. Можна повторити [1] або вийти [0].'
    }
}

function Invoke-YeboyEntry {
    param([ValidateSet('Launcher', 'Setup', 'Ensure', 'Repair')][string]$Mode)
    if ($Mode -eq 'Ensure') { return (Install-YeboyCore) }
    if ($Mode -eq 'Repair') { return (Install-YeboyCore -Repair) }
    if ($Mode -eq 'Setup') { return (Show-YeboySetup) }
    if (-not (Test-YeboyEnvironment)) {
        $setup = Show-YeboySetup
        if ($setup -ne 0) { return $setup }
        if (-not (Test-YeboyEnvironment)) { return 0 } # Cancelled before installation.
    }
    while ($true) {
        $result = Start-YeboyMenu
        if ($result -eq 25) {
            $repair = Install-YeboyCore -Repair
            if ($repair -ne 0) {
                Write-Host 'Оновлення бібліотек не завершене. Зупиніть бота через [3] і повторіть [5].'
            }
            if (-not (Test-YeboyEnvironment)) { return 1 }
            continue
        }
        if ($result -ne 24) { return $result }
        # The Python menu has exited, so Windows can replace loaded .pyd files.
        $setup = Show-YeboySetup
        if ($setup -ne 0) { return $setup }
        if (-not (Test-YeboyEnvironment)) { return 1 }
    }
}

if (-not $LibraryOnly) {
    $ErrorActionPreference = 'Stop'
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    $OutputEncoding = [Console]::OutputEncoding
    $env:PYTHONUTF8 = '1'
    try {
        Push-Location -LiteralPath $script:YeboyRoot
        try { $result = Invoke-YeboyEntry -Mode $Mode } finally { Pop-Location }
        exit $result
    } catch {
        Write-Host 'Операцію не завершено. Перевірте доступ до файлів, App Installer та правила Windows.'
        Write-Host 'Токен і дані бота не видаляються. Повторіть запуск YEBOYBot.cmd.'
        exit 1
    }
}
