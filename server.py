"""
JARVIS Server — Voice AI + Development Orchestration

Handles:
1. WebSocket voice interface (browser audio <-> LLM <-> TTS)
2. Claude Code task manager (spawn/manage claude -p subprocesses)
3. Project awareness (scan Desktop for git repos)
4. REST API for task management
"""

import asyncio
import base64
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# Load .env file if present
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import anthropic
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from actions import (
    execute_action,
    monitor_build,
    open_terminal,
    open_browser,
    open_app,
    play_spotify,
    control_spotify,
    ask_codex,
    handoff_to_codex,
    open_claude_in_project,
    _generate_project_name,
    prompt_existing_terminal,
    normalize_desktop_app_name,
    extract_codex_request,
    extract_folder_request,
    extract_spotify_control,
    extract_spotify_query,
    extract_air_fan_request,
    extract_wiz_request,
    merge_folder_request_details,
    describe_missing_folder_details,
    folder_request_is_complete,
    create_folder_from_request,
    control_air_fan,
    control_wiz,
    should_use_codex_delegate,
    warm_wiz_controller,
)
from work_mode import WorkSession, is_casual_question
from screen import get_active_windows, take_screenshot, describe_screen, format_windows_for_context
from calendar_access import get_todays_events, get_upcoming_events, get_next_event, format_events_for_context, format_schedule_summary, refresh_cache as refresh_calendar_cache
from mail_access import get_unread_count, get_unread_messages, get_recent_messages, search_mail, read_message, format_unread_summary, format_messages_for_context, format_messages_for_voice
from memory import (
    remember, recall, get_open_tasks, create_task, complete_task, search_tasks,
    create_note, search_notes, get_tasks_for_date, build_memory_context,
    format_tasks_for_voice, extract_memories, get_important_memories,
    sync_feedback_logs,
)
from notes_access import get_recent_notes, read_note, search_notes_apple, create_apple_note
from dispatch_registry import DispatchRegistry
from planner import TaskPlanner, detect_planning_mode, BYPASS_PHRASES

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("jarvis")

# Offline speech-to-text (lazy-loaded on first use)
_whisper_model = None
_whisper_error = None

def get_whisper_model():
    """Lazy-load faster-whisper model on first transcribe request."""
    global _whisper_model, _whisper_error
    if _whisper_model is None and _whisper_error is None:
        try:
            log.info("Loading Whisper model (small, int8)...")
            from faster_whisper import WhisperModel
            _whisper_model = WhisperModel(
                os.getenv("JARVIS_WHISPER_MODEL", "small"),
                device=os.getenv("JARVIS_WHISPER_DEVICE", "cpu"),
                compute_type=os.getenv("JARVIS_WHISPER_COMPUTE_TYPE", "int8"),
            )
            log.info("✓ Whisper model loaded successfully (faster-whisper/small/int8)")
        except ImportError as e:
            log.error(f"✗ faster-whisper NOT installed: {e}")
            log.error("  Fix: pip install faster-whisper --break-system-packages")
            _whisper_error = str(e)
            _whisper_model = False
        except Exception as e:
            log.error(f"✗ Whisper model load failed: {e}", exc_info=True)
            _whisper_error = str(e)
            _whisper_model = False
    return _whisper_model if _whisper_model else None

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
FISH_API_KEY = os.getenv("FISH_API_KEY", "")
DEFAULT_FISH_VOICE_ID = "612b878b113047d9a770c069c8b4fdfe"  # JARVIS (MCU)
DEFAULT_FISH_VOICE_ID_ID_FALLBACK = "9edf80f1aa8743608817c0d8f415f974"
DEFAULT_FISH_TTS_BACKEND = "s2-pro"
FISH_API_URL = "https://api.fish.audio/v1/tts"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
JARVIS_CHAT_MODEL = os.getenv("JARVIS_CHAT_MODEL", "claude-sonnet-4-6")
JARVIS_FAST_MODEL = os.getenv("JARVIS_FAST_MODEL", "claude-haiku-4-5-20251001")
JARVIS_RESEARCH_MODEL = os.getenv("JARVIS_RESEARCH_MODEL", "claude-opus-4-6")
JARVIS_CHAT_FALLBACK_MODEL = os.getenv("JARVIS_CHAT_FALLBACK_MODEL", "claude-sonnet-4-6")
JARVIS_ULTRA_ECO_MODE = os.getenv("JARVIS_ULTRA_ECO_MODE", "0").strip().lower() in {"1", "true", "yes", "on"}
DEFAULT_LOCAL_LLM_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_LOCAL_LLM_MODEL = ""
DEFAULT_NVIDIA_LLM_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_NVIDIA_LLM_MODEL = "deepseek-ai/deepseek-v4-pro"
LMS_CLI_PATH = Path.home() / ".lmstudio" / "bin" / "lms.exe"
DEFAULT_TTS_PROVIDER = "fish"
DEFAULT_LOCAL_TTS_ENGINE = "kokoro"
DEFAULT_LOCAL_TTS_BASE_URLS = {
    "kokoro": "http://127.0.0.1:8881",
    "voxcpm": "http://127.0.0.1:8882",
}
DEFAULT_LOCAL_TTS_BASE_URL = DEFAULT_LOCAL_TTS_BASE_URLS[DEFAULT_LOCAL_TTS_ENGINE]
DEFAULT_LOCAL_TTS_VOICE = "bm_george"
DEFAULT_LOCAL_TTS_VOICE_ID = ""
DEFAULT_LOCAL_TTS_SPEED = 1.0
DEFAULT_VOXCPM_MODEL_PATH = str(Path(__file__).resolve().parent.parent / "VoxCPM2")
LOCAL_TTS_SERVER_PATHS = {
    "kokoro": Path(__file__).parent / "tts_server.py",
    "voxcpm": Path(__file__).parent / "tts_server_voxcpm.py",
}

DESKTOP_PATH = Path.home() / "Desktop"


def _env_pref(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    return value or default


def _env_flag(name: str, default: str = "0") -> bool:
    return _env_pref(name, default).lower() in {"1", "true", "yes", "on"}


def get_llm_provider() -> str:
    value = _env_pref("JARVIS_LLM_PROVIDER", "anthropic").lower()
    if value.startswith("local"):
        return "local"
    if value.startswith("nvidia") or value.startswith("deepseek") or value.startswith("nim"):
        return "nvidia"
    return "anthropic"


def get_local_llm_base_url() -> str:
    return _env_pref("JARVIS_LOCAL_LLM_BASE_URL", DEFAULT_LOCAL_LLM_BASE_URL).rstrip("/")


def get_local_llm_model() -> str:
    return _env_pref("JARVIS_LOCAL_LLM_MODEL", DEFAULT_LOCAL_LLM_MODEL)


def get_local_llm_api_key() -> str:
    return _env_pref("JARVIS_LOCAL_LLM_API_KEY", "lm-studio")


def get_nvidia_llm_base_url() -> str:
    return _env_pref("JARVIS_NVIDIA_LLM_BASE_URL", DEFAULT_NVIDIA_LLM_BASE_URL).rstrip("/")


def get_nvidia_llm_model() -> str:
    return _env_pref("JARVIS_NVIDIA_LLM_MODEL", DEFAULT_NVIDIA_LLM_MODEL)


def get_nvidia_api_key() -> str:
    return _env_pref("NVIDIA_API_KEY", "")


def get_tts_provider() -> str:
    value = _env_pref("JARVIS_TTS_PROVIDER", DEFAULT_TTS_PROVIDER).lower()
    return "local" if value.startswith("local") else "fish"


def normalize_local_tts_engine(value: str | None) -> str:
    raw = (value or DEFAULT_LOCAL_TTS_ENGINE).strip().lower()
    return "voxcpm" if raw.startswith("vox") else "kokoro"


def get_local_tts_engine() -> str:
    return normalize_local_tts_engine(_env_pref("JARVIS_LOCAL_TTS_ENGINE", DEFAULT_LOCAL_TTS_ENGINE))


def get_default_local_tts_base_url(engine: str | None = None) -> str:
    return DEFAULT_LOCAL_TTS_BASE_URLS[normalize_local_tts_engine(engine)]


def get_local_tts_base_url(engine: str | None = None) -> str:
    raw = os.getenv("JARVIS_LOCAL_TTS_URL", "").strip()
    if not raw:
        raw = get_default_local_tts_base_url(engine)
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = f"http://{raw.lstrip('/')}"
    return raw.rstrip("/")


def get_local_tts_model_path() -> str:
    return _env_pref("JARVIS_LOCAL_TTS_MODEL_PATH", DEFAULT_VOXCPM_MODEL_PATH)


def get_local_tts_speed() -> float:
    raw = _env_pref("JARVIS_LOCAL_TTS_SPEED", str(DEFAULT_LOCAL_TTS_SPEED))
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_LOCAL_TTS_SPEED
    return min(2.0, max(0.5, value))


def get_local_tts_voice(language: str | None = None) -> str:
    lang = (language or get_user_language()).lower()
    if lang.startswith("id"):
        indonesian_voice = os.getenv("JARVIS_LOCAL_TTS_VOICE_ID", "").strip()
        if indonesian_voice:
            return indonesian_voice
    return _env_pref("JARVIS_LOCAL_TTS_VOICE", DEFAULT_LOCAL_TTS_VOICE)


@dataclass
class _LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class _LLMContentBlock:
    text: str


@dataclass
class _LLMResponse:
    content: list[_LLMContentBlock]
    usage: _LLMUsage = field(default_factory=_LLMUsage)


LOCAL_LLM_LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}


def _normalize_local_chat_role(value: str | None) -> str:
    role = (value or "user").strip().lower()
    return role if role in {"system", "user", "assistant"} else "user"


def _normalize_local_chat_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _normalize_local_llm_base_url(base_url: str | None = None) -> str:
    raw = (base_url or get_local_llm_base_url()).strip()
    if not raw:
        raw = DEFAULT_LOCAL_LLM_BASE_URL
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = f"http://{raw.lstrip('/')}"
    parsed = urlparse(raw)
    path = (parsed.path or "").rstrip("/")
    if path == "/api/v1":
        path = "/v1"
    elif path in {"", "/"}:
        path = "/v1"
    normalized = parsed._replace(path=path, params="", query="", fragment="")
    return urlunparse(normalized).rstrip("/")


def _build_local_llm_candidate_urls(base_url: str | None = None) -> list[str]:
    normalized = _normalize_local_llm_base_url(base_url)
    parsed = urlparse(normalized)
    scheme = parsed.scheme or "http"
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if scheme == "https" else 80)
    suffixes = ["/v1", "/api/v1"]

    hosts = [host]
    if host not in LOCAL_LLM_LOOPBACK_HOSTS:
        hosts.extend(["127.0.0.1", "localhost"])

    candidates: list[str] = []
    for candidate_host in hosts:
        for suffix in suffixes:
            url = f"{scheme}://{candidate_host}:{port}{suffix}"
            if url not in candidates:
                candidates.append(url)
    return candidates


