"""
probe_video.py — STEP 1a

Get duration, resolution, fps, has_audio, and embedded subtitle streams.
Uses ffprobe when available; otherwise falls back to parsing `ffmpeg -i`.

Usage:
    python scripts/probe_video.py input_videos/01.mp4
    -> prints JSON and writes temp/01_probe.json
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

from common import ffprobe_bin, ffmpeg_bin, run, TEMP, save_json


def _fps_from_rate(rate: str) -> float:
    try:
        if "/" in rate:
            a, b = rate.split("/")
            return round(float(a) / float(b), 3) if float(b) else 0.0
        return round(float(rate), 3)
    except Exception:
        return 0.0


def probe_with_ffprobe(path: str) -> dict:
    fp = ffprobe_bin()
    res = run([fp, "-v", "quiet", "-print_format", "json",
               "-show_format", "-show_streams", path])
    data = json.loads(res.stdout)
    info = {"file": path, "duration_sec": 0.0, "width": 0, "height": 0,
            "fps": 0.0, "has_audio": False, "embedded_subtitles": []}
    info["duration_sec"] = round(float(data.get("format", {}).get("duration", 0) or 0), 3)
    for i, s in enumerate(data.get("streams", [])):
        codec_type = s.get("codec_type")
        if codec_type == "video" and info["width"] == 0:
            info["width"] = s.get("width", 0)
            info["height"] = s.get("height", 0)
            info["fps"] = _fps_from_rate(s.get("avg_frame_rate") or s.get("r_frame_rate") or "0")
        elif codec_type == "audio":
            info["has_audio"] = True
        elif codec_type == "subtitle":
            info["embedded_subtitles"].append({"index": s.get("index", i),
                                               "codec": s.get("codec_name", "")})
    return info


def probe_with_ffmpeg(path: str) -> dict:
    """Fallback when ffprobe binary is unavailable (parses stderr of ffmpeg -i)."""
    res = run([ffmpeg_bin(), "-hide_banner", "-i", path], check=False)
    txt = res.stderr or ""
    info = {"file": path, "duration_sec": 0.0, "width": 0, "height": 0,
            "fps": 0.0, "has_audio": False, "embedded_subtitles": []}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", txt)
    if m:
        h, mm, s = m.groups()
        info["duration_sec"] = round(int(h) * 3600 + int(mm) * 60 + float(s), 3)
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", txt)
    if m:
        info["width"], info["height"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)\s*fps", txt)
    if m:
        info["fps"] = float(m.group(1))
    info["has_audio"] = "Audio:" in txt
    for sm in re.finditer(r"Stream #\d+:\d+.*?Subtitle:\s*(\w+)", txt):
        info["embedded_subtitles"].append({"codec": sm.group(1)})
    return info


def probe(path: str) -> dict:
    if not Path(path).exists():
        raise FileNotFoundError(path)
    if ffprobe_bin():
        return probe_with_ffprobe(path)
    return probe_with_ffmpeg(path)


def main():
    if len(sys.argv) < 2:
        print("usage: python scripts/probe_video.py <video>", file=sys.stderr)
        sys.exit(2)
    path = sys.argv[1]
    info = probe(path)
    nn = Path(path).stem
    save_json(info, TEMP / f"{nn}_probe.json")
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
