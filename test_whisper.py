#!/usr/bin/env python3
"""Test faster-whisper transcription locally."""

import sys
import tempfile
from pathlib import Path

def test_whisper_import():
    """Test if faster-whisper can be imported."""
    try:
        from faster_whisper import WhisperModel
        print("✓ faster-whisper import OK")
        return True
    except ImportError as e:
        print(f"✗ faster-whisper import failed: {e}")
        return False

def test_whisper_model_load():
    """Test if Whisper model can be loaded."""
    if not test_whisper_import():
        return False

    try:
        from faster_whisper import WhisperModel
        print("Loading Whisper model (small, int8)...")
        model = WhisperModel("small", compute_type="int8")
        print("✓ Model loaded successfully")
        return model
    except Exception as e:
        print(f"✗ Model load failed: {e}")
        return None

def test_transcribe_silence():
    """Test transcription on a short silence."""
    model = test_whisper_model_load()
    if not model:
        return False

    try:
        import wave
        import struct

        # Create a 2-second silence audio file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

            # Write WAV header and silence
            with wave.open(tmp_path, "w") as wav:
                wav.setnchannels(1)  # Mono
                wav.setsampwidth(2)  # 16-bit
                wav.setframerate(16000)  # 16kHz

                # Write 2 seconds of silence (zero bytes)
                silence = struct.pack("<h", 0) * (16000 * 2)
                wav.writeframes(silence)

        print(f"\nTranscribing silence test file: {tmp_path}")
        segments, info = model.transcribe(tmp_path)

        text = " ".join([segment.text.strip() for segment in segments])
        print(f"Result: '{text}'")
        print(f"Language: {info.language} (confidence: {info.language_probability:.2f})")

        # Clean up
        Path(tmp_path).unlink()

        print("✓ Transcription test passed")
        return True
    except Exception as e:
        print(f"✗ Transcription test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("Testing faster-whisper setup...\n")

    if not test_whisper_import():
        print("\nInstall faster-whisper:")
        print("  pip install faster-whisper --break-system-packages")
        sys.exit(1)

    if not test_whisper_model_load():
        sys.exit(1)

    if not test_transcribe_silence():
        sys.exit(1)

    print("\n✓ All tests passed! Whisper is ready.")
