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

# First-run safety boundary: fail before Python/WinGet/downloads for incomplete ZIPs.
$script:YeboyInstallStage = 'launcher'
$script:YeboyRequiredSources = @(
    'YEBOYBot.cmd',
    '.env.v2.example',
    'core_v2\requirements.txt',
    'core_v2\config.py',
    'core_v2\runtime.py',
    'core_v2\media_environment.py',
    'config\example_options.ini',
    'tools\installer_policy.py',
    'tools\portable_media.py',
    'tools\launcher\bootstrap_entry.py',
    'tools\launcher\bootstrap.py',
    'tools\launcher\media.py',
    'tools\launcher\system_media.py',
    'tools\launcher\__main__.py'
)

function Test-YeboySourceBundle {
    $missing = @()
    foreach ($relative in $script:YeboyRequiredSources) {
        $path = Join-Path $script:YeboyRoot $relative
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            $missing += $relative
        }
    }
    if ($missing.Count -eq 0) { return $true }
    Write-Host '[ПОМИЛКА] Архів YEBOYBot розпаковано не повністю. Відсутні файли:'
    foreach ($relative in $missing) { Write-Host ('  - {0}' -f $relative) }
    Write-Host 'Розпакуйте повний ZIP робочої гілки в нову папку. Збережіть власні .env.v2, data та backups.'
    Write-Host 'Завантаження і встановлення системних програм не починалося.'
    return $false
}

function Test-YeboyReportPath {
    param([string]$Path)
    foreach ($entry in @((Join-Path $script:YeboyRoot 'data'),
            (Join-Path $script:YeboyRoot 'data\v2'), $Path)) {
        $item = Get-Item -LiteralPath $entry -Force -ErrorAction SilentlyContinue
        if ($null -ne $item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            return $false
        }
    }
    return $true
}

function Test-YeboyFreshVenvDeniedReport {
    $path = Join-Path $script:YeboyRoot 'data\v2\install-report.json'
    try {
        if ($script:YeboyAttemptId -cnotmatch '^[a-f0-9]{32}$' -or
            $null -eq $script:YeboyBootstrapStarted -or -not (Test-YeboyReportPath $path)) { return $false }
        $file = Get-Item -LiteralPath $path -Force -ErrorAction Stop
        if ($file.PSIsContainer -or $file.Length -gt 16384) { return $false }
        $data = Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $stamp = [DateTimeOffset]::Parse([string]$data.time_utc,
            [Globalization.CultureInfo]::InvariantCulture)
        return ($data.schema -eq 2 -and $data.status -ceq 'failed' -and
            $data.attempt_id -ceq $script:YeboyAttemptId -and
            $stamp -ge $script:YeboyBootstrapStarted -and $stamp -le [DateTimeOffset]::UtcNow -and
            $data.stage -ceq 'environment-probe' -and
            $data.category -ceq 'access-denied' -and $data.winerror -eq 5)
    } catch { return $false }
}

function Get-YeboyVenvBlockEvents {
    # Use named event payload fields. A process name in the display message
    # may describe the caller or an unrelated installation on the same PC.
    if ($null -eq $script:YeboyBootstrapStarted) { return }
    $target = Join-Path $script:YeboyRoot '.venv-v2\Scripts\python.exe'
    $sources = @(
        @{ Log = 'Microsoft-Windows-CodeIntegrity/Operational'; Id = 3077; Code = 'CodeIntegrity:3077'; Field = 'FileName' },
        @{ Log = 'Microsoft-Windows-AppLocker/EXE and DLL'; Id = 8004; Code = 'AppLocker:8004'; Field = 'FilePath' },
        @{ Log = 'Microsoft-Windows-Windows Defender/Operational'; Id = 1121; Code = 'Defender:1121'; Field = 'Path' }
    )
    foreach ($source in $sources) {
        try {
            $filter = @{ LogName = [string]$source.Log; Id = [int]$source.Id
                StartTime = $script:YeboyBootstrapStarted.LocalDateTime; EndTime = [DateTime]::Now }
            $events = @(Get-WinEvent -FilterHashtable $filter -MaxEvents 30 -ErrorAction Stop)
            foreach ($event in $events) {
                try {
                    [xml]$xml = $event.ToXml()
                    foreach ($field in $xml.SelectNodes("//*[local-name()='EventData']/*[local-name()='Data']")) {
                        if ($field.GetAttribute('Name') -ieq $source.Field -and
                            $field.InnerText -ieq $target) {
                            Write-Output ([string]$source.Code)
                            break
                        }
                    }
                } catch { continue }
            }
        } catch { continue }
    }
}

