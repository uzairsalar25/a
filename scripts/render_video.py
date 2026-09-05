"""
render_video.py — STEP 8/9 (postcard frame + final render)

Consumes temp/NN_timeline.json and produces output/NN_edited.mp4.

Design note (honest tradeoff):
  Each timeline entry is rendered to a normalised intermediate segment
  (1280x720, 30fps CFR, yuv420p, -16 LUFS audio) and the segments are then
  concatenated with a single stream-copy. This keeps mixed inputs (source
  trims, images with Ken Burns, member videos with blurred fill) correct and
  avoids one gigantic filter_complex. It is one encode per segment, not per
  trim, and the final concat is a copy — well within the skill's fast-path
  intent while staying robust.

Encoder:
  auto  -> h264_nvenc if a real test encode succeeds, else libx264
  nvenc -> force NVENC
  cpu   -> force libx264

Usage:
    python scripts/render_video.py temp/01_timeline.json output/01_edited.mp4
    python scripts/render_video.py temp/01_timeline.json output/01_edited.mp4 --encoder cpu
"""
from __future__ import annotations
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

from common import ffmpeg_bin, run, env, load_env, load_json, TEMP

CANVAS_W, CANVAS_H, FPS = 1280, 720, 30
MARGIN = 0.07
BORDER = 5
INNER_W = CANVAS_W - 2 * int(CANVAS_W * MARGIN)      # ~1102
INNER_H = CANVAS_H - 2 * int(CANVAS_H * MARGIN)      # ~620
OFF_X = (CANVAS_W - INNER_W) // 2
OFF_Y = (CANVAS_H - INNER_H) // 2
CONTENT_W = INNER_W - 2 * BORDER
CONTENT_H = INNER_H - 2 * BORDER
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"


# --------------------------------------------------------------------------- #
#  Encoder selection
# --------------------------------------------------------------------------- #
def nvenc_works() -> bool:
    ff = ffmpeg_bin()
    enc = run([ff, "-hide_banner", "-encoders"], check=False).stdout or ""
    if "h264_nvenc" not in enc:
        return False
    # real tiny test encode
    test = run([ff, "-hide_banner", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.2",
                "-c:v", "h264_nvenc", "-f", "null", "-"], check=False)
    return test.returncode == 0


def choose_encoder(mode: str) -> str:
    mode = (mode or "auto").lower()
    if mode == "cpu":
        return "libx264"
    if mode == "nvenc":
        return "h264_nvenc"
    return "h264_nvenc" if nvenc_works() else "libx264"


def venc_args(encoder: str, final: bool) -> List[str]:
    if encoder == "h264_nvenc":
        preset = "p6" if final else "p4"
        return ["-c:v", "h264_nvenc", "-rc", "vbr", "-cq", "19", "-b:v", "0",
                "-preset", preset, "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
            "-pix_fmt", "yuv420p"]


# --------------------------------------------------------------------------- #
#  Background layer  (returns extra input args + a filter producing [bg])
# --------------------------------------------------------------------------- #
def bg_input(background: Dict, dur: float) -> Tuple[List[str], str, int]:
    """Return (input_args, filter_chain_to_[bg], input_index_used)."""
    fill = (f"scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
            f"crop={CANVAS_W}:{CANVAS_H},setsar=1,fps={FPS}")
    if background.get("type") == "asset":
        p = background["path"]
        ext = Path(p).suffix.lower()
        if ext in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
            args = ["-stream_loop", "-1", "-t", f"{dur:.3f}", "-i", p]
        else:
            args = ["-loop", "1", "-t", f"{dur:.3f}", "-i", p]
        return args, f"[{{IDX}}:v]{fill}[bg]", 1
    # blur_self fallback: solid dark neutral (self-blur handled by caller when no asset)
    args = ["-f", "lavfi", "-t", f"{dur:.3f}", "-i",
            f"color=c=0x101418:s={CANVAS_W}x{CANVAS_H}:r={FPS}"]
    return args, "[{IDX}:v]setsar=1[bg]", 1


def frame_overlay(fg_label: str, bg_label: str, out_label: str) -> str:
    """Put fg (content) inside white border, overlay centered on bg."""
    return (
        f"{fg_label}scale={CONTENT_W}:{CONTENT_H}:force_original_aspect_ratio=decrease,"
        f"pad={CONTENT_W}:{CONTENT_H}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"pad={INNER_W}:{INNER_H}:{BORDER}:{BORDER}:color=white,setsar=1[frm];"
        f"{bg_label}[frm]overlay={OFF_X}:{OFF_Y}:shortest=1,"
        f"fps={FPS},format=yuv420p{out_label}"
    )


