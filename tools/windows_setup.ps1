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
                            'HKCU:\SOFTWARE\Python\PythonCore\3.12-64\InstallPath',
                            'HKLM:\SOFTWARE\Python\PythonCore\3.12\InstallPath',
                            'HKLM:\SOFTWARE\Python\PythonCore\3.12-64\InstallPath',
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
                $found = @(& $candidate.Path @prefix -I -X utf8 -c "import sys,struct,venv,ensurepip;print(sys.executable);sys.exit(sys.version_info[:2]!=(3,12) or struct.calcsize('P')!=8)" 2>$null)
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

function Invoke-YeboyMedia {
    $python = Join-Path $script:YeboyRoot '.venv-v2\Scripts\python.exe'
    try {
        $global:LASTEXITCODE = 1
        & $python -I -X utf8 -c $script:YeboyModuleCode $script:YeboyRoot 'tools.launcher.media' --prepare | Out-Host
        return [int]$LASTEXITCODE
    } catch {
        Write-Host 'Не вдалося підготувати FFmpeg і Deno. Зупиніть бота та перевірте доступ до папки й мережі.'
        return 1
    }
}

function Test-YeboyEnvironment {
    $directory = Join-Path $script:YeboyRoot '.venv-v2'
    $python = Join-Path $directory 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { return $false }
    if ((Get-Item -LiteralPath $directory).Attributes -band [IO.FileAttributes]::ReparsePoint) { return $false }
    # Reuse the existing isolation/path checks before importing runtime packages.
    $check = "import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from tools.launcher.bootstrap import validate_environment,IMPORT_CHECK;validate_environment(Path(sys.argv[1]));exec(IMPORT_CHECK);from tools.launcher.media import check_enabled_music;check_enabled_music(Path(sys.argv[1]))"
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

function Invoke-YeboyUpdateWorker {
    param(
        [Parameter(Mandatory)][string]$Python,
        [Parameter(Mandatory)][string]$Snapshot,
        [Parameter(Mandatory)][ValidateSet('apply', 'rollback', 'confirm')][string]$Action
    )
    try {
        $global:LASTEXITCODE = 1
        & $Python -I -X utf8 $Snapshot $Action $script:YeboyRoot | Out-Host
        return [int]$LASTEXITCODE
    } catch {
        Write-Host 'Не вдалося запустити зовнішній updater. Перевірте файли в data\\v2.'
        return 1
    }
}

function Test-YeboyAfterUpdate {
    $python = Join-Path $script:YeboyRoot '.venv-v2\Scripts\python.exe'
    try {
        $global:LASTEXITCODE = 1
        & $python -I -X utf8 -c $script:YeboyModuleCode $script:YeboyRoot 'tools.launcher.verify_update' | Out-Host
        return [int]$LASTEXITCODE
    } catch {
        Write-Host 'Перевірка нової версії не запустилася.'
        return 1
    }
}

function Start-YeboyAfterUpdate {
    $python = Join-Path $script:YeboyRoot '.venv-v2\Scripts\python.exe'
    $code = "import sys;sys.path.insert(0,sys.argv[1]);from tools.launcher.process import start;print(start()['status'])"
    try {
        $global:LASTEXITCODE = 1
        & $python -I -X utf8 -c $code $script:YeboyRoot | Out-Host
        return [int]$LASTEXITCODE
    } catch {
        Write-Host 'Нову версію перевірено, але бот не стартував. Скористайтеся [1] меню.'
        return 1
    }
}

function Invoke-YeboyGitHubUpdate {
    $python = Join-Path $script:YeboyRoot '.venv-v2\Scripts\python.exe'
    $source = Join-Path $script:YeboyRoot 'tools\update_worker.py'
    $directory = Join-Path $script:YeboyRoot 'data\v2'
    $planFile = Join-Path $directory 'update-plan.json'
    if (-not (Test-Path -LiteralPath $source -PathType Leaf) -or
        -not (Test-Path -LiteralPath $planFile -PathType Leaf)) {
        Write-Host 'Не знайдено повного комплекту або плану оновлення GitHub.'
        return 1
    }
    $wasRunning = $false
    try {
        $plan = Get-Content -LiteralPath $planFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $wasRunning = [bool]$plan.was_running
    } catch {
        Write-Host 'План оновлення пошкоджений; автоматична заміна заблокована.'
        return 1
    }

    # This saved stdlib-only worker is not overwritten when the GitHub branch
    # updates tools/, YEBOYBot.cmd or windows_setup.ps1.
    $snapshot = Join-Path $directory ('updater-worker-' + [guid]::NewGuid().ToString('N') + '.py')
    try {
        Copy-Item -LiteralPath $source -Destination $snapshot -ErrorAction Stop
    } catch {
        Write-Host 'Неможливо створити захищену копію інструмента оновлення.'
        return 1
    }

    $ok = $false
    $recovered = $false
    try {
        Write-Host ''
        Write-Host '════════════════════════════════════════════════════════════════════════'
        Write-Host '  YEBOYBot / БЕЗПЕЧНЕ ОНОВЛЕННЯ GITHUB'
        Write-Host '════════════════════════════════════════════════════════════════════════'
        Write-Host '[1/4] Перевірка джерела та встановлення коду...'
        if ((Invoke-YeboyUpdateWorker -Python $python -Snapshot $snapshot -Action 'apply') -eq 0) {
            Write-Host '[2/4] Перевстановлення або перевірка потрібних залежностей...'
            if ((Install-YeboyCore -Repair) -eq 0) {
                Write-Host '[3/4] Перевірка нової конфігурації та схеми без Discord login...'
                if ((Test-YeboyAfterUpdate) -eq 0) {
                    Write-Host '[4/4] Підтвердження встановленої версії та коміту...'
                    if ((Invoke-YeboyUpdateWorker -Python $python -Snapshot $snapshot -Action 'confirm') -eq 0) {
                        $ok = $true
                    }
                }
            }
        }

        if (-not $ok) {
            Write-Host ''
            Write-Host 'Оновлення не підтверджено. Відновлюємо попередній код...'
            $rollback = Invoke-YeboyUpdateWorker -Python $python -Snapshot $snapshot -Action 'rollback'
            if ($rollback -eq 0) {
                $recovered = $true
                Write-Host 'Перевірка бібліотек після повернення до попередньої версії...'
                if ((Install-YeboyCore -Repair) -ne 0) {
                    $recovered = $false
                    Write-Host 'Бібліотеки не відновлено. Залиште бот вимкненим і перевірте журнал.'
                }
            } else {
                Write-Host 'Відновлення не підтверджене; копія updater і резерви збережені.'
            }
            if ($recovered -and $wasRunning) {
                [void](Start-YeboyAfterUpdate)
            }
            # Code/deps restored => safe to return to menu. Otherwise halt.
            if ($recovered) { return 2 }
            return 1
        }

        Write-Host 'Оновлення успішне. Програмні файли, залежності та конфігурацію перевірено.'
        if ($wasRunning) {
            Write-Host 'Повертаємо бота до стану запуску...'
            [void](Start-YeboyAfterUpdate)
        }
        return 0
    } finally {
        # On unconfirmed rollback keep the original worker snapshot for recovery.
        if ($ok -or $recovered) {
            Remove-Item -LiteralPath $snapshot -Force -ErrorAction SilentlyContinue
        }
    }
}

function Install-YeboyCore {
    param([switch]$AllowPythonInstall, [switch]$Repair)
    Write-Host '[1/4] Перевірка Python 3.12 x64...'
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
    Write-Host ('Знайдено сумісний Python 3.12 x64: {0}' -f $python)
    Write-Host '[2/4] Окреме середовище й усі бібліотеки Core V2...'
    $prepared = Invoke-YeboyBootstrap -Python $python -Repair:$Repair
    if ($prepared -ne 0) { return $prepared }
    Write-Host '[3/4] Локальні FFmpeg і Deno: перевірка та підготовка...'
    $media = Invoke-YeboyMedia
    if ($media -ne 0) { return $media }
    Write-Host '[4/4] Перевірка середовища та критичних бібліотек...'
    if (-not (Test-YeboyEnvironment)) {
        Write-Host 'Перевірка бібліотек не пройдена. Для відновлення виконайте в папці бота:'
        Write-Host 'powershell -NoProfile -ExecutionPolicy Bypass -File tools\windows_setup.ps1 -Mode Repair'
        return 1
    }
    Write-Host 'ГОТОВО: Python, бібліотеки Core V2, FFmpeg і Deno перевірені.'
    Write-Host 'Далі у панелі: [12] налаштування Discord, потім [1] запуск бота.'
    return 0
}

function Show-YeboySetup {
    while ($true) {
        Write-Host ''
        Write-Host '════════════════════════════════════════════════════════════════════════'
        Write-Host '  YEBOYBot  /  ПОЧАТКОВЕ ВСТАНОВЛЕННЯ'
        Write-Host '  Підготуємо все необхідне для першого запуску.'
        Write-Host '════════════════════════════════════════════════════════════════════════'
        Write-Host '[0] Закрити вікно без встановлення'
        Write-Host '[1] Перевірити й встановити потрібне для Core V2'
        Write-Host ''
        Write-Host '[1] дозволяє встановити Python 3.12 x64 для цього користувача,'
        Write-Host 'якщо його немає, через WinGet з погодженням умов Python і каталогу.'
        Write-Host 'Наявний сумісний Python і перевірені бібліотеки буде використано повторно.'
        Write-Host 'Далі: бібліотеки бота, FFmpeg і Deno у папці bin (без зміни PATH).'
        Write-Host 'Архіви програм завантажуються через HTTPS із перевіркою SHA-256.'
        Write-Host 'Потрібні інтернет і дозвіл Windows на встановлення програм.'
        $choice = Read-Host 'Номер дії (Enter — вихід)'
        if ([string]::IsNullOrWhiteSpace($choice) -or $choice.Trim() -eq '0') { return 0 }
        if ($choice.Trim() -ne '1') {
            Write-Host 'Оберіть [1] для встановлення або [0] для виходу.'
            continue
        }
        $result = Install-YeboyCore -AllowPythonInstall
        if ($result -eq 0) { return 0 }
        Write-Host ''
        Write-Host ('Встановлення зупинено (код {0}). Причина наведена вище.' -f $result)
        Write-Host 'Усуньте вказану причину й відкрийте YEBOYBot.cmd знову.'
        Write-Host 'Повторення без змін не допоможе; конфігурація та дані бота збережені.'
        return $result
    }
}

function Restore-YeboyInterruptedUpdate {
    $directory = Join-Path $script:YeboyRoot 'data\v2'
    $plan = Join-Path $directory 'update-plan.json'
    $transaction = Join-Path $directory 'update-transaction.json'
    if (-not (Test-Path -LiteralPath $plan -PathType Leaf) -and
        -not (Test-Path -LiteralPath $transaction -PathType Leaf)) {
        return 0
    }
    Write-Host ''
    Write-Host '════════════════════════════════════════════════════════════════════════'
    Write-Host '  YEBOYBot / ВИЯВЛЕНО НЕЗАВЕРШЕНЕ ОНОВЛЕННЯ'
    Write-Host '════════════════════════════════════════════════════════════════════════'
    Write-Host 'Можлива причина: вимкнення ПК або закриття вікна під час оновлення.'
    Write-Host 'Не видаляйте data, backups, .env.v2 та збережений зовнішній worker.'
    if ((Read-Host 'Для повернення попередньої версії введіть ВІДНОВИТИ').Trim() -ne 'ВІДНОВИТИ') {
        Write-Host 'Оновлення не продовжено. Спершу відновіть незавершену транзакцію.'
        return 1
    }
    if (-not (Test-Path -LiteralPath $transaction -PathType Leaf)) {
        # Neither ZIP nor Git has changed code if no transaction was recorded.
        if (Test-Path -LiteralPath $plan -PathType Leaf) {
            Remove-Item -LiteralPath $plan -Force -ErrorAction Stop
        }
        Write-Host 'Незастосований план скасовано; програмні файли не змінювалися.'
        return 0
    }
    $snapshot = Get-ChildItem -LiteralPath $directory -Filter 'updater-worker-*.py' -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    if (-not $snapshot) {
        Write-Host 'Не знайдено збереженого updater-worker. Автоматичний rollback небезпечний.'
        return 1
    }
    $python = Join-Path $script:YeboyRoot '.venv-v2\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $python = Find-YeboyPython
    }
    if (-not $python) {
        Write-Host 'Python 3.12 недоступний. Відновіть його перед повторенням.'
        return 1
    }
    if ((Invoke-YeboyUpdateWorker -Python $python -Snapshot $snapshot.FullName -Action 'rollback') -ne 0) {
        Write-Host 'Rollback не підтверджений. Код та дані залишено для діагностики.'
        return 1
    }
    if ((Install-YeboyCore -Repair) -ne 0) {
        Write-Host 'Код відновлено, але перевірка бібліотек не пройдена.'
        return 1
    }
    Remove-Item -LiteralPath $snapshot.FullName -Force -ErrorAction SilentlyContinue
    Write-Host 'Попередню версію відновлено. Можна повернутися до головного меню.'
    return 0
}

function Invoke-YeboyEntry {
    param([ValidateSet('Launcher', 'Setup', 'Ensure', 'Repair')][string]$Mode)
    if ($Mode -eq 'Ensure') { return (Install-YeboyCore) }
    if ($Mode -eq 'Repair') { return (Install-YeboyCore -Repair) }
    if ($Mode -eq 'Setup') { return (Show-YeboySetup) }
    $recovery = Restore-YeboyInterruptedUpdate
    if ($recovery -ne 0) { return $recovery }
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
        if ($result -eq 26) {
            # The original menu has exited: GitHub may now update even this script.
            $updated = Invoke-YeboyGitHubUpdate
            if (-not (Test-YeboyEnvironment)) {
                Write-Host 'Середовище не пройшло перевірку після оновлення. Перевірте recovery і журнал.'
                return 1
            }
            if ($updated -eq 1) {
                Write-Host 'Оновлення або rollback не підтверджені. Панель зупинено.'
                return 1
            }
            if ($updated -eq 2) {
                Write-Host 'Оновлення скасовано; попередню версію відновлено.'
            }
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
