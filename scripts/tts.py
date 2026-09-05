"""
tts.py — STEP 5 (narration voice)

Engines:
  edge     -> FREE Microsoft Edge neural voice (default, no key)
  ai33pro  -> ElevenLabs-compatible endpoint (AI33PRO_* env) for premium voice

Usage:
    python scripts/tts.py --text-file temp/01_narr_1.txt --out temp/01_narr_1.mp3
    python scripts/tts.py --text "hello world" --out out.mp3
Returns exit 0 on success; prints the mp3 duration in seconds.
"""
from __future__ import annotations
import argparse
import asyncio
import sys
from pathlib import Path

from common import env, load_env, ffmpeg_bin, run, log_error


def audio_duration(path: str) -> float:
    """Duration via ffmpeg (works without ffprobe)."""
    import re
    res = run([ffmpeg_bin(), "-hide_banner", "-i", path], check=False)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", res.stderr or "")
    if not m:
        return 0.0
    h, mm, s = m.groups()
    return int(h) * 3600 + int(mm) * 60 + float(s)


# --------------------------------------------------------------------------- #
def tts_edge(text: str, out: str) -> bool:
    import edge_tts
    voice = env("EDGE_TTS_VOICE", "en-US-GuyNeural")
    rate = env("EDGE_TTS_RATE", "+8%")
    pitch = env("EDGE_TTS_PITCH", "+0Hz")

    async def _run():
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        await communicate.save(out)

    asyncio.run(_run())
    return Path(out).exists() and Path(out).stat().st_size > 100


# --------------------------------------------------------------------------- #
def tts_ai33pro(text: str, out: str) -> bool:
    import requests
    key = env("AI33PRO_API_KEY")
    base = env("AI33PRO_BASE_URL").rstrip("/")
    voice_id = env("AI33PRO_VOICE_ID")
    if not (key and base and voice_id):
        raise RuntimeError("AI33PRO_API_KEY / AI33PRO_BASE_URL / AI33PRO_VOICE_ID missing")
    url = f"{base}/v1/text-to-speech/{voice_id}"
    headers = {"xi-api-key": key, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.40, "similarity_boost": 0.80,
                           "style": 0.60, "use_speaker_boost": True},
    }
    r = requests.post(url, headers=headers, json=payload, timeout=180)
    r.raise_for_status()
    Path(out).write_bytes(r.content)
    return Path(out).exists() and Path(out).stat().st_size > 100


# --------------------------------------------------------------------------- #
def synthesize(text: str, out: str) -> bool:
    engine = env("TTS_ENGINE", "edge").lower()
    text = text.strip()
    if not text:
        raise ValueError("empty narration text")
    if engine == "ai33pro":
        try:
            return tts_ai33pro(text, out)
        except Exception as e:
            log_error(f"AI33Pro TTS failed ({e}); falling back to free EdgeTTS")
            return tts_edge(text, out)
    return tts_edge(text, out)


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-file")
    ap.add_argument("--text")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        print("need --text or --text-file", file=sys.stderr)
        sys.exit(2)

    ok = False
    for attempt in range(2):
        try:
            ok = synthesize(text, args.out)
            if ok:
                break
        except Exception as e:
            log_error(f"TTS attempt {attempt+1} failed: {e}")
    if not ok:
        sys.exit(1)
    print(f"{audio_duration(args.out):.2f}")


if __name__ == "__main__":
    main()
