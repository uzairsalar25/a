"""
build_timeline.py — STEP 6/7 (B-roll selection + timeline assembly)

Reads temp/NN_edl.json + the generated narration mp3s and produces
temp/NN_timeline.json — an ordered list of "main" and "narr" entries the
renderer consumes.

Rules honoured:
  - one background/grid asset chosen for the WHOLE video
  - each narration uses only its members' folder(s); no unrelated substitution
  - no B-roll file reused anywhere in the same video (per-video used set)
  - images interleaved among videos; clip lengths jittered to cover narration
  - no freeze padding — enough unique clips are allocated to cover the audio

Usage:
    python scripts/build_timeline.py 01
    (expects temp/01_edl.json and temp/01_narr_*.mp3 to exist)
"""
from __future__ import annotations
import random
import sys
from pathlib import Path
from typing import Dict, List

from common import ASSETS, TEMP, INPUT, load_json, save_json, ffmpeg_bin, run

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
CLIP_MIN, CLIP_MAX = 2.0, 4.0   # seconds per visual


def audio_dur(path: str) -> float:
    import re
    res = run([ffmpeg_bin(), "-hide_banner", "-i", path], check=False)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", res.stderr or "")
    if not m:
        return 0.0
    h, mm, s = m.groups()
    return int(h) * 3600 + int(mm) * 60 + float(s)


def list_media(folder: Path) -> List[Path]:
    if not folder.exists():
        return []
    return [p for p in folder.iterdir()
            if p.suffix.lower() in IMAGE_EXT | VIDEO_EXT]


def pick_background() -> Dict:
    """Choose exactly ONE background/grid for the whole video."""
    pool = list_media(ASSETS / "backgrounds") + list_media(ASSETS / "grids")
    pool = [p for p in pool if p.suffix.lower() in IMAGE_EXT | VIDEO_EXT]
    if pool:
        chosen = random.choice(pool)
        return {"type": "asset", "path": str(chosen)}
    return {"type": "blur_self"}   # fallback: blurred copy of source frame


def collect_pool(members: List[str], used: set) -> List[Path]:
    """Gather unused media from the given member folders, shuffled, images
    interleaved among videos."""
    videos, images = [], []
    for m in members:
        for p in list_media(ASSETS / "member_clips" / m):
            if str(p) in used:
                continue
            (images if p.suffix.lower() in IMAGE_EXT else videos).append(p)
    random.shuffle(videos)
    random.shuffle(images)
    # interleave: roughly one image every ~3 videos
    out: List[Path] = []
    vi = ii = 0
    while vi < len(videos) or ii < len(images):
        for _ in range(3):
            if vi < len(videos):
                out.append(videos[vi]); vi += 1
        if ii < len(images):
            out.append(images[ii]); ii += 1
    return out


def allocate_clips(members: List[str], used: set, need: float) -> List[Dict]:
    """Pick unique clips to cover `need` seconds; jitter lengths 2-4s."""
    pool = collect_pool(members, used)
    clips: List[Dict] = []
    filled = 0.0
    for p in pool:
        if filled >= need:
            break
        length = round(random.uniform(CLIP_MIN, CLIP_MAX), 2)
        length = min(length, max(need - filled, 0.6))
        used.add(str(p))
        clips.append({
            "path": str(p),
            "is_image": p.suffix.lower() in IMAGE_EXT,
            "dur": length,
        })
        filled += length
    # normalise so clip durations sum exactly to `need` (no freeze padding)
    if clips:
        total = sum(c["dur"] for c in clips)
        if total > 0:
            scale = need / total
            for c in clips:
                c["dur"] = round(c["dur"] * scale, 3)
    return clips


def build_timeline(nn: str) -> Dict:
    edl = load_json(TEMP / f"{nn}_edl.json")
    src = str(INPUT / edl["video"])
    blocks = edl.get("_main_blocks") or [[s] for s in edl["keep_segments"]]
    narrs = edl["narration_blocks"]
    background = pick_background()
    used: set = set()

    timeline: List[Dict] = []
    # 1) HOOK (cold open) as first main piece
    hook = edl["hook"]
    timeline.append({"type": "main", "src": src,
                     "start": hook["start"], "end": hook["end"]})

    # index narrations by position
    after_hook = next((n for n in narrs if n["position"] == "after_hook"), None)
    mids = [n for n in narrs if n["position"] == "mid"]
    closing = next((n for n in narrs if n["position"] == "closing"), None)

    def narr_entry(nblock, i):
        mp3 = TEMP / f"{nn}_narr_{i}.mp3"
        need = audio_dur(str(mp3)) if mp3.exists() else 20.0
        clips = allocate_clips(nblock.get("members", ["general_royal"]), used, need)
        return {"type": "narr", "audio": str(mp3), "dur": round(need, 3),
                "members": nblock.get("members", []), "clips": clips}

    narr_i = 1
    # 2) after-hook narration
    if after_hook:
        timeline.append(narr_entry(after_hook, narr_i)); narr_i += 1

    # 3) blocks with mid narrations between them
    for bi, block in enumerate(blocks):
        for seg in block:
            timeline.append({"type": "main", "src": src,
                             "start": seg["start"], "end": seg["end"]})
        # mid narration after each block except the last
        if bi < len(blocks) - 1 and (bi) < len(mids):
            timeline.append(narr_entry(mids[bi], narr_i)); narr_i += 1

    # 4) closing narration
    if closing:
        timeline.append(narr_entry(closing, narr_i)); narr_i += 1

    return {
        "video": edl["video"],
        "nn": nn,
        "background": background,
        "target_w": 1280, "target_h": 720, "fps": 30,
        "timeline": timeline,
    }


def main():
    if len(sys.argv) < 2:
        print("usage: python scripts/build_timeline.py <NN>", file=sys.stderr)
        sys.exit(2)
    nn = sys.argv[1]
    tl = build_timeline(nn)
    save_json(tl, TEMP / f"{nn}_timeline.json")
    n_main = sum(1 for x in tl["timeline"] if x["type"] == "main")
    n_narr = sum(1 for x in tl["timeline"] if x["type"] == "narr")
    print(f"timeline -> temp/{nn}_timeline.json  (main={n_main}, narr={n_narr}, "
          f"bg={tl['background']['type']})")


if __name__ == "__main__":
    main()