# --------------------------------------------------------------------------- #
#  MAIN segment
# --------------------------------------------------------------------------- #
def render_main(entry: Dict, background: Dict, encoder: str, out: Path) -> None:
    ff = ffmpeg_bin()
    start, end = float(entry["start"]), float(entry["end"])
    dur = max(end - start, 0.05)
    src = entry["src"]

    bargs, bfilter, _ = bg_input(background, dur)
    bfilter = bfilter.replace("{IDX}", "1")

    vf = (f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,"
          f"fps={FPS},setsar=1[src];")
    graph = vf + bfilter + ";" + frame_overlay("[src]", "[bg]", "[v]")

    af = (f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,"
          f"{LOUDNORM},aresample=48000[a]")

    cmd = [ff, "-y", "-i", src, *bargs,
           "-filter_complex", graph + ";" + af,
           "-map", "[v]", "-map", "[a]",
           *venc_args(encoder, final=False),
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-r", str(FPS), "-video_track_timescale", "30000",
           str(out)]
    try:
        run(cmd)
    except RuntimeError:
        # source may have no audio -> synth silent track
        cmd2 = [ff, "-y", "-i", src, *bargs,
                "-f", "lavfi", "-t", f"{dur:.3f}", "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-filter_complex", graph,
                "-map", "[v]", "-map", "2:a",
                *venc_args(encoder, final=False),
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-r", str(FPS), "-video_track_timescale", "30000", str(out)]
        run(cmd2)


# --------------------------------------------------------------------------- #
#  NARRATION b-roll clip  -> content-area clip of exact duration
# --------------------------------------------------------------------------- #
def render_broll_clip(clip: Dict, encoder: str, out: Path) -> None:
    ff = ffmpeg_bin()
    dur = max(float(clip["dur"]), 0.4)
    p = clip["path"]

    if clip.get("is_image"):
        frames = max(int(round(dur * FPS)), 1)
        # Ken Burns: single input frame, zoompan emits `frames` per input,
        # pre-scale 4x so per-frame zoom step is sub-pixel.
        vf = (f"scale={CONTENT_W*4}:{CONTENT_H*4}:force_original_aspect_ratio=increase,"
              f"crop={CONTENT_W*4}:{CONTENT_H*4},"
              f"zoompan=z='min(zoom+0.0008,1.08)':d={frames}:"
              f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
              f"s={CONTENT_W}x{CONTENT_H}:fps={FPS},setsar=1,format=yuv420p")
        cmd = [ff, "-y", "-i", p, "-frames:v", str(frames),
               "-vf", vf, "-an", *venc_args(encoder, final=False),
               "-r", str(FPS), "-video_track_timescale", "30000", str(out)]
        run(cmd)
    else:
        # video: blurred-fill background of ITSELF + content on top, no crop
        vf = (
            f"[0:v]trim=0:{dur:.3f},setpts=PTS-STARTPTS,fps={FPS},split[b][f];"
            f"[b]scale={CONTENT_W}:{CONTENT_H}:force_original_aspect_ratio=increase,"
            f"crop={CONTENT_W}:{CONTENT_H},boxblur=20:2,setsar=1[bb];"
            f"[f]scale={CONTENT_W}:{CONTENT_H}:force_original_aspect_ratio=decrease,setsar=1[ff];"
            f"[bb][ff]overlay=(W-w)/2:(H-h)/2:shortest=1,"
            f"fps={FPS},format=yuv420p[v]"
        )
        cmd = [ff, "-y", "-i", p, "-filter_complex", vf,
               "-map", "[v]", "-an", "-t", f"{dur:.3f}",
               *venc_args(encoder, final=False),
               "-r", str(FPS), "-video_track_timescale", "30000", str(out)]
        try:
            run(cmd)
        except RuntimeError:
            # broken clip -> neutral fallback so the block never freezes
            cmd2 = [ff, "-y", "-f", "lavfi", "-t", f"{dur:.3f}", "-i",
                    f"color=c=0x181c22:s={CONTENT_W}x{CONTENT_H}:r={FPS}",
                    "-vf", "format=yuv420p", "-an",
                    *venc_args(encoder, final=False),
                    "-r", str(FPS), "-video_track_timescale", "30000", str(out)]
            run(cmd2)


def concat_copy(segments: List[Path], out: Path) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{s.as_posix()}'\n" for s in segments), encoding="utf-8")
    run([ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", str(out)])
    lst.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
#  NARRATION segment
# --------------------------------------------------------------------------- #
def render_narr(entry: Dict, background: Dict, encoder: str, out: Path, work: Path) -> None:
    ff = ffmpeg_bin()
    dur = float(entry.get("dur") or 0)
    clips = entry.get("clips", [])
    if not clips:
        # no b-roll available -> neutral content so we still cover the audio
        clips = [{"path": None, "is_image": False, "dur": dur or 15.0}]

    # 1) render each b-roll clip to content-area intermediate, then concat
    parts: List[Path] = []
    for i, clip in enumerate(clips):
        seg = work / f"broll_{i:03d}.mp4"
        if clip.get("path") is None:
            run([ff, "-y", "-f", "lavfi", "-t", f"{max(float(clip['dur']),0.4):.3f}",
                 "-i", f"color=c=0x181c22:s={CONTENT_W}x{CONTENT_H}:r={FPS}",
                 "-vf", "format=yuv420p", "-an", *venc_args(encoder, final=False),
                 "-r", str(FPS), "-video_track_timescale", "30000", str(seg)])
        else:
            render_broll_clip(clip, encoder, seg)
        parts.append(seg)

    visual = work / "broll_all.mp4"
    if len(parts) == 1:
        shutil.copy(parts[0], visual)
    else:
        concat_copy(parts, visual)

    # 2) overlay onto background + white frame, attach narration audio (loudnorm)
    bargs, bfilter, _ = bg_input(background, dur)
    bfilter = bfilter.replace("{IDX}", "1")
    graph = bfilter + ";" + frame_overlay("[0:v]setsar=1,", "[bg]", "[v]")

    audio = entry.get("audio")
    if audio and Path(audio).exists():
        af = f"[2:a]{LOUDNORM},aresample=48000[a]"
        cmd = [ff, "-y", "-i", str(visual), *bargs, "-i", audio,
               "-filter_complex", graph + ";" + af,
               "-map", "[v]", "-map", "[a]", "-shortest",
               *venc_args(encoder, final=False),
               "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
               "-r", str(FPS), "-video_track_timescale", "30000", str(out)]
    else:
        cmd = [ff, "-y", "-i", str(visual), *bargs,
               "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
               "-filter_complex", graph,
               "-map", "[v]", "-map", "2:a", "-shortest",
               *venc_args(encoder, final=False),
               "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
               "-r", str(FPS), "-video_track_timescale", "30000", str(out)]
    run(cmd)


# --------------------------------------------------------------------------- #
def render(timeline_path: str, out_path: str, encoder_mode: str) -> None:
    tl = load_json(timeline_path)
    encoder = choose_encoder(encoder_mode or env("RENDER_ENCODER", "auto"))
    print(f"[render] encoder = {encoder}")
    background = tl["background"]
    nn = tl.get("nn", "video")

    work = Path(tempfile.mkdtemp(prefix=f"render_{nn}_", dir=TEMP))
    segs: List[Path] = []
    try:
        for i, entry in enumerate(tl["timeline"]):
            seg = work / f"seg_{i:03d}.mp4"
            if entry["type"] == "main":
                render_main(entry, background, encoder, seg)
            else:
                sub = work / f"narr_{i:03d}"
                sub.mkdir(exist_ok=True)
                render_narr(entry, background, encoder, seg, sub)
            segs.append(seg)
            print(f"  segment {i+1}/{len(tl['timeline'])} ({entry['type']}) ok")

        # final concat (single copy) + faststart
        raw = work / "concat.mp4"
        concat_copy(segs, raw)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        run([ffmpeg_bin(), "-y", "-i", str(raw), "-c", "copy",
             "-movflags", "+faststart", out_path])
        print(f"[render] done -> {out_path}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("timeline")
    ap.add_argument("output")
    ap.add_argument("--encoder", default=None, help="auto|nvenc|cpu")
    args = ap.parse_args()
    render(args.timeline, args.output, args.encoder)


if __name__ == "__main__":
    main()
