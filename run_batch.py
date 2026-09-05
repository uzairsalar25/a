#!/usr/bin/env python3
"""
run_batch.py — FULL AUTO ORCHESTRATOR

One command edits & renders a range of numbered videos end-to-end:

    python run_batch.py 1 5          # videos 01..05
    python run_batch.py 3            # only video 03
    python run_batch.py 3 9 --encoder cpu

Pipeline per video (never asks questions; logs + skips on failure):
    probe -> subtitles -> EDL -> narration TTS -> timeline (+b-roll)
    -> render -> metadata -> progress log
"""
from __future__ import annotations
import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from common import (load_env, INPUT, TEMP, OUTPUT, log_error, log_progress,  # noqa: E402
                    save_json, load_json)
import probe_video          # noqa: E402
import transcribe           # noqa: E402
import build_edl            # noqa: E402
import tts                  # noqa: E402
import build_timeline       # noqa: E402
import render_video         # noqa: E402
import gen_metadata         # noqa: E402

# Where to read source videos from. Default = project's input_videos/.
# Overridden by --input so users can point at their existing data folder
# (e.g. D:\RoyalData\videos) WITHOUT copying gigabytes into the project.
INPUT_DIR = INPUT


def find_video(nn: str) -> Path | None:
    # match 01.mp4 exactly, and also "01 - anything.mp4" / "01_anything.mp4"
    for ext in (".mp4", ".mov", ".mkv", ".webm", ".m4v"):
        p = INPUT_DIR / f"{nn}{ext}"
        if p.exists():
            return p
    for p in sorted(INPUT_DIR.glob(f"{nn}*")):
        if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm", ".m4v"):
            return p
    return None


def process_one(nn: str, encoder: str) -> bool:
    video = find_video(nn)
    if not video:
        log_error(f"{nn}: no input video found in input_videos/")
        return False
    t0 = time.time()
    print(f"\n========== VIDEO {nn} ({video.name}) ==========")

    # STEP 1 — probe + subtitles
    info = probe_video.probe(str(video))
    save_json(info, TEMP / f"{nn}_probe.json")
    srt = TEMP / f"{nn}.srt"
    if not transcribe.get_subtitles(str(video), srt):
        log_error(f"{nn}: subtitles failed — skipping")
        return False

    # STEP 2/3/4 — EDL (story analysis, hook, narration text)
    edl = build_edl.build_edl(str(srt), str(video))
    save_json(edl, TEMP / f"{nn}_edl.json")
    print(f"  EDL: retention={edl['_retention_ratio']} "
          f"blocks={len(edl['_main_blocks'])} narr={len(edl['narration_blocks'])}")

    # STEP 5 — TTS for each narration block
    for i, nb in enumerate(edl["narration_blocks"], 1):
        txt_path = TEMP / f"{nn}_narr_{i}.txt"
        txt_path.write_text(nb["text"], encoding="utf-8")
        mp3 = TEMP / f"{nn}_narr_{i}.mp3"
        ok = False
        for attempt in range(2):
            try:
                if tts.synthesize(nb["text"], str(mp3)):
                    ok = True
                    break
            except Exception as e:
                log_error(f"{nn}: TTS narr {i} attempt {attempt+1}: {e}")
        if not ok:
            log_error(f"{nn}: TTS failed for narration {i} — skipping video")
            return False
        print(f"  narr {i}: {tts.audio_duration(str(mp3)):.1f}s ({nb['position']})")

    # STEP 6/7 — timeline + b-roll
    tl = build_timeline.build_timeline(nn)
    save_json(tl, TEMP / f"{nn}_timeline.json")

    # STEP 9 — render
    out = OUTPUT / f"{nn}_edited.mp4"
    render_video.render(str(TEMP / f"{nn}_timeline.json"), str(out), encoder)

    # STEP 10 — metadata (failure must not invalidate the render)
    try:
        gen_metadata.generate(nn)
    except Exception as e:
        log_error(f"{nn}: metadata failed (render still OK): {e}")

    dur_in = info["duration_sec"]
    hook = edl["hook"]
    log_progress(f"{nn} | OK | in={dur_in/60:.1f}m | "
                 f"hook={hook['start']:.0f}-{hook['end']:.0f}s | "
                 f"narr_blocks={len(edl['narration_blocks'])} | "
                 f"took={time.time()-t0:.0f}s")
    return True


def parse_range(a: str, b: str | None) -> list[str]:
    start = int(a)
    end = int(b) if b is not None else start
    return [f"{n:02d}" for n in range(start, end + 1)]


def main():
    load_env()
    ap = argparse.ArgumentParser(description="Royal News full-auto batch editor")
    ap.add_argument("start", help="first video number, e.g. 1")
    ap.add_argument("end", nargs="?", default=None, help="last video number (optional)")
    ap.add_argument("--encoder", default=None, help="auto|nvenc|cpu")
    ap.add_argument("--input", default=None,
                    help="apne videos ka folder path (default: input_videos/)")
    args = ap.parse_args()

    global INPUT_DIR
    if args.input:
        INPUT_DIR = Path(args.input).expanduser()
        if not INPUT_DIR.is_dir():
            print(f"[ERROR] --input folder nahi mila: {INPUT_DIR}")
            sys.exit(2)
        print(f"Input folder: {INPUT_DIR}")

    numbers = parse_range(args.start, args.end)
    print(f"Batch: {numbers}")
    done, failed = [], []
    for nn in numbers:
        try:
            (done if process_one(nn, args.encoder) else failed).append(nn)
        except Exception as e:
            log_error(f"{nn}: UNEXPECTED {e}\n{traceback.format_exc()}")
            failed.append(nn)

    print("\n================= SUMMARY =================")
    print(f"  DONE  : {', '.join(done) or '-'}")
    print(f"  FAILED: {', '.join(failed) or '-'}")
    print("  (details in output/progress.log and output/errors.log)")


if __name__ == "__main__":
    main()
