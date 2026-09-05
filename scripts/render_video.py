#!/usr/bin/env python3
"""STEP 9 renderer: assemble the timeline into the final 1280x720/30fps video.

Usage:
    python scripts/render_video.py temp/NN_timeline.json output/NN_edited.mp4 \
        [--encoder auto|nvenc|cpu]

The renderer performs ONE final FFmpeg encode (per FAST-PATH rules). It:
  * places every segment inside the postcard frame over ONE background asset;
  * pads main-video trims inside the frame (letterbox), while narration
    B-roll clips are shown over a blurred-fill copy of themselves;
  * normalises both source and narration audio to -16 LUFS;
  * resets timestamps per segment (no freeze-frame / tpad clone);
  * guarantees narration visuals exactly cover the narration audio.

Timeline JSON (object form, preferred):
{
  "background": "assets/backgrounds/bg1.jpg",   // optional; auto-picked if absent
  "frame": {"margin": 0.065, "border_px": 5},   // optional overrides
  "segments": [
    {"type": "main", "src": "input_videos/03.mp4", "start": 0.0, "end": 28.4},
    {"type": "narr", "audio": "temp/03_narr_1.mp3",
     "clips": ["assets/member_clips/harry/a.mp4", ".../b.mp4"]},
    ...
  ]
}
A bare list of segments is also accepted (background auto-picked).
Each narration clip may be a string path or {"path": ..., "in": 0.0}.
"""

import argparse
import glob
import json
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ffcommon import (  # noqa: E402
    load_env, run, media_duration, probe_summary, is_image, eprint,
)

CANVAS_W, CANVAS_H = 1280, 720
FPS = 30
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
AFMT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


def even(n):
    n = int(round(n))
    return n - (n % 2)


def frame_geometry(cfg):
    """Return outer white-frame rect and inner content rect (all even)."""
    margin = float(cfg.get("margin", 0.065))
    border = int(cfg.get("border_px", 5))
    fx = even(CANVAS_W * margin)
    fy = even(CANVAS_H * margin)
    fw = even(CANVAS_W - 2 * fx)
    fh = even(CANVAS_H - 2 * fy)
    ix = fx + border
    iy = fy + border
    wc = even(fw - 2 * border)
    hc = even(fh - 2 * border)
    return {
        "fx": fx, "fy": fy, "fw": fw, "fh": fh,
        "ix": ix, "iy": iy, "wc": wc, "hc": hc, "border": border,
    }


def pick_background(explicit):
    """Resolve the single background asset for the whole video."""
    if explicit and os.path.exists(explicit):
        return explicit
    pool = []
    for folder in ("assets/backgrounds", "assets/grids"):
        pool.extend(sorted(glob.glob(os.path.join(folder, "*"))))
    pool = [p for p in pool if os.path.isfile(p)]
    if pool:
        return pool[0]
    return None  # caller falls back to a blurred source frame


def nvenc_works():
    """Verify h264_nvenc is present AND can actually encode a tiny clip."""
    try:
        enc = run(["ffmpeg", "-hide_banner", "-encoders"],
                  check=False, capture=True)
        if "h264_nvenc" not in (enc.stdout or ""):
            return False
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            out = tmp.name
        test = run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1",
            "-c:v", "h264_nvenc", "-f", "mp4", out,
        ], check=False, capture=True)
        ok = test.returncode == 0
        try:
            os.remove(out)
        except OSError:
            pass
        return ok
    except Exception:  # noqa: BLE001
        return False


def encoder_args(encoder, final=True):
    if encoder == "nvenc":
        preset = "p6" if final else "p4"
        return ["-c:v", "h264_nvenc", "-rc", "vbr", "-cq", "19",
                "-b:v", "0", "-preset", preset, "-pix_fmt", "yuv420p"]
    # libx264 fallback / CPU
    return ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
            "-pix_fmt", "yuv420p"]


def distribute_durations(total, count, seed):
    """Split `total` seconds across `count` clips with 2-4s jitter, summing
    exactly to total (last clip trimmed to close any rounding gap)."""
    if count <= 0:
        return []
    rng = random.Random(seed)
    base = total / count
    raw = [max(0.4, base * (1.0 + rng.uniform(-0.25, 0.25))) for _ in range(count)]
    scale = total / sum(raw)
    durs = [round(d * scale, 3) for d in raw]
    # Fix rounding so the sum is exact.
    durs[-1] = round(total - sum(durs[:-1]), 3)
    return durs


