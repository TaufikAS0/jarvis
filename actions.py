"""
JARVIS Action Executor — AppleScript-based system actions.

Execute actions IMMEDIATELY, before generating any LLM response.
Each function returns {"success": bool, "confirmation": str}.
"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger("jarvis.actions")

DESKTOP_PATH = Path.home() / "Desktop"
IS_WINDOWS = os.name == "nt"
OBSIDIAN_CONFIG_PATH = Path(os.environ.get("APPDATA", "")) / "Obsidian" / "obsidian.json"
OBSIDIAN_JARVIS_NOTE_CANDIDATES = [
    "Codex Skills/Skills/jarvis-launcher.md",
    "jarvis-launcher.md",
]

_WINDOWS_BROWSER_PATHS = {
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
    "firefox": [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    ],
}
CODEX_WINDOWS_APP_ID = "OpenAI.Codex_2p2nqsd0c76g0!App"
SPOTIFY_WINDOWS_APP_ID = "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"
JARVIS_SHOW_LAUNCHER = Path(r"C:\Users\ASUS\.codex\skills\jarvis-launcher\scripts\launch_stark_show.ps1")

_APP_SPECS = {
    "obsidian": {
        "aliases": {
            "obsidian",
            "obsidian app",
            "obsidian application",
            "my obsidian",
            "my obsidian app",
            "obsidian notes",
        },
        "windows_protocol": "obsidian://open",
        "windows_paths": [
            str(Path.home() / "AppData" / "Local" / "Programs" / "Obsidian" / "Obsidian.exe"),
        ],
        "darwin_app": "Obsidian",
        "confirmation": "Opened Obsidian, sir.",
    },
    "vscode": {
        "aliases": {
            "vscode",
            "vs code",
            "visual studio code",
            "code",
        },
        "windows_protocol": "vscode://",
        "windows_paths": [
            str(Path.home() / "AppData" / "Local" / "Programs" / "Microsoft VS Code" / "Code.exe"),
            r"C:\Program Files\Microsoft VS Code\Code.exe",
            r"C:\Program Files (x86)\Microsoft VS Code\Code.exe",
        ],
        "darwin_app": "Visual Studio Code",
        "confirmation": "Opened Visual Studio Code, sir.",
    },
    "explorer": {
        "aliases": {
            "explorer",
            "file explorer",
            "windows explorer",
            "my files",
            "files",
        },
        "windows_executable": "explorer.exe",
        "darwin_app": "Finder",
        "confirmation": "Opened File Explorer, sir.",
    },
    "terminal": {
        "aliases": {
            "terminal",
            "windows terminal",
            "powershell",
            "power shell",
            "command prompt",
            "cmd",
        },
        "windows_executable": "powershell.exe",
        "darwin_app": "Terminal",
        "confirmation": "Opened the terminal, sir.",
    },
    "chrome": {
        "aliases": {
            "chrome",
            "google chrome",
            "browser",
            "web browser",
        },
        "windows_paths": _WINDOWS_BROWSER_PATHS["chrome"],
        "darwin_app": "Google Chrome",
        "confirmation": "Opened Chrome, sir.",
    },
    "spotify": {
        "aliases": {
            "spotify",
            "spotify app",
            "music app",
        },
        "windows_app_id": SPOTIFY_WINDOWS_APP_ID,
        "windows_protocol": "spotify:",
        "confirmation": "Opened Spotify, sir.",
    },
    "codex": {
        "aliases": {
            "codex",
            "openai codex",
            "codex app",
            "codex desktop",
        },
        "windows_app_id": CODEX_WINDOWS_APP_ID,
        "confirmation": "Opened Codex, sir.",
    },
    "jarvis": {
        "aliases": {
            "jarvis",
            "open jarvis",
            "show jarvis",
            "buka jarvis",
            "jarvis launcher",
            "jarvis skill",
        },
        "confirmation": "Opened JARVIS in Obsidian, sir.",
    },
}

_APP_ALIAS_LOOKUP = {
    alias: key
    for key, spec in _APP_SPECS.items()
    for alias in spec["aliases"]
}


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _first_existing_path(paths: list[str]) -> str | None:
    for path in paths:
        if path and os.path.exists(path):
            return path
    return None


def _get_primary_obsidian_vault() -> tuple[str, str] | None:
    """Return (vault_name, vault_path) for the currently configured Obsidian vault."""
    try:
        if not OBSIDIAN_CONFIG_PATH.exists():
            return None
        data = json.loads(OBSIDIAN_CONFIG_PATH.read_text(encoding="utf-8"))
        vaults = data.get("vaults", {})
        if not vaults:
            return None
        preferred = None
        for vault in vaults.values():
            if vault.get("open"):
                preferred = vault
                break
        if preferred is None:
            preferred = next(iter(vaults.values()))
        vault_path = preferred.get("path", "").strip()
        if not vault_path:
            return None
        vault_name = Path(vault_path).name
        return vault_name, vault_path
    except Exception:
        return None


def _find_obsidian_jarvis_note() -> tuple[str, str, str] | None:
    """Return (vault_name, vault_path, relative_note_path) for the JARVIS launcher note."""
    vault = _get_primary_obsidian_vault()
    if not vault:
        return None
    vault_name, vault_path = vault
    vault_root = Path(vault_path)
    for rel_path in OBSIDIAN_JARVIS_NOTE_CANDIDATES:
        if (vault_root / rel_path).exists():
            return vault_name, vault_path, rel_path.replace("\\", "/")

    fallback = next(vault_root.rglob("jarvis-launcher.md"), None)
    if fallback:
        rel = fallback.relative_to(vault_root).as_posix()
        return vault_name, vault_path, rel
    return None


async def _maximize_windows_process(process_name: str, timeout_seconds: int = 8) -> None:
    """Bring a Windows app to the foreground and maximize it."""
    if not IS_WINDOWS:
        return
    script = f"""
