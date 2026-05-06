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

import httpx

log = logging.getLogger("jarvis.actions")

DESKTOP_PATH = Path.home() / "Desktop"
IS_WINDOWS = os.name == "nt"
OBSIDIAN_CONFIG_PATH = Path(os.environ.get("APPDATA", "")) / "Obsidian" / "obsidian.json"
OBSIDIAN_NOTE_CANDIDATES = {
    "jarvis": {
        "candidates": [
            "Codex Skills/Skills/jarvis-launcher.md",
            "jarvis-launcher.md",
        ],
        "fallback_names": ["jarvis-launcher.md"],
        "missing_confirmation": "I couldn't find the JARVIS launcher note in your Obsidian vault, sir.",
    },
    "skill_index": {
        "candidates": [
            "Codex Skills/Skill Index.md",
            "Skill Index.md",
        ],
        "fallback_names": ["Skill Index.md"],
        "missing_confirmation": "I couldn't find the Codex Skill Index in your Obsidian vault, sir.",
    },
    "codex_bridge": {
        "candidates": [
            "Codex Skills/Skills/JARVIS Flow/Codex & Claude Bridge.md",
            "Codex Skills/Skills/JARVIS Flow/Codex Bridge.md",
            "Skills/JARVIS Flow/Codex & Claude Bridge.md",
            "Skills/JARVIS Flow/Codex Bridge.md",
        ],
        "fallback_names": ["Codex & Claude Bridge.md", "Codex Bridge.md"],
        "missing_confirmation": "I couldn't find the Codex & Claude Bridge note in your Obsidian vault, sir.",
    },
    "ask_codex": {
        "candidates": [
            "Codex Skills/Skills/jarvis-ask-codex.md",
            "Skills/jarvis-ask-codex.md",
        ],
        "fallback_names": ["jarvis-ask-codex.md"],
        "missing_confirmation": "I couldn't find the Ask Codex note in your Obsidian vault, sir.",
    },
    "obsidian_updates": {
        "candidates": [
            "Codex Skills/Skills/JARVIS Flow/Obsidian Updates.md",
            "Skills/JARVIS Flow/Obsidian Updates.md",
        ],
        "fallback_names": ["Obsidian Updates.md"],
        "missing_confirmation": "I couldn't find the Obsidian updates note in your Obsidian vault, sir.",
    },
}

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
CODEX_JARVIS_THREAD_TITLE = os.getenv("CODEX_JARVIS_THREAD_TITLE", "JARVIS Bridge")
PROJECT_FOLDER_MANAGER_ROOT = Path(r"C:\Users\ASUS\.codex\skills\project-folder-manager")
PROJECT_STRUCTURE_SCRIPT = PROJECT_FOLDER_MANAGER_ROOT / "scripts" / "create_project_structure.ps1"
WIZ_CONTROLLER_ROOT = Path(os.getenv("WIZ_CONTROLLER_ROOT", r"D:\WIZ Lamp\wiz-controller"))
WIZ_CONTROLLER_LAUNCHER = Path(os.getenv("WIZ_CONTROLLER_LAUNCHER", str(WIZ_CONTROLLER_ROOT / "run_wiz_controller.bat")))
WIZ_CONTROLLER_START_LOG = Path(os.getenv("WIZ_CONTROLLER_START_LOG", str(WIZ_CONTROLLER_ROOT / "jarvis-wiz-start.log")))
WIZ_CONTROLLER_ERROR_LOG = Path(os.getenv("WIZ_CONTROLLER_ERROR_LOG", str(WIZ_CONTROLLER_ROOT / "jarvis-wiz-error.log")))
WIZ_CONTROLLER_URL = os.getenv("WIZ_CONTROLLER_URL", "http://127.0.0.1:5000").rstrip("/")
WIZ_CONTROLLER_HTTP_TIMEOUT = float(os.getenv("WIZ_CONTROLLER_TIMEOUT", "6"))
AIR_FAN_BASE_URL = os.getenv("AIR_FAN_BASE_URL", "http://192.168.1.36").rstrip("/")
AIR_FAN_UI_PATH = os.getenv("AIR_FAN_UI_PATH", "/airControl.html")
AIR_FAN_HTTP_TIMEOUT = float(os.getenv("AIR_FAN_TIMEOUT", "5"))

_AIR_FAN_ALLOWED_SPEEDS = (20, 40, 60, 80, 100)
_AIR_FAN_PROFILE_CODES = {
    "quiet": "0",
    "normal": "1",
    "dynamic": "2",
    "manual": "3",
}
_AIR_FAN_PROFILE_NAMES = {
    "0": "Quiet",
    "1": "Normal",
    "2": "Dynamic",
    "3": "Manual",
}
_AIR_FAN_STATUS_NAMES = {
    "0": "Connected",
    "1": "Disconnected",
    "2": "Fault",
}
_AIR_FAN_ALIASES = (
    "fan",
    "kipas",
    "blower",
    "air control",
    "air cleaner",
    "air purifier",
)
_AIR_FAN_CONTROLLER_ALIASES = (
    "air control",
    "fan control",
    "fan controller",
    "air controller",
    "controller fan",
    "kontrol fan",
    "kontrol kipas",
)

_WIZ_COLOR_KEYWORDS = {
    "red": ("red", "#ff3030"),
    "merah": ("red", "#ff3030"),
    "blue": ("blue", "#2f80ff"),
    "biru": ("blue", "#2f80ff"),
    "green": ("green", "#22c55e"),
    "hijau": ("green", "#22c55e"),
    "yellow": ("yellow", "#ffd43b"),
    "kuning": ("yellow", "#ffd43b"),
    "orange": ("orange", "#ff8a3d"),
    "oranye": ("orange", "#ff8a3d"),
    "purple": ("purple", "#8b5cf6"),
    "ungu": ("purple", "#8b5cf6"),
    "pink": ("pink", "#ec4899"),
    "magenta": ("pink", "#ec4899"),
    "cyan": ("cyan", "#22d3ee"),
    "turquoise": ("cyan", "#22d3ee"),
}

_WIZ_WHITE_MODE_PRESETS = {
    "warm white": {"label": "warm white", "temperature": 2700, "brightness": None},
    "putih hangat": {"label": "warm white", "temperature": 2700, "brightness": None},
    "warm": {"label": "warm white", "temperature": 2700, "brightness": None},
    "daylight": {"label": "daylight", "temperature": 6500, "brightness": 100},
    "siang": {"label": "daylight", "temperature": 6500, "brightness": 100},
    "focus": {"label": "focus", "temperature": 5000, "brightness": 100},
    "fokus": {"label": "focus", "temperature": 5000, "brightness": 100},
    "focus mode": {"label": "focus", "temperature": 5000, "brightness": 100},
    "fokus mode": {"label": "focus", "temperature": 5000, "brightness": 100},
    "mode focus": {"label": "focus", "temperature": 5000, "brightness": 100},
    "mode fokus": {"label": "focus", "temperature": 5000, "brightness": 100},
    "bright white": {"label": "bright white", "temperature": 6500, "brightness": 100},
    "putih terang": {"label": "bright white", "temperature": 6500, "brightness": 100},
    "cool white": {"label": "cool white", "temperature": 6000, "brightness": 100},
    "putih dingin": {"label": "cool white", "temperature": 6000, "brightness": 100},
    # Plain "putih"/"white" — neutral white (~5500K) at full brightness
    "putih": {"label": "white",      "temperature": 5500, "brightness": 100},
    "white": {"label": "white",      "temperature": 5500, "brightness": 100},
    "warna putih": {"label": "white", "temperature": 5500, "brightness": 100},
    "warna white": {"label": "white", "temperature": 5500, "brightness": 100},
}

# ---------------------------------------------------------------------------
# Scene presets — natural lighting profiles
# Each scene maps to a color temperature + brightness + optional color combo
# ---------------------------------------------------------------------------
_WIZ_SCENE_PRESETS: dict[str, dict] = {
    # --- Work / Focus ---
    "lampu kerja":      {"label": "work mode",   "temperature": 5000, "brightness": 100},
    "work mode":        {"label": "work mode",   "temperature": 5000, "brightness": 100},
    "mode kerja":       {"label": "work mode",   "temperature": 5000, "brightness": 100},
    "working mode":     {"label": "work mode",   "temperature": 5000, "brightness": 100},
    "study mode":       {"label": "work mode",   "temperature": 5000, "brightness": 100},
    "mode belajar":     {"label": "work mode",   "temperature": 5000, "brightness": 100},
    # --- Relax / Chill ---
    "lampu santai":     {"label": "relax mode",  "temperature": 2700, "brightness": 40},
    "relax mode":       {"label": "relax mode",  "temperature": 2700, "brightness": 40},
    "mode santai":      {"label": "relax mode",  "temperature": 2700, "brightness": 40},
    "chill mode":       {"label": "relax mode",  "temperature": 2700, "brightness": 40},
    "relaxing":         {"label": "relax mode",  "temperature": 2700, "brightness": 40},
    "santai":           {"label": "relax mode",  "temperature": 2700, "brightness": 40},
    # --- Night / Sleep ---
    "lampu malam":      {"label": "night mode",  "temperature": 2200, "brightness": 10},
    "night mode":       {"label": "night mode",  "temperature": 2200, "brightness": 10},
    "mode malam":       {"label": "night mode",  "temperature": 2200, "brightness": 10},
    "sleep mode":       {"label": "night mode",  "temperature": 2200, "brightness": 10},
    "mode tidur":       {"label": "night mode",  "temperature": 2200, "brightness": 10},
    "lampu tidur":      {"label": "night mode",  "temperature": 2200, "brightness": 10},
    "bedtime":          {"label": "night mode",  "temperature": 2200, "brightness": 10},
    # --- Movie / Cinema ---
    "lampu nonton":     {"label": "movie mode",  "temperature": 2700, "brightness": 15},
    "movie mode":       {"label": "movie mode",  "temperature": 2700, "brightness": 15},
    "mode nonton":      {"label": "movie mode",  "temperature": 2700, "brightness": 15},
    "cinema mode":      {"label": "movie mode",  "temperature": 2700, "brightness": 15},
    "bioskop":          {"label": "movie mode",  "temperature": 2700, "brightness": 15},
    # --- Reading ---
    "lampu baca":       {"label": "reading mode", "temperature": 4000, "brightness": 80},
    "reading mode":     {"label": "reading mode", "temperature": 4000, "brightness": 80},
    "mode baca":        {"label": "reading mode", "temperature": 4000, "brightness": 80},
    # --- Morning ---
    "lampu pagi":       {"label": "morning mode", "temperature": 4000, "brightness": 70},
    "morning mode":     {"label": "morning mode", "temperature": 4000, "brightness": 70},
    "mode pagi":        {"label": "morning mode", "temperature": 4000, "brightness": 70},
    # --- Gaming ---
    "gaming mode":      {"label": "gaming mode", "hex": "#8b5cf6", "brightness": 60},
    "mode gaming":      {"label": "gaming mode", "hex": "#8b5cf6", "brightness": 60},
    "lampu gaming":     {"label": "gaming mode", "hex": "#8b5cf6", "brightness": 60},
    # --- Dinner ---
    "lampu makan":      {"label": "dinner mode", "temperature": 2700, "brightness": 50},
    "dinner mode":      {"label": "dinner mode", "temperature": 2700, "brightness": 50},
    "mode makan malam": {"label": "dinner mode", "temperature": 2700, "brightness": 50},
}

# All known scene label names for pattern matching
_WIZ_SCENE_LABELS = set(preset["label"] for preset in _WIZ_SCENE_PRESETS.values())

_WIZ_DEVICE_ALIASES = (
    "wiz",
    "light",
    "lights",
    "lamp",
    "lamps",
    "lampu",
    "lampunya",
    "smart light",
    "smart lights",
    "smart lamp",
    "smart lamps",
    "house lights",
)

_WIZ_CONTROLLER_ALIASES = (
    "wiz controller",
    "light controller",
    "lamp controller",
    "lighting controller",
    "smart light controller",
)

_WIZ_START_LOCK: asyncio.Lock | None = None
_WIZ_HTTP_CLIENT: httpx.AsyncClient | None = None
_WIZ_DEVICE_CACHE_TTL_SECONDS = float(os.getenv("WIZ_DEVICE_CACHE_TTL_SECONDS", "20"))
_WIZ_DISCOVERY_CACHE_TTL_SECONDS = float(os.getenv("WIZ_DISCOVERY_CACHE_TTL_SECONDS", "12"))
_WIZ_LAST_DEVICE_SNAPSHOT: list[dict] = []
_WIZ_LAST_DEVICE_SNAPSHOT_AT = 0.0
_WIZ_LAST_DISCOVERY_SNAPSHOT: list[dict] = []
_WIZ_LAST_DISCOVERY_SNAPSHOT_AT = 0.0
_WIZ_COLOR_PATTERN = "|".join(
    re.escape(keyword) for keyword in sorted(_WIZ_COLOR_KEYWORDS, key=len, reverse=True)
)
_WIZ_WHITE_MODE_PATTERN = "|".join(
    re.escape(keyword) for keyword in sorted(_WIZ_WHITE_MODE_PRESETS, key=len, reverse=True)
)
_WIZ_SCENE_PATTERN = "|".join(
    re.escape(keyword) for keyword in sorted(_WIZ_SCENE_PRESETS, key=len, reverse=True)
)

# ---------------------------------------------------------------------------
# Device name aliases — maps friendly names to device IPs
# Users can set these via env or a config file; falls back to WiZ device names
# Format: "friendly_name:ip" pairs separated by commas
# Example: WIZ_DEVICE_ALIASES="kamar:192.168.1.4,ruang tamu:192.168.1.5"
# ---------------------------------------------------------------------------
_WIZ_DEVICE_NAME_MAP: dict[str, str] = {}
_raw_device_aliases = os.getenv("WIZ_DEVICE_ALIASES", "")
for _pair in _raw_device_aliases.split(","):
    _pair = _pair.strip()
    if ":" in _pair:
        _name, _ip = _pair.rsplit(":", 1)
        _WIZ_DEVICE_NAME_MAP[_name.strip().lower()] = _ip.strip()