class GraphBuilder:
    def __init__(self, geo):
        self.geo = geo
        self.inputs = []       # list of ffmpeg input arg-lists (each incl. -i)
        self.filters = []      # filter_complex chain strings
        self.seg_v = []        # per-segment video labels
        self.seg_a = []        # per-segment audio labels
        self.total = 0.0

    def add_input(self, args):
        idx = len(self.inputs)
        self.inputs.append(args)
        return idx

    # -- main source segment -------------------------------------------------
    def add_main(self, seg, seg_no):
        src = seg["src"]
        start = float(seg["start"])
        end = float(seg["end"])
        dur = round(end - start, 3)
        if dur <= 0:
            raise ValueError("main segment %d has non-positive duration" % seg_no)
        self.total += dur

        vi = self.add_input(["-i", src])
        wc, hc = self.geo["wc"], self.geo["hc"]
        vlabel = "sv%d" % seg_no
        self.filters.append(
            "[%d:v]trim=%.3f:%.3f,setpts=PTS-STARTPTS,fps=%d,"
            "scale=%d:%d:force_original_aspect_ratio=decrease,"
            "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[%s]"
            % (vi, start, end, FPS, wc, hc, wc, hc, vlabel)
        )
        self.seg_v.append(vlabel)

        alabel = "sa%d" % seg_no
        info = probe_summary(src)
        if info.get("has_audio"):
            self.filters.append(
                "[%d:a]atrim=%.3f:%.3f,asetpts=PTS-STARTPTS,%s,%s[%s]"
                % (vi, start, end, AFMT, LOUDNORM, alabel)
            )
        else:
            si = self.add_input(["-f", "lavfi", "-t", "%.3f" % dur,
                                 "-i", "anullsrc=r=48000:cl=stereo"])
            self.filters.append("[%d:a]%s[%s]" % (si, AFMT, alabel))
        self.seg_a.append(alabel)

    # -- narration segment ---------------------------------------------------
    def add_narr(self, seg, seg_no):
        audio = seg["audio"]
        clips = seg.get("clips", [])
        if not clips:
            raise ValueError("narr segment %d has no B-roll clips" % seg_no)
        adur = media_duration(audio)
        if adur <= 0:
            raise ValueError("narration audio %s has zero duration" % audio)
        self.total += adur

        durs = distribute_durations(adur, len(clips), seed=seg_no)
        wc, hc = self.geo["wc"], self.geo["hc"]
        clip_labels = []
        for ci, (clip, cd) in enumerate(zip(clips, durs)):
            path = clip["path"] if isinstance(clip, dict) else clip
            cin = float(clip.get("in", 0.0)) if isinstance(clip, dict) else 0.0
            lbl = "n%d_%d" % (seg_no, ci)
            if is_image(path):
                idx = self.add_input(["-loop", "1", "-t", "%.3f" % cd, "-i", path])
                frames = max(1, int(cd * FPS))
                self.filters.append(
                    "[%d:v]scale=%d:-1:force_original_aspect_ratio=increase,"
                    "crop=%d:%d,zoompan=z='min(zoom+0.0006,1.05)':d=%d:"
                    "s=%dx%d:fps=%d,setsar=1[%s]"
                    % (idx, wc, wc, hc, frames, wc, hc, FPS, lbl)
                )
            else:
                idx = self.add_input(["-i", path])
                # blurred-fill background + fitted foreground (no crop of subject)
                self.filters.append(
                    "[%d:v]trim=%.3f:%.3f,setpts=PTS-STARTPTS,fps=%d,split=2[%s_a][%s_b];"
                    "[%s_a]scale=%d:%d:force_original_aspect_ratio=increase,"
                    "crop=%d:%d,boxblur=18:2[%s_bg];"
                    "[%s_b]scale=%d:%d:force_original_aspect_ratio=decrease[%s_fg];"
                    "[%s_bg][%s_fg]overlay=(W-w)/2:(H-h)/2,setsar=1[%s]"
                    % (idx, cin, cin + cd, FPS, lbl, lbl,
                       lbl, wc, hc, wc, hc, lbl,
                       lbl, wc, hc, lbl,
                       lbl, lbl, lbl)
                )
            clip_labels.append(lbl)

        # Concatenate the clips, then trim to the exact narration duration so
        # visuals and audio end together (never freeze-pad to fill).
        vlabel = "sv%d" % seg_no
        concat_in = "".join("[%s]" % c for c in clip_labels)
        self.filters.append(
            "%sconcat=n=%d:v=1:a=0[%s_raw];[%s_raw]trim=0:%.3f,setpts=PTS-STARTPTS[%s]"
            % (concat_in, len(clip_labels), vlabel, vlabel, adur, vlabel)
        )
        self.seg_v.append(vlabel)

        ai = self.add_input(["-i", audio])
        alabel = "sa%d" % seg_no
        self.filters.append("[%d:a]%s,%s[%s]" % (ai, AFMT, LOUDNORM, alabel))
        self.seg_a.append(alabel)

    # -- background + final composite ---------------------------------------
    def finalize(self, background):
        g = self.geo
        # Background input (index depends on order; add it LAST but reference
        # by its real index). We add it now and capture the index.
        if background is None:
            # Fallback: heavily blurred copy of the first main segment frame.
            first_main = None
            for lbl in self.seg_v:
                first_main = lbl
                break
            bg_src = self._fallback_source()
            bgi = self.add_input(["-i", bg_src])
            bg_chain = (
                "[%d:v]scale=%d:%d:force_original_aspect_ratio=increase,"
                "crop=%d:%d,boxblur=30:3,setsar=1,fps=%d[bg]"
                % (bgi, CANVAS_W, CANVAS_H, CANVAS_W, CANVAS_H, FPS)
            )
        elif is_image(background):
            bgi = self.add_input(["-loop", "1", "-t", "%.3f" % self.total,
                                  "-i", background])
            bg_chain = (
                "[%d:v]scale=%d:%d:force_original_aspect_ratio=increase,"
                "crop=%d:%d,setsar=1,fps=%d[bg]"
                % (bgi, CANVAS_W, CANVAS_H, CANVAS_W, CANVAS_H, FPS)
            )
        else:
            bgi = self.add_input(["-stream_loop", "-1", "-t", "%.3f" % self.total,
                                  "-i", background])
            bg_chain = (
                "[%d:v]scale=%d:%d:force_original_aspect_ratio=increase,"
                "crop=%d:%d,setsar=1,fps=%d[bg]"
                % (bgi, CANVAS_W, CANVAS_H, CANVAS_W, CANVAS_H, FPS)
            )
        self.filters.append(bg_chain)

        # Concatenate all inner segments (video then audio).
        v_in = "".join("[%s]" % v for v in self.seg_v)
        a_in = "".join("[%s]" % a for a in self.seg_a)
        n = len(self.seg_v)
        self.filters.append("%sconcat=n=%d:v=1:a=0[innerv]" % (v_in, n))
        self.filters.append("%sconcat=n=%d:v=0:a=1[outa]" % (a_in, n))

        # White frame (filled box) then inner content overlaid inside border.
        self.filters.append(
            "[bg]drawbox=x=%d:y=%d:w=%d:h=%d:color=white@1.0:t=fill[framed];"
            "[framed][innerv]overlay=x=%d:y=%d:shortest=1[outv]"
            % (g["fx"], g["fy"], g["fw"], g["fh"], g["ix"], g["iy"])
        )
        return bgi

    def _fallback_source(self):
        # Used only when no background asset exists: reuse the first main src.
        for args in self.inputs:
            # args like ["-i", path] for a plain video input
            if args[0] == "-i" and not is_image(args[1]):
                return args[1]
        raise RuntimeError("no source available for background fallback")