$signature = @'
using System;
using System.Runtime.InteropServices;
public static class WinApi {{
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
}}
'@
Add-Type -TypeDefinition $signature -ErrorAction SilentlyContinue | Out-Null
$deadline = (Get-Date).AddSeconds({timeout_seconds})
do {{
    $proc = Get-Process -Name { _ps_quote(process_name) } -ErrorAction SilentlyContinue | Where-Object {{ $_.MainWindowHandle -ne 0 }} | Select-Object -First 1
    if ($proc) {{
        [WinApi]::ShowWindowAsync($proc.MainWindowHandle, 3) | Out-Null
        [WinApi]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
        exit 0
    }}
    Start-Sleep -Milliseconds 300
}} while ((Get-Date) -lt $deadline)
exit 0
"""
    proc = await asyncio.create_subprocess_exec(
        "powershell",
        "-NoProfile",
        "-Command",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


async def _open_jarvis_obsidian_note() -> dict:
    """Open the JARVIS launcher note in Obsidian and maximize the window."""
    note_info = _find_obsidian_jarvis_note()
    if not note_info:
        return {
            "success": False,
            "confirmation": "I couldn't find the JARVIS note in your Obsidian vault, sir.",
        }

    vault_name, _, rel_path = note_info
    uri = f"obsidian://open?vault={quote(vault_name, safe='')}&file={quote(rel_path, safe='')}"

    obsidian_path = _first_existing_path([
        str(Path.home() / "AppData" / "Local" / "Programs" / "Obsidian" / "Obsidian.exe"),
        r"C:\Program Files\Obsidian\Obsidian.exe",
        r"C:\Program Files (x86)\Obsidian\Obsidian.exe",
    ])

    if obsidian_path:
        success, detail = await _start_windows_process(obsidian_path, [uri])
    else:
        success, detail = await _start_windows_process(uri)

    if success:
        await _maximize_windows_process("Obsidian")
    elif detail:
        log.error(f"open_jarvis_obsidian_note failed: {detail}")

    return {
        "success": success,
        "confirmation": "Opened JARVIS in Obsidian and maximized it, sir."
        if success
        else "I had trouble opening the JARVIS note in Obsidian, sir.",
    }


async def run_jarvis_show() -> dict:
    """Run the packaged JARVIS launcher show, including the local website."""
    if not JARVIS_SHOW_LAUNCHER.exists():
        return {
            "success": False,
            "confirmation": "I couldn't find the packaged JARVIS launcher, sir.",
        }

    success, detail = await _start_windows_process(
        "powershell.exe",
        [
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(JARVIS_SHOW_LAUNCHER),
        ],
    )
    if not success and detail:
        log.error(f"run_jarvis_show failed: {detail}")
    return {
        "success": success,
        "confirmation": "Launched the full JARVIS show, sir." if success else "I had trouble starting the JARVIS show, sir.",
    }


def get_dev_agent_mode() -> str:
    """Return the configured coding agent mode."""
    mode = os.getenv("DEV_AGENT", "").strip().lower()
    if mode:
        return mode
    return "codex" if IS_WINDOWS else "claude"


def should_use_codex_delegate() -> bool:
    """True when JARVIS should hand coding work to Codex on this machine."""
    return IS_WINDOWS and get_dev_agent_mode() in {"auto", "codex", "codex_handoff"}


def _normalize_spotify_command_text(text: str) -> str:
    """Normalize a spoken Spotify command into a compact lowercase form."""
    original = re.sub(r"\s+", " ", text.strip())
    if not original:
        return ""

    normalized = original.lower()
    normalized = re.sub(r"[!?]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    prefix_cleaners = [
        r"^hey\s+jarvis\b[,\s]*",
        r"^jarvis\b[,\s]*",
        r"^(?:can you|could you|would you|will you|please|tolong|mohon|coba|bisa(?:kah)?\s+(?:tolong\s+)?)\s+",
    ]
    suffix_cleaners = [
        r"\s+(?:please|dong|deh|ya|for me|buat saya|langsung)$",
    ]
    changed = True
    while changed:
        changed = False
        for pattern in prefix_cleaners:
            updated = re.sub(pattern, "", normalized, flags=re.IGNORECASE).strip()
            if updated != normalized:
                normalized = updated
                changed = True
        for pattern in suffix_cleaners:
            updated = re.sub(pattern, "", normalized, flags=re.IGNORECASE).strip()
            if updated != normalized:
                normalized = updated
                changed = True

    return normalized


def extract_spotify_control(text: str) -> str | None:
    """Extract short Spotify transport commands like pause or next."""
    normalized = _normalize_spotify_command_text(text)
    if not normalized:
        return None

    control_patterns = {
        "pause": [
            r"^(?:pause|stop)\s+(?:spotify|music|musik|song|lagu)$",
            r"^spotify\s+(?:pause|stop)$",
            r"^(?:pause|hentikan)\s+(?:the\s+)?(?:music|musik|song|lagu)$",
        ],
        "resume": [
            r"^(?:resume|continue|lanjutkan)\s+(?:spotify|music|musik|song|lagu)$",
            r"^spotify\s+(?:resume|continue|lanjutkan)$",
            r"^(?:play)\s+(?:spotify|music|musik)$",
        ],
        "next": [
            r"^(?:next|skip)\s+(?:song|track|lagu)?$",
            r"^spotify\s+(?:next|skip)$",
            r"^(?:lagu|track)\s+berikut(?:nya)?$",
        ],
        "previous": [
            r"^(?:previous|back|last)\s+(?:song|track|lagu)?$",
            r"^spotify\s+(?:previous|back)$",
            r"^(?:lagu|track)\s+sebelum(?:nya)?$",
        ],
        "play_first_result": [
            r"^(?:play|putar(?:kan)?|mainkan)\s+(?:the\s+)?(?:first|top)\s+result$",
            r"^(?:putar(?:kan)?|mainkan)\s+hasil\s+pertama$",
            r"^spotify\s+(?:first|top)\s+result$",
        ],
    }

    for control, patterns in control_patterns.items():
        if any(re.match(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns):
            return control

    return None


def extract_spotify_query(text: str) -> str | None:
    """Extract a song request from a short Spotify/music command."""
    normalized = _normalize_spotify_command_text(text)
    if not normalized:
        return None

    patterns = [
        r"^(?:open|buka|launch|start)\s+spotify(?:\s+(?:and|dan|lalu|terus))?\s+(?:play|putar(?:kan)?|mainkan|nyalakan)\s+(?:lagu\s+)?(.+)$",
        r"^spotify\s+(?:play|putar(?:kan)?|mainkan|nyalakan)\s+(?:lagu\s+)?(.+)$",
        r"^(?:play|putar(?:kan)?|mainkan|nyalakan)\s+(?:lagu\s+)?(.+?)\s+(?:on|in|via|di|dari|from)\s+spotify$",
        r"^(?:play|putar(?:kan)?|mainkan|nyalakan)\s+(?:lagu\s+)?(.+)$",
    ]

    for pattern in patterns:
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        query = match.group(1).strip(" .,!?'\"")
        query = re.sub(r"\s+", " ", query)
        if not query:
            return None
        if query.lower() in {"spotify", "music", "lagu", "song", "musik"}:
            return None
        if "spotify" not in normalized and len(query.split()) < 2:
            return None
        return query

    if "spotify" in normalized and re.search(r"\b(play|putar(?:kan)?|mainkan|nyalakan)\b", normalized):
        fallback = re.search(
            r"\b(?:play|putar(?:kan)?|mainkan|nyalakan)\b\s+(?:lagu\s+)?(.+?)(?:\s+(?:on|in|via|di|dari|from)\s+spotify)?$",
            normalized,
            flags=re.IGNORECASE,
        )
        if fallback:
            query = re.sub(r"\s+", " ", fallback.group(1).strip(" .,!?'\""))
            if query and query.lower() not in {"spotify", "music", "lagu", "song", "musik"}:
                return query

    return None


def normalize_desktop_app_name(text: str) -> str | None:
    """Return a canonical app key for short open/launch commands."""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", text.lower()).strip()
    cleaned = re.sub(r"\b(the|app|application|please|now|for me|for us)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    command_prefixes = (
        "open ",
        "launch ",
        "start ",
        "run ",
        "show ",
        "pull up ",
    )
    for prefix in command_prefixes:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break

    cleaned = re.sub(r"\b(on my computer|on my pc|for me)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return _APP_ALIAS_LOOKUP.get(cleaned)


async def _start_windows_process(file_path: str, arguments: list[str] | None = None) -> tuple[bool, str]:
    """Launch a Windows process or protocol handler in a visible way."""
    script = [
        "$ErrorActionPreference = 'Stop'",
        f"$target = {_ps_quote(file_path)}",
    ]
    if arguments:
        arg_list = ", ".join(_ps_quote(arg) for arg in arguments)
        script.append(f"$argsList = @({arg_list})")
        script.append("Start-Process -FilePath $target -ArgumentList $argsList")
    else:
        script.append("Start-Process -FilePath $target")

    proc = await asyncio.create_subprocess_exec(
        "powershell",
        "-NoProfile",
        "-Command",
        "; ".join(script),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode == 0, (stderr or stdout).decode(errors="ignore").strip()


async def _play_spotify_search_result(query: str, timeout_seconds: int = 10) -> tuple[bool, str]:
    """Use Windows UI Automation to play the best Spotify search result and verify playback."""
    if not IS_WINDOWS:
        return False, "Spotify playback automation is only available on Windows"

    script = f"""
