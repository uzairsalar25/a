"""Shared helpers for the Royal News Video Editor scripts.

Small, dependency-light utilities for probing media, loading the local .env,
and running ffmpeg/ffprobe. Every script in scripts/ imports from here.
"""

import json
import os
import subprocess
import sys


def load_env(path=".env"):
    """Load KEY=VALUE lines from a .env file into os.environ.

    Existing environment variables win, so a real shell export is never
    overwritten. Missing file is a no-op.
    """
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def run(cmd, check=True, capture=False):
    """Run a subprocess. Returns CompletedProcess.

    When capture is True, stdout/stderr are captured as text; otherwise they
    stream to the console so long ffmpeg encodes show progress.
    """
    kwargs = {"text": True}
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    proc = subprocess.run(cmd, **kwargs)
    if check and proc.returncode != 0:
        err = proc.stderr if capture else ""
        raise RuntimeError(
            "Command failed (%d): %s\n%s" % (proc.returncode, " ".join(cmd), err)
        )
    return proc


def ffprobe_json(path):
    """Return the full ffprobe JSON (format + streams) for a media file."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    proc = run(cmd, check=True, capture=True)
    return json.loads(proc.stdout)


def probe_summary(path):
    """Summarise a media file: duration, resolution, fps, audio, subtitles."""
    data = ffprobe_json(path)
    fmt = data.get("format", {})
    streams = data.get("streams", [])

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    sub_streams = [
        {"index": s.get("index"), "codec": s.get("codec_name"),
         "lang": s.get("tags", {}).get("language")}
        for s in streams if s.get("codec_type") == "subtitle"
    ]

    fps = None
    width = height = None
    if video is not None:
        width = video.get("width")
        height = video.get("height")
        rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/0"
        try:
            num, den = rate.split("/")
            fps = round(float(num) / float(den), 3) if float(den) else None
        except (ValueError, ZeroDivisionError):
            fps = None

    return {
        "path": path,
        "duration_sec": round(float(fmt.get("duration", 0.0) or 0.0), 3),
        "width": width,
        "height": height,
        "fps": fps,
        "has_audio": has_audio,
        "subtitle_streams": sub_streams,
    }


def media_duration(path):
    """Return the duration of a media file in seconds (0.0 if unknown)."""
    data = ffprobe_json(path)
    return round(float(data.get("format", {}).get("duration", 0.0) or 0.0), 3)


def is_image(path):
    return os.path.splitext(path)[1].lower() in {
        ".jpg", ".jpeg", ".png", ".webp", ".bmp",
    }


def eprint(*args):
    print(*args, file=sys.stderr)