# Common room names for parsing (bilingual)
_WIZ_TARGET_PATTERNS = (
    r"(?:lampu\s+)?(?:di\s+|at\s+|in\s+)?(?:kamar(?:\s+tidur)?|bedroom)",
    r"(?:lampu\s+)?(?:di\s+|at\s+|in\s+)?(?:ruang\s+tamu|living\s+room)",
    r"(?:lampu\s+)?(?:di\s+|at\s+|in\s+)?(?:dapur|kitchen)",
    r"(?:lampu\s+)?(?:di\s+|at\s+|in\s+)?(?:ruang\s+kerja|office|kantor)",
    r"(?:lampu\s+)?(?:di\s+|at\s+|in\s+)?(?:kamar\s+mandi|bathroom)",
)

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
            "jarvis launcher",
            "jarvis skill",
            "jarvis note",
        },
        "obsidian_note_key": "jarvis",
        "confirmation": "Opened JARVIS in Obsidian, sir.",
    },
    "skill_index": {
        "aliases": {
            "skill index",
            "codex skill index",
            "obsidian skill index",
            "jarvis skill index",
            "codex skills",
        },
        "obsidian_note_key": "skill_index",
        "confirmation": "Opened the Codex Skill Index in Obsidian, sir.",
    },
    "codex_bridge": {
        "aliases": {
            "codex bridge",
            "codex and claude bridge",
            "codex claude bridge",
            "jarvis bridge",
            "jarvis codex bridge",
            "codex bridge note",
        },
        "obsidian_note_key": "codex_bridge",
        "confirmation": "Opened the Codex & Claude Bridge note in Obsidian, sir.",
    },
    "ask_codex_note": {
        "aliases": {
            "ask codex note",
            "jarvis ask codex note",
            "codex handoff note",
            "codex prompt note",
        },
        "obsidian_note_key": "ask_codex",
        "confirmation": "Opened the Ask Codex note in Obsidian, sir.",
    },
    "obsidian_updates": {
        "aliases": {
            "obsidian updates",
            "obsidian update note",
            "obsidian cli note",
            "obsidian changelog note",
        },
        "obsidian_note_key": "obsidian_updates",
        "confirmation": "Opened the Obsidian updates note in Obsidian, sir.",
    },
}

_APP_ALIAS_LOOKUP = {
    alias: key
    for key, spec in _APP_SPECS.items()
    for alias in spec["aliases"]
}