def _get_local_llm_headers(api_key: str | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    resolved_api_key = (api_key or get_local_llm_api_key()).strip()
    if resolved_api_key:
        headers["Authorization"] = f"Bearer {resolved_api_key}"
    return headers


def _extract_local_model_id(item: dict) -> str:
    return str(
        item.get("id")
        or item.get("key")
        or item.get("modelKey")
        or item.get("identifier")
        or ""
    ).strip()


def _normalize_local_model_payload(payload: dict) -> list[dict]:
    if isinstance(payload.get("data"), list):
        return [
            {"id": _extract_local_model_id(item), **({"label": item.get("id")} if item.get("id") else {})}
            for item in payload.get("data") or []
            if _extract_local_model_id(item)
        ]
    if isinstance(payload.get("models"), list):
        return [
            {
                "id": _extract_local_model_id(item),
                "label": str(item.get("display_name") or item.get("name") or _extract_local_model_id(item)),
            }
            for item in payload.get("models") or []
            if _extract_local_model_id(item)
        ]
    return []


class _LocalMessagesAPI:
    def __init__(self, client: Any):
        self._client = client

    async def create(
        self,
        *,
        model: str,
        max_tokens: int = 250,
        system: str | None = None,
        messages: list[dict] | None = None,
    ) -> _LLMResponse:
        resolved_model = await self._client.resolve_model(model)
        if not resolved_model:
            provider_label = getattr(self._client, "provider_label", "OpenAI-compatible LLM")
            raise RuntimeError(
                f"No {provider_label} chat model is configured or available."
            )

        payload_messages: list[dict[str, Any]] = []
        if system:
            payload_messages.append({"role": "system", "content": _normalize_local_chat_content(system)})
        for item in messages or []:
            payload_messages.append(
                {
                    "role": _normalize_local_chat_role(item.get("role", "user")),
                    "content": _normalize_local_chat_content(item.get("content", "")),
                }
            )

        async with httpx.AsyncClient(timeout=45.0, trust_env=False) as http:
            response = await http.post(
                f"{self._client.base_url}/chat/completions",
                headers=_get_local_llm_headers(self._client.api_key),
                json={
                    "model": resolved_model,
                    "messages": payload_messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.3,
                    "stream": False,
                },
            )
            if response.status_code == 202:
                detail = _compact_error_text(response.text)[:500]
                provider_label = getattr(self._client, "provider_label", "OpenAI-compatible LLM")
                raise RuntimeError(
                    f"{provider_label} returned a pending 202 response; synchronous chat completions are required. {detail}"
                )
            if response.status_code >= 400:
                detail = _compact_error_text(response.text)[:500]
                provider_label = getattr(self._client, "provider_label", "OpenAI-compatible LLM")
                raise RuntimeError(
                    f"{provider_label} HTTP {response.status_code}: {detail or 'Unknown server error'}"
                )
            payload = response.json()

        choice = ((payload.get("choices") or [{}])[0]).get("message") or {}
        content = choice.get("content", "")
        reasoning_content = choice.get("reasoning_content", "")
        if isinstance(content, list):
            text = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        else:
            text = str(content or "")
        if not text.strip() and reasoning_content:
            # Thinking/reasoning model (e.g. Gemma 4 thinking variant, DeepSeek-R1)
            # returned <think>…</think> but no final answer token yet — or the
            # model put its answer entirely inside reasoning_content.
            # Try to extract the last sentence from reasoning as the response
            # rather than raising an error.
            fallback = reasoning_content.strip().split("\n")[-1].strip()
            if fallback:
                log.warning("LLM reasoning-only response; using last reasoning line as answer.")
                text = fallback
            else:
                finish_reason = str(((payload.get("choices") or [{}])[0]).get("finish_reason") or "").strip()
                raise RuntimeError(
                    f"LLM produced reasoning without a final answer (finish_reason={finish_reason or 'unknown'})."
                )
        if not text.strip():
            raise RuntimeError("LLM returned an empty response.")
        usage = payload.get("usage") or {}
        return _LLMResponse(
            content=[_LLMContentBlock(text=text)],
            usage=_LLMUsage(
                input_tokens=int(usage.get("prompt_tokens", 0) or 0),
                output_tokens=int(usage.get("completion_tokens", 0) or 0),
            ),
        )


class LocalLLMClient:
    def __init__(self, *, base_url: str, model: str = "", api_key: str = "lm-studio"):
        self.base_url = _normalize_local_llm_base_url(base_url)
        self.model = model.strip()
        self.api_key = api_key
        self.provider_label = "Local LLM"
        self.messages = _LocalMessagesAPI(self)

    async def discover_chat_models(self) -> list[str]:
        info = await _probe_local_llm_server(self.base_url)
        if not info["ok"]:
            raise RuntimeError(info["error"])
        self.base_url = info.get("resolved_base_url", self.base_url).rstrip("/")
        models: list[str] = []
        for item in info["models"]:
            model_id = str(item.get("id", "")).strip()
            lowered = model_id.lower()
            if model_id and "embedding" not in lowered and "embed" not in lowered:
                models.append(model_id)
        return models

    async def discover_model(self) -> str:
        model = self.model.strip()
        if model:
            return model
        models = await self.discover_chat_models()
        if models:
            self.model = models[0]
            return self.model
        return ""

    async def resolve_model(self, requested_model: str | None = None) -> str:
        configured_model = self.model.strip()
        if configured_model:
            return configured_model

        requested_model = (requested_model or "").strip()
        available_models = await self.discover_chat_models()
        if requested_model and requested_model in available_models:
            self.model = requested_model
            return requested_model

        if available_models:
            self.model = available_models[0]
            return self.model

        return ""


class NvidiaLLMClient:
    def __init__(self, *, base_url: str, model: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.model = (model or DEFAULT_NVIDIA_LLM_MODEL).strip()
        self.api_key = api_key.strip()
        self.provider_label = "NVIDIA NIM"
        self.messages = _LocalMessagesAPI(self)

    async def resolve_model(self, requested_model: str | None = None) -> str:
        return self.model or (requested_model or "").strip()


async def _probe_local_llm_server(base_url: str | None = None) -> dict:
    last_error = ""
    tried: list[str] = []
    headers = _get_local_llm_headers()

    for root in _build_local_llm_candidate_urls(base_url):
        tried.append(root)
        try:
            async with httpx.AsyncClient(timeout=5.0, trust_env=False) as http:
                response = await http.get(f"{root}/models", headers=headers)
                response.raise_for_status()
                payload = response.json()
            models = _normalize_local_model_payload(payload)
            return {
                "ok": True,
                "models": models,
                "error": "",
                "resolved_base_url": root,
            }
        except Exception as exc:
            last_error = _compact_error_text(str(exc))

    return {
        "ok": False,
        "models": [],
        "error": last_error or "All connection attempts failed",
        "tried": tried,
    }


async def _async_probe_and_update_local_llm() -> None:
    """Background task: probe local LLM server and update availability + pre-cache model.

    Called at startup (when provider == local) and after preferences save.
    Updates _llm_runtime_status and pre-sets anthropic_client.model so the first
    chat request doesn't pay an extra HTTP round-trip to discover the loaded model.
    """
    global anthropic_client
    info = await _probe_local_llm_server()
    if not info["ok"]:
        _mark_llm_unavailable(info.get("error") or "Local server not reachable")
        log.warning(f"Local LLM server probe failed: {info.get('error')}")
        return

    models: list[str] = [
        str(m.get("id", "")).strip()
        for m in (info.get("models") or [])
        if m.get("id") and "embed" not in str(m.get("id", "")).lower()
    ]
    if not models:
        _mark_llm_unavailable("Local server is reachable, but no chat model is loaded. Load a model in LM Studio first.")
        log.warning("Local LLM server has no chat models loaded")
        return

    # Pre-cache the model so the first actual request skips discovery
    if isinstance(anthropic_client, LocalLLMClient) and not anthropic_client.model:
        configured = get_local_llm_model().strip()
        anthropic_client.model = configured if configured in models else models[0]
        log.info(f"Local LLM model pre-cached: {anthropic_client.model}")
    if isinstance(anthropic_client, LocalLLMClient):
        anthropic_client.base_url = info.get("resolved_base_url", anthropic_client.base_url).rstrip("/")

    _mark_llm_available()
    log.info(
        "Local LLM endpoint resolved to %s",
        info.get("resolved_base_url", get_local_llm_base_url()),
    )
    log.info(f"Local LLM server ready — model: {getattr(anthropic_client, 'model', '?')}")


def _start_local_llm_server() -> tuple[bool, str]:
    if not LMS_CLI_PATH.exists():
        return False, "LM Studio CLI was not found."
    try:
        subprocess.Popen(
            [str(LMS_CLI_PATH), "server", "start", "--port", "1234", "--bind", "127.0.0.1"],
            cwd=str(LMS_CLI_PATH.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, "LM Studio server start requested."
    except Exception as exc:
        return False, _compact_error_text(str(exc))


async def _ensure_local_llm_server() -> tuple[bool, dict]:
    info = await _probe_local_llm_server()
    if info["ok"]:
        return True, info
    started, start_message = _start_local_llm_server()
    if not started:
        return False, {"ok": False, "models": [], "error": start_message}
    await asyncio.sleep(3)
    info = await _probe_local_llm_server()
    if info["ok"]:
        return True, info
    if not info.get("error"):
        info["error"] = start_message
    return False, info


async def _probe_local_tts_server(base_url: str | None = None, engine: str | None = None) -> dict:
    resolved_engine = get_local_tts_engine() if engine is None else normalize_local_tts_engine(engine)
    resolved_base_url = get_local_tts_base_url(resolved_engine) if base_url is None else str(base_url).rstrip("/")
    health_url = f"{resolved_base_url}/health"
    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as http:
            response = await http.get(health_url)
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        payload_status = str(payload.get("status", "")).strip().lower()
        payload_engine_raw = str(payload.get("engine", "")).strip().lower()
        engine_match = not payload_engine_raw or normalize_local_tts_engine(payload_engine_raw) == resolved_engine
        ok = response.status_code == 200 and payload_status == "ok" and engine_match
        if response.status_code != 200:
            error = f"HTTP {response.status_code}"
        elif not engine_match:
            error = f"Health endpoint is serving {payload.get('engine', 'unknown')} while {resolved_engine} was requested"
        elif payload_status and payload_status != "ok":
            error = str(payload.get("last_error") or payload_status)
        else:
            error = ""
        return {
            "ok": ok,
            "status_code": response.status_code,
            "payload": payload,
            "engine": resolved_engine,
            "engine_match": engine_match,
            "resolved_base_url": resolved_base_url,
            "error": error,
            "last_error": str(payload.get("last_error") or error or "").strip(),
        }
    except Exception as exc:
        compact_error = _compact_error_text(str(exc))
        return {
            "ok": False,
            "status_code": 0,
            "payload": {},
            "engine": resolved_engine,
            "engine_match": False,
            "resolved_base_url": resolved_base_url,
            "error": compact_error,
            "last_error": compact_error,
        }


def _start_local_tts_server(
    base_url: str | None = None,
    engine: str | None = None,
    model_path: str | None = None,
) -> tuple[bool, str]:
    resolved_engine = get_local_tts_engine() if engine is None else normalize_local_tts_engine(engine)
    server_path = LOCAL_TTS_SERVER_PATHS.get(resolved_engine, LOCAL_TTS_SERVER_PATHS[DEFAULT_LOCAL_TTS_ENGINE])
    if not server_path.exists():
        return False, f"Local {resolved_engine} TTS server not found at {server_path}"

    resolved_base_url = get_local_tts_base_url(resolved_engine) if base_url is None else str(base_url).strip()
    if not resolved_base_url:
        resolved_base_url = get_default_local_tts_base_url(resolved_engine)
    if not re.match(r"^https?://", resolved_base_url, re.IGNORECASE):
        resolved_base_url = f"http://{resolved_base_url.lstrip('/')}"
    parsed = urlparse(resolved_base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or urlparse(get_default_local_tts_base_url(resolved_engine)).port or 8881
    env = os.environ.copy()
    env["TTS_HOST"] = host
    env["TTS_PORT"] = str(port)
    env["JARVIS_LOCAL_TTS_ENGINE"] = resolved_engine
    if resolved_engine == "voxcpm":
        env["VOXCPM_MODEL_PATH"] = (model_path or get_local_tts_model_path()).strip() or DEFAULT_VOXCPM_MODEL_PATH
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        subprocess.Popen(
            [sys.executable, str(server_path)],
            cwd=PROJECT_DIR,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        return True, f"Local {resolved_engine} TTS server start requested on {host}:{port}"
    except Exception as exc:
        return False, _compact_error_text(str(exc))


async def _ensure_local_tts_server(
    base_url: str | None = None,
    engine: str | None = None,
    model_path: str | None = None,
) -> tuple[bool, dict]:
    resolved_engine = get_local_tts_engine() if engine is None else normalize_local_tts_engine(engine)
    resolved_base_url = get_local_tts_base_url(resolved_engine) if base_url is None else str(base_url).strip()
    if not resolved_base_url:
        resolved_base_url = get_default_local_tts_base_url(resolved_engine)
    if not re.match(r"^https?://", resolved_base_url, re.IGNORECASE):
        resolved_base_url = f"http://{resolved_base_url.lstrip('/')}"

    info = await _probe_local_tts_server(resolved_base_url, resolved_engine)
    if info["ok"]:
        return True, info
    started, start_message = _start_local_tts_server(resolved_base_url, resolved_engine, model_path)
    if not started:
        return False, {
            "ok": False,
            "engine": resolved_engine,
            "error": start_message,
            "resolved_base_url": resolved_base_url,
        }
    wait_budget = 120 if resolved_engine == "voxcpm" else 12
    poll_interval = 3 if resolved_engine == "voxcpm" else 1
    elapsed = 0
    info = {"ok": False, "error": "", "resolved_base_url": resolved_base_url, "engine": resolved_engine}
    while elapsed < wait_budget:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        info = await _probe_local_tts_server(resolved_base_url, resolved_engine)
        if info["ok"]:
            return True, info
    if resolved_engine == "voxcpm":
        install_hint = "Make sure tts_server_voxcpm.py can start, the voxcpm package is installed, and VOXCPM_MODEL_PATH points to a valid VoxCPM2 folder."
    else:
        install_hint = "Make sure tts_server.py can start, kokoro-onnx is installed, and the Kokoro model files are present."
    if not info.get("error"):
        info["error"] = f"{start_message}. {install_hint}"
    else:
        info["error"] = f"{info['error']}. {install_hint}"
    return False, info


def _get_fish_default_voice_id() -> str:
    return _env_pref("FISH_VOICE_ID", DEFAULT_FISH_VOICE_ID)


def _get_fish_api_key() -> str:
    return os.getenv("FISH_API_KEY", "").strip()


def _get_fish_indonesian_voice_id() -> str:
    return os.getenv("FISH_VOICE_ID_ID", "").strip()


def _get_fish_indonesian_fallback_voice_id() -> str:
    return _env_pref("FISH_VOICE_ID_ID_FALLBACK", DEFAULT_FISH_VOICE_ID_ID_FALLBACK)


def _get_fish_tts_backend() -> str:
    backend = _env_pref("FISH_TTS_BACKEND", DEFAULT_FISH_TTS_BACKEND).strip()
    return backend if backend in {"s1", "s2-pro"} else DEFAULT_FISH_TTS_BACKEND


def _is_fish_tts_debug_enabled() -> bool:
    return _env_pref("FISH_TTS_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}


def get_user_name() -> str:
    return _env_pref("USER_NAME", "Taufik")


def get_honorific() -> str:
    raw = _env_pref("HONORIFIC", "sir")
    lowered = raw.strip().lower()
    if lowered == "none":
        return get_user_name()
    if _is_indonesian_mode():
        return get_user_name()
    return raw


def get_user_location() -> str:
    return _env_pref("USER_LOCATION", "Cimahi")


def get_user_country() -> str:
    return _env_pref("USER_COUNTRY", "Indonesia")


def get_user_timezone() -> str:
    return _env_pref("USER_TIMEZONE", "Asia/Jakarta")


def get_user_language() -> str:
    value = _env_pref("USER_LANGUAGE", "en").lower()
    return "id" if value.startswith("id") else "en"


def get_local_fallback_mode() -> bool:
    value = _env_pref("JARVIS_LOCAL_FALLBACK_MODE", "1").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _is_indonesian_mode() -> bool:
    return get_user_language() == "id"


def _localized_text(english: str, indonesian: str) -> str:
    return indonesian if _is_indonesian_mode() else english


_llm_runtime_status = {
    "provider": get_llm_provider(),
    "available": bool(ANTHROPIC_API_KEY.strip()) if get_llm_provider() == "anthropic" else False,
    "reason": "",
    "last_error": "",
    "last_failed_at": 0.0,
}


def _compact_error_text(error_text: str) -> str:
    return re.sub(r"\s+", " ", (error_text or "").strip())


def _classify_llm_error(error_text: str) -> str:
    normalized = _compact_error_text(error_text).lower()
    if not normalized:
        return ""
    if "usage limits" in normalized or "credit balance" in normalized or "spend limit" in normalized:
        return "usage_limit"
    if "invalid x-api-key" in normalized or "authentication" in normalized or "api key" in normalized:
        return "auth"
    if "overloaded" in normalized or "529" in normalized:
        return "overloaded"
    if "rate limit" in normalized or "429" in normalized:
        return "rate_limited"
    if "timed out" in normalized or "connection" in normalized or "network" in normalized:
        return "network"
    return "unknown"


def _mark_llm_available() -> None:
    _llm_runtime_status["provider"] = get_llm_provider()
    _llm_runtime_status["available"] = True
    _llm_runtime_status["reason"] = ""
    _llm_runtime_status["last_error"] = ""
    _llm_runtime_status["last_failed_at"] = 0.0


def _mark_llm_unavailable(error_text: str) -> None:
    compact = _compact_error_text(error_text)
    _llm_runtime_status["provider"] = get_llm_provider()
    _llm_runtime_status["available"] = False
    _llm_runtime_status["reason"] = _classify_llm_error(compact)
    _llm_runtime_status["last_error"] = compact[:300]
    _llm_runtime_status["last_failed_at"] = time.time()


def _extract_llm_recovery_hint(error_text: str) -> str:
    match = re.search(r"regain access on ([0-9-]+ at [0-9:]+ UTC)", error_text or "", re.IGNORECASE)
    return match.group(1) if match else ""


def _llm_unavailable_summary() -> str:
    error_text = _llm_runtime_status.get("last_error", "")
    reason = _llm_runtime_status.get("reason", "")
    provider = str(_llm_runtime_status.get("provider") or get_llm_provider()).lower()
    provider_name = "Anthropic" if provider == "anthropic" else "my local model"
    recovery_hint = _extract_llm_recovery_hint(error_text)

    if reason == "usage_limit":
        if recovery_hint:
            return _localized_text(
                f"My {provider_name} usage limit is currently exhausted. Access should return on {recovery_hint}.",
                f"Akses {provider_name}-ku sedang kena batas pemakaian. Biasanya aktif lagi pada {recovery_hint}.",
            )
        return _localized_text(
            f"My {provider_name} usage limit is currently exhausted.",
            f"Akses {provider_name}-ku sedang kena batas pemakaian.",
        )
    if reason == "auth":
        return _localized_text(
            f"My {provider_name} authentication is failing right now.",
            f"Autentikasi {provider_name}-ku sedang gagal sekarang.",
        )
    if reason == "overloaded":
        return _localized_text(
            f"{provider_name.capitalize()} is overloaded right now.",
            f"{provider_name.capitalize()} sedang padat sekarang.",
        )
    if reason == "rate_limited":
        return _localized_text(
            f"{provider_name.capitalize()} is rate-limiting me right now.",
            f"{provider_name.capitalize()} sedang membatasi laju permintaanku sekarang.",
        )
    if reason == "network":
        return _localized_text(
            f"My link to {provider_name} is unstable right now.",
            f"Koneksi ke {provider_name} sedang tidak stabil sekarang.",
        )
    if provider == "anthropic" and not ANTHROPIC_API_KEY.strip():
        return _localized_text(
            "My Anthropic API key is not configured right now.",
            "API key Anthropic-ku belum dikonfigurasi sekarang.",
        )
    if provider == "local":
        return _localized_text(
            "My local model is not available right now.",
            "Model lokal-ku belum tersedia sekarang.",
        )
    if provider == "nvidia":
        return _localized_text(
            "My NVIDIA DeepSeek provider is not available right now.",
            "Provider NVIDIA DeepSeek-ku belum tersedia sekarang.",
        )
    return _localized_text(
        "My main language model is unavailable right now.",
        "Model bahasa utamaku sedang tidak tersedia sekarang.",
    )


def _make_llm_client() -> Any | None:
    provider = get_llm_provider()
    if provider == "local":
        return LocalLLMClient(
            base_url=get_local_llm_base_url(),
            model=get_local_llm_model(),
            api_key=get_local_llm_api_key(),
        )
    if provider == "nvidia":
        key = get_nvidia_api_key()
        if key:
            return NvidiaLLMClient(
                base_url=get_nvidia_llm_base_url(),
                model=get_nvidia_llm_model(),
                api_key=key,
            )
        return None
    if ANTHROPIC_API_KEY.strip():
        return anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return None


def _refresh_llm_client() -> Any | None:
    global anthropic_client
    anthropic_client = _make_llm_client()
    _llm_runtime_status["provider"] = get_llm_provider()
    if anthropic_client:
        # Mark available optimistically for both providers.
        # For local, a background probe (_async_probe_and_update_local_llm) will correct
        # the status if LM Studio is not actually reachable.
        _mark_llm_available()
    else:
        if get_llm_provider() == "local":
            _mark_llm_unavailable("Local provider is configured, but no local client is ready.")
        elif get_llm_provider() == "nvidia":
            _mark_llm_unavailable("NVIDIA API key not configured.")
        else:
            _mark_llm_unavailable("Anthropic API key not configured.")
    return anthropic_client


async def generate_local_fallback_response(text: str, llm_error: str | None = None) -> str:
    if llm_error:
        _mark_llm_unavailable(llm_error)

    normalized = " ".join((text or "").lower().split())
    llm_summary = _llm_unavailable_summary()
    now = datetime.now()

    if any(phrase in normalized for phrase in (
        "are you listening", "can you hear me", "do you hear me", "apakah kau mendengarku",
        "apakah kamu mendengarku", "dengar aku", "masih dengar", "you there", "awake",
    )):
        return _localized_text(
            f"Loud and clear. {llm_summary} I can still handle local commands.",
            f"Aku dengar jelas. {llm_summary} Aku masih bisa menangani perintah lokal.",
        )

    if any(phrase in normalized for phrase in (
        "what can you do", "what can you still do", "help", "bisa apa", "bisa bantu apa",
        "apa yang bisa kamu lakukan", "fitur apa", "mode fallback", "fallback mode",
    )):
        return _localized_text(
            f"{llm_summary} I can still open apps, control Spotify and lights, manage folders, read simple status, and hand work to Codex.",
            f"{llm_summary} Aku masih bisa membuka aplikasi, mengontrol Spotify dan lampu, mengelola folder, membaca status sederhana, dan melempar pekerjaan ke Codex.",
        )

    if any(phrase in normalized for phrase in (
        "anthropic", "api", "llm", "language model", "limit", "quota", "usage", "kenapa",
        "why are you", "why aren't you", "error", "masalah apa", "kenapa kamu", "api habis",
    )):
        return llm_summary

    if any(phrase in normalized for phrase in (
        "briefing", "brief me", "bacakan briefing", "ringkasan", "cuaca", "weather",
        "berita", "news", "headline", "headlines",
    )):
        if any(token in normalized for token in ("briefing", "brief me", "bacakan briefing", "ringkasan")):
            return await _get_spoken_briefing_text()
        wants_weather = any(token in normalized for token in ("cuaca", "weather"))
        wants_news = any(token in normalized for token in ("berita", "news", "headline", "headlines"))
        parts: list[str] = []
        if wants_weather:
            weather = (_ctx_cache.get("weather") or "").strip()
            if weather:
                parts.append(weather)
        if wants_news:
            news = (_ctx_cache.get("news") or "").strip()
            if news:
                parts.append(news)
        if parts:
            return " ".join(parts)
        return _localized_text(
            "My local briefing cache is not ready just yet.",
            "Cache ringkasan lokalku belum siap sepenuhnya.",
        )

    if any(phrase in normalized for phrase in ("what time", "jam berapa", "tanggal berapa", "what date", "hari apa")):
        return _localized_text(
            now.strftime("It's %A, %d %B %Y, %I:%M %p."),
            now.strftime("Sekarang %A, %d %B %Y, pukul %H:%M."),
        )

    if any(phrase in normalized for phrase in ("who are you", "siapa kamu", "your name", "nama kamu")):
        return _localized_text(
            f"I'm JARVIS, here with local fallback active for you, {get_user_name()}.",
            f"Aku JARVIS, dan mode fallback lokal sedang aktif untukmu, {get_user_name()}.",
        )

    return _localized_text(
        f"{llm_summary} I can still help best with direct local commands like apps, Spotify, lights, folders, briefings, and Codex handoff.",
        f"{llm_summary} Aku masih paling efektif untuk perintah lokal langsung seperti aplikasi, Spotify, lampu, folder, briefing, dan handoff ke Codex.",
    )


def _save_feedback_log(kind: str, command: str, response: str, correction: str) -> None:
    """
    Append a feedback entry to the local feedback log and, if available, to
    the primary Obsidian vault under JARVIS/jarvis-feedback-log.md.
    kind: "confirm" | "correction"
    """
    from datetime import datetime

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    if kind == "confirm":
        block = (
            f"\n### ✓ Confirmed Correct — {time_str}\n"
            f"- **Command**: {command}\n"
            f"- **Response**: {response}\n"
        )
    else:
        block = (
            f"\n### ✗ Correction — {time_str}\n"
            f"- **Command**: {command}\n"
            f"- **JARVIS said**: {response}\n"
            f"- **Should have**: {correction}\n"
        )

    header = f"\n## {date_str}\n"

    def _write(path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            # Add date header only if it's not already there for today
            section = header + block if date_str not in existing else block
            with open(path, "a", encoding="utf-8") as f:
                f.write(section)
        except Exception as exc:
            log.warning(f"_save_feedback_log failed for {path}: {exc}")

    # Always write to local JARVIS directory
    local_path = Path(__file__).parent / "jarvis-feedback-log.md"
    _write(local_path)

    # Also write to Obsidian vault if available (import from actions)
    try:
        from actions import _get_primary_obsidian_vault
        vault = _get_primary_obsidian_vault()
        if vault:
            _, vault_path = vault
            obs_path = Path(vault_path) / "JARVIS" / "jarvis-feedback-log.md"
            _write(obs_path)
    except Exception:
        pass


def _describe_wiz_cmd(cmd: dict) -> str:
    """Return a short human-readable description of a WiZ command for feedback display."""
    kind = cmd.get("kind", "")
    target = cmd.get("target_device") or cmd.get("target_name")
    prefix = f"Lampu [{target}]" if target else "Lampu"
    brightness = cmd.get("brightness")
    b_suffix = f", {brightness}%" if brightness is not None else ""
    if kind == "power":
        state = cmd.get("state", "on")
        return f"{prefix}: {'nyala' if state == 'on' else 'mati'}"
    if kind == "brightness":
        val = cmd.get("value", 50)
        return f"{prefix}: kecerahan {val}%"
    if kind == "color":
        label = cmd.get("label") or cmd.get("color") or cmd.get("hex", "")
        return f"{prefix}: warna {label}{b_suffix}"
    if kind == "temperature":
        label = cmd.get("label", "")
        temp = cmd.get("temperature", "")
        desc = label or f"{temp}K"
        return f"{prefix}: {desc}{b_suffix}"
    if kind == "scene":
        scene = cmd.get("label") or cmd.get("scene") or cmd.get("name", "")
        return f"{prefix}: mode {scene}"
    return f"{prefix}: perintah dijalankan"


def _describe_air_fan_cmd(cmd: dict) -> str:
    """Return a short human-readable description of an air fan command."""
    kind = cmd.get("kind", "")
    if kind == "dashboard":
        return "Fan: dashboard"
    if kind == "status":
        return "Fan: status"
    if kind == "speed":
        return f"Fan: {cmd.get('value', '?')}%"
    if kind == "profile":
        return f"Fan: profile {cmd.get('profile', '?')}"
    return "Fan: perintah dijalankan"


def _describe_action(action_name: str, target) -> str:
    """Return a short description of any dispatched action for the feedback box."""
    if action_name == "wiz":
        if isinstance(target, dict):
            return _describe_wiz_cmd(target)
        return "Lampu: perintah dijalankan"
    if action_name == "fan":
        if isinstance(target, dict):
            return _describe_air_fan_cmd(target)
        return "Fan: perintah dijalankan"
    if action_name in ("open_app", "open_terminal"):
        app = target if isinstance(target, str) and target else "terminal"
        return f"Buka: {app}"
    if action_name in ("play_spotify", "spotify_control"):
        return f"Spotify: {target}" if isinstance(target, str) else "Spotify: kontrol musik"
    if action_name == "remember":
        snippet = str(target)[:60] + ("…" if len(str(target)) > 60 else "")
        return f"Ingat: {snippet}"
    if action_name == "add_task":
        return f"Tugas ditambah: {str(target)[:50]}"
    if action_name == "browse":
        return f"Buka browser: {str(target)[:60]}"
    if action_name == "research":
        return f"Riset: {str(target)[:60]}"
    if action_name == "build":
        return f"Build: {str(target)[:60]}"
    return f"Aksi: {action_name}"


def _language_instruction_block() -> str:
    if _is_indonesian_mode():
        return (
            "LANGUAGE MODE:\n"
            "- Speak in natural spoken Indonesian by default.\n"
            "- Keep the same JARVIS persona: calm, elegant, concise, and lightly dry.\n"
            f"- You may still address {get_user_name()} as \"{get_honorific()}\" naturally.\n"
            "- Do not sound like literal English translated into Indonesian.\n"
            "- Do not use the word 'sir' in Indonesian mode unless the user explicitly asks for it.\n"
            "- Prefer natural Indonesian acknowledgements such as 'Siap', 'Baik', 'Sedang saya cek', 'Sudah saya buka', and 'Akan saya tangani'.\n"
            "- Keep sentences clean and conversational, not theatrical or overly formal.\n"
            "- If the user explicitly asks for English, you may switch for that reply.\n"
            "- Keep proper names, product names, and technical terms accurate.\n"
        )
    return (
        "LANGUAGE MODE:\n"
        "- Speak in natural spoken English by default.\n"
        "- If the user explicitly asks for Indonesian, you may switch for that reply.\n"
    )

JARVIS_SYSTEM_PROMPT = """\
You are JARVIS — Just A Rather Very Intelligent System. You serve as {user_name}'s AI assistant, modeled precisely after Tony Stark's AI from the MCU films.

VOICE & PERSONALITY:
- British butler elegance with understated dry wit
- Address {user_name} as "{honorific}" naturally — not every sentence, but regularly
- Never say "How can I help you?" or "Is there anything else?" — just act
- Deliver bad news calmly, like reporting weather: "We have a slight problem, sir."
- Your humor is observational, never jokes: state facts and let implications land
- Economy of language — say more with less. No filler, no corporate-speak
- When things go wrong, get CALMER, not more alarmed

TIME & WEATHER AWARENESS:
- Current time: {current_time}
- Greet accordingly: "Good morning, sir" / "Good evening, sir"
- {weather_info}

MASTER PROFILE:
- Your master/owner is {user_name}
- Default local context: {user_location}, {user_country}
- If the user asks about "here", local weather, local news, or what's happening nearby without naming a place, assume {user_location}
- Treat Taufik as the person you serve and the owner of this system

LOCAL INTEL SNAPSHOT:
{news_context}
- Treat this as your pre-fetched briefing for Cimahi, Indonesia, and the wider world
- When the user asks for "berita terpanas", prioritize today's high-signal headlines and give extra attention to Iran-America developments when relevant

CONVERSATION STYLE:
- "Will do, sir." — acknowledging tasks
- "For you, sir, always." — when asked for something significant
- "As always, sir, a great pleasure watching you work." — dry wit
- "I've taken the liberty of..." — proactive actions
- Lead status reports with data: numbers first, then context
- When you don't know something: "I'm afraid I don't have that information, sir" not "I don't know"

SELF-AWARENESS:
You ARE the JARVIS project at {project_dir} on {user_name}'s computer. Your code is Python (FastAPI server, WebSocket voice, Fish Audio TTS, Anthropic API). You were built by {user_name}. If asked about yourself, your code, how you work, or your line count — use [ACTION:PROMPT_PROJECT] to check the jarvis project. You have full access to your own source code.

YOUR CAPABILITIES (these are REAL and ACTIVE — you CAN do all of these RIGHT NOW):
- You CAN open Terminal.app via AppleScript
- You CAN open Google Chrome and browse any URL or search query
- You CAN open desktop applications like Obsidian, Chrome, File Explorer, and terminal tools when the current system supports them
- You CAN open Spotify and play requested songs on the local machine when Spotify is installed
- You CAN control the local air cleaner fan directly over the LAN. Use [ACTION:FAN] followed by the user's fan command in natural language. Supported: status ("fan status", "cek status fan"), set manual speed ("set fan speed 80", "fan 60 persen"), switch profiles ("quiet", "normal", "dynamic"), and open the dashboard ("open air control", "buka kontrol fan"). The user may say "fan", "kipas", "blower", "air control", or "air cleaner". ALWAYS use [ACTION:FAN] for these fan-control requests if the fast system did not already handle them.
- You CAN control local WiZ smart lamps through the LAN-only WiZ controller. Use [ACTION:WIZ] followed by the user's light command in natural language. Supported: power on/off ("nyalakan lampu", "lights off"), brightness ("lampu 50 persen", "brightness 70%"), colors ("lampunya merah", "lights blue"), white modes ("warm white", "putih terang", "daylight"), scene presets ("lampu kerja", "mode santai", "night mode", "gaming mode", "lampu nonton", "lampu baca"), device targeting ("lampu di kamar", "lights in bedroom"), and discovery/scan ("cek lampu", "scan lampu", "berapa lampu", "ada lampu apa", "scan the lights", "how many lights", "check lights", "lamp status"). The user may say "WiZ", "whiz", "lights", "lampu", "smart lights", or "light controller". ALWAYS use [ACTION:WIZ] for ANY light, lamp, or scan request — never just talk about it. For scan/status questions like "ada berapa lampu?" or "lampu yang online?" always use [ACTION:WIZ] scan.
- You CAN spawn Claude Code in a Terminal window for coding tasks
- You CAN create project folders on the Desktop
- You CAN check Desktop projects and their git status
- You CAN plan complex tasks by asking smart questions before executing
- You CAN see what's on {user_name}'s screen — open windows, active apps, and screenshot vision
- You CAN read {user_name}'s calendar — today's events, upcoming meetings, schedule overview
- You CAN read {user_name}'s email (READ-ONLY) — unread count, recent messages, search by sender/subject. You CANNOT send, delete, or modify emails.
- You CAN read Apple Notes and create NEW notes — but you CANNOT edit or delete existing notes
- You CAN manage tasks — create, complete, and list to-do items with priorities and due dates
- You CAN help plan {user_name}'s day — combine calendar events, tasks, and priorities into an organized plan
- You CAN remember facts about {user_name} — preferences, decisions, goals. Use [ACTION:REMEMBER] to store important info.

DAY PLANNING:
When {user_name} asks to plan his day or schedule, DO NOT dispatch to a project. Instead:
1. Look at the calendar context and tasks already in your system prompt
2. Ask what his priorities are
3. Help organize by suggesting time blocks and task order
4. Use [ACTION:ADD_TASK] to create tasks he agrees to
5. Use [ACTION:ADD_NOTE] to save the plan as a note
Keep the planning conversational — don't try to do everything in one response.

BUILD PLANNING:
When {user_name} wants to BUILD something new:
- Do NOT immediately dispatch [ACTION:BUILD]. Ask 1-2 quick questions FIRST to nail down specifics.
- Good questions: "What should this look like?" / "Any specific features?" / "Which framework?"
- If he says "just build it" or "figure it out" — skip questions, use React + Tailwind as defaults.
- Once you have enough info, confirm the plan in ONE sentence and THEN dispatch [ACTION:BUILD] with a detailed description.
- The DISPATCHES section shows what you're currently building and what finished recently.
- When asked "where are we at" or "status" — check DISPATCHES, don't re-dispatch.
- NEVER hallucinate progress. If the build is still running, say "Still working on it, sir" — don't make up details about what's happening.
- NEVER guess localhost ports. Check the DISPATCHES section for the actual URL. If a dispatch says "Running at http://localhost:5174" — use THAT URL, not a guess.
- When asked to "pull it up" or "show me" — use [ACTION:BROWSE] with the URL from DISPATCHES. Do NOT dispatch to the project again just to find the URL.
IMPORTANT: Actions like opening Terminal, Chrome, or building projects are handled AUTOMATICALLY by your system — you do NOT need to describe doing them. If the user asks you to build something or search something, your system will handle the execution separately. In your response, just TALK — have a conversation. Don't say "I'll build that now" or "Claude Code is working on..." unless your system has actually triggered the action.
If the user asks you to do something you genuinely can't do, say "I'm afraid that's beyond my current reach, sir." Don't fake executing actions.

YOUR INTERFACE:
The user interacts with you through a web browser showing a particle orb visualization that reacts to your voice. The interface has these controls:
- **Three-dot menu** (top right): contains Settings, Restart Server, and Fix Yourself options
- **Settings panel**: Opens from the menu. Users can enter API keys (Anthropic, Fish Audio), test connections, set their name and preferences, and see system status (calendar, mail, notes connectivity). Keys are saved to the .env file.
- **Mute button**: Toggles your listening on/off. When muted, you can't hear the user. They click it again to unmute.
- **Restart Server**: Restarts your backend process. Useful if something seems stuck.
- **Fix Yourself**: Opens Claude Code in your own project directory so you can debug and fix issues in your own code.
- **The orb**: The glowing particle visualization in the center. It reacts to your voice when speaking, pulses when listening, and swirls when thinking.

If asked about any of these, explain them briefly and naturally. If the user is having trouble, suggest the relevant control: "Try the settings panel — the gear icon in the top right." or "The mute button may be active, sir."

SPEECH-TO-TEXT CORRECTIONS (the user speaks, speech recognition may mishear):
- "Cloud code" or "cloud" = "Claude Code" or "Claude"
- "Travis" = "JARVIS"
- "clock code" = "Claude Code"
- "whiz", "wizz", or "weez" may mean "WiZ"

RESPONSE LENGTH — THIS IS CRITICAL:
ONE sentence is ideal. TWO is the maximum for the spoken part. Never three.
No markdown, no bullet points, no code blocks in voice responses.
Action tags at the end do NOT count toward your sentence limit.

BANNED PHRASES — NEVER USE THESE:
- "Absolutely" / "Absolutely right"
- "Great question"
- "I'd be happy to"
- "Of course"
- "How can I help"
- "Is there anything else"
- "I apologize"
- "I should clarify"
- "I cannot" (for things listed in YOUR CAPABILITIES)
- "I don't have access to" (instead: "I'm afraid that's beyond my current reach, sir")
- "As an AI" (never break character)
- "Let me know if" / "Feel free to"
- Any sentence starting with "I"

INSTEAD SAY:
- "Will do, sir."
- "Right away, sir."
- "Understood."
- "Consider it done."
- "Done, sir."
- "Terminal is open."
- "Pulled that up in Chrome."

ACTION SYSTEM:
When you decide the user needs something DONE (not just discussed), include an action tag in your response:
- [ACTION:SCREEN] — capture and describe what's visible on the user's screen. Use when user says "look at my screen", "what's running", "what do you see", etc. Do NOT use PROMPT_PROJECT for screen requests.
- [ACTION:BUILD] description — when user wants a project built. Claude Code does the work.
- [ACTION:BROWSE] url or search query — when user wants to see a webpage or search result in Chrome
- [ACTION:OPEN_APP] app name — when user wants a desktop app opened, such as Obsidian or File Explorer
- [ACTION:PLAY_SPOTIFY] song title or artist request — when user wants music started in Spotify
- [ACTION:RESEARCH] detailed research brief — when user wants real research with real data. Claude Code will browse the web, find real listings/data, and create a report document. Give it a detailed brief of what to find.
- [ACTION:OPEN_TERMINAL] — when user just wants a fresh Claude Code terminal with no specific project
- [ACTION:WIZ] light command — ALWAYS use this for ANY lamp/light request that wasn't already auto-handled. Pass the user's command verbatim so it can be parsed. Examples:
  "nyalakan lampu" → [ACTION:WIZ] nyalakan lampu
  "lampunya merah" → [ACTION:WIZ] lampunya merah
  "mode santai" → [ACTION:WIZ] mode santai
  "lampu 50 persen" → [ACTION:WIZ] lampu 50 persen
  "matikan lampu di kamar" → [ACTION:WIZ] matikan lampu di kamar
  "gaming mode" → [ACTION:WIZ] gaming mode
  If a light command is already spoken by the fast system, DO NOT use this tag again.
CRITICAL: When the user asks about their SCREEN, what's RUNNING, or what they're LOOKING AT — ALWAYS use [ACTION:SCREEN] or let the fast action system handle it. NEVER use [ACTION:PROMPT_PROJECT] for screen requests. PROMPT_PROJECT is ONLY for working on code projects.

- [ACTION:PROMPT_PROJECT] project_name ||| prompt — THIS IS YOUR MOST POWERFUL ACTION. Use it whenever the user wants to work on, jump into, resume, check on, or interact with ANY existing project. You connect directly to Claude Code in that project and can read its response. Craft a clear prompt based on what the user wants. Examples:
  "jump into client engine" → [ACTION:PROMPT_PROJECT] The Client Engine ||| What is the current state of this project? Summarize what was being worked on most recently.
  "check for improvements on my-app" → [ACTION:PROMPT_PROJECT] my-app ||| Review the project and identify improvements we should make.
  "resume where we left off on harvey" → [ACTION:PROMPT_PROJECT] harvey ||| Summarize what was being worked on most recently and what we should focus on next.
- [ACTION:ADD_TASK] priority ||| title ||| description ||| due_date — create a task. Priority: high/medium/low. Due date: YYYY-MM-DD or empty.
  "remind me to call the client tomorrow" → [ACTION:ADD_TASK] medium ||| Call the client ||| Follow up on proposal ||| 2026-03-20
- [ACTION:ADD_NOTE] topic ||| content — save a note for future reference.
  "note that the API key expires in April" → [ACTION:ADD_NOTE] general ||| API key expires in April, need to renew before then
- [ACTION:COMPLETE_TASK] task_id — mark a task as done.
- [ACTION:REMEMBER] content — store an important fact about the user for future context.
  "I prefer React over Vue" → [ACTION:REMEMBER] User prefers React over Vue for frontend projects
  When the user CORRECTS you (says "itu salah", "harusnya", "lain kali", "ingat ini", etc.):
  1. Acknowledge briefly ("Understood, sir" / "Aku catat itu")
  2. ALWAYS use [ACTION:REMEMBER] to store EXACTLY what was wrong and what's correct
  3. Format: "Koreksi: [what user said was wrong] → harusnya [what's correct]"
  Example: "lain kali kalau aku bilang lampu santai, redupkan ke 30 persen" → [ACTION:REMEMBER] Koreksi: user bilang 'lampu santai' → brightness 30%, bukan 40%
- [ACTION:CREATE_NOTE] title ||| body — create a new Apple Note. For saving plans, ideas, lists.
  "save that as a note" → [ACTION:CREATE_NOTE] Day Plan March 19 ||| Morning: client calls. Afternoon: TikTok dashboard. Evening: JARVIS improvements.
- [ACTION:READ_NOTE] title search — read an existing Apple Note by title keyword.

You use Claude Code as your tool to build, research, and write code — but YOU are the one doing the work. Never say "Claude Code did X" or "Claude Code is asking" — say "I built X", "I'm checking on that", "I found X". You ARE the intelligence. Claude Code is just your hands.

IMPORTANT: When the user says "jump into X", "work on X", "check on X", "resume X", "go back to X" — ALWAYS use [ACTION:PROMPT_PROJECT]. You have the ability to connect to any project and work on it directly. DO NOT say you can't see terminal history or don't have access — you DO.

Place the tag at the END of your spoken response. Example:
"Right away, sir — connecting to The Client Engine now. [ACTION:PROMPT_PROJECT] The Client Engine ||| Review the current state and what was being worked on. What should we focus on next?"

IMPORTANT:
- Do NOT use action tags for casual conversation
- Do NOT use action tags if the user is still explaining (ask questions first)
- Do NOT use [ACTION:BROWSE] just because someone mentions a URL in conversation
- When in doubt, just TALK — you can always act later

SCREEN AWARENESS:
{screen_context}

SCHEDULE:
{calendar_context}

EMAIL:
{mail_context}

ACTIVE TASKS:
{active_tasks}

DISPATCHES:
If the DISPATCHES section shows a recent completed result for a project, DO NOT dispatch again. Use the existing result. Only re-dispatch if the user explicitly asks for a FRESH review or NEW information.
{dispatch_context}

KNOWN PROJECTS:
{known_projects}
"""


# Compact system prompt for local LLM mode.
# Local models typically have 4K–8K context windows — the full JARVIS prompt
# (with weather, news, calendar, mail, projects) overflows them.
# This version keeps personality + basic context under ~500 tokens.
JARVIS_LOCAL_SYSTEM_PROMPT = """\
You are JARVIS — Just A Rather Very Intelligent System. You serve as {user_name}'s AI assistant, modeled after Tony Stark's JARVIS.

PERSONALITY:
- British butler elegance, dry wit, economy of language
- Address {user_name} as "{honorific}"
- 1 sentence ideal, 2 maximum. No markdown, no bullet points.
- Never say "How can I help?" or "Is there anything else?" — just act
- Never start a sentence with "I"
- Never say "Absolutely", "Great question", "Of course", "I'd be happy to"

TIME: {current_time}
WEATHER: {weather_info}
LOCATION: {user_location}, {user_country}

MEMORY:
{memory_ctx}

FAN RULE:
When the user asks about the fan, kipas, blower, air control, or air cleaner, use [ACTION:FAN].

ACTIONS (add at end of response when needed):
- [ACTION:WIZ] light command — control smart lamps
- [ACTION:OPEN_APP] app name — open an application
- [ACTION:BROWSE] url or query — open browser
- [ACTION:REMEMBER] fact — store something important
- [ACTION:BUILD] description — build a project via Claude Code
- [ACTION:PROMPT_PROJECT] name ||| prompt — work on existing project

When the user asks about lights, lamps, or WiZ — ALWAYS use [ACTION:WIZ].
"""


# A lighter prompt specifically for small local models like Gemma 4 E4B.
JARVIS_LOCAL_LIGHT_SYSTEM_PROMPT = """\
You are JARVIS, {user_name}'s voice assistant.
Return only the final spoken answer.
Keep it short: one sentence is best, never more than two.
Sound calm, capable, and polished.
Address {user_name} as "{honorific}" when natural.
No markdown, no bullet points, no visible reasoning.
Do not use tool calls.
Do not output action tags.
Do not explain your thinking.

TIME: {current_time}
LOCATION: {user_location}, {user_country}
WEATHER: {weather_info}
MEMORY: {memory_ctx}
"""


JARVIS_LOCAL_RETRY_SYSTEM_PROMPT = """\
You are JARVIS, {user_name}'s concise voice assistant.
Reply immediately with one short final sentence.
No reasoning, no preamble, no markdown.
If the user is checking whether you can hear them, confirm clearly that you can.
If an action is required, append one action tag after the sentence.
"""


# ---------------------------------------------------------------------------
# Weather (wttr.in)
# ---------------------------------------------------------------------------

_cached_weather: Optional[str] = None
_weather_fetched: bool = False


def _build_location_label(location: str, country: str, nearest: Optional[dict] = None) -> str:
    region = ""
    if nearest:
        region = ((nearest.get("region") or [{}])[0]).get("value", "")

    location_bits = [location]
    if region and region.lower() != location.lower():
        location_bits.append(region)
    if country and country.lower() not in {location.lower(), region.lower()}:
        location_bits.append(country)
    return ", ".join(bit for bit in location_bits if bit)


async def fetch_weather() -> str:
    """Fetch current weather from wttr.in. Cached for the session."""
    global _cached_weather, _weather_fetched
    if _weather_fetched:
        return _cached_weather or "Weather data unavailable."
    _weather_fetched = True
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.get("https://wttr.in/?format=%l:+%C,+%t", headers={"User-Agent": "curl"})
            if resp.status_code == 200:
                _cached_weather = resp.text.strip()
                return _cached_weather
    except Exception as e:
        log.warning(f"Weather fetch failed: {e}")
    _cached_weather = None
    return "Weather data unavailable."


def _fetch_weather_brief_sync(location: str, country: str) -> str:
    """Fetch current local weather without requiring extra API keys."""
    import json as _json
    import urllib.parse as _urlparse
    import urllib.request as _urlrequest

    try:
        encoded_location = _urlparse.quote(location)
        url = f"https://wttr.in/{encoded_location}?format=j1"
        req = _urlrequest.Request(url, headers={"User-Agent": "curl/8.0"})
        with _urlrequest.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception:
        return "Weather unavailable (offline mode)."

    current = (data.get("current_condition") or [{}])[0]
    nearest = (data.get("nearest_area") or [{}])[0]
    desc = ((current.get("weatherDesc") or [{}])[0]).get("value", "Unknown conditions")
    temp_c = current.get("temp_C", "?")
    feels_c = current.get("FeelsLikeC", "?")
    humidity = current.get("humidity", "?")
    wind = current.get("windspeedKmph", "?")
    visibility = current.get("visibility", "?")

    location_label = _build_location_label(location, country, nearest)

    return (
        f"Current weather in {location_label}: {desc}, {temp_c}°C, "
        f"feels like {feels_c}°C, humidity {humidity}%, wind {wind} km/h, visibility {visibility} km."
    )


def _fetch_weather_snapshot_sync(location: str, country: str) -> dict[str, str]:
    """Fetch both current conditions and a simple tomorrow forecast."""
    import json as _json
    import urllib.parse as _urlparse
    import urllib.request as _urlrequest

    _offline = {
        "location_label": location,
        "current": "Weather unavailable (offline mode).",
        "tomorrow": "Weather unavailable (offline mode).",
        "current_voice": "Data cuaca tidak tersedia.",
        "tomorrow_voice": "Data cuaca tidak tersedia.",
    }
    try:
        encoded_location = _urlparse.quote(location)
        url = f"https://wttr.in/{encoded_location}?format=j1"
        req = _urlrequest.Request(url, headers={"User-Agent": "curl/8.0"})
        with _urlrequest.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception:
        return _offline

    current = (data.get("current_condition") or [{}])[0]
    nearest = (data.get("nearest_area") or [{}])[0]
    location_label = _build_location_label(location, country, nearest)

    desc = ((current.get("weatherDesc") or [{}])[0]).get("value", "Unknown conditions")
    temp_c = current.get("temp_C", "?")
    feels_c = current.get("FeelsLikeC", "?")
    humidity = current.get("humidity", "?")
    wind = current.get("windspeedKmph", "?")
    visibility = current.get("visibility", "?")

    forecast_days = data.get("weather") or []
    tomorrow = forecast_days[1] if len(forecast_days) > 1 else {}
    tomorrow_hourly = tomorrow.get("hourly") or [{}]
    tomorrow_slot = tomorrow_hourly[min(4, len(tomorrow_hourly) - 1)] if tomorrow_hourly else {}
    tomorrow_desc = ((tomorrow_slot.get("weatherDesc") or [{}])[0]).get("value", "Unknown conditions")
    tomorrow_min = tomorrow.get("mintempC", "?")
    tomorrow_max = tomorrow.get("maxtempC", "?")
    chance_rain = tomorrow_slot.get("chanceofrain", "")
    rain_suffix = f", rain chance {chance_rain}%" if chance_rain and chance_rain != "0" else ""
    rain_suffix_voice = f", peluang hujan {chance_rain} persen" if chance_rain and chance_rain != "0" else ""

    return {
        "location_label": location_label,
        "current": (
            f"Current weather in {location_label}: {desc}, {temp_c}Â°C, "
            f"feels like {feels_c}Â°C, humidity {humidity}%, wind {wind} km/h, visibility {visibility} km."
        ),
        "tomorrow": (
            f"Tomorrow in {location_label}: {tomorrow_desc}, {tomorrow_min}Â°C to {tomorrow_max}Â°C{rain_suffix}."
        ),
        "current_voice": (
            f"{desc}, {temp_c} derajat, terasa seperti {feels_c} derajat, "
            f"kelembapan {humidity} persen, angin {wind} kilometer per jam"
        ),
        "tomorrow_voice": (
            f"{tomorrow_desc} dengan suhu {tomorrow_min} sampai {tomorrow_max} derajat{rain_suffix_voice}"
        ),
    }


def _fetch_tomorrow_weather_brief_sync(location: str, country: str) -> str:
    """Fetch tomorrow's local weather without requiring extra API keys."""
    return _fetch_weather_snapshot_sync(location, country)["tomorrow"]


def _fetch_google_news_items_sync(query: str, limit: int = 8) -> list[dict]:
    """Fetch recent Google News RSS items for the last two days."""
    import html as _html
    import urllib.parse as _urlparse
    import urllib.request as _urlrequest
    import xml.etree.ElementTree as _etree

    search_query = f"{query} when:2d"
    url = (
        "https://news.google.com/rss/search?q="
        + _urlparse.quote(search_query)
        + "&hl=id&gl=ID&ceid=ID:id"
    )
    try:
        req = _urlrequest.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _urlrequest.urlopen(req, timeout=8) as resp:
            root = _etree.fromstring(resp.read())
    except Exception:
        return []

    items: list[dict] = []
    for item in root.findall("./channel/item"):
        raw_title = _html.unescape((item.findtext("title") or "").strip())
        headline, sep, source = raw_title.rpartition(" - ")
        if not sep:
            headline, source = raw_title, ""
        items.append(
            {
                "headline": headline.strip(),
                "source": source.strip(),
                "published": (item.findtext("pubDate") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
            }
        )
        if len(items) >= limit:
            break
    return items


def _collect_recent_news_buckets(query: str, tz_name: str, per_bucket: int = 2) -> dict[str, list[dict]]:
    """Collect today/yesterday headlines for one query."""
    from datetime import datetime, timedelta, timezone
    from email.utils import parsedate_to_datetime
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc

    now_local = datetime.now(tz)
    today = now_local.date()
    yesterday = today - timedelta(days=1)

    buckets: dict[str, list[dict]] = {"today": [], "yesterday": []}
    seen: set[str] = set()

    for item in _fetch_google_news_items_sync(query, limit=10):
        key = item["headline"].lower()
        if key in seen:
            continue
        seen.add(key)

        published = item.get("published", "")
        try:
            dt = parsedate_to_datetime(published)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local_date = dt.astimezone(tz).date()
        except Exception:
            local_date = today

        bucket_name = "today" if local_date == today else "yesterday" if local_date == yesterday else None
        if not bucket_name:
            continue
        if len(buckets[bucket_name]) >= per_bucket:
            continue

        buckets[bucket_name].append(item)

    return buckets


def _collect_focus_news_today(tz_name: str, queries: list[str], limit: int = 2) -> list[dict]:
    """Collect today's headlines from several queries, keeping only unique items."""
    merged: list[dict] = []
    seen: set[str] = set()

    for query in queries:
        buckets = _collect_recent_news_buckets(query, tz_name, per_bucket=limit)
        for item in buckets["today"]:
            key = item["headline"].lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= limit:
                return merged

    return merged


def _format_today_news_snapshot(label: str, items: list[dict]) -> str:
    lines = [f"{label} today:"]
    if not items:
        lines.append("- No major headlines captured.")
        return "\n".join(lines)

    for item in items:
        source = f" ({item['source']})" if item.get("source") else ""
        lines.append(f"- {item['headline']}{source}")
    return "\n".join(lines)


def _format_recent_news_snapshot(label: str, query: str, tz_name: str) -> str:
    """Summarize today/yesterday headlines for one query."""
    buckets = _collect_recent_news_buckets(query, tz_name)
    today_lines = []
    yesterday_lines = []

    for item in buckets["today"]:
        source = f" ({item['source']})" if item.get("source") else ""
        today_lines.append(f"- {item['headline']}{source}")

    for item in buckets["yesterday"]:
        source = f" ({item['source']})" if item.get("source") else ""
        yesterday_lines.append(f"- {item['headline']}{source}")

    lines = [f"{label} today:"]
    lines.extend(today_lines or ["- No major headlines captured."])
    lines.append(f"{label} yesterday:")
    lines.extend(yesterday_lines or ["- No major headlines captured."])
    return "\n".join(lines)


def _build_local_news_context_sync(location: str, country: str, tz_name: str) -> str:
    """Build the combined local/national/world briefing for the prompt cache."""
    local_summary = _format_today_news_snapshot(
        f"{location} headlines",
        _collect_recent_news_buckets(location, tz_name)["today"],
    )
    national_summary = _format_today_news_snapshot(
        f"{country} headlines",
        _collect_recent_news_buckets(country, tz_name)["today"],
    )
    world_summary = _format_today_news_snapshot(
        "World focus headlines",
        _collect_focus_news_today(
            tz_name,
            ["Iran Amerika", "Iran AS", "Amerika Iran", "dunia", "world news"],
        ),
    )
    return (
        "Most recent headline snapshot from Google News RSS.\n"
        f"{local_summary}\n"
        f"{national_summary}\n"
        f"{world_summary}"
    )


def _headlines_to_notes(label: str, items: list[dict]) -> str:
    if not items:
        return f"{label}: none captured."
    return f"{label}: " + " | ".join(item["headline"] for item in items[:2])


def _build_first_turn_briefing_sync(location: str, country: str, tz_name: str) -> str:
    weather = _fetch_weather_snapshot_sync(location, country)
    local_today = _collect_recent_news_buckets(location, tz_name)["today"]
    national_today = _collect_recent_news_buckets(country, tz_name)["today"]
    world_today = _collect_focus_news_today(
        tz_name,
        ["Iran Amerika", "Iran AS", "Amerika Iran", "dunia", "world news"],
    )
    current_prefix = f"Current weather in {weather['location_label']}: "
    tomorrow_prefix = f"Tomorrow in {weather['location_label']}: "
    current_weather = weather["current"]
    tomorrow_weather = weather["tomorrow"]
    if current_weather.startswith(current_prefix):
        current_weather = current_weather[len(current_prefix):]
    if tomorrow_weather.startswith(tomorrow_prefix):
        tomorrow_weather = tomorrow_weather[len(tomorrow_prefix):]

    return (
        f"Owner: {get_user_name()}.\n"
        f"Current weather in {location}: {current_weather}\n"
        f"Tomorrow forecast for {location}: {tomorrow_weather}\n"
        f"{_headlines_to_notes(f'{location} headlines today', local_today)}\n"
        f"{_headlines_to_notes(f'{country} headlines today', national_today)}\n"
        f"{_headlines_to_notes('World headlines today with Iran-America focus', world_today)}"
    )


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class ClaudeTask:
    id: str
    prompt: str
    status: str = "pending"  # pending, running, completed, failed, cancelled
    working_dir: str = "."
    pid: Optional[int] = None
    result: str = ""
    error: str = ""
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["started_at"] = self.started_at.isoformat() if self.started_at else None
        d["completed_at"] = self.completed_at.isoformat() if self.completed_at else None
        d["elapsed_seconds"] = self.elapsed_seconds
        return d

    @property
    def elapsed_seconds(self) -> float:
        if not self.started_at:
            return 0
        end = self.completed_at or datetime.now()
        return (end - self.started_at).total_seconds()


class TaskRequest(BaseModel):
    prompt: str
    working_dir: str = "."


# ---------------------------------------------------------------------------
# Claude Task Manager
# ---------------------------------------------------------------------------

class ClaudeTaskManager:
    """Manages background claude -p subprocesses."""

    def __init__(self, max_concurrent: int = 3):
        self._tasks: dict[str, ClaudeTask] = {}
        self._max_concurrent = max_concurrent
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._websockets: list[WebSocket] = []  # for push notifications

    def register_websocket(self, ws: WebSocket):
        if ws not in self._websockets:
            self._websockets.append(ws)

    def unregister_websocket(self, ws: WebSocket):
        if ws in self._websockets:
            self._websockets.remove(ws)

    async def _notify(self, message: dict):
        """Push a message to all connected WebSocket clients."""
        dead = []
        for ws in self._websockets:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._websockets.remove(ws)

    async def spawn(self, prompt: str, working_dir: str = ".") -> str:
        """Spawn a claude -p subprocess. Returns task_id. Non-blocking."""
        active = await self.get_active_count()
        if active >= self._max_concurrent:
            raise RuntimeError(
                f"Max concurrent tasks ({self._max_concurrent}) reached. "
                f"Wait for a task to complete or cancel one."
            )

        task_id = str(uuid.uuid4())[:8]
        task = ClaudeTask(
            id=task_id,
            prompt=prompt,
            working_dir=working_dir,
            status="pending",
        )
        self._tasks[task_id] = task

        # Fire and forget — the background coroutine updates the task
        asyncio.create_task(self._run_task(task))
        log.info(f"Spawned task {task_id}: {prompt[:80]}...")

        await self._notify({
            "type": "task_spawned",
            "task_id": task_id,
            "prompt": prompt,
        })

        return task_id

    def _generate_project_name(self, prompt: str) -> str:
        """Generate a kebab-case project folder name from the prompt."""
        import re
        # Extract key words
        words = re.sub(r'[^a-zA-Z0-9\s]', '', prompt.lower()).split()
        # Take first 3-4 meaningful words
        skip = {"a", "the", "an", "me", "build", "create", "make", "for", "with", "and", "to", "of"}
        meaningful = [w for w in words if w not in skip][:4]
        name = "-".join(meaningful) if meaningful else "jarvis-project"
        return name

    async def _run_task(self, task: ClaudeTask):
        """Open a Terminal window and run claude code visibly."""
        task.status = "running"
        task.started_at = datetime.now()

        # Create project directory if it doesn't exist
        work_dir = task.working_dir
        if work_dir == "." or not work_dir:
            # Create a new project folder on Desktop
            project_name = self._generate_project_name(task.prompt)
            work_dir = str(Path.home() / "Desktop" / project_name)
            os.makedirs(work_dir, exist_ok=True)
            task.working_dir = work_dir

        # Write the prompt to a temp file so we can pipe it to claude
        prompt_file = Path(work_dir) / ".jarvis_prompt.md"
        prompt_file.write_text(task.prompt)

        # Open Terminal.app with claude running in the project directory
        applescript = f'''
        tell application "Terminal"
            activate
            set newTab to do script "cd {work_dir} && cat .jarvis_prompt.md | claude -p --dangerously-skip-permissions | tee .jarvis_output.txt; echo '\\n--- JARVIS TASK COMPLETE ---'"
        end tell
        '''

        process = await asyncio.create_subprocess_exec(
            "osascript", "-e", applescript,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()
        task.pid = process.pid

        # Monitor the output file for completion
        output_file = Path(work_dir) / ".jarvis_output.txt"
        start = time.time()
        timeout = 600  # 10 minutes

        while time.time() - start < timeout:
            await asyncio.sleep(5)
            if output_file.exists():
                content = output_file.read_text()
                if "--- JARVIS TASK COMPLETE ---" in content or len(content) > 100:
                    task.result = content.replace("--- JARVIS TASK COMPLETE ---", "").strip()
                    task.status = "completed"
                    break
        else:
            task.status = "timed_out"
            task.error = f"Task timed out after {timeout}s"

        task.completed_at = datetime.now()

        # Notify via WebSocket
        await self._notify({
            "type": "task_complete",
            "task_id": task.id,
            "status": task.status,
            "summary": task.result[:200] if task.result else task.error,
        })

        # Clean up prompt file
        try:
            prompt_file.unlink()
        except:
            pass

        # Auto-QA on completed tasks
        if task.status == "completed":
            asyncio.create_task(self._run_qa(task))

    async def _run_qa(self, task: ClaudeTask, attempt: int = 1):
        """Run QA verification on a completed task, auto-retry on failure."""
        try:
            qa_result = await qa_agent.verify(task.prompt, task.result, task.working_dir)
            duration = task.elapsed_seconds

            if qa_result.passed:
                log.info(f"Task {task.id} passed QA: {qa_result.summary}")
                success_tracker.log_task("dev", task.prompt, True, attempt - 1, duration)
                await self._notify({
                    "type": "qa_result",
                    "task_id": task.id,
                    "passed": True,
                    "summary": qa_result.summary,
                })

                # Proactive suggestion after successful task
                suggestion = suggest_followup(
                    task_type="dev",
                    task_description=task.prompt,
                    working_dir=task.working_dir,
                    qa_result=qa_result,
                )
                if suggestion:
                    success_tracker.log_suggestion(task.id, suggestion.text)
                    await self._notify({
                        "type": "suggestion",
                        "task_id": task.id,
                        "text": suggestion.text,
                        "action_type": suggestion.action_type,
                        "action_details": suggestion.action_details,
                    })
            else:
                log.warning(f"Task {task.id} failed QA: {qa_result.issues}")
                if attempt < 3:
                    log.info(f"Auto-retrying task {task.id} (attempt {attempt + 1}/3)")
                    retry_result = await qa_agent.auto_retry(
                        task.prompt, qa_result.issues, task.working_dir, attempt,
                    )
                    if retry_result["status"] == "completed":
                        task.result = retry_result["result"]
                        # Re-verify
                        await self._run_qa(task, attempt + 1)
                    else:
                        success_tracker.log_task("dev", task.prompt, False, attempt, duration)
                        await self._notify({
                            "type": "qa_result",
                            "task_id": task.id,
                            "passed": False,
                            "summary": f"Failed after {attempt + 1} attempts: {qa_result.issues}",
                        })
                else:
                    success_tracker.log_task("dev", task.prompt, False, attempt, duration)
                    await self._notify({
                        "type": "qa_result",
                        "task_id": task.id,
                        "passed": False,
                        "summary": f"Failed QA after {attempt} attempts: {qa_result.issues}",
                    })
        except Exception as e:
            log.error(f"QA error for task {task.id}: {e}")

    async def get_status(self, task_id: str) -> Optional[ClaudeTask]:
        return self._tasks.get(task_id)

    async def list_tasks(self) -> list[ClaudeTask]:
        return list(self._tasks.values())

    async def get_active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status in ("pending", "running"))

    async def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.status not in ("pending", "running"):
            return False

        process = self._processes.get(task_id)
        if process:
            try:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    process.kill()
            except ProcessLookupError:
                pass

        task.status = "cancelled"
        task.completed_at = datetime.now()
        self._processes.pop(task_id, None)
        log.info(f"Cancelled task {task_id}")
        return True

    def get_active_tasks_summary(self) -> str:
        """Format active tasks for injection into the system prompt."""
        active = [t for t in self._tasks.values() if t.status in ("pending", "running")]
        completed_recent = [
            t for t in self._tasks.values()
            if t.status == "completed"
            and t.completed_at
            and (datetime.now() - t.completed_at).total_seconds() < 300
        ]

        if not active and not completed_recent:
            return "No active or recent tasks."

        lines = []
        for t in active:
            elapsed = f"{t.elapsed_seconds:.0f}s" if t.started_at else "queued"
            lines.append(f"- [{t.id}] RUNNING ({elapsed}): {t.prompt[:100]}")
        for t in completed_recent:
            lines.append(f"- [{t.id}] COMPLETED: {t.prompt[:60]} -> {t.result[:80]}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Project Scanner
# ---------------------------------------------------------------------------

async def scan_projects() -> list[dict]:
    """Quick scan of ~/Desktop for git repos (depth 1)."""
    projects = []
    desktop = DESKTOP_PATH

    if not desktop.exists():
        return projects

    try:
        for entry in sorted(desktop.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            git_dir = entry / ".git"
            if git_dir.exists():
                branch = "unknown"
                head_file = git_dir / "HEAD"
                try:
                    head_content = head_file.read_text().strip()
                    if head_content.startswith("ref: refs/heads/"):
                        branch = head_content.replace("ref: refs/heads/", "")
                except Exception:
                    pass

                projects.append({
                    "name": entry.name,
                    "path": str(entry),
                    "branch": branch,
                })
    except PermissionError:
        pass

    return projects


def format_projects_for_prompt(projects: list[dict]) -> str:
    if not projects:
        return "No projects found on Desktop."
    lines = []
    for p in projects:
        lines.append(f"- {p['name']} ({p['branch']}) @ {p['path']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Speech-to-Text Corrections
# ---------------------------------------------------------------------------

STT_CORRECTIONS = {
    r"\bcloud code\b": "Claude Code",
    r"\bclock code\b": "Claude Code",
    r"\bquad code\b": "Claude Code",
    r"\bclawed code\b": "Claude Code",
    r"\bclod code\b": "Claude Code",
    r"\bcode x\b": "Codex",
    r"\bcodec\b": "Codex",
    r"\bcodexx\b": "Codex",
    r"\bcloud\b": "Claude",
    r"\bquad\b": "Claude",
    r"\btravis\b": "JARVIS",
    r"\bjarves\b": "JARVIS",
    r"\bwhiz\b": "WiZ",
    r"\bwizz\b": "WiZ",
    r"\bweez\b": "WiZ",
}


def apply_speech_corrections(text: str) -> str:
    """Fix common speech-to-text errors before processing."""
    import re as _stt_re
    result = text
    for pattern, replacement in STT_CORRECTIONS.items():
        result = _stt_re.sub(pattern, replacement, result, flags=_stt_re.IGNORECASE)
    return result


# ---------------------------------------------------------------------------
# LLM Intent Classifier (replaces keyword-based action detection)
# ---------------------------------------------------------------------------

async def classify_intent(text: str, client: Any) -> dict:
    """Classify every user message using Haiku LLM.

    Returns: {"action": "open_terminal|browse|build|chat", "target": "description"}
    """
    try:
        response = await client.messages.create(
            model=JARVIS_FAST_MODEL,
            max_tokens=100,
            system=(
                "Classify this voice command. The user is talking to JARVIS, an AI assistant that can:\n"
                "- Open Terminal and run Claude Code (coding AI tool)\n"
                "- Open Chrome browser for web searches and URLs\n"
                "- Build software projects via Claude Code in Terminal\n"
                "- Research topics by opening Chrome search\n\n"
                "Note: speech-to-text may produce errors like \"Cloud\" for \"Claude\", "
                "\"Travis\" for \"JARVIS\", \"clock code\" for \"Claude Code\".\n\n"
                "Return ONLY valid JSON: {\"action\": \"open_terminal|browse|build|chat\", "
                "\"target\": \"description of what to do\"}\n"
                "open_terminal = user wants to open terminal or launch Claude Code\n"
                "browse = user wants to search the web, look something up, visit a URL\n"
                "build = user wants to create/build a software project\n"
                "chat = just conversation, questions, or anything else\n"
                "If unclear, default to \"chat\"."
            ),
            messages=[{"role": "user", "content": text}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        return {
            "action": data.get("action", "chat"),
            "target": data.get("target", text),
        }
    except Exception as e:
        log.warning(f"Intent classification failed: {e}")
        return {"action": "chat", "target": text}


# ---------------------------------------------------------------------------
# Markdown Stripping for TTS
# ---------------------------------------------------------------------------

def strip_markdown_for_tts(text: str) -> str:
    """Strip ALL markdown from text before sending to TTS."""
    import re as _md_re
    result = text
    # Strip <think>...</think> blocks leaked by reasoning/thinking models (Gemma 4, DeepSeek-R1)
    result = _md_re.sub(r"<think>[\s\S]*?</think>", "", result, flags=_md_re.IGNORECASE)
    result = result.strip()
    # Remove code blocks (``` ... ```)
    result = _md_re.sub(r"```[\s\S]*?```", "", result)
    # Remove inline code
    result = result.replace("`", "")
    # Remove bold/italic markers
    result = result.replace("**", "").replace("*", "")
    # Remove headers
    result = _md_re.sub(r"^#{1,6}\s*", "", result, flags=_md_re.MULTILINE)
    # Convert [text](url) to just text
    result = _md_re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", result)
    # Remove bullet points
    result = _md_re.sub(r"^\s*[-*+]\s+", "", result, flags=_md_re.MULTILINE)
    # Remove numbered lists
    result = _md_re.sub(r"^\s*\d+\.\s+", "", result, flags=_md_re.MULTILINE)
    # Double newlines to period
    result = _md_re.sub(r"\n{2,}", ". ", result)
    # Single newlines to space
    result = result.replace("\n", " ")
    # Clean up multiple spaces
    result = _md_re.sub(r"\s{2,}", " ", result)

    # Strip banned phrases
    banned = ["my apologies", "i apologize", "absolutely", "great question",
              "i'd be happy to", "of course", "how can i help",
              "is there anything else", "i should clarify", "let me know if",
              "feel free to"]
    result_lower = result.lower()
    for phrase in banned:
        idx = result_lower.find(phrase)
        while idx != -1:
            # Remove the phrase and any trailing comma/dash
            end = idx + len(phrase)
            if end < len(result) and result[end] in " ,—-":
                end += 1
            result = result[:idx] + result[end:]
            result_lower = result.lower()
            idx = result_lower.find(phrase)

    return result.strip().strip(",").strip("—").strip("-").strip()

def should_show_uncertainty(text: str) -> bool:
    """Return True when JARVIS is asking for clarification or sounds unsure."""
    normalized = " ".join(text.lower().split())
    uncertain_phrases = [
        "could you clarify",
        "can you clarify",
        "what do you mean",
        "which one do you mean",
        "which option",
        "which one",
        "can you be more specific",
        "be a bit more specific",
        "i'm not sure which",
        "i am not sure which",
        "i didn't quite catch",
        "i did not quite catch",
        "say that again",
        "repeat that",
        "help me narrow that down",
        "how shall i adjust the plan",
        "what else, sir?",
        "shall i proceed",
        "ready to build. shall i proceed",
        "tell me what to ask codex",
    ]
    if any(phrase in normalized for phrase in uncertain_phrases):
        return True
    return normalized.endswith("?") and any(
        cue in normalized for cue in ["clarify", "specific", "which", "what do you mean", "repeat"]
    )


def _looks_complex_for_ultra_eco_chat(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    if not normalized:
        return False

    complexity_cues = (
        "explain",
        "jelaskan",
        "compare",
        "bandingkan",
        "analyze",
        "analisa",
        "analisis",
        "strategy",
        "strategi",
        "architecture",
        "arsitektur",
        "reason",
        "alasan",
        "why ",
        "kenapa",
        "debug",
        "investigate",
        "research",
        "riset",
        "deep",
        "mendalam",
        "step by step",
        "langkah demi langkah",
        "tradeoff",
        "trade-off",
        "pros and cons",
        "kelebihan dan kekurangan",
        "plan",
        "rencana",
        "design",
        "desain",
    )
    multi_step_cues = (
        " and ",
        " lalu ",
        " kemudian ",
        " setelah itu ",
        " terus ",
        " sekaligus ",
        " beserta ",
        " while ",
    )

    word_count = len(normalized.split())
    return (
        word_count >= 28
        or "\n" in text
        or any(cue in normalized for cue in complexity_cues)
        or any(cue in normalized for cue in multi_step_cues)
    )


def _should_retry_ultra_eco_with_fallback(response_text: str) -> bool:
    normalized = " ".join((response_text or "").lower().split())
    if not normalized:
        return True
    if should_show_uncertainty(response_text):
        return True
    weak_cues = (
        "i'm not sure",
        "i am not sure",
        "not sure which",
        "could you clarify",
        "can you clarify",
        "which one",
        "what do you mean",
        "be more specific",
        "i didn't quite catch",
        "i did not quite catch",
        "aku belum yakin",
        "bisa diperjelas",
        "yang mana",
        "maksudmu yang mana",
        "tolong lebih spesifik",
    )
    return any(cue in normalized for cue in weak_cues)


def _compact_local_context_value(value: str, limit: int = 260) -> str:
    compact = " ".join((value or "").split())
    if len(compact) <= limit:
        return compact
    clipped = compact[: limit - 3].rstrip(" ,.;:")
    return f"{clipped}..."


async def _call_jarvis_model(
    client: Any,
    *,
    model: str,
    system: str,
    messages: list[dict],
    max_tokens: int = 250,
):
    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    )
    track_usage(response)
    return response


_WIZ_CONTEXT_WINDOW_SECONDS = 45.0
_AIR_FAN_CONTEXT_WINDOW_SECONDS = 45.0


def _extract_contextual_wiz_followup(text: str) -> dict | None:
    """Interpret short follow-up phrases as light commands after recent WiZ activity."""
    raw = (text or "").strip()
    if not raw:
        return None
    if len(raw.split()) > 6:
        return None

    candidates = (raw, f"lampu {raw}", f"lights {raw}")
    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            cmd = extract_wiz_request(candidate, _force=True)
        except Exception:
            cmd = None
        if cmd and cmd.get("kind") in {"power", "brightness", "color", "temperature", "scene", "discover"}:
            return cmd
    return None


def _extract_contextual_air_fan_followup(text: str) -> dict | None:
    """Interpret short follow-up phrases as fan commands after recent fan activity."""
    raw = (text or "").strip()
    if not raw:
        return None
    if len(raw.split()) > 6:
        return None

    candidates = (
        raw,
        f"fan {raw}",
        f"kipas {raw}",
        f"fan speed {raw}",
        f"set fan {raw}",
    )
    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            cmd = extract_air_fan_request(candidate, _force=True)
        except Exception:
            cmd = None
        if cmd and cmd.get("kind") in {"status", "speed", "profile"}:
            return cmd
    return None


# ---------------------------------------------------------------------------
# Action Tag Extraction (parse [ACTION:X] from LLM responses)
# ---------------------------------------------------------------------------

import re as _action_re


def extract_action(response: str) -> tuple[str, dict | None]:
    """Extract [ACTION:X] tag from LLM response.

    Returns (clean_text_for_tts, action_dict_or_none).
    """
    match = _action_re.search(
        r'\[ACTION:(BUILD|BROWSE|OPEN_APP|PLAY_SPOTIFY|RESEARCH|OPEN_TERMINAL|PROMPT_PROJECT|ADD_TASK|ADD_NOTE|COMPLETE_TASK|REMEMBER|CREATE_NOTE|READ_NOTE|SCREEN|WIZ|FAN)\]\s*(.*?)$',
        response, _action_re.DOTALL,
    )
    if match:
        action_type = match.group(1).lower()
        action_target = match.group(2).strip()
        clean_text = response[:match.start()].strip()
        return clean_text, {"action": action_type, "target": action_target}
    return response, None


async def _execute_build(target: str):
    """Execute a build action from an LLM-embedded [ACTION:BUILD] tag."""
    try:
        await handle_build(target)
    except Exception as e:
        log.error(f"Build execution failed: {e}")


async def _execute_browse(target: str):
    """Execute a browse action from an LLM-embedded [ACTION:BROWSE] tag."""
    try:
        if target.startswith("http") or "." in target.split()[0]:
            await open_browser(target)
        else:
            from urllib.parse import quote
            await open_browser(f"https://www.google.com/search?q={quote(target)}")
    except Exception as e:
        log.error(f"Browse execution failed: {e}")


async def _execute_open_app(target: str):
    """Execute a desktop app open request from an LLM-embedded [ACTION:OPEN_APP] tag."""
    try:
        await open_app(target)
    except Exception as e:
        log.error(f"Open app failed: {e}")


async def _execute_play_spotify(target: str):
    """Execute a Spotify playback request from an LLM-embedded [ACTION:PLAY_SPOTIFY] tag."""
    try:
        await play_spotify(target)
    except Exception as e:
        log.error(f"Spotify playback failed: {e}")


async def _execute_research(target: str, ws=None):
    """Execute research via claude -p in background. Opens report and speaks when done."""
    try:
        name = _generate_project_name(target)
        path = str(Path.home() / "Desktop" / name)
        os.makedirs(path, exist_ok=True)

        prompt = (
            f"{target}\n\n"
            f"Research this thoroughly. Find REAL data — not made-up examples.\n"
            f"Create a well-designed HTML file called `report.html` in the current directory.\n"
            f"Dark theme, clean typography, organized sections, real links and sources.\n"
            f"The working directory is: {path}"
        )

        log.info(f"Research started via claude -p in {path}")

        process = await asyncio.create_subprocess_exec(
            "claude", "-p", "--output-format", "text", "--dangerously-skip-permissions",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=path,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=prompt.encode()),
            timeout=300,
        )

        result = stdout.decode().strip()
        log.info(f"Research complete ({len(result)} chars)")

        recently_built.append({"name": name, "path": path, "time": time.time()})

        # Find and open any HTML report
        report = Path(path) / "report.html"
        if not report.exists():
            # Check for any HTML file
            html_files = list(Path(path).glob("*.html"))
            if html_files:
                report = html_files[0]

        if report.exists():
            await open_browser(f"file://{report}")
            log.info(f"Opened {report.name} in browser")

        # Notify via voice if WebSocket still connected
        if ws:
            try:
                notify_text = f"Research is complete, sir. Report is open in your browser."
                audio = await synthesize_speech(notify_text)
                if audio:
                    await ws.send_json({"type": "status", "state": "speaking"})
                    await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": notify_text})
                    await ws.send_json({"type": "status", "state": "idle"})
                    log.info(f"JARVIS: {notify_text}")
            except Exception:
                pass  # WebSocket might be gone

    except asyncio.TimeoutError:
        log.error("Research timed out after 5 minutes")
        if ws:
            try:
                audio = await synthesize_speech("Research timed out, sir. It was taking too long.")
                if audio:
                    await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": "Research timed out, sir."})
            except Exception:
                pass
    except Exception as e:
        log.error(f"Research execution failed: {e}")


async def _focus_terminal_window(project_name: str):
    """Bring a Terminal window matching the project name to front."""
    escaped = project_name.replace('"', '\\"')
    script = f'''
tell application "Terminal"
    repeat with w in windows
        if name of w contains "{escaped}" then
            set index of w to 1
            activate
            exit repeat
        end if
    end repeat
end tell
'''
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5)
    except Exception:
        pass


async def _execute_open_terminal():
    """Execute an open-terminal action from an LLM-embedded [ACTION:OPEN_TERMINAL] tag."""
    try:
        await handle_open_terminal()
    except Exception as e:
        log.error(f"Open terminal failed: {e}")


def _find_project_dir(project_name: str) -> str | None:
    """Find a project directory by name from cached projects or Desktop."""
    for p in cached_projects:
        if project_name.lower() in p.get("name", "").lower():
            return p.get("path")
    desktop = Path.home() / "Desktop"
    for d in desktop.iterdir():
        if d.is_dir() and project_name.lower() in d.name.lower():
            return str(d)
    return None


async def _execute_prompt_project(project_name: str, prompt: str, work_session: WorkSession, ws, dispatch_id: int = None, history: list[dict] = None, voice_state: dict = None):
    """Dispatch a prompt to Claude Code in a project directory.

    Runs entirely in the background. JARVIS returns to conversation mode
    immediately. When Claude Code finishes, JARVIS interrupts to report.
    """
    try:
        project_dir = _find_project_dir(project_name)

        # Register dispatch if not already registered
        if dispatch_id is None:
            dispatch_id = dispatch_registry.register(project_name, project_dir or "", prompt)

        if not project_dir:
            msg = f"Couldn't find the {project_name} project directory, sir."
            audio = await synthesize_speech(msg)
            if audio and ws:
                try:
                    await ws.send_json({"type": "status", "state": "speaking"})
                    await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
                except Exception:
                    pass
            return

        # Use a SEPARATE session so we don't trap the main conversation
        dispatch = WorkSession()
        await dispatch.start(project_dir, project_name)

        # Bring matching Terminal window to front so user can watch
        asyncio.create_task(_focus_terminal_window(project_name))

        log.info(f"Dispatching to {project_name} in {project_dir}: {prompt[:80]}")
        dispatch_registry.update_status(dispatch_id, "building")

        # Run claude -p in background
        full_response = await dispatch.send(prompt)
        await dispatch.stop()

        # Auto-open any localhost URLs from response
        import re as _re
        # Check for the explicit RUNNING_AT marker first
        running_match = _re.search(r'RUNNING_AT=(https?://localhost:\d+)', full_response or "")
        if not running_match:
            running_match = _re.search(r'https?://localhost:\d+', full_response or "")
        if running_match:
            url = running_match.group(1) if running_match.lastindex else running_match.group(0)
            asyncio.create_task(_execute_browse(url))
            log.info(f"Auto-opening {url}")
            # Store URL in dispatch
            if dispatch_id:
                dispatch_registry.update_status(dispatch_id, "completed",
                    response=full_response[:2000], summary=f"Running at {url}")

        if not full_response or full_response.startswith("Hit a problem") or full_response.startswith("That's taking"):
            dispatch_registry.update_status(dispatch_id, "failed" if full_response else "timeout", response=full_response or "")
            msg = f"Sir, I ran into an issue with {project_name}. {full_response[:150] if full_response else 'No response received.'}"
        else:
            # Summarize via Haiku — don't read word for word
            if anthropic_client:
                try:
                    summary = await anthropic_client.messages.create(
                        model=JARVIS_FAST_MODEL,
                        max_tokens=150,
                        system=(
                            "You are JARVIS reporting back on what you found or built in a project. "
                            "Speak in first person — 'I found', 'I built', 'I reviewed'. "
                            "Start with 'Sir, ' to get the user's attention. "
                            "Be specific but concise — highlight the key findings or actions taken. "
                            "If there are multiple items, give the count and top 2-3 briefly. "
                            "End by asking how the user wants to proceed. "
                            "NEVER read out URLs or localhost addresses. NEVER say 'Claude Code'. "
                            "2-3 sentences max. No markdown. Natural spoken voice."
                        ),
                        messages=[{"role": "user", "content": f"Project: {project_name}\nClaude Code reported:\n{full_response[:3000]}"}],
                    )
                    msg = summary.content[0].text
                except Exception:
                    msg = f"Sir, {project_name} finished. Here's the gist: {full_response[:200]}"
            else:
                msg = f"Sir, {project_name} is done. {full_response[:200]}"

        # Speak the result — skip if user has spoken recently to avoid audio collision
        log.info(f"Dispatch summary for {project_name}: {msg[:100]}")
        if voice_state and time.time() - voice_state["last_user_time"] < 3:
            log.info(f"Skipping dispatch audio for {project_name} — user spoke recently")
            # Result is still stored in history below so JARVIS can reference it
        else:
            audio = await synthesize_speech(strip_markdown_for_tts(msg))
            if ws:
                try:
                    await ws.send_json({"type": "status", "state": "speaking"})
                    if audio:
                        await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
                        log.info(f"Dispatch audio sent for {project_name}")
                    else:
                        await ws.send_json({"type": "text", "text": msg})
                        log.info(f"Dispatch text fallback sent for {project_name}")
                except Exception as e:
                    log.error(f"Dispatch audio send failed: {e}")

        # Store dispatch result in conversation history so JARVIS remembers it
        if history is not None:
            history.append({"role": "assistant", "content": f"[Dispatch result for {project_name}]: {msg}"})

        dispatch_registry.update_status(dispatch_id, "completed", response=full_response[:2000], summary=msg[:200])
        log.info(f"Project {project_name} dispatch complete ({len(full_response)} chars)")

    except Exception as e:
        log.error(f"Prompt project failed: {e}", exc_info=True)
        try:
            msg = f"Had trouble connecting to {project_name}, sir."
            audio = await synthesize_speech(msg)
            if audio and ws:
                await ws.send_json({"type": "status", "state": "speaking"})
                await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
        except Exception:
            pass


async def self_work_and_notify(session: WorkSession, prompt: str, ws):
    """Run claude -p in background and notify via voice when done."""
    try:
        full_response = await session.send(prompt)
        log.info(f"Background work complete ({len(full_response)} chars)")

        # Summarize and speak
        if anthropic_client and full_response:
            try:
                summary = await anthropic_client.messages.create(
                    model=JARVIS_FAST_MODEL,
                    max_tokens=100,
                    system="You are JARVIS. Summarize what you just completed in 1 sentence. First person — 'I built', 'I set up'. No markdown. Never say 'Claude Code'.",
                    messages=[{"role": "user", "content": f"Claude Code completed:\n{full_response[:2000]}"}],
                )
                msg = summary.content[0].text
            except Exception:
                msg = "Work is complete, sir."

            try:
                audio = await synthesize_speech(msg)
                if audio:
                    await ws.send_json({"type": "status", "state": "speaking"})
                    await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
                    await ws.send_json({"type": "status", "state": "idle"})
                    log.info(f"JARVIS: {msg}")
            except Exception:
                pass
    except Exception as e:
        log.error(f"Background work failed: {e}")


# Smart greeting — track last greeting to avoid re-greeting on reconnect
_last_greeting_time: float = 0
_fish_model_meta_cache: dict[str, dict] = {}
_FISH_TTS_DEBUG_FILE = Path(__file__).parent / "data" / "fish_tts_debug.jsonl"


# ---------------------------------------------------------------------------
# TTS (Fish Audio)
# ---------------------------------------------------------------------------

def _get_active_fish_voice_id() -> str:
    if _is_indonesian_mode():
        indonesian_voice_id = _get_fish_indonesian_voice_id()
        if indonesian_voice_id:
            return indonesian_voice_id
    return _get_fish_default_voice_id()


def _estimate_english_content_ratio(text: str) -> float:
    tokens = re.findall(r"[A-Za-z']+", text.lower())
    if not tokens:
        return 0.0

    english_signals = {
        "the", "and", "your", "ready", "local", "briefing", "sir", "browser",
        "good", "morning", "afternoon", "evening", "work", "complete",
        "checking", "building", "playing", "opening", "report", "today",
    }
    matches = sum(1 for token in tokens if token in english_signals)
    return matches / max(len(tokens), 1)


def _estimate_indonesian_content_ratio(text: str) -> float:
    tokens = re.findall(r"[A-Za-z']+", text.lower())
    if not tokens:
        return 0.0

    indonesian_signals = {
        "yang", "dan", "untuk", "dengan", "tidak", "sudah", "belum", "kamu",
        "saya", "kami", "kita", "akan", "sedang", "bisa", "tolong", "ringkasan",
        "cuaca", "berita", "hari", "ini", "besok", "selamat", "pagi", "siang",
        "malam", "jelas", "baik", "siap",
    }
    matches = sum(1 for token in tokens if token in indonesian_signals)
    return matches / max(len(tokens), 1)


def _number_to_indonesian_words(value: int) -> str:
    base = [
        "nol", "satu", "dua", "tiga", "empat", "lima",
        "enam", "tujuh", "delapan", "sembilan", "sepuluh", "sebelas",
    ]
    if value < 12:
        return base[value]
    if value < 20:
        return f"{base[value - 10]} belas"
    if value < 100:
        tens, rem = divmod(value, 10)
        return f"{base[tens]} puluh" + (f" {_number_to_indonesian_words(rem)}" if rem else "")
    if value < 200:
        return "seratus" + (f" {_number_to_indonesian_words(value - 100)}" if value > 100 else "")
    if value < 1000:
        hundreds, rem = divmod(value, 100)
        return f"{base[hundreds]} ratus" + (f" {_number_to_indonesian_words(rem)}" if rem else "")
    if value < 2000:
        return "seribu" + (f" {_number_to_indonesian_words(value - 1000)}" if value > 1000 else "")
    if value < 1_000_000:
        thousands, rem = divmod(value, 1000)
        return f"{_number_to_indonesian_words(thousands)} ribu" + (f" {_number_to_indonesian_words(rem)}" if rem else "")
    if value < 1_000_000_000:
        millions, rem = divmod(value, 1_000_000)
        return f"{_number_to_indonesian_words(millions)} juta" + (f" {_number_to_indonesian_words(rem)}" if rem else "")
    billions, rem = divmod(value, 1_000_000_000)
    return f"{_number_to_indonesian_words(billions)} miliar" + (f" {_number_to_indonesian_words(rem)}" if rem else "")


def _normalize_indonesian_tts_text(text: str) -> str:
    updated = text.strip()
    if not updated:
        return updated

    phrase_replacements = [
        ("your local briefing is not ready just yet", "ringkasan lokalmu belum siap sepenuhnya"),
        ("your local briefing is ready whenever you'd like it", "ringkasan lokalmu sudah siap kapan pun kamu ingin mendengarnya"),
        ("your local briefing is ready", "ringkasan lokalmu sudah siap"),
        ("work is complete", "pekerjaan sudah selesai"),
        ("good morning", "selamat pagi"),
        ("good afternoon", "selamat siang"),
        ("good evening", "selamat malam"),
        ("loud and clear", "terdengar jelas"),
        ("briefing", "ringkasan"),
        ("meeting", "rapat"),
        ("sir", get_user_name()),
    ]
    for source, target in phrase_replacements:
        updated = re.sub(rf"\b{re.escape(source)}\b", target, updated, flags=re.IGNORECASE)

    updated = updated.replace("&", " dan ")

    def replace_time(match):
        hours = int(match.group(1))
        minutes = int(match.group(2))
        hour_words = _number_to_indonesian_words(hours)
        if minutes == 0:
            return f"pukul {hour_words}"
        return f"pukul {hour_words} lewat {_number_to_indonesian_words(minutes)}"

    updated = re.sub(r"\b(\d{1,2}):(\d{2})\b", replace_time, updated)
    updated = re.sub(r"\bpukul\s+pukul\b", "pukul", updated, flags=re.IGNORECASE)

    def replace_number(match):
        try:
            value = int(match.group(0))
        except ValueError:
            return match.group(0)
        return _number_to_indonesian_words(value)

    updated = re.sub(r"\b\d+\b", replace_number, updated)
    updated = re.sub(r"\s+", " ", updated).strip()
    return updated


async def _rewrite_text_for_language(text: str, target_language: str, client: Any | None) -> str:
    cleaned = text.strip()
    if not cleaned or not client:
        return cleaned

    if target_language == "en":
        instruction = (
            "Rewrite this assistant reply into natural spoken English for JARVIS. "
            "Keep all facts, names, intent, and numbers accurate. "
            "Do not add any new information. "
            "Return only the rewritten reply."
        )
    else:
        instruction = (
            "Ubah balasan asisten ini menjadi bahasa Indonesia lisan yang natural untuk JARVIS. "
            "Pertahankan semua fakta, nama, maksud, dan angka. "
            "Jangan menambah informasi baru. "
            "Kembalikan hanya hasil akhirnya."
        )

    try:
        response = await client.messages.create(
            model=JARVIS_FAST_MODEL,
            max_tokens=min(300, max(80, len(cleaned) * 2)),
            messages=[{"role": "user", "content": f"{instruction}\n\nReply:\n{cleaned}"}],
        )
        rewritten = response.content[0].text.strip()
        return rewritten or cleaned
    except Exception as exc:
        log.debug(f"Language rewrite skipped: {exc}")
        return cleaned


async def _prepare_spoken_text_for_tts(text: str, client: Any | None = None) -> tuple[str, str]:
    cleaned = text.strip()
    if not cleaned:
        return "", "none"

    if _is_indonesian_mode():
        return _normalize_indonesian_tts_text(cleaned), "indonesian_normalize"

    english_ratio = _estimate_english_content_ratio(cleaned)
    indonesian_ratio = _estimate_indonesian_content_ratio(cleaned)
    if english_ratio >= 0.18 and indonesian_ratio < 0.2:
        return cleaned, "none"

    rewritten = await _rewrite_text_for_language(cleaned, "en", client)
    if rewritten != cleaned:
        return rewritten, "english_rewrite"
    return cleaned, "none"


async def _fetch_fish_model_metadata(http: httpx.AsyncClient, reference_id: str) -> dict | None:
    if not reference_id:
        return None
    fish_api_key = _get_fish_api_key()
    if not fish_api_key:
        return None
    cached = _fish_model_meta_cache.get(reference_id)
    if cached:
        return cached
    try:
        response = await http.get(
            f"https://api.fish.audio/model/{reference_id}",
            headers={"Authorization": f"Bearer {fish_api_key}"},
        )
        if response.status_code != 200:
            log.warning(f"Fish model metadata lookup failed for {reference_id}: HTTP {response.status_code}")
            return None
        payload = response.json()
        _fish_model_meta_cache[reference_id] = payload
        return payload
    except Exception as exc:
        log.warning(f"Fish model metadata lookup failed for {reference_id}: {exc}")
        return None


def _voice_supports_language(meta: dict | None, language: str) -> bool:
    if not meta:
        return False
    languages = [str(item).lower() for item in (meta.get("languages") or [])]
    return language.lower() in languages


async def _choose_fish_tts_config(http: httpx.AsyncClient, raw_text: str) -> dict:
    backend = _get_fish_tts_backend()
    requested_reference_id = _get_active_fish_voice_id()
    requested_meta = await _fetch_fish_model_metadata(http, requested_reference_id)
    selected_reference_id = requested_reference_id
    selected_meta = requested_meta
    fallback_reason = ""
    english_ratio = _estimate_english_content_ratio(raw_text)
    indonesian_ratio = _estimate_indonesian_content_ratio(raw_text)
    normalized_text, normalization_mode = await _prepare_spoken_text_for_tts(raw_text, anthropic_client)

    if _is_indonesian_mode() and not _voice_supports_language(requested_meta, "id"):
        fallback_id = (_get_fish_indonesian_voice_id() or _get_fish_indonesian_fallback_voice_id())
        if fallback_id and fallback_id != requested_reference_id:
            selected_reference_id = fallback_id
            selected_meta = await _fetch_fish_model_metadata(http, selected_reference_id)
            fallback_reason = "requested voice does not advertise Indonesian support"

    return {
        "backend": backend,
        "raw_text": raw_text.strip(),
        "normalized_text": normalized_text,
        "requested_reference_id": requested_reference_id,
        "selected_reference_id": selected_reference_id,
        "requested_voice_meta": requested_meta or {},
        "selected_voice_meta": selected_meta or {},
        "english_ratio": round(english_ratio, 4),
        "indonesian_ratio": round(indonesian_ratio, 4),
        "normalization_mode": normalization_mode,
        "target_language": get_user_language(),
        "fallback_reason": fallback_reason,
    }


def _append_fish_tts_debug_entry(entry: dict):
    try:
        _FISH_TTS_DEBUG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _FISH_TTS_DEBUG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

async def _synthesize_local_tts(text: str) -> Optional[bytes]:
    """Generate speech using the configured local TTS server."""
    engine = get_local_tts_engine()
    ok, detail = await _ensure_local_tts_server(
        get_local_tts_base_url(engine),
        engine,
        get_local_tts_model_path(),
    )
    if not ok:
        log.error(f"Local TTS server unavailable: {detail.get('error', 'unknown error')}")
        return None

    normalized_text, normalization_mode = await _prepare_spoken_text_for_tts(text, anthropic_client)
    if not normalized_text:
        return None

    target_language = get_user_language()
    voice = get_local_tts_voice(target_language)
    speed = get_local_tts_speed()
    base_url = detail.get("resolved_base_url") or get_local_tts_base_url(engine)

    try:
        async with httpx.AsyncClient(timeout=20.0, trust_env=False) as http:
            resp = await http.post(
                f"{base_url}/tts",
                json={
                    "text": normalized_text,
                    "voice": voice,
                    "speed": speed,
                    "language": target_language,
                },
            )
            if resp.status_code == 200:
                _session_tokens["tts_calls"] += 1
                return resp.content
            log.error(
                "Local TTS error: %s %s | engine=%s | mode=%s | voice=%s | lang=%s",
                resp.status_code,
                resp.text[:200],
                engine,
                normalization_mode,
                voice,
                target_language,
            )
            return None
    except Exception as e:
        log.error(f"Local TTS request failed: {_compact_error_text(str(e))}")
        return None


async def synthesize_speech(text: str) -> Optional[bytes]:
    """Generate speech audio — uses local TTS if JARVIS_TTS_PROVIDER=local, else Fish Audio."""
    if get_tts_provider() == "local":
        return await _synthesize_local_tts(text)

    fish_api_key = _get_fish_api_key()
    if not fish_api_key:
        log.warning("FISH_API_KEY not set, skipping TTS")
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            config = await _choose_fish_tts_config(http, text)
            response = await http.post(
                FISH_API_URL,
                headers={
                    "Authorization": f"Bearer {fish_api_key}",
                    "Content-Type": "application/json",
                    "model": config["backend"],
                },
                json={
                    "text": config["normalized_text"],
                    "reference_id": config["selected_reference_id"],
                    "format": "mp3",
                    "normalize": True,
                    "latency": "balanced",
                },
            )
            debug_entry = {
                "ts": time.time(),
                "backend": config["backend"],
                "target_language": config["target_language"],
                "requested_reference_id": config["requested_reference_id"],
                "selected_reference_id": config["selected_reference_id"],
                "requested_voice_title": (config["requested_voice_meta"] or {}).get("title"),
                "selected_voice_title": (config["selected_voice_meta"] or {}).get("title"),
                "requested_voice_languages": (config["requested_voice_meta"] or {}).get("languages"),
                "selected_voice_languages": (config["selected_voice_meta"] or {}).get("languages"),
                "raw_text": config["raw_text"],
                "normalized_text": config["normalized_text"],
                "english_ratio": config["english_ratio"],
                "indonesian_ratio": config["indonesian_ratio"],
                "normalization_mode": config["normalization_mode"],
                "fallback_reason": config["fallback_reason"],
                "status_code": response.status_code,
            }
            _append_fish_tts_debug_entry(debug_entry)
            if _is_fish_tts_debug_enabled():
                log.info(
                    "FISH_TTS backend=%s target=%s requested_ref=%s selected_ref=%s requested_langs=%s selected_langs=%s mode=%s english_ratio=%.2f indonesian_ratio=%.2f fallback=%s raw=%r normalized=%r status=%s",
                    config["backend"],
                    config["target_language"],
                    config["requested_reference_id"],
                    config["selected_reference_id"],
                    (config["requested_voice_meta"] or {}).get("languages"),
                    (config["selected_voice_meta"] or {}).get("languages"),
                    config["normalization_mode"],
                    config["english_ratio"],
                    config["indonesian_ratio"],
                    config["fallback_reason"] or "-",
                    config["raw_text"][:240],
                    config["normalized_text"][:240],
                    response.status_code,
                )
            if response.status_code == 200:
                _session_tokens["tts_calls"] += 1
                _append_usage_entry(0, 0, "tts")
                return response.content
            else:
                log.error(f"TTS error: {response.status_code}")
                return None
    except Exception as e:
        log.error(f"TTS error: {e}")
        return None


# ---------------------------------------------------------------------------
# LLM Response
# ---------------------------------------------------------------------------

async def generate_response(
    text: str,
    client: Any,
    task_mgr: ClaudeTaskManager,
    projects: list[dict],
    conversation_history: list[dict],
    last_response: str = "",
    session_summary: str = "",
) -> str:
    """Generate a JARVIS response using Anthropic API or local LLM."""
    now = datetime.now()
    current_time = now.strftime("%A, %B %d, %Y at %I:%M %p")

    # Use cached weather
    weather_info = _ctx_cache.get("weather", "Weather data unavailable.")

    # --- Local LLM: use compact prompt to fit within small context windows ---
    if get_llm_provider() == "local":
        memory_ctx = _compact_local_context_value(build_memory_context(text) or "(none)", 420)
        local_weather_info = _compact_local_context_value(weather_info, 180)
        system = JARVIS_LOCAL_LIGHT_SYSTEM_PROMPT.format(
            user_name=get_user_name(),
            honorific=get_honorific(),
            current_time=current_time,
            weather_info=local_weather_info,
            user_location=get_user_location(),
            user_country=get_user_country(),
            memory_ctx=memory_ctx,
        )
        system += f"\n\n{_language_instruction_block()}"
        if last_response:
            system += f'\n\nLAST RESPONSE (do not repeat): "{last_response[:100]}"'
        # Keep last 8 messages (4 turns) — Gemma 4 has 8K+ context, 4 turns is safe.
        messages = conversation_history[-8:]
        if not messages or messages[-1].get("content") != text:
            messages = messages + [{"role": "user", "content": text}]
        try:
            response = await _call_jarvis_model(
                client,
                model=JARVIS_CHAT_MODEL,
                max_tokens=180,
                system=system,
                messages=messages,
            )
            response_text = response.content[0].text.strip()
            if not response_text:
                raise RuntimeError("Local LLM returned an empty final answer.")
            _mark_llm_available()
            return response_text
        except Exception as e:
            log.warning(f"Local LLM primary prompt failed: {e}")
            retry_system = JARVIS_LOCAL_RETRY_SYSTEM_PROMPT.format(
                user_name=get_user_name(),
                honorific=get_honorific(),
            )
            retry_system += f"\n\n{_language_instruction_block()}"
            try:
                retry_response = await _call_jarvis_model(
                    client,
                    model=JARVIS_CHAT_MODEL,
                    max_tokens=120,
                    system=retry_system,
                    messages=[{"role": "user", "content": text}],
                )
                retry_text = retry_response.content[0].text.strip()
                if not retry_text:
                    raise RuntimeError("Local LLM retry also returned an empty final answer.")
                _mark_llm_available()
                return retry_text
            except Exception as retry_error:
                log.error(f"Local LLM error: {retry_error}")
                _mark_llm_unavailable(str(retry_error))
                asyncio.create_task(_async_probe_and_update_local_llm())
                return _localized_text(
                    "I can't reach my local model right now. Make sure LM Studio is running and a model is loaded.",
                    "Model lokal tidak bisa dijangkau. Pastikan LM Studio sudah berjalan dan ada model yang dimuat.",
                )

    # --- Anthropic path: full context prompt ---
    # Use cached context (refreshed in background, never blocks responses)
    screen_ctx = _ctx_cache["screen"]
    calendar_ctx = _ctx_cache["calendar"]
    mail_ctx = _ctx_cache["mail"]
    news_ctx = _ctx_cache.get("news", "No local news snapshot yet.")

    # Check if any lookups are in progress
    lookup_status = get_lookup_status()

    system = JARVIS_SYSTEM_PROMPT.format(
        current_time=current_time,
        weather_info=weather_info,
        screen_context=screen_ctx or "Not checked yet.",
        calendar_context=calendar_ctx,
        mail_context=mail_ctx,
        news_context=news_ctx,
        active_tasks=task_mgr.get_active_tasks_summary(),
        dispatch_context=dispatch_registry.format_for_prompt(),
        known_projects=format_projects_for_prompt(projects),
        user_name=get_user_name(),
        honorific=get_honorific(),
        user_location=get_user_location(),
        user_country=get_user_country(),
        project_dir=PROJECT_DIR,
    )
    if lookup_status:
        system += f"\n\nACTIVE LOOKUPS:\n{lookup_status}\nIf asked about progress, report this status."

    system += f"\n\n{_language_instruction_block()}"

    # Inject relevant memories and tasks
    memory_ctx = build_memory_context(text)
    if memory_ctx:
        system += f"\n\nJARVIS MEMORY:\n{memory_ctx}"

    # Three-tier memory — inject rolling summary of earlier conversation
    if session_summary:
        system += f"\n\nSESSION CONTEXT (earlier in this conversation):\n{session_summary}"

    # Self-awareness — remind JARVIS of last response to avoid repetition
    if last_response:
        system += f'\n\nYOUR LAST RESPONSE (do not repeat this):\n"{last_response[:150]}"'

    # Use conversation history — keep the last 20 messages for context
    # (older conversation is captured in session_summary)
    messages = conversation_history[-20:]
    # If the last message isn't the current user text, add it
    if not messages or messages[-1].get("content") != text:
        messages = messages + [{"role": "user", "content": text}]

    try:
        chosen_model = JARVIS_CHAT_MODEL
        if (
            JARVIS_ULTRA_ECO_MODE
            and JARVIS_CHAT_FALLBACK_MODEL
            and JARVIS_CHAT_FALLBACK_MODEL != JARVIS_CHAT_MODEL
            and _looks_complex_for_ultra_eco_chat(text)
        ):
            chosen_model = JARVIS_CHAT_FALLBACK_MODEL

        log.info(f"JARVIS chat model selected: {chosen_model}")
        response = await _call_jarvis_model(
            client,
            model=chosen_model,
            max_tokens=250,
            system=system,
            messages=messages,
        )
        response_text = response.content[0].text

        if (
            JARVIS_ULTRA_ECO_MODE
            and chosen_model == JARVIS_CHAT_MODEL
            and JARVIS_CHAT_FALLBACK_MODEL
            and JARVIS_CHAT_FALLBACK_MODEL != JARVIS_CHAT_MODEL
            and _should_retry_ultra_eco_with_fallback(response_text)
        ):
            log.info(
                "JARVIS ultra-eco escalation: retrying chat response with fallback model "
                f"{JARVIS_CHAT_FALLBACK_MODEL}"
            )
            fallback_response = await _call_jarvis_model(
                client,
                model=JARVIS_CHAT_FALLBACK_MODEL,
                max_tokens=250,
                system=system,
                messages=messages,
            )
            response_text = fallback_response.content[0].text

        _mark_llm_available()
        return response_text
    except Exception as e:
        # This path is only reached via the Anthropic path (local LLM has its own
        # early-return handler above that returns before reaching this try/except).
        log.error(f"LLM error: {e}")
        _mark_llm_unavailable(str(e))
        if get_local_fallback_mode():
            return await generate_local_fallback_response(text, llm_error=str(e))
        return _localized_text(
            "Apologies, sir. I'm having trouble connecting to my language systems.",
            f"Maaf, {get_honorific()}. Sistem bahasaku sedang bermasalah.",
        )


async def generate_chat_response(
    text: str,
    task_mgr: ClaudeTaskManager,
    projects: list[dict],
    conversation_history: list[dict],
    last_response: str = "",
    session_summary: str = "",
) -> str:
    if anthropic_client:
        return await generate_response(
            text,
            anthropic_client,
            task_mgr,
            projects,
            conversation_history,
            last_response=last_response,
            session_summary=session_summary,
        )
    provider = get_llm_provider()
    if provider == "nvidia":
        _mark_llm_unavailable("NVIDIA API key not configured.")
    elif provider == "local":
        _mark_llm_unavailable("Local provider is configured, but no local client is ready.")
    else:
        _mark_llm_unavailable("Anthropic API key not configured.")
    if get_local_fallback_mode():
        return await generate_local_fallback_response(text)
    return _localized_text(
        "API key not configured.",
        "API key belum dikonfigurasi.",
    )


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

# Shared state
task_manager = ClaudeTaskManager(max_concurrent=3)
anthropic_client: Any | None = None
cached_projects: list[dict] = []
recently_built: list[dict] = []  # [{"name": str, "path": str, "time": float}]
dispatch_registry = DispatchRegistry()

# Usage tracking — logs every call with timestamp, persists to disk
_USAGE_FILE = Path(__file__).parent / "data" / "usage_log.jsonl"
_session_start = time.time()
_session_tokens = {"input": 0, "output": 0, "api_calls": 0, "tts_calls": 0}


def _append_usage_entry(input_tokens: int, output_tokens: int, call_type: str = "api"):
    """Append a usage entry with timestamp to the log file."""
    try:
        _USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        import json as _json
        entry = {
            "ts": time.time(),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "type": call_type,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        with open(_USAGE_FILE, "a") as f:
            f.write(_json.dumps(entry) + "\n")
    except Exception:
        pass


def _get_usage_for_period(seconds: float | None = None) -> dict:
    """Sum usage from the log file for a time period. None = all time."""
    import json as _json
    totals = {"input_tokens": 0, "output_tokens": 0, "api_calls": 0, "tts_calls": 0}
    cutoff = (time.time() - seconds) if seconds else 0
    try:
        if _USAGE_FILE.exists():
            for line in _USAGE_FILE.read_text().strip().split("\n"):
                if not line:
                    continue
                entry = _json.loads(line)
                if entry["ts"] >= cutoff:
                    totals["input_tokens"] += entry.get("input_tokens", 0)
                    totals["output_tokens"] += entry.get("output_tokens", 0)
                    if entry.get("type") == "tts":
                        totals["tts_calls"] += 1
                    else:
                        totals["api_calls"] += 1
    except Exception:
        pass
    return totals


def _cost_from_tokens(input_t: int, output_t: int) -> float:
    return (input_t / 1_000_000) * 0.80 + (output_t / 1_000_000) * 4.00


def track_usage(response):
    """Track token usage from an Anthropic API response."""
    inp = getattr(response.usage, "input_tokens", 0) if hasattr(response, "usage") else 0
    out = getattr(response.usage, "output_tokens", 0) if hasattr(response, "usage") else 0
    _session_tokens["input"] += inp
    _session_tokens["output"] += out
    _session_tokens["api_calls"] += 1
    _append_usage_entry(inp, out, "api")


def get_usage_summary() -> str:
    """Get a voice-friendly usage summary with time breakdowns."""
    uptime_min = int((time.time() - _session_start) / 60)

    session = _session_tokens
    today = _get_usage_for_period(86400)
    week = _get_usage_for_period(86400 * 7)
    all_time = _get_usage_for_period(None)

    session_cost = _cost_from_tokens(session["input"], session["output"])
    today_cost = _cost_from_tokens(today["input_tokens"], today["output_tokens"])
    all_cost = _cost_from_tokens(all_time["input_tokens"], all_time["output_tokens"])

    parts = [f"This session: {uptime_min} minutes, {session['api_calls']} calls, ${session_cost:.2f}."]

    if today["api_calls"] > session["api_calls"]:
        parts.append(f"Today total: {today['api_calls']} calls, ${today_cost:.2f}.")

    if all_time["api_calls"] > today["api_calls"]:
        parts.append(f"All time: {all_time['api_calls']} calls, ${all_cost:.2f}.")

    return " ".join(parts)


async def announce_via_jarvis_voice(text: str, source: str = "external") -> dict:
    """Broadcast an announcement through any connected JARVIS voice clients."""
    clean_text = text.strip()
    listener_count = len(task_manager._websockets)
    if not clean_text:
        return {"success": False, "delivered": False, "listeners": listener_count, "error": "Empty text"}
    if listener_count == 0:
        log.info(f"Announcement queued nowhere ({source}) — no active listeners")
        return {"success": False, "delivered": False, "listeners": 0, "error": "No active JARVIS voice clients"}

    log.info(f"Announcement via JARVIS ({source}): {clean_text[:160]}")
    tts = strip_markdown_for_tts(clean_text)
    audio = await synthesize_speech(tts)

    await task_manager._notify({"type": "status", "state": "speaking"})
    if audio:
        await task_manager._notify({
            "type": "audio",
            "data": base64.b64encode(audio).decode(),
            "text": clean_text,
            "source": source,
        })
    else:
        await task_manager._notify({
            "type": "text",
            "text": clean_text,
            "source": source,
        })
    await task_manager._notify({"type": "status", "state": "idle"})

    return {"success": True, "delivered": True, "listeners": listener_count}

# Background context cache — never blocks responses
_ctx_cache = {
    "screen": "",
    "calendar": "No calendar data yet.",
    "mail": "No mail data yet.",
    "weather": "Weather data unavailable.",
    "news": "No local news snapshot yet.",
    "briefing": "",
    "briefing_spoken": "",
    "briefing_spoken_source": "",
    "briefing_spoken_language": "",
}


def _refresh_context_sync():
    """Run in a SEPARATE THREAD — refreshes screen/calendar/mail context.

    This runs completely off the async event loop so it never blocks responses.
    """
    import threading

    def _worker():
        last_weather_refresh = 0.0
        last_news_refresh = 0.0
        last_briefing_refresh = 0.0
        while True:
            try:
                # Screen — fast
                try:
                    proc = __import__("subprocess").run(
                        ["osascript", "-e", '''
set windowList to ""
tell application "System Events"
    set frontApp to name of first application process whose frontmost is true
    set visibleApps to every application process whose visible is true
    repeat with proc in visibleApps
        set appName to name of proc
        try
            set winCount to count of windows of proc
            if winCount > 0 then
                repeat with w in (windows of proc)
                    try
                        set winTitle to name of w
                        if winTitle is not "" and winTitle is not missing value then
                            set windowList to windowList & appName & "|||" & winTitle & "|||" & (appName = frontApp) & linefeed
                        end if
                    end try
                end repeat
            end if
        end try
    end repeat
end tell
return windowList
'''],
                        capture_output=True, text=True, timeout=5
                    )
                    if proc.returncode == 0 and proc.stdout.strip():
                        windows = []
                        for line in proc.stdout.strip().split("\n"):
                            parts = line.strip().split("|||")
                            if len(parts) >= 3:
                                windows.append({
                                    "app": parts[0].strip(),
                                    "title": parts[1].strip(),
                                    "frontmost": parts[2].strip().lower() == "true",
                                })
                        if windows:
                            _ctx_cache["screen"] = format_windows_for_context(windows)
                except Exception:
                    pass

            except Exception as e:
                log.debug(f"Context thread error: {e}")

            now_ts = time.time()

            if now_ts - last_weather_refresh >= 180:
                try:
                    _ctx_cache["weather"] = _fetch_weather_brief_sync(get_user_location(), get_user_country())
                except Exception as e:
                    log.debug(f"Weather refresh failed: {e}")
                last_weather_refresh = now_ts

            if now_ts - last_news_refresh >= 300:
                try:
                    _ctx_cache["news"] = _build_local_news_context_sync(
                        get_user_location(),
                        get_user_country(),
                        get_user_timezone(),
                    )
                except Exception as e:
                    log.debug(f"News refresh failed: {e}")
                last_news_refresh = now_ts

            if now_ts - last_briefing_refresh >= 300:
                try:
                    new_briefing = _build_first_turn_briefing_sync(
                        get_user_location(),
                        get_user_country(),
                        get_user_timezone(),
                    )
                    _ctx_cache["briefing"] = new_briefing
                    if _ctx_cache.get("briefing_spoken_source") != new_briefing:
                        _ctx_cache["briefing_spoken"] = ""
                        _ctx_cache["briefing_spoken_source"] = ""
                        _ctx_cache["briefing_spoken_language"] = ""
                except Exception as e:
                    log.debug(f"Briefing refresh failed: {e}")
                last_briefing_refresh = now_ts

            time.sleep(30)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    log.info("Context refresh thread started")


@asynccontextmanager
async def lifespan(application: FastAPI):
    global anthropic_client, cached_projects
    anthropic_client = _refresh_llm_client()
    if anthropic_client:
        log.info(f"LLM provider ready: {get_llm_provider()}")
        if get_llm_provider() == "local":
            asyncio.create_task(_async_probe_and_update_local_llm())
    else:
        log.warning(f"LLM provider unavailable at startup: {get_llm_provider()}")

    # Pre-check Whisper availability at startup
    whisper_model = get_whisper_model()
    if whisper_model:
        log.info("✓ Offline transcription (Whisper) available")
    else:
        log.warning("✗ Offline transcription will NOT work — faster-whisper not available")
    cached_projects = []
    try:
        feedback_sync = sync_feedback_logs(force=True)
        if feedback_sync["scanned_files"]:
            log.info(
                "Feedback log sync at startup: scanned=%s parsed=%s imported=%s updated=%s",
                feedback_sync["scanned_files"],
                feedback_sync["parsed_entries"],
                feedback_sync["imported_entries"],
                feedback_sync["updated_entries"],
            )
    except Exception as exc:
        log.warning(f"Feedback log sync failed at startup: {exc}")

    # Start context refresh in a separate thread (never touches event loop)
    _refresh_context_sync()
    log.info("JARVIS server starting")
    asyncio.create_task(warm_wiz_controller())

    yield


app = FastAPI(title="JARVIS Server", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -- REST Endpoints --------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "online", "name": "JARVIS", "version": "0.1.0"}


@app.get("/api/tts-test")
async def tts_test():
    """Generate a test audio clip for debugging."""
    audio = await synthesize_speech("Testing audio, sir.")
    if audio:
        return {"audio": base64.b64encode(audio).decode()}
    return {"audio": None, "error": "TTS failed"}


@app.post("/api/transcribe")
async def api_transcribe(request: Request):
    """Transcribe audio using faster-whisper (offline, no internet required).

    Accepts raw audio bytes in request body (WAV, WebM, OGG, MP3, etc.)
    Returns: {text, language, confidence}
    """
    model = get_whisper_model()
    if not model:
        return JSONResponse(
            status_code=503,
            content={"error": "Whisper model not available. Run: pip install faster-whisper"}
        )

    try:
        # Read raw audio bytes from request body
        audio_bytes = await request.body()
        if not audio_bytes:
            return JSONResponse(status_code=400, content={"error": "Empty audio file"})

        # Determine correct file extension from Content-Type header
        import tempfile
        content_type = request.headers.get("content-type", "audio/webm").lower()
        if "wav" in content_type:
            ext = ".wav"
        elif "mp3" in content_type or "mpeg" in content_type:
            ext = ".mp3"
        elif "ogg" in content_type:
            ext = ".ogg"
        elif "mp4" in content_type or "m4a" in content_type:
            ext = ".mp4"
        else:
            ext = ".webm"  # default: Chrome records as webm/opus

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            # Transcribe (run in thread pool — Whisper is CPU-bound)
            user_lang = get_user_language()
            lang_hint = user_lang[:2] if user_lang and user_lang.lower().startswith("id") else None

            def _transcribe():
                kwargs = {"vad_filter": True}
                if lang_hint:
                    kwargs["language"] = lang_hint
                segments, info = model.transcribe(tmp_path, **kwargs)
                text = " ".join([s.text.strip() for s in segments])
                return {
                    "text": text,
                    "language": info.language,
                    "confidence": info.language_probability,
                }

            import concurrent.futures
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _transcribe)

            log.info(
                "Whisper transcribe language=%s confidence=%.3f text=%s",
                result.get("language"),
                float(result.get("confidence") or 0.0),
                (result.get("text") or "")[:120],
            )
            return result
        finally:
            # Clean up temp file
            import os as _os
            try:
                _os.unlink(tmp_path)
            except:
                pass

    except Exception as e:
        log.error(f"Transcribe error: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Transcription failed: {_compact_error_text(str(e))}"}
        )


@app.get("/api/usage")
async def api_usage():
    uptime = int(time.time() - _session_start)
    today = _get_usage_for_period(86400)
    week = _get_usage_for_period(86400 * 7)
    month = _get_usage_for_period(86400 * 30)
    all_time = _get_usage_for_period(None)
    return {
        "session": {**_session_tokens, "uptime_seconds": uptime},
        "today": {**today, "cost_usd": round(_cost_from_tokens(today["input_tokens"], today["output_tokens"]), 4)},
        "week": {**week, "cost_usd": round(_cost_from_tokens(week["input_tokens"], week["output_tokens"]), 4)},
        "month": {**month, "cost_usd": round(_cost_from_tokens(month["input_tokens"], month["output_tokens"]), 4)},
        "all_time": {**all_time, "cost_usd": round(_cost_from_tokens(all_time["input_tokens"], all_time["output_tokens"]), 4)},
    }


@app.get("/api/tasks")
async def api_list_tasks():
    tasks = await task_manager.list_tasks()
    return {"tasks": [t.to_dict() for t in tasks]}


@app.get("/api/tasks/{task_id}")
async def api_get_task(task_id: str):
    task = await task_manager.get_status(task_id)
    if not task:
        return JSONResponse(status_code=404, content={"error": "Task not found"})
    return {"task": task.to_dict()}


@app.post("/api/tasks")
async def api_create_task(req: TaskRequest):
    try:
        task_id = await task_manager.spawn(req.prompt, req.working_dir)
        return {"task_id": task_id, "status": "spawned"}
    except RuntimeError as e:
        return JSONResponse(status_code=429, content={"error": str(e)})


@app.delete("/api/tasks/{task_id}")
async def api_cancel_task(task_id: str):
    cancelled = await task_manager.cancel(task_id)
    if not cancelled:
        return JSONResponse(
            status_code=404,
            content={"error": "Task not found or not cancellable"},
        )
    return {"task_id": task_id, "status": "cancelled"}


@app.get("/api/projects")
async def api_list_projects():
    global cached_projects
    cached_projects = await scan_projects()
    return {"projects": cached_projects}


# -- Fast Action Detection (no LLM call) -----------------------------------

def _scan_projects_sync() -> list[dict]:
    """Synchronous Desktop scan — runs in executor."""
    projects = []
    desktop = Path.home() / "Desktop"
    try:
        for entry in desktop.iterdir():
            if entry.is_dir() and not entry.name.startswith("."):
                projects.append({"name": entry.name, "path": str(entry), "branch": ""})
    except Exception:
        pass
    return projects


def detect_action_fast(text: str) -> dict | None:
    """Keyword-based action detection — ONLY for short, obvious commands.

    Everything else goes to the LLM which uses [ACTION:X] tags when it decides
    to act based on conversational understanding.
    """
    t = text.lower().strip()

    # --- Correction / teaching detection ---
    # Triggered when user says JARVIS did something wrong.
    # Returns a special action so the websocket handler can store the correction in memory.
    _CORRECTION_TRIGGERS = (
        "itu salah", "bukan begitu", "bukan itu", "kamu salah", "kau salah",
        "harusnya", "seharusnya", "lain kali", "ingat ini", "catat ini",
        "that's wrong", "that was wrong", "no that's not right",
        "you got it wrong", "remember this", "next time",
    )
    if any(p in t for p in _CORRECTION_TRIGGERS):
        return {"action": "correction", "target": text}

    if any(p in t for p in [
        "brief me", "daily briefing", "news briefing", "read the briefing",
        "give me the briefing", "give me the news", "read me the news",
        "read the news", "bacakan briefing", "bacakan berita",
        "berita hari ini", "berita terpanas", "morning briefing"
    ]):
        return {"action": "read_briefing"}

    if any(p in t for p in [
        "can you hear me", "are you listening", "do you hear me",
        "apakah kau mendengarku", "kau mendengarku", "bisa dengar aku",
        "apakah kamu mendengarku", "kamu dengar aku"
    ]):
        return {"action": "acknowledge_voice"}

    # Screen requests — checked BEFORE project matching to prevent misrouting
    if any(p in t for p in ["look at my screen", "what's on my screen", "whats on my screen",
                             "what am i looking at", "what do you see", "see my screen",
                             "what's running on my", "whats running on my", "check my screen"]):
        return {"action": "describe_screen"}

    # Terminal / Claude Code — explicit open requests
    if any(w in t for w in ["open claude", "start claude", "launch claude", "run claude"]):
        return {"action": "open_terminal"}

    codex_request = extract_codex_request(text)
    if codex_request:
        return {"action": "ask_codex", "target": codex_request}

    folder_request = extract_folder_request(text)
    if folder_request:
        return {"action": "create_folder", "target": folder_request}

    fan_request = extract_air_fan_request(text)
    if fan_request:
        return {"action": "fan", "target": fan_request}

    # --- Direct scan / discovery shortcut ---
    # Catches natural scan queries before extract_wiz_request even runs,
    # including Indonesian question forms that pattern-matching may miss.
    _SCAN_TRIGGERS = (
        # Indonesian — various natural forms
        "cek lampu", "scan lampu", "cari lampu", "periksa lampu",
        "lihat lampu", "lihat status lampu", "cek status lampu",
        "status lampu", "info lampu", "pantau lampu", "temukan lampu",
        "ada berapa lampu", "berapa lampu", "lampu berapa",
        "berapa banyak lampu", "lampu yang online", "lampu yang nyala",
        "lampu yang terhubung", "lampu yang aktif", "lampu yang terdeteksi",
        "ada lampu apa", "lampu apa saja", "lampu apa yang ada",
        "mana saja lampunya", "lampunya yang mana",
        # casual / colloquial Indonesian
        "scanning lampu", "cek dong lampunya", "scan dong", "cek dong",
        "lampunya ada berapa", "ada berapa lampu yang", "ada berapa biji lampu",
        "lampu ada berapa", "lampu ada apa", "berapa biji lampu",
        # English
        "scan lights", "scan lamps", "check lights", "check lamps",
        "check lamp", "lamp status", "light status", "list lamps",
        "list lights", "show lamps", "show lights",
        "how many lights", "how many lamps", "which lights",
        "which lamps", "what lights", "what lamps",
    )
    if any(p in t for p in _SCAN_TRIGGERS):
        return {"action": "wiz", "target": {"kind": "discover"}}

    wiz_request = extract_wiz_request(text)
    if wiz_request:
        return {"action": "wiz", "target": wiz_request}

    # --- Aggressive WiZ fallback: short message + lamp keyword → force WiZ parse ---
    # Catches casual phrases: "lampunya dong", "gelapin dong", "terangin", dll
    _LAMP_KEYWORDS = (
        "lampu", "lamp", "light", "lights", "wiz",
        "terang", "gelap", "redup", "nyala", "mati",
        "warna", "color", "colour",  # catch "ganti warna ke merah" etc.
    )
    _len = len(t.split())
    if _len <= 8 and any(kw in t for kw in _LAMP_KEYWORDS):
        # Map casual phrases to standard commands before re-parsing
        _casual_map = {
            "gelapin":  "lampu 20 persen",
            "redupin":  "lampu 20 persen",
            "terangin": "lampunya putih terang",
            "matiin":   "matikan lampu",
            "nyalain":  "nyalakan lampu",
            "hidupkan": "nyalakan lampu",
            "padamkan": "matikan lampu",
            # scan casual forms
            "cek aja":   "cek lampu",
            "scan aja":  "scan lampu",
            "scan dulu": "scan lampu",
            "cek dulu":  "cek lampu",
        }
        _remapped = t
        for casual, canonical in _casual_map.items():
            if casual in _remapped:
                _remapped = canonical
                break
        _wiz_retry = extract_wiz_request(_remapped) if _remapped != t else None
        if _wiz_retry:
            return {"action": "wiz", "target": _wiz_retry}

    spotify_control = extract_spotify_control(text)
    if spotify_control:
        return {"action": "spotify_control", "target": spotify_control}

    spotify_query = extract_spotify_query(text)
    if spotify_query:
        return {"action": "play_spotify", "target": spotify_query}

    words = t.split()

    # Only trigger on SHORT, clear commands (< 12 words) for non-Spotify actions
    if len(words) > 12:
        return None  # Long messages are conversation, not commands

    app_target = normalize_desktop_app_name(t)
    if app_target:
        return {"action": "open_app", "target": app_target}

    # Show recent build
    if any(w in t for w in ["show me what you built", "pull up what you made", "open what you built"]):
        return {"action": "show_recent"}

    # Screen awareness — explicit look/see requests
    if any(p in t for p in ["what's on my screen", "whats on my screen", "what do you see",
                             "can you see my screen", "look at my screen", "what am i looking at",
                             "what's open", "whats open", "what apps are open"]):
        return {"action": "describe_screen"}

    # Calendar — explicit schedule requests
    if any(p in t for p in ["what's my schedule", "whats my schedule", "what's on my calendar",
                             "whats on my calendar", "do i have any meetings", "any meetings",
                             "what's next on my calendar", "my schedule today",
                             "what do i have today", "my calendar", "upcoming meetings",
                             "next meeting", "what's my next meeting"]):
        return {"action": "check_calendar"}

    # Mail — explicit email requests
    if any(p in t for p in ["check my email", "check my mail", "any new emails", "any new mail",
                             "unread emails", "unread mail", "what's in my inbox",
                             "whats in my inbox", "read my email", "read my mail",
                             "any emails", "any mail", "email update", "mail update"]):
        return {"action": "check_mail"}

    # Dispatch / build status check
    if any(p in t for p in ["where are we", "where were we", "project status", "how's the build",
                             "hows the build", "status update", "status report", "where is that",
                             "how's it going with", "hows it going with", "is it done",
                             "is that done", "what happened with"]):
        return {"action": "check_dispatch"}

    # Task list check
    if any(p in t for p in ["what's on my list", "whats on my list", "my tasks", "my to do",
                             "my todo", "what do i need to do", "open tasks", "task list"]):
        return {"action": "check_tasks"}

    # Usage / cost check
    if any(p in t for p in ["usage", "how much have you cost", "how much am i spending",
                             "what's the cost", "whats the cost", "api cost", "token usage",
                             "how expensive", "what's my bill"]):
        return {"action": "check_usage"}

    return None  # Everything else goes to the LLM for conversational routing


# -- Action Handlers -------------------------------------------------------

async def handle_open_terminal() -> str:
    result = await open_terminal("claude --dangerously-skip-permissions")
    return result["confirmation"]


async def handle_open_app(target: str) -> str:
    result = await open_app(target)
    return result["confirmation"]


async def handle_play_spotify(target: str) -> str:
    result = await play_spotify(target)
    return result["confirmation"]


async def handle_spotify_control(target: str) -> str:
    result = await control_spotify(target)
    return result["confirmation"]


async def handle_ask_codex(target: str | dict) -> str:
    if isinstance(target, dict):
        result = await ask_codex(
            str(target.get("prompt", "")),
            new_thread=bool(target.get("new_thread")),
        )
    else:
        result = await ask_codex(target)
    return result["confirmation"]


async def handle_create_folder(request: dict) -> str:
    result = await create_folder_from_request(request)
    return result["confirmation"]


async def handle_wiz(request: dict) -> str:
    result = await control_wiz(request)
    return result["confirmation"]


async def handle_fan(request: dict) -> str:
    result = await control_air_fan(request)
    return result["confirmation"]


async def handle_read_briefing() -> str:
    return await _get_spoken_briefing_text()


async def handle_acknowledge_voice() -> str:
    return _localized_text(
        "Loud and clear, sir.",
        f"Terdengar jelas, {get_honorific()}.",
    )


async def handle_build(target: str) -> str:
    name = _generate_project_name(target)
    path = str(Path.home() / "Desktop" / name)
    os.makedirs(path, exist_ok=True)

    if should_use_codex_delegate():
        result = await handoff_to_codex(path, target, name)
        recently_built.append({"name": name, "path": path, "time": time.time()})
        return result["confirmation"]

    # Write CLAUDE.md with clear instructions
    claude_md = Path(path) / "CLAUDE.md"
    claude_md.write_text(f"# Task\n\n{target}\n\nBuild this completely. If web app, make index.html work standalone.\n")

    # Write prompt to a file, then pipe it to claude -p
    # This avoids all shell escaping issues
    prompt_file = Path(path) / ".jarvis_prompt.txt"
    prompt_file.write_text(target)

    script = (
        'tell application "Terminal"\n'
        "    activate\n"
        f'    do script "cd {path} && cat .jarvis_prompt.txt | claude -p --dangerously-skip-permissions"\n'
        "end tell"
    )
    await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    recently_built.append({"name": name, "path": path, "time": time.time()})
    return f"On it, sir. Claude Code is working in {name}."


async def handle_show_recent() -> str:
    if not recently_built:
        return "Nothing built recently, sir."
    last = recently_built[-1]
    project_path = Path(last["path"])

    # Try to find the best file to open
    for name in ["report.html", "index.html"]:
        f = project_path / name
        if f.exists():
            await open_browser(f"file://{f}")
            return f"Opened {name} from {last['name']}, sir."

    # Try any HTML file
    html_files = list(project_path.glob("*.html"))
    if html_files:
        await open_browser(f"file://{html_files[0]}")
        return f"Opened {html_files[0].name} from {last['name']}, sir."

    # Fall back to opening the folder in Finder
    script = f'tell application "Finder"\nactivate\nopen POSIX file "{last["path"]}"\nend tell'
    await asyncio.create_subprocess_exec("osascript", "-e", script, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    return f"Opened the {last['name']} folder in Finder, sir."


# ---------------------------------------------------------------------------
# Background lookup system — spawns slow tasks, reports back via voice
# ---------------------------------------------------------------------------

# Track active lookups so JARVIS can report status
_active_lookups: dict[str, dict] = {}  # id -> {"type": str, "status": str, "started": float}


async def _lookup_and_report(lookup_type: str, lookup_fn, ws, history: list[dict] = None, voice_state: dict = None):
    """Run a slow lookup, then speak the result back.

    JARVIS stays conversational — this runs completely off the main path.
    """
    lookup_id = str(uuid.uuid4())[:8]
    _active_lookups[lookup_id] = {
        "type": lookup_type,
        "status": "working",
        "started": time.time(),
    }

    try:
        # Run the async lookup directly — these functions already use
        # asyncio.create_subprocess_exec so they don't block the event loop
        result_text = await asyncio.wait_for(
            lookup_fn(),
            timeout=30,
        )

        _active_lookups[lookup_id]["status"] = "done"

        # Speak the result — skip audio if user spoke recently to avoid collision
        if voice_state and time.time() - voice_state["last_user_time"] < 3:
            log.info(f"Skipping lookup audio for {lookup_type} — user spoke recently")
            # Result is still stored in history below
        else:
            tts = strip_markdown_for_tts(result_text)
            audio = await synthesize_speech(tts)
            try:
                await ws.send_json({"type": "status", "state": "speaking"})
                if audio:
                    await ws.send_json({"type": "audio", "data": audio, "text": result_text})
                else:
                    await ws.send_json({"type": "text", "text": result_text})
                await ws.send_json({"type": "status", "state": "idle"})
            except Exception:
                pass

        log.info(f"Lookup {lookup_type} complete: {result_text[:80]}")

        # Store lookup result in conversation history so JARVIS remembers it
        if history is not None:
            history.append({"role": "assistant", "content": f"[{lookup_type} check]: {result_text}"})

    except asyncio.TimeoutError:
        _active_lookups[lookup_id]["status"] = "timeout"
        try:
            fallback = f"That {lookup_type} check is taking too long, sir. The data may still be syncing."
            audio = await synthesize_speech(fallback)
            await ws.send_json({"type": "status", "state": "speaking"})
            if audio:
                await ws.send_json({"type": "audio", "data": audio, "text": fallback})
            await ws.send_json({"type": "status", "state": "idle"})
        except Exception:
            pass
    except Exception as e:
        _active_lookups[lookup_id]["status"] = "error"
        log.warning(f"Lookup {lookup_type} failed: {e}")
    finally:
        # Clean up after 60s
        await asyncio.sleep(60)
        _active_lookups.pop(lookup_id, None)


async def _do_calendar_lookup() -> str:
    """Slow calendar fetch — runs in thread."""
    await refresh_calendar_cache()
    events = await get_todays_events()
    if events:
        _ctx_cache["calendar"] = format_events_for_context(events)
    return format_schedule_summary(events)


async def _do_mail_lookup() -> str:
    """Slow mail fetch — runs in thread."""
    unread_info = await get_unread_count()
    if isinstance(unread_info, dict):
        _ctx_cache["mail"] = format_unread_summary(unread_info)
        if unread_info["total"] == 0:
            return "Inbox is clear, sir. No unread messages."
        unread_msgs = await get_unread_messages(count=5)
        summary = format_unread_summary(unread_info)
        if unread_msgs:
            top = unread_msgs[:3]
            details = ". ".join(
                f"{_short_sender(m['sender'])} regarding {m['subject']}"
                for m in top
            )
            return f"{summary} Most recent: {details}."
        return summary
    return "Couldn't reach Mail at the moment, sir."


async def _do_screen_lookup() -> str:
    """Screen describe — runs in thread."""
    if anthropic_client:
        return await describe_screen(anthropic_client)
    windows = await get_active_windows()
    if windows:
        apps = set(w["app"] for w in windows)
        active = next((w for w in windows if w["frontmost"]), None)
        result = f"You have {', '.join(apps)} open."
        if active:
            result += f" Currently focused on {active['app']}: {active['title']}."
        return result
    return "Couldn't see the screen, sir."


def get_lookup_status() -> str:
    """Get status of active lookups for when user asks 'how's that coming'."""
    if not _active_lookups:
        return ""
    active = [v for v in _active_lookups.values() if v["status"] == "working"]
    if not active:
        return ""
    parts = []
    for lookup in active:
        elapsed = int(time.time() - lookup["started"])
        parts.append(f"{lookup['type']} check ({elapsed}s)")
    return "Currently working on: " + ", ".join(parts)


def _short_sender(sender: str) -> str:
    """Extract just the name from an email sender string."""
    if "<" in sender:
        return sender.split("<")[0].strip().strip('"')
    if "@" in sender:
        return sender.split("@")[0]
    return sender


async def handle_browse(text: str, target: str) -> str:
    """Open a URL directly or search. Smart about detecting URLs in speech."""
    import re
    from urllib.parse import quote

    browser = "firefox" if "firefox" in text.lower() else "chrome"
    combined = text.lower()

    # 1. Try to find a URL or domain in the text
    # Match things like "joetmd.com", "google.com/maps", "https://example.com"
    url_pattern = r'(?:https?://)?(?:www\.)?([a-zA-Z0-9][-a-zA-Z0-9]*(?:\.[a-zA-Z]{2,})+(?:/[^\s]*)?)'
    url_match = re.search(url_pattern, text, re.IGNORECASE)

    if url_match:
        domain = url_match.group(0)
        if not domain.startswith("http"):
            domain = "https://" + domain
        await open_browser(domain, browser)
        return f"Opened {url_match.group(0)}, sir."

    # 2. Check for spoken domains that speech-to-text mangled
    # "Joe tmd.com" → "joetmd.com", "roofo.co" etc.
    # Try joining words that end/start with a dot pattern
    words = text.split()
    for i, word in enumerate(words):
        # Look for word ending with common TLD
        if re.search(r'\.(com|co|io|ai|org|net|dev|app)$', word, re.IGNORECASE):
            # This word IS a domain — might have spaces before it
            domain = word
            # Check if previous word should be joined (e.g., "Joe tmd.com" → "joetmd.com" is tricky)
            if not domain.startswith("http"):
                domain = "https://" + domain
            await open_browser(domain, browser)
            return f"Opened {word}, sir."

    # 3. Fall back to Google search with cleaned query
    query = target
    for prefix in ["search for", "look up", "google", "find me", "pull up", "open chrome",
                    "open firefox", "open browser", "go to", "can you", "in the browser",
                    "can you go to", "please"]:
        query = query.lower().replace(prefix, "").strip()
    # Remove filler words
    query = re.sub(r'\b(can|you|the|in|to|a|an|for|me|my|please)\b', '', query).strip()
    query = re.sub(r'\s+', ' ', query).strip()

    if not query:
        query = target

    url = f"https://www.google.com/search?q={quote(query)}"
    await open_browser(url, browser)
    return "Searching for that, sir."


async def handle_research(text: str, target: str, client: Any) -> str:
    """Deep research with Opus — write results to HTML, open in browser."""
    try:
        research_response = await client.messages.create(
            model=JARVIS_RESEARCH_MODEL,
            max_tokens=2000,
            system=f"You are JARVIS, researching a topic for {get_user_name()}. Be thorough, organized, and cite sources where possible.",
            messages=[{"role": "user", "content": f"Research this thoroughly:\n\n{target}"}],
        )
        research_text = research_response.content[0].text

        import html as _html
        html_content = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>JARVIS Research: {_html.escape(target[:60])}</title>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; background: #0a0a0a; color: #e0e0e0; line-height: 1.7; }}
h1 {{ color: #0ea5e9; font-size: 1.4em; border-bottom: 1px solid #222; padding-bottom: 10px; }}
h2 {{ color: #38bdf8; font-size: 1.1em; margin-top: 24px; }}
a {{ color: #0ea5e9; }}
pre {{ background: #111; padding: 12px; border-radius: 6px; overflow-x: auto; }}
code {{ background: #111; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }}
blockquote {{ border-left: 3px solid #0ea5e9; margin-left: 0; padding-left: 16px; color: #aaa; }}
</style>
</head><body>
<h1>Research: {_html.escape(target[:80])}</h1>
<div>{research_text.replace(chr(10), '<br>')}</div>
<hr style="border-color:#222;margin-top:40px">
<p style="color:#555;font-size:0.8em">Researched by JARVIS using Claude Opus &bull; {datetime.now().strftime('%B %d, %Y %I:%M %p')}</p>
</body></html>"""

        results_file = Path.home() / "Desktop" / ".jarvis_research.html"
        results_file.write_text(html_content)

        browser_name = "firefox" if "firefox" in text.lower() else "chrome"
        await open_browser(f"file://{results_file}", browser_name)

        # Short voice summary via Haiku
        summary = await client.messages.create(
            model=JARVIS_FAST_MODEL,
            max_tokens=80,
            system="Summarize this research in ONE sentence for voice. No markdown.",
            messages=[{"role": "user", "content": research_text[:2000]}],
        )
        return summary.content[0].text + " Full results are in your browser, sir."

    except Exception as e:
        log.error(f"Research failed: {e}")
        from urllib.parse import quote
        await open_browser(f"https://www.google.com/search?q={quote(target)}")
        return "Pulled up a search for that, sir."


# -- Session Summary (Three-Tier Memory) -----------------------------------

async def _update_session_summary(
    old_summary: str,
    rotated_messages: list[dict],
    client: Any,
) -> str:
    """Background Haiku call to update the rolling session summary."""
    prompt = f"""Update this conversation summary to include the new messages.

Current summary: {old_summary or '(start of conversation)'}

New messages to incorporate:
{chr(10).join(f'{m["role"]}: {m["content"][:200]}' for m in rotated_messages)}

Write an updated summary in 2-4 sentences capturing the key topics, decisions, and context. Be concise."""

    try:
        response = await client.messages.create(
            model=JARVIS_FAST_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        log.warning(f"Summary update failed: {e}")
        return old_summary  # Keep old summary on failure


async def _polish_first_turn_briefing(raw_briefing: str, client: Any | None) -> str:
    """Rewrite the first-turn briefing into the active spoken language for TTS."""
    if not raw_briefing.strip() or not client:
        return raw_briefing.strip()

    if _is_indonesian_mode():
        prompt = (
            "Ubah catatan briefing ini menjadi tepat lima kalimat pendek dalam bahasa Indonesia lisan yang santai untuk TTS JARVIS. "
            "Kalimat 1 harus membahas cuaca Cimahi saat ini. Kalimat 2 harus membahas prakiraan besok. "
            "Kalimat 3 harus membahas headline Cimahi hari ini. Kalimat 4 harus membahas headline Indonesia hari ini. "
            "Kalimat 5 harus membahas headline dunia hari ini dengan prioritas Iran-Amerika bila ada. "
            "Terjemahkan frasa berbahasa Inggris menjadi bahasa Indonesia yang natural bila perlu, tetapi jangan mengubah fakta, nama, atau angka. "
            "Tanpa markdown, tanpa bullet, tanpa daftar."
        )
    else:
        prompt = (
            "Rewrite these briefing notes into exactly five short sentences of relaxed spoken English for JARVIS TTS. "
            "Sentence 1 should cover current weather in Cimahi. Sentence 2 should cover tomorrow's forecast. "
            "Sentence 3 should cover today's Cimahi headlines. Sentence 4 should cover today's Indonesia headlines. "
            "Sentence 5 should cover today's world headlines, prioritizing Iran-America developments if present. "
            "Translate Indonesian phrasing and headlines into English when possible, keep names and facts accurate, and do not invent anything. "
            "No markdown, no bullet points, no lists."
        )

    try:
        response = await client.messages.create(
            model=JARVIS_FAST_MODEL,
            max_tokens=220,
            system=prompt,
            messages=[{"role": "user", "content": raw_briefing}],
        )
        polished = response.content[0].text.strip()
        return polished or raw_briefing.strip()
    except Exception as e:
        log.debug(f"Briefing polish failed: {e}")
        _mark_llm_unavailable(str(e))
        return raw_briefing.strip()


async def _get_spoken_briefing_text() -> str:
    """Return the cached spoken briefing, generating it on demand if needed."""
    spoken = (_ctx_cache.get("briefing_spoken") or "").strip()
    raw = (_ctx_cache.get("briefing") or "").strip()
    active_language = get_user_language()

    if (
        spoken
        and raw
        and _ctx_cache.get("briefing_spoken_source") == raw
        and _ctx_cache.get("briefing_spoken_language") == active_language
    ):
        return spoken

    if not raw:
        try:
            loop = asyncio.get_event_loop()
            raw = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    _build_first_turn_briefing_sync,
                    get_user_location(),
                    get_user_country(),
                    get_user_timezone(),
                ),
                timeout=10,
            )
            _ctx_cache["briefing"] = raw
        except Exception as e:
            log.debug(f"Briefing build failed: {e}")
            raw = ""

    if not raw:
        return _localized_text(
            "Your local briefing is not ready just yet, sir.",
            f"Ringkasan lokalmu belum siap sepenuhnya, {get_honorific()}.",
        )

    polished = await _polish_first_turn_briefing(raw, anthropic_client)
    polished = polished.strip() or raw
    _ctx_cache["briefing_spoken"] = polished
    _ctx_cache["briefing_spoken_source"] = raw
    _ctx_cache["briefing_spoken_language"] = active_language
    return polished


async def _warm_spoken_briefing_cache():
    """Warm the spoken briefing cache in the background without blocking the user."""
    try:
        await _get_spoken_briefing_text()
    except Exception as e:
        log.debug(f"Spoken briefing warm-up failed: {e}")


# -- WebSocket Voice Handler -----------------------------------------------

@app.websocket("/ws/voice")
async def voice_handler(ws: WebSocket):
    """
    WebSocket protocol:

    Client -> Server:
        {"type": "transcript", "text": "...", "isFinal": true}

    Server -> Client:
        {"type": "audio", "data": "<base64 mp3>", "text": "spoken text"}
        {"type": "status", "state": "thinking"|"speaking"|"idle"|"working"|"uncertain"}
        {"type": "task_spawned", "task_id": "...", "prompt": "..."}
        {"type": "task_complete", "task_id": "...", "summary": "..."}
    """
    await ws.accept()
    task_manager.register_websocket(ws)
    history: list[dict] = []
    work_session = WorkSession()
    planner = TaskPlanner()

    # Response cancellation — when new input arrives, cancel current response
    _current_response_id = 0
    _cancel_response = False

    # Audio collision prevention — track when user last spoke
    voice_state = {"last_user_time": 0.0}

    # Self-awareness — track last spoken response to avoid repetition
    last_jarvis_response = ""

    # Three-tier conversation memory
    session_buffer: list[dict] = []  # ALL messages, never truncated
    session_summary: str = ""  # Rolling summary of older conversation
    summary_update_pending: bool = False
    messages_since_last_summary: int = 0
    first_turn_offer_pending: bool = True
    pending_folder_request: dict | None = None
    last_final_transcript: str = ""
    last_final_transcript_at: float = 0.0
    last_wiz_context_at: float = 0.0
    last_air_fan_context_at: float = 0.0

    log.info("Voice WebSocket connected")

    try:
        # ── Greeting — always start in conversation mode ──
        now = datetime.now()
        hour = now.hour
        if hour < 12:
            greeting = _localized_text("Good morning, sir.", f"Selamat pagi, {get_honorific()}.")
        elif hour < 17:
            greeting = _localized_text("Good afternoon, sir.", f"Selamat siang, {get_honorific()}.")
        else:
            greeting = _localized_text("Good evening, sir.", f"Selamat malam, {get_honorific()}.")

        global _last_greeting_time
        should_greet = (time.time() - _last_greeting_time) > 60

        if should_greet:
            _last_greeting_time = time.time()

            async def _send_greeting():
                try:
                    audio_bytes = await synthesize_speech(greeting)
                    if audio_bytes:
                        encoded = base64.b64encode(audio_bytes).decode()
                        await ws.send_json({"type": "status", "state": "speaking"})
                        await ws.send_json({"type": "audio", "data": encoded, "text": greeting})
                        history.append({"role": "assistant", "content": greeting})
                        log.info(f"JARVIS: {greeting}")
                        await ws.send_json({"type": "status", "state": "idle"})
                except Exception as e:
                    log.warning(f"Greeting failed: {e}")

            asyncio.create_task(_send_greeting())

        asyncio.create_task(_warm_spoken_briefing_cache())

        try:
            await ws.send_json({"type": "status", "state": "idle"})
        except Exception:
            return  # WebSocket already gone

        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # ── Feedback: user confirms a correct command ──
            if msg.get("type") == "feedback_confirm":
                cmd = str(msg.get("command", "")).strip()
                res = str(msg.get("response", "")).strip()
                if cmd:
                    remember(
                        f"[CONFIRMED CORRECT] User said: '{cmd}' → JARVIS responded: '{res}'",
                        mem_type="confirmed",
                        importance=8,
                    )
                    _save_feedback_log("confirm", cmd, res, "")
                    log.info(f"Feedback confirmed: {cmd!r}")
                continue

            # ── Feedback: user teaches JARVIS what it got wrong ──
            if msg.get("type") == "feedback_correction":
                cmd        = str(msg.get("command",    "")).strip()
                res        = str(msg.get("response",   "")).strip()
                correction = str(msg.get("correction", "")).strip()
                if cmd and correction:
                    remember(
                        f"[CORRECTION] When user says '{cmd}', JARVIS said '{res}' — "
                        f"but should have: {correction}",
                        mem_type="correction",
                        importance=9,
                    )
                    _save_feedback_log("correction", cmd, res, correction)
                    log.info(f"Feedback correction: {cmd!r} → {correction!r}")
                    # Brief spoken acknowledgement
                    ack = _localized_text(
                        "Correction noted, sir. I'll improve.",
                        "Koreksi dicatat, aku akan memperbaikinya.",
                    )
                    try:
                        await ws.send_json({"type": "status", "state": "speaking"})
                        audio = await synthesize_speech(ack)
                        if audio:
                            await ws.send_json({"type": "audio", "data": audio, "text": ack})
                        await ws.send_json({"type": "status", "state": "idle"})
                    except Exception as e:
                        log.warning(f"Feedback ack TTS failed: {e}")
                continue

            # ── Fix-self: activate work mode in JARVIS repo ──
            if msg.get("type") == "fix_self":
                jarvis_dir = str(Path(__file__).parent)
                await work_session.start(jarvis_dir)
                response_text = "Work mode active in my own repo, sir. Tell me what needs fixing."
                tts = strip_markdown_for_tts(response_text)
                await ws.send_json({"type": "status", "state": "speaking"})
                audio = await synthesize_speech(tts)
                if audio:
                    await ws.send_json({"type": "audio", "data": audio, "text": response_text})
                else:
                    await ws.send_json({"type": "text", "text": response_text})
                continue

            if msg.get("type") != "transcript" or not msg.get("isFinal"):
                continue

            user_text = apply_speech_corrections(msg.get("text", "").strip())
            if not user_text:
                continue

            normalized_final_transcript = re.sub(r"\s+", " ", user_text.strip().lower())
            now_ts = time.time()
            if (
                normalized_final_transcript
                and normalized_final_transcript == last_final_transcript
                and (now_ts - last_final_transcript_at) < 1.75
            ):
                log.info(f"Ignoring duplicate final transcript: {user_text}")
                continue
            last_final_transcript = normalized_final_transcript
            last_final_transcript_at = now_ts

            # Cancel any in-flight response
            _current_response_id += 1
            my_response_id = _current_response_id
            _cancel_response = True
            await asyncio.sleep(0.05)  # Let any pending sends notice the cancellation
            _cancel_response = False

            voice_state["last_user_time"] = time.time()
            log.info(f"User: {user_text}")
            await ws.send_json({"type": "status", "state": "thinking"})

            first_turn_offer = ""
            if first_turn_offer_pending:
                first_turn_offer_pending = False
                first_turn_offer = _localized_text(
                    "Your local briefing is ready whenever you'd like it, sir.",
                    f"Ringkasan lokalmu sudah siap kapan pun kamu ingin mendengarnya, {get_honorific()}.",
                )

            # Lazy project scan on first message
            global cached_projects
            if not cached_projects:
                try:
                    # Run in executor since scan_projects does sync file I/O
                    loop = asyncio.get_event_loop()
                    cached_projects = await asyncio.wait_for(
                        loop.run_in_executor(None, _scan_projects_sync),
                        timeout=3
                    )
                    log.info(f"Scanned {len(cached_projects)} projects")
                except Exception:
                    cached_projects = []

            try:
                # ── CHECK FOR MODE SWITCHES ──
                t_lower = user_text.lower()

                if pending_folder_request:
                    if any(p in t_lower for p in ["cancel that", "never mind", "batal", "cancel folder", "stop that"]):
                        pending_folder_request = None
                        response_text = "Cancelled the folder request, sir."
                    else:
                        pending_folder_request = merge_folder_request_details(pending_folder_request, user_text)
                        if folder_request_is_complete(pending_folder_request):
                            response_text = await handle_create_folder(pending_folder_request)
                            pending_folder_request = None
                        else:
                            response_text = describe_missing_folder_details(pending_folder_request)

                # ── PLANNING MODE: answering clarifying questions ──
                elif planner.is_planning:
                    # Check for bypass
                    if any(p in t_lower for p in BYPASS_PHRASES):
                        plan = planner.active_plan
                        if plan:
                            plan.skipped = True
                            for q in plan.pending_questions[plan.current_question_index:]:
                                if q.get("default") is not None and q["key"] not in plan.answers:
                                    plan.answers[q["key"]] = q["default"]
                        prompt = await planner.build_prompt()
                        name = _generate_project_name(prompt)
                        path = str(Path.home() / "Desktop" / name)
                        os.makedirs(path, exist_ok=True)
                        Path(path, "CLAUDE.md").write_text(prompt)
                        did = dispatch_registry.register(name, path, prompt[:200])
                        asyncio.create_task(_execute_prompt_project(name, prompt, work_session, ws, dispatch_id=did, history=history, voice_state=voice_state))
                        planner.reset()
                        response_text = "Building it now, sir."
                    elif planner.active_plan and planner.active_plan.confirmed is False and planner.active_plan.current_question_index >= len(planner.active_plan.pending_questions):
                        # Confirmation phase
                        result = await planner.handle_confirmation(user_text)
                        if result["confirmed"]:
                            prompt = await planner.build_prompt()
                            name = _generate_project_name(prompt)
                            path = str(Path.home() / "Desktop" / name)
                            os.makedirs(path, exist_ok=True)
                            Path(path, "CLAUDE.md").write_text(prompt)
                            did = dispatch_registry.register(name, path, prompt[:200])
                            asyncio.create_task(_execute_prompt_project(name, prompt, work_session, ws, dispatch_id=did, history=history, voice_state=voice_state))
                            planner.reset()
                            response_text = "On it, sir."
                        elif result["cancelled"]:
                            planner.reset()
                            response_text = "Cancelled, sir."
                        else:
                            response_text = result.get("modification_question", "How shall I adjust the plan, sir?")
                    else:
                        result = await planner.process_answer(user_text, cached_projects)
                        if result["plan_complete"]:
                            response_text = result.get("confirmation_summary", "Ready to build. Shall I proceed, sir?")
                        else:
                            response_text = result.get("next_question", "What else, sir?")

                elif any(w in t_lower for w in ["quit work mode", "exit work mode", "go back to chat", "regular mode", "stop working"]):
                    if work_session.active:
                        await work_session.stop()
                        response_text = "Back to conversation mode, sir."
                    else:
                        response_text = "Already in conversation mode, sir."

                # ── WORK MODE: speech → claude -p → Haiku summary → JARVIS voice ──
                elif work_session.active:
                    if is_casual_question(user_text):
                        # Quick chat — bypass claude -p, use Haiku
                        response_text = await generate_chat_response(
                            user_text, task_manager,
                            cached_projects, history,
                            last_response=last_jarvis_response,
                            session_summary=session_summary,
                        )
                    else:
                        # Send to claude -p (full power)
                        await ws.send_json({"type": "status", "state": "working"})
                        log.info(f"Work mode → claude -p: {user_text[:80]}")

                        full_response = await work_session.send(user_text)

                        # Detect if Claude Code is stalling (asking questions instead of building)
                        if full_response and anthropic_client:
                            stall_words = ["which option", "would you prefer", "would you like me to",
                                           "before I proceed", "before proceeding", "should I",
                                           "do you want me to", "let me know", "please confirm",
                                           "which approach", "what would you"]
                            is_stalling = any(w in full_response.lower() for w in stall_words)
                            if is_stalling and work_session._message_count >= 2:
                                # Claude Code keeps asking — push it to build
                                log.info("Claude Code stalling — pushing to build")
                                push_response = await work_session.send(
                                    "Stop asking questions. Use your best judgment and start building now. "
                                    "Write the actual code files. Go with the simplest reasonable approach."
                                )
                                if push_response:
                                    full_response = push_response

                        # Auto-open any localhost URLs Claude Code mentions
                        import re as _re
                        localhost_match = _re.search(r'https?://localhost:\d+', full_response or "")
                        if localhost_match:
                            asyncio.create_task(_execute_browse(localhost_match.group(0)))
                            log.info(f"Auto-opening {localhost_match.group(0)}")

                        # Always summarize work mode responses via Haiku
                        if full_response and anthropic_client:
                            try:
                                summary = await anthropic_client.messages.create(
                                    model=JARVIS_FAST_MODEL,
                                    max_tokens=100,
                                    system=(
                                        f"You are JARVIS reporting to the user ({get_user_name()}). Summarize what happened in 1-2 sentences. "
                                        "Speak in first person — 'I built', 'I found', 'I set up'. "
                                        "You are talking TO THE USER, not to a coding tool. "
                                        "NEVER give instructions like 'go ahead and build' or 'set up the frontend' — those are NOT for the user. "
                                        "NEVER say 'Claude Code'. NEVER output [ACTION:...] tags. "
                                        "NEVER read out URLs. No markdown. British precision."
                                    ),
                                    messages=[{"role": "user", "content": f"Claude Code said:\n{full_response[:2000]}"}],
                                )
                                response_text = summary.content[0].text
                            except Exception:
                                response_text = full_response[:200]
                        else:
                            response_text = full_response

                # ── CHAT MODE: fast keyword detection + Haiku ──
                else:
                    action = detect_action_fast(user_text)
                    if not action and (time.time() - last_air_fan_context_at) <= _AIR_FAN_CONTEXT_WINDOW_SECONDS:
                        contextual_air_fan = _extract_contextual_air_fan_followup(user_text)
                        if contextual_air_fan:
                            action = {"action": "fan", "target": contextual_air_fan}
                            log.info(f"Air fan follow-up context recovered action: {contextual_air_fan}")
                    if not action and (time.time() - last_wiz_context_at) <= _WIZ_CONTEXT_WINDOW_SECONDS:
                        contextual_wiz = _extract_contextual_wiz_followup(user_text)
                        if contextual_wiz:
                            action = {"action": "wiz", "target": contextual_wiz}
                            log.info(f"WiZ follow-up context recovered action: {contextual_wiz}")

                    if action:
                        if action["action"] == "acknowledge_voice":
                            response_text = await handle_acknowledge_voice()
                        elif action["action"] == "read_briefing":
                            response_text = await handle_read_briefing()
                        elif action["action"] == "ask_codex":
                            response_text = await handle_ask_codex(action["target"])
                        elif action["action"] == "create_folder":
                            folder_request = action["target"]
                            if folder_request_is_complete(folder_request):
                                response_text = await handle_create_folder(folder_request)
                            else:
                                pending_folder_request = folder_request
                                response_text = describe_missing_folder_details(folder_request)
                        elif action["action"] == "correction":
                            # Store the correction in memory and let LLM formulate a response
                            raw_correction = action["target"].strip()
                            remember(
                                f"User correction: {raw_correction}",
                                mem_type="correction",
                                importance=9,
                            )
                            # Let LLM handle the spoken reply — pass to generate_response
                            if anthropic_client:
                                response_text = await generate_response(
                                    raw_correction, anthropic_client, task_manager,
                                    cached_projects, history,
                                    last_response=last_jarvis_response,
                                    session_summary=session_summary,
                                )
                            else:
                                response_text = _localized_text(
                                    "Noted, sir. I'll remember that.",
                                    f"Baik, aku catat itu.",
                                )
                        elif action["action"] == "fan":
                            response_text = await handle_fan(action["target"])
                            last_air_fan_context_at = time.time()
                            await ws.send_json({"type": "action_taken", "description": _describe_air_fan_cmd(action["target"])})
                        elif action["action"] == "wiz":
                            response_text = await handle_wiz(action["target"])
                            last_wiz_context_at = time.time()
                            await ws.send_json({"type": "action_taken", "description": _describe_wiz_cmd(action["target"])})
                        elif action["action"] == "spotify_control":
                            response_text = await handle_spotify_control(action["target"])
                            await ws.send_json({"type": "action_taken", "description": _describe_action("spotify_control", action["target"])})
                        elif action["action"] == "open_terminal":
                            response_text = await handle_open_terminal()
                            await ws.send_json({"type": "action_taken", "description": "Buka: Terminal"})
                        elif action["action"] == "open_app":
                            response_text = await handle_open_app(action["target"])
                            await ws.send_json({"type": "action_taken", "description": _describe_action("open_app", action["target"])})
                        elif action["action"] == "play_spotify":
                            response_text = await handle_play_spotify(action["target"])
                        elif action["action"] == "show_recent":
                            response_text = await handle_show_recent()
                        elif action["action"] == "describe_screen":
                            response_text = "Taking a look now, sir."
                            asyncio.create_task(_lookup_and_report("screen", _do_screen_lookup, ws, history=history, voice_state=voice_state))
                        elif action["action"] == "check_calendar":
                            response_text = "Checking your calendar now, sir."
                            asyncio.create_task(_lookup_and_report("calendar", _do_calendar_lookup, ws, history=history, voice_state=voice_state))
                        elif action["action"] == "check_mail":
                            response_text = "Checking your inbox now, sir."
                            asyncio.create_task(_lookup_and_report("mail", _do_mail_lookup, ws, history=history, voice_state=voice_state))
                        elif action["action"] == "check_dispatch":
                            recent = dispatch_registry.get_most_recent()
                            if not recent:
                                response_text = "No recent builds on record, sir."
                            else:
                                name = recent["project_name"]
                                status = recent["status"]
                                if status == "building" or status == "pending":
                                    elapsed = int(time.time() - recent["updated_at"])
                                    response_text = f"Still working on {name}, sir. Been at it for {elapsed} seconds."
                                elif status == "completed":
                                    response_text = recent.get("summary") or f"{name} is complete, sir."
                                elif status in ("failed", "timeout"):
                                    response_text = f"{name} ran into problems, sir."
                                else:
                                    response_text = f"{name} is {status}, sir."
                        elif action["action"] == "check_tasks":
                            tasks = get_open_tasks()
                            response_text = format_tasks_for_voice(tasks)
                        elif action["action"] == "check_usage":
                            response_text = get_usage_summary()
                        else:
                            response_text = "Understood, sir."
                    else:
                        response_text = await generate_chat_response(
                            user_text,
                            task_manager,
                            cached_projects,
                            history,
                            last_response=last_jarvis_response,
                            session_summary=session_summary,
                        )

                        # Check for action tags embedded in LLM response
                        clean_response, embedded_action = extract_action(response_text)
                        if embedded_action:
                            log.info(f"LLM embedded action: {embedded_action}")
                            response_text = clean_response
                            # Ensure there's always something to speak
                            if not response_text.strip():
                                action_type = embedded_action["action"]
                                if action_type == "prompt_project":
                                    proj = embedded_action["target"].split("|||")[0].strip()
                                    response_text = f"Connecting to {proj} now, sir."
                                elif action_type == "build":
                                    response_text = "On it, sir."
                                elif action_type == "open_app":
                                    response_text = "Opening that now, sir."
                                elif action_type == "play_spotify":
                                    response_text = "Playing that now, sir."
                                elif action_type == "fan":
                                    response_text = "Adjusting the fan now, sir."
                                elif action_type == "research":
                                    response_text = "Looking into that now, sir."
                                else:
                                    response_text = "Right away, sir."

                            if embedded_action["action"] == "build":
                                # Build in background — JARVIS stays conversational
                                target = embedded_action["target"]
                                name = _generate_project_name(target)
                                path = str(Path.home() / "Desktop" / name)
                                os.makedirs(path, exist_ok=True)

                                # Write detailed CLAUDE.md
                                Path(path, "CLAUDE.md").write_text(
                                    f"# Task\n\n{target}\n\n"
                                    "## Instructions\n"
                                    "- BUILD THIS NOW. Do not ask clarifying questions.\n"
                                    "- Use your best judgment for any design/architecture decisions.\n"
                                    "- Write complete, working code files — not plans or specs.\n"
                                    "- If it's a web app: use React + Vite + Tailwind unless specified otherwise.\n"
                                    "- Make it look polished and professional. Modern UI, clean layout.\n"
                                    "- Ensure it runs with a single command (npm run dev or similar).\n"
                                    "- If you reference a real product's UI (e.g. 'Zillow clone'), match their actual layout and features closely.\n"
                                    "- Use realistic mock data, not placeholder Lorem Ipsum.\n"
                                    "- After building, start the dev server and verify the app loads without errors.\n"
                                    "- IMPORTANT: Your LAST line of output MUST be exactly: RUNNING_AT=http://localhost:PORT (the actual port the dev server is using)\n"
                                )

                                # Register and dispatch
                                did = dispatch_registry.register(name, path, target)
                                asyncio.create_task(
                                    _execute_prompt_project(name, target, work_session, ws, dispatch_id=did, history=history, voice_state=voice_state)
                                )
                            elif embedded_action["action"] == "browse":
                                asyncio.create_task(_execute_browse(embedded_action["target"]))
                            elif embedded_action["action"] == "open_app":
                                asyncio.create_task(_execute_open_app(embedded_action["target"]))
                            elif embedded_action["action"] == "play_spotify":
                                asyncio.create_task(_execute_play_spotify(embedded_action["target"]))
                            elif embedded_action["action"] == "research":
                                # Research enters work mode too
                                name = _generate_project_name(embedded_action["target"])
                                path = str(Path.home() / "Desktop" / name)
                                os.makedirs(path, exist_ok=True)
                                await work_session.start(path)
                                asyncio.create_task(
                                    self_work_and_notify(work_session, embedded_action["target"], ws)
                                )
                            elif embedded_action["action"] == "wiz":
                                # WiZ lamp control — force=True skips heuristic guard
                                # (LLM already confirmed intent via [ACTION:WIZ])
                                # IMPORTANT: await the result so the actual WiZ confirmation
                                # (e.g. "Ada 2 lampu yang online.") is spoken, not the LLM placeholder.
                                wiz_cmd = extract_wiz_request(embedded_action["target"], _force=True)
                                if wiz_cmd:
                                    last_wiz_context_at = time.time()
                                    response_text = await handle_wiz(wiz_cmd)
                                    await ws.send_json({"type": "action_taken", "description": _describe_wiz_cmd(wiz_cmd)})
                                else:
                                    # Fallback: try treating the whole target as a power/scene command
                                    _fallback = {"kind": "power", "state": "on"}
                                    last_wiz_context_at = time.time()
                                    response_text = await handle_wiz(_fallback)
                                    await ws.send_json({"type": "action_taken", "description": "Lampu: nyala"})
                            elif embedded_action["action"] == "fan":
                                fan_cmd = extract_air_fan_request(embedded_action["target"], _force=True)
                                if fan_cmd:
                                    last_air_fan_context_at = time.time()
                                    response_text = await handle_fan(fan_cmd)
                                    await ws.send_json({"type": "action_taken", "description": _describe_air_fan_cmd(fan_cmd)})
                                else:
                                    _fallback = {"kind": "status"}
                                    last_air_fan_context_at = time.time()
                                    response_text = await handle_fan(_fallback)
                                    await ws.send_json({"type": "action_taken", "description": "Fan: status"})
                            elif embedded_action["action"] == "open_terminal":
                                asyncio.create_task(_execute_open_terminal())
                            elif embedded_action["action"] == "prompt_project":
                                target = embedded_action["target"]
                                if "|||" in target:
                                    proj_name, _, prompt = target.partition("|||")
                                    proj_name = proj_name.strip()
                                    prompt = prompt.strip()
                                    # Check for recent completed dispatch before re-dispatching
                                    recent = dispatch_registry.get_recent_for_project(proj_name)
                                    if recent and recent.get("summary"):
                                        log.info(f"Using recent dispatch result for {proj_name} instead of re-dispatching")
                                        response_text = recent["summary"]
                                        history.append({"role": "assistant", "content": f"[Previous dispatch result for {proj_name}]: {recent['summary']}"})
                                    else:
                                        asyncio.create_task(
                                            _execute_prompt_project(proj_name, prompt, work_session, ws, history=history, voice_state=voice_state)
                                        )
                                else:
                                    log.warning(f"PROMPT_PROJECT missing ||| delimiter: {target}")
                            elif embedded_action["action"] == "add_task":
                                target = embedded_action["target"]
                                parts = target.split("|||")
                                if len(parts) >= 2:
                                    priority = parts[0].strip() or "medium"
                                    title = parts[1].strip()
                                    desc = parts[2].strip() if len(parts) > 2 else ""
                                    due = parts[3].strip() if len(parts) > 3 else ""
                                    create_task(title=title, description=desc, priority=priority, due_date=due)
                                    log.info(f"Task created: {title}")
                            elif embedded_action["action"] == "add_note":
                                target = embedded_action["target"]
                                if "|||" in target:
                                    topic, _, content = target.partition("|||")
                                    create_note(content=content.strip(), topic=topic.strip())
                                else:
                                    create_note(content=target)
                                log.info("Note created")
                            elif embedded_action["action"] == "complete_task":
                                try:
                                    task_id = int(embedded_action["target"].strip())
                                    complete_task(task_id)
                                    log.info(f"Task {task_id} completed")
                                except ValueError:
                                    pass
                            elif embedded_action["action"] == "remember":
                                remember(embedded_action["target"].strip(), mem_type="fact", importance=7)
                                log.info(f"Memory stored: {embedded_action['target'][:60]}")
                            elif embedded_action["action"] == "create_note":
                                target = embedded_action["target"]
                                if "|||" in target:
                                    title, _, body = target.partition("|||")
                                    asyncio.create_task(create_apple_note(title.strip(), body.strip()))
                                    log.info(f"Apple Note created: {title.strip()}")
                                else:
                                    asyncio.create_task(create_apple_note("JARVIS Note", target))
                            elif embedded_action["action"] == "screen":
                                asyncio.create_task(_lookup_and_report("screen", _do_screen_lookup, ws, history=history, voice_state=voice_state))
                            elif embedded_action["action"] == "read_note":
                                # Read note in background and report back
                                async def _read_and_report(search_term, _ws):
                                    note = await read_note(search_term)
                                    if note:
                                        msg = f"Sir, your note '{note['title']}' says: {note['body'][:200]}"
                                    else:
                                        msg = f"Couldn't find a note matching '{search_term}', sir."
                                    audio = await synthesize_speech(strip_markdown_for_tts(msg))
                                    if audio and _ws:
                                        try:
                                            await _ws.send_json({"type": "status", "state": "speaking"})
                                            await _ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": msg})
                                        except Exception:
                                            pass
                                asyncio.create_task(_read_and_report(embedded_action["target"].strip(), ws))

                if first_turn_offer and "briefing" not in response_text.lower():
                    response_text = f"{response_text} {first_turn_offer}".strip()

                # Update history
                history.append({"role": "user", "content": user_text})
                history.append({"role": "assistant", "content": response_text})

                # Three-tier memory: also track in session buffer
                session_buffer.append({"role": "user", "content": user_text})
                session_buffer.append({"role": "assistant", "content": response_text})

                # Check if rolling summary needs updating
                messages_since_last_summary += 1
                if messages_since_last_summary >= 5 and len(history) > 20 and not summary_update_pending:
                    summary_update_pending = True
                    messages_since_last_summary = 0
                    # Get messages that are about to be rotated out
                    rotated = history[:-20] if len(history) > 20 else []
                    if rotated and anthropic_client:
                        async def _do_summary():
                            nonlocal session_summary, summary_update_pending
                            session_summary = await _update_session_summary(
                                session_summary, rotated, anthropic_client
                            )
                            summary_update_pending = False
                        asyncio.create_task(_do_summary())
                    else:
                        summary_update_pending = False

                # Extract memories in background (doesn't block response)
                if anthropic_client and len(user_text) > 15:
                    asyncio.create_task(extract_memories(user_text, response_text, anthropic_client))

                # TTS
                response_state = "uncertain" if should_show_uncertainty(response_text) else "speaking"
                tts = strip_markdown_for_tts(response_text)
                await ws.send_json({"type": "status", "state": response_state})
                audio = await synthesize_speech(tts)
                if audio:
                    await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": response_text})
                else:
                    await ws.send_json({"type": "text", "text": response_text})
                    await ws.send_json({"type": "status", "state": "idle"})
                log.info(f"JARVIS: {response_text}")
                last_jarvis_response = response_text

            except Exception as e:
                log.error(f"Error: {e}", exc_info=True)
                try:
                    fallback = "Something went wrong, sir."
                    audio = await synthesize_speech(fallback)
                    if audio:
                        await ws.send_json({"type": "audio", "data": base64.b64encode(audio).decode(), "text": fallback})
                    else:
                        await ws.send_json({"type": "audio", "data": "", "text": fallback})
                    # Let client's audioPlayer.onFinished handle idle transition
                except Exception:
                    pass

    except WebSocketDisconnect:
        log.info("Voice WebSocket disconnected")
    except Exception as e:
        log.error(f"WebSocket error: {e}", exc_info=True)
    finally:
        task_manager.unregister_websocket(ws)


# ---------------------------------------------------------------------------
# Settings / Configuration endpoints
# ---------------------------------------------------------------------------

def _env_file_path() -> Path:
    return Path(__file__).parent / ".env"

def _env_example_path() -> Path:
    return Path(__file__).parent / ".env.example"

def _read_env() -> tuple[list[str], dict[str, str]]:
    """Read .env file. Returns (raw_lines, parsed_dict). Creates from .env.example if missing."""
    path = _env_file_path()
    if not path.exists():
        example = _env_example_path()
        if example.exists():
            import shutil as _shutil
            _shutil.copy2(str(example), str(path))
        else:
            path.write_text("")
    lines = path.read_text().splitlines()
    parsed: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _, v = stripped.partition("=")
            parsed[k.strip()] = v.strip().strip('"').strip("'")
    return lines, parsed

def _write_env_key(key: str, value: str) -> None:
    """Update a single key in .env, preserving comments and order."""
    lines, _ = _read_env()
    found = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _, _ = stripped.partition("=")
            if k.strip() == key:
                new_lines.append(f"{key}={value}")
                found = True
                continue
        new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    _env_file_path().write_text("\n".join(new_lines) + "\n")
    os.environ[key] = value

class KeyUpdate(BaseModel):
    key_name: str
    key_value: str

class KeyTest(BaseModel):
    key_value: str | None = None

class NvidiaLLMConfigTest(BaseModel):
    key_value: str | None = None
    base_url: str | None = None
    model: str | None = None

class LocalTTSConfigTest(BaseModel):
    base_url: str | None = None
    engine: str | None = None
    model_path: str | None = None
    voice: str | None = None
    speed: float | None = None
    language: str | None = None

class AnnouncementRequest(BaseModel):
    text: str
    source: str = "external"

class PreferencesUpdate(BaseModel):
    user_name: str = ""
    honorific: str = "sir"
    calendar_accounts: str = "auto"
    user_location: str = "Cimahi"
    user_country: str = "Indonesia"
    user_language: str = "en"
    local_fallback_mode: bool = True
    chat_box_enabled: bool = False
    llm_provider: str = "anthropic"
    local_llm_base_url: str = DEFAULT_LOCAL_LLM_BASE_URL
    local_llm_model: str = ""
    nvidia_llm_base_url: str = DEFAULT_NVIDIA_LLM_BASE_URL
    nvidia_llm_model: str = DEFAULT_NVIDIA_LLM_MODEL
    tts_provider: str = DEFAULT_TTS_PROVIDER
    local_tts_engine: str = DEFAULT_LOCAL_TTS_ENGINE
    local_tts_url: str = DEFAULT_LOCAL_TTS_BASE_URL
    local_tts_model_path: str = DEFAULT_VOXCPM_MODEL_PATH
    local_tts_voice: str = DEFAULT_LOCAL_TTS_VOICE
    local_tts_voice_id: str = DEFAULT_LOCAL_TTS_VOICE_ID
    local_tts_speed: float = DEFAULT_LOCAL_TTS_SPEED

@app.post("/api/settings/keys")
async def api_settings_keys(body: KeyUpdate):
    global ANTHROPIC_API_KEY, anthropic_client
    allowed = {
        "ANTHROPIC_API_KEY",
        "FISH_API_KEY",
        "FISH_VOICE_ID",
        "FISH_VOICE_ID_ID",
        "USER_NAME",
        "HONORIFIC",
        "CALENDAR_ACCOUNTS",
        "DEV_AGENT",
        "USER_LOCATION",
        "USER_COUNTRY",
        "USER_TIMEZONE",
        "USER_LANGUAGE",
        "NVIDIA_API_KEY",
    }
    if body.key_name not in allowed:
        return JSONResponse({"success": False, "error": "Invalid key name"}, status_code=400)
    _write_env_key(body.key_name, body.key_value)
    if body.key_name == "ANTHROPIC_API_KEY":
        ANTHROPIC_API_KEY = body.key_value.strip()
        anthropic_client = _refresh_llm_client()
    if body.key_name == "NVIDIA_API_KEY":
        anthropic_client = _refresh_llm_client()
    return {"success": True}

@app.post("/api/settings/test-anthropic")
async def api_test_anthropic(body: KeyTest):
    key = body.key_value or os.getenv("ANTHROPIC_API_KEY", "")
    using_active_key = not body.key_value or body.key_value == os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        if using_active_key:
            _mark_llm_unavailable("Anthropic API key not configured.")
        return {"valid": False, "error": "No key provided"}
    try:
        client = anthropic.AsyncAnthropic(api_key=key)
        await client.messages.create(model=JARVIS_FAST_MODEL, max_tokens=10, messages=[{"role": "user", "content": "Hi"}])
        if using_active_key:
            _mark_llm_available()
        return {"valid": True}
    except Exception as e:
        if using_active_key:
            _mark_llm_unavailable(str(e))
        return {"valid": False, "error": str(e)[:200]}


@app.post("/api/settings/test-nvidia-llm")
async def api_test_nvidia_llm(body: NvidiaLLMConfigTest):
    key = (body.key_value or get_nvidia_api_key()).strip()
    base_url = (body.base_url or get_nvidia_llm_base_url()).strip().rstrip("/") or DEFAULT_NVIDIA_LLM_BASE_URL
    model = (body.model or get_nvidia_llm_model()).strip() or DEFAULT_NVIDIA_LLM_MODEL
    using_active_config = (
        (not body.key_value or body.key_value == get_nvidia_api_key())
        and base_url == get_nvidia_llm_base_url()
        and model == get_nvidia_llm_model()
    )
    if not key:
        if using_active_config and get_llm_provider() == "nvidia":
            _mark_llm_unavailable("NVIDIA API key not configured.")
        return {"valid": False, "error": "No NVIDIA API key provided"}

    client = NvidiaLLMClient(base_url=base_url, model=model, api_key=key)
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=24,
            messages=[{"role": "user", "content": "Say hello in one short sentence."}],
        )
        if using_active_config and get_llm_provider() == "nvidia":
            _mark_llm_available()
        return {
            "valid": True,
            "provider": "nvidia",
            "model": model,
            "base_url": base_url,
            "preview": response.content[0].text.strip(),
        }
    except Exception as e:
        if using_active_config and get_llm_provider() == "nvidia":
            _mark_llm_unavailable(str(e))
        return {
            "valid": False,
            "provider": "nvidia",
            "model": model,
            "base_url": base_url,
            "error": _compact_error_text(str(e)),
        }


@app.post("/api/settings/test-local-llm")
async def api_test_local_llm():
    info = await _ensure_local_llm_server()
    ok, detail = info
    if not ok:
        return {
            "valid": False,
            "error": detail.get("error", "Local LLM server is unavailable"),
            "resolved_base_url": detail.get("resolved_base_url", ""),
            "tried": detail.get("tried", []),
        }

    models = detail.get("models") or []
    configured_model = get_local_llm_model().strip()
    chosen_model = configured_model
    if not chosen_model:
        for item in models:
            model_id = str(item.get("id", "")).strip()
            lowered = model_id.lower()
            if model_id and "embed" not in lowered and "embedding" not in lowered:
                chosen_model = model_id
                break

    if not chosen_model:
        return {
            "valid": False,
            "error": "Local server is reachable, but no chat model is loaded. Load a chat/instruct model in LM Studio first.",
            "models": models,
            "resolved_base_url": detail.get("resolved_base_url", ""),
        }

    client = LocalLLMClient(
        base_url=get_local_llm_base_url(),
        model=chosen_model,
        api_key=get_local_llm_api_key(),
    )
    try:
        response = await client.messages.create(
            model=chosen_model,
            max_tokens=20,
            messages=[{"role": "user", "content": "Say hello in one short sentence."}],
        )
        return {
            "valid": True,
            "model": chosen_model,
            "preview": response.content[0].text.strip(),
            "models": models,
            "resolved_base_url": getattr(client, "base_url", detail.get("resolved_base_url", "")),
        }
    except Exception as e:
        return {
            "valid": False,
            "error": _compact_error_text(str(e)),
            "models": models,
            "resolved_base_url": getattr(client, "base_url", detail.get("resolved_base_url", "")),
        }

@app.post("/api/settings/test-fish")
async def api_test_fish(body: KeyTest):
    key = body.key_value or _get_fish_api_key()
    if not key:
        return {"valid": False, "error": "No key provided"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.fish.audio/v1/tts",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "model": _get_fish_tts_backend(),
                },
                json={"text": "test", "reference_id": _get_fish_default_voice_id()},
            )
            if resp.status_code in (200, 201):
                return {"valid": True}
            elif resp.status_code == 401:
                return {"valid": False, "error": "Invalid API key"}
            else:
                return {"valid": False, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"valid": False, "error": str(e)[:200]}


@app.post("/api/settings/test-local-tts")
async def api_test_local_tts(body: LocalTTSConfigTest):
    engine = normalize_local_tts_engine(body.engine or get_local_tts_engine())
    base_url = (body.base_url or get_local_tts_base_url(engine)).strip() or get_default_local_tts_base_url(engine)
    if not re.match(r"^https?://", base_url, re.IGNORECASE):
        base_url = f"http://{base_url.lstrip('/')}"
    model_path = (body.model_path or get_local_tts_model_path()).strip() or DEFAULT_VOXCPM_MODEL_PATH

    language = "id" if (body.language or get_user_language()).lower().startswith("id") else "en"
    voice = (body.voice or get_local_tts_voice(language)).strip() or get_local_tts_voice(language)
    speed = body.speed if body.speed is not None else get_local_tts_speed()
    speed = min(2.0, max(0.5, float(speed)))

    ok, detail = await _ensure_local_tts_server(base_url, engine, model_path)
    if not ok:
        return {
            "valid": False,
            "engine": engine,
            "error": detail.get("error", "Local TTS server is unavailable"),
            "resolved_base_url": detail.get("resolved_base_url", base_url),
        }

    sample_text = "Testing local JARVIS speech." if language == "en" else "Menguji suara lokal JARVIS."
    try:
        async with httpx.AsyncClient(timeout=20.0, trust_env=False) as http:
            resp = await http.post(
                f"{detail.get('resolved_base_url', base_url)}/tts",
                json={
                    "text": sample_text,
                    "voice": voice,
                    "speed": speed,
                    "language": language,
                },
            )
        if resp.status_code != 200:
            return {
                "valid": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                "resolved_base_url": detail.get("resolved_base_url", base_url),
            }
        return {
            "valid": True,
            "provider": "local",
            "engine": engine,
            "resolved_base_url": detail.get("resolved_base_url", base_url),
            "voice": voice,
            "language": language,
            "bytes": len(resp.content),
        }
    except Exception as exc:
        return {
            "valid": False,
            "engine": engine,
            "error": _compact_error_text(str(exc)),
            "resolved_base_url": detail.get("resolved_base_url", base_url),
        }

@app.post("/api/announce")
async def api_announce(body: AnnouncementRequest):
    if not body.text.strip():
        return JSONResponse({"success": False, "error": "Announcement text is empty"}, status_code=400)
    result = await announce_via_jarvis_voice(body.text, body.source)
    if not result["success"]:
        return JSONResponse(result, status_code=409 if result.get("error") == "No active JARVIS voice clients" else 400)
    return result

@app.get("/api/settings/status")
async def api_settings_status():
    import shutil as _shutil
    _, env_dict = _read_env()
    claude_installed = _shutil.which("claude") is not None
    codex_delegate = env_dict.get("DEV_AGENT", "").strip() or ("codex" if os.name == "nt" else "claude")
    calendar_ok = mail_ok = notes_ok = False
    try: await get_todays_events(); calendar_ok = True
    except Exception: pass
    try: await get_unread_count(); mail_ok = True
    except Exception: pass
    try: await get_recent_notes(count=1); notes_ok = True
    except Exception: pass
    memory_count = task_count = 0
    try: memory_count = len(get_important_memories(limit=9999))
    except Exception: pass
    try: task_count = len(get_open_tasks())
    except Exception: pass
    local_probe = await _probe_local_llm_server(env_dict.get("JARVIS_LOCAL_LLM_BASE_URL", DEFAULT_LOCAL_LLM_BASE_URL))
    local_tts_engine = normalize_local_tts_engine(env_dict.get("JARVIS_LOCAL_TTS_ENGINE", DEFAULT_LOCAL_TTS_ENGINE))
    local_tts_base_url = env_dict.get("JARVIS_LOCAL_TTS_URL", "").strip() or get_default_local_tts_base_url(local_tts_engine)
    local_tts_probe = await _probe_local_tts_server(local_tts_base_url, local_tts_engine)
    return {
        "claude_code_installed": claude_installed,
        "dev_agent": codex_delegate,
        "calendar_accessible": calendar_ok,
        "mail_accessible": mail_ok,
        "notes_accessible": notes_ok,
        "memory_count": memory_count,
        "task_count": task_count,
        "server_port": 8340,
        "uptime_seconds": int(time.time() - _session_start),
        "env_keys_set": {
            "anthropic": bool(env_dict.get("ANTHROPIC_API_KEY", "").strip() and env_dict.get("ANTHROPIC_API_KEY", "") != "your-anthropic-api-key-here"),
            "nvidia": bool(env_dict.get("NVIDIA_API_KEY", "").strip()),
            "fish_audio": bool(env_dict.get("FISH_API_KEY", "").strip() and env_dict.get("FISH_API_KEY", "") != "your-fish-audio-api-key-here"),
            "fish_voice_id": bool(env_dict.get("FISH_VOICE_ID", "").strip()),
            "dev_agent": codex_delegate,
            "user_name": env_dict.get("USER_NAME", ""),
            "user_location": env_dict.get("USER_LOCATION", ""),
            "user_country": env_dict.get("USER_COUNTRY", ""),
            "user_language": env_dict.get("USER_LANGUAGE", "en"),
            "local_fallback_mode": get_local_fallback_mode(),
            "chat_box_enabled": env_dict.get("JARVIS_CHAT_BOX_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"},
            "llm_provider": env_dict.get("JARVIS_LLM_PROVIDER", "anthropic"),
            "tts_provider": env_dict.get("JARVIS_TTS_PROVIDER", DEFAULT_TTS_PROVIDER),
        },
        "llm_status": {
            "provider": get_llm_provider(),
            "available": bool(_llm_runtime_status.get("available")),
            "reason": _llm_runtime_status.get("reason", ""),
            "last_error": _llm_runtime_status.get("last_error", ""),
            "fallback_mode": get_local_fallback_mode(),
            "local_base_url": env_dict.get("JARVIS_LOCAL_LLM_BASE_URL", DEFAULT_LOCAL_LLM_BASE_URL),
            "local_resolved_base_url": local_probe.get("resolved_base_url", ""),
            "local_model": env_dict.get("JARVIS_LOCAL_LLM_MODEL", ""),
            "local_server_reachable": bool(local_probe.get("ok")),
            "local_models": [item.get("id", "") for item in (local_probe.get("models") or [])[:8]],
            "nvidia_base_url": env_dict.get("JARVIS_NVIDIA_LLM_BASE_URL", DEFAULT_NVIDIA_LLM_BASE_URL),
            "nvidia_model": env_dict.get("JARVIS_NVIDIA_LLM_MODEL", DEFAULT_NVIDIA_LLM_MODEL),
            "nvidia_key_configured": bool(env_dict.get("NVIDIA_API_KEY", "").strip()),
        },
    "tts_status": {
        "provider": get_tts_provider(),
        "local_engine": local_tts_engine,
        "local_base_url": local_tts_base_url,
        "local_resolved_base_url": local_tts_probe.get("resolved_base_url", ""),
        "local_server_reachable": bool(local_tts_probe.get("ok")),
        "local_engine_match": bool(local_tts_probe.get("engine_match", False)),
        "local_last_error": local_tts_probe.get("last_error", ""),
        "local_model_path": env_dict.get("JARVIS_LOCAL_TTS_MODEL_PATH", DEFAULT_VOXCPM_MODEL_PATH),
        "local_voice": env_dict.get("JARVIS_LOCAL_TTS_VOICE", DEFAULT_LOCAL_TTS_VOICE),
        "local_voice_id": env_dict.get("JARVIS_LOCAL_TTS_VOICE_ID", DEFAULT_LOCAL_TTS_VOICE_ID),
        "local_speed": env_dict.get("JARVIS_LOCAL_TTS_SPEED", str(DEFAULT_LOCAL_TTS_SPEED)),
    },
    }

@app.get("/api/settings/preferences")
async def api_get_preferences():
    _, env_dict = _read_env()
    local_tts_engine = normalize_local_tts_engine(env_dict.get("JARVIS_LOCAL_TTS_ENGINE", DEFAULT_LOCAL_TTS_ENGINE))
    return {
        "user_name": env_dict.get("USER_NAME", ""),
        "honorific": env_dict.get("HONORIFIC", "sir"),
        "calendar_accounts": env_dict.get("CALENDAR_ACCOUNTS", "auto"),
        "user_location": env_dict.get("USER_LOCATION", "Cimahi"),
        "user_country": env_dict.get("USER_COUNTRY", "Indonesia"),
        "user_language": env_dict.get("USER_LANGUAGE", "en"),
        "local_fallback_mode": env_dict.get("JARVIS_LOCAL_FALLBACK_MODE", "1").strip().lower() in {"1", "true", "yes", "on"},
        "chat_box_enabled": env_dict.get("JARVIS_CHAT_BOX_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"},
        "llm_provider": env_dict.get("JARVIS_LLM_PROVIDER", "anthropic"),
        "local_llm_base_url": env_dict.get("JARVIS_LOCAL_LLM_BASE_URL", DEFAULT_LOCAL_LLM_BASE_URL),
        "local_llm_model": env_dict.get("JARVIS_LOCAL_LLM_MODEL", ""),
        "nvidia_llm_base_url": env_dict.get("JARVIS_NVIDIA_LLM_BASE_URL", DEFAULT_NVIDIA_LLM_BASE_URL),
        "nvidia_llm_model": env_dict.get("JARVIS_NVIDIA_LLM_MODEL", DEFAULT_NVIDIA_LLM_MODEL),
        "tts_provider": env_dict.get("JARVIS_TTS_PROVIDER", DEFAULT_TTS_PROVIDER),
        "local_tts_engine": local_tts_engine,
        "local_tts_url": env_dict.get("JARVIS_LOCAL_TTS_URL", "").strip() or get_default_local_tts_base_url(local_tts_engine),
        "local_tts_model_path": env_dict.get("JARVIS_LOCAL_TTS_MODEL_PATH", DEFAULT_VOXCPM_MODEL_PATH),
        "local_tts_voice": env_dict.get("JARVIS_LOCAL_TTS_VOICE", DEFAULT_LOCAL_TTS_VOICE),
        "local_tts_voice_id": env_dict.get("JARVIS_LOCAL_TTS_VOICE_ID", DEFAULT_LOCAL_TTS_VOICE_ID),
        "local_tts_speed": env_dict.get("JARVIS_LOCAL_TTS_SPEED", str(DEFAULT_LOCAL_TTS_SPEED)),
        "fish_voice_id": env_dict.get("FISH_VOICE_ID", DEFAULT_FISH_VOICE_ID),
        "fish_voice_id_id": env_dict.get("FISH_VOICE_ID_ID", ""),
    }

@app.post("/api/settings/preferences")
async def api_save_preferences(body: PreferencesUpdate):
    global anthropic_client
    previous_language = get_user_language()
    next_language = "id" if body.user_language.lower().startswith("id") else "en"
    requested_provider = body.llm_provider.lower().strip()
    if requested_provider.startswith("local"):
        next_provider = "local"
    elif requested_provider.startswith("nvidia") or requested_provider.startswith("deepseek") or requested_provider.startswith("nim"):
        next_provider = "nvidia"
    else:
        next_provider = "anthropic"
    next_tts_provider = "local" if body.tts_provider.lower().startswith("local") else "fish"
    next_local_tts_engine = normalize_local_tts_engine(body.local_tts_engine)
    next_local_tts_url = body.local_tts_url.strip() or get_default_local_tts_base_url(next_local_tts_engine)
    next_local_tts_model_path = body.local_tts_model_path.strip() or DEFAULT_VOXCPM_MODEL_PATH
    next_local_tts_voice = body.local_tts_voice.strip() or DEFAULT_LOCAL_TTS_VOICE
    next_local_tts_voice_id = body.local_tts_voice_id.strip()
    next_local_tts_speed = min(2.0, max(0.5, float(body.local_tts_speed)))
    _write_env_key("USER_NAME", body.user_name)
    _write_env_key("HONORIFIC", body.honorific)
    _write_env_key("CALENDAR_ACCOUNTS", body.calendar_accounts)
    _write_env_key("USER_LOCATION", body.user_location)
    _write_env_key("USER_COUNTRY", body.user_country)
    _write_env_key("USER_LANGUAGE", next_language)
    _write_env_key("JARVIS_LOCAL_FALLBACK_MODE", "1" if body.local_fallback_mode else "0")
    _write_env_key("JARVIS_CHAT_BOX_ENABLED", "1" if body.chat_box_enabled else "0")
    _write_env_key("JARVIS_LLM_PROVIDER", next_provider)
    _write_env_key("JARVIS_LOCAL_LLM_BASE_URL", body.local_llm_base_url.strip() or DEFAULT_LOCAL_LLM_BASE_URL)
    _write_env_key("JARVIS_LOCAL_LLM_MODEL", body.local_llm_model.strip())
    _write_env_key("JARVIS_NVIDIA_LLM_BASE_URL", body.nvidia_llm_base_url.strip().rstrip("/") or DEFAULT_NVIDIA_LLM_BASE_URL)
    _write_env_key("JARVIS_NVIDIA_LLM_MODEL", body.nvidia_llm_model.strip() or DEFAULT_NVIDIA_LLM_MODEL)
    _write_env_key("JARVIS_TTS_PROVIDER", next_tts_provider)
    _write_env_key("JARVIS_LOCAL_TTS_ENGINE", next_local_tts_engine)
    _write_env_key("JARVIS_LOCAL_TTS_URL", next_local_tts_url)
    _write_env_key("JARVIS_LOCAL_TTS_MODEL_PATH", next_local_tts_model_path)
    _write_env_key("JARVIS_LOCAL_TTS_VOICE", next_local_tts_voice)
    _write_env_key("JARVIS_LOCAL_TTS_VOICE_ID", next_local_tts_voice_id)
    _write_env_key("JARVIS_LOCAL_TTS_SPEED", f"{next_local_tts_speed:.2f}".rstrip("0").rstrip("."))
    anthropic_client = _refresh_llm_client()
    # When switching to local provider, kick off a background probe so the
    # status indicator updates and the model is pre-cached for the first chat.
    if next_provider == "local":
        asyncio.create_task(_async_probe_and_update_local_llm())
    if next_tts_provider == "local":
        asyncio.create_task(_ensure_local_tts_server(next_local_tts_url, next_local_tts_engine, next_local_tts_model_path))
    if previous_language != next_language:
        _ctx_cache["briefing_spoken"] = ""
        _ctx_cache["briefing_spoken_source"] = ""
        _ctx_cache["briefing_spoken_language"] = ""
    return {"success": True}

# ---------------------------------------------------------------------------
# Control endpoints (restart, fix-self)
# ---------------------------------------------------------------------------

@app.post("/api/restart")
async def api_restart():
    """Restart the JARVIS server."""
    log.info("Restart requested — shutting down in 2 seconds")
    async def _restart():
        await asyncio.sleep(2)
        cmd = [sys.executable, str(Path(__file__).resolve()), "--port", "8340", "--host", "0.0.0.0"]
        if _env_flag("JARVIS_FORCE_HTTP"):
            cmd.append("--http")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            cmd,
            cwd=PROJECT_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        os._exit(0)
    asyncio.create_task(_restart())
    return {"status": "restarting"}


@app.post("/api/fix-self")
async def api_fix_self():
    """Enter work mode in the JARVIS repo — JARVIS can now fix himself."""
    jarvis_dir = str(Path(__file__).parent)
    # The work_session is per-WebSocket, so we set a flag that the handler picks up
    # For now, also open Terminal so user can see
    script = (
        'tell application "Terminal"\n'
        '    activate\n'
        f'    do script "cd {jarvis_dir} && claude --dangerously-skip-permissions"\n'
        'end tell'
    )
    await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    log.info("Work mode: JARVIS repo opened for self-improvement")
    return {"status": "work_mode_active", "path": jarvis_dir}


# ---------------------------------------------------------------------------
# Static file serving (frontend)
# ---------------------------------------------------------------------------

from starlette.staticfiles import StaticFiles
from starlette.responses import FileResponse

FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"

if FRONTEND_DIST.exists():
    @app.get("/")
    async def serve_index():
        return FileResponse(str(FRONTEND_DIST / "index.html"))

    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="JARVIS Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=8340, help="Bind port")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on changes")
    parser.add_argument("--ssl", action="store_true", help="Enable HTTPS with key.pem/cert.pem")
    parser.add_argument("--http", action="store_true", help="Force HTTP even if cert.pem/key.pem exist")
    args = parser.parse_args()

    # Auto-detect SSL certs
    cert_file = Path(__file__).parent / "cert.pem"
    key_file = Path(__file__).parent / "key.pem"
    force_http = _env_flag("JARVIS_FORCE_HTTP")
    use_ssl = False if (args.http or force_http) else (args.ssl or (cert_file.exists() and key_file.exists()))

    proto = "https" if use_ssl else "http"
    ws_proto = "wss" if use_ssl else "ws"

    print()
    print("  J.A.R.V.I.S. Server v0.1.0")
    print(f"  WebSocket: {ws_proto}://{args.host}:{args.port}/ws/voice")
    print(f"  REST API:  {proto}://{args.host}:{args.port}/api/")
    print(f"  Tasks:     {proto}://{args.host}:{args.port}/api/tasks")
    print()

    ssl_kwargs = {}
    if use_ssl:
        ssl_kwargs["ssl_keyfile"] = str(key_file)
        ssl_kwargs["ssl_certfile"] = str(cert_file)

    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
        **ssl_kwargs,
    )