$needleRaw = {_ps_quote(query)}
function Normalize-SpotifyText([string]$text) {{
    if (-not $text) {{
        return ""
    }}
    return (($text.ToLower() -replace '[^a-z0-9]+', ' ').Trim())
}}

function Test-SpotifyPlayback([System.Windows.Automation.AutomationElement]$root, [string]$verifyNeedle) {{
    if (-not $root) {{
        return $false
    }}

    $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($element in $all) {{
        $name = $element.Current.Name
        if (-not $name) {{
            continue
        }}

        $normalized = Normalize-SpotifyText $name
        if ($name -like 'Now playing:*') {{
            if (-not $verifyNeedle -or $normalized -like "*$verifyNeedle*") {{
                Write-Output $name
                return $true
            }}
        }}

        if ($name -like 'Pause *') {{
            if (-not $verifyNeedle -or $normalized -like "*$verifyNeedle*") {{
                Write-Output $name
                return $true
            }}
        }}
    }}

    return $false
}}

$needle = Normalize-SpotifyText $needleRaw

$signature = @'
using System;
using System.Runtime.InteropServices;
public static class WinApi {{
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
}}
'@
Add-Type -TypeDefinition $signature -ErrorAction SilentlyContinue | Out-Null
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$shell = New-Object -ComObject WScript.Shell