function Write-YeboyLauncherReport {
    param(
        [Parameter(Mandatory)][ValidateSet('source-bundle', 'python', 'media-probe',
            'bootstrap', 'media', 'readiness', 'launcher')][string]$Stage,
        [Parameter(Mandatory)][int]$ExitCode,
        [string[]]$EventCodes = @()
    )
    # Persist only a controlled stage, process code, and timestamp. Never
    # serialize exceptions, stdout, user paths, private configuration or tokens.
    $directory = Join-Path $script:YeboyRoot 'data\v2'
    $destination = Join-Path $directory 'launcher-report.json'
    $temporary = $null
    try {
        if (-not (Test-YeboyReportPath $destination)) { throw 'Unsafe report path' }
        [void][IO.Directory]::CreateDirectory($directory)
        $allowed = @('CodeIntegrity:3077', 'AppLocker:8004', 'Defender:1121')
        $safeEvents = @($EventCodes | Where-Object { $allowed -ccontains $_ } | Select-Object -Unique)
        $report = [ordered]@{
            schema = 2
            attempt_id = $script:YeboyAttemptId
            timestamp_utc = [DateTime]::UtcNow.ToString('o')
            status = 'failed'
            stage = $Stage
            exit_code = $ExitCode
            matched_security_events = $safeEvents
        } | ConvertTo-Json -Compress
        $temporary = Join-Path $directory ('launcher-report-' + [guid]::NewGuid().ToString('N') + '.tmp')
        [IO.File]::WriteAllText($temporary, $report + "`n", [Text.UTF8Encoding]::new($false))
        # File.Replace is not reliably supported for this diagnostic file
        # on every Windows filesystem; Move-Item -Force works for both first
        # create and overwrite, while never following a checked reparse target.
        Move-Item -LiteralPath $temporary -Destination $destination -Force -ErrorAction Stop
        Write-Host 'Безпечний звіт запуску: data/v2/launcher-report.json (без токена, шляхів і сирого виводу).'
    } catch {
        Write-Host 'Звіт запуску не вдалося записати. Скопіюйте номер етапу й код помилки з консолі.'
    } finally {
        if ($null -ne $temporary -and (Test-Path -LiteralPath $temporary)) {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
    }
}

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

function Invoke-YeboySystemMediaProbe {
    param([Parameter(Mandatory)][string]$Python)
    # Pure standard-library probe: no pip, downloads, installer, or configuration edits.
    $arguments = @('-I', '-X', 'utf8', '-c', $script:YeboyModuleCode,
                   $script:YeboyRoot, 'tools.launcher.system_media', '--json')
    try {
        $global:LASTEXITCODE = 1
        $lines = @(& $Python @arguments 2>$null)
        if ($LASTEXITCODE -ne 0 -or $lines.Count -ne 1) { return $null }
        return (ConvertFrom-Json -InputObject ([string]$lines[0]) -ErrorAction Stop)
    } catch { return $null }
}

function Select-YeboyMedia {
    param([Parameter(Mandatory)][string]$Python)
    Write-Host ''
    Write-Host '════════════════════════════════════════════════════════════════════════'
    Write-Host '  YEBOYBot / ПЕРЕВІРКА НАЯВНИХ ПРОГРАМ НА ПК'
    Write-Host '════════════════════════════════════════════════════════════════════════'
    Write-Host 'Пошук FFmpeg та Deno у PATH і типових каталогах (без встановлення).'
    $report = Invoke-YeboySystemMediaProbe -Python $Python
    if ($null -eq $report -or $null -eq $report.ffmpeg -or $null -eq $report.deno) {
        Write-Host 'Не вдалося перевірити зовнішні програми. Встановлення зупинено без змін.'
        return $null
    }
    $selected = @{ ffmpeg = ''; deno = ''; cancelled = $false }
    foreach ($name in @('ffmpeg', 'deno')) {
        $label = if ($name -eq 'ffmpeg') { 'FFmpeg' } else { 'Deno' }
        $tool = $report.$name
        Write-Host ''
        if (-not $tool.found) {
            Write-Host ('[!] {0}: сумісної зовнішньої копії не знайдено.' -f $label)
            Write-Host ('    План: локальний комплект у bin\{0}.' -f $name)
            continue
        }
        Write-Host ('[?] {0}: {1}' -f $label, $tool.path)
        if (-not $tool.compatible) {
            Write-Host ('[!] Версія не підтверджена або нижча від мінімальної. Локальна копія буде перевірена чи встановлена.')
            continue
        }
        Write-Host ('[OK] Перевірена версія: {0}. Можна використовувати зовнішню програму.' -f $tool.version)
        while ($true) {
            Write-Host ('  [1] ТАК — використовувати наявний {0} без копіювання в папку бота' -f $label)
            Write-Host ('  [2] НІ — використати/встановити окремий {0} у папку бота' -f $label)
            Write-Host '  [0] Скасувати підготовку'
            $choice = Read-Host ('Джерело {0}' -f $label)
            if ([string]::IsNullOrWhiteSpace($choice) -or $choice.Trim() -eq '0') {
                $selected.cancelled = $true
                return $selected
            }
            if ($choice.Trim() -eq '1') {
                $selected[$name] = [string]$tool.path
                break
            }
            if ($choice.Trim() -eq '2') { break }
            Write-Host 'Виберіть 1, 2 або 0.'
        }
    }
    Write-Host ''
    Write-Host 'Бібліотеки Python залишаються в ізольованій .venv-v2 навіть при виборі зовнішніх програм.'
    return $selected
}

function Find-YeboyWinget {
    $command = Get-Command 'winget.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
    return $null
}

function Confirm-YeboyPackageInstall {
    param([string]$Package)
    Write-Host ('[i] WinGet встановить {0} для поточного користувача з офіційного каталогу.' -f $Package)
    $choice = Read-Host 'Погоджуєтеся з умовами пакета й каталогу та дозволяєте встановлення? [1] Так, Enter — ні'
    return ($null -ne $choice -and $choice.Trim() -eq '1')
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

function Invoke-YeboyWingetMedia {
    param([Parameter(Mandatory)][ValidateSet('ffmpeg', 'deno')][string]$Name)
    $winget = Find-YeboyWinget
    if (-not $winget) {
        Write-Host '[!] WinGet не знайдено. Не можна автоматично встановити системну програму.'
        return 1
    }
    $package = if ($Name -eq 'ffmpeg') { 'Gyan.FFmpeg' } else { 'DenoLand.Deno' }
    Write-Host ('[!] Windows відмовила у запуску локального {0} (WinError 5). Причина не підтверджена.' -f $Name)
    if (-not (Confirm-YeboyPackageInstall -Package $package)) { return 2 }
    # Keep official source/hash/terms and per-user scope.
    $arguments = @('install', '--id', $package, '--exact', '--source', 'winget',
                   '--scope', 'user', '--architecture', 'x64', '--silent',
                   '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity')
    try {
        $global:LASTEXITCODE = 1
        & $winget @arguments | Out-Host
        return [int]$LASTEXITCODE
    } catch {
        Write-Host '[!] WinGet не вдалося запустити. Наявні файли й конфігурація збережені.'
        return 1
    }
}

function Invoke-YeboyBootstrap {
    param([Parameter(Mandatory)][string]$Python, [switch]$Repair)
    # Invoke a real .py entry point, never pass python.exe as a script argument.
    # The prior inline -c trampoline could hand a binary to Python as source on
    # some Windows installations; the absolute entry path is unambiguous.
    $entry = Join-Path $script:YeboyRoot 'tools\launcher\bootstrap_entry.py'
    if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) {
        Write-Host 'Відсутній tools\launcher\bootstrap_entry.py. Повторно розпакуйте повний ZIP бота.'
        return 1
    }
    $arguments = @('-I', '-X', 'utf8', $entry)
    if ($Repair) { $arguments += '--repair' }
    if (-not $script:YeboyAttemptId) { $script:YeboyAttemptId = [guid]::NewGuid().ToString('N') }
    $script:YeboyBootstrapStarted = [DateTimeOffset]::UtcNow
    $previousAttempt = $env:YEBOY_INSTALL_ATTEMPT
    try {
        $env:YEBOY_INSTALL_ATTEMPT = $script:YeboyAttemptId
        $global:LASTEXITCODE = 1
        & $Python @arguments | Out-Host
        return [int]$LASTEXITCODE
    } catch {
        Write-Host 'Не вдалося запустити встановлення бібліотек. Перевірте доступ до папки проєкту.'
        return 1
    } finally {
        $env:YEBOY_INSTALL_ATTEMPT = $previousAttempt
    }
}

function Invoke-YeboyMedia {
    param([switch]$SelectMedia, [string]$SystemFfmpeg = '', [string]$SystemDeno = '')
    $python = Join-Path $script:YeboyRoot '.venv-v2\Scripts\python.exe'
    $arguments = @('-I', '-X', 'utf8', '-c', $script:YeboyModuleCode,
                   $script:YeboyRoot, 'tools.launcher.media', '--prepare')
    if ($SelectMedia) {
        $arguments += '--select-media'
        if ($SystemFfmpeg) { $arguments += @('--system-ffmpeg', $SystemFfmpeg) }
        if ($SystemDeno) { $arguments += @('--system-deno', $SystemDeno) }
    }
    try {
        $global:LASTEXITCODE = 1
        & $python @arguments | Out-Host
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

# This process-owned handle spans apply, dependency changes, verification and
# recovery. Its release on process exit distinguishes a crash from a live updater.
# FileShare.None also prevents a second PowerShell window from recovering live work.
function Invoke-YeboyUpdateSession {
    param([Parameter(Mandatory)][ValidateSet('Update', 'Recover')][string]$Action)
    $directory = Join-Path $script:YeboyRoot 'data\v2'
    $lockPath = Join-Path $directory 'update-session.lock'
    $session = $null
    try {
        [System.IO.Directory]::CreateDirectory($directory) | Out-Null
        if ((Test-Path -LiteralPath $lockPath) -and
            ((Get-Item -LiteralPath $lockPath -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw 'Unsafe update lock'
        }
        $session = [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    } catch {
        Write-Host 'Інше вікно оновлює або відновлює бота. Дочекайтеся завершення операції.'
        return 1
    }
    $script:YeboyRestartAfterUpdate = $false
    try {
        if ($Action -eq 'Update') { $result = Invoke-YeboyGitHubUpdateCore }
        else { $result = Restore-YeboyInterruptedUpdateCore }
    } finally {
        $session.Dispose()
    }
    # Launch only after releasing the session lock and completing verification.
    if ($script:YeboyRestartAfterUpdate -and ($result -eq 0 -or $result -eq 2)) {
        [void](Start-YeboyAfterUpdate)
    }
    return $result
}

function Invoke-YeboyGitHubUpdate {
    return (Invoke-YeboyUpdateSession -Action 'Update')
}

function Restore-YeboyInterruptedUpdate {
    return (Invoke-YeboyUpdateSession -Action 'Recover')
}

function Complete-YeboyUpdateRepair {
    $marker = Join-Path $script:YeboyRoot 'data\v2\update-repair-required.json'
    Write-Host 'Перевірка бібліотек і конфігурації після повернення попереднього коду...'
    if ((Install-YeboyCore -Repair) -ne 0 -or (Test-YeboyAfterUpdate) -ne 0) {
        Write-Host 'Відновлення не підтверджене. Повторіть YEBOYBot.cmd; запуск бота заблоковано.'
        return 1
    }
    Remove-Item -LiteralPath $marker -Force -ErrorAction Stop
    return 0
}

function Invoke-YeboyGitHubUpdateCore {
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
            # rollback removes code transaction markers; retain this independent
            # gate through dependency repair and offline verification (also on crash).
            $repairMarker = Join-Path $directory 'update-repair-required.json'
            Set-Content -LiteralPath $repairMarker -Value '{"phase":"repair_required"}' -Encoding UTF8 -ErrorAction Stop
            $rollback = Invoke-YeboyUpdateWorker -Python $python -Snapshot $snapshot -Action 'rollback'
            if ($rollback -eq 0) {
                $recovered = ((Complete-YeboyUpdateRepair) -eq 0)
            } else {
                Write-Host 'Відновлення не підтверджене; копія updater і резерви збережені.'
            }
            if ($recovered -and $wasRunning) {
                $script:YeboyRestartAfterUpdate = $true
            }
            # Code/deps restored => safe to return to menu. Otherwise halt.
            if ($recovered) { return 2 }
            return 1
        }

        Write-Host 'Оновлення успішне. Програмні файли, залежності та конфігурацію перевірено.'
        if ($wasRunning) {
            Write-Host 'Повертаємо бота до стану запуску...'
            $script:YeboyRestartAfterUpdate = $true
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
    $script:YeboyInstallStage = 'python'
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
        if (-not (Confirm-YeboyPackageInstall -Package 'Python.Python.3.12')) {
            Write-Host '[i] Встановлення Python скасовано.'
            return 0
        }
        Write-Host 'Встановлення Python для поточного користувача через офіційний каталог WinGet...'
        $installed = Invoke-YeboyWinget -Executable $winget
        if ($installed -ne 0) {
            Write-Host ('Встановлення Python не підтверджено (код {0}). Перевірте інтернет, App Installer і правила Windows; повторіть пізніше.' -f $installed)
            return $installed
        }
        # A running console retains its old PATH. Probe registry/default paths too.
        $python = Find-YeboyPython
        if (-not $python) {
            Write-Host 'Python 3.12 x64 після встановлення не підтверджено. Повторно відкрийте YEBOYBot.cmd.'
            return 1
        }
    }
    Write-Host ('[OK] Сумісний Python 3.12 x64: {0}' -f $python)
    $mediaSelection = $null
    if ($script:YeboyInteractiveInstall) {
        $script:YeboyInstallStage = 'media-probe'
        $mediaSelection = Select-YeboyMedia -Python $python
        if ($null -eq $mediaSelection) { return 1 }
        if ($mediaSelection.cancelled) {
            Write-Host 'Встановлення скасовано до підготовки бібліотек. Наявні файли збережено.'
            return 0
        }
    }
    $script:YeboyInstallStage = 'bootstrap'
    Write-Host '[2/4] Окреме середовище й усі бібліотеки Core V2...'
    $prepared = Invoke-YeboyBootstrap -Python $python -Repair:$Repair
    if ($prepared -ne 0) { return $prepared }
    $script:YeboyInstallStage = 'media'
    Write-Host '[3/4] FFmpeg і Deno: вибрані зовнішні або локальні програми...'
    if ($null -ne $mediaSelection) {
        $media = Invoke-YeboyMedia -SelectMedia -SystemFfmpeg $mediaSelection.ffmpeg -SystemDeno $mediaSelection.deno
    } else {
        $media = Invoke-YeboyMedia
    }
    if ($script:YeboyInteractiveInstall -and $null -ne $mediaSelection) {
        # Codes 31/32 are emitted only for WinError 5 on executable launch,
        # never for hash mismatch or network failures. At most one fallback
        # per tool, never install WinGet packages during silent repair.
        $attempted = @{ ffmpeg = $false; deno = $false }
        while ($media -eq 31 -or $media -eq 32) {
            $name = if ($media -eq 31) { 'ffmpeg' } else { 'deno' }
            if ($attempted[$name]) { break }
            $attempted[$name] = $true
            $wingetCode = Invoke-YeboyWingetMedia -Name $name
            if ($wingetCode -eq 2) { break }
            # Even if WinGet returns 'already installed', resolve the actual
            # executable and verify its version via absolute path, not PATH.
            $report = Invoke-YeboySystemMediaProbe -Python $python
            $candidate = if ($null -ne $report) { $report.$name } else { $null }
            if ($null -eq $candidate -or -not $candidate.compatible) {
                Write-Host ('[!] {0}: WinGet не надав доступного сумісного виконуваного файлу (код {1}).' -f $name, $wingetCode)
                break
            }
            Write-Host ('[OK] {0} з WinGet перевірено: {1}; повторна підготовка без ручного PATH.' -f $name, $candidate.path)
            $mediaSelection[$name] = [string]$candidate.path
            $media = Invoke-YeboyMedia -SelectMedia -SystemFfmpeg $mediaSelection.ffmpeg -SystemDeno $mediaSelection.deno
        }
    }
    if ($media -ne 0) { return $media }
    $script:YeboyInstallStage = 'readiness'
    Write-Host '[4/4] Перевірка середовища та критичних бібліотек...'
    if (-not (Test-YeboyEnvironment)) {
        Write-Host 'Перевірка бібліотек не пройдена. Відкрийте YEBOYBot.cmd та виберіть [1] початкового меню для повторної перевірки.'
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
        Write-Host 'Якщо Python 3.12 x64 відсутній, перед WinGet буде окреме підтвердження.'
        Write-Host 'Спочатку буде перевірено Python, FFmpeg і Deno, показано версії та шляхи.'
        Write-Host 'Ви оберете: використовувати сумісні FFmpeg/Deno з ПК або локальні.'
        Write-Host 'Python-бібліотеки ізолюються в .venv-v2; глобальні пакети не змінюються.'
        Write-Host 'Якщо потрібні локальні програми, архіви перевіряються за SHA-256.'
        Write-Host 'У разі відмови запуску FFmpeg/Deno WinGet потребуватиме окремого підтвердження.'
        Write-Host 'Нічого вручну в PowerShell робити не потрібно; PATH не змінюємо самі.'
        Write-Host 'Потрібні інтернет і дозвіл Windows на встановлення програм.'
        $choice = Read-Host 'Номер дії (Enter — вихід)'
        if ([string]::IsNullOrWhiteSpace($choice) -or $choice.Trim() -eq '0') { return 0 }
        if ($choice.Trim() -ne '1') {
            Write-Host 'Оберіть [1] для встановлення або [0] для виходу.'
            continue
        }
        $script:YeboyAttemptId = [guid]::NewGuid().ToString('N')
        $script:YeboyBootstrapStarted = $null
        $script:YeboyInstallStage = 'source-bundle'
        if (-not (Test-YeboySourceBundle)) {
            Write-YeboyLauncherReport -Stage 'source-bundle' -ExitCode 1
            return 1
        }
        $script:YeboyInteractiveInstall = $true
        try {
            try { $result = Install-YeboyCore -AllowPythonInstall }
            catch {
                # Do not print a raw PowerShell exception: it may contain
                # local paths, environment variables, or executable output.
                Write-Host '[ПОМИЛКА] Внутрішня помилка на зазначеному етапі початкового встановлення.'
                $result = 1
            }
        } finally { $script:YeboyInteractiveInstall = $false }
        if ($result -eq 0) { return 0 }
        $evidence = @()
        if ($script:YeboyInstallStage -eq 'bootstrap' -and (Test-YeboyFreshVenvDeniedReport)) {
            Write-Host 'Автоматична перевірка журналів Windows для .venv-v2\Scripts\python.exe…'
            # Never print or persist a free-form event message: accept only
            # the three fixed, expected event IDs.
            $allowedEvents = @('CodeIntegrity:3077', 'AppLocker:8004', 'Defender:1121')
            $evidence = @(Get-YeboyVenvBlockEvents | Where-Object { $allowedEvents -ccontains $_ } | Select-Object -Unique)
            if ($evidence.Count -gt 0) {
                Write-Host ('[!] Знайдені можливі події блокування: {0}.' -f ($evidence -join ', '))
                Write-Host 'Перевірте політику з адміністратором ПК; захист Windows не змінювався.'
            } else {
                Write-Host '[i] Подій блокування не підтверджено. Це не виключає заборону доступу чи сторонній захист.'
            }
        }
        Write-YeboyLauncherReport -Stage $script:YeboyInstallStage -ExitCode ([int]$result) -EventCodes $evidence
        Write-Host ''
        Write-Host ('Встановлення зупинено (код {0}). Причина наведена вище.' -f $result)
        Write-Host 'Усуньте вказану причину й відкрийте YEBOYBot.cmd знову.'
        Write-Host 'Повторення без змін не допоможе; конфігурація та дані бота збережені.'
        return $result
    }
}

function Restore-YeboyInterruptedUpdateCore {
    $directory = Join-Path $script:YeboyRoot 'data\v2'
    $plan = Join-Path $directory 'update-plan.json'
    $transaction = Join-Path $directory 'update-transaction.json'
    $repairMarker = Join-Path $directory 'update-repair-required.json'
    if (-not (Test-Path -LiteralPath $plan -PathType Leaf) -and
        -not (Test-Path -LiteralPath $transaction -PathType Leaf) -and
        -not (Test-Path -LiteralPath $repairMarker -PathType Leaf)) {
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
        if (Test-Path -LiteralPath $repairMarker -PathType Leaf) {
            return (Complete-YeboyUpdateRepair)
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
    Set-Content -LiteralPath $repairMarker -Value '{"phase":"repair_required"}' -Encoding UTF8 -ErrorAction Stop
    if ((Invoke-YeboyUpdateWorker -Python $python -Snapshot $snapshot.FullName -Action 'rollback') -ne 0) {
        Write-Host 'Rollback не підтверджений. Код та дані залишено для діагностики.'
        return 1
    }
    if ((Complete-YeboyUpdateRepair) -ne 0) { return 1 }
    Remove-Item -LiteralPath $snapshot.FullName -Force -ErrorAction SilentlyContinue
    Write-Host 'Попередню версію відновлено. Можна повернутися до головного меню.'
    return 0
}

function Invoke-YeboyEntry {
    param([ValidateSet('Launcher', 'Setup', 'Ensure', 'Repair')][string]$Mode)
    if ($Mode -eq 'Ensure' -or $Mode -eq 'Repair') {
        if (-not (Test-YeboySourceBundle)) {
            Write-YeboyLauncherReport -Stage 'source-bundle' -ExitCode 1
            return 1
        }
        if ($Mode -eq 'Ensure') { return (Install-YeboyCore) }
        return (Install-YeboyCore -Repair)
    }
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
        Write-YeboyLauncherReport -Stage $script:YeboyInstallStage -ExitCode 1
        Write-Host 'Токен і дані бота не видаляються. Повторіть запуск YEBOYBot.cmd.'
        exit 1
    }
}
