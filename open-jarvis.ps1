param(
    [ValidateSet("fullscreen", "maximized", "windowed")]
    [string]$WindowMode = "maximized"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendLog = Join-Path $root "backend.log"
$backendErr = Join-Path $root "backend.err.log"
$frontendDir = Join-Path $root "frontend"
$frontendLog = Join-Path $frontendDir "frontend.log"
$frontendErr = Join-Path $frontendDir "frontend.err.log"
$jarvisChromeProfile = Join-Path $env:LOCALAPPDATA "JarvisChromeProfile"
$frontendPackageJson = Join-Path $frontendDir "package.json"
$defaultVoxCpmModelPath = "D:\AI Project\VoxCPM2"
$launchUrl = "http://127.0.0.1:5173"

function Stop-JarvisProcess {
    param(
        [string]$Name,
        [string]$Needle
    )

    $targets = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq $Name -and $_.CommandLine -like "*$Needle*"
    }

    foreach ($target in $targets) {
        try {
            Stop-Process -Id $target.ProcessId -Force -ErrorAction Stop
        } catch {
            Write-Warning "Failed to stop $Name $($target.ProcessId): $($_.Exception.Message)"
        }
    }
}

function Stop-JarvisBrowser {
    param(
        [string]$UrlNeedle,
        [string]$ProfilePath
    )

    $targets = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "chrome.exe" -and (
            ($_.CommandLine -like "*$UrlNeedle*") -or
            ($_.CommandLine -like "*$ProfilePath*")
        )
    }

    foreach ($target in $targets | Select-Object -Unique ProcessId,CommandLine) {
        try {
            & taskkill.exe /PID $target.ProcessId /T /F 2>$null | Out-Null
        } catch {
            # Process already gone — ignore silently
        }
    }
}

function Stop-JarvisFrontend {
    param(
        [string]$FrontendNeedle
    )

    $nodeTargets = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "node.exe" -and $_.CommandLine -like "*$FrontendNeedle*"
    })

    $parentIds = @($nodeTargets | ForEach-Object { $_.ParentProcessId } | Where-Object { $_ })

    $targetIds = @($nodeTargets | ForEach-Object { $_.ProcessId }) + $parentIds
    foreach ($targetId in $targetIds | Select-Object -Unique) {
        try {
            & taskkill.exe /PID $targetId /T /F 2>$null | Out-Null
        } catch {
        }
    }
}

function Wait-HttpOk {
    param(
        [string]$Url,
        [switch]$SkipCertCheck,
        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            if ($SkipCertCheck) {
                & curl.exe -k -s -o NUL $Url
            } else {
                & curl.exe -s -o NUL $Url
            }
            if ($LASTEXITCODE -eq 0) {
                return $true
            }
        } catch {
        }
        Start-Sleep -Milliseconds 750
    }

    return $false
}

Stop-JarvisProcess -Name "python.exe" -Needle "server.py"
Stop-JarvisProcess -Name "python.exe" -Needle "tts_server.py"
Stop-JarvisProcess -Name "python.exe" -Needle "tts_server_voxcpm.py"
Stop-JarvisFrontend -FrontendNeedle $frontendDir
Stop-JarvisBrowser -UrlNeedle "http://localhost:5173" -ProfilePath $jarvisChromeProfile
Stop-JarvisBrowser -UrlNeedle $launchUrl -ProfilePath $jarvisChromeProfile

Remove-Item $backendLog, $backendErr, $frontendLog, $frontendErr -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $jarvisChromeProfile -Force | Out-Null

$pythonLauncher = "C:\Windows\py.exe"
if (-not (Test-Path $pythonLauncher)) {
    throw "Python launcher not found at $pythonLauncher"
}
if (-not (Test-Path $frontendPackageJson)) {
    throw "Frontend package.json not found at $frontendPackageJson"
}

$frontendLauncher = (Get-Command npm.cmd -ErrorAction Stop).Source

