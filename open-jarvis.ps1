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
Stop-JarvisProcess -Name "node.exe" -Needle "\vite\bin\vite.js"

Remove-Item $backendLog, $backendErr, $frontendLog, $frontendErr -ErrorAction SilentlyContinue

$pythonLauncher = "C:\Windows\py.exe"
if (-not (Test-Path $pythonLauncher)) {
    throw "Python launcher not found at $pythonLauncher"
}

Start-Process -FilePath $pythonLauncher `
    -ArgumentList "-3.11", "server.py" `
    -WorkingDirectory $root `
    -RedirectStandardOutput $backendLog `
    -RedirectStandardError $backendErr | Out-Null

if (-not (Wait-HttpOk -Url "https://localhost:8340/api/health" -SkipCertCheck -TimeoutSeconds 30)) {
    throw "Backend failed to start. Check $backendErr"
}

Start-Process -FilePath "npm.cmd" `
    -ArgumentList "run", "dev" `
    -WorkingDirectory $frontendDir `
    -RedirectStandardOutput $frontendLog `
    -RedirectStandardError $frontendErr | Out-Null

if (-not (Wait-HttpOk -Url "http://localhost:5173" -TimeoutSeconds 30)) {
    throw "Frontend failed to start. Check $frontendErr"
}

$chromeCandidates = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
)

$chromeArgs = @("http://localhost:5173")
if ($WindowMode -eq "fullscreen") {
    $chromeArgs = @("--new-window", "--kiosk", "http://localhost:5173")
} elseif ($WindowMode -eq "maximized") {
    $chromeArgs = @("--new-window", "--start-maximized", "http://localhost:5173")
}

$chrome = $chromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($chrome) {
    Start-Process -FilePath $chrome -ArgumentList $chromeArgs | Out-Null
} else {
    Start-Process "http://localhost:5173" | Out-Null
}

Write-Host "JARVIS started."
Write-Host "Frontend: http://localhost:5173"
Write-Host "Backend: https://localhost:8340"
Write-Host "Window mode: $WindowMode"
