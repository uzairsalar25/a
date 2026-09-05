#!/usr/bin/env python3
"""STEP 5 helper: generate one narration MP3 via an ElevenLabs-compatible API.

Usage:
    python scripts/tts_elevenlabs.py --text-file temp/NN_narr_1.txt \
        --out temp/NN_narr_1.mp3 [--voice VOICE_ID] [--stability 0.4] \
        [--similarity 0.8] [--style 0.6] [--speed 1.05]

Reads AI33PRO_API_KEY and AI33PRO_BASE_URL from the environment or .env.
Defaults target an energetic broadcast-news delivery (see SKILL Step 5).
Retries twice on failure; exit code 1 means the caller should skip the video.
On success prints the output MP3 duration (seconds) to stdout.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ffcommon import load_env, media_duration, eprint  # noqa: E402

DEFAULT_BASE = "https://api.elevenlabs.io"
DEFAULT_MODEL = "eleven_multilingual_v2"
# Energetic broadcast-style default voice ("Adam"). Override with --voice or
# the AI33PRO_VOICE_ID env var to lock one consistent voice per video.
DEFAULT_VOICE = "pNInz6obpgDQGcFmaJgB"
RETRIES = 2


def _requests():
    try:
        import requests  # noqa: WPS433
    except ImportError:
        eprint("error: the 'requests' package is required "
               "(pip install -r requirements.txt)")
        raise SystemExit(1)
    return requests


def synthesize(text, out_path, voice, settings):
    requests = _requests()
    api_key = os.environ.get("AI33PRO_API_KEY")
    if not api_key:
        eprint("error: AI33PRO_API_KEY not set")
        return 1
    base = os.environ.get("AI33PRO_BASE_URL", DEFAULT_BASE).rstrip("/")
    url = "%s/v1/text-to-speech/%s" % (base, voice)

    payload = {
        "text": text,
        "model_id": os.environ.get("AI33PRO_MODEL_ID", DEFAULT_MODEL),
        "voice_settings": {
            "stability": settings["stability"],
            "similarity_boost": settings["similarity"],
            "style": settings["style"],
            "use_speaker_boost": True,
        },
    }
    # ElevenLabs takes speed via the query/output; keep it in voice_settings
    # when the backend supports it, harmless otherwise.
    if settings.get("speed"):
        payload["voice_settings"]["speed"] = settings["speed"]

    headers = {
        "xi-api-key": api_key,
        "authorization": "Bearer " + api_key,
        "accept": "audio/mpeg",
        "content-type": "application/json",
    }
    params = {"output_format": "mp3_44100_128"}

    last_err = None
    for attempt in range(1, RETRIES + 2):
        try:
            resp = requests.post(url, headers=headers, params=params,
                                 json=payload, timeout=180)
            if resp.status_code == 200 and resp.content:
                os.makedirs(os.path.dirname(os.path.abspath(out_path)),
                            exist_ok=True)
                with open(out_path, "wb") as fh:
                    fh.write(resp.content)
                return 0
            last_err = "HTTP %d: %s" % (resp.status_code, resp.text[:300])
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
        eprint("tts attempt %d failed: %s" % (attempt, last_err))
        time.sleep(2 * attempt)

    eprint("error: TTS failed after retries: %s" % last_err)
    return 1


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--text-file", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--voice",
                        default=os.environ.get("AI33PRO_VOICE_ID", DEFAULT_VOICE))
    parser.add_argument("--stability", type=float, default=0.40)
    parser.add_argument("--similarity", type=float, default=0.80)
    parser.add_argument("--style", type=float, default=0.60)
    parser.add_argument("--speed", type=float, default=1.05)
    args = parser.parse_args(argv[1:])

    load_env()
    if not os.path.exists(args.text_file):
        eprint("error: text file not found: %s" % args.text_file)
        return 1
    with open(args.text_file, "r", encoding="utf-8") as fh:
        text = fh.read().strip()
    if not text:
        eprint("error: text file is empty")
        return 1

    settings = {
        "stability": args.stability,
        "similarity": args.similarity,
        "style": args.style,
        "speed": args.speed,
    }
    rc = synthesize(text, args.out, args.voice, settings)
    if rc != 0:
        return rc
    try:
        dur = media_duration(args.out)
        print("%.3f" % dur)
    except Exception:  # noqa: BLE001 - duration is informational only
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
