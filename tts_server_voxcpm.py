"""
Local TTS server for JARVIS using VoxCPM2.

Install:
    py -3.11 -m pip install voxcpm soundfile numpy

Model files:
    Point VOXCPM_MODEL_PATH at the downloaded VoxCPM2 folder.
    Default: D:\AI Project\VoxCPM2

Run:
    py -3.11 tts_server_voxcpm.py

Then in JARVIS .env:
    JARVIS_TTS_PROVIDER=local
    JARVIS_LOCAL_TTS_ENGINE=voxcpm
    JARVIS_LOCAL_TTS_URL=http://127.0.0.1:8882
"""

import io
import logging
import os
import wave
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

log = logging.getLogger("tts-server-voxcpm")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

HOST = os.getenv("TTS_HOST", "127.0.0.1")
PORT = int(os.getenv("TTS_PORT", "8882"))
BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = Path(
    os.getenv(
        "VOXCPM_MODEL_PATH",
        str(BASE_DIR.parent / "VoxCPM2"),
    )
)
CFG_VALUE = float(os.getenv("VOXCPM_CFG_VALUE", "2.0"))
INFERENCE_TIMESTEPS = int(os.getenv("VOXCPM_INFERENCE_TIMESTEPS", "10"))

try:
    from voxcpm import VoxCPM

    VOXCPM_IMPORT_ERROR = ""
except ImportError as exc:  # pragma: no cover - runtime dependency
    VoxCPM = None  # type: ignore[assignment]
    VOXCPM_IMPORT_ERROR = str(exc)

app = FastAPI(title="JARVIS Local TTS - VoxCPM2")
_model = None
_model_error = ""


def get_model():
    global _model, _model_error
    if _model is not None:
        return _model
    if VoxCPM is None:
        _model_error = VOXCPM_IMPORT_ERROR or "voxcpm package not installed"
        raise ModuleNotFoundError(_model_error)
    if not MODEL_PATH.exists():
        _model_error = f"VoxCPM model folder not found at {MODEL_PATH}"
        raise FileNotFoundError(_model_error)
    log.info("Loading VoxCPM model from %s ...", MODEL_PATH)
    try:
        _model = VoxCPM.from_pretrained(str(MODEL_PATH))
    except Exception as exc:
        _model = None
        _model_error = str(exc) or exc.__class__.__name__
        log.exception("VoxCPM model load failed: %s", _model_error)
        raise
    _model_error = ""
    log.info("VoxCPM model loaded. TTS server ready.")
    return _model


class TTSRequest(BaseModel):
    text: str
    voice: str = ""
    speed: float = 1.0
    language: str = "en"


def _status_payload() -> dict:
    if VoxCPM is None:
        status = "package_missing"
    elif not MODEL_PATH.exists():
        status = "model_not_found"
    elif _model is None and _model_error:
        status = "model_load_failed"
    elif _model is None:
        status = "model_not_loaded"
    else:
        status = "ok"
    return {
        "status": status,
        "engine": "voxcpm",
        "model": "VoxCPM2",
        "model_path": str(MODEL_PATH),
        "model_exists": MODEL_PATH.exists(),
        "package_available": VoxCPM is not None,
        "cfg_value": CFG_VALUE,
        "inference_timesteps": INFERENCE_TIMESTEPS,
        "last_error": _model_error or VOXCPM_IMPORT_ERROR,
    }


@app.on_event("startup")
async def startup():
    try:
        get_model()
    except Exception as exc:  # pragma: no cover - startup warning only
        log.warning("VoxCPM model not ready on startup: %s", exc)


@app.get("/health")
async def health():
    return _status_payload()


def _to_wav_bytes(samples, sample_rate: int) -> bytes:
    pcm = np.asarray(samples, dtype=np.float32)
    if pcm.ndim > 1:
        pcm = pcm[:, 0]
    pcm = np.clip(pcm, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm16.tobytes())
    return buf.getvalue()


@app.post("/tts")
async def synthesize(req: TTSRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text is empty")
    try:
        model = get_model()
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"VoxCPM package is missing: {exc}. Run: py -3.11 -m pip install voxcpm soundfile numpy",
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"VoxCPM is not ready: {exc}") from exc

    try:
        log.info("Generating VoxCPM audio for %s chars...", len(req.text))
        wav = model.generate(
            text=req.text,
            cfg_value=CFG_VALUE,
            inference_timesteps=INFERENCE_TIMESTEPS,
        )
        sample_rate = int(getattr(getattr(model, "tts_model", None), "sample_rate", 24000))
        audio_bytes = _to_wav_bytes(wav, sample_rate)
        return Response(content=audio_bytes, media_type="audio/wav")
    except Exception as exc:
        global _model_error
        _model_error = str(exc) or exc.__class__.__name__
        log.error("VoxCPM synthesis error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Synthesis failed: {exc}") from exc


if __name__ == "__main__":
    log.info("Starting JARVIS Local TTS (VoxCPM2) on http://%s:%s", HOST, PORT)
    log.info("Model: %s", MODEL_PATH)
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
