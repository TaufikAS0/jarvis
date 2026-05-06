"""
Local TTS server for JARVIS using Kokoro ONNX.

Install:
    pip install kokoro-onnx soundfile fastapi uvicorn numpy

Model files:
    Place `kokoro-v1.9.onnx` and `voices-v1.0.bin` in the JARVIS folder,
    or point to them with TTS_MODEL_PATH and TTS_VOICES_PATH env vars.

Run:
    python tts_server.py

Then in JARVIS .env:
    JARVIS_TTS_PROVIDER=local
    JARVIS_LOCAL_TTS_URL=http://127.0.0.1:8881
    JARVIS_LOCAL_TTS_VOICE=bm_george
"""

import io
import logging
import os
from pathlib import Path

import soundfile as sf
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

log = logging.getLogger("tts-server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

HOST = os.getenv("TTS_HOST", "127.0.0.1")
PORT = int(os.getenv("TTS_PORT", "8881"))
BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = Path(os.getenv("TTS_MODEL_PATH", str(BASE_DIR / "kokoro-v1.0.onnx")))
VOICES_PATH = Path(os.getenv("TTS_VOICES_PATH", str(BASE_DIR / "voices-v1.0.bin")))
DEFAULT_VOICE_EN = os.getenv("TTS_VOICE_EN", "bm_george")   # British male — closest to JARVIS
DEFAULT_VOICE_ID = os.getenv("TTS_VOICE_ID", DEFAULT_VOICE_EN)

app = FastAPI(title="JARVIS Local TTS")
_pipeline = None
_model_ready = False


def get_pipeline():
    global _pipeline, _model_ready
    if _pipeline is None:
        if not MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Kokoro model not found at {MODEL_PATH}\n"
                f"Download from: https://github.com/thewh1teagle/kokoro-onnx/releases"
            )
        if not VOICES_PATH.exists():
            raise FileNotFoundError(
                f"Kokoro voices not found at {VOICES_PATH}\n"
                f"Download from: https://github.com/thewh1teagle/kokoro-onnx/releases"
            )
        log.info("Loading Kokoro model from %s ...", MODEL_PATH)
        from kokoro_onnx import Kokoro
        _pipeline = Kokoro(str(MODEL_PATH), str(VOICES_PATH))
        _model_ready = True
        log.info("Kokoro model loaded. TTS server ready.")
    return _pipeline


class TTSRequest(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE_EN
    speed: float = 1.0
    language: str = "en"


def resolve_voice(language: str, requested_voice: str | None) -> str:
    voice = (requested_voice or "").strip()
    if voice:
        return voice
    if (language or "").lower().startswith("id"):
        return DEFAULT_VOICE_ID
    return DEFAULT_VOICE_EN


def resolve_kokoro_lang(voice: str) -> str:
    """Map voice prefix to Kokoro lang code."""
    v = (voice or "").lower()
    if v.startswith("bf_") or v.startswith("bm_"):
        return "en-gb"
    if v.startswith("af_") or v.startswith("am_"):
        return "en-us"
    return "en-us"


@app.on_event("startup")
async def startup():
    try:
        get_pipeline()
    except FileNotFoundError as e:
        log.warning("Kokoro model not found on startup: %s", e)
        log.warning("Place kokoro-v1.9.onnx and voices-v1.0.bin in: %s", BASE_DIR)


@app.get("/health")
async def health():
    return {
        "status": "ok" if _model_ready else "model_not_loaded",
        "engine": "kokoro",
        "model": "kokoro-onnx",
        "model_path": str(MODEL_PATH),
        "model_exists": MODEL_PATH.exists(),
        "voices_exists": VOICES_PATH.exists(),
        "default_voice_en": DEFAULT_VOICE_EN,
        "default_voice_id": DEFAULT_VOICE_ID,
    }


@app.post("/tts")
async def synthesize(req: TTSRequest):
    """Generate speech. Returns WAV audio bytes."""
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text is empty")
    try:
        pipeline = get_pipeline()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))

    voice = resolve_voice(req.language, req.voice)
    lang = resolve_kokoro_lang(voice)

    try:
        samples, sr = pipeline.create(req.text, voice=voice, speed=req.speed, lang=lang)
        buf = io.BytesIO()
        sf.write(buf, samples, sr, format="WAV")
        buf.seek(0)
        return Response(content=buf.read(), media_type="audio/wav")
    except Exception as e:
        log.error("Kokoro synthesis error: %s", e)
        raise HTTPException(status_code=500, detail=f"Synthesis failed: {e}")


if __name__ == "__main__":
    log.info("Starting JARVIS Local TTS on http://%s:%s", HOST, PORT)
    log.info("Model: %s", MODEL_PATH)
    log.info("Voices: %s", VOICES_PATH)
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
