"""
common.py — Shared helpers for the Royal News editing pipeline.

- ffmpeg / ffprobe binary discovery (system first, then imageio-ffmpeg fallback)
- .env loading
- SRT parsing helpers
- simple logging to output/errors.log & output/progress.log
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
TEMP = ROOT / "temp"
OUTPUT = ROOT / "output"
INPUT = ROOT / "input_videos"
ASSETS = ROOT / "assets"

TEMP.mkdir(exist_ok=True)
OUTPUT.mkdir(exist_ok=True)


# --------------------------------------------------------------------------- #
#  .env loading
# --------------------------------------------------------------------------- #
def load_env() -> None:
    """Load ROOT/.env into os.environ (no external dep required)."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        # do not overwrite an already-exported value
        os.environ.setdefault(key, val)


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


# --------------------------------------------------------------------------- #
#  ffmpeg / ffprobe discovery
# --------------------------------------------------------------------------- #
def _imageio_ffmpeg() -> Optional[str]:
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or _imageio_ffmpeg() or "ffmpeg"


def ffprobe_bin() -> str:
    """ffprobe: system first. imageio ships only ffmpeg, so if ffprobe is
    missing we return '' and callers fall back to ffmpeg-based probing."""
    return shutil.which("ffprobe") or ""


def run(cmd: List[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess with clean error surfacing."""
    res = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )
    if check and res.returncode != 0:
        err = (res.stderr or "")[-2000:]
        raise RuntimeError(f"Command failed ({res.returncode}): {' '.join(cmd[:3])} ...\n{err}")
    return res


# --------------------------------------------------------------------------- #
#  Logging
# --------------------------------------------------------------------------- #
def log_error(msg: str) -> None:
    (OUTPUT / "errors.log").open("a", encoding="utf-8").write(msg.rstrip() + "\n")
    print(f"[ERROR] {msg}", file=sys.stderr)


def log_progress(msg: str) -> None:
    (OUTPUT / "progress.log").open("a", encoding="utf-8").write(msg.rstrip() + "\n")
    print(f"[OK] {msg}")


# --------------------------------------------------------------------------- #
#  SRT parsing
# --------------------------------------------------------------------------- #
@dataclass
class Cue:
    idx: int
    start: float   # seconds
    end: float     # seconds
    text: str

    def to_dict(self):
        return asdict(self)


_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


def _ts_to_sec(ts: str) -> float:
    m = _TS.search(ts)
    if not m:
        return 0.0
    h, mm, s, ms = map(int, m.groups())
    return h * 3600 + mm * 60 + s + ms / 1000.0


def sec_to_ts(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(path: str | Path) -> List[Cue]:
    """Parse an .srt file into a list of Cue objects."""
    raw = Path(path).read_text(encoding="utf-8", errors="ignore")
    blocks = re.split(r"\n\s*\n", raw.strip())
    cues: List[Cue] = []
    for b in blocks:
        lines = [ln for ln in b.splitlines() if ln.strip() != ""]
        if not lines:
            continue
        # find the timing line
        timing_i = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if timing_i is None:
            continue
        start_s, _, end_s = lines[timing_i].partition("-->")
        idx = 0
        if timing_i > 0 and lines[0].strip().isdigit():
            idx = int(lines[0].strip())
        text = " ".join(lines[timing_i + 1:]).strip()
        cues.append(Cue(idx, _ts_to_sec(start_s), _ts_to_sec(end_s), text))
    return cues


def write_srt(cues: List[Cue], path: str | Path) -> None:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{sec_to_ts(c.start)} --> {sec_to_ts(c.end)}\n{c.text}\n")
    Path(path).write_text("\n".join(out), encoding="utf-8")


# --------------------------------------------------------------------------- #
#  Sentence-boundary helpers (used for safe cuts)
# --------------------------------------------------------------------------- #
_SENT_END = re.compile(r"[.!?…]['\"”’)]?\s*$")


def cue_ends_sentence(c: Cue) -> bool:
    return bool(_SENT_END.search(c.text.strip()))


def nearest_safe_end(cues: List[Cue], target: float) -> float:
    """Return the end time of the last cue that finishes a sentence at or
    before `target`. Falls back to the nearest cue end."""
    best = None
    for c in cues:
        if c.end <= target + 0.01 and cue_ends_sentence(c):
            best = c.end
    if best is not None:
        return best
    # fallback: last cue end before target
    prev = [c.end for c in cues if c.end <= target + 0.01]
    return prev[-1] if prev else target


def nearest_safe_start(cues: List[Cue], target: float) -> float:
    """Return the start time of the first cue whose start is at/after target."""
    for c in cues:
        if c.start >= target - 0.01:
            return c.start
    return target


def save_json(obj, path: str | Path) -> None:
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def load_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    load_env()
    print("ROOT       :", ROOT)
    print("ffmpeg     :", ffmpeg_bin())
    print("ffprobe    :", ffprobe_bin() or "(missing — will use ffmpeg fallback)")
    print("WHISPER    :", env("WHISPER_MODEL", "small"))
    print("TTS_ENGINE :", env("TTS_ENGINE", "edge"))