def build_and_render(timeline, out_path, encoder):
    if isinstance(timeline, list):
        segments = timeline
        cfg = {}
        background = None
    else:
        segments = timeline["segments"]
        cfg = timeline.get("frame", {})
        background = timeline.get("background")

    geo = frame_geometry(cfg)
    builder = GraphBuilder(geo)

    for i, seg in enumerate(segments):
        stype = seg.get("type")
        if stype == "main":
            builder.add_main(seg, i)
        elif stype == "narr":
            builder.add_narr(seg, i)
        else:
            raise ValueError("segment %d has unknown type %r" % (i, stype))

    background = pick_background(background)
    builder.finalize(background)

    filter_complex = ";".join(builder.filters)

    # Assemble the input args in order.
    in_args = []
    for args in builder.inputs:
        in_args.extend(args)

    def make_cmd(enc):
        return (
            ["ffmpeg", "-y"] + in_args +
            ["-filter_complex", filter_complex,
             "-map", "[outv]", "-map", "[outa]"] +
            encoder_args(enc, final=True) +
            ["-r", str(FPS), "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", out_path]
        )

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    if encoder == "auto":
        encoder = "nvenc" if nvenc_works() else "cpu"
    if encoder == "nvenc":
        print("[render] encoder: h264_nvenc")
        proc = run(make_cmd("nvenc"), check=False)
        if proc.returncode == 0:
            return 0
        eprint("[render] NVENC failed at runtime; falling back to libx264")
        encoder = "cpu"
    print("[render] encoder: libx264")
    run(make_cmd("cpu"), check=True)
    return 0


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("timeline")
    parser.add_argument("output")
    parser.add_argument("--encoder", choices=["auto", "nvenc", "cpu"],
                        default="auto")
    args = parser.parse_args(argv[1:])

    load_env()
    if not os.path.exists(args.timeline):
        eprint("error: timeline not found: %s" % args.timeline)
        return 1
    with open(args.timeline, "r", encoding="utf-8") as fh:
        timeline = json.load(fh)
    try:
        return build_and_render(timeline, args.output, args.encoder)
    except Exception as exc:  # noqa: BLE001
        eprint("error: render failed: %s" % exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