# --- Auto-start local TTS server if configured ---
$envFile = Join-Path $root ".env"
$ttsProvider = "fish"
$ttsEngine = "kokoro"
$voxModelPath = $defaultVoxCpmModelPath
if (Test-Path $envFile) {
    foreach ($envLine in Get-Content $envFile) {
        if ($envLine -match '^\s*#') {
            continue
        }
        if ($envLine -match '^\s*JARVIS_TTS_PROVIDER\s*=\s*(.+?)\s*$') {
            $ttsProvider = $matches[1].Trim().Trim("'").Trim('"').ToLower()
            continue
        }
        if ($envLine -match '^\s*JARVIS_LOCAL_TTS_ENGINE\s*=\s*(.+?)\s*$') {
            $ttsEngine = $matches[1].Trim().Trim("'").Trim('"').ToLower()
            continue
        }
        if ($envLine -match '^\s*JARVIS_LOCAL_TTS_MODEL_PATH\s*=\s*(.+?)\s*$') {
            $voxModelPath = $matches[1].Trim().Trim("'").Trim('"')
            continue
        }
    }
}

if ($ttsProvider -eq "local") {
    $resolvedTtsEngine = if ($ttsEngine -like "vox*") { "voxcpm" } else { "kokoro" }
    if ($resolvedTtsEngine -eq "voxcpm") {
        Write-Host "VoxCPM2 auto-start is disabled in launcher."
        Write-Host "Enable it explicitly from JARVIS Settings when you want to use the heavier local voice engine."
    } else {
        $ttsScriptName = "tts_server.py"
        $ttsServerPath = Join-Path $root $ttsScriptName
        $ttsLog = Join-Path $root (($ttsScriptName -replace '\.py$', '.log'))
        $ttsErrLog = Join-Path $root (($ttsScriptName -replace '\.py$', '.err.log'))
        if (Test-Path $ttsServerPath) {
            Remove-Item Env:VOXCPM_MODEL_PATH -ErrorAction SilentlyContinue
            Write-Host "Starting local TTS server (Kokoro)..."
            Start-Process -FilePath $pythonLauncher `
                -ArgumentList "-3.11", $ttsScriptName `
                -WorkingDirectory $root `
                -RedirectStandardOutput $ttsLog `
                -RedirectStandardError $ttsErrLog | Out-Null
            Start-Sleep -Seconds 3
        }
    }
}

Start-Process -FilePath $pythonLauncher `
    -ArgumentList "-3.11", "server.py" `
    -WorkingDirectory $root `
    -RedirectStandardOutput $backendLog `
    -RedirectStandardError $backendErr | Out-Null

if (-not (Wait-HttpOk -Url "https://localhost:8340/api/health" -SkipCertCheck -TimeoutSeconds 30)) {
    throw "Backend failed to start. Check $backendErr"
}

Start-Process -FilePath $frontendLauncher `
    -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "5173", "--strictPort") `
    -WorkingDirectory $frontendDir `
    -RedirectStandardOutput $frontendLog `
    -RedirectStandardError $frontendErr | Out-Null

if (-not (Wait-HttpOk -Url $launchUrl -TimeoutSeconds 30)) {
    throw "Frontend failed to start. Check $frontendErr"
}

$chromeCandidates = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
)

function New-ChromeProfileArg {
    param(
        [string]$ProfilePath
    )

    return ('--user-data-dir="{0}"' -f $ProfilePath)
}

$chromeArgs = @(
    (New-ChromeProfileArg -ProfilePath $jarvisChromeProfile),
    "--no-first-run",
    "--disable-session-crashed-bubble",
    $launchUrl
)
if ($WindowMode -eq "fullscreen") {
    $chromeArgs = @(
        "--new-window",
        "--kiosk",
        (New-ChromeProfileArg -ProfilePath $jarvisChromeProfile),
        "--no-first-run",
        "--disable-session-crashed-bubble",
        $launchUrl
    )
} elseif ($WindowMode -eq "maximized") {
    $chromeArgs = @(
        "--new-window",
        "--start-maximized",
        (New-ChromeProfileArg -ProfilePath $jarvisChromeProfile),
        "--no-first-run",
        "--disable-session-crashed-bubble",
        $launchUrl
    )
}

$chrome = $chromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($chrome) {
    Start-Process -FilePath $chrome -ArgumentList $chromeArgs | Out-Null
} else {
    Start-Process $launchUrl | Out-Null
}

Write-Host "JARVIS started."
Write-Host "Frontend: $launchUrl"
Write-Host "Backend: https://localhost:8340"
Write-Host "Window mode: $WindowMode"
