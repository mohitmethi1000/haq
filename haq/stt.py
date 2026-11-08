"""Speech to text with Sarvam Saaras (code-mixed Hindi/English, 22 Indian languages).

`mode=codemix` keeps English words in English and Hindi in Devanagari, which suits the
downstream model better than forcing one script. REST accepts clips up to ~30 s.
"""
from __future__ import annotations

import os

import requests

URL = "https://api.sarvam.ai/speech-to-text"


def transcribe(audio: bytes, filename: str = "audio.wav", model: str = "saaras:v4") -> dict:
    key = os.environ.get("SARVAM_API_KEY", "")
    if not key:
        raise RuntimeError("SARVAM_API_KEY not set; voice input unavailable (type instead).")
    last = None
    for m, extra in ((model, {"mode": "codemix"}), (model, {}), ("saarika:v2.5", {})):
        data = {"model": m, "language_code": "unknown", **extra}
        r = requests.post(URL, headers={"api-subscription-key": key},
                          files={"file": (filename, audio, "audio/wav")}, data=data, timeout=60)
        if r.ok:
            j = r.json()
            return {"text": j.get("transcript", ""), "language": j.get("language_code"), "model": m}
        last = f"{r.status_code} {r.text[:300]}"
    raise RuntimeError(f"Sarvam STT failed: {last}")