$deadline = (Get-Date).AddSeconds({timeout_seconds})
do {{
    $proc = Get-Process -Name 'Spotify' -ErrorAction SilentlyContinue | Where-Object {{ $_.MainWindowHandle -ne 0 }} | Select-Object -First 1
    if ($proc) {{
        [WinApi]::ShowWindowAsync($proc.MainWindowHandle, 3) | Out-Null
        [WinApi]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
        Start-Sleep -Milliseconds 450

        $root = [System.Windows.Automation.AutomationElement]::FromHandle($proc.MainWindowHandle)
        $buttonCondition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Button
        )
        $buttons = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $buttonCondition)
        $best = $null
        $bestScore = -100000
        $bestVerifyNeedle = $needle

        foreach ($button in $buttons) {{
            $name = $button.Current.Name
            if (-not $name -or -not $name.StartsWith('Play ')) {{
                continue
            }}

            $normalizedName = Normalize-SpotifyText $name
            if ($normalizedName -notlike "*$needle*") {{
                continue
            }}

            $score = 0
            $candidateVerifyNeedle = $needle
            if ($name -match '^Play (.+?) by (.+)$') {{
                $titleNorm = Normalize-SpotifyText $matches[1]
                $artistNorm = Normalize-SpotifyText $matches[2]
                $candidateVerifyNeedle = $titleNorm
                $score += 200
                if ($titleNorm -eq $needle) {{
                    $score += 500
                }} elseif ($titleNorm -like "$needle*") {{
                    $score += 320
                }} elseif ($needle -like "$titleNorm*") {{
                    $score += 240
                }}
                if ($artistNorm -and $needle -like "*$artistNorm*") {{
                    $score += 140
                }}
            }} else {{
                $score += 30
            }}

            if ($normalizedName -eq ("play " + $needle)) {{
                $score += 260
            }} elseif ($normalizedName -like ("play " + $needle + "*")) {{
                $score += 180
            }}
            if ($name -match 'Radio|Playlist') {{
                $score -= 120
            }}

            $score -= $name.Length
            if ($score -gt $bestScore) {{
                $best = $button
                $bestScore = $score
                $bestVerifyNeedle = $candidateVerifyNeedle
            }}
        }}

        if ($best) {{
            try {{
                $invoke = $best.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
                $invoke.Invoke()
            }} catch {{
            }}

            for ($attempt = 0; $attempt -lt 5; $attempt++) {{
                Start-Sleep -Milliseconds 450
                if (Test-SpotifyPlayback $root $bestVerifyNeedle) {{
                    exit 0
                }}
            }}

            try {{
                $best.SetFocus()
            }} catch {{
            }}
            Start-Sleep -Milliseconds 180
            $shell.SendKeys('{{ENTER}}')
            for ($attempt = 0; $attempt -lt 4; $attempt++) {{
                Start-Sleep -Milliseconds 420
                if (Test-SpotifyPlayback $root $bestVerifyNeedle) {{
                    exit 0
                }}
            }}

            $shell.SendKeys(' ')
            for ($attempt = 0; $attempt -lt 4; $attempt++) {{
                Start-Sleep -Milliseconds 420
                if (Test-SpotifyPlayback $root $bestVerifyNeedle) {{
                    exit 0
                }}
            }}
        }}
    }}
    Start-Sleep -Milliseconds 300
}} while ((Get-Date) -lt $deadline)
Write-Error 'Spotify result playback could not be verified'
exit 1
"""
    proc = await asyncio.create_subprocess_exec(
        "powershell",
        "-NoProfile",
        "-Command",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    detail = (stderr or stdout).decode(errors="ignore").strip()
    return proc.returncode == 0, detail


async def _trigger_spotify_playback_with_space(query: str, timeout_seconds: int = 8) -> tuple[bool, str]:
    """Fallback: focus Spotify, send keyboard triggers, and verify playback."""
    if not IS_WINDOWS:
        return False, "Spotify playback automation is only available on Windows"

    script = f"""
$needleRaw = {_ps_quote(query)}
function Normalize-SpotifyText([string]$text) {{
    if (-not $text) {{
        return ""
    }}
    return (($text.ToLower() -replace '[^a-z0-9]+', ' ').Trim())
}}

function Test-SpotifyPlayback([System.Windows.Automation.AutomationElement]$root, [string]$verifyNeedle) {{
    if (-not $root) {{
        return $false
    }}

    $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($element in $all) {{
        $name = $element.Current.Name
        if (-not $name) {{
            continue
        }}

        $normalized = Normalize-SpotifyText $name
        if ($name -like 'Now playing:*') {{
            if (-not $verifyNeedle -or $normalized -like "*$verifyNeedle*") {{
                Write-Output $name
                return $true
            }}
        }}

        if ($name -like 'Pause *') {{
            if (-not $verifyNeedle -or $normalized -like "*$verifyNeedle*") {{
                Write-Output $name
                return $true
            }}
        }}
    }}

    return $false
}}

$needle = Normalize-SpotifyText $needleRaw
$signature = @'
using System;
using System.Runtime.InteropServices;
public static class WinApi {{
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
}}
'@
Add-Type -TypeDefinition $signature -ErrorAction SilentlyContinue | Out-Null
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$shell = New-Object -ComObject WScript.Shell
$deadline = (Get-Date).AddSeconds({timeout_seconds})
do {{
    $proc = Get-Process -Name 'Spotify' -ErrorAction SilentlyContinue | Where-Object {{ $_.MainWindowHandle -ne 0 }} | Select-Object -First 1
    if ($proc) {{
        [WinApi]::ShowWindowAsync($proc.MainWindowHandle, 3) | Out-Null
        [WinApi]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
        Start-Sleep -Milliseconds 700
        $root = [System.Windows.Automation.AutomationElement]::FromHandle($proc.MainWindowHandle)

        if (Test-SpotifyPlayback $root $needle) {{
            exit 0
        }}

        $sequences = @('{{ESC}}', '{{ENTER}}', ' ')
        foreach ($keys in $sequences) {{
            $shell.SendKeys($keys)
            Start-Sleep -Milliseconds 450
            if (Test-SpotifyPlayback $root $needle) {{
                exit 0
            }}
        }}
    }}
    Start-Sleep -Milliseconds 300
}} while ((Get-Date) -lt $deadline)
Write-Error 'Spotify keyboard fallback could not verify playback'
exit 1
"""
    proc = await asyncio.create_subprocess_exec(
        "powershell",
        "-NoProfile",
        "-Command",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    detail = (stderr or stdout).decode(errors="ignore").strip()
    return proc.returncode == 0, detail


async def _control_spotify_transport(control: str, timeout_seconds: int = 8) -> tuple[bool, str]:
    """Trigger short Spotify transport controls via UI Automation."""
    if not IS_WINDOWS:
        return False, "Spotify transport automation is only available on Windows"

    script = f"""
