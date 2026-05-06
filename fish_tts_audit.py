"""
Fish Audio TTS audit for Indonesian naturalness.

Runs a small experiment matrix against the configured Fish Audio account:
- backend comparison: s1 vs s2-pro
- voice comparison: current configured voice vs Indonesian fallback/public voice
- text comparison: pure Indonesian, mixed language, raw numbers, spelled-out numbers

Outputs:
- JSON report saved under data/fish_tts_audit/<timestamp>/report.json
- MP3 files for each successful test case
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
import server  # noqa: E402


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = ROOT / ".env"
    if not env_path.exists():
        return env
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def fetch_model(client: httpx.Client, key: str, model_id: str) -> dict:
    resp = client.get(
        f"https://api.fish.audio/model/{model_id}",
        headers={"Authorization": f"Bearer {key}"},
    )
    payload = resp.json() if resp.status_code == 200 else {"error": resp.text[:400]}
    return {
        "status": resp.status_code,
        "_id": payload.get("_id"),
        "title": payload.get("title"),
        "languages": payload.get("languages"),
        "tags": payload.get("tags"),
        "default_text": payload.get("default_text"),
    }


def run() -> int:
    env = load_env()
    fish_key = env.get("FISH_API_KEY", "").strip()
    if not fish_key:
        print("FISH_API_KEY is missing in .env", file=sys.stderr)
        return 1

    current_voice = env.get("FISH_VOICE_ID", "").strip() or server.FISH_VOICE_ID
    fallback_voice = env.get("FISH_VOICE_ID_ID_FALLBACK", "").strip() or server.FISH_VOICE_ID_ID_FALLBACK

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "data" / "fish_tts_audit" / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    text_cases = {
        "A_public_id_pure": "Selamat pagi, Taufik. Cuaca di Cimahi hari ini cerah berawan dan suhu sekitar dua puluh empat derajat Celsius.",
        "B_current_id_pure": "Selamat pagi, Taufik. Cuaca di Cimahi hari ini cerah berawan dan suhu sekitar dua puluh empat derajat Celsius.",
        "C_mixed_language": "Selamat pagi, Taufik. Your local briefing is ready dan cuaca di Cimahi cerah.",
        "D_numbers_raw": "Tagihan kamu 12450 rupiah dan rapat dimulai pukul 14:30 di lantai 3.",
        "E_numbers_spelled": "Tagihan kamu dua belas ribu empat ratus lima puluh rupiah dan rapat dimulai pukul dua lewat tiga puluh siang di lantai tiga.",
    }

    voice_matrix = [
        ("public_id_voice", fallback_voice),
        ("current_configured_voice", current_voice),
    ]

    backend_matrix = ["s1", "s2-pro"]
    report: dict[str, object] = {
        "generated_at": timestamp,
        "current_voice_id": current_voice,
        "fallback_voice_id": fallback_voice,
        "experiments": [],
    }

    with httpx.Client(timeout=40.0) as client:
        report["voices"] = {
            "current": fetch_model(client, fish_key, current_voice),
            "fallback": fetch_model(client, fish_key, fallback_voice),
        }

        for backend in backend_matrix:
            for voice_label, reference_id in voice_matrix:
                for case_name, text in text_cases.items():
                    if case_name.startswith("A_") and voice_label != "public_id_voice":
                        continue
                    if case_name.startswith("B_") and voice_label != "current_configured_voice":
                        continue

                    normalized_text = server._normalize_indonesian_tts_text(text)
                    english_ratio = server._estimate_english_content_ratio(text)
                    payload = {
                        "text": normalized_text,
                        "reference_id": reference_id,
                        "format": "mp3",
                        "normalize": True,
                        "latency": "balanced",
                    }
                    resp = client.post(
                        "https://api.fish.audio/v1/tts",
                        headers={
                            "Authorization": f"Bearer {fish_key}",
                            "Content-Type": "application/json",
                            "model": backend,
                        },
                        json=payload,
                    )

                    file_name = f"{backend}__{voice_label}__{case_name}.mp3"
                    file_path = out_dir / file_name
                    if resp.status_code == 200:
                        file_path.write_bytes(resp.content)

                    report["experiments"].append({
                        "backend": backend,
                        "voice_label": voice_label,
                        "reference_id": reference_id,
                        "case": case_name,
                        "status_code": resp.status_code,
                        "bytes": len(resp.content),
                        "raw_text": text,
                        "normalized_text": normalized_text,
                        "english_ratio": round(english_ratio, 4),
                        "audio_file": str(file_path) if resp.status_code == 200 else None,
                    })

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nSaved report to: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