def _normalize_wiz_command_text(text: str) -> str:
    normalized = text.lower().strip()
    normalized = re.sub(r"\bwi\s*z\b", "wiz", normalized)
    normalized = re.sub(r"\bwhiz\b", "wiz", normalized)
    normalized = re.sub(r"\bwizz\b", "wiz", normalized)
    normalized = re.sub(r"\bweez\b", "wiz", normalized)
    normalized = re.sub(r"\bsmartlights\b", "smart lights", normalized)
    normalized = re.sub(r"\bnyalain\b", "nyalakan", normalized)
    normalized = re.sub(r"\bhidupin\b", "hidupkan", normalized)
    normalized = re.sub(r"\bmatiin\b", "matikan", normalized)
    normalized = re.sub(r"\bwarnaiin\b", "warnai", normalized)
    normalized = re.sub(r"\bwarnai\b", "warnai", normalized)
    normalized = re.sub(r"\bgantiin\s+warna\b", "ubah warna", normalized)
    normalized = re.sub(r"\bubahin\s+warna\b", "ubah warna", normalized)
    normalized = re.sub(r"\bwarnanya\b", "warna", normalized)
    normalized = re.sub(r"\bmerahin\b", "merah", normalized)
    normalized = re.sub(r"\bbiruin\b", "biru", normalized)
    normalized = re.sub(r"\bhijauin\b", "hijau", normalized)
    normalized = re.sub(r"\bunguin\b", "ungu", normalized)
    normalized = re.sub(r"\bkuningin\b", "kuning", normalized)
    normalized = re.sub(r"\boranyein\b", "oranye", normalized)
    normalized = re.sub(r"\bputihin\b", "putih", normalized)
    normalized = re.sub(r"^hey\s+jarvis\b[,\s]*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"^jarvis\b[,\s]*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(
        r"^(?:can you|could you|would you|will you|please|tolong|mohon|coba|bisa(?:kah)?\s+(?:tolong\s+)?)\s+",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\s+(?:please|dong|deh|ya|sir|for me|buat saya|right now|sekarang)$",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"[^a-z0-9#\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _wiz_matches_any_pattern(normalized: str, patterns: tuple[str, ...]) -> bool:
    return any(re.match(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns)


def _cache_wiz_devices(devices: list[dict]) -> None:
    global _WIZ_LAST_DEVICE_SNAPSHOT, _WIZ_LAST_DEVICE_SNAPSHOT_AT
    _WIZ_LAST_DEVICE_SNAPSHOT = [dict(device) for device in (devices or []) if isinstance(device, dict)]
    _WIZ_LAST_DEVICE_SNAPSHOT_AT = time.monotonic() if _WIZ_LAST_DEVICE_SNAPSHOT else 0.0


def _get_cached_wiz_devices(max_age_seconds: float | None = None) -> list[dict]:
    if not _WIZ_LAST_DEVICE_SNAPSHOT:
        return []
    if max_age_seconds is not None and _WIZ_LAST_DEVICE_SNAPSHOT_AT:
        if (time.monotonic() - _WIZ_LAST_DEVICE_SNAPSHOT_AT) > max_age_seconds:
            return []
    return [dict(device) for device in _WIZ_LAST_DEVICE_SNAPSHOT]


def _cache_wiz_discovery_snapshot(devices: list[dict]) -> None:
    global _WIZ_LAST_DISCOVERY_SNAPSHOT, _WIZ_LAST_DISCOVERY_SNAPSHOT_AT
    _WIZ_LAST_DISCOVERY_SNAPSHOT = [dict(device) for device in (devices or []) if isinstance(device, dict)]
    _WIZ_LAST_DISCOVERY_SNAPSHOT_AT = time.monotonic() if _WIZ_LAST_DISCOVERY_SNAPSHOT else 0.0


def _get_cached_wiz_discovery_snapshot(max_age_seconds: float | None = None) -> list[dict]:
    if not _WIZ_LAST_DISCOVERY_SNAPSHOT:
        return []
    if max_age_seconds is not None and _WIZ_LAST_DISCOVERY_SNAPSHOT_AT:
        if (time.monotonic() - _WIZ_LAST_DISCOVERY_SNAPSHOT_AT) > max_age_seconds:
            return []
    return [dict(device) for device in _WIZ_LAST_DISCOVERY_SNAPSHOT]


def _wiz_has_device_context(normalized: str) -> bool:
    return any(alias in normalized for alias in _WIZ_DEVICE_ALIASES + _WIZ_CONTROLLER_ALIASES)


def _wiz_start_lock() -> asyncio.Lock:
    global _WIZ_START_LOCK
    if _WIZ_START_LOCK is None:
        _WIZ_START_LOCK = asyncio.Lock()
    return _WIZ_START_LOCK


def _wiz_is_indonesian_mode() -> bool:
    return os.getenv("USER_LANGUAGE", "en").strip().lower().startswith("id")


def _wiz_response(english: str, indonesian: str) -> str:
    return indonesian if _wiz_is_indonesian_mode() else english


def _normalize_air_fan_command_text(text: str) -> str:
    normalized = text.lower().strip()
    normalized = re.sub(r"\baircontrol\b", "air control", normalized)
    normalized = re.sub(r"\bfannya\b", "fan", normalized)
    normalized = re.sub(r"\bkipasnya\b", "kipas", normalized)
    normalized = re.sub(r"\bblowernya\b", "blower", normalized)
    normalized = re.sub(r"\bkecepatan(?:nya)?\b", "speed", normalized)
    normalized = re.sub(r"\bprofil\b", "profile", normalized)
    normalized = re.sub(r"\bdinamis\b", "dynamic", normalized)
    normalized = re.sub(r"\bsenyap\b", "quiet", normalized)
    normalized = re.sub(r"\bmanual mode\b", "manual", normalized)
    normalized = re.sub(r"\bmode manual\b", "manual", normalized)
    normalized = re.sub(r"\bmode otomatis\b", "auto", normalized)
    normalized = re.sub(r"\bnyalain\b", "nyalakan", normalized)
    normalized = re.sub(r"\bhidupin\b", "hidupkan", normalized)
    normalized = re.sub(r"\bmatiin\b", "matikan", normalized)
    normalized = re.sub(r"^hey\s+jarvis\b[,\s]*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"^jarvis\b[,\s]*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(
        r"^(?:can you|could you|would you|will you|please|tolong|mohon|coba|bisa(?:kah)?\s+(?:tolong\s+)?)\s+",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\s+(?:please|dong|deh|ya|sir|for me|buat saya|right now|sekarang)$",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"[^a-z0-9#%\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _air_fan_matches_any_pattern(normalized: str, patterns: tuple[str, ...]) -> bool:
    return any(re.match(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns)


def _air_fan_is_indonesian_mode() -> bool:
    return os.getenv("USER_LANGUAGE", "en").strip().lower().startswith("id")


def _air_fan_response(english: str, indonesian: str) -> str:
    return indonesian if _air_fan_is_indonesian_mode() else english


def _air_fan_has_device_context(normalized: str) -> bool:
    return any(alias in normalized for alias in _AIR_FAN_ALIASES + _AIR_FAN_CONTROLLER_ALIASES)


def _looks_like_air_fan_request(text: str) -> bool:
    normalized = _normalize_air_fan_command_text(text)
    if not normalized:
        return False

    if any(alias in normalized for alias in _AIR_FAN_CONTROLLER_ALIASES):
        return True

    if any(alias in normalized for alias in ("fan", "kipas", "blower")) and any(
        keyword in normalized
        for keyword in (
            "speed", "status", "profile", "quiet", "normal", "dynamic",
            "manual", "dashboard", "control", "rpm",
        )
    ):
        return True

    return _air_fan_matches_any_pattern(normalized, (
        r"^(?:cek|lihat|status|show|check)\s+(?:status\s+)?(?:fan|kipas|blower|air control|air cleaner|air purifier)$",
        r"^(?:fan|kipas|blower|air control|air cleaner|air purifier)\s+status$",
        r"^(?:open|buka|launch|show)\s+(?:the\s+)?(?:air control|fan control|fan controller|controller fan|kontrol fan|kontrol kipas|dashboard fan)$",
        r"^(?:set|atur|ubah|ganti|make|switch)\s+(?:fan|kipas|blower)(?:\s+(?:speed|profile))?\b",
        r"^(?:set|atur|ubah|ganti|make|switch)\s+(?:speed|profile)\s+(?:fan|kipas|blower|air control)?\b",
        r"^(?:fan|kipas|blower)(?:\s+(?:speed|profile))?\s+(?:to|ke|jadi\s+)?\d{1,3}(?:\s*(?:%|percent|persen))?$",
        r"^(?:quiet|normal|dynamic)\s+(?:profile|mode|fan|kipas|blower)$",
        r"^(?:speed|profile)\s+(?:fan|kipas|blower|air control)\b",
    ))


def _looks_like_wiz_request(text: str) -> bool:
    normalized = _normalize_wiz_command_text(text)
    if not normalized:
        return False

    if any(alias in normalized for alias in _WIZ_CONTROLLER_ALIASES):
        return True

    # Scene presets are always WiZ requests
    if any(phrase in normalized for phrase in _WIZ_SCENE_PRESETS):
        return True

    if "wiz" in normalized and any(
        keyword in normalized
        for keyword in (
            "lampu", "light", "lights", "controller", "dashboard", "discover", "scan",
            "brightness", "kecerahan", "warna", "color", "colour", "daylight", "focus",
            "warm white", "putih", "nyalakan", "matikan", "turn on", "turn off",
        )
    ):
        return True

    # Scan / status queries — many Indonesian natural forms
    _SCAN_SUBSTRINGS = (
        "scan lampu", "cari lampu", "cek lampu", "periksa lampu",
        "lihat lampu", "status lampu", "info lampu", "berapa lampu",
        "lampu berapa", "ada berapa lampu", "lampu yang online",
        "lampu yang nyala", "lampu yang terhubung", "lampu apa",
        "cek status lampu", "lihat status lampu", "temukan lampu",
        "scan lights", "check lights", "check lamps", "lamp status",
        "light status", "how many lights", "how many lamps", "list lamps",
    )
    if any(s in normalized for s in _SCAN_SUBSTRINGS):
        return True

    return _wiz_matches_any_pattern(normalized, (
        r"^(?:scan|discover|find)\s+(?:the\s+)?(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)(?:\s+.+)?$",
        r"^(?:scan|discover|find|cek|cari|periksa|lihat|pantau|temukan)\s+(?:the\s+|status\s+)?(?:lights?|lamps?|lampu(?:nya)?)(?:\s+.+)?$",
        r"^(?:cek|lihat|periksa|pantau|info|status)\s+(?:status\s+)?(?:lampu(?:nya)?|lights?|lamps?)(?:\s+.+)?$",
        r"^(?:ada\s+)?(?:berapa|how\s+many)\s+(?:banyak\s+)?(?:lampu(?:nya)?|lights?|lamps?)(?:\s+.+)?$",
        r"^(?:lampu(?:nya)?|lights?|lamps?)\s+(?:berapa|yang\s+(?:online|nyala|terhubung|aktif|mana|ada))(?:\s+.+)?$",
        r"^(?:open|buka|launch|show)\s+(?:the\s+)?(?:light|lamp|lighting|smart light)\s+controller$",
        r"^(?:buka|open|launch|show)\s+(?:dashboard|kontrol|controller)\s+(?:lampu|lights?|lamps?)$",
        r"^(?:turn|switch)\s+(?:on|off)\s+(?:the\s+)?(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?|house lights)$",
        r"^(?:nyalakan|hidupkan|matikan|padamkan)\s+(?:lampu(?:nya)?|lights?|lamps?|smart lights?)$",
        r"^(?:lights?|lamps?|lampu(?:nya)?|smart lights?|house lights)\s+(?:on|off)$",
        r"^(?:lampu(?:nya)?|lights?|lamps?)\s+(?:nyala|mati)$",
        r"^(?:set|make|change|atur|ubah)\s+(?:the\s+)?(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)\b",
        r"^(?:buat|bikin|jadikan|ganti)\s+(?:lampu(?:nya)?|lights?|lamps?)\b",
        # Color-change commands that omit the lamp noun (e.g. "ganti warna ke merah", "ubah warna jadi biru")
        rf"^(?:ganti|ubah|change|set)\s+(?:warna(?:nya)?|color|colour)\b",
        rf"^(?:warnai(?:n)?|warnain)\s+\w+",  # colloquial: "warnain merah"
        rf"^(?:ganti|ubah|change|set)\s+(?:warna(?:nya)?|color|colour)\s+(?:ke\s+|jadi\s+|menjadi\s+|to\s+)?(?:{_WIZ_COLOR_PATTERN})$",
        rf"^(?:turn|make|set|change|colour|color|warnai|ubah(?:\s+warna)?)\s+(?:the\s+)?(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)\s+(?:to\s+|jadi\s+|menjadi\s+|ke\s+)?(?:{_WIZ_COLOR_PATTERN})$",
        rf"^(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)\s+(?:warna\s+|color\s+|colour\s+|jadi\s+|menjadi\s+)?(?:{_WIZ_COLOR_PATTERN})$",
        r"^(?:brightness|kecerahan)\b",
        r"^(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)\s+\d{1,3}(?:\s*(?:%|percent|persen))?$",
        rf"^(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)\s+(?:{_WIZ_COLOR_PATTERN})$",
        rf"^(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)\s+(?:{_WIZ_WHITE_MODE_PATTERN})$",
        rf"^(?:mode\s+)?(?:{_WIZ_WHITE_MODE_PATTERN}|{_WIZ_SCENE_PATTERN})\s+(?:for\s+)?(?:the\s+)?(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)$",
        rf"^(?:turn|set|change|make|atur|ubah|aktifkan|pakai)\s+(?:the\s+)?(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)\s+(?:to\s+|ke\s+)?(?:{_WIZ_WHITE_MODE_PATTERN}|{_WIZ_SCENE_PATTERN})$",
        rf"^(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)\s+(?:{_WIZ_SCENE_PATTERN})$",
    ))


def _wiz_count_phrase(count: int) -> str:
    if _wiz_is_indonesian_mode():
        return f"{count} lampu"
    return "1 light" if count == 1 else f"{count} lights"


def _wiz_failure_tail(failure_count: int) -> str:
    if failure_count <= 0:
        return ""
    if failure_count == 1:
        return _wiz_response(" One light didn't respond.", " Satu lampu belum merespons.")
    return _wiz_response(
        f" {failure_count} lights didn't respond.",
        f" {failure_count} lampu belum merespons.",
    )


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _first_existing_path(paths: list[str]) -> str | None:
    for path in paths:
        if path and os.path.exists(path):
            return path
    return None


def _first_existing_dir(paths: list[Path]) -> str | None:
    for path in paths:
        if path and path.exists():
            return str(path)
    return None


def _get_documents_dir() -> str | None:
    home = Path.home()
    return _first_existing_dir([
        home / "OneDrive" / "Dokumen",
        home / "OneDrive" / "Documents",
        home / "Documents",
        home / "Dokumen",
    ])


def _get_downloads_dir() -> str | None:
    home = Path.home()
    return _first_existing_dir([
        home / "Downloads",
        home / "Unduhan",
        home / "OneDrive" / "Downloads",
    ])


def _get_default_folder_root() -> str | None:
    configured = os.getenv("JARVIS_DEFAULT_FOLDER_ROOT", "").strip()
    if not configured:
        env_file = Path(__file__).with_name(".env")
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("JARVIS_DEFAULT_FOLDER_ROOT="):
                    configured = line.split("=", 1)[1].strip().strip('"')
                    break
    if configured and Path(configured).exists():
        return configured
    return None


def _get_known_folder_roots() -> list[tuple[str, str]]:
    roots: list[tuple[str, str]] = []
    documents_dir = _get_documents_dir()
    downloads_dir = _get_downloads_dir()
    vault = _get_primary_obsidian_vault()
    default_root = _get_default_folder_root()

    roots.append(("desktop", str(DESKTOP_PATH)))
    if default_root:
        roots.extend([
            ("default folder", default_root),
            ("default root", default_root),
            ("jin work", default_root),
            ("jin work document", default_root),
            ("jin document", default_root),
        ])
    if documents_dir:
        roots.extend([
            ("documents", documents_dir),
            ("document", documents_dir),
            ("dokumen", documents_dir),
            ("docs", documents_dir),
        ])
    if downloads_dir:
        roots.extend([
            ("downloads", downloads_dir),
            ("download", downloads_dir),
            ("unduhan", downloads_dir),
        ])
    if vault:
        _, vault_path = vault
        roots.extend([
            ("obsidian vault", vault_path),
            ("obsidian", vault_path),
            ("vault", vault_path),
        ])
    return roots


def _normalize_folder_option_text(text: str) -> str:
    normalized = text.lower()
    normalized = re.sub(r"^\d+\s*[\.\-]*\s*", "", normalized)
    normalized = re.sub(r"[\{\}\(\)\[\]]", " ", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _get_default_parent_folder_options() -> list[dict]:
    default_root = _get_default_folder_root()
    if not default_root:
        return []

    root = Path(default_root)
    if not root.exists():
        return []

    excluded_contains = {
        "github",
        "jin project github",
        "marketing strategy",
        "ide bisnis",
        "other people project",
        "pajak",
        "project belum jelas",
        "freelance",
        "itali",
        "acak acakan",
        "asset",
        "folder epson",
        "icon",
        "nitip sementara",
        "template",
        "on the italy folder",
    }
    generic_tokens = {"project", "folder", "backup"}

    def _sort_key(item: Path) -> tuple[int, int, str]:
        match = re.match(r"^\s*(\d+)", item.name)
        if match:
            return (0, int(match.group(1)), item.name.lower())
        return (1, 0, item.name.lower())

    options: list[dict] = []
    for child in sorted(root.iterdir(), key=_sort_key):
        if not child.is_dir():
            continue

        normalized_full = _normalize_folder_option_text(child.name)
        if not normalized_full:
            continue
        if any(marker in normalized_full for marker in excluded_contains):
            continue

        no_brackets = re.sub(r"\{[^}]*\}|\([^)]*\)", " ", child.name)
        normalized_base = _normalize_folder_option_text(no_brackets) or normalized_full
        tokens = {
            token
            for token in set((normalized_full + " " + normalized_base).split())
            if len(token) > 1 and token not in generic_tokens
        }
        index_match = re.match(r"^\s*(\d{1,3})(?:\s*[\.\-]|\s+)", child.name)
        index_value = index_match.group(1) if index_match else None
        options.append({
            "name": child.name,
            "path": str(child),
            "index": index_value,
            "normalized_full": normalized_full,
            "normalized_base": normalized_base,
            "tokens": tokens,
            "aliases": {normalized_full, normalized_base},
        })

    token_counts: dict[str, int] = {}
    for option in options:
        for token in option["tokens"]:
            token_counts[token] = token_counts.get(token, 0) + 1

    for option in options:
        words = option["normalized_full"].split()
        if len(words) >= 2:
            option["aliases"].add(" ".join(words[:2]))
        if option["normalized_base"]:
            option["aliases"].add(f"{option['normalized_base']} project")
        if option.get("index"):
            option["aliases"].update({
                option["index"],
                f"nomor {option['index']}",
                f"number {option['index']}",
                f"folder {option['index']}",
                f"folder nomor {option['index']}",
            })
        for token in option["tokens"]:
            if token_counts.get(token) == 1:
                option["aliases"].add(token)
    return options


def _resolve_default_parent_folder_choice(text: str) -> dict:
    query = _normalize_folder_command_text(text)
    if not query:
        return {"path": None, "name": None, "matches": []}

    query = _normalize_folder_option_text(query)
    if not query:
        return {"path": None, "name": None, "matches": []}

    options = _get_default_parent_folder_options()
    if not options:
        return {"path": None, "name": None, "matches": []}

    exact_matches = []
    partial_matches = []
    query_tokens = set(query.split())

    for option in options:
        aliases = {alias for alias in option["aliases"] if alias}
        if query in aliases:
            exact_matches.append(option)
            continue
        if any(alias in query for alias in aliases if len(alias) >= 3):
            partial_matches.append(option)
            continue
        if any(set(alias.split()).issubset(query_tokens) for alias in aliases if alias):
            partial_matches.append(option)

    matches = exact_matches or partial_matches
    deduped: list[dict] = []
    seen_paths: set[str] = set()
    for option in matches:
        if option["path"] in seen_paths:
            continue
        deduped.append(option)
        seen_paths.add(option["path"])

    if len(deduped) == 1:
        selected = deduped[0]
        return {"path": selected["path"], "name": selected["name"], "matches": [selected["name"]]}

    return {"path": None, "name": None, "matches": [option["name"] for option in deduped]}


def _normalize_compare_path(path: str | None) -> str:
    if not path:
        return ""
    return os.path.normcase(os.path.normpath(path))


def _default_root_requires_parent_choice(root_path: str | None) -> bool:
    default_root = _get_default_folder_root()
    if not default_root or not root_path:
        return False
    return _normalize_compare_path(root_path) == _normalize_compare_path(default_root)


def _extract_default_parent_folder_choice(text: str) -> dict:
    normalized = _normalize_folder_command_text(text)
    if not normalized:
        return {"path": None, "name": None, "matches": []}

    location_match = re.search(
        r"(?:\bdi\b|\bin\b|\bon\b|\bunder\b|\bat\b|\bdalam\b|\binside\b|\bke\b|\bto\b)\s+(.+)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if location_match:
        return _resolve_default_parent_folder_choice(location_match.group(1).strip())

    looks_like_location_only = not re.search(
        r"\b(?:buat|buatkan|create|make|bernama|named|called|nama|namanya)\b",
        normalized,
        flags=re.IGNORECASE,
    )
    if looks_like_location_only:
        return _resolve_default_parent_folder_choice(normalized)

    return {"path": None, "name": None, "matches": []}


def _build_parent_folder_state(root_path: str | None, *, choice_detail: dict | None = None) -> tuple[bool, list[str], str | None]:
    if not _default_root_requires_parent_choice(root_path):
        return False, [], None

    detail = choice_detail or {"path": None, "name": None, "matches": []}
    matches = detail.get("matches") or []
    if detail.get("path"):
        return False, [], detail.get("name")

    if matches:
        return True, matches, None

    options = [option["name"] for option in _get_default_parent_folder_options()]
    return True, options, None


def _format_parent_folder_choices(options: list[str], *, limit: int = 16) -> str:
    trimmed = [option for option in options[:limit] if option]
    return "; ".join(trimmed)


def _short_parent_folder_label(name: str) -> str:
    cleaned = re.sub(r"^\s*\d{1,3}(?:\s*[\.\-]|\s+)", "", name).strip()
    cleaned = re.sub(r"[\{\}\(\)\[\]]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or name


def _normalize_folder_command_text(text: str) -> str:
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
        r"^(?:the)\s+",
    ]
    suffix_cleaners = [
        r"\s+(?:please|dong|deh|ya|sir|bro|saja|aja|just|now|for me)$",
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


def _detect_project_folder_mode(normalized_text: str) -> bool:
    project_markers = [
        "project baru",
        "proyek baru",
        "new project",
        "folder project",
        "folder proyek",
        "project folder",
        "proyek folder",
        "struktur project",
        "struktur proyek",
        "standard project",
        "project structure",
    ]
    return any(marker in normalized_text for marker in project_markers)


def _detect_regular_folder_mode(normalized_text: str) -> bool:
    regular_markers = [
        "folder biasa",
        "folder normal",
        "regular folder",
        "normal folder",
        "single folder",
        "satu folder",
        "hanya folder",
        "cuma folder",
    ]
    return any(marker in normalized_text for marker in regular_markers)


def _extract_root_path(text: str) -> str | None:
    raw = re.sub(r"\s+", " ", text.strip())
    if not raw:
        return None

    direct_path = re.search(r"([A-Za-z]:\\[^\\/:*?\"<>|\r\n]+(?:\\[^\\/:*?\"<>|\r\n]+)*)", raw)
    if direct_path:
        return direct_path.group(1).strip().rstrip(" .,!?'\"")

    normalized = _normalize_folder_command_text(text)
    for alias, path in sorted(_get_known_folder_roots(), key=lambda item: len(item[0]), reverse=True):
        pattern = rf"(?:\bdi\b|\bin\b|\bon\b|\bunder\b|\bat\b|\bdalam\b|\binside\b|\bke\b|\bto\b)\s+{re.escape(alias)}\b"
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            return path
        if normalized == alias:
            return path
    parent_choice = _extract_default_parent_folder_choice(text)
    if parent_choice.get("path"):
        return parent_choice["path"]
    return None


def _cleanup_folder_name(candidate: str) -> str | None:
    cleaned = candidate.strip().strip(" .,!?'\"")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"^(?:folder|directory|project|proyek)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:bernama|named|called)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:nama|namanya|name(?:d)?)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" .,!?'\"")
    if not cleaned:
        return None
    if cleaned.lower() in {
        "folder", "directory", "project", "proyek", "baru", "new",
        "desktop", "documents", "document", "dokumen", "downloads", "download",
        "obsidian", "vault",
    }:
        return None
    return cleaned


def _extract_folder_name(text: str, *, project_mode: bool, follow_up: bool = False) -> str | None:
    raw = re.sub(r"\s+", " ", text.strip())
    normalized = _normalize_folder_command_text(text)
    if not raw or not normalized:
        return None

    quoted = re.search(r'"([^"]+)"', raw)
    if quoted:
        return _cleanup_folder_name(quoted.group(1))

    named_patterns = [
        r"(?:bernama|named|called)\s+(.+?)(?:\s+(?:di|in|on|under|at|dalam|inside)\b|$)",
        r"(?:folder|directory|project|proyek)\s+bernama\s+(.+?)(?:\s+(?:di|in|on|under|at|dalam|inside)\b|$)",
    ]
    for pattern in named_patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            return _cleanup_folder_name(match.group(1))

    create_patterns = [
        r"^(?:buat(?:kan)?|create|make)\s+(?:folder|directory)(?:\s+(?:baru|new))?\s+(.+?)(?:\s+(?:di|in|on|under|at|dalam|inside)\b|$)",
        r"^(?:buat(?:kan)?|create|make)\s+(?:project|proyek)(?:\s+(?:baru|new))?\s+(.+?)(?:\s+(?:di|in|on|under|at|dalam|inside)\b|$)",
    ]
    for pattern in create_patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            return _cleanup_folder_name(match.group(1))

    if follow_up:
        root_path = _extract_root_path(text)
        leftover = raw
        if root_path:
            for alias, _ in sorted(_get_known_folder_roots(), key=lambda item: len(item[0]), reverse=True):
                leftover = re.sub(
                    rf"(?:\bdi\b|\bin\b|\bon\b|\bunder\b|\bat\b|\bdalam\b|\binside\b)\s+{re.escape(alias)}\b",
                    " ",
                    leftover,
                    flags=re.IGNORECASE,
                )
        leftover = re.sub(r"\s+", " ", leftover).strip(" .,!?'\"")
        if leftover and len(leftover.split()) <= 8:
            return _cleanup_folder_name(leftover)

    if project_mode and not follow_up:
        project_match = re.search(r"(?:project|proyek)(?:\s+(?:baru|new))?\s+(.+)$", raw, flags=re.IGNORECASE)
        if project_match:
            return _cleanup_folder_name(project_match.group(1))

    return None


def extract_folder_request(text: str) -> dict | None:
    """Extract a folder-creation request from a spoken command."""
    normalized = _normalize_folder_command_text(text)
    if not normalized:
        return None

    folder_markers = ["folder", "directory", "project", "proyek"]
    create_markers = ["buat", "buatkan", "create", "make"]
    if not any(marker in normalized for marker in folder_markers):
        return None
    if not any(marker in normalized for marker in create_markers):
        return None

    regular_mode = _detect_regular_folder_mode(normalized)
    project_mode = _detect_project_folder_mode(normalized) or not regular_mode
    parent_choice = _extract_default_parent_folder_choice(text)
    root_path = _extract_root_path(text) or _get_default_folder_root()
    needs_parent_choice, parent_options, selected_parent_name = _build_parent_folder_state(
        root_path,
        choice_detail=parent_choice,
    )
    return {
        "mode": "project" if project_mode else "folder",
        "name": _extract_folder_name(text, project_mode=project_mode),
        "root_path": root_path,
        "needs_parent_choice": needs_parent_choice,
        "parent_options": parent_options,
        "selected_parent_name": selected_parent_name,
        "original_text": text.strip(),
    }


def merge_folder_request_details(request: dict, text: str) -> dict:
    """Fill missing folder request details from a follow-up utterance."""
    merged = dict(request)
    project_mode = merged.get("mode") == "project"
    explicit_root = _extract_root_path(text)
    parent_choice = _extract_default_parent_folder_choice(text)
    location_only_reply = False

    if explicit_root:
        merged["root_path"] = explicit_root
        if parent_choice.get("path"):
            merged["selected_parent_name"] = parent_choice.get("name")
            location_only_reply = not re.search(
                r"\b(?:bernama|named|called|nama|namanya|folder|directory)\b",
                _normalize_folder_command_text(text),
                flags=re.IGNORECASE,
            )
    elif merged.get("needs_parent_choice"):
        if parent_choice.get("path"):
            merged["root_path"] = parent_choice["path"]
            merged["selected_parent_name"] = parent_choice.get("name")
            location_only_reply = True
        elif parent_choice.get("matches"):
            merged["parent_options"] = parent_choice["matches"]
    if not merged.get("root_path"):
        merged["root_path"] = _get_default_folder_root()

    needs_parent_choice, parent_options, selected_parent_name = _build_parent_folder_state(
        merged.get("root_path"),
        choice_detail=parent_choice if parent_choice.get("path") else {"matches": merged.get("parent_options") or []},
    )
    merged["needs_parent_choice"] = needs_parent_choice
    merged["parent_options"] = parent_options
    if selected_parent_name:
        merged["selected_parent_name"] = selected_parent_name

    if not merged.get("name") and not location_only_reply:
        merged["name"] = _extract_folder_name(text, project_mode=project_mode, follow_up=True)
    return merged


def describe_missing_folder_details(request: dict) -> str:
    """Return the next clarification question for a folder request."""
    if request.get("needs_parent_choice"):
        options = request.get("parent_options") or []
        root_path = request.get("root_path") or _get_default_folder_root() or "the default folder root"
        if options:
            if len(options) <= 4:
                choice_text = _format_parent_folder_choices(options)
                if choice_text:
                    return (
                        f"Which parent folder inside {root_path} should I use, sir? "
                        f"You can say one of these: {choice_text}."
                    )
            else:
                sample_labels = [_short_parent_folder_label(option) for option in options[:4]]
                sample_text = "; ".join(label for label in sample_labels if label)
                if sample_text:
                    return (
                        f"Which parent folder inside {root_path} should I use, sir? "
                        f"You can say the folder name, like {sample_text}, or say its number."
                    )
            if options:
                return (
                    f"Which parent folder inside {root_path} should I use, sir? "
                    f"You can say the folder name or its number."
                )
        return f"Which parent folder inside {root_path} should I use, sir?"

    missing_name = not request.get("name")
    missing_root = not request.get("root_path")

    if missing_name and missing_root:
        return "What should I name the folder, and where would you like me to create it, sir?"
    if missing_name:
        return "What should I name the folder, sir?"
    if missing_root:
        return "Where would you like me to create it, sir?"
    return ""


def folder_request_is_complete(request: dict) -> bool:
    return bool(request.get("name") and request.get("root_path") and not request.get("needs_parent_choice"))


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


def _find_obsidian_note(note_key: str) -> tuple[str, str, str] | None:
    """Return (vault_name, vault_path, relative_note_path) for a known Obsidian note."""
    note_spec = OBSIDIAN_NOTE_CANDIDATES.get(note_key)
    if not note_spec:
        return None

    vault = _get_primary_obsidian_vault()
    if not vault:
        return None
    vault_name, vault_path = vault
    vault_root = Path(vault_path)

    for rel_path in note_spec.get("candidates", []):
        if (vault_root / rel_path).exists():
            return vault_name, vault_path, rel_path.replace("\\", "/")

    for fallback_name in note_spec.get("fallback_names", []):
        fallback = next(vault_root.rglob(fallback_name), None)
        if fallback:
            rel = fallback.relative_to(vault_root).as_posix()
            return vault_name, vault_path, rel

    return None


def _find_obsidian_jarvis_note() -> tuple[str, str, str] | None:
    """Return (vault_name, vault_path, relative_note_path) for the JARVIS launcher note."""
    return _find_obsidian_note("jarvis")


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
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
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
    return await _open_named_obsidian_note(
        "jarvis",
        success_confirmation="Opened JARVIS in Obsidian and maximized it, sir.",
    )


async def _open_named_obsidian_note(note_key: str, *, success_confirmation: str) -> dict:
    """Open a known Obsidian note and maximize the Obsidian window on Windows."""
    note_info = _find_obsidian_note(note_key)
    if not note_info:
        missing_confirmation = OBSIDIAN_NOTE_CANDIDATES.get(note_key, {}).get(
            "missing_confirmation",
            "I couldn't find that note in your Obsidian vault, sir.",
        )
        return {
            "success": False,
            "confirmation": missing_confirmation,
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
        "confirmation": success_confirmation
        if success
        else "I had trouble opening that note in Obsidian, sir.",
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


def extract_codex_request(text: str) -> dict | None:
    """Extract an Ask Codex request from a spoken command."""
    original = re.sub(r"\s+", " ", text.strip())
    if not original:
        return None

    normalized = original.lower()
    normalized = re.sub(r"[!?]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    prefix_cleaners = [
        r"^hey\s+jarvis\b[,\s]*",
        r"^jarvis\b[,\s]*",
        r"^(?:can you|could you|would you|will you|please|tolong|mohon|coba|bisa(?:kah)?\s+(?:tolong\s+)?)\s+",
    ]
    changed = True
    while changed:
        changed = False
        for pattern in prefix_cleaners:
            updated = re.sub(pattern, "", normalized, flags=re.IGNORECASE).strip()
            if updated != normalized:
                normalized = updated
                changed = True

    if not normalized:
        return None

    new_thread_markers = (
        "new chat",
        "chat baru",
        "new thread",
        "thread baru",
        "fresh chat",
        "fresh thread",
    )
    new_thread = any(marker in normalized for marker in new_thread_markers)
    for marker in new_thread_markers:
        normalized = normalized.replace(marker, " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return None

    patterns = [
        r"^(?:ask|tell|send|suruh|minta|tanyakan|kirim)\s+(?:ke\s+)?codex\s+(?:to\s+)?(.+)$",
        r"^(?:ask\s+the\s+codex|ask\s+openai\s+codex)\s+(?:to\s+)?(.+)$",
        r"^(?:open|buka)\s+codex\s+(?:dan\s+)?(?:ketik|tulis|ask|send)\s+(.+)$",
    ]

    for pattern in patterns:
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        prompt = match.group(1).strip(" .,!?'\"")
        prompt = re.sub(r"\s+", " ", prompt)
        if not prompt:
            return None
        return {
            "prompt": prompt,
            "new_thread": new_thread,
        }

    return None


def extract_codex_prompt(text: str) -> str | None:
    """Backward-compatible prompt extractor for older callers."""
    request = extract_codex_request(text)
    return request["prompt"] if request else None


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


def _extract_device_target(normalized: str) -> tuple[str, str | None]:
    """Extract a device/room target from the text. Returns (cleaned_text, target_name)."""
    # Match "lampu di kamar" / "light in bedroom" / "lampu kamar tidur" etc.
    target_match = re.search(
        r"\b(?:di|at|in|for)\s+(kamar(?:\s+tidur)?|ruang\s+tamu|dapur|kitchen|living\s*room|"
        r"bedroom|office|kantor|ruang\s+kerja|kamar\s+mandi|bathroom)\b",
        normalized,
        flags=re.IGNORECASE,
    )
    if target_match:
        target_name = target_match.group(1).strip()
        cleaned = normalized[:target_match.start()] + normalized[target_match.end():]
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned, target_name

    # Match device alias names from config
    for alias_name in _WIZ_DEVICE_NAME_MAP:
        if alias_name in normalized:
            cleaned = normalized.replace(alias_name, "").strip()
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            return cleaned, alias_name

    return normalized, None


def extract_wiz_request(text: str, _force: bool = False) -> dict | None:
    """Extract WiZ lighting commands from short local-control requests.

    _force=True skips the heuristic _looks_like_wiz_request guard.
    Use this when the LLM has already tagged the intent as [ACTION:WIZ].
    """
    normalized = _normalize_wiz_command_text(text)
    if not normalized:
        return None
    if not _force and not _looks_like_wiz_request(normalized):
        return None

    # Extract optional device/room target (e.g. "lampu di kamar", "bedroom light")
    normalized_for_parse, device_target = _extract_device_target(normalized)

    def _with_target(result: dict) -> dict:
        if device_target:
            result["target_device"] = device_target
        return result

    # ── Compound detection: color/temp + brightness in same command ──────────
    # e.g. "warna putih dan kecerahan 100%"  /  "set color white brightness 80"
    # Extract brightness value from text (after normalization, % is stripped)
    _b_match = re.search(
        r"(?:kecerahan|brightness)\s+ke\s+(\d{1,3})"
        r"|(?:kecerahan|brightness)\s+(\d{1,3})"
        r"|ke\s+(\d{1,3})\s+(?:persen|percent)"
        r"|(?:jadi|to|ke)\s+(\d{1,3})\s*(?:persen|percent)?(?:\s|$)",
        normalized_for_parse, re.IGNORECASE,
    )
    _compound_brightness: int | None = None
    if _b_match:
        _raw = next((g for g in _b_match.groups() if g is not None), None)
        if _raw is not None:
            _compound_brightness = max(0, min(100, int(_raw)))

    # Extract color/temperature from text (longest match first to prefer specific presets)
    _compound_color: dict | None = None
    for _phrase, _preset in sorted(_WIZ_WHITE_MODE_PRESETS.items(), key=lambda x: len(x[0]), reverse=True):
        if _phrase in normalized_for_parse:
            _compound_color = {
                "kind": "temperature",
                "temperature": _preset["temperature"],
                "label": _preset["label"],
            }
            break
    if _compound_color is None:
        for _kw, (_label, _hex) in _WIZ_COLOR_KEYWORDS.items():
            if re.search(rf"\b{re.escape(_kw)}\b", normalized_for_parse):
                _compound_color = {"kind": "color", "hex": _hex, "label": _label}
                break

    # If BOTH color AND brightness found in same command → merge and return early
    if _compound_color and _compound_brightness is not None:
        _merged = dict(_compound_color)
        _merged["brightness"] = _compound_brightness
        return _with_target(_merged)

    # If only color found and we're in forced (LLM) mode → return it directly
    # (avoids the strict device-context guard in the white-mode block below)
    if _compound_color and _force:
        return _with_target(_compound_color)

    if any(phrase in normalized for phrase in (
        # English
        "scan wiz", "scan wiz devices", "scan wiz lights",
        "discover wiz", "discover wiz devices", "discover lampu wiz",
        "scan lights", "scan lamps", "scan the lights",
        "check lights", "check lamps", "check lamp status", "check light status",
        "light status", "lamp status", "status lights", "status lamps",
        "list lights", "list lamps", "show lights", "show lamps",
        "how many lights", "how many lamps",
        # Indonesian
        "scan lampu wiz", "scan lampu",
        "cari lampu", "cari lampu wiz",
        "cek lampu", "cek status lampu", "cek lampu wiz",
        "periksa lampu", "lihat lampu", "lihat status lampu",
        "status lampu", "info lampu", "pantau lampu",
        "berapa lampu", "lampu berapa", "ada berapa lampu",
        "berapa banyak lampu", "berapa lampu yang",
        "lampu yang online", "lampu yang nyala", "lampu yang terhubung",
        "lampu yang aktif", "lampu yang terdeteksi",
        "ada lampu apa", "lampu apa saja", "lampu apa yang",
        "temukan lampu",
    )) or _wiz_matches_any_pattern(normalized, (
        r"^(?:scan|discover|find)\s+(?:the\s+)?(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)(?:\s+.+)?$",
        r"^(?:scan|discover|find|cek|cari|periksa|lihat|pantau|temukan)\s+(?:the\s+|status\s+)?(?:lights?|lamps?|lampu(?:nya)?)(?:\s+(?:yang\s+)?(?:terhubung|connected|online|di jaringan|on the network|aktif|nyala))?$",
        r"^(?:cek|lihat|periksa|pantau|info|status)\s+(?:status\s+)?(?:lampu(?:nya)?|lights?|lamps?)(?:\s+.+)?$",
        r"^(?:status|info)\s+(?:lampu(?:nya)?|lights?|lamps?)$",
        r"^(?:ada\s+)?(?:berapa|how\s+many)\s+(?:banyak\s+)?(?:lampu(?:nya)?|lights?|lamps?)(?:\s+.+)?$",
        r"^(?:lampu(?:nya)?|lights?|lamps?)\s+(?:berapa|yang\s+(?:online|nyala|terhubung|connected|aktif|mana|ada|terdeteksi))(?:\s+.+)?$",
        r"^(?:ada\s+)?(?:lampu(?:nya)?|lights?|lamps?)\s+(?:apa\s+(?:saja|yang)|mana\s+saja)(?:\s+.+)?$",
    )):
        return _with_target({"kind": "discover"})

    if (
        any(phrase in normalized for phrase in (
            "open wiz controller",
            "buka wiz controller",
            "open the wiz controller",
            "buka controller wiz",
            "open wiz dashboard",
            "buka wiz dashboard",
            "show wiz dashboard",
            "launch wiz controller",
        ))
        or normalized in {"wiz dashboard", "wiz controller", "light controller", "lamp controller"}
        or _wiz_matches_any_pattern(normalized, (
            r"^(?:open|buka|launch|show)\s+(?:the\s+)?(?:light|lamp|lighting|smart light)\s+controller$",
            r"^(?:buka|open|launch|show)\s+(?:dashboard|kontrol|controller)\s+(?:lampu|lights?|lamps?)$",
        ))
    ):
        return _with_target({"kind": "dashboard"})

    brightness_match = re.search(
        r"(?:brightness|kecerahan)(?:\s+(?:of\s+)?(?:the\s+)?)?(?:(?:lampu(?:nya)?\s+)?(?:wiz|lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?))?(?:\s+\w+){0,4}?\s+(?:to\s+|ke\s+)?(\d{1,3})\s*(?:%|persen|percent)?\b",
        normalized,
        flags=re.IGNORECASE,
    ) or re.search(
        r"(?:set|atur|ubah)\s+(?:the\s+)?(?:brightness|kecerahan)\s+(?:of\s+)?(?:the\s+)?(?:(?:lampu(?:nya)?\s+)?(?:wiz|lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?))?(?:\s+\w+){0,4}?\s+(?:to\s+|ke\s+)?(\d{1,3})\s*(?:%|persen|percent)?\b",
        normalized,
        flags=re.IGNORECASE,
    ) or re.search(
        r"(?:set|atur|ubah|make)\s+(?:the\s+)?(?:(?:lampu(?:nya)?\s+)?(?:wiz|lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?))(?:\s+\w+){0,4}?\s+(?:to\s+|ke\s+)?(\d{1,3})\s*(?:%|persen|percent)\b",
        normalized,
        flags=re.IGNORECASE,
    ) or re.match(
        r"^(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)\s+(\d{1,3})\s*(?:%|persen|percent)?$",
        normalized,
        flags=re.IGNORECASE,
    )
    if brightness_match:
        brightness = max(0, min(100, int(brightness_match.group(1))))
        return _with_target({"kind": "brightness", "value": brightness})

    for phrase, preset in _WIZ_WHITE_MODE_PRESETS.items():
        if phrase in normalized:
            if not (
                _wiz_has_device_context(normalized)
                or _wiz_matches_any_pattern(normalized, (
                    r"^(?:set|make|change|atur|ubah)\s+(?:the\s+)?(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)\s+(?:to\s+|ke\s+)?",
                    r"^(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)\s+",
                ))
            ):
                continue
            return _with_target({
                "kind": "temperature",
                "temperature": preset["temperature"],
                "brightness": preset["brightness"],
                "label": preset["label"],
            })

    for keyword, (label, hex_color) in _WIZ_COLOR_KEYWORDS.items():
        if re.search(rf"\b{re.escape(keyword)}\b", normalized):
            if (
                any(token in normalized for token in ("warna", "color", "colours", "colour", "jadi", "menjadi", "ke", "to", "turn", "warnai"))
                or _wiz_matches_any_pattern(normalized, (
                    rf"^(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)\s+{re.escape(keyword)}$",
                    rf"^(?:set|make|change|atur|ubah)\s+(?:the\s+)?(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)\s+(?:to\s+|ke\s+)?{re.escape(keyword)}$",
                    rf"^(?:buat|bikin|jadikan|ganti)\s+(?:lampu(?:nya)?|lights?|lamps?)\s+(?:jadi\s+|menjadi\s+|to\s+|ke\s+)?{re.escape(keyword)}$",
                    rf"^(?:lampu(?:nya)?|lights?|lamps?)\s+(?:jadi\s+|menjadi\s+)?{re.escape(keyword)}$",
                    rf"^(?:turn|make|change|colour|color|warnai)\s+(?:the\s+)?(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)\s+(?:to\s+|jadi\s+|menjadi\s+|ke\s+)?{re.escape(keyword)}$",
                    rf"^(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?)\s+(?:warna\s+|color\s+|colour\s+)?{re.escape(keyword)}$",
                ))
            ):
                return _with_target({"kind": "color", "hex": hex_color, "label": label})

    # --- Scene presets (checked before hex fallback) ---
    for phrase, preset in _WIZ_SCENE_PRESETS.items():
        if phrase in normalized:
            result: dict = {"kind": "scene", "label": preset["label"]}
            if "hex" in preset:
                result["hex"] = preset["hex"]
                result["brightness"] = preset.get("brightness", 100)
            else:
                result["temperature"] = preset["temperature"]
                result["brightness"] = preset.get("brightness")
            return _with_target(result)
    # Also match "scene <name>" / "mode <name>" patterns dynamically
    scene_match = re.match(
        r"^(?:scene|mode|set scene|set mode|atur mode|ganti mode|switch to)\s+(.+)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if scene_match:
        scene_name = scene_match.group(1).strip()
        if scene_name in _WIZ_SCENE_PRESETS:
            preset = _WIZ_SCENE_PRESETS[scene_name]
            result = {"kind": "scene", "label": preset["label"]}
            if "hex" in preset:
                result["hex"] = preset["hex"]
                result["brightness"] = preset.get("brightness", 100)
            else:
                result["temperature"] = preset["temperature"]
                result["brightness"] = preset.get("brightness")
            return _with_target(result)

    hex_match = re.search(r"#([0-9a-f]{6})\b", normalized, flags=re.IGNORECASE)
    if hex_match:
        return _with_target({"kind": "color", "hex": f"#{hex_match.group(1).lower()}", "label": f"#{hex_match.group(1).lower()}"})

    if any(phrase in normalized for phrase in (
        "nyalakan lampu wiz",
        "turn on wiz",
        "turn on the wiz",
        "turn on wiz lights",
        "lampu wiz on",
        "wiz lights on",
    )) or _wiz_matches_any_pattern(normalized, (
        r"^(?:turn|switch)\s+on\s+(?:the\s+)?(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?|house lights)$",
        r"^(?:nyalakan|hidupkan)\s+(?:lampu(?:nya)?|lights?|lamps?|smart lights?)$",
        r"^(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?|house lights)\s+on$",
        r"^(?:lampu(?:nya)?)\s+nyala$",
    )):
        return _with_target({"kind": "power", "state": "on"})

    if any(phrase in normalized for phrase in (
        "matikan lampu wiz",
        "turn off wiz",
        "turn off the wiz",
        "turn off wiz lights",
        "lampu wiz off",
        "wiz lights off",
    )) or _wiz_matches_any_pattern(normalized, (
        r"^(?:turn|switch)\s+off\s+(?:the\s+)?(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?|house lights)$",
        r"^(?:matikan|padamkan)\s+(?:lampu(?:nya)?|lights?|lamps?|smart lights?)$",
        r"^(?:lights?|lamps?|lampu(?:nya)?|smart lights?|smart lamps?|house lights)\s+off$",
        r"^(?:lampu(?:nya)?)\s+mati$",
    )):
        return _with_target({"kind": "power", "state": "off"})

    return None


def extract_air_fan_request(text: str, _force: bool = False) -> dict | None:
    """Extract local air cleaner fan commands from short control requests."""
    normalized = _normalize_air_fan_command_text(text)
    if not normalized:
        return None
    if not _force and not _looks_like_air_fan_request(normalized):
        return None

    if (
        any(phrase in normalized for phrase in (
            "open air control",
            "buka air control",
            "open fan control",
            "buka fan control",
            "open fan controller",
            "buka kontrol fan",
            "buka kontrol kipas",
            "show air control",
            "launch air control",
        ))
        or normalized in {"air control", "fan control", "fan controller", "kontrol fan", "kontrol kipas"}
        or _air_fan_matches_any_pattern(normalized, (
            r"^(?:open|buka|launch|show)\s+(?:the\s+)?(?:air control|fan control|fan controller|controller fan|kontrol fan|kontrol kipas|dashboard fan)$",
        ))
    ):
        return {"kind": "dashboard"}

    if (
        any(phrase in normalized for phrase in (
            "fan status",
            "status fan",
            "status kipas",
            "cek status fan",
            "cek status kipas",
            "check fan status",
            "air control status",
            "status air control",
            "fan speed sekarang",
            "fan speed saat ini",
            "current fan speed",
            "berapa speed fan",
            "berapa speed kipas",
            "berapa rpm fan",
            "fan runtime",
        ))
        or _air_fan_matches_any_pattern(normalized, (
            r"^(?:cek|lihat|status|show|check)\s+(?:status\s+)?(?:fan|kipas|blower|air control|air cleaner|air purifier)$",
            r"^(?:fan|kipas|blower|air control|air cleaner|air purifier)\s+status$",
            r"^(?:fan|kipas|blower)\s+(?:speed|rpm)$",
            r"^(?:status|speed|rpm)$",
        ))
    ):
        return {"kind": "status"}

    profile_aliases = {
        "quiet": "quiet",
        "normal": "normal",
        "dynamic": "dynamic",
    }
    profile_match = re.match(
        r"^(?:set|atur|ubah|ganti|make|switch|pakai|use)\s+(?:fan\s+)?(?:profile|mode)?\s*(quiet|normal|dynamic)\b",
        normalized,
        flags=re.IGNORECASE,
    ) or re.match(
        r"^(quiet|normal|dynamic)(?:\s+(?:profile|mode|fan|kipas|blower))?$",
        normalized,
        flags=re.IGNORECASE,
    ) or re.match(
        r"^(?:fan|kipas|blower)\s+(quiet|normal|dynamic)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if profile_match:
        profile_name = profile_aliases.get(profile_match.group(1).lower())
        if profile_name:
            return {"kind": "profile", "profile": profile_name}

    speed_match = re.match(
        r"^(?:set|atur|ubah|ganti|make)\s+(?:fan|kipas|blower)(?:\s+(?:manual\s+)?)?(?:speed)?\s*(?:to|ke|jadi)?\s*(\d{1,3})\s*(?:%|percent|persen)?$",
        normalized,
        flags=re.IGNORECASE,
    ) or re.match(
        r"^(?:set|atur|ubah|ganti|make)\s+(?:speed|manual speed)\s+(?:fan|kipas|blower|air control)?\s*(?:to|ke|jadi)?\s*(\d{1,3})\s*(?:%|percent|persen)?$",
        normalized,
        flags=re.IGNORECASE,
    ) or re.match(
        r"^(?:fan|kipas|blower)(?:\s+(?:manual\s+)?)?(?:speed)?\s*(?:to|ke|jadi)?\s*(\d{1,3})\s*(?:%|percent|persen)?$",
        normalized,
        flags=re.IGNORECASE,
    ) or re.match(
        r"^(?:speed|manual speed)\s*(?:fan|kipas|blower)?\s*(?:to|ke|jadi)?\s*(\d{1,3})\s*(?:%|percent|persen)?$",
        normalized,
        flags=re.IGNORECASE,
    ) or re.match(
        r"^(\d{1,3})\s*(?:%|percent|persen)?\s+(?:fan|kipas|blower)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if speed_match:
        return {"kind": "speed", "value": int(speed_match.group(1))}

    if _force:
        if normalized in profile_aliases:
            return {"kind": "profile", "profile": profile_aliases[normalized]}
        if re.fullmatch(r"\d{1,3}(?:\s*(?:%|percent|persen))?", normalized, flags=re.IGNORECASE):
            return {"kind": "speed", "value": int(re.sub(r"[^0-9]", "", normalized))}
        if normalized in {"status", "speed", "rpm"}:
            return {"kind": "status"}

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


async def _start_windows_process(
    file_path: str,
    arguments: list[str] | None = None,
    *,
    working_directory: str | None = None,
    window_style: str | None = None,
) -> tuple[bool, str]:
    """Launch a Windows process or protocol handler in a visible way."""
    script = [
        "$ErrorActionPreference = 'Stop'",
        f"$target = {_ps_quote(file_path)}",
    ]
    start_args: list[str] = ["-FilePath $target"]
    if arguments:
        arg_list = ", ".join(_ps_quote(arg) for arg in arguments)
        script.append(f"$argsList = @({arg_list})")
        start_args.append("-ArgumentList $argsList")
    if working_directory:
        script.append(f"$workingDirectory = {_ps_quote(working_directory)}")
        start_args.append("-WorkingDirectory $workingDirectory")
    if window_style:
        script.append(f"$windowStyle = {_ps_quote(window_style)}")
        start_args.append("-WindowStyle $windowStyle")
    script.append(f"Start-Process {' '.join(start_args)}")

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
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
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
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
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


def _get_wiz_http_client() -> httpx.AsyncClient:
    """Return a reusable httpx client for WiZ controller communication."""
    global _WIZ_HTTP_CLIENT
    if _WIZ_HTTP_CLIENT is None or _WIZ_HTTP_CLIENT.is_closed:
        _WIZ_HTTP_CLIENT = httpx.AsyncClient(
            timeout=WIZ_CONTROLLER_HTTP_TIMEOUT,
            trust_env=False,
            base_url=WIZ_CONTROLLER_URL,
        )
    return _WIZ_HTTP_CLIENT


async def _wiz_http_request(
    method: str,
    path: str,
    payload: dict | None = None,
) -> tuple[bool, dict | None, str]:
    try:
        client = _get_wiz_http_client()
        response = await client.request(method, path, json=payload)
    except httpx.HTTPError as exc:
        return False, None, str(exc)

    try:
        data = response.json()
    except ValueError:
        data = None

    if response.status_code >= 400:
        error_message = ""
        if isinstance(data, dict):
            error_message = str(data.get("error") or data.get("message") or "").strip()
        if not error_message:
            error_message = f"HTTP {response.status_code}"
        return False, data, error_message

    return True, data, ""


async def _wiz_controller_is_live() -> bool:
    ok, payload, _ = await _wiz_http_request("GET", "/health")
    return ok and isinstance(payload, dict) and payload.get("success") is True


async def _start_wiz_controller_launcher_silent() -> tuple[bool, str]:
    if not WIZ_CONTROLLER_LAUNCHER.exists():
        return False, "WiZ launcher batch file was not found."
    command = f'set "WIZ_SKIP_BROWSER=1" && call "{WIZ_CONTROLLER_LAUNCHER}"'
    return await _start_windows_process(
        "cmd.exe",
        ["/c", command],
        working_directory=str(WIZ_CONTROLLER_ROOT),
        window_style="Hidden",
    )


async def _ensure_wiz_controller_running() -> tuple[bool, bool, str]:
    async with _wiz_start_lock():
        if await _wiz_controller_is_live():
            return True, False, ""

        if not IS_WINDOWS:
            return False, False, _wiz_response(
                "The local light controller is only configured for Windows on this machine.",
                "Controller lampu lokal saat ini hanya dikonfigurasi untuk Windows di mesin ini.",
            )

        launch_attempts: list[tuple[str, tuple[bool, str]]] = []
        launch_attempts.append(("silent launcher", await _start_wiz_controller_launcher_silent()))
        for delay in (1.0, 1.5, 2.0, 2.5, 3.0):
            await asyncio.sleep(delay)
            if await _wiz_controller_is_live():
                return True, True, ""

        launch_errors = [detail for _, (success, detail) in launch_attempts if not success and detail]
        return False, True, (
            _wiz_response(
                "I tried to start the local light backend, but it still never came online. ",
                "Aku sudah mencoba menyalakan backend lampu lokal, tapi servicenya belum online. ",
            )
            + (f"{' | '.join(launch_errors)} " if launch_errors else "")
            + _wiz_response(
                f"Check {WIZ_CONTROLLER_START_LOG} and {WIZ_CONTROLLER_ERROR_LOG} for startup details.",
                f"Cek {WIZ_CONTROLLER_START_LOG} dan {WIZ_CONTROLLER_ERROR_LOG} untuk detail startup.",
            )
        )


async def _get_wiz_devices(refresh: bool = True) -> tuple[bool, list[dict], str]:
    suffix = "?refresh=1" if refresh else "?refresh=0"
    ok, payload, error = await _wiz_http_request("GET", f"/devices{suffix}")
    if not ok:
        return False, [], error
    devices = payload.get("devices") if isinstance(payload, dict) else []
    _cache_wiz_devices(devices or [])
    return True, devices or [], ""


async def _discover_wiz_devices() -> tuple[bool, list[dict], str]:
    ok, payload, error = await _wiz_http_request("POST", "/discover", {})
    if not ok:
        return False, [], error
    devices = payload.get("devices") if isinstance(payload, dict) else []
    _cache_wiz_devices(devices or [])
    _cache_wiz_discovery_snapshot(devices or [])
    return True, devices or [], ""


async def _discover_wiz_devices_with_retry() -> tuple[bool, list[dict], str]:
    last_error = ""
    for delay in (0.0, 0.8):
        if delay:
            await asyncio.sleep(delay)
        ok, devices, error = await _discover_wiz_devices()
        if ok:
            return True, devices, ""
        last_error = error or last_error

    ok, devices, error = await _get_wiz_devices(refresh=True)
    if ok:
        _cache_wiz_discovery_snapshot(devices or [])
        return True, devices, ""
    return False, [], error or last_error


def _filter_devices_by_target(devices: list[dict], target_name: str | None) -> list[dict]:
    """Filter devices by friendly name, IP, or alias. Returns all if target_name is None."""
    if not target_name:
        return devices

    target_lower = target_name.lower().strip()

    # Check device alias map first
    if target_lower in _WIZ_DEVICE_NAME_MAP:
        target_ip = _WIZ_DEVICE_NAME_MAP[target_lower]
        matched = [d for d in devices if d.get("ip") == target_ip]
        if matched:
            return matched

    # Match by IP directly
    matched = [d for d in devices if d.get("ip") == target_lower]
    if matched:
        return matched

    # Match by device name (partial, case-insensitive)
    matched = [
        d for d in devices
        if target_lower in (d.get("name") or "").lower()
        or target_lower in (d.get("mac") or "").lower()
    ]
    if matched:
        return matched

    return []


def _select_online_wiz_devices(devices: list[dict], target_name: str | None = None) -> tuple[list[dict], bool]:
    online_devices = [device for device in (devices or []) if device.get("online")]
    if not online_devices:
        return [], False
    if not target_name:
        return online_devices, True
    return _filter_devices_by_target(online_devices, target_name), True


async def _get_online_wiz_devices(target: str | None = None) -> tuple[bool, list[dict], str]:
    ok, _, error = await _ensure_wiz_controller_running()
    if not ok:
        return False, [], error

    latest_devices: list[dict] = []
    saw_online_devices = False

    cached_devices = _get_cached_wiz_devices(_WIZ_DEVICE_CACHE_TTL_SECONDS)
    if cached_devices:
        latest_devices = cached_devices
        selected_devices, had_online = _select_online_wiz_devices(cached_devices, target)
        saw_online_devices = saw_online_devices or had_online
        if selected_devices:
            return True, selected_devices, ""

    for refresh in (False, True):
        ok, devices, error = await _get_wiz_devices(refresh=refresh)
        if not ok:
            if refresh:
                return False, [], error
            continue
        latest_devices = devices
        selected_devices, had_online = _select_online_wiz_devices(devices, target)
        saw_online_devices = saw_online_devices or had_online
        if selected_devices:
            return True, selected_devices, ""

    ok, devices, error = await _discover_wiz_devices()
    if ok:
        latest_devices = devices
        selected_devices, had_online = _select_online_wiz_devices(devices, target)
        saw_online_devices = saw_online_devices or had_online
        if selected_devices:
            return True, selected_devices, ""
    elif error:
        return False, [], error

    if target and saw_online_devices:
        return False, [], _wiz_response(
            f"I found lights online, but none matched '{target}', sir.",
            f"Ada lampu yang online, tapi belum ada yang cocok dengan '{target}'.",
        )

    if latest_devices:
        return False, [], _wiz_response(
            "I can reach the light controller, but none of the lights appear online right now, sir.",
            "Controller lampunya bisa dijangkau, tapi belum ada lampu yang online sekarang.",
        )
    return False, [], _wiz_response(
        "I scanned the local network and didn't find any connected lights, sir.",
        "Aku sudah scan jaringan lokal, tapi belum menemukan lampu yang terhubung.",
    )


async def _apply_wiz_group_command(
    request_builder,
    success_text: str,
    target: str | None = None,
) -> dict:
    ok, devices, error = await _get_online_wiz_devices(target=target)
    if not ok:
        return {"success": False, "confirmation": error}

    success_count = 0
    failure_count = 0
    last_error = ""

    for device in devices:
        path, payload = request_builder(device)
        req_ok, payload_json, req_error = await _wiz_http_request("POST", path, payload)
        if req_ok and (not isinstance(payload_json, dict) or payload_json.get("success", True)):
            success_count += 1
        else:
            failure_count += 1
            last_error = req_error or (payload_json.get("error") if isinstance(payload_json, dict) else "") or last_error

    if success_count <= 0:
        confirmation = _wiz_response(
            "The lights didn't take that command, sir.",
            "Perintahnya belum berhasil diterapkan ke lampu.",
        )
        if last_error:
            confirmation = f"{confirmation[:-1]}: {last_error}"
        return {"success": False, "confirmation": confirmation}

    confirmation = success_text.format(count_phrase=_wiz_count_phrase(success_count)) + _wiz_failure_tail(failure_count)
    return {"success": failure_count == 0, "confirmation": confirmation}


async def warm_wiz_controller() -> None:
    try:
        ok, launched, error = await _ensure_wiz_controller_running()
        if ok:
            cache_ok, devices, cache_error = await _get_wiz_devices(refresh=False)
            online_count = len([device for device in devices if device.get("online")]) if cache_ok else 0
            if not online_count:
                discover_ok, discovered, discover_error = await _discover_wiz_devices()
                if discover_ok:
                    online_count = len([device for device in discovered if device.get("online")])
                elif discover_error:
                    log.info(f"WiZ warm-up discovery skipped: {discover_error}")
            if launched:
                log.info(f"WiZ controller warmed and launched successfully with {online_count} online device(s)")
            else:
                log.info(f"WiZ controller already online during warm-up with {online_count} online device(s)")
            if cache_error:
                log.debug(f"WiZ warm-up cache probe note: {cache_error}")
        elif error:
            log.warning(f"WiZ warm-up failed: {error}")
    except Exception as exc:
        log.warning(f"WiZ warm-up exception: {exc}")


def _get_air_fan_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=AIR_FAN_HTTP_TIMEOUT,
        trust_env=False,
        base_url=AIR_FAN_BASE_URL,
        headers={"Connection": "close"},
    )


def _parse_air_fan_payload(text: str) -> dict[str, str]:
    payload = (text or "").strip()
    if not payload:
        raise ValueError("Device returned an empty payload.")
    if payload.startswith("#"):
        payload = payload[1:]
    if payload.endswith("&"):
        payload = payload[:-1]

    result: dict[str, str] = {}
    for pair in payload.split(";"):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        key, value = pair.split(":", 1)
        result[key.strip()] = value.strip()
    if not result:
        raise ValueError("Device returned an unreadable payload.")
    return result


async def _air_fan_http_request(
    method: str,
    path: str,
    payload: dict[str, str] | None = None,
) -> tuple[bool, dict[str, str] | None, str]:
    try:
        async with _get_air_fan_http_client() as client:
            response = await client.request(
                method,
                path,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"} if payload else None,
            )
    except httpx.HTTPError as exc:
        return False, None, str(exc)

    if response.status_code >= 400:
        return False, None, f"HTTP {response.status_code}"

    raw_text = response.text.strip()
    if not raw_text:
        return True, {}, ""

    try:
        return True, _parse_air_fan_payload(raw_text), ""
    except ValueError as exc:
        return False, None, str(exc)


def _air_fan_profile_name(code: str | int | None) -> str:
    return _AIR_FAN_PROFILE_NAMES.get(str(code), "Unknown")


def _air_fan_status_name(code: str | int | None) -> str:
    return _AIR_FAN_STATUS_NAMES.get(str(code), "Unknown")


def _air_fan_allowed_speed_phrase() -> str:
    return ", ".join(str(value) for value in _AIR_FAN_ALLOWED_SPEEDS)


def _air_fan_ui_url() -> str:
    suffix = AIR_FAN_UI_PATH if AIR_FAN_UI_PATH.startswith("/") else f"/{AIR_FAN_UI_PATH}"
    return f"{AIR_FAN_BASE_URL}{suffix}"


async def _get_air_fan_state() -> tuple[bool, dict | None, str]:
    ok_cfg, cfg, cfg_error = await _air_fan_http_request("GET", "/fan-cfg")
    if not ok_cfg:
        return False, None, cfg_error

    ok_runtime, runtime, runtime_error = await _air_fan_http_request("GET", "/fan-runtime")
    if not ok_runtime:
        return False, None, runtime_error

    cfg = cfg or {}
    runtime = runtime or {}
    profile_code = cfg.get("fanProfile")
    status_code = runtime.get("fanStatus")

    return True, {
        "fan_power": int(cfg["fanPower"]) if cfg.get("fanPower", "").isdigit() else None,
        "manual_enabled": cfg.get("fanManualen") == "1",
        "profile_code": int(profile_code) if str(profile_code).isdigit() else None,
        "profile_name": _air_fan_profile_name(profile_code),
        "set_speed_percent": int(runtime["fanSetspeed"]) if runtime.get("fanSetspeed", "").isdigit() else None,
        "actual_speed_rpm": int(runtime["fanActspeed"]) if runtime.get("fanActspeed", "").isdigit() else None,
        "fan_status_code": int(status_code) if str(status_code).isdigit() else None,
        "fan_status_name": _air_fan_status_name(status_code),
    }, ""


def _air_fan_status_summary(state: dict) -> tuple[str, str]:
    status_name = str(state.get("fan_status_name") or "Unknown")
    actual_rpm = state.get("actual_speed_rpm")
    set_speed = state.get("set_speed_percent")
    profile_name = str(state.get("profile_name") or "Unknown")
    manual_enabled = bool(state.get("manual_enabled"))

    status_en = status_name.lower()
    status_id = {
        "Connected": "terhubung",
        "Disconnected": "terputus",
        "Fault": "fault",
        "Unknown": "tidak diketahui",
    }.get(status_name, status_name.lower())
    profile_id = {
        "Quiet": "quiet",
        "Normal": "normal",
        "Dynamic": "dynamic",
        "Manual": "manual",
        "Unknown": "tidak diketahui",
    }.get(profile_name, profile_name.lower())

    if manual_enabled:
        english = f"Fan is {status_en} in manual mode at {set_speed or 0} percent"
        indonesian = f"Fan {status_id} dalam mode manual di {set_speed or 0} persen"
    else:
        english = f"Fan is {status_en} on the {profile_name.lower()} profile"
        indonesian = f"Fan {status_id} di profile {profile_id}"

    if actual_rpm is not None:
        english += f", around {actual_rpm} RPM"
        indonesian += f", sekitar {actual_rpm} RPM"

    return english, indonesian


async def control_air_fan(request: dict) -> dict:
    """Control the local air cleaner fan over its built-in LAN HTTP interface."""
    kind = str((request or {}).get("kind", "")).strip().lower()
    if not kind:
        return {
            "success": False,
            "confirmation": _air_fan_response(
                "Tell me what to do with the fan, sir.",
                "Beri tahu aku mau diapakan fan-nya.",
            ),
        }

    if kind == "dashboard":
        browser_result = await open_browser(_air_fan_ui_url(), "chrome")
        if not browser_result["success"]:
            return {
                "success": False,
                "confirmation": _air_fan_response(
                    "I can reach the fan controller, but I couldn't open the dashboard, sir.",
                    "Controller fan-nya bisa dijangkau, tapi dashboard-nya belum berhasil kubuka.",
                ),
            }
        return {
            "success": True,
            "confirmation": _air_fan_response(
                "Opened the air control dashboard, sir.",
                "Dashboard air control sudah kubuka.",
            ),
        }

    if kind == "status":
        ok, state, error = await _get_air_fan_state()
        if not ok or not state:
            return {
                "success": False,
                "confirmation": _air_fan_response(
                    f"I couldn't read the fan status, sir. {error}",
                    f"Aku belum berhasil membaca status fan. {error}",
                ),
            }
        english, indonesian = _air_fan_status_summary(state)
        return {
            "success": True,
            "confirmation": _air_fan_response(f"{english}, sir.", f"{indonesian}."),
        }

    if kind == "speed":
        raw_value = int((request or {}).get("value", 0))
        if raw_value not in _AIR_FAN_ALLOWED_SPEEDS:
            allowed = _air_fan_allowed_speed_phrase()
            return {
                "success": False,
                "confirmation": _air_fan_response(
                    f"Fan speed must be one of {allowed} percent, sir.",
                    f"Speed fan harus salah satu dari {allowed} persen.",
                ),
            }

        ok_cfg, _, cfg_error = await _air_fan_http_request("POST", "/fan-cfg", {
            "fanManualen": "1",
            "fanProfile": _AIR_FAN_PROFILE_CODES["manual"],
        })
        if not ok_cfg:
            return {
                "success": False,
                "confirmation": _air_fan_response(
                    f"I couldn't switch the fan into manual mode, sir. {cfg_error}",
                    f"Aku belum berhasil mengaktifkan mode manual fan. {cfg_error}",
                ),
            }

        ok_runtime, _, runtime_error = await _air_fan_http_request("POST", "/fan-runtime", {
            "fanSetspeed": str(raw_value),
        })
        if not ok_runtime:
            return {
                "success": False,
                "confirmation": _air_fan_response(
                    f"I couldn't set the fan speed, sir. {runtime_error}",
                    f"Aku belum berhasil mengatur speed fan. {runtime_error}",
                ),
            }

        ok_state, state, state_error = await _get_air_fan_state()
        if not ok_state or not state:
            return {
                "success": True,
                "confirmation": _air_fan_response(
                    f"Set the fan to {raw_value} percent, sir, but I couldn't refresh the status afterward. {state_error}",
                    f"Speed fan sudah kuatur ke {raw_value} persen, tapi aku belum berhasil membaca status terbaru. {state_error}",
                ),
            }

        english, indonesian = _air_fan_status_summary(state)
        return {
            "success": True,
            "confirmation": _air_fan_response(
                f"Set the fan to {raw_value} percent, sir. {english}.",
                f"Speed fan sudah kuatur ke {raw_value} persen. {indonesian}.",
            ),
        }

    if kind == "profile":
        profile = str((request or {}).get("profile", "")).strip().lower()
        profile_code = _AIR_FAN_PROFILE_CODES.get(profile)
        if profile not in {"quiet", "normal", "dynamic"} or not profile_code:
            return {
                "success": False,
                "confirmation": _air_fan_response(
                    "Fan profile must be quiet, normal, or dynamic, sir.",
                    "Profile fan harus quiet, normal, atau dynamic.",
                ),
            }

        ok_cfg, _, cfg_error = await _air_fan_http_request("POST", "/fan-cfg", {
            "fanManualen": "0",
            "fanProfile": profile_code,
        })
        if not ok_cfg:
            return {
                "success": False,
                "confirmation": _air_fan_response(
                    f"I couldn't switch the fan profile, sir. {cfg_error}",
                    f"Aku belum berhasil mengganti profile fan. {cfg_error}",
                ),
            }

        ok_state, state, state_error = await _get_air_fan_state()
        if not ok_state or not state:
            return {
                "success": True,
                "confirmation": _air_fan_response(
                    f"Switched the fan profile to {profile}, sir, but I couldn't refresh the status afterward. {state_error}",
                    f"Profile fan sudah kuganti ke {profile}, tapi aku belum berhasil membaca status terbaru. {state_error}",
                ),
            }

        english, indonesian = _air_fan_status_summary(state)
        return {
            "success": True,
            "confirmation": _air_fan_response(
                f"Switched the fan profile to {profile}, sir. {english}.",
                f"Profile fan sudah kuganti ke {profile}. {indonesian}.",
            ),
        }

    return {
        "success": False,
        "confirmation": _air_fan_response(
            "I need a clearer fan command, sir.",
            "Aku butuh perintah fan yang lebih jelas.",
        ),
    }


async def control_wiz(request: dict) -> dict:
    """Control the local-only WiZ controller through its LAN HTTP bridge."""
    kind = str((request or {}).get("kind", "")).strip().lower()
    target_device = (request or {}).get("target_device")  # optional device/room name
    if not kind:
        return {
            "success": False,
            "confirmation": _wiz_response(
                "Tell me what to do with the lights, sir.",
                "Beri tahu aku mau diapakan lampunya.",
            ),
        }

    if kind == "dashboard":
        ok, launched, error = await _ensure_wiz_controller_running()
        if not ok:
            return {"success": False, "confirmation": error}
        browser_result = await open_browser(WIZ_CONTROLLER_URL, "chrome")
        if not browser_result["success"]:
            return {
                "success": False,
                "confirmation": _wiz_response(
                    "The light controller is up, but I couldn't open the dashboard, sir.",
                    "Controller lampunya sudah aktif, tapi dashboard-nya belum berhasil kubuka.",
                ),
            }
        return {
            "success": True,
            "confirmation": _wiz_response(
                "Opened the local light controller, sir.",
                "Dashboard kontrol lampunya sudah kubuka.",
            ),
        }

    if kind == "discover":
        ok, _, error = await _ensure_wiz_controller_running()
        if not ok:
            return {"success": False, "confirmation": error}
        devices = _get_cached_wiz_discovery_snapshot(_WIZ_DISCOVERY_CACHE_TTL_SECONDS)
        if not devices:
            ok, devices, error = await _discover_wiz_devices_with_retry()
            if not ok:
                return {
                    "success": False,
                    "confirmation": _wiz_response(
                        f"I couldn't complete the light scan, sir. {error or 'The controller did not return a usable result.'}",
                        f"Aku belum berhasil menyelesaikan scan lampunya. {error or 'Controller belum mengembalikan hasil yang bisa dipakai.'}",
                    ),
                }
        online_count = sum(1 for device in devices if device.get("online"))
        offline_count = max(0, len(devices) - online_count)
        if not devices:
            return {
                "success": True,
                "confirmation": _wiz_response(
                    "I scanned the local network, but I didn't find any connected lights, sir.",
                    "Aku sudah scan lampu yang terhubung ke jaringan ini, tapi belum ada yang merespons.",
                ),
            }
        if offline_count:
            return {
                "success": True,
                "confirmation": _wiz_response(
                    f"Scan complete, sir. {online_count} light{'s' if online_count != 1 else ''} online, {offline_count} offline.",
                    f"Aku sudah scan lampu yang terhubung ke jaringan ini. {online_count} online, {offline_count} offline.",
                ),
            }
        return {
            "success": True,
            "confirmation": _wiz_response(
                f"Scan complete, sir. Found {_wiz_count_phrase(online_count)} online.",
                f"Aku sudah scan lampu yang terhubung ke jaringan ini. Ada {_wiz_count_phrase(online_count)} yang online.",
            ),
        }

    if kind == "power":
        state = str(request.get("state", "")).lower()
        if state == "on":
            return await _apply_wiz_group_command(
                lambda device: ("/device/on", {"ip": device["ip"]}),
                _wiz_response(
                    "Powered on {count_phrase}, sir.",
                    "{count_phrase} sudah dinyalakan.",
                ),
                target=target_device,
            )
        if state == "off":
            return await _apply_wiz_group_command(
                lambda device: ("/device/off", {"ip": device["ip"]}),
                _wiz_response(
                    "Powered down {count_phrase}, sir.",
                    "{count_phrase} sudah dimatikan.",
                ),
                target=target_device,
            )
        return {
            "success": False,
            "confirmation": _wiz_response(
                "I need a clear on or off command for the lights, sir.",
                "Aku butuh perintah yang jelas, mau dinyalakan atau dimatikan.",
            ),
        }

    if kind == "brightness":
        brightness = max(0, min(100, int(request.get("value", 0))))
        return await _apply_wiz_group_command(
            lambda device: ("/device/brightness", {"ip": device["ip"], "brightness": brightness}),
            _wiz_response(
                f"Set {{count_phrase}} to {brightness} percent, sir.",
                f"Kecerahan {{count_phrase}} sudah kuatur ke {brightness} persen.",
            ),
            target=target_device,
        )

    if kind == "color":
        hex_color = str(request.get("hex", "")).strip()
        label = str(request.get("label", "that color")).strip() or "that color"
        color_brightness = request.get("brightness")  # optional brightness alongside color
        if not hex_color:
            return {
                "success": False,
                "confirmation": _wiz_response(
                    "I need a valid color first, sir.",
                    "Aku butuh warna yang jelas dulu.",
                ),
            }

        if color_brightness is None:
            # Simple color-only command
            return await _apply_wiz_group_command(
                lambda device: ("/device/color", {"ip": device["ip"], "hex": hex_color}),
                _wiz_response(
                    f"Set {{count_phrase}} to {label}, sir.",
                    f"Warna {{count_phrase}} sudah kuganti jadi {label}.",
                ),
                target=target_device,
            )

        # Color + brightness compound — apply both per device
        brightness_val = max(0, min(100, int(color_brightness)))

        async def _apply_color_brightness(device: dict) -> tuple[bool, str]:
            c_ok, _, c_err = await _wiz_http_request(
                "POST", "/device/color", {"ip": device["ip"], "hex": hex_color},
            )
            if not c_ok:
                return False, c_err or ""
            b_ok, _, b_err = await _wiz_http_request(
                "POST", "/device/brightness", {"ip": device["ip"], "brightness": brightness_val},
            )
            return b_ok, b_err or ""

        ok, devices, error = await _get_online_wiz_devices(target=target_device)
        if not ok:
            return {"success": False, "confirmation": error}

        success_count, failure_count = 0, 0
        for device in devices:
            device_ok, _ = await _apply_color_brightness(device)
            if device_ok:
                success_count += 1
            else:
                failure_count += 1

        if success_count <= 0:
            return {"success": False, "confirmation": _wiz_response(
                "Lights didn't accept the color change, sir.",
                "Lampu tidak menerima perubahan warna.",
            )}
        confirmation = _wiz_response(
            f"Set {{count_phrase}} to {label} at {brightness_val}%, sir.{_wiz_failure_tail(failure_count)}",
            f"Warna {{count_phrase}} diganti ke {label}, kecerahan {brightness_val}%.{_wiz_failure_tail(failure_count)}",
        )
        count_phrase = _wiz_count_phrase(success_count)
        return {"success": failure_count == 0, "confirmation": confirmation.format(count_phrase=count_phrase)}

    if kind == "temperature":
        temperature = int(request.get("temperature", 0))
        # brightness can come from: compound command override, preset value, or None
        brightness = request.get("brightness")
        if brightness is not None:
            brightness = max(0, min(100, int(brightness)))
        label = str(request.get("label", "that white mode")).strip() or "that white mode"

        async def _apply_temperature(device: dict) -> tuple[bool, str]:
            req_ok, payload_json, req_error = await _wiz_http_request(
                "POST",
                "/device/temperature",
                {"ip": device["ip"], "temperature": temperature},
            )
            if not req_ok or (isinstance(payload_json, dict) and not payload_json.get("success", True)):
                return False, req_error or (payload_json.get("error") if isinstance(payload_json, dict) else "") or ""

            if brightness is None:
                return True, ""

            bright_ok, bright_payload, bright_error = await _wiz_http_request(
                "POST",
                "/device/brightness",
                {"ip": device["ip"], "brightness": int(brightness)},
            )
            if bright_ok and (not isinstance(bright_payload, dict) or bright_payload.get("success", True)):
                return True, ""
            return False, bright_error or (bright_payload.get("error") if isinstance(bright_payload, dict) else "") or ""

        ok, devices, error = await _get_online_wiz_devices(target=target_device)
        if not ok:
            return {"success": False, "confirmation": error}

        success_count = 0
        failure_count = 0
        last_error = ""
        for device in devices:
            device_ok, device_error = await _apply_temperature(device)
            if device_ok:
                success_count += 1
            else:
                failure_count += 1
                last_error = device_error or last_error

        if success_count <= 0:
            confirmation = _wiz_response(
                "The lights didn't accept that white-light mode, sir.",
                "Lampunya belum menerima mode putih itu.",
            )
            if last_error:
                confirmation = f"{confirmation[:-1]}: {last_error}"
            return {"success": False, "confirmation": confirmation}

        confirmation = _wiz_response(
            f"Set {_wiz_count_phrase(success_count)} to {label}, sir.{_wiz_failure_tail(failure_count)}",
            f"Mode {_wiz_count_phrase(success_count)} sudah kuubah ke {label}.{_wiz_failure_tail(failure_count)}",
        )
        return {"success": failure_count == 0, "confirmation": confirmation}

    if kind == "scene":
        label = str(request.get("label", "that scene")).strip() or "that scene"
        hex_color = request.get("hex")
        temperature = request.get("temperature")
        brightness = request.get("brightness")

        if hex_color:
            # Color-based scene (e.g. gaming mode)
            async def _apply_scene_color(device: dict) -> tuple[bool, str]:
                req_ok, payload_json, req_error = await _wiz_http_request(
                    "POST", "/device/color",
                    {"ip": device["ip"], "hex": hex_color},
                )
                if not req_ok or (isinstance(payload_json, dict) and not payload_json.get("success", True)):
                    return False, req_error or ""
                if brightness is not None:
                    b_ok, b_payload, b_error = await _wiz_http_request(
                        "POST", "/device/brightness",
                        {"ip": device["ip"], "brightness": int(brightness)},
                    )
                    if not b_ok:
                        return False, b_error or ""
                return True, ""

            ok, devices, error = await _get_online_wiz_devices(target=target_device)
            if not ok:
                return {"success": False, "confirmation": error}

            success_count = 0
            failure_count = 0
            for device in devices:
                device_ok, _ = await _apply_scene_color(device)
                if device_ok:
                    success_count += 1
                else:
                    failure_count += 1
        elif temperature:
            # Temperature-based scene (most scenes)
            async def _apply_scene_temp(device: dict) -> tuple[bool, str]:
                req_ok, payload_json, req_error = await _wiz_http_request(
                    "POST", "/device/temperature",
                    {"ip": device["ip"], "temperature": int(temperature)},
                )
                if not req_ok or (isinstance(payload_json, dict) and not payload_json.get("success", True)):
                    return False, req_error or ""
                if brightness is not None:
                    b_ok, b_payload, b_error = await _wiz_http_request(
                        "POST", "/device/brightness",
                        {"ip": device["ip"], "brightness": int(brightness)},
                    )
                    if not b_ok:
                        return False, b_error or ""
                return True, ""

            ok, devices, error = await _get_online_wiz_devices(target=target_device)
            if not ok:
                return {"success": False, "confirmation": error}

            success_count = 0
            failure_count = 0
            for device in devices:
                device_ok, _ = await _apply_scene_temp(device)
                if device_ok:
                    success_count += 1
                else:
                    failure_count += 1
        else:
            return {
                "success": False,
                "confirmation": _wiz_response(
                    f"Scene '{label}' doesn't have valid parameters, sir.",
                    f"Scene '{label}' belum punya parameter yang valid.",
                ),
            }

        if success_count <= 0:
            return {
                "success": False,
                "confirmation": _wiz_response(
                    f"Couldn't apply {label} to the lights, sir.",
                    f"Belum berhasil menerapkan {label} ke lampunya.",
                ),
            }
        return {
            "success": failure_count == 0,
            "confirmation": _wiz_response(
                f"Switched {_wiz_count_phrase(success_count)} to {label}.{_wiz_failure_tail(failure_count)}",
                f"{_wiz_count_phrase(success_count)} sudah kuganti ke {label}.{_wiz_failure_tail(failure_count)}",
            ),
        }

    return {
        "success": False,
        "confirmation": _wiz_response(
            "I don't recognize that light command yet, sir.",
            "Aku belum paham perintah lampu yang itu.",
        ),
    }


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


async def ask_codex(prompt: str, *, new_thread: bool = False) -> dict:
    """Open Codex and send a request into a persistent or fresh Codex thread."""
    cleaned = re.sub(r"\s+", " ", prompt).strip(" .,!?'\"")
    if not cleaned:
        return {
            "success": False,
            "confirmation": "Tell me what to ask Codex, sir.",
        }

    app_result = await open_app("codex")
    if not app_result["success"]:
        return {
            "success": False,
            "confirmation": "I couldn't open Codex on this machine, sir.",
        }

    if not IS_WINDOWS:
        return {
            "success": False,
            "confirmation": "Ask Codex automation is currently set up for Windows only, sir.",
        }

    seed_prompt = (
        f"{CODEX_JARVIS_THREAD_TITLE}\n"
        "Keep this as the persistent thread for future JARVIS requests so context carries forward.\n\n"
        f"Current request:\n{cleaned}"
    )

    script = f"""
$forceNewThread = {'$true' if new_thread else '$false'}
$threadTitleRaw = {_ps_quote(CODEX_JARVIS_THREAD_TITLE)}
$promptText = {_ps_quote(cleaned)}
$seedPromptText = {_ps_quote(seed_prompt)}
$signature = @'
using System;
using System.Runtime.InteropServices;
public static class WinApi {{
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
}}
'@
Add-Type -TypeDefinition $signature -ErrorAction SilentlyContinue | Out-Null
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
$shell = New-Object -ComObject WScript.Shell

function Normalize-CodexText([string]$text) {{
    if (-not $text) {{
        return ""
    }}
    return (($text.ToLower() -replace '[^a-z0-9]+', ' ').Trim())
}}

function Activate-Element($element) {{
    if (-not $element) {{
        return $false
    }}

    try {{
        $selection = $element.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
        $selection.Select()
        return $true
    }} catch {{
    }}

    try {{
        $invoke = $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        $invoke.Invoke()
        return $true
    }} catch {{
    }}

    try {{
        $element.SetFocus()
        Start-Sleep -Milliseconds 120
        $shell.SendKeys('{{ENTER}}')
        return $true
    }} catch {{
    }}

    try {{
        $rect = $element.Current.BoundingRectangle
        if ($rect.Width -gt 1 -and $rect.Height -gt 1) {{
            $x = [int]($rect.Left + ($rect.Width / 2))
            $y = [int]($rect.Top + ($rect.Height / 2))
            [WinApi]::SetCursorPos($x, $y) | Out-Null
            Start-Sleep -Milliseconds 80
            [WinApi]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
            Start-Sleep -Milliseconds 50
            [WinApi]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
            return $true
        }}
    }} catch {{
    }}

    return $false
}}

function Find-ThreadListItem($root, [string]$titleNeedle) {{
    $walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
    $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    for ($i = 0; $i -lt $all.Count; $i++) {{
        $element = $all.Item($i)
        $name = $element.Current.Name
        if (-not $name) {{
            continue
        }}

        $normalized = Normalize-CodexText $name
        if ($normalized -notlike "*$titleNeedle*") {{
            continue
        }}

        $candidate = $element
        for ($depth = 0; $depth -lt 10 -and $candidate; $depth++) {{
            $type = $candidate.Current.ControlType.ProgrammaticName
            if ($type -eq 'ControlType.ListItem') {{
                return $candidate
            }}
            $candidate = $walker.GetParent($candidate)
        }}
    }}
    return $null
}}

$proc = $null
for ($attempt = 0; $attempt -lt 20; $attempt++) {{
    $proc = Get-Process | Where-Object {{ $_.ProcessName -like '*codex*' -and $_.MainWindowHandle -ne 0 }} | Select-Object -First 1
    if ($proc) {{ break }}
    Start-Sleep -Milliseconds 300
}}

if (-not $proc) {{
    Write-Error 'Codex window not ready'
    exit 1
}}

[WinApi]::ShowWindowAsync($proc.MainWindowHandle, 3) | Out-Null
[WinApi]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 900

$root = [System.Windows.Automation.AutomationElement]::FromHandle($proc.MainWindowHandle)
$threadNeedle = Normalize-CodexText $threadTitleRaw
$threadItem = $null
if (-not $forceNewThread) {{
    $threadItem = Find-ThreadListItem $root $threadNeedle
}}

if ($threadItem) {{
    if (-not (Activate-Element $threadItem)) {{
        Write-Error 'Could not activate the persistent Codex thread'
        exit 1
    }}
    Start-Sleep -Milliseconds 900
    [System.Windows.Forms.Clipboard]::SetText($promptText)
}} else {{
$buttonCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Button
)
$buttons = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $buttonCondition)
    $newThread = $null
    foreach ($button in $buttons) {{
        $name = $button.Current.Name
        if ($name -eq 'New thread' -or $name -like 'Start new thread*') {{
            $newThread = $button
            break
        }}
    }}

    if (-not $newThread) {{
        Write-Error 'Codex new thread button not found'
        exit 1
    }}

    if (-not (Activate-Element $newThread)) {{
        Write-Error 'Could not start a new Codex thread'
        exit 1
    }}

    Start-Sleep -Milliseconds 1200
    if ($forceNewThread) {{
        [System.Windows.Forms.Clipboard]::SetText($promptText)
    }} else {{
        [System.Windows.Forms.Clipboard]::SetText($seedPromptText)
    }}
}}

Start-Sleep -Milliseconds 150
$shell.SendKeys('^v')
Start-Sleep -Milliseconds 200
$shell.SendKeys('{{ENTER}}')
Write-Output 'SENT'
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
    success = proc.returncode == 0
    if not success and detail:
        log.warning(f"ask_codex automation failed: {detail}")

    return {
        "success": success,
        "confirmation": (
            "Opened Codex and sent your request in a new chat, sir."
            if success and new_thread else
            "Opened Codex and sent your request in the persistent JARVIS thread, sir."
            if success else
            "I opened Codex, but sending the request ran into trouble, sir."
        ),
    }


async def create_folder_from_request(request: dict) -> dict:
    """Create a regular folder or a standard project folder structure."""
    name = (request.get("name") or "").strip()
    root_path = (request.get("root_path") or "").strip()
    mode = request.get("mode") or "folder"

    if not name or not root_path:
        return {
            "success": False,
            "confirmation": "I still need the folder name and location before I can create it, sir.",
        }

    root = Path(root_path)
    if not root.exists():
        return {
            "success": False,
            "confirmation": f"I couldn't find the root folder {root_path}, sir.",
        }

    invalid_chars = set('<>:"/\\|?*')
    if any(char in invalid_chars for char in name):
        return {
            "success": False,
            "confirmation": "That folder name contains invalid path characters, sir.",
        }

    if mode == "project":
        if not IS_WINDOWS or not PROJECT_STRUCTURE_SCRIPT.exists():
            return {
                "success": False,
                "confirmation": "The project folder skill is not available on this machine, sir.",
            }

        proc = await asyncio.create_subprocess_exec(
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PROJECT_STRUCTURE_SCRIPT),
            "-ProjectName",
            name,
            "-RootPath",
            str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        detail = stderr.decode(errors="ignore").strip()
        output = stdout.decode(errors="ignore").strip().splitlines()

        if proc.returncode != 0:
            if detail:
                log.error(f"create_folder_from_request project mode failed: {detail}")
            return {
                "success": False,
                "confirmation": "I had trouble creating that standard project folder, sir.",
            }

        status = "CREATED"
        project_folder_name = name
        project_path = ""
        for line in output:
            if line.startswith("STATUS="):
                status = line.split("=", 1)[1].strip().upper()
            elif line.startswith("PROJECT_FOLDER_NAME="):
                project_folder_name = line.split("=", 1)[1].strip()
            elif line.startswith("PROJECT_PATH="):
                project_path = line.split("=", 1)[1].strip()

        verb = "already exists" if status == "EXISTS" else "is ready"
        return {
            "success": True,
            "confirmation": f"The standard project folder {project_folder_name} {verb} in {root}, sir.",
            "path": project_path or str(root / project_folder_name),
        }

    folder_path = root / name
    existed = folder_path.exists()
    folder_path.mkdir(parents=True, exist_ok=True)
    return {
        "success": True,
        "confirmation": (
            f"That folder already exists in {root}, sir."
            if existed else
            f"Created the folder {name} in {root}, sir."
        ),
        "path": str(folder_path),
    }


async def open_app(target: str) -> dict:
    """Open a supported desktop application by name."""
    app_key = normalize_desktop_app_name(target) or target.strip().lower()
    spec = _APP_SPECS.get(app_key)
    if not spec:
        return {
            "success": False,
            "confirmation": f"I don't know how to open {target}, sir.",
        }

    note_key = spec.get("obsidian_note_key")
    if note_key:
        return await _open_named_obsidian_note(
            note_key,
            success_confirmation=spec["confirmation"],
        )

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

    elif action == "wiz":
        result = await control_wiz(target if isinstance(target, dict) else {})
        result["project_dir"] = None
        return result

    elif action == "fan":
        result = await control_air_fan(target if isinstance(target, dict) else {})
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