$mode = {_ps_quote(control)}
$signature = @'
using System;
using System.Runtime.InteropServices;
public static class WinApi {{
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
}}
'@
Add-Type -TypeDefinition $signature -ErrorAction SilentlyContinue | Out-Null
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$shell = New-Object -ComObject WScript.Shell

function Pick-SpotifyButton($buttons, [string]$mode) {{
    $best = $null
    $bestScore = -100000

    foreach ($button in $buttons) {{
        $name = $button.Current.Name
        if (-not $name) {{
            continue
        }}

        $score = -100000
        switch ($mode) {{
            'pause' {{
                if ($name -like 'Pause*') {{
                    $score = 1000 - $name.Length
                }}
            }}
            'resume' {{
                if ($name -eq 'Play') {{
                    $score = 1200
                }} elseif ($name -like 'Play *' -and $name -notmatch 'Radio|Playlist') {{
                    $score = 800 - $name.Length
                }}
            }}
            'next' {{
                if ($name -eq 'Next') {{
                    $score = 1200
                }} elseif ($name -like 'Next*') {{
                    $score = 900 - $name.Length
                }}
            }}
            'previous' {{
                if ($name -eq 'Previous') {{
                    $score = 1200
                }} elseif ($name -like 'Previous*' -or $name -like 'Back*') {{
                    $score = 900 - $name.Length
                }}
            }}
            'play_first_result' {{
                if ($name -like 'Play *' -and $name -notmatch 'Radio|Playlist') {{
                    $rect = $button.Current.BoundingRectangle
                    $score = 100000 - ([int]$rect.Top * 100) - [int]$rect.Left
                }}
            }}
        }}

        if ($score -gt $bestScore) {{
            $best = $button
            $bestScore = $score
        }}
    }}

    return $best
}}

$deadline = (Get-Date).AddSeconds({timeout_seconds})
do {{
    $proc = Get-Process -Name 'Spotify' -ErrorAction SilentlyContinue | Where-Object {{ $_.MainWindowHandle -ne 0 }} | Select-Object -First 1
    if ($proc) {{
        [WinApi]::ShowWindowAsync($proc.MainWindowHandle, 3) | Out-Null
        [WinApi]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
        Start-Sleep -Milliseconds 500

        $root = [System.Windows.Automation.AutomationElement]::FromHandle($proc.MainWindowHandle)
        $buttonCondition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Button
        )
        $buttons = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $buttonCondition)
        $best = Pick-SpotifyButton $buttons $mode
        if ($best) {{
            try {{
                $invoke = $best.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
                $invoke.Invoke()
            }} catch {{
                try {{
                    $best.SetFocus()
                }} catch {{
                }}
                Start-Sleep -Milliseconds 150
                $shell.SendKeys('{{ENTER}}')
            }}
            Write-Output $best.Current.Name
            exit 0
        }}
    }}
    Start-Sleep -Milliseconds 300
}} while ((Get-Date) -lt $deadline)
Write-Error 'Spotify control button not found'
exit 1
"""
    proc = await asyncio.create_subprocess_exec(
        "powershell",
        "-NoProfile",
        "-Command",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    detail = (stderr or stdout).decode(errors="ignore").strip()
    return proc.returncode == 0, detail


async def _mark_terminal_as_jarvis(revert_after: float = 5.0):
    """Temporarily set the front Terminal window to Ocean theme, then revert.

    Shows the user JARVIS is active in that terminal. Reverts after revert_after seconds.
    """
    # Save the current profile, switch to Ocean, then revert
    script_save = (
        'tell application "Terminal"\n'
        '    return name of current settings of front window\n'
        'end tell'
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script_save,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        original_profile = stdout.decode().strip()

        # Switch to Ocean
        script_set = (
            'tell application "Terminal"\n'
            '    set current settings of front window to settings set "Ocean"\n'
            'end tell'
        )
        proc2 = await asyncio.create_subprocess_exec(
            "osascript", "-e", script_set,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc2.communicate()

        # Schedule revert
        if original_profile and original_profile != "Ocean":
            asyncio.get_event_loop().call_later(
                revert_after,
                lambda: asyncio.ensure_future(_revert_terminal_theme(original_profile))
            )
    except Exception:
        pass


async def _revert_terminal_theme(profile_name: str):
    """Revert a Terminal window back to its original profile."""
    escaped = profile_name.replace('"', '\\"')
    script = (
        'tell application "Terminal"\n'
        f'    set current settings of front window to settings set "{escaped}"\n'
        'end tell'
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
    except Exception:
        pass


async def open_terminal(command: str = "") -> dict:
    """Open Terminal.app and optionally run a command. Marks it blue for JARVIS."""
    if IS_WINDOWS:
        arguments = ["-NoExit"]
        if command:
            arguments.extend(["-Command", command])
        success, detail = await _start_windows_process("powershell.exe", arguments)
        if not success and detail:
            log.error(f"open_terminal failed: {detail}")
        return {
            "success": success,
            "confirmation": "Terminal is open, sir." if success else "I had trouble opening Terminal, sir.",
        }

    if command:
        escaped = command.replace('"', '\\"')
        script = (
            'tell application "Terminal"\n'
            "    activate\n"
            f'    do script "{escaped}"\n'
            "end tell"
        )
    else:
        script = (
            'tell application "Terminal"\n'
            "    activate\n"
            "end tell"
        )
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    success = proc.returncode == 0
    if not success:
        log.error(f"open_terminal failed: {stderr.decode()}")
    else:
        await _mark_terminal_as_jarvis()
    return {
        "success": success,
        "confirmation": "Terminal is open, sir." if success else "I had trouble opening Terminal, sir.",
    }


async def open_browser(url: str, browser: str = "chrome") -> dict:
    """Open URL in user's browser (Chrome or Firefox)."""
    escaped_url = url.replace('"', '\\"')

    if IS_WINDOWS:
        browser_key = "firefox" if browser.lower() == "firefox" else "chrome"
        executable = _first_existing_path(_WINDOWS_BROWSER_PATHS.get(browser_key, []))
        if executable:
            success, detail = await _start_windows_process(executable, [url])
        else:
            success, detail = await _start_windows_process(url)
        if not success and detail:
            log.error(f"open_browser ({browser_key}) failed: {detail}")
        app_name = "Firefox" if browser_key == "firefox" else "Chrome"
        return {
            "success": success,
            "confirmation": f"Pulled that up in {app_name}, sir." if success else f"{app_name} ran into a problem, sir.",
        }

    if browser.lower() == "firefox":
        app_name = "Firefox"
        script = (
            'tell application "Firefox"\n'
            "    activate\n"
            f'    open location "{escaped_url}"\n'
            "end tell"
        )
    else:
        app_name = "Chrome"
        script = (
            'tell application "Google Chrome"\n'
            "    activate\n"
            f'    open location "{escaped_url}"\n'
            "end tell"
        )

    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    success = proc.returncode == 0
    if not success:
        log.error(f"open_browser ({app_name}) failed: {stderr.decode()}")
    return {
        "success": success,
        "confirmation": f"Pulled that up in {app_name}, sir." if success else f"{app_name} ran into a problem, sir.",
    }


