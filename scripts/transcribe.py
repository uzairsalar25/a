"""
transcribe.py — STEP 1b (subtitles)

Subtitle priority (per SKILL.md):
  a) embedded subtitle stream  -> extract with ffmpeg
  b) sidecar input_videos/NN.srt -> use as-is
  c) transcribe:
        - if ASSEMBLYAI_API_KEY set -> AssemblyAI
        - else                      -> FREE local faster-whisper

Usage:
    python scripts/transcribe.py input_videos/01.mp4 temp/01.srt
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

from common import (ffmpeg_bin, run, env, load_env, log_error,
                    Cue, write_srt, TEMP)
from probe_video import probe


# --------------------------------------------------------------------------- #
def try_embedded(video: str, out_srt: Path) -> bool:
    info = probe(video)
    if not info.get("embedded_subtitles"):
        return False
    try:
        run([ffmpeg_bin(), "-y", "-i", video, "-map", "0:s:0", str(out_srt)])
        return out_srt.exists() and out_srt.stat().st_size > 10
    except Exception:
        return False


def try_sidecar(video: str, out_srt: Path) -> bool:
    side = Path(video).with_suffix(".srt")
    if side.exists() and side.stat().st_size > 10:
        out_srt.write_text(side.read_text(encoding="utf-8", errors="ignore"),
                           encoding="utf-8")
        return True
    return False


# --------------------------------------------------------------------------- #
def transcribe_assemblyai(video: str, out_srt: Path) -> bool:
    key = env("ASSEMBLYAI_API_KEY")
    if not key:
        return False
    import requests
    base = "https://api.assemblyai.com/v2"
    headers = {"authorization": key}
    # 1) extract audio to keep upload small
    audio = TEMP / (Path(video).stem + "_audio.mp3")
    run([ffmpeg_bin(), "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000",
         "-b:a", "64k", str(audio)])
    # 2) upload
    with open(audio, "rb") as f:
        up = requests.post(f"{base}/upload", headers=headers, data=f, timeout=600)
    up.raise_for_status()
    upload_url = up.json()["upload_url"]
    # 3) request transcript
    tr = requests.post(f"{base}/transcript", headers=headers,
                       json={"audio_url": upload_url, "punctuate": True,
                             "format_text": True}, timeout=60)
    tr.raise_for_status()
    tid = tr.json()["id"]
    # 4) poll
    for _ in range(600):
        st = requests.get(f"{base}/transcript/{tid}", headers=headers, timeout=60).json()
        if st["status"] == "completed":
            break
        if st["status"] == "error":
            raise RuntimeError("AssemblyAI: " + st.get("error", "unknown"))
        time.sleep(3)
    else:
        raise RuntimeError("AssemblyAI timed out")
    # 5) fetch srt
    srt = requests.get(f"{base}/transcript/{tid}/srt", headers=headers, timeout=60)
    srt.raise_for_status()
    out_srt.write_text(srt.text, encoding="utf-8")
    return out_srt.exists() and out_srt.stat().st_size > 10


# --------------------------------------------------------------------------- #
def transcribe_whisper(video: str, out_srt: Path) -> bool:
    from faster_whisper import WhisperModel
    model_size = env("WHISPER_MODEL", "small")
    # extract 16k mono wav
    wav = TEMP / (Path(video).stem + "_audio.wav")
    run([ffmpeg_bin(), "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000",
         str(wav)])
    model = WhisperModel(model_size, device="auto", compute_type="int8")
    segments, _ = model.transcribe(str(wav), vad_filter=True, beam_size=5,
                                   word_timestamps=False)
    cues = []
    for i, seg in enumerate(segments, 1):
        txt = seg.text.strip()
        if txt:
            cues.append(Cue(i, seg.start, seg.end, txt))
    if not cues:
        return False
    write_srt(cues, out_srt)
    return True


# --------------------------------------------------------------------------- #
def get_subtitles(video: str, out_srt: str | Path) -> bool:
    out_srt = Path(out_srt)
    out_srt.parent.mkdir(parents=True, exist_ok=True)

    if try_embedded(video, out_srt):
        print(f"[subtitles] embedded stream -> {out_srt}")
        return True
    if try_sidecar(video, out_srt):
        print(f"[subtitles] sidecar .srt -> {out_srt}")
        return True
    # transcription
    if env("ASSEMBLYAI_API_KEY"):
        try:
            if transcribe_assemblyai(video, out_srt):
                print(f"[subtitles] AssemblyAI -> {out_srt}")
                return True
        except Exception as e:
            log_error(f"{Path(video).name}: AssemblyAI failed ({e}); falling back to Whisper")
    if transcribe_whisper(video, out_srt):
        print(f"[subtitles] Whisper (local) -> {out_srt}")
        return True
    return False


def main():
    load_env()
    if len(sys.argv) < 3:
        print("usage: python scripts/transcribe.py <video> <out.srt>", file=sys.stderr)
        sys.exit(2)
    video, out_srt = sys.argv[1], sys.argv[2]
    ok = get_subtitles(video, out_srt)
    if not ok:
        log_error(f"{Path(video).name}: transcription failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
