#!/usr/bin/env python3
"""STEP 1 helper: transcribe a video to SRT via AssemblyAI.

Usage:
    python scripts/transcribe_assemblyai.py input_videos/NN.mp4 temp/NN.srt

Reads ASSEMBLYAI_API_KEY from the environment or .env. Uploads the media
directly (AssemblyAI extracts audio from video), polls until the transcript
is ready, then downloads the SRT export.

Exit codes: 0 ok, 1 failure (caller logs to output/errors.log and skips).
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ffcommon import load_env, eprint  # noqa: E402

BASE = "https://api.assemblyai.com/v2"
POLL_SECONDS = 3
MAX_POLLS = 400  # ~20 min ceiling


def _requests():
    try:
        import requests  # noqa: WPS433 - optional dep, checked at runtime
    except ImportError:
        eprint("error: the 'requests' package is required "
               "(pip install -r requirements.txt)")
        raise SystemExit(1)
    return requests


def transcribe(media_path, out_srt):
    requests = _requests()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not api_key:
        eprint("error: ASSEMBLYAI_API_KEY not set")
        return 1
    headers = {"authorization": api_key}

    # 1) Upload the media file.
    with open(media_path, "rb") as fh:
        up = requests.post(BASE + "/upload", headers=headers, data=fh)
    up.raise_for_status()
    audio_url = up.json()["upload_url"]

    # 2) Request transcription.
    req = requests.post(
        BASE + "/transcript",
        headers=headers,
        json={"audio_url": audio_url, "punctuate": True, "format_text": True},
    )
    req.raise_for_status()
    tid = req.json()["id"]

    # 3) Poll for completion.
    for _ in range(MAX_POLLS):
        poll = requests.get(BASE + "/transcript/" + tid, headers=headers)
        poll.raise_for_status()
        status = poll.json().get("status")
        if status == "completed":
            break
        if status == "error":
            eprint("error: AssemblyAI: %s" % poll.json().get("error"))
            return 1
        time.sleep(POLL_SECONDS)
    else:
        eprint("error: transcription timed out")
        return 1

    # 4) Download SRT export.
    srt = requests.get(BASE + "/transcript/" + tid + "/srt", headers=headers)
    srt.raise_for_status()
    os.makedirs(os.path.dirname(os.path.abspath(out_srt)), exist_ok=True)
    with open(out_srt, "w", encoding="utf-8") as fh:
        fh.write(srt.text)
    return 0


def main(argv):
    if len(argv) != 3:
        eprint("usage: transcribe_assemblyai.py <video> <out.srt>")
        return 2
    load_env()
    media_path, out_srt = argv[1], argv[2]
    if not os.path.exists(media_path):
        eprint("error: file not found: %s" % media_path)
        return 1
    try:
        return transcribe(media_path, out_srt)
    except Exception as exc:  # noqa: BLE001
        eprint("error: transcription failed: %s" % exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