# Keep backward compat
async def open_chrome(url: str) -> dict:
    return await open_browser(url, "chrome")


async def play_spotify(query: str) -> dict:
    """Open Spotify, search for a track, and start the first result on Windows."""
    cleaned = re.sub(r"\s+", " ", query).strip(" .,!?'\"")
    if not cleaned:
        return {
            "success": False,
            "confirmation": "Tell me what to play on Spotify, sir.",
        }

    if not IS_WINDOWS:
        uri = f"https://open.spotify.com/search/{quote(cleaned, safe='')}"
        return await open_browser(uri, "chrome")

    search_uri = f"spotify:search:{quote(cleaned, safe='')}"
    success, detail = await _start_windows_process(search_uri)
    if not success:
        app_result = await open_app("spotify")
        if not app_result["success"]:
            return {
                "success": False,
                "confirmation": "I couldn't open Spotify on this machine, sir.",
            }
        await asyncio.sleep(1.2)
        success, detail = await _start_windows_process(search_uri)

    if not success:
        if detail:
            log.error(f"play_spotify search failed: {detail}")
        return {
            "success": False,
            "confirmation": f"I had trouble searching Spotify for {cleaned}, sir.",
        }

    await asyncio.sleep(2.0)
    play_success, play_detail = await _play_spotify_search_result(cleaned)
    if not play_success:
        if play_detail:
            log.warning(f"play_spotify UI automation failed: {play_detail}")
    fallback_success, fallback_detail = await _trigger_spotify_playback_with_space(cleaned)
    if not fallback_success and fallback_detail:
        log.warning(f"play_spotify keyboard fallback failed: {fallback_detail}")
        return {
            "success": True,
            "confirmation": f"Opened Spotify search for {cleaned}, sir, but playback still needs one tap.",
        }

    return {
        "success": True,
        "confirmation": f"Playing {cleaned} on Spotify, sir.",
    }


async def control_spotify(control: str) -> dict:
    """Control Spotify playback transport actions on Windows."""
    normalized = re.sub(r"\s+", "_", control.strip().lower())
    confirmations = {
        "pause": "Paused Spotify, sir.",
        "resume": "Resumed Spotify, sir.",
        "next": "Skipping to the next track, sir.",
        "previous": "Going back to the previous track, sir.",
        "play_first_result": "Playing the first Spotify result, sir.",
    }

    if normalized not in confirmations:
        return {
            "success": False,
            "confirmation": "I don't recognize that Spotify control yet, sir.",
        }

    success, detail = await _control_spotify_transport(normalized)
    if not success and detail:
        log.warning(f"control_spotify ({normalized}) failed: {detail}")

    return {
        "success": success,
        "confirmation": confirmations[normalized] if success else f"I had trouble controlling Spotify, sir.",
    }


async def open_app(target: str) -> dict:
    """Open a supported desktop application by name."""
    app_key = normalize_desktop_app_name(target) or target.strip().lower()
    if app_key == "jarvis":
        return await _open_jarvis_obsidian_note()

    spec = _APP_SPECS.get(app_key)
    if not spec:
        return {
            "success": False,
            "confirmation": f"I don't know how to open {target}, sir.",
        }

    if IS_WINDOWS:
        app_id = spec.get("windows_app_id")
        executable = spec.get("windows_executable")
        if app_id:
            success, detail = await _start_windows_process("explorer.exe", [f"shell:AppsFolder\\{app_id}"])
        elif executable:
            success, detail = await _start_windows_process(executable)
        else:
            path = _first_existing_path(spec.get("windows_paths", []))
            if path:
                success, detail = await _start_windows_process(path)
            elif spec.get("windows_protocol"):
                success, detail = await _start_windows_process(spec["windows_protocol"])
            else:
                success, detail = False, f"No Windows launch target configured for {target}"

        if not success and detail:
            log.error(f"open_app ({app_key}) failed: {detail}")
        return {
            "success": success,
            "confirmation": spec["confirmation"] if success else f"I had trouble opening {target}, sir.",
        }

    app_name = spec.get("darwin_app")
    if not app_name:
        return {
            "success": False,
            "confirmation": f"I had trouble opening {target}, sir.",
        }

    script = (
        f'tell application "{app_name}"\n'
        "    activate\n"
        "end tell"
    )
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    success = proc.returncode == 0
    if not success:
        log.error(f"open_app ({app_key}) failed: {stderr.decode()}")
    return {
        "success": success,
        "confirmation": spec["confirmation"] if success else f"I had trouble opening {target}, sir.",
    }


async def handoff_to_codex(working_dir: str, prompt: str, project_name: str | None = None) -> dict:
    """Prepare a coding handoff for the Codex desktop app on Windows."""
    if not IS_WINDOWS:
        return {
            "success": False,
            "confirmation": "Codex handoff is currently set up for Windows only, sir.",
        }

    project_path = Path(working_dir)
    project_path.mkdir(parents=True, exist_ok=True)
    task_file = project_path / "CODEX_TASK.md"
    announce_script = project_path / "JARVIS_ANNOUNCE.ps1"
    project_label = project_name or project_path.name
    announce_script.write_text(
        "$Text = ($args -join ' ').Trim()\n"
        "if (-not $Text) { Write-Error 'Provide announcement text.'; exit 1 }\n"
        "$payload = @{ text = $Text; source = 'codex' } | ConvertTo-Json -Compress\n"
        "$tmp = New-TemporaryFile\n"
        "Set-Content -LiteralPath $tmp -Value $payload -Encoding UTF8\n"
        "try {\n"
        "  curl.exe -k -s -X POST https://localhost:8340/api/announce -H \"Content-Type: application/json\" --data-binary \"@$tmp\"\n"
        "} finally {\n"
        "  Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue\n"
        "}\n",
        encoding="utf-8",
    )
    handoff_text = (
        f"# Codex Task\n\n"
        f"Project: {project_label}\n"
        f"Working directory: {project_path}\n\n"
        f"## Request\n{prompt.strip()}\n\n"
        f"## Notes\n"
        f"- Work directly in this folder.\n"
        f"- If there is an existing codebase, preserve current patterns unless the prompt says otherwise.\n"
        f"- Summarize what changed and any blockers when done.\n"
        f"- When you finish or hit a blocker, run `.\\{announce_script.name} \"short spoken update\"` so JARVIS can read it aloud.\n"
    )
    task_file.write_text(handoff_text, encoding="utf-8")

    clipboard_text = (
        f"Please work in this folder:\n{project_path}\n\n"
        f"Task:\n{prompt.strip()}\n\n"
        f"A task brief has also been written to:\n{task_file}\n"
        f"When done, announce the result with:\n{announce_script}\n"
    )
    clipboard_proc = await asyncio.create_subprocess_exec(
        "powershell",
        "-NoProfile",
        "-Command",
        f"Set-Clipboard -Value {_ps_quote(clipboard_text)}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    clipboard_stdout, clipboard_stderr = await clipboard_proc.communicate()
    if clipboard_proc.returncode != 0:
        detail = (clipboard_stderr or clipboard_stdout).decode(errors="ignore").strip()
        if detail:
            log.warning(f"Failed to copy Codex handoff to clipboard: {detail}")

    app_result = await open_app("codex")
    folder_result = await _start_windows_process("explorer.exe", [str(project_path)])

    success = app_result["success"] and folder_result[0]
    if not folder_result[0] and folder_result[1]:
        log.error(f"Failed to open project folder for Codex handoff: {folder_result[1]}")

    confirmation = (
        f"Opened Codex, copied the task brief, and opened {project_label} for handoff, sir."
        if success
        else "I prepared the Codex handoff, but part of the launch sequence ran into trouble, sir."
    )
    return {
        "success": success,
        "confirmation": confirmation,
        "task_file": str(task_file),
    }


async def open_claude_in_project(project_dir: str, prompt: str) -> dict:
    if IS_WINDOWS:
        claude_md = Path(project_dir) / "CLAUDE.md"
        claude_md.write_text(f"# Task\n\n{prompt}\n\nBuild this completely. If web app, make index.html work standalone.\n")

        command = f'Set-Location "{project_dir}"; claude --dangerously-skip-permissions'
        success, detail = await _start_windows_process("powershell.exe", ["-NoExit", "-Command", command])
        if not success and detail:
            log.error(f"open_claude_in_project failed: {detail}")
        return {
            "success": success,
            "confirmation": "Claude Code is running in Terminal, sir. You can watch the progress."
            if success
            else "Had trouble spawning Claude Code, sir.",
        }

    """Open Terminal, cd to project dir, run Claude Code interactively.

    Writes the prompt to CLAUDE.md (which claude reads automatically on startup)
    then launches claude in interactive mode with --dangerously-skip-permissions.
    No prompt escaping needed — CLAUDE.md handles context delivery.
    """
    # Write prompt to CLAUDE.md — claude reads this automatically
    claude_md = Path(project_dir) / "CLAUDE.md"
    claude_md.write_text(f"# Task\n\n{prompt}\n\nBuild this completely. If web app, make index.html work standalone.\n")

    # Launch claude interactive — it reads CLAUDE.md on its own
    script = (
        'tell application "Terminal"\n'
        "    activate\n"
        f'    do script "cd {project_dir} && claude --dangerously-skip-permissions"\n'
        "end tell"
    )
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    success = proc.returncode == 0
    if not success:
        log.error(f"open_claude_in_project failed: {stderr.decode()}")
    else:
        await _mark_terminal_as_jarvis()
    return {
        "success": success,
        "confirmation": "Claude Code is running in Terminal, sir. You can watch the progress."
        if success
        else "Had trouble spawning Claude Code, sir.",
    }


async def prompt_existing_terminal(project_name: str, prompt: str) -> dict:
    """Find a Terminal window matching a project name and type a prompt into it.

    Uses System Events keystroke to type into an active Claude Code session
    rather than `do script` which would open a new shell.
    """
    if IS_WINDOWS:
        return {
            "success": False,
            "confirmation": "Prompting an existing terminal is not wired up on Windows yet, sir.",
        }

    escaped_name = project_name.replace('"', '\\"')
    escaped_prompt = prompt.replace("\\", "\\\\").replace('"', '\\"')

    # Single atomic script: find window, focus it, type into it
    script = f'''
tell application "Terminal"
    set matched to false
    set targetWindow to missing value
    repeat with w in windows
        if name of w contains "{escaped_name}" then
            set targetWindow to w
            set matched to true
            exit repeat
        end if
    end repeat

    if not matched then
        return "NOT_FOUND"
    end if

    -- Bring the matched window to front
    set index of targetWindow to 1
    set selected tab of targetWindow to selected tab of targetWindow
    activate
end tell

-- Wait for window to be fully focused
delay 1

-- Now type into it
tell application "System Events"
    tell process "Terminal"
        set frontmost to true
        delay 0.3
        keystroke "{escaped_prompt}"
        delay 0.2
        keystroke return
    end tell
end tell

return "OK"
'''

    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

        result = stdout.decode().strip()
        if result == "NOT_FOUND":
            return {
                "success": False,
                "confirmation": f"Couldn't find a terminal for {project_name}, sir.",
            }

        success = proc.returncode == 0
        if not success:
            log.error(f"prompt_existing_terminal failed: {stderr.decode()[:200]}")

        if success:
            await _mark_terminal_as_jarvis()

        return {
            "success": success,
            "confirmation": f"Sent that to {project_name}, sir." if success
            else f"Had trouble typing into {project_name}, sir.",
        }

    except asyncio.TimeoutError:
        return {"success": False, "confirmation": "Terminal operation timed out, sir."}
    except Exception as e:
        log.error(f"prompt_existing_terminal failed: {e}")
        return {"success": False, "confirmation": "Something went wrong reaching that terminal, sir."}


async def get_chrome_tab_info() -> dict:
    """Read the current Chrome tab's title and URL via AppleScript."""
    script = (
        'tell application "Google Chrome"\n'
        "    set tabTitle to title of active tab of front window\n"
        "    set tabURL to URL of active tab of front window\n"
        '    return tabTitle & "|" & tabURL\n'
        "end tell"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            result = stdout.decode().strip()
            parts = result.split("|", 1)
            if len(parts) == 2:
                return {"title": parts[0], "url": parts[1]}
        return {}
    except Exception as e:
        log.warning(f"get_chrome_tab_info failed: {e}")
        return {}


async def monitor_build(project_dir: str, ws=None, synthesize_fn=None) -> None:
    """Monitor a Claude Code build for completion. Notify via WebSocket when done."""
    import base64

    output_file = Path(project_dir) / ".jarvis_output.txt"
    start = time.time()
    timeout = 600  # 10 minutes

    while time.time() - start < timeout:
        await asyncio.sleep(5)
        if output_file.exists():
            content = output_file.read_text()
            if "--- JARVIS TASK COMPLETE ---" in content:
                log.info(f"Build complete in {project_dir}")
                if ws and synthesize_fn:
                    try:
                        msg = "The build is complete, sir."
                        audio_bytes = await synthesize_fn(msg)
                        if audio_bytes:
                            encoded = base64.b64encode(audio_bytes).decode()
                            await ws.send_json({"type": "status", "state": "speaking"})
                            await ws.send_json({"type": "audio", "data": encoded, "text": msg})
                            await ws.send_json({"type": "status", "state": "idle"})
                    except Exception as e:
                        log.warning(f"Build notification failed: {e}")
                return

    log.warning(f"Build timed out in {project_dir}")


async def execute_action(intent: dict, projects: list = None) -> dict:
    """Route a classified intent to the right action function.

    Args:
        intent: {"action": str, "target": str} from classify_intent()
        projects: list of known project dicts for resolving working dirs

    Returns: {"success": bool, "confirmation": str, "project_dir": str | None}
    """
    action = intent.get("action", "chat")
    target = intent.get("target", "")

    if action == "open_terminal":
        result = await open_terminal("claude --dangerously-skip-permissions")
        result["project_dir"] = None
        return result

    elif action == "open_app":
        result = await open_app(target)
        result["project_dir"] = None
        return result

    elif action == "browse":
        if target.startswith("http://") or target.startswith("https://"):
            url = target
        else:
            url = f"https://www.google.com/search?q={quote(target)}"

        # Detect which browser user wants
        target_lower = target.lower()
        if "firefox" in target_lower:
            browser = "firefox"
        else:
            browser = "chrome"

        result = await open_browser(url, browser)
        result["project_dir"] = None
        return result

    elif action == "build":
        # Create project folder on Desktop, spawn Claude Code
        project_name = _generate_project_name(target)
        project_dir = str(DESKTOP_PATH / project_name)
        os.makedirs(project_dir, exist_ok=True)
        result = await open_claude_in_project(project_dir, target)
        result["project_dir"] = project_dir
        return result

    else:
        return {"success": False, "confirmation": "", "project_dir": None}


def _generate_project_name(prompt: str) -> str:
    """Generate a kebab-case project folder name from the prompt."""
    # First: check for a quoted name like "tiktok-analytics-dashboard"
    quoted = re.search(r'"([^"]+)"', prompt)
    if quoted:
        name = quoted.group(1).strip()
        # Already kebab-case or close to it
        name = re.sub(r"[^a-zA-Z0-9\s-]", "", name).strip()
        if name:
            return re.sub(r"[\s]+", "-", name.lower())

    # Second: check for "called X" or "named X" pattern
    called = re.search(r'(?:called|named)\s+(\S+(?:[-_]\S+)*)', prompt, re.IGNORECASE)
    if called:
        name = re.sub(r"[^a-zA-Z0-9-]", "", called.group(1))
        if len(name) > 3:
            return name.lower()

    # Fallback: extract meaningful words
    words = re.sub(r"[^a-zA-Z0-9\s]", "", prompt.lower()).split()
    skip = {"a", "the", "an", "me", "build", "create", "make", "for", "with", "and",
            "to", "of", "i", "want", "need", "new", "project", "directory", "called",
            "on", "desktop", "that", "application", "app", "full", "stack", "simple",
            "web", "page", "site", "named"}
    meaningful = [w for w in words if w not in skip and len(w) > 2][:4]
    return "-".join(meaningful) if meaningful else "jarvis-project"
